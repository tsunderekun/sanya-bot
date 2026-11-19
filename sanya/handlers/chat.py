import logging
import random
import time

from aiogram import Bot, Router, types
from aiogram.enums import ChatType

from ..config import MAX_PROACTIVE_PER_HOUR, ORIGINAL_SANYA_ID
from ..database import Database
from ..intent import (
    SANYA_FAVORITE_TOPICS_PATTERN,
    is_bot_mention,
    is_bot_name_call,
    suppress_background_reply,
)
from ..observations import message_self_facts
from ..repository import Repository
from ..services.queue import RequestQueue
from ..services.safe_mode import safe_mode
from ..services.stickers import try_react_whale
from .lore_panel import is_lore_panel_reply, save_lore_panel_reply
from .memes import maybe_send_horse
from .reply_style import is_trigger_panel_reply, save_trigger_panel_reply
from .vocabulary import dictionary_reply, is_dictionary_reply


async def handle_message(
    message: types.Message, bot: Bot, db: Database, requests: RequestQueue
):
    if not message.from_user or message.from_user.is_bot:
        return
    # Обновляем профиль пользователя для каждого пишущего
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
    address_text = message.text or message.caption or ""
    raw_text = address_text

    if has_sticker:
        stk_emoji = message.sticker.emoji or "❓"
        stk_pack = message.sticker.set_name or "custom"
        raw_text = f"[Стикер {stk_emoji} (набор: {stk_pack})]"

    if not raw_text.strip() and not (has_photo or has_reply_photo or has_sticker):
        return

    bot_user = await bot.get_me()
    if is_trigger_panel_reply(message, bot_user):
        await save_trigger_panel_reply(message, db)
        return
    if is_lore_panel_reply(message, bot_user):
        await save_lore_panel_reply(message, db)
        return
    if is_dictionary_reply(message, bot_user):
        await dictionary_reply(message, db)
        return
    is_private = message.chat.type == ChatType.PRIVATE
    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_user.id
    )

    is_mentioned = is_bot_mention(address_text, bot_user.username)
    is_called_by_name = is_bot_name_call(address_text)

    is_from_original_sanya = bool(
        message.from_user and message.from_user.id == ORIGINAL_SANYA_ID
    )

    is_custom_trigger = (
        message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        and not (is_reply_to_bot or is_mentioned or is_called_by_name)
        and bool(address_text.strip())
        and await db.run(
            Repository.matches_chat_trigger_phrase, message.chat.id, address_text
        )
        is True
    )
    should_respond = (
        is_private
        or is_reply_to_bot
        or is_mentioned
        or is_called_by_name
        or is_custom_trigger
    )
    self_facts = (
        message_self_facts(message, address_text)
        if message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        else ()
    )
    if self_facts:
        await db.run(
            Repository.record_self_facts,
            message.chat.id,
            message.from_user.id,
            self_facts,
        )
        should_respond = False
    background_blocked = not should_respond and (
        bool(self_facts) or suppress_background_reply(address_text)
    )
    safe = await safe_mode(db, message.chat.id)
    horse_sent = False
    if (
        not safe
        and not should_respond
        and not background_blocked
        and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
    ):
        horse_sent = await maybe_send_horse(message, address_text)
    if not safe and not background_blocked and not self_facts and not horse_sent:
        await try_react_whale(message)

    # Проверка на проактивный ответ Сани на любимые темы (max 3 в час на чат)
    is_proactive = False
    if (
        not safe
        and not should_respond
        and not background_blocked
        and not horse_sent
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
        # Фоновая реплика доступна в истории автора или по ссылке из ветки.
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
        if not self_facts:
            await db.run(Repository.analyze_chat_phrases, message.chat.id, raw_text)
        return

    # Если обратились к боту или сработал проактивный ответ — ставим в очередь
    await requests.enqueue(
        message,
        is_proactive=is_proactive,
        **({"custom_trigger": True} if is_custom_trigger else {}),
    )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(handle_message)
    return router
