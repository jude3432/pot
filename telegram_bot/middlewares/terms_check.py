import asyncio
import logging
import time
from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup
from typing import Callable, Dict, Any, Awaitable
import database.repository as repo
from config import settings
from telegram_bot.keyboards.premium import premium_button, PREMIUM_EMOJI

logger = logging.getLogger(__name__)


# 🛠️ إصلاح: قائمة الأدمن ليتجاوزوا فحص الشروط
ADMIN_IDS = [item.strip() for item in str(getattr(settings, "ADMIN_IDS", settings.ADMIN_ID)).split(",") if item.strip()]

# 🌟 (Update 20 / Perf) كاش قبول الشروط الإيجابي:
# كان الميدلوير ينفّذ get_user على كل رسالة وكل نقرة زر لأي مستخدم.
# نخزّن القبول فقط (الاتجاه الآمن): غير المقبول يُفحص دائماً من القاعدة،
# فلا يوجد أي تأخير على من وافق للتو، والحذف يُبطل الكاش صراحةً.
_TERMS_TTL = 60.0
_terms_accepted_cache = {}  # telegram_id -> expires_at
_SUBSCRIPTION_TTL = max(30, int(getattr(settings, 'FORCE_SUBSCRIPTION_CACHE_TTL_SECONDS', 300)))
_SUBSCRIPTION_NEGATIVE_TTL = max(
    0,
    int(getattr(settings, 'FORCE_SUBSCRIPTION_NEGATIVE_CACHE_TTL_SECONDS', 8)),
)
_subscription_cache = {}  # telegram_id -> (subscribed, expires_at)
_USER_STATUS_TTL = 30.0
_user_status_cache = {}  # telegram_id -> (is_banned, terms_accepted, expires_at)


def invalidate_terms_cache(telegram_id=None):
    """إبطال كاش القبول (يُستدعى بعد حذف الحساب أو تصفير القاعدة)."""
    if telegram_id is None:
        _terms_accepted_cache.clear()
    else:
        _terms_accepted_cache.pop(str(telegram_id), None)


def _is_admin(user_id) -> bool:
    return str(user_id) in ADMIN_IDS


def invalidate_subscription_cache(telegram_id=None):
    if telegram_id is None:
        _subscription_cache.clear()
    else:
        _subscription_cache.pop(str(telegram_id), None)


def invalidate_user_status_cache(telegram_id=None):
    """إبطال كاش حالة المستخدم فور تغييرات الإدارة."""
    if telegram_id is None:
        _user_status_cache.clear()
    else:
        _user_status_cache.pop(str(telegram_id), None)


async def is_force_subscribed(bot, telegram_id) -> bool:
    chat_id = str(getattr(settings, 'FORCE_SUBSCRIPTION_CHAT_ID', '') or '').strip()
    if not chat_id:
        return True
    tid = str(telegram_id)
    now = time.time()
    cached = _subscription_cache.get(tid)
    if isinstance(cached, tuple):
        subscribed, expires_at = cached
        if expires_at > now:
            return bool(subscribed)
    elif cached and cached > now:
        # Backward compatibility with a cache entry made before this deploy.
        return True
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=int(tid))
        status = str(member.status)
        subscribed = status in {'creator', 'administrator', 'member'} or (
            status == 'restricted' and bool(getattr(member, 'is_member', False))
        )
        ttl = _SUBSCRIPTION_TTL if subscribed else _SUBSCRIPTION_NEGATIVE_TTL
        if ttl:
            _subscription_cache[tid] = (subscribed, time.time() + ttl)
        return subscribed
    except Exception:
        logger.exception('Force-subscription membership check failed for chat %s', chat_id)
        return False


def get_force_subscription_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [premium_button(
            text='📢 اشترك بالقناة الآن',
            url=getattr(settings, 'FORCE_SUBSCRIPTION_INVITE_URL', 'https://t.me/+0WYPXoqqbj1hNWI0'),
        )],
        [premium_button(text='✅ تحقق من الاشتراك', callback_data='force_sub_check')],
    ])


def get_force_subscription_text() -> str:
    return (
        f'<tg-emoji emoji-id="{PREMIUM_EMOJI["security"]}">🔒</tg-emoji> '
        '<b>الاشتراك بالقناة مطلوب</b>\n\n'
        'للاستفادة من خدمات <b>Jude Robert</b>، اشترك بالقناة الرسمية أولاً.\n'
        'بعد الاشتراك اضغط على زر التحقق ليتم تفعيل البوت لحسابك.\n\n'
        f'<tg-emoji emoji-id="{PREMIUM_EMOJI["info"]}">ℹ️</tg-emoji> '
        '<i>إذا اشتركت ولم يتم التحقق مباشرة، انتظر ثوانٍ ثم اضغط التحقق مرة أخرى.</i>'
    )


def get_terms_text() -> str:
    return (
        "📃 <b>الشروط والأحكام</b>\n\n"
        "🚧 <b>يجب عليك الموافقة على الشروط قبل استخدام البوت:</b>\n\n"
        "1️⃣ <b>المتابعة تعني الموافقة على الشروط:</b> أنت تقر بأنك قرأت الشروط وتوافق عليها بالكامل.\n"
        "2️⃣ <b>تبديل طرق الدفع غير مسموح:</b> لا يسمح بشحن رصيد وسحبه بطرق مختلفة بغرض التلاعب.\n"
        "3️⃣ <b>أرباح الإحالات:</b> تحتسب فقط بعد تسجيل 3 إحالات نشطة.\n"
        "4️⃣ <b>المسؤولية:</b> أي محاولة احتيال أو تلاعب تؤدي إلى حظر الحساب ومصادرة الرصيد.\n\n"
        "🚧 بمجرد المتابعة بعد الموافقة، فأنت تقر بأنك قرأت الشروط ووافقت عليها."
    )


class TermsCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Any, Dict[str, Any]], Awaitable[Any]],
        event: Any,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get('event_from_user')
        if not user:
            return await handler(event, data)

        # 🛠️ إصلاح: المشرفون يتجاوزون فحص الشروط بالكامل
        # (ضروري لأن أزرار الموافقة/الرفض تُنقر من داخل القنوات)
        if _is_admin(user.id):
            return await handler(event, data)

        telegram_id = str(user.id)
        username = user.username

        # الحظر أولوية مطلقة: يجب ألا يرى المحظور أي أزرار، حتى أزرار الاشتراك أو القائمة.
        cached_status = _user_status_cache.get(telegram_id)
        if cached_status and cached_status[2] > time.time():
            if cached_status[0]:
                blocked_text = '⛔ حسابك محظور حالياً. إذا كنت تظن أن هناك خطأ، تواصل مع الدعم.'
                if isinstance(event, Message):
                    await event.answer(blocked_text)
                elif isinstance(event, CallbackQuery):
                    try:
                        await event.message.edit_text(blocked_text, reply_markup=None)
                    except Exception:
                        pass
                    await event.answer('⛔ حسابك محظور حالياً.', show_alert=True)
                return
            if cached_status[1] and _terms_accepted_cache.get(telegram_id, 0) > time.time():
                data['terms_user'] = None
                return await handler(event, data)

        db_user = await asyncio.to_thread(repo.get_user, telegram_id)
        if db_user and db_user.get('is_banned'):
            blocked_text = '⛔ حسابك محظور حالياً. إذا كنت تظن أن هناك خطأ، تواصل مع الدعم.'
            if isinstance(event, Message):
                await event.answer(blocked_text)
            elif isinstance(event, CallbackQuery):
                try:
                    await event.message.edit_text(blocked_text, reply_markup=None)
                except Exception:
                    pass
                await event.answer('⛔ حسابك محظور حالياً.', show_alert=True)
            return
        if db_user:
            _user_status_cache[telegram_id] = (
                bool(db_user.get('is_banned')),
                bool(db_user.get('terms_accepted')),
                time.time() + _USER_STATUS_TTL,
            )

        # Force subscription is checked before terms and database work.
        # The verification callback itself must reach its handler.
        if getattr(settings, 'FORCE_SUBSCRIPTION_CHAT_ID', '').strip():
            if not (isinstance(event, CallbackQuery) and event.data == 'force_sub_check'):
                bot = data.get('bot') or getattr(event, 'bot', None)
                if bot and not await is_force_subscribed(bot, user.id):
                    prompt = get_force_subscription_text()
                    keyboard = get_force_subscription_keyboard()
                    if isinstance(event, Message):
                        await event.answer(prompt, reply_markup=keyboard, parse_mode='HTML')
                    elif isinstance(event, CallbackQuery):
                        await event.message.answer(prompt, reply_markup=keyboard, parse_mode='HTML')
                        await event.answer('اشترك بالقناة أولاً ثم اضغط تحقق.', show_alert=True)
                    return

        # إنشاء سجل المستخدم بعد اجتياز فحص الحظر والاشتراك.
        if not db_user:
            await asyncio.to_thread(repo.create_user, telegram_id, username)
            db_user = await asyncio.to_thread(repo.get_user, telegram_id)

        # 🌟 مسار الكاش السريع: مقبول مسبقاً خلال 60 ثانية.
        if _terms_accepted_cache.get(telegram_id, 0) > time.time():
            data['terms_user'] = db_user
            return await handler(event, data)

        is_bypass = False

        if isinstance(event, Message):
            if event.text and (
                event.text.startswith('/start') or
                event.text.startswith('/admin') or
                event.text.startswith('/delete') or
                event.text.startswith('/cancel') or
                event.text.startswith('/home')
            ):
                is_bypass = True
        elif isinstance(event, CallbackQuery):
            if event.data in [
                'accept_terms',
                'reject_terms',
                'show_terms_only',
                'delete_confirm',
                'delete_cancel',
                'back_to_main_menu',
                'force_sub_check'
            ]:
                is_bypass = True

        if not is_bypass and db_user and not db_user.get('terms_accepted'):
            from telegram_bot.keyboards.inline import get_terms_keyboard
            terms_text = get_terms_text()

            if isinstance(event, Message):
                await event.answer(terms_text, reply_markup=get_terms_keyboard(), parse_mode="HTML")
            elif isinstance(event, CallbackQuery):
                await event.message.answer(terms_text, reply_markup=get_terms_keyboard(), parse_mode="HTML")
                await event.answer("يرجى الموافقة على الشروط أولاً!", show_alert=True)
            return

        # 🌟 المستخدم مقبول → خزّن القبول في الكاش لتتخطى الأحداث التالية الاستعلام
        if db_user and db_user.get('terms_accepted'):
            _terms_accepted_cache[telegram_id] = time.time() + _TERMS_TTL

        # Pass the already-fetched record to handlers to avoid a duplicate
        # database round-trip on /start and other first interactions.
        data['terms_user'] = db_user
        return await handler(event, data)
