import asyncio
import logging
import random

from aiogram import Bot, types
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.types import BufferedInputFile, ReplyParameters

from .render import (
    needs_document,
    render_compact_reply,
    render_messages,
    render_table_reply,
)

logger = logging.getLogger(__name__)


async def keep_typing(bot: Bot, chat_id: int, stop_event: asyncio.Event):
    """Фоновая индикация набора сообщения."""
    while not stop_event.is_set():
        try:
            await bot.send_chat_action(chat_id, "typing")
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            pass


async def send_burst_messages(
    bot: Bot,
    chat_id: int,
    reply_to_id: int,
    text: str,
    is_detailed: bool = False,
    *,
    compact: bool = False,
) -> list[int]:
    """Отправляет ответ без обрезки кода и возвращает ID отправленных частей."""
    reply = (
        ReplyParameters(message_id=reply_to_id, allow_sending_without_reply=True)
        if reply_to_id
        else None
    )
    if compact:
        sent = await bot.send_message(
            chat_id,
            render_compact_reply(text),
            parse_mode="HTML",
            reply_parameters=reply,
        )
        return [sent.message_id]
    if table := render_table_reply(text):
        rich_html, fallback = table
        rich_type = getattr(types, "InputRichMessage", None)
        send_rich = getattr(bot, "send_rich_message", None)
        if rich_type is not None and send_rich is not None:
            try:
                sent = await send_rich(
                    chat_id,
                    rich_type(html=rich_html, skip_entity_detection=True),
                    reply_parameters=reply,
                )
            except (TelegramBadRequest, TelegramNotFound):
                logger.info("Telegram отклонил таблицу; отправляю список")
            else:
                return [sent.message_id]
        text, is_detailed = fallback, True
    if is_detailed and needs_document(text):
        sent = await bot.send_document(
            chat_id,
            BufferedInputFile(text.encode("utf-8"), filename="sanya-answer.md"),
            reply_parameters=reply,
        )
        return [sent.message_id]
    chunks = render_messages(text, detailed=is_detailed)
    sent_ids = []
    for index, chunk in enumerate(chunks):
        if index and not is_detailed:
            await asyncio.sleep(random.uniform(0.35, 0.75))
        sent = await bot.send_message(
            chat_id,
            chunk,
            parse_mode="HTML",
            reply_parameters=reply if index == 0 else None,
        )
        sent_ids.append(sent.message_id)
    return sent_ids
