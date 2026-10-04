from telegram_bot.keyboards.premium import premium_button, PREMIUM_EMOJI
import asyncio
import html
import logging
import re
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.filters import Command, CommandObject
from config import settings
from config.currency import format_new
import database.repository as repo
from telegram_bot.keyboards.inline import (
    get_main_menu_keyboard,
    get_terms_keyboard,
)
from telegram_bot.middlewares.terms_check import (
    get_terms_text,
    is_force_subscribed,
    invalidate_subscription_cache,
)
from telegram_bot.miniapp_shortcuts import resolve_miniapp_shortcut

router = Router()
logger = logging.getLogger(__name__)

# 🛠️ إصلاح: قائمة الأدمن مع التسامح مع عدم وجود ADMIN_IDS في الإعدادات
ADMIN_IDS = [item.strip() for item in str(getattr(settings, "ADMIN_IDS", settings.ADMIN_ID)).split(",") if item.strip()]


def is_admin_user(user_id) -> bool:
    return str(user_id) in ADMIN_IDS


def get_user_menu_keyboard(user_id):
    return get_main_menu_keyboard(is_admin=is_admin_user(user_id))


async def send_log_message(bot, text, parse_mode="HTML"):
    """إرسال رسالة سجل إلى قناة Log"""
    log_channel_id = getattr(settings, "LOG_CHANNEL_ID", None)
    if not log_channel_id:
        return False
    try:
        await bot.send_message(chat_id=log_channel_id, text=text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logger.error(f"⛔ send_log_message failed: {e}")
        return False


# ================================================================
# 🔹 دالة موحّدة لعرض القائمة الرئيسية الكاملة (15 زراً)
# ================================================================
async def show_main_menu(message: Message, user_id, edit: bool = False, user_record=None):
    """تعرض بطاقة الحساب الحية والقائمة الرئيسية الكاملة."""
    if user_record is None:
        user, history_rows = await asyncio.gather(
            asyncio.to_thread(repo.get_user, str(user_id)),
            asyncio.to_thread(repo.get_user_transactions_history, str(user_id), 1),
        )
    else:
        user = user_record
        try:
            history_rows = await asyncio.to_thread(repo.get_user_transactions_history, str(user_id), 1)
        except Exception as exc:
            logger.warning("Could not load last operation for welcome card: %s", exc)
            history_rows = []
    bot_balance = int(user['bot_balance']) if user and user.get('bot_balance') is not None else 0
    # game_balance is already returned in the users row; avoid a second get_user query.
    game_balance = int(user.get('game_balance') or 0) if user else 0
    bot_balance_new_str = format_new(bot_balance)
    telegram_username = str((user or {}).get('telegram_username') or '').strip()
    ichancy_username = str((user or {}).get('ichancy_username') or '').strip()
    player_id = str((user or {}).get('player_id') or '').strip()
    last = history_rows[0] if history_rows else None

    type_labels = {
        'deposit_bot': 'شحن رصيد البوت',
        'withdraw_bot': 'سحب من رصيد البوت',
        'deposit_game': 'شحن حساب iChancy',
        'game_deposit': 'شحن حساب iChancy',
        'withdraw_game': 'سحب من حساب iChancy',
        'game_withdraw': 'سحب من حساب iChancy',
        'gift_send': 'إهداء رصيد',
        'gift_redeem': 'استرداد كود هدية',
    }
    status_labels = {'approved': 'مكتملة', 'pending': 'قيد المعالجة', 'rejected': 'مرفوضة', 'failed': 'فشلت'}
    if last:
        operation_name = type_labels.get(str(last.get('type') or ''), str(last.get('type') or 'عملية'))
        operation_status = status_labels.get(str(last.get('status') or '').lower(), str(last.get('status') or 'غير معروف'))
        operation_amount = last.get('converted_amount_syp') or last.get('amount') or 0
        operation_date = last.get('created_at')
        operation_date = operation_date.strftime('%Y-%m-%d %H:%M') if hasattr(operation_date, 'strftime') else ''
        last_operation = f"{operation_name} — {int(operation_amount):,} ل.س — {operation_status}"
        if operation_date:
            last_operation += f" — {operation_date}"
    else:
        last_operation = "لا توجد عمليات مسجلة حتى الآن"

    def ce(name: str, fallback: str) -> str:
        return f'<tg-emoji emoji-id="{PREMIUM_EMOJI[name]}">{fallback}</tg-emoji>'

    account_line = html.escape(f"@{telegram_username}" if telegram_username else "بدون اسم مستخدم")
    ichancy_line = html.escape(ichancy_username if ichancy_username else "غير مربوط")
    player_line = html.escape(player_id if player_id else "غير متوفر")
    card_rule = "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    text = (
        f"{card_rule}\n"
        f"{ce('ichancy', '⚡')} <b>أهلاً بك في Jude Robert</b>\n"
        f"<i>بطاقة حسابك الرقمية — كل معلوماتك في مكان واحد</i>\n"
        f"{card_rule}\n\n"
        f"{ce('balance', '🔷')} <b>رصيد البوت</b>\n"
        f"<code>{bot_balance:,} ل.س</code>  <i>({bot_balance_new_str} ل.س جديدة)</i>\n\n"
        f"{ce('game', '🕹️')} <b>رصيد اللعبة — iChancy</b>\n"
        f"<code>{game_balance:,} NSP</code>\n\n"
        f"{ce('account', '🆔')} <b>Telegram ID:</b> <code>{user_id}</code>\n"
        f"{ce('account', '👤')} <b>الحساب:</b> {account_line}\n"
        f"{ce('game', '🎮')} <b>iChancy ID:</b> <code>{player_line}</code>\n"
        f"{ce('account', '🔗')} <b>اسم حساب iChancy:</b> <code>{ichancy_line}</code>\n\n"
        f"{ce('history', '🕘')} <b>آخر عملية</b>\n"
        f"<i>{html.escape(last_operation)}</i>\n\n"
        f"{ce('website', '🌐')} <b>اختر الخدمة المطلوبة من الأزرار بالأسفل</b>\n"
        f"{card_rule}"
    )

    keyboard = get_user_menu_keyboard(user_id)

    if edit:
        try:
            await message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            return
        except Exception:
            # Keep the account card visible if Telegram rejects a custom entity.
            plain_text = re.sub(r'<tg-emoji[^>]*>.*?</tg-emoji>', '', text)
            try:
                await message.edit_text(plain_text, reply_markup=keyboard, parse_mode="HTML")
                return
            except Exception:
                pass
    try:
        await message.answer(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as exc:
        logger.warning("Premium welcome-card entities were rejected: %s", exc)
        # No ordinary-emoji fallback: preserve the text card without an orphaned banner.
        plain_text = re.sub(r'<tg-emoji[^>]*>.*?</tg-emoji>', '', text)
        try:
            await message.answer(plain_text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as fallback_exc:
            logger.error("Welcome-card fallback failed: %s", fallback_exc)


async def open_miniapp_shortcut_flow(message: Message, user_id, state: FSMContext, action: str):
    """Route a whitelisted Mini App shortcut into the existing safe bot flow."""
    # Local import avoids coupling the router modules during application startup.
    from telegram_bot.handlers.menu import (
        start_deposit_flow,
        start_gift_flow,
        start_withdraw_flow,
    )

    openers = {
        'deposit': start_deposit_flow,
        'withdraw': start_withdraw_flow,
        'gift': start_gift_flow,
    }
    opener = openers.get(action)
    if not opener:
        return False
    await opener(message, user_id, state, edit=False)
    return True


# ================================================================
# ✔️ معالجات الموافقة على الشروط (كانت مفقودة بالكامل!)
# ================================================================

@router.callback_query(F.data == "accept_terms")
async def accept_terms_callback(callback: CallbackQuery):
    """→ الإصلاح الأهم: عند الضغط على 'موافق' يتم تسجيل القبول وعرض القائمة الرئيسية."""
    telegram_id = str(callback.from_user.id)
    user = await asyncio.to_thread(repo.get_user, telegram_id)
    if not user:
        await asyncio.to_thread(repo.create_user, telegram_id, callback.from_user.username)
    await asyncio.to_thread(repo.update_user_terms, telegram_id, accepted=True)

    try:
        await callback.message.delete()
    except Exception:
        pass

    await show_main_menu(callback.message, callback.from_user.id)
    await callback.answer("✔️ شكراً لموافقتك على الشروط!")


@router.callback_query(F.data == "reject_terms")
async def reject_terms_callback(callback: CallbackQuery):
    """عند رفض الشروط."""
    await callback.message.edit_text(
        "⛔ <b>تم رفض الشروط.</b>\n\nلا يمكنك استخدام البوت دون الموافقة على الشروط.\n"
        "للمحاولة مجدداً اضغط على /start",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            premium_button(text="🔁 المحاولة مجدداً", callback_data="show_terms_only")
        ]])
    )
    await callback.answer()


@router.callback_query(F.data == "show_terms_only")
async def show_terms_only_callback(callback: CallbackQuery):
    """عرض نص الشروط مع زر الموافقة."""
    terms_text = get_terms_text()
    await callback.message.edit_text(terms_text, reply_markup=get_terms_keyboard(), parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "force_sub_check")
async def force_subscription_check_callback(callback: CallbackQuery):
    """التحقق من الاشتراك ثم عرض الشروط أو القائمة الرئيسية."""
    telegram_id = str(callback.from_user.id)
    invalidate_subscription_cache(telegram_id)
    if not await is_force_subscribed(callback.bot, telegram_id):
        await callback.answer("لم يتم العثور على اشتراكك بعد. اشترك ثم حاول مرة أخرى.", show_alert=True)
        return

    user = await asyncio.to_thread(repo.get_user, telegram_id)
    if user and user.get('terms_accepted'):
        await show_main_menu(callback.message, telegram_id, edit=True, user_record=user)
    else:
        await callback.message.edit_text(
            get_terms_text(),
            reply_markup=get_terms_keyboard(),
            parse_mode='HTML',
        )
    await callback.answer("✅ تم التحقق من اشتراكك بنجاح")


# ================================================================
# ✔️ أمر /start — يعرض القائمة الرئيسية دائماً
# ================================================================
@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext, command: CommandObject, terms_user=None):
    """معالج أمر البدء — يعرض القائمة الرئيسية دائماً."""
    await state.clear()
    user_id = message.from_user.id
    telegram_id = str(user_id)

    args = (command.args or '').strip()
    shortcut_action = resolve_miniapp_shortcut(args)

    # معالجة الإحالة: /start ref_123456
    if args.startswith("ref_"):
        referrer_id = args[4:].strip()
        existing = await asyncio.to_thread(repo.get_user, telegram_id)
        if not existing:
            await asyncio.to_thread(repo.create_user, telegram_id, message.from_user.username)
        if referrer_id and referrer_id != telegram_id:
            await asyncio.to_thread(repo.add_referral, referrer_id, telegram_id)

    # Reuse the middleware record when available to avoid a duplicate query.
    user = terms_user or await asyncio.to_thread(repo.get_user, telegram_id)
    if not user:
        await asyncio.to_thread(repo.create_user, telegram_id, message.from_user.username)
        user = await asyncio.to_thread(repo.get_user, telegram_id)

    # المستخدم العادي لا يرى القائمة الرئيسية قبل قبول الشروط
    if not is_admin_user(user_id) and user and not user.get('terms_accepted'):
        await message.answer(get_terms_text(), reply_markup=get_terms_keyboard(), parse_mode="HTML")
        return

    # اختصارات Mini App لا تنفذ أي حركة مالية؛ تفتح فقط أول شاشة
    # من مسار البوت الحالي بعد التحقق من المستخدم والشروط.
    if shortcut_action:
        await open_miniapp_shortcut_flow(message, user_id, state, shortcut_action)
        return

    await show_main_menu(message, user_id, user_record=user)


# ================================================================
# ✔️ معالجات الأوامر المنشورة في قائمة الأوامر (كانت بلا معالجات)
# ================================================================

@router.message(Command("home"))
async def cmd_home(message: Message, state: FSMContext):
    """العودة إلى القائمة الرئيسية."""
    await state.clear()
    await show_main_menu(message, message.from_user.id)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    """إلغاء أي عملية جارية والعودة للقائمة الرئيسية."""
    await state.clear()
    await show_main_menu(message, message.from_user.id)


@router.message(Command("delete"))
async def cmd_delete(message: Message):
    """حذف الحساب مع تأكيد."""
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        premium_button(text="🧹 نعم، احذف حسابي نهائياً", callback_data="delete_confirm"),
        premium_button(text="⛔ إلغاء", callback_data="delete_cancel"),
    ]])
    await message.answer(
        "🚧 <b>هل أنت متأكد من حذف حسابك؟</b>\n\n"
        "سيتم حذف جميع بياناتك وأرصدتك ومعاملاتك نهائياً ولا يمكن التراجع.",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


@router.callback_query(F.data == "delete_confirm")
async def delete_confirm_callback(callback: CallbackQuery):
    telegram_id = str(callback.from_user.id)
    await asyncio.to_thread(repo.delete_user_completely, telegram_id)
    # 🌟 (Update 20 / Perf) إبطال كاش قبول الشروط فوراً حتى لا يمر حساب محذوف من الميدلوير
    from telegram_bot.middlewares.terms_check import invalidate_terms_cache
    invalidate_terms_cache(telegram_id)
    await callback.message.edit_text(
        "🧹 <b>تم حذف حسابك بنجاح.</b>\n\nللبدء من جديد اضغط على /start",
        parse_mode="HTML"
    )
    await callback.answer("تم حذف الحساب.")


@router.callback_query(F.data == "delete_cancel")
async def delete_cancel_callback(callback: CallbackQuery):
    await show_main_menu(callback.message, callback.from_user.id, edit=True)
    await callback.answer("تم الإلغاء.")


# ================================================================
# ✔️ بيانات Mini App (web_app_data) — زر "العودة للقائمة الرئيسية"
# ================================================================
# عندما يضغط المستخدم زر "🏡 العودة للقائمة الرئيسية" داخل Mini App
# الشروحات، يرسل الـ Mini App البيانات '/start' عبر WebApp.sendData()،
# فيستقبلها البوت هنا كرسالة من نوع web_app_data ويعرض القائمة مباشرة.

@router.message(F.web_app_data)
async def handle_web_app_data(message: Message):
    data = ""
    try:
        data = (message.web_app_data.data or "").strip()
    except Exception:
        pass
    telegram_id = str(message.from_user.id)
    user = await asyncio.to_thread(repo.get_user, telegram_id)
    if data in ("/start", "main_menu"):
        if user and user.get("terms_accepted"):
            await show_main_menu(message, telegram_id, edit=False)
        else:
            await message.answer(get_terms_text(), reply_markup=get_terms_keyboard(), parse_mode="HTML")
