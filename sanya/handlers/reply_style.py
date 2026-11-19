from aiogram import types
from aiogram.enums import ChatType

from ..database import Database
from ..reply_style import LORE_LABELS, TONE_LABELS
from ..repository import Repository
from .access import check_is_admin
from .command_args import command_args


async def _level(
    message: types.Message,
    db: Database,
    axis: str,
    command: str,
    labels: tuple[str, ...],
):
    arg = command_args(message)
    chat = message.chat
    if not arg:
        style = (await db.run(Repository.get_chat_reply_settings, chat.id)).style
        current = getattr(style, axis)
        scale = "; ".join(f"{i} — {label}" for i, label in enumerate(labels))
        await message.reply(
            f"{command}: сейчас {current} — {labels[current]}\n{scale}\nПример: {command} 2"
        )
        return
    if arg not in ("0", "1", "2", "3"):
        await message.reply(f"Укажи число от 0 до 3: {command} 2")
        return
    if chat.type != ChatType.PRIVATE and (
        not message.from_user or not await check_is_admin(chat, message.from_user.id)
    ):
        await message.reply("В группе настройки меняют только админы.")
        return
    level = int(arg)
    await db.run(Repository.set_chat_reply_level, chat.id, axis, level)
    await message.reply(f"{command}: {level} — {labels[level]}")


async def cmd_tone(message: types.Message, db: Database):
    await _level(message, db, "tone", "/тон", TONE_LABELS)


async def cmd_lore_level(message: types.Message, db: Database):
    await _level(message, db, "lore", "/лор", LORE_LABELS)
