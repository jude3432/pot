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
        "Accept": "application/json",
        "Content-Type": "application/json",
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
        self._official_api_base_url = str(self.BASE_URL or "").rstrip("/")
        self._official_last_auth_error = None
        self.session.headers.update(self.HEADERS)
        self.session.headers["User-Agent"] = settings.USER_AGENT
        # The official API authenticates with Bearer tokens. Never retain or
        # send browser cookies, including cookies returned by an API response.
        self.session.cookies.clear()
        self._load_official_tokens_from_db()

    def _post_json_without_cookies(self, url, payload, headers=None, timeout=30):
        """POST JSON without ever sending or retaining a session cookie."""
        self.session.cookies.clear()
        try:
            return self.session.post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout,
            )
        finally:
            self.session.cookies.clear()

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
        """Extract a player id from the official API response shapes."""
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
        base = str(self._official_api_base_url or self.BASE_URL).rstrip("/")
        url = f"{base}{self.OFFICIAL_API_PREFIX}/signin"
        try:
            response = self._post_json_without_cookies(
                url,
                {"username": username, "password": password},
                headers={"Accept-Encoding": "identity"},
                timeout=30,
            )
            data = self._response_json(response, "official signIn")
            raw_body = (response.text or "").lower()
            if response.status_code == 403 and ("cloudflare" in raw_body or "you have been blocked" in raw_body):
                self._official_last_auth_error = "Cloudflare يحظر اتصال Render بخدمة iChancy؛ يجب طلب whitelist لعنوان IP أو استخدام خادم مسموح من iChancy"
            result = data.get("result") if isinstance(data, dict) else None
            if response.status_code != 200 or not isinstance(result, dict):
                logger.warning(
                    "Official signIn failed on %s (HTTP %s): %s",
                    base,
                    response.status_code,
                    self._notification_error(data, "endpoint unavailable or invalid credentials"),
                )
                return False
            access_token = result.get("accessToken")
            refresh_token = result.get("refreshToken")
            if not access_token or not refresh_token:
                logger.warning("Official signIn on %s returned no token pair", base)
                return False
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
        base = str(self._official_api_base_url or self.BASE_URL).rstrip("/")
        url = f"{base}{self.OFFICIAL_API_PREFIX}/refreshToken"
        try:
            response = self._post_json_without_cookies(
                url,
                {"refreshToken": self._official_refresh_token},
                timeout=30,
            )
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
        """Call an API endpoint with Bearer auth and automatic token rotation.

        A decoded API error is returned as a dict; no request falls back to
        browser cookies or a legacy session.
        """
        path = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        if not path.startswith("/global/api/"):
            path = f"{self.OFFICIAL_API_PREFIX}{path}"
        url = f"{str(self._official_api_base_url or self.BASE_URL).rstrip('/')}{path}"
        operation = operation or endpoint
        with self._official_lock:
            if not self._official_access_token or time.time() >= self._official_access_token_expires_at:
                if not self._official_refresh() and not self._official_sign_in():
                    if self._official_last_auth_error:
                        return {"status": False, "result": False, "notification": [{"content": self._official_last_auth_error}]}
                    return None
            for attempt in range(2):
                try:
                    response = self._post_json_without_cookies(
                        url,
                        payload,
                        headers={"Authorization": f"Bearer {self._official_access_token}", "Content-Type": "application/json"},
                        timeout=45,
                    )
                    data = self._response_json(response, operation)
                    invalid = response.status_code == 401 or (
                        isinstance(data, dict)
                        and data.get("result") == "ex"
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
            "limit": 20,
            "filter": {
                "userName": {"action": "=", "value": target_username, "valueLabel": target_username},
            },
        }
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

    def _login_agent(self):
        """Keep the official rotating access/refresh-token pair usable."""
        with self._official_lock:
            if self._official_access_token and time.time() < self._official_access_token_expires_at:
                return True
            return bool(self._official_refresh() or self._official_sign_in())

    def _check_session_validity(self):
        probe = self._official_request(
            "/global/api/UserApi/getAgentAllWallets",
            {},
            "official session check",
        )
        return bool(
            isinstance(probe, dict)
            and probe.get("status") is not False
            and probe.get("result") not in (None, False, "ex")
        )

    def _fetch_player_statistics_page(self, payload):
        data = self._official_request(
            "/global/api/Statistics/getPlayersStatisticsPro",
            payload,
            "player statistics (Bearer API)",
        )
        if not isinstance(data, dict) or data.get("status") is False:
            raise ValueError("Player statistics endpoint failed")
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
        official_payload = {
            "player": {
                "login": username,
                "email": email,
                "password": password,
                "parentId": registration_parent_id,
            }
        }
        official = self._official_request("/global/api/UserApi/registerPlayer", official_payload, "official registerPlayer")
        if not isinstance(official, dict):
            return {"success": False, "error": "تعذر الاتصال بواجهة التسجيل الرسمية في iChancy."}

        result_data = official.get("result")
        registration_player_id = self._extract_player_id(result_data)
        if result_data in (1, "1") or registration_player_id:
            return {
                "success": True,
                "username": username,
                "password": password,
                "email": email,
                "player_id": registration_player_id,
                "response": official,
            }

        error_message = self._notification_error(official, "Registration rejected by iChancy")
        logger.warning(
            "Official registerPlayer rejected request: status=%s result=%r notification=%s",
            official.get("status"),
            result_data,
            error_message,
        )
        return {"success": False, "error": error_message, "response": official}

    def _get_admin_balance(self):
        data = self._official_request(
            "/global/api/UserApi/getAgentAllWallets",
            {},
            "official getAgentAllWallets",
        )
        result = data.get("result") if isinstance(data, dict) else None
        wallets = result if isinstance(result, list) else (
            result.get("records", []) if isinstance(result, dict) else []
        )
        for wallet in wallets:
            if not isinstance(wallet, dict):
                continue
            currency = str(wallet.get("currencyCode") or wallet.get("currency") or "").upper()
            if currency and currency != "NSP":
                continue
            balance = self._extract_balance_from_result(wallet)
            if balance is not None:
                return int(balance)
        logger.warning("Official agent-wallet response did not contain an NSP balance.")
        return None

    def _get_agent_transaction_list(self, from_date, to_date, limit=1000, start=0, is_to_me=False, affiliate_id=None):
        """جلب سجل حركات الكاشيرة/الوكيل من iChancy."""
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
        # This report endpoint is not listed in the supplied API PDF, but it
        # remains part of the app. Use the official Bearer token; never fall
        # back to a browser session or cookie.
        data = self._official_request(
            "/global/api/Agent/getAgentTransactionList",
            payload,
            "agent transaction report (Bearer API)",
        )
        if data is None:
            return {"status": False, "result": {"records": [], "totalRecordsCount": 0}, "error": "API request failed"}
        return data

    def _get_player_balance(self, player_id):
        official = self._official_request("/global/api/UserApi/getPlayerBalanceById", {"playerId": str(player_id)}, "official getPlayerBalanceById")
        if not isinstance(official, dict) or official.get("status") is False:
            return None
        balance = self._extract_balance_from_result(official.get("result"))
        if balance is None:
            logger.warning("Official player-balance response did not contain a balance.")
            return None
        return int(balance)

    def _transfer_money(self, player_id, amount, comment=None):
        official_payload = {"amount": amount, "comment": comment or "", "playerId": str(player_id), "currencyCode": "NSP", "currency": "NSP", "moneyStatus": 5}
        official = self._official_request("/global/api/UserApi/depositToPlayer", official_payload, "official depositToPlayer")
        if isinstance(official, dict) and official.get("result") and official.get("status") is not False:
            logger.info("Official API transferred +%s NSP to Player: %s", amount, player_id)
            return True
        logger.error(
            "Official deposit failed: %s",
            self._notification_error(official if isinstance(official, dict) else None),
        )
        return False

    def _withdraw_money(self, player_id, amount, comment=None):
        official_payload = {"amount": -abs(amount), "comment": comment or "", "playerId": str(player_id), "currencyCode": "NSP", "currency": "NSP", "moneyStatus": 5}
        official = self._official_request("/global/api/UserApi/withdrawFromPlayer", official_payload, "official withdrawFromPlayer")
        if isinstance(official, dict) and official.get("result") and official.get("status") is not False:
            logger.info("Official API withdrew -%s NSP from Player: %s", amount, player_id)
            return True
        logger.error(
            "Official withdrawal failed: %s",
            self._notification_error(official if isinstance(official, dict) else None),
        )
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
