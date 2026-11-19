from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from ..database import Database
from ..reply_style import LORE_LABELS, TONE_LABELS
from ..repository import Repository
from .access import check_is_admin
from .command_args import command_args

MODE_LABELS = {"normal": "норм", "rofl": "рофл"}
MODE_ARGS = {label: mode for mode, label in MODE_LABELS.items()} | {
    mode: mode for mode in MODE_LABELS
}
SETTINGS_PREFIX = "settings:"
LEVELS = {"tone": ("/тон", TONE_LABELS), "lore": ("/лор", LORE_LABELS)}
VIEWS = {"all", "mode", *LEVELS}


def button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=SETTINGS_PREFIX + data)


async def settings_view(db: Database, chat_id: int, view: str):
    settings = await db.run(Repository.get_chat_reply_settings, chat_id)
    text = ["Настройки этого чата"] if view == "all" else []
    rows = []
    if view in ("all", "mode"):
        text.append(f"Режим этого чата: {MODE_LABELS[settings.mode]}")
        rows.append(
            [
                button(
                    ("✓ " if key == settings.mode else "") + label, f"{view}:mode:{key}"
                )
                for key, label in MODE_LABELS.items()
            ]
        )
    for axis, (command, labels) in LEVELS.items():
        if view not in ("all", axis):
            continue
        current = getattr(settings.style, axis)
        text.append(f"{command}: сейчас {current} — {labels[current]}")
        choices = [
            button(f"{'✓ ' if i == current else ''}{i} · {label}", f"{view}:{axis}:{i}")
            for i, label in enumerate(labels)
        ]
        rows.extend((choices[:2], choices[2:]))
    if view == "all":
        rows.append([button("Словарь", "dict")])
    else:
        rows.append([button("Все настройки", "open")])
    return "\n".join(text), types.InlineKeyboardMarkup(inline_keyboard=rows)


async def show_settings(message: types.Message, db: Database, view: str = "all"):
    text, keyboard = await settings_view(db, message.chat.id, view)
    await message.reply(text, reply_markup=keyboard, parse_mode=None)


async def cmd_settings(message: types.Message, db: Database):
    await show_settings(message, db)


async def update_settings(query: types.CallbackQuery, db: Database):
    msg = query.message
    if not isinstance(msg, types.Message):
        await query.answer("Открой /settings заново")
        return
    data = (query.data or "").removeprefix(SETTINGS_PREFIX)
    if data == "open":
        await query.answer()
        await show_settings(msg, db)
        return
    if data == "dict":
        from .vocabulary import show_dictionary

        await query.answer()
        await show_dictionary(msg, db)
        return
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in VIEWS:
        await query.answer("Кнопка устарела")
        return
    view, axis, value = parts
    if not (
        (axis == "mode" and value in MODE_LABELS)
        or (axis in LEVELS and value in ("0", "1", "2", "3"))
    ) or view not in ("all", axis):
        await query.answer("Кнопка устарела")
        return
    if msg.chat.type != ChatType.PRIVATE and not await check_is_admin(
        msg.chat, query.from_user.id
    ):
        await query.answer("В группе настройки меняют только админы", show_alert=True)
        return
    if axis == "mode":
        await db.run(Repository.set_chat_reply_mode, msg.chat.id, value)
    else:
        await db.run(Repository.set_chat_reply_level, msg.chat.id, axis, int(value))
    await query.answer("Сохранено")
    text, keyboard = await settings_view(db, msg.chat.id, view)
    try:
        await msg.edit_text(text, reply_markup=keyboard, parse_mode=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


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
        await show_settings(message, db, axis)
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
