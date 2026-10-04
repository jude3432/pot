import requests
import logging
import asyncio
import time
import threading
import re
from config import settings

logger = logging.getLogger(__name__)


class IChancyClient:
    BASE_URL = getattr(settings, 'ICHANCY_AGENT_BASE_URL', 'https://agents.ichancy100.com')

    HEADERS = {
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',
        'DNT': '1',
        'Connection': 'keep-alive',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin': 'https://agents.ichancy100.com',
        'Referer': 'https://agents.ichancy100.com/'
    }

    def __init__(self):
        self.session = requests.Session()
        proxy_url = getattr(settings, 'ICHANCY_PROXY_URL', '')
        if proxy_url:
            self.session.proxies.update({'http': proxy_url, 'https': proxy_url})
            logger.info('iChancy proxy enabled for this session')
        self._official_lock = threading.RLock()
        self._official_access_token = None
        self._official_refresh_token = None
        self._official_access_token_expires_at = 0.0
        self._official_api_base_url = self.BASE_URL
        self._official_last_auth_error = None
        self.update_headers_and_cookies()
        self._load_official_tokens_from_db()
        self.load_cookie_from_db()

    def update_headers_and_cookies(self, new_cookie_string=None):
        self.session.headers.clear()
        self.session.headers.update(self.HEADERS)
        self.session.headers['User-Agent'] = settings.USER_AGENT

        cookie_to_use = new_cookie_string if new_cookie_string else getattr(settings, 'COOKIE_STRING', '')
        cookies_dict = self._parse_cookie_string(cookie_to_use)
        self.session.cookies.update(cookies_dict)
        logger.info(f"Loaded {len(cookies_dict)} cookies into Caesar_Bot iChancy session.")

    @staticmethod
    def _parse_cookie_string(cookie_string):
        cookies = {}
        if not cookie_string:
            return cookies
        for pair in cookie_string.split(';'):
            pair = pair.strip()
            if '=' in pair:
                name, value = pair.split('=', 1)
                cookies[name.strip()] = value.strip()
        return cookies

    @staticmethod
    def _is_invalid_session_result(result_data):
        if isinstance(result_data, str):
            return result_data.lower() in {"unauthorized", "expired", "session_expired", "ex", "not_authorized"}
        if isinstance(result_data, dict):
            msg = str(result_data.get('message') or result_data.get('error') or '').lower()
            return msg in {"unauthorized", "expired", "session_expired", "ex", "not_authorized"}
        return False

    @staticmethod
    def _extract_balance_from_result(result_data):
        """استخراج balance من أشكال ردود iChancy المختلفة. يرجع None إذا لا يوجد balance صريح."""
        if result_data is None:
            return None
        if isinstance(result_data, (int, float)):
            return int(result_data)
        if isinstance(result_data, list):
            for item in result_data:
                val = IChancyClient._extract_balance_from_result(item)
                if val is not None:
                    return val
            return None
        if isinstance(result_data, dict):
            for key in ('balance', 'Balance', 'amount', 'walletBalance', 'currentBalance'):
                if key in result_data:
                    try:
                        return int(float(result_data.get(key) or 0))
                    except Exception:
                        return None
            for key in ('record', 'player', 'data', 'result'):
                if key in result_data:
                    val = IChancyClient._extract_balance_from_result(result_data.get(key))
                    if val is not None:
                        return val
            if 'records' in result_data:
                return IChancyClient._extract_balance_from_result(result_data.get('records'))
        return None

    # ------------------------------------------------------------------
    # Official Agent API integration
    # ------------------------------------------------------------------
    # The legacy cookie/session implementation below is intentionally kept
    # for endpoints not covered by the official documentation.
    OFFICIAL_API_PREFIX = "/global/api/UserApi"

    @staticmethod
    def _response_json(response, operation="iChancy API"):
        """Safely decode an API response and retain useful diagnostics."""
        content_type = (response.headers.get("content-type") or "").lower()
        body = response.text or ""
        if not body.strip():
            logger.error("%s returned an empty response (HTTP %s)", operation, response.status_code)
            return None
        try:
            data = response.json()
            if "json" not in content_type:
                logger.warning(
                    "%s returned valid JSON with unexpected content-type HTTP %s (%s)",
                    operation,
                    response.status_code,
                    content_type or "missing",
                )
            return data
        except ValueError:
            logger.error(
                "%s returned non-JSON HTTP %s (%s): %r",
                operation,
                response.status_code,
                content_type or "missing",
                body[:500],
            )
            return None

    @staticmethod
    def _notification_error(data, default="iChancy API request failed"):
        for item in (data or {}).get("notification", []) if isinstance(data, dict) else []:
            if isinstance(item, dict) and item.get("content"):
                return str(item["content"])
        return default

    @staticmethod
    def _extract_player_id(payload):
        """Extract a player id from the documented and legacy response shapes."""
        if isinstance(payload, dict):
            for key in ("playerId", "playerID", "player_id", "playerIdValue"):
                value = payload.get(key)
                if value not in (None, ""):
                    return str(value)
            for key in ("player", "data", "record", "result"):
                value = IChancyClient._extract_player_id(payload.get(key))
                if value:
                    return value
        elif isinstance(payload, list):
            for item in payload:
                value = IChancyClient._extract_player_id(item)
                if value:
                    return value
        return None

    @staticmethod
    def _is_player_registration_access_error(message):
        text = str(message or "").strip().lower()
        return any(marker in text for marker in (
            "not access to add player",
            "no access to add player",
            "access to add player",
            "ليس لديك صلاحية إضافة لاعب",
            "لا تملك صلاحية إضافة لاعب",
        ))

    def _load_official_tokens_from_db(self):
        """Load rotating official API tokens without changing existing settings."""
        try:
            from database.connection import DatabaseManager
            DatabaseManager.execute_query(
                "ALTER TABLE bot_settings ADD COLUMN IF NOT EXISTS ichancy_access_token TEXT, "
                "ADD COLUMN IF NOT EXISTS ichancy_refresh_token TEXT, "
                "ADD COLUMN IF NOT EXISTS ichancy_access_token_expires_at DOUBLE PRECISION",
                ()
            )
            row = DatabaseManager.execute_query_dict(
                "SELECT ichancy_access_token, ichancy_refresh_token, ichancy_access_token_expires_at "
                "FROM bot_settings WHERE id = 1", fetch="one"
            )
            if row:
                self._official_access_token = row.get("ichancy_access_token")
                self._official_refresh_token = row.get("ichancy_refresh_token")
                self._official_access_token_expires_at = float(row.get("ichancy_access_token_expires_at") or 0)
        except Exception as exc:
            logger.warning("Official API token storage is not available yet: %s", exc)

    def _save_official_tokens(self, access_token, refresh_token, expires_at):
        self._official_access_token = access_token
        self._official_refresh_token = refresh_token
        self._official_access_token_expires_at = float(expires_at or 0)
        try:
            from database.connection import DatabaseManager
            DatabaseManager.execute_query(
                "UPDATE bot_settings SET ichancy_access_token = %s, ichancy_refresh_token = %s, "
                "ichancy_access_token_expires_at = %s WHERE id = 1",
                (access_token, refresh_token, float(expires_at or 0))
            )
        except Exception as exc:
            logger.warning("Could not persist official API tokens: %s", exc)

    def _official_sign_in(self):
        username = getattr(settings, "AGENT_USERNAME", None)
        password = getattr(settings, "AGENT_PASSWORD", None)
        self._official_last_auth_error = None
        if not username or not password or str(password).startswith("ضع_"):
            self._official_last_auth_error = "بيانات AGENT_USERNAME أو AGENT_PASSWORD غير مضبوطة في Render"
            logger.error("Official API sign-in skipped: AGENT_USERNAME/AGENT_PASSWORD is not configured")
            return False
        bases = []
        for base in (self._official_api_base_url, self.BASE_URL, "https://agents.ichancy.com"):
            base = str(base or "").rstrip("/")
            if base and base not in bases:
                bases.append(base)
        for base in bases:
            url = f"{base}{self.OFFICIAL_API_PREFIX}/signIn"
            try:
                response = self.session.post(
                    url,
                    json={"username": username, "password": password},
                    headers={"Accept-Encoding": "identity", "Content-Type": "application/json"},
                    timeout=30,
                )
                data = self._response_json(response, f"official signIn ({base})")
                raw_body = (response.text or "").lower()
                if response.status_code == 403 and ("cloudflare" in raw_body or "you have been blocked" in raw_body):
                    self._official_last_auth_error = "Cloudflare يحظر اتصال Render بخدمة iChancy؛ يجب طلب whitelist لعنوان IP أو استخدام خادم مسموح من iChancy"
                result = data.get("result") if isinstance(data, dict) else None
                if response.status_code != 200 or not isinstance(result, dict):
                    logger.warning("Official signIn failed on %s (HTTP %s): %s", base, response.status_code, self._notification_error(data, "endpoint unavailable or invalid credentials"))
                    continue
                access_token = result.get("accessToken")
                refresh_token = result.get("refreshToken")
                if not access_token or not refresh_token:
                    logger.warning("Official signIn on %s returned no token pair", base)
                    continue
                self._official_api_base_url = base
                self._save_official_tokens(access_token, refresh_token, time.time() + 3600 - 30)
                logger.info("Official iChancy API sign-in succeeded on %s", base)
                return True
            except requests.RequestException as exc:
                logger.warning("Official signIn network error on %s: %s", base, exc)
        return False

    def _official_refresh(self):
        if not self._official_refresh_token:
            return False
        url = f"{self._official_api_base_url}{self.OFFICIAL_API_PREFIX}/refreshToken"
        try:
            response = self.session.post(url, json={"refreshToken": self._official_refresh_token}, timeout=30)
            data = self._response_json(response, "official refreshToken")
            result = data.get("result") if isinstance(data, dict) else None
            if response.status_code != 200 or not isinstance(result, dict):
                logger.warning("Official refresh failed (HTTP %s): %s", response.status_code, self._notification_error(data, "Invalid or expired refresh token"))
                return False
            access_token = result.get("accessToken")
            refresh_token = result.get("refreshToken")
            if not access_token or not refresh_token:
                return False
            self._save_official_tokens(access_token, refresh_token, time.time() + 3600 - 30)
            logger.info("Official iChancy API token refreshed and rotated")
            return True
        except requests.RequestException as exc:
            logger.error("Official refresh network error: %s", exc)
            return False

    def _official_request(self, endpoint, payload, operation=None):
        """Call a documented endpoint; return None only when unavailable.

        A decoded API error is returned as a dict so callers do not silently
        fall back after a real business/permission error. Legacy fallback is
        used only for transport/non-JSON failures.
        """
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if not path.startswith("/global/api/"):
            path = f"{self.OFFICIAL_API_PREFIX}{path}"
        url = f"{self._official_api_base_url}{path}"
        operation = operation or endpoint
        with self._official_lock:
            if not self._official_access_token or time.time() >= self._official_access_token_expires_at:
                if not self._official_refresh() and not self._official_sign_in():
                    if self._official_last_auth_error:
                        return {"status": False, "result": False, "notification": [{"content": self._official_last_auth_error}]}
                    return None
            for attempt in range(2):
                try:
                    response = self.session.post(
                        url,
                        json=payload,
                        headers={"Authorization": f"Bearer {self._official_access_token}", "Content-Type": "application/json"},
                        timeout=45,
                    )
                    data = self._response_json(response, operation)
                    # ``ex`` may be a business-level registration response,
                    # not an expired session. Retrying registerPlayer after
                    # refreshing auth can submit the same registration twice
                    # and hide the actual API error.
                    invalid = response.status_code == 401 or (
                        isinstance(data, dict)
                        and data.get("result") == "ex"
                        and "registerplayer" not in operation.lower()
                    )
                    if invalid and attempt == 0:
                        if self._official_refresh() or self._official_sign_in():
                            continue
                    if data is None:
                        return None
                    return data
                except requests.RequestException as exc:
                    logger.error("%s network error: %s", operation, exc)
                    return None
        return None

    def _official_player_id(self, target_username):
        payload = {
            "start": 0,
            "limit": 100,
            "filter": {
                "withoutTotalCount": {"action": "=", "value": True},
                "userName": {"action": "like", "value": target_username, "valueLabel": target_username},
            },
            "isNextPage": False,
        }
        data = self._official_request("/global/api/Player/getPlayersForCurrentAgent", payload, "official player search")
        if data is None:
            data = self._official_request("/global/api/UserApi/getPlayersForCurrentAgent", payload, "official player search")
        if not isinstance(data, dict):
            return None
        result = data.get("result")
        if isinstance(result, dict):
            records = result.get("records", [])
        elif isinstance(result, list):
            records = result
        else:
            records = []
        target = str(target_username).strip().lower()
        for row in records:
            if str(row.get("username") or row.get("userName") or row.get("login") or "").strip().lower() == target:
                return self._extract_player_id(row) or (str(row.get("id")) if row.get("id") not in (None, "") else None)
        return None

    @staticmethod
    def _normalize_agent_id(value):
        """Extract the numeric agent id, including from accidentally pasted KEY=VALUE text."""
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        match = re.search(r"(\d+)", text)
        return match.group(1) if match else None

    def load_cookie_from_db(self):
        try:
            from database.connection import DatabaseManager
            res = DatabaseManager.execute_query_dict("SELECT ichancy_cookie FROM bot_settings WHERE id = 1", fetch='one')
            if res and res.get('ichancy_cookie'):
                cookie_str = res['ichancy_cookie']
                self.update_headers_and_cookies(cookie_str)
                logger.info("Successfully synchronized active session cookie from database.")
                return True
        except Exception as e:
            logger.error(f"Error loading cookie from database: {e}")
        return False

    def _login_agent(self):
        # Prefer the documented token API. Avoid duplicate signIn calls because
        # iChancy invalidates the previous token pair after every new signIn.
        if self._official_access_token and time.time() < self._official_access_token_expires_at:
            return True
        if self._official_refresh() or self._official_sign_in():
            return True
        # Keep the legacy cookie login below for unsupported administrative
        # endpoints and emergency compatibility.
        logger.info("[Caesar_Bot] [Auto-Login] Starting legacy session login...")
        login_page_url = f"{self.BASE_URL}/login"
        try:
            self.session.get(login_page_url, timeout=30)

            signin_url = f"{self.BASE_URL}/global/api/User/signIn"
            payload = {
                "username": settings.AGENT_USERNAME,
                "login": settings.AGENT_USERNAME,
                "password": settings.AGENT_PASSWORD
            }
            response_post = self.session.post(signin_url, json=payload, timeout=30)
            if response_post.status_code != 200:
                logger.error(f"Agent login failed: HTTP {response_post.status_code} - Response: {response_post.text}")
                return False

            response_json = self._response_json(response_post, "legacy signIn")
            if response_json is None:
                return False
            result = response_json.get("result", {})
            is_success = False
            if isinstance(result, dict) and result.get("message") == "dashboard":
                is_success = True
            elif response_json.get("status") is True:
                is_success = True

            if not is_success:
                logger.error(f"Agent login failed: {response_json}")
                return False

            init_apis = [
                f"{self.BASE_URL}/global/api/core/getData",
                f"{self.BASE_URL}/global/api/Agent/getAgentWallet",
                f"{self.BASE_URL}/global/api/Message/getTotalUnreadMessagesCount",
                f"{self.BASE_URL}/global/api/UserNotification/getAllUserNotifications"
            ]
            for api_url in init_apis:
                try:
                    self.session.post(api_url, json={}, timeout=15)
                except Exception as init_err:
                    logger.warning(f"Init endpoint failed: {api_url} -> {init_err}")

            cookies_dict = self.session.cookies.get_dict()
            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])
            try:
                from database.connection import DatabaseManager
                DatabaseManager.execute_query("UPDATE bot_settings SET ichancy_cookie = %s, last_cookie_update = CURRENT_TIMESTAMP WHERE id = 1", (cookie_str,))
            except Exception as db_err:
                logger.warning(f"Failed to persist cookie to DB: {db_err}")
            self.update_headers_and_cookies(cookie_str)
            logger.info("[Caesar_Bot] Agent Login: SUCCESSFUL")
            return True
        except Exception as e:
            logger.error(f"[Caesar_Bot] Agent Login exception: {e}")
            return False

    def _check_session_validity(self):
        # Validate official API auth first using a documented read endpoint.
        if self._official_access_token or self._official_refresh_token:
            probe = self._official_request(
                "/global/api/UserApi/getPlayersForCurrentAgent",
                {"start": 0, "limit": 1, "filter": {"withoutTotalCount": {"action": "=", "value": True}}, "isNextPage": False},
                "official session check",
            )
            if probe is not None and probe.get("result") != "ex":
                return bool(probe.get("status", True))
        url = f"{self.BASE_URL}/global/api/Agent/getAgentWalletByAgentId"
        payload = {
            'affiliateId': int(settings.PARENT_ID) if settings.PARENT_ID else None,
            'currencyCode': "NSP"
        }
        try:
            response = self.session.post(url, json=payload, timeout=10)
            if response.status_code != 200:
                return False
            data = self._response_json(response, "legacy agent transaction list")
            if data is None:
                return {'status': False, 'result': {'records': [], 'totalRecordsCount': 0}, 'error': 'Invalid JSON response'}
            result_data = data.get('result')
            if self._is_invalid_session_result(result_data):
                return False
            return bool(result_data)
        except Exception:
            return False

    def _fetch_player_statistics_page(self, payload):
        url = f"{self.BASE_URL}/global/api/Statistics/getPlayersStatisticsPro"
        response = self.session.post(url, json=payload, timeout=30)
        if response.status_code in [401, 403]:
            logger.warning("Session expired while fetching player statistics. Re-login...")
            if self._login_agent():
                response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = self._response_json(response, "legacy statistics")
        if data is None:
            raise ValueError("Invalid response from legacy statistics endpoint")
        return data

    def _extract_player_id_from_records(self, records, target_username):
        target = str(target_username).strip().lower()
        for row in records or []:
            username = str(row.get('username') or row.get('userName') or row.get('login') or '').strip().lower()
            if username == target:
                player_id = self._extract_player_id(row) or row.get('id')
                if player_id:
                    return str(player_id)
        return None

    def _get_player_id(self, target_username, max_attempts=5, delay_seconds=2):
        logger.info(f"[Caesar_Bot] Fetching Player ID for iChancy username: {target_username}")
        target_username = str(target_username).strip()
        if not target_username:
            return None

        base_payload = {
            "start": 0,
            "limit": 100,
            "filter": {
                "username": {
                    "action": "=",
                    "value": target_username
                }
            }
        }

        official_player_id = self._official_player_id(target_username)
        if official_player_id:
            return official_player_id

        for attempt in range(1, max_attempts + 1):
            try:
                logger.info(f"[Caesar_Bot] Player ID fetch attempt {attempt}/{max_attempts} for {target_username}")
                data = self._fetch_player_statistics_page(base_payload)
                result_data = data.get('result')
                if isinstance(result_data, dict):
                    records = result_data.get('records', [])
                    player_id = self._extract_player_id_from_records(records, target_username)
                    if player_id:
                        logger.info(f"[Caesar_Bot] Player ID found by exact filter: {player_id}")
                        return player_id

                logger.warning("Player ID not found with exact filter, attempting fallback search...")
                fallback_payload = {
                    "start": 0,
                    "limit": 500,
                    "filter": {}
                }
                fallback_data = self._fetch_player_statistics_page(fallback_payload)
                fallback_result = fallback_data.get('result')
                if isinstance(fallback_result, dict):
                    fallback_records = fallback_result.get('records', [])
                    player_id = self._extract_player_id_from_records(fallback_records, target_username)
                    if player_id:
                        logger.info(f"[Caesar_Bot] Player ID found by fallback search: {player_id}")
                        return player_id

                if attempt < max_attempts:
                    logger.warning(f"[Caesar_Bot] Player ID not visible yet. Waiting {delay_seconds}s before retry...")
                    time.sleep(delay_seconds)
            except Exception as e:
                logger.error(f"Error fetching player ID on attempt {attempt}: {e}")
                if attempt < max_attempts:
                    time.sleep(delay_seconds)

        logger.error(f"[Caesar_Bot] Failed to fetch Player ID after {max_attempts} attempts for {target_username}")
        return None

    def _register_account(self, username, password, email, parent_id=None):
        logger.info(f"[Caesar_Bot] Submitting registration for username: {username} with email: {email}")
        # iChancy expects the player to be assigned to the authenticated agent.
        # iChancy's registration permission is attached to the configured
        # parent/affiliate account. Prefer PARENT_ID (the documented setting),
        # and only use AGENT_ID as a backwards-compatible fallback.
        registration_parent_id = (
            self._normalize_agent_id(parent_id)
            or self._normalize_agent_id(settings.PARENT_ID)
            or self._normalize_agent_id(getattr(settings, "AGENT_ID", None))
        )
        logger.info(
            "[Caesar_Bot] Registration target: endpoint=%s, agent_id=%s, configured_agent_id=%s, configured_parent_id=%s",
            self._official_api_base_url,
            registration_parent_id or "<missing>",
            self._normalize_agent_id(getattr(settings, "AGENT_ID", None)) or "<invalid>",
            self._normalize_agent_id(settings.PARENT_ID) or "<invalid>",
        )
        official_payload = {"player": {"login": username, "email": email, "password": password, "parentId": registration_parent_id}}
        official = self._official_request("/global/api/UserApi/registerPlayer", official_payload, "official registerPlayer")
        if official is not None:
            result_data = official.get("result")
            registration_player_id = self._extract_player_id(result_data)
            if result_data == 1 or registration_player_id:
                return {"success": True, "username": username, "password": password, "email": email, "player_id": registration_player_id, "response": official}
            # A decoded response from the official endpoint is authoritative.
            # Falling through to the legacy endpoint here can submit the same
            # registration twice and turn a generic ``ex`` into a misleading
            # duplicate-account message.
            error_message = self._notification_error(official, "Registration rejected by iChancy")
            logger.warning(
                "[Caesar_Bot] Official registerPlayer rejected request: status=%s result=%r notification=%s",
                official.get("status"),
                result_data,
                error_message,
            )
            # Some deployments expose registration through the legacy cookie
            # endpoint while the official endpoint is read-only for the agent.
            # Retry there only for an explicit permission denial; never retry
            # ordinary validation or duplicate-account responses.
            if not self._is_player_registration_access_error(error_message):
                return {"success": False, "error": error_message, "response": official}
            logger.warning("[Caesar_Bot] Official registration denied by role; trying legacy registration endpoint once")
        try:
            try:
                logger.info("[Caesar_Bot] Pre-initializing session parameters via getData...")
                self.session.post(f"{self.BASE_URL}/global/api/core/getData", json={}, timeout=15)
            except Exception as e:
                logger.warning(f"[Caesar_Bot] Failed pre-initializing via getData: {e}")

            url = f"{self.BASE_URL}/global/api/Player/registerPlayer"
            payload = {
                "player": {
                    "login": username,
                    "email": email,
                    "password": password,
                    "parentId": int(registration_parent_id) if registration_parent_id else None
                }
            }
            logger.info(
                "[Caesar_Bot] Sending legacy registerPlayer request: endpoint=%s parent_id=%s",
                url,
                registration_parent_id or "<missing>",
            )
            response = self.session.post(url, json=payload, timeout=30)

            if response.status_code in [401, 403]:
                logger.warning("Session expired on registration. Re-login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)

            response_json = self._response_json(response, "legacy registration")
            if response_json is None:
                return {
                    "success": False,
                    "error": f"iChancy أرسل ردًا غير صالح (HTTP {response.status_code})",
                }
            logger.info(
                "[Caesar_Bot] Legacy registration response: http_status=%s result=%r notification=%s",
                response.status_code,
                response_json.get("result"),
                self._notification_error(response_json, "none"),
            )
            result_data = response_json.get("result")

            is_invalid_session = False
            if self._is_invalid_session_result(result_data):
                if not self._check_session_validity():
                    is_invalid_session = True
            elif not result_data:
                if not self._check_session_validity():
                    is_invalid_session = True

            if is_invalid_session:
                logger.warning("[Caesar_Bot] Session expired on registration. Retrying login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
                    response_json = self._response_json(response, "legacy registration retry")
                    if response_json is None:
                        return {"success": False, "error": "iChancy أرسل ردًا غير صالح بعد إعادة المحاولة"}
                    logger.info(f"[Caesar_Bot] Raw registration response JSON after retry: {response_json}")
                    result_data = response_json.get("result")

            if not result_data or isinstance(result_data, str):
                error_content = "Registration failed"
                if result_data == "ex":
                    error_content = "اسم المستخدم أو البريد الإلكتروني مسجل مسبقاً في المنصة!"
                notifications = response_json.get("notification", [])
                if notifications:
                    error_content = notifications[0].get("content", error_content)
                logger.error(
                    "[Caesar_Bot] Registration failed after official+legacy attempts: result=%r error=%s",
                    result_data,
                    error_content,
                )
                return {'success': False, 'error': error_content}

            # Registration success is independent from the eventual visibility of
            # the player in the search endpoint. Resolve the id in the handler
            # without blocking the registration response for up to 10 seconds.
            player_id = self._extract_player_id(response_json.get("result"))
            return {
                'success': True,
                'username': username,
                'password': password,
                'email': email,
                'player_id': player_id,
                'response': response_json
            }
        except Exception as e:
            logger.error(f"HTTP exception during registration: {e}")
            return {'success': False, 'error': str(e)}

    def _get_admin_balance(self):
        aff_id = None
        try:
            val = settings.AGENT_ID or settings.PARENT_ID
            if val:
                aff_id = int(str(val).strip())
        except Exception:
            aff_id = None

        urls_to_try = [
            (f"{self.BASE_URL}/global/api/Agent/getAgentWalletByAgentId", {'affiliateId': aff_id, 'currencyCode': "NSP"}),
            (f"{self.BASE_URL}/global/api/Agent/getAgentWallet", {'currencyCode': "NSP"})
        ]

        for url, payload in urls_to_try:
            try:
                response = self.session.post(url, json=payload, timeout=20)
                if response.status_code in [401, 403]:
                    logger.warning("Session expired! Triggering automatic self-healing login...")
                    if self._login_agent():
                        response = self.session.post(url, json=payload, timeout=20)
                if response.status_code != 200:
                    continue
                data = self._response_json(response, "legacy admin balance")
                if data is None:
                    continue
                result_data = data.get('result')
                if self._is_invalid_session_result(result_data):
                    logger.warning("Session invalid on admin balance! Retrying login...")
                    if self._login_agent():
                        response = self.session.post(url, json=payload, timeout=20)
                        data = self._response_json(response, "legacy admin balance retry")
                        if data is None:
                            continue
                        result_data = data.get('result')
                if result_data is not None:
                    if isinstance(result_data, (int, float)):
                        return int(result_data)
                    if isinstance(result_data, list) and len(result_data) > 0 and isinstance(result_data[0], dict):
                        for k in ['balance', 'amount', 'walletBalance']:
                            if k in result_data[0] and result_data[0][k] is not None:
                                return int(float(result_data[0][k]))
                    elif isinstance(result_data, dict):
                        for k in ['balance', 'amount', 'walletBalance']:
                            if k in result_data and result_data[k] is not None:
                                return int(float(result_data[k]))
            except Exception as e:
                logger.warning(f"Error fetching admin balance from {url}: {e}")
                continue

        logger.warning("Admin balance could not be extracted from any endpoint.")
        return None

    def _get_agent_transaction_list(self, from_date, to_date, limit=1000, start=0, is_to_me=False, affiliate_id=None):
        """جلب سجل حركات الكاشيرة/الوكيل من iChancy."""
        url = f"{self.BASE_URL}/global/api/Agent/getAgentTransactionList"
        agent_id = affiliate_id or getattr(settings, 'AGENT_ID', None) or getattr(settings, 'PARENT_ID', None)
        try:
            agent_id_int = int(agent_id) if agent_id else None
        except Exception:
            agent_id_int = None
        payload = {
            "start": int(start or 0),
            "limit": int(limit or 1000),
            "filter": {
                "currency": {
                    "action": "=",
                    "valueLabel": "NSP",
                    "value": "NSP"
                },
                "date": {
                    "action": "between",
                    "from": from_date,
                    "to": to_date,
                    "valueLabel": f"{from_date} - {to_date}"
                },
                "isToMe": {
                    "action": "=",
                    "value": bool(is_to_me),
                    "valueLabel": bool(is_to_me)
                }
            }
        }
        if agent_id_int is not None:
            payload["filter"]["affiliateId"] = {
                "action": "=",
                "value": agent_id_int,
                "valueLabel": agent_id_int
            }
        try:
            response = self.session.post(url, json=payload, timeout=45)
            if response.status_code in [401, 403]:
                logger.warning("Session expired while fetching agent transaction list. Re-login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=45)
            response.raise_for_status()
            data = self._response_json(response, "legacy session check")
            if data is None:
                return False
            result_data = data.get('result')
            if self._is_invalid_session_result(result_data):
                logger.warning("Session invalid on agent transaction list. Retrying login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=45)
                    response.raise_for_status()
                    data = self._response_json(response, "legacy agent transaction retry")
                    if data is None:
                        return {'status': False, 'result': {'records': [], 'totalRecordsCount': 0}, 'error': 'Invalid JSON response'}
            return data
        except Exception as e:
            logger.error(f"Error fetching agent transaction list: {e}")
            return {'status': False, 'result': {'records': [], 'totalRecordsCount': 0}, 'error': str(e)}

    def _get_player_balance(self, player_id):
        official = self._official_request("/global/api/UserApi/getPlayerBalanceById", {"playerId": str(player_id)}, "official getPlayerBalanceById")
        if official is not None:
            result = official.get("result")
            balance = self._extract_balance_from_result(result)
            if balance is not None:
                return int(balance)
            if official.get("status") is False or isinstance(result, list):
                return None
        url = f"{self.BASE_URL}/global/api/Player/getPlayerBalanceById"
        payload = {'playerId': player_id}
        try:
            response = self.session.post(url, json=payload, timeout=30)
            if response.status_code in [401, 403]:
                logger.warning("Session expired! Triggering automatic self-healing login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            data = self._response_json(response, "legacy session check")
            if data is None:
                return False
            result_data = data.get('result')
            if self._is_invalid_session_result(result_data):
                logger.warning("Session invalid on player balance! Retrying login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
                    data = self._response_json(response, "legacy API retry response")
                    if data is None:
                        return None
                    result_data = data.get('result')
            if self._is_invalid_session_result(result_data):
                logger.warning(f"Invalid session result on player balance after retry: {result_data}")
                return None
            balance = self._extract_balance_from_result(result_data)
            if balance is not None:
                return int(balance)
            logger.warning(f"Player balance response did not contain a balance field: {data}")
            return None
        except Exception as e:
            logger.error(f"Error fetching player balance: {e}")
            return None

    def _transfer_money(self, player_id, amount, comment=None):
        official_payload = {"amount": amount, "comment": comment or "", "playerId": str(player_id), "currencyCode": "NSP", "currency": "NSP", "moneyStatus": 5}
        official = self._official_request("/global/api/UserApi/depositToPlayer", official_payload, "official depositToPlayer")
        if official is not None:
            if official.get("result") and official.get("status") is not False:
                logger.info("Official API transferred +%s NSP to Player: %s", amount, player_id)
                return True
            logger.error("Official deposit failed: %s", self._notification_error(official))
            return False
        url = f"{self.BASE_URL}/global/api/Player/depositToPlayer"
        payload = {
            'amount': amount,
            'comment': comment,
            'playerId': player_id,
            'currencyCode': "NSP",
            'moneyStatus': 5
        }
        try:
            response = self.session.post(url, json=payload, timeout=30)
            if response.status_code in [401, 403]:
                logger.warning("Session expired! Triggering automatic self-healing login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            response_json = self._response_json(response, "legacy transfer response")
            if response_json is None:
                return False
            result_data = response_json.get("result")
            if self._is_invalid_session_result(result_data):
                logger.warning("Session invalid on money transfer! Retrying login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
                    response_json = self._response_json(response, "legacy transfer retry response")
                    if response_json is None:
                        return False
                    result_data = response_json.get("result")
            if self._is_invalid_session_result(result_data):
                logger.error(f"Transfer failed after re-login: invalid session result={result_data}")
                return False
            if result_data:
                logger.info(f"Successfully transferred +{amount} NSP to Player: {player_id}")
                return True
            logger.error(f"Transfer failed: empty/false result response={response_json}")
            return False
        except Exception as e:
            logger.error(f"Error transferring money: {e}")
            return False

    def _withdraw_money(self, player_id, amount, comment=None):
        official_payload = {"amount": -abs(amount), "comment": comment or "", "playerId": str(player_id), "currencyCode": "NSP", "currency": "NSP", "moneyStatus": 5}
        official = self._official_request("/global/api/UserApi/withdrawFromPlayer", official_payload, "official withdrawFromPlayer")
        if official is not None:
            if official.get("result") and official.get("status") is not False:
                logger.info("Official API withdrew -%s NSP from Player: %s", amount, player_id)
                return True
            logger.error("Official withdrawal failed: %s", self._notification_error(official))
            return False
        url = f"{self.BASE_URL}/global/api/Player/withdrawFromPlayer"
        payload = {
            'amount': -amount,
            'comment': comment,
            'playerId': player_id,
            'currencyCode': "NSP",
            'moneyStatus': 5
        }
        try:
            response = self.session.post(url, json=payload, timeout=30)
            if response.status_code in [401, 403]:
                logger.warning("Session expired while withdrawing money. Re-login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()
            response_json = self._response_json(response, "legacy transfer response")
            if response_json is None:
                return False
            result_data = response_json.get("result")
            if self._is_invalid_session_result(result_data):
                logger.warning("Session invalid on money withdrawal! Retrying login...")
                if self._login_agent():
                    response = self.session.post(url, json=payload, timeout=30)
                    response_json = self._response_json(response, "legacy transfer retry response")
                    if response_json is None:
                        return False
                    result_data = response_json.get("result")
            if self._is_invalid_session_result(result_data):
                logger.error(f"Withdraw failed after re-login: invalid session result={result_data}")
                return False
            if result_data:
                logger.info(f"Successfully withdrew -{amount} NSP from Player: {player_id}")
                return True
            logger.error(f"Withdraw failed: empty/false result response={response_json}")
            return False
        except Exception as e:
            logger.error(f"Error withdrawing money: {e}")
            return False

    async def register_account(self, username, password, email):
        return await asyncio.to_thread(self._register_account, username, password, email)

    async def get_player_id(self, target_username):
        return await asyncio.to_thread(self._get_player_id, target_username)

    async def login_agent(self):
        return await asyncio.to_thread(self._login_agent)

    async def get_admin_balance(self):
        return await asyncio.to_thread(self._get_admin_balance)

    async def get_player_balance(self, player_id):
        return await asyncio.to_thread(self._get_player_balance, player_id)

    async def get_agent_transaction_list(self, from_date, to_date, limit=1000, start=0, is_to_me=False, affiliate_id=None):
        return await asyncio.to_thread(self._get_agent_transaction_list, from_date, to_date, limit, start, is_to_me, affiliate_id)

    async def transfer_money(self, player_id, amount, comment=None):
        return await asyncio.to_thread(self._transfer_money, player_id, amount, comment)

    async def withdraw_money(self, player_id, amount, comment=None):
        return await asyncio.to_thread(self._withdraw_money, player_id, amount, comment)

    async def get_player_turnover(self, player_id, field_name='totalBet'):
        """جلب إجمالي مبالغ المراهنات (Turnover) للاعب من إحصائيات iChancy."""
        try:
            payload = {
                "start": 0,
                "limit": 1,
                "filter": {
                    "playerId": {
                        "action": "=",
                        "value": player_id
                    }
                }
            }
            data = await asyncio.to_thread(self._fetch_player_statistics_page, payload)
            result = data.get('result', {})
            if isinstance(result, dict) and 'records' in result and result['records']:
                player_stats = result['records'][0]
                turnover = player_stats.get(field_name, 0)
                return int(turnover or 0)
            return 0
        except Exception as e:
            logger.error(f"Error fetching player turnover: {e}")
            return 0

    async def check_session_validity(self):
        return await asyncio.to_thread(self._check_session_validity)

    # ================================================================
    # 🆕 (Update 18) جلب إحصائيات كل اللاعبين دفعة واحدة للوحة المتصدرين
    # ================================================================

    def _get_all_players_stats_bulk(self, field_name='totalBet', max_pages=40, page_size=500):
        """جلب إجمالي المراهنات التراكمي لكل لاعبي الوكيل عبر التصفح (Pagination).

        بديل كفء عن استدعاء get_player_turnover لكل لاعب على حدة:
        طلب واحد لكل 500 لاعب، ويتوقف عند صفحة غير مكتملة.
        تعيد: {player_id: {'username': str, 'turnover': int}} أو {} عند الفشل.
        """
        results = {}
        safe_field = str(field_name or 'totalBet')
        page_size = max(50, min(int(page_size or 500), 1000))
        for page in range(max(1, int(max_pages or 1))):
            payload = {
                "start": page * page_size,
                "limit": page_size,
                "filter": {}
            }
            try:
                data = self._fetch_player_statistics_page(payload)
            except Exception as e:
                logger.error(f"[Caesar_Bot] Bulk stats fetch failed on page {page}: {e}")
                break
            result = data.get('result') if isinstance(data, dict) else None
            records = result.get('records') if isinstance(result, dict) else None
            if not records:
                break
            for row in records:
                if not isinstance(row, dict):
                    continue
                player_id = row.get('playerId') or row.get('playerID') or row.get('id')
                if not player_id:
                    continue
                username = row.get('username') or row.get('login') or ''
                try:
                    turnover = int(float(row.get(safe_field) or 0))
                except (TypeError, ValueError):
                    turnover = 0
                results[str(player_id)] = {'username': str(username), 'turnover': turnover}
            if len(records) < page_size:
                break
        logger.info(f"[Caesar_Bot] Bulk stats fetched: {len(results)} players (field={safe_field})")
        return results

    async def get_all_players_stats_bulk(self, field_name='totalBet', max_pages=40, page_size=500):
        return await asyncio.to_thread(self._get_all_players_stats_bulk, field_name, max_pages, page_size)

    # ================================================================
    # 🆕 دوال الـ API القياسية (تعيد dict بـ success/message)
    # لكي تتوافق مع ما تتوقعه معالجات الإيداع/السحب التلقائي في اللعبة
    # ================================================================

    async def deposit_to_player(self, player_id, amount, comment=None):
        """إيداع مبلغ في حساب اللاعب مع تحقق رصيد بعد العملية قبل إعلان النجاح."""
        try:
            before_balance = await self.get_player_balance(player_id) if getattr(settings, 'VERIFY_ICHANCY_TRANSFER', True) else None
            ok = await asyncio.to_thread(self._transfer_money, player_id, amount, comment)
            if not ok:
                return {'success': False, 'message': 'فشل الإيداع في حساب اللاعب (لم يؤكد الـ API العملية).'}
            if getattr(settings, 'VERIFY_ICHANCY_TRANSFER', True):
                attempts = int(getattr(settings, 'ICHANCY_TRANSFER_VERIFY_ATTEMPTS', 3) or 3)
                delay = float(getattr(settings, 'ICHANCY_TRANSFER_VERIFY_DELAY_SECONDS', 1.0) or 1.0)
                for _ in range(max(1, attempts)):
                    await asyncio.sleep(delay)
                    after_balance = await self.get_player_balance(player_id)
                    if before_balance is not None and after_balance is not None and int(after_balance) >= int(before_balance) + int(amount):
                        return {'success': True, 'message': 'تم الإيداع وتحقق الرصيد بنجاح.', 'player_id': player_id, 'amount': amount, 'before_balance': before_balance, 'after_balance': after_balance}
                return {'success': False, 'uncertain': True, 'message': f'أرسل API نتيجة نجاح، لكن لم يتم تأكيد زيادة رصيد اللاعب بعد التحقق. قبل={before_balance}'}
            return {'success': True, 'message': 'تم الإيداع في حساب اللاعب بنجاح.', 'player_id': player_id, 'amount': amount}
        except Exception as e:
            logger.error(f"deposit_to_player exception: {e}")
            return {'success': False, 'message': str(e)}

    async def withdraw_from_player(self, player_id, amount, comment=None):
        """سحب مبلغ من حساب اللاعب مع تحقق رصيد بعد العملية قبل إعلان النجاح."""
        try:
            before_balance = await self.get_player_balance(player_id) if getattr(settings, 'VERIFY_ICHANCY_TRANSFER', True) else None
            ok = await asyncio.to_thread(self._withdraw_money, player_id, amount, comment)
            if not ok:
                return {'success': False, 'message': 'فشل السحب من حساب اللاعب (لم يؤكد الـ API العملية).'}
            if getattr(settings, 'VERIFY_ICHANCY_TRANSFER', True):
                attempts = int(getattr(settings, 'ICHANCY_TRANSFER_VERIFY_ATTEMPTS', 3) or 3)
                delay = float(getattr(settings, 'ICHANCY_TRANSFER_VERIFY_DELAY_SECONDS', 1.0) or 1.0)
                for _ in range(max(1, attempts)):
                    await asyncio.sleep(delay)
                    after_balance = await self.get_player_balance(player_id)
                    if before_balance is not None and after_balance is not None and int(after_balance) <= max(0, int(before_balance) - int(amount)):
                        return {'success': True, 'message': 'تم السحب وتحقق الرصيد بنجاح.', 'player_id': player_id, 'amount': amount, 'before_balance': before_balance, 'after_balance': after_balance}
                return {'success': False, 'uncertain': True, 'message': f'أرسل API نتيجة نجاح، لكن لم يتم تأكيد انخفاض رصيد اللاعب بعد التحقق. قبل={before_balance}'}
            return {'success': True, 'message': 'تم السحب من حساب اللاعب بنجاح.', 'player_id': player_id, 'amount': amount}
        except Exception as e:
            logger.error(f"withdraw_from_player exception: {e}")
            return {'success': False, 'message': str(e)}


ichancy_api_client = IChancyClient()
