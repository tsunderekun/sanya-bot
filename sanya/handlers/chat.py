import logging
import random
import time

from aiogram import Bot, Router, types
from aiogram.enums import ChatType

from ..config import MAX_PROACTIVE_PER_HOUR, ORIGINAL_SANYA_ID
from ..database import Database
from ..intent import SANYA_FAVORITE_TOPICS_PATTERN
from ..repository import Repository
from ..services.queue import RequestQueue
from ..services.stickers import try_react_whale


async def handle_message(
    message: types.Message, bot: Bot, db: Database, requests: RequestQueue
):
    if not message.from_user or message.from_user.is_bot:
        return
    # Фоновая реакция кита 🐳 на сообщение (с определенным шансом или при упоминании)
    await try_react_whale(message)

    # Обновляем профиль пользователя для каждого пишущего
    if message.from_user:
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or ""
        await db.run(Repository.update_user_profile, message.from_user.id, uname, fname)

    # Обновляем название чата, если это группа
    if (
        message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        and message.chat.title
    ):
        await db.run(Repository.update_chat_title, message.chat.id, message.chat.title)

    has_photo = bool(message.photo)
    has_reply_photo = bool(message.reply_to_message and message.reply_to_message.photo)
    has_sticker = bool(message.sticker)
    raw_text = message.text or message.caption or ""

    if has_sticker:
        stk_emoji = message.sticker.emoji or "❓"
        stk_pack = message.sticker.set_name or "custom"
        raw_text = f"[Стикер {stk_emoji} (набор: {stk_pack})]"

    if not raw_text.strip() and not (has_photo or has_reply_photo or has_sticker):
        return

    bot_user = await bot.get_me()
    is_private = message.chat.type == ChatType.PRIVATE
    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_user.id
    )

    bot_username = (bot_user.username or "").lower()
    text_lower = raw_text.lower()
    is_mentioned = bool(bot_username and f"@{bot_username}" in text_lower)

    is_called_by_name = any(
        trigger in text_lower
        for trigger in [
            "саня",
            "санек",
            "сань",
            "спермер",
            "пониеб",
            "пониёб",
            "ponyeb",
            "сперма",
        ]
    )

    is_from_original_sanya = bool(
        message.from_user and message.from_user.id == ORIGINAL_SANYA_ID
    )

    should_respond = is_private or is_reply_to_bot or is_mentioned or is_called_by_name

    # Проверка на проактивный ответ Сани на любимые темы (max 3 в час на чат)
    is_proactive = False
    if (
        not should_respond
        and not is_private
        and not is_from_original_sanya
        and not (message.from_user and message.from_user.is_bot)
    ):
        if (
            SANYA_FAVORITE_TOPICS_PATTERN.search(raw_text)
            and len(raw_text.split()) >= 3
        ):
            now = time.time()
            chat_id = message.chat.id
            last_proactive = requests.last_proactive.get(chat_id, 0.0)
            recent_proactives = [
                t for t in requests.proactive_history.get(chat_id, []) if now - t < 3600
            ]
            requests.proactive_history[chat_id] = recent_proactives

            # Кулдаун 15 минут между проактивными репликами и не более MAX_PROACTIVE_PER_HOUR (3) в час
            if (now - last_proactive >= 900) and (
                len(recent_proactives) < MAX_PROACTIVE_PER_HOUR
            ):
                # 35% шанс ворваться в разговор
                if random.random() < 0.35:
                    is_proactive = True
                    requests.last_proactive[chat_id] = now
                    requests.proactive_history[chat_id].append(now)
                    logging.info(
                        f"[{chat_id}] Сработало проактивное вклинивание Сани в разговор ({len(recent_proactives) + 1}/{MAX_PROACTIVE_PER_HOUR} за час)"
                    )

    if not should_respond and not is_proactive:
        # Сохраняем фоновое сообщение чата в историю, чтобы бот понимал общий контекст разговора
        sender_name = (
            message.from_user.username or message.from_user.first_name or "Кореш"
        )
        if is_from_original_sanya:
            sender_name = "Спермер (Оригинал)"
        reply_to_id = (
            message.reply_to_message.message_id if message.reply_to_message else None
        )
        await db.run(
            Repository.save_message,
            message.chat.id,
            message.message_id,
            reply_to_id,
            message.from_user.id,
            sender_name,
            "user",
            raw_text,
        )
        # Автоматический анализ фактов и частоты фраз в фоне
        await db.run(
            Repository.analyze_message_for_auto_facts,
            message.chat.id,
            message.from_user.id,
            sender_name,
            raw_text,
        )
        return

    # Если обратились к боту или сработал проактивный ответ — ставим в очередь
    await requests.enqueue(message, is_proactive=is_proactive)


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(handle_message)
    return router
