"""API Syria integration for automatic Syriatel Cash deposit verification.

The public API documentation defines a single API v1 endpoint authenticated by
X-Api-Key. We use find_tx when the user supplies a transaction number and
history when the user supplies the sender's phone number.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import urlencode

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

BASE_URL = getattr(
    settings,
    "SYRIATEL_API_BASE_URL",
    "https://apisyria.com/api/v1",
).rstrip("?")
DEFAULT_TIMEOUT_SECONDS = 12
MAX_RETRIES = 2


def _normalize_ref(value: str) -> str:
    """Normalize user/API references without changing their semantic value."""
    return str(value or "").strip().replace(" ", "").replace("-", "").upper()


def _normalize_phone(value: str) -> str:
    """Normalize Syrian GSM values while retaining the leading zero."""
    return _normalize_ref(value).replace("+963", "0")


def _is_phone(value: str) -> bool:
    ref = _normalize_phone(value)
    return ref.startswith("09") and len(ref) == 10 and ref.isdigit()


def _parse_amount(value):
    try:
        # API values are documented as strings; reject malformed values rather
        # than accidentally treating them as zero.
        return int(Decimal(str(value).replace(",", "").strip()))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_decimal(value):
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _parse_date(value):
    if not value:
        return None
    text = str(value).strip()
    candidates = (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S%z",
    )
    for fmt in candidates:
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is not None:
                return parsed.astimezone(timezone(timedelta(hours=3))).replace(tzinfo=None)
            return parsed
        except ValueError:
            continue
    return None


def _created_datetime(created_at):
    if not created_at:
        return None
    if isinstance(created_at, datetime):
        if created_at.tzinfo is not None:
            return created_at.astimezone(timezone(timedelta(hours=3))).replace(tzinfo=None)
        return created_at.replace(tzinfo=None)
    return _parse_date(created_at)


def _api_configured():
    token = str(getattr(settings, "SYRIATEL_API_TOKEN", "") or "").strip()
    account = str(getattr(settings, "SYRIATEL_API_QUERY", "") or "").strip()
    return token, account


def _transaction_amount(tx):
    return _parse_amount(tx.get("amount") if tx.get("amount") is not None else tx.get("net"))


def _transaction_reference(tx):
    return _normalize_ref(tx.get("transaction_no") or tx.get("tx") or tx.get("id"))


def _transaction_sender(tx):
    return _normalize_phone(
        tx.get("from") or tx.get("from_gsm") or tx.get("sender") or tx.get("sender_gsm")
    )


def _within_time_window(tx, created_at, tolerance_minutes=180):
    """Allow API/server clock skew while rejecting stale/future transactions."""
    created_dt = _created_datetime(created_at)
    tx_dt = _parse_date(tx.get("date") or tx.get("created_at") or tx.get("timestamp"))
    if not created_dt or not tx_dt:
        return True
    diff_minutes = (tx_dt - created_dt).total_seconds() / 60.0
    # Syria API timestamps are commonly UTC+3, while the bot DB may be UTC.
    return -190 <= diff_minutes <= (int(tolerance_minutes) + 180)


def _extract_items(data):
    payload = data.get("data") if isinstance(data, dict) else {}
    if not isinstance(payload, dict):
        return []
    items = payload.get("items") or payload.get("transactions") or []
    return items if isinstance(items, list) else []


async def _request(params):
    """GET API Syria with header-only secret handling and bounded retries."""
    token = str(getattr(settings, "SYRIATEL_API_TOKEN", "") or "").strip()
    if not token:
        return {"ok": False, "reason": "not_configured"}

    headers = {
        "X-Api-Key": token,
        "Accept": "application/json",
        "User-Agent": "CaesarBot-SyriatelVerifier/1.0",
    }
    timeout = aiohttp.ClientTimeout(total=DEFAULT_TIMEOUT_SECONDS)
    for attempt in range(MAX_RETRIES):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                query = urlencode(params)
                async with session.get(f"{BASE_URL}?{query}", headers=headers) as response:
                    status = response.status
                    data = await response.json(content_type=None)
            if status == 429 or status >= 500:
                if attempt + 1 < MAX_RETRIES:
                    await asyncio.sleep(0.35 * (attempt + 1))
                    continue
            if not isinstance(data, dict):
                return {"ok": False, "reason": "invalid_response", "http_status": status}
            if status >= 400:
                # Do not expose API key or full response to the user/logs.
                return {"ok": False, "reason": "api_http_error", "http_status": status}
            if data.get("success") is not True:
                return {
                    "ok": False,
                    "reason": str(data.get("code") or data.get("message") or "api_error"),
                }
            return {"ok": True, "data": data}
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            if attempt + 1 < MAX_RETRIES:
                await asyncio.sleep(0.35 * (attempt + 1))
                continue
            logger.warning("API Syria verification request failed: %s", type(exc).__name__)
            return {"ok": False, "reason": "network_error"}
        except Exception:
            logger.exception("Unexpected API Syria verification error")
            return {"ok": False, "reason": "api_error"}
    return {"ok": False, "reason": "api_error"}


async def get_incoming_history(query=None, period="7"):
    """Fetch incoming Syriatel history for the configured account."""
    _, configured_account = _api_configured()
    account = str(query or configured_account).strip()
    if not account:
        return {"ok": False, "reason": "not_configured"}
    selected_period = str(period or "7")
    if selected_period not in {"7", "30", "all"}:
        selected_period = "7"
    result = await _request({
        "resource": "syriatel",
        "action": "history",
        "gsm": account,
        "period": selected_period,
    })
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "transactions": _extract_items(result["data"]),
        "raw": result["data"],
    }


async def find_transaction(transaction_number, query=None, period="7"):
    """Find one transaction using API Syria's dedicated find_tx endpoint."""
    _, configured_account = _api_configured()
    account = str(query or configured_account).strip()
    tx_ref = _normalize_ref(transaction_number)
    if not account or not tx_ref:
        return {"ok": False, "reason": "not_configured" if not account else "invalid_reference"}
    if not tx_ref.isdigit() or not 3 <= len(tx_ref) <= 30:
        return {"ok": False, "reason": "invalid_transaction_number"}
    selected_period = str(period or "7")
    if selected_period not in {"7", "30", "all"}:
        selected_period = "7"
    result = await _request({
        "resource": "syriatel",
        "action": "find_tx",
        "tx": tx_ref,
        "gsm": account,
        "period": selected_period,
    })
    if not result.get("ok"):
        return result
    payload = result["data"].get("data") or {}
    if payload.get("found") is not True or not isinstance(payload.get("transaction"), dict):
        return {"ok": False, "reason": "not_found", "raw": result["data"]}
    tx = payload["transaction"]
    return {"ok": True, "transaction": tx, "external_ref": _transaction_reference(tx) or tx_ref, "raw": result["data"]}


def find_matching_transaction(transactions, expected_amount, user_reference, created_at=None, tolerance_minutes=180):
    """Match exact amount plus transaction number or sender GSM."""
    expected = _parse_amount(expected_amount)
    ref = _normalize_ref(user_reference)
    if expected is None or expected < 1 or not ref:
        return {"ok": False, "reason": "invalid_reference"}
    phone_ref = _normalize_phone(user_reference) if _is_phone(user_reference) else None
    for tx in transactions or []:
        if not isinstance(tx, dict) or _transaction_amount(tx) != expected:
            continue
        if phone_ref:
            if _transaction_sender(tx) != phone_ref:
                continue
        elif _transaction_reference(tx) != ref:
            continue
        if not _within_time_window(tx, created_at, tolerance_minutes):
            continue
        external_ref = _transaction_reference(tx) or ref
        return {"ok": True, "transaction": tx, "external_ref": external_ref}
    return {"ok": False, "reason": "not_found"}


async def verify_incoming_deposit(expected_amount, user_reference, created_at=None):
    """Verify a deposit using find_tx for transaction numbers or history for GSM."""
    reference = str(user_reference or "").strip()
    if not reference or reference == "مكتوب داخل الإيصال":
        return {"ok": False, "reason": "reference_required"}

    if _is_phone(reference):
        history = await get_incoming_history(period="7")
        if not history.get("ok"):
            return history
        return find_matching_transaction(
            history.get("transactions") or [],
            expected_amount,
            reference,
            created_at=created_at,
        )

    # API Syria requires a numeric transaction number for find_tx. This avoids
    # downloading a broad history for normal transaction-number verification.
    direct = await find_transaction(reference, period="7")
    if not direct.get("ok"):
        return direct
    tx = direct.get("transaction") or {}
    expected = _parse_amount(expected_amount)
    if _transaction_amount(tx) != expected:
        return {"ok": False, "reason": "amount_mismatch"}
    if not _within_time_window(tx, created_at):
        return {"ok": False, "reason": "outside_time_window"}
    return direct


def _sham_transaction_reference(tx):
    return _normalize_ref(tx.get("tran_id") or tx.get("transaction_no") or tx.get("tx"))


def _sham_transaction_currency(tx):
    return _normalize_ref(tx.get("currency") or tx.get("currency_code"))


def _sham_transaction_amount(tx):
    return _parse_decimal(tx.get("amount") if tx.get("amount") is not None else tx.get("value"))


def _sham_transaction_date(tx):
    return tx.get("datetime") or tx.get("date") or tx.get("created_at")


async def get_shamcash_logs(account_address, period="7"):
    """Fetch incoming ShamCash logs for the exact receiving account."""
    account = str(account_address or "").strip()
    if not account:
        return {"ok": False, "reason": "account_required"}
    result = await _request({
        "resource": "shamcash",
        "action": "logs",
        "account_address": account,
    })
    if not result.get("ok"):
        return result
    payload = result["data"].get("data") or {}
    items = payload.get("items") if isinstance(payload, dict) else []
    return {
        "ok": True,
        "transactions": items if isinstance(items, list) else [],
        "raw": result["data"],
    }


async def find_shamcash_transaction(transaction_number, account_address):
    """Find a ShamCash transaction using API Syria's documented find_tx endpoint."""
    account = str(account_address or "").strip()
    tx_ref = _normalize_ref(transaction_number)
    if not account:
        return {"ok": False, "reason": "account_required"}
    if not tx_ref.isdigit() or not 3 <= len(tx_ref) <= 30:
        return {"ok": False, "reason": "invalid_transaction_number"}
    result = await _request({
        "resource": "shamcash",
        "action": "find_tx",
        "tx": tx_ref,
        "account_address": account,
    })
    if not result.get("ok"):
        return result
    payload = result["data"].get("data") or {}
    if payload.get("found") is not True or not isinstance(payload.get("transaction"), dict):
        return {"ok": False, "reason": "not_found", "raw": result["data"]}
    tx = payload["transaction"]
    return {
        "ok": True,
        "transaction": tx,
        "external_ref": _sham_transaction_reference(tx) or tx_ref,
        "raw": result["data"],
    }


def find_matching_shamcash_transaction(
    transactions, expected_amount, expected_currency, created_at=None, user_reference=None
):
    """Match ShamCash amount/currency, optionally by transaction number."""
    expected = _parse_decimal(expected_amount)
    currency = _normalize_ref(expected_currency)
    reference = _normalize_ref(user_reference)
    if expected is None or expected < 1 or currency not in {"SYP", "USD", "EUR"}:
        return {"ok": False, "reason": "invalid_reference"}
    for tx in transactions or []:
        if not isinstance(tx, dict):
            continue
        if _sham_transaction_amount(tx) != expected:
            continue
        if _sham_transaction_currency(tx) != currency:
            continue
        if reference and _sham_transaction_reference(tx) != reference:
            continue
        if not _within_time_window({"date": _sham_transaction_date(tx)}, created_at):
            continue
        external_ref = _sham_transaction_reference(tx) or reference
        return {"ok": True, "transaction": tx, "external_ref": external_ref}
    return {"ok": False, "reason": "not_found"}


async def verify_shamcash_deposit(
    expected_amount, expected_currency, user_reference, account_address, created_at=None
):
    """Verify ShamCash using find_tx for a transaction number or logs otherwise."""
    reference = str(user_reference or "").strip()
    if not reference or reference == "مكتوب داخل الإيصال":
        return {"ok": False, "reason": "reference_required"}
    if reference.isdigit() and 3 <= len(_normalize_ref(reference)) <= 30:
        direct = await find_shamcash_transaction(reference, account_address)
        if not direct.get("ok"):
            return direct
        tx = direct.get("transaction") or {}
        if _sham_transaction_amount(tx) != _parse_decimal(expected_amount):
            return {"ok": False, "reason": "amount_mismatch"}
        if _sham_transaction_currency(tx) != _normalize_ref(expected_currency):
            return {"ok": False, "reason": "currency_mismatch"}
        if not _within_time_window({"date": _sham_transaction_date(tx)}, created_at):
            return {"ok": False, "reason": "outside_time_window"}
        return direct
    logs = await get_shamcash_logs(account_address, period="7")
    if not logs.get("ok"):
        return logs
    return find_matching_shamcash_transaction(
        logs.get("transactions") or [], expected_amount, expected_currency,
        created_at=created_at, user_reference=reference,
    )
