import asyncio
import random

from aiogram import Bot
from aiogram.types import BufferedInputFile, ReplyParameters

from .render import needs_document, render_messages


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
    bot: Bot, chat_id: int, reply_to_id: int, text: str, is_detailed: bool = False
):
    """Отправляет готовый ответ без обрезки блоков кода при лимите Telegram."""
    reply = (
        ReplyParameters(message_id=reply_to_id, allow_sending_without_reply=True)
        if reply_to_id
        else None
    )
    if is_detailed and needs_document(text):
        await bot.send_document(
            chat_id,
            BufferedInputFile(text.encode("utf-8"), filename="sanya-answer.md"),
            reply_parameters=reply,
        )
        return
    chunks = render_messages(text, detailed=is_detailed)
    for index, chunk in enumerate(chunks):
        if index and not is_detailed:
            await asyncio.sleep(random.uniform(0.35, 0.75))
        await bot.send_message(
            chat_id,
            chunk,
            parse_mode="HTML",
            reply_parameters=reply if index == 0 else None,
        )
