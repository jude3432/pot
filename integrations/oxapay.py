"""OxaPay merchant integration for in-chat USDT deposits."""
import hashlib
import hmac
import json
import logging
import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger(__name__)

API_BASE = os.getenv("OXAPAY_API_BASE", "https://api.oxapay.com/v1").rstrip("/")
MERCHANT_API_KEY = os.getenv("OXAPAY_MERCHANT_API_KEY", "").strip()
CALLBACK_URL = os.getenv("OXAPAY_WEBHOOK_URL", "").strip()
FEE_PAID_BY_PAYER = 1 if os.getenv("OXAPAY_FEE_PAID_BY_PAYER", "1").strip().lower() in ("1", "true", "yes") else 0
DEPOSIT_LOG_CHANNEL_ID = os.getenv("OXAPAY_DEPOSIT_LOG_CHANNEL_ID", "").strip()

# OxaPay network labels are configurable because providers may rename them.
NETWORKS = {
    "polygon": os.getenv("OXAPAY_NETWORK_POLYGON", "Polygon"),
    "trc20": os.getenv("OXAPAY_NETWORK_TRC20", "TRC20"),
    "bep20": os.getenv("OXAPAY_NETWORK_BEP20", "BEP20"),
}
# OxaPay's payment endpoint accepts short network identifiers. Keep aliases
# here so old Render environment values remain compatible after deployment.
NETWORK_ALIASES = {
    "bsc network": "BEP20",
    "bsc": "BEP20",
    "bep20": "BEP20",
    "bep-20": "BEP20",
    "binance smart chain": "BEP20",
    "binance smart chain network": "BEP20",
    "trc20": "TRC20",
    "trc-20": "TRC20",
    "tron": "TRC20",
    "tron network": "TRC20",
    "polygon": "Polygon",
    "polygon network": "Polygon",
}
NETWORK_KEY_ALIASES = {
    "trc": "trc20",
    "trc20": "trc20",
    "trc-20": "trc20",
    "bep": "bep20",
    "bep20": "bep20",
    "bep-20": "bep20",
    "polygon": "polygon",
}
NETWORK_LABELS = {
    "polygon": "USDT Polygon",
    "trc20": "USDT TRC20",
    "bep20": "USDT BEP20",
}


def is_configured() -> bool:
    return configuration_error() is None


def configuration_error() -> str | None:
    """Return a safe, actionable configuration error without exposing secrets."""
    if not MERCHANT_API_KEY:
        return "OXAPAY_MERCHANT_API_KEY is missing"
    parsed = urlparse(CALLBACK_URL)
    if parsed.scheme != "https" or not parsed.netloc:
        return "OXAPAY_WEBHOOK_URL must be a public HTTPS URL"
    if "your-service-name" in CALLBACK_URL.lower():
        return "OXAPAY_WEBHOOK_URL still contains the example placeholder"
    return None


def network_name(key: str) -> str | None:
    normalized_key = str(key or "").strip().lower()
    normalized_key = NETWORK_KEY_ALIASES.get(normalized_key, normalized_key)
    configured = NETWORKS.get(normalized_key)
    if not configured:
        return None
    return NETWORK_ALIASES.get(configured.strip().lower(), configured.strip())


def network_label(key: str) -> str:
    return NETWORK_LABELS.get(str(key or "").lower(), str(key or "USDT"))


def verify_hmac(raw_body: bytes, signature: str | None) -> bool:
    if not MERCHANT_API_KEY or not signature:
        return False
    expected = hmac.new(MERCHANT_API_KEY.encode(), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(expected, signature.strip())


async def create_white_label_payment(*, amount_usdt: Decimal, network: str, order_id: str, description: str) -> dict:
    """Create an OxaPay payment that returns the address directly (no hosted page)."""
    config_error = configuration_error()
    if config_error:
        raise RuntimeError(f"OxaPay configuration error: {config_error}")
    ox_network = network_name(network)
    if not ox_network:
        raise ValueError("Unsupported OxaPay network")
    try:
        amount = Decimal(str(amount_usdt)).quantize(Decimal("0.00000001"))
    except (InvalidOperation, ValueError):
        raise ValueError("Invalid USDT amount")
    if amount <= 0:
        raise ValueError("USDT amount must be positive")

    payload = {
        "pay_currency": "USDT",
        "network": ox_network,
        "amount": float(amount),
        "currency": "USDT",
        "lifetime": int(os.getenv("OXAPAY_PAYMENT_LIFETIME_MINUTES", "120")),
        # The payer covers OxaPay/network fees; the merchant receives the requested amount.
        "fee_paid_by_payer": FEE_PAID_BY_PAYER,
        "under_paid_coverage": 0,
        "auto_withdrawal": False,
        "callback_url": CALLBACK_URL,
        "order_id": str(order_id),
        "description": description[:255],
    }
    timeout = aiohttp.ClientTimeout(total=12, connect=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{API_BASE}/payment/white-label",
            json=payload,
            headers={"merchant_api_key": MERCHANT_API_KEY, "Content-Type": "application/json"},
        ) as response:
            try:
                body = await response.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as exc:
                raw_body = (await response.text())[:500]
                raise RuntimeError(
                    f"OxaPay returned non-JSON response (HTTP {response.status}): {raw_body}"
                ) from exc

            try:
                provider_status = int(body.get("status") or response.status)
            except (TypeError, ValueError):
                provider_status = response.status
            if response.status >= 400 or provider_status >= 400:
                error = body.get("error")
                if isinstance(error, dict):
                    error = error.get("message") or error.get("key") or error.get("type")
                error = error or body.get("message") or f"HTTP {response.status}"
                raise RuntimeError(f"OxaPay error ({provider_status}): {error}")
            data = body.get("data") or {}
            required = (data.get("track_id"), data.get("address"), data.get("pay_amount"))
            if not all(required):
                raise RuntimeError(
                    "OxaPay returned incomplete payment data: "
                    f"track_id={bool(data.get('track_id'))}, "
                    f"address={bool(data.get('address'))}, "
                    f"pay_amount={bool(data.get('pay_amount'))}"
                )
            return data


def parse_callback(raw_body: bytes, signature: str | None) -> dict:
    if not verify_hmac(raw_body, signature):
        raise PermissionError("Invalid OxaPay HMAC signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid OxaPay JSON payload") from exc
    return payload
