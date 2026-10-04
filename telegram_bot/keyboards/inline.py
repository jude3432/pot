from telegram_bot.keyboards.premium import premium_button
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from config import settings
import database.repository as repo


def get_terms_keyboard():
    keyboard = [
        [premium_button(text="✔️ موافق", callback_data="accept_terms")],
        [premium_button(text="📍 قراءة الشروط", callback_data="show_terms_only")],
        [premium_button(text="⛔ إلغاء", callback_data="reject_terms")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_user_app_url():
    """🌟 Mini App للمستخدم عبر مسار جديد لكسر كاش Telegram WebView نهائياً."""
    base = getattr(settings, 'RENDER_EXTERNAL_URL', 'https://ichancy100.onrender.com')
    return f"{base}/user-app-pingo?v=caesar-handoff-v8-20260715"


def get_guides_url():
    """💬 رابط Mini App الشروحات (مع cache-buster لكسر كاش Telegram WebView)."""
    base = getattr(settings, 'RENDER_EXTERNAL_URL', 'https://ichancy100.onrender.com')
    return f"{base}/guides.html?v=guides-miniapp-v2-20260801"


def get_main_menu_keyboard(is_admin=False):
    keyboard = []
    # User Mini App access is temporarily hidden until its display issue is fixed.
    if is_admin:
        keyboard.append([
            premium_button(text="🛠️ لوحة تحكم الإدارة", callback_data="admin_panel")
        ])

    keyboard.extend([
        [premium_button(text="🧭 حساب iChancy", callback_data="ichancy_menu")],
        [
            premium_button(text="📨 شحن رصيد", callback_data="deposit_bot"),
            premium_button(text="📬 سحب رصيد", callback_data="withdraw_bot")
        ],
        [
            premium_button(text="🧧 إهداء رصيد", callback_data="gift_send"),
            premium_button(text="🎟️ كود هدية", callback_data="gift_redeem")
        ],
        [
            premium_button(text="🪙 الإحالات", callback_data="referral_menu"),
            premium_button(text="🔁 السجل", callback_data="history_menu")
        ],
        [
            premium_button(text="🗂️ رسالة للإدارة", callback_data="message_admin"),
            premium_button(text="📨 تواصل معنا", callback_data="contact_us")
        ],
        [
            premium_button(text="📍 الشروط", callback_data="show_terms_only"),
            premium_button(text="💬 الشروحات", web_app=WebAppInfo(url=get_guides_url()))
        ],
        [
            premium_button(text="✨ مسابقات Jude Robert", callback_data="contests_menu"),
            premium_button(text="🕹️ ألعاب iChancy", web_app=WebAppInfo(url=repo.get_button_link('games_url')))
        ],
        [premium_button(text="🧧 العروض والبونصات", callback_data="offers_menu")],
        [premium_button(text="🏅 المتصدرون الأسبوعيون", callback_data="weekly_leaderboard_menu")],
        [premium_button(text="🎟️ بطاقات التوقع", callback_data="prediction_cards_menu")],
        [
            premium_button(text="🧭 فتح الموقع", web_app=WebAppInfo(url=repo.get_button_link('website_url'))),
            premium_button(text="📲 تحميل التطبيق", url=repo.get_button_link('app_download_url'))
        ],
        [premium_button(text="📚 Facebook البوت", url=repo.get_button_link('betting_url'))]
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_ichancy_submenu(has_account=False):
    keyboard = []

    if not has_account:
        keyboard.append([
            premium_button(text="🌟 إنشاء حساب جديد", callback_data="create_ichancy_account")
        ])
    else:
        keyboard.append([
            premium_button(text="📨 شحن حساب اللعبة", callback_data="deposit_game_acc"),
            premium_button(text="📬 سحب من حساب اللعبة", callback_data="withdraw_game_acc")
        ])

    keyboard.append([
        premium_button(text="🏡 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_prediction_card_options_keyboard(card_id, options, user_id=None):
    rows = []
    for opt in options:
        rows.append([premium_button(text=f"🧿 {opt}", callback_data=f"predict_select:{card_id}:{opt}")])
    rows.append([premium_button(text="🏡 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_prediction_cards_list_keyboard(cards):
    rows = []
    for c in cards:
        label = f"🎟️ #{c.get('id')} {c.get('team_a')} × {c.get('team_b')}"
        rows.append([premium_button(text=label[:64], callback_data=f"prediction_card_detail:{c.get('id')}")])
    rows.append([premium_button(text="🏡 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_contests_list_keyboard(contests):
    rows = []
    for c in contests:
        rows.append([premium_button(text=f"✨ #{c.get('id')} {c.get('title')}"[:64], callback_data=f"contest_detail:{c.get('id')}")])
    rows.append([premium_button(text="🏡 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_contest_submit_keyboard(contest_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [premium_button(text="✔️ إرسال المشاركة", callback_data=f"contest_submit:{contest_id}")],
        [premium_button(text="🏡 العودة للقائمة الرئيسية", callback_data="back_to_main_menu")],
    ])
