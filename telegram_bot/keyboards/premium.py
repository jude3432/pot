"""Telegram Custom Emoji icons for buttons.

The IDs come from the supplied NewsEmoji/FinanceEmoji collections.  Telegram
renders these through ``icon_custom_emoji_id``; the wrapper falls back to a
regular button when running against an older aiogram release or an ineligible
bot account, so keyboard actions never break.
"""
from __future__ import annotations
import os
import re
from aiogram.types import InlineKeyboardButton

# Telegram only renders button custom icons for eligible bot accounts. Keep
# the familiar visible emoji by default; enable premium-only mode explicitly
# after confirming the bot owner/account eligibility.
CUSTOM_EMOJI_BUTTONS_ENABLED = os.getenv(
    "TELEGRAM_CUSTOM_EMOJI_BUTTONS", "true"
).strip().lower() in {"1", "true", "yes", "on"}

# Curated semantic choices. Finance IDs are preferred for money actions;
# NewsEmoji IDs are used for navigation/status/admin actions.
PREMIUM_EMOJI = {
    "brand": "5438496463044752972",       # ⭐
    "home": "5416041192905265756",        # 🏠
    "back": "5416117059207572332",        # ➡️
    "settings": "5341715473882955310",    # ⚙️
    "admin": "5341715473882955310",       # ⚙️
    "balance": "5287231198098117669",     # 💰 Finance
    "wallet": "5445221832074483553",      # 💼 Finance
    "deposit": "5445355530111437729",    # 📤 Finance
    "withdraw": "5443127283898405358",   # 📥 Finance
    "payment": "5445353829304387411",    # 💳 Finance
    "success": "5206607081334906820",    # ✔️
    "cancel": "5260293700088511294",     # ⛔
    "error": "5210952531676504517",       # ❌
    "warning": "5447644880824181073",    # ⚠️
    "info": "5334544901428229844",       # ℹ️
    "gift": "5294167145079395967",       # 🛍 Finance
    "bonus": "5427168083074628963",      # 💎
    "game": "5361741454685256344",       # 🎮
    "account": "5332724926216428039",    # 📇 Finance
    "referrals": "5271837459783638319",  # ↔️ Finance
    "leaderboard": "5440539497383087970",# 🥇
    "contest": "5461151367559141950",    # 🎉
    "support": "5443038326535759644",    # 💬
    "contact": "5253742260054409879",    # ✉️
    "guide": "5222444124698853913",      # 📖
    "website": "5447410659077661506",    # 🌐
    "download": "5406745015365943482",   # 🔽
    "confirm": "5206607081334906820",    # ✔️
    "loading": "5386367538735104399",    # 🕐
    "security": "5197288647275071607",   # 🔒 Finance
    "maintenance": "5341715473882955310",# ⚙️
    "search": "5231012545799666522",     # 🔍
    "document": "5444856076954520455",   # 🧾 Finance
    "prediction": "5310278924616356636", # 🎯 Finance
    "history": "5274055917766202507",    # 📅 Finance
}

# Prefixes are intentionally explicit. When Telegram accepts the premium
# icon, the matching legacy glyph is removed from the label to avoid doubles.
PREFIX_TO_ICON = {
    "✔": "success", "✅": "success", "⛔": "cancel", "❌": "error",
    "⚠": "warning", "❗": "warning", "ℹ": "info", "💰": "balance",
    "💵": "balance", "💲": "balance", "💳": "payment", "📤": "deposit",
    "📥": "withdraw", "📬": "withdraw", "🔽": "withdraw", "⬇": "withdraw", "🔼": "deposit",
    "⬆": "deposit", "🪙": "balance", "💼": "wallet", "🧧": "gift",
    "🎁": "gift", "💎": "bonus", "🎮": "game", "🕹": "game", "👾": "game",
    "👤": "account", "📇": "account", "🧭": "account", "🤝": "referrals",
    "🪙": "balance", "🏆": "leaderboard", "🥇": "leaderboard", "🏅": "leaderboard",
    "🎉": "contest", "✨": "contest", "💬": "support", "📨": "contact",
    "✉": "contact", "📖": "guide", "📚": "guide", "🌐": "website",
    "🔗": "website", "📲": "download", "⚙": "settings", "🔧": "settings",
    "🔒": "security", "🔐": "security", "🛡": "security", "🔍": "search",
    "🧾": "document", "🎯": "prediction", "📅": "history", "🗓": "history",
    "🏠": "home", "🏡": "home", "↩": "back", "➡": "back", "🔄": "loading",
    "⏳": "loading", "🕒": "loading", "🕐": "loading", "🧪": "maintenance",
    "🎟": "gift", "🗂": "guide", "📌": "info", "🔻": "withdraw", "🔁": "loading",
    "🧑‍💼": "account", "🛠": "settings", "◈": "brand",
}

# Longest-first avoids matching a short prefix inside a compound emoji.
_PREFIXES = sorted(PREFIX_TO_ICON, key=len, reverse=True)
def _icon_for_text(text: object) -> str | None:
    value = str(text or "")
    # Meaning wins over a reused visible emoji (e.g. 📨 شحن vs 📨 تواصل).
    if "شحن" in value or "إيداع" in value:
        return PREMIUM_EMOJI["deposit"]
    if "سحب" in value:
        return PREMIUM_EMOJI["withdraw"]
    if "إحالة" in value or "الإحالات" in value:
        return PREMIUM_EMOJI["referrals"]
    if "سجل" in value or "تاريخ" in value:
        return PREMIUM_EMOJI["history"]
    if "دعم" in value:
        return PREMIUM_EMOJI["support"]
    if "تواصل" in value or "رسالة" in value:
        return PREMIUM_EMOJI["contact"]
    for prefix in _PREFIXES:
        if value.lstrip().startswith(prefix):
            return PREMIUM_EMOJI[PREFIX_TO_ICON[prefix]]
    lowered = value.lower()
    if any(x in lowered for x in ("تأكيد", "موافق", "اعتماد", "إرسال")):
        return PREMIUM_EMOJI["confirm"]
    if any(x in value for x in ("إلغاء", "رفض", "إغلاق")):
        return PREMIUM_EMOJI["cancel"]
    return None

def _remove_icon_prefix(text: str) -> str:
    """Remove only the mapped leading glyph, including variation selectors."""
    value = text.lstrip()
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            rest = value[len(prefix):].lstrip("\ufe0f\u200d\u20e3")
            return rest.lstrip()
    return text

def _button_style(text: object, kwargs: dict) -> str | None:
    """Apply a decorative, balanced Telegram-native palette by action family."""
    if kwargs.get("style"):
        return None
    value = str(text or "")
    callback = str(kwargs.get("callback_data") or "").lower()
    if callback in {"history_menu", "message_admin", "contact_us", "show_terms_only", "back_to_main_menu"}:
        return "default"
    if callback in {"gift_redeem"}:
        return "primary"
    if any(word in value for word in ("شحن", "إيداع", "تأكيد", "موافق", "إنشاء", "إهداء", "العروض", "مسابقات", "تحميل")):
        return "success"
    if any(word in value for word in ("سحب", "إلغاء", "رفض", "حذف", "إغلاق")):
        return "danger"
    if "deposit" in callback or any(word in callback for word in ("accept", "confirm", "create", "gift_send", "offer", "contest_submit")):
        return "success"
    if "withdraw" in callback or any(word in callback for word in ("cancel", "reject", "delete", "close")):
        return "danger"
    if any(word in value for word in (
        "حساب", "لوحة", "إحالات", "الشروحات", "ألعاب", "المتصدرون", "بطاقات",
        "الموقع", "دعم", "معلومات", "عودة", "بحث",
    )) or any(word in callback for word in (
        "menu", "account", "referral", "support", "guide", "game", "leaderboard",
        "prediction", "website",
    )):
        return "primary"
    return "default"

def premium_button(*, text: str, **kwargs):
    """Build a button with a premium icon when Telegram/aiogram supports it."""
    requested_icon = kwargs.pop("icon_custom_emoji_id", None) or _icon_for_text(text)
    icon_id = requested_icon if CUSTOM_EMOJI_BUTTONS_ENABLED else None
    style = _button_style(text, kwargs)
    if style:
        kwargs["style"] = style
    if icon_id:
        try:
            return InlineKeyboardButton(
                text=_remove_icon_prefix(str(text)),
                icon_custom_emoji_id=icon_id,
                **kwargs,
            )
        except (TypeError, ValueError):
            # Older aiogram or an API model without Bot API 9.4 support.
            kwargs.pop("style", None)
            pass
    try:
        return InlineKeyboardButton(text=text, **kwargs)
    except (TypeError, ValueError):
        # Preserve action compatibility if style is unsupported by an older model.
        kwargs.pop("style", None)
        return InlineKeyboardButton(text=text, **kwargs)
