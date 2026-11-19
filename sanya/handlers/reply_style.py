from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from ..database import Database
from ..reply_style import LORE_LABELS, STICKER_LABELS, TONE_LABELS
from ..repository import Repository
from .access import check_is_admin
from .command_args import command_args

MODE_LABELS = {"normal": "норм", "rofl": "рофл"}
MODE_ARGS = {label: mode for mode, label in MODE_LABELS.items()} | {
    mode: mode for mode in MODE_LABELS
}
SETTINGS_PREFIX = "settings:"
OWNED_CLOSE_PREFIX = SETTINGS_PREFIX + "close:"
LEVELS = {"tone": ("/тон", TONE_LABELS), "lore": ("/лор", LORE_LABELS)}
VIEWS = {"all", "mode", "sticker", *LEVELS}


def button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=SETTINGS_PREFIX + data)


def panel_keyboard(rows):
    close = types.InlineKeyboardButton(
        text="Закрыть", callback_data=SETTINGS_PREFIX + "close", style="danger"
    )
    return types.InlineKeyboardMarkup(inline_keyboard=[*rows, [close]])


def _panel_owner(message: types.Message) -> int | None:
    if message.reply_markup:
        for row in message.reply_markup.inline_keyboard:
            for control in row:
                data = control.callback_data or ""
                if data.startswith(OWNED_CLOSE_PREFIX):
                    owner = data.removeprefix(OWNED_CLOSE_PREFIX)
                    if owner.isascii() and owner.isdecimal() and len(owner) <= 16:
                        return int(owner)
    return None


def _with_panel_owner(keyboard: types.InlineKeyboardMarkup, owner: int):
    for row in keyboard.inline_keyboard:
        for control in row:
            if control.callback_data == SETTINGS_PREFIX + "close":
                control.callback_data = OWNED_CLOSE_PREFIX + str(owner)
    return keyboard


async def settings_view(db: Database, chat_id: int, view: str):
    settings = await db.run(Repository.get_chat_reply_settings, chat_id)
    level = settings.sticker_level
    sticker = STICKER_LABELS[level] if level is not None else "по умолчанию"
    if view == "all":
        text = (
            "Настройки этого чата\n\n"
            f"Режим: {MODE_LABELS[settings.mode]}\n"
            f"Тон: {settings.style.tone} — {TONE_LABELS[settings.style.tone]}\n"
            f"Лор: {settings.style.lore} — {LORE_LABELS[settings.style.lore]}\n"
            f"Автостикеры: {sticker}"
        )
        return text, panel_keyboard(
            [
                [
                    button("Режим", "view:mode"),
                    button("Тон", "view:tone"),
                    button("Лор", "view:lore"),
                ],
                [button("Автостикеры", "view:sticker"), button("Словарь", "dict")],
            ]
        )
    text = []
    rows = []
    if view == "mode":
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
        if view != axis:
            continue
        current = getattr(settings.style, axis)
        text.append(f"{command}: сейчас {current} — {labels[current]}")
        choices = [
            button(f"{'✓ ' if i == current else ''}{i} · {label}", f"{view}:{axis}:{i}")
            for i, label in enumerate(labels)
        ]
        rows.extend((choices[:2], choices[2:]))
    if view == "sticker":
        text.append(f"Автостикеры: {sticker}")
        rows.append(
            [
                button(f"{'✓ ' if i == level else ''}{label}", f"{view}:sticker:{i}")
                for i, label in enumerate(STICKER_LABELS)
            ]
        )
        rows.append([button("По умолчанию", f"{view}:sticker:default")])
    rows.append([button("Все настройки", "view:all")])
    return "\n".join(text), panel_keyboard(rows)


async def show_settings(
    message: types.Message, db: Database, view: str = "all", *, owner: int | None = None
):
    text, keyboard = await settings_view(db, message.chat.id, view)
    if owner is not None:
        keyboard = _with_panel_owner(keyboard, owner)
    await message.reply(text, reply_markup=keyboard, parse_mode=None)


async def cmd_settings(message: types.Message, db: Database):
    await show_settings(message, db)


async def edit_panel(message: types.Message, text: str, keyboard):
    owner = _panel_owner(message)
    if owner is not None and keyboard is not None:
        keyboard = _with_panel_owner(keyboard, owner)
    try:
        await message.edit_text(text, reply_markup=keyboard, parse_mode=None)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def close_panel(query: types.CallbackQuery, msg: types.Message):
    parent = msg.reply_to_message
    opener = parent.from_user if parent and not parent.sender_chat else None
    owner = _panel_owner(msg)
    if owner is None and opener and not opener.is_bot:
        owner = opener.id
    if (
        msg.chat.type != ChatType.PRIVATE
        and owner != query.from_user.id
        and not await check_is_admin(msg.chat, query.from_user.id)
    ):
        await query.answer("Закрыть меню может его автор или админ.", show_alert=True)
        return
    try:
        if await msg.delete():
            await query.answer()
            return
    except TelegramAPIError:
        pass
    try:
        await edit_panel(msg, "Меню закрыто.", None)
    except TelegramAPIError:
        await query.answer("Telegram не дал закрыть меню.", show_alert=True)
        return
    await query.answer()


async def update_settings(query: types.CallbackQuery, db: Database):
    msg = query.message
    if not isinstance(msg, types.Message):
        await query.answer("Открой /settings заново")
        return
    data = (query.data or "").removeprefix(SETTINGS_PREFIX)
    if data == "close" or (query.data or "").startswith(OWNED_CLOSE_PREFIX):
        await close_panel(query, msg)
        return
    if data == "open":
        await query.answer()
        await show_settings(msg, db, owner=query.from_user.id)
        return
    navigation = data.removeprefix("view:")
    if data.startswith("view:") and navigation in VIEWS:
        await query.answer()
        await edit_panel(msg, *await settings_view(db, msg.chat.id, navigation))
        return
    if data == "dict" or data.startswith("dict:"):
        from .vocabulary import dictionary_view

        page = "0" if data == "dict" else data.removeprefix("dict:")
        if not (page.isascii() and page.isdecimal() and len(page) <= 8):
            await query.answer("Кнопка устарела")
            return
        await query.answer()
        await edit_panel(msg, *await dictionary_view(db, msg.chat.id, int(page)))
        return
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in VIEWS:
        await query.answer("Кнопка устарела")
        return
    view, axis, value = parts
    if not (
        (axis == "mode" and value in MODE_LABELS)
        or (axis in LEVELS and value in ("0", "1", "2", "3"))
        or (axis == "sticker" and value in ("0", "1", "2", "3", "default"))
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
    elif axis == "sticker":
        await db.run(
            Repository.set_chat_sticker_level,
            msg.chat.id,
            None if value == "default" else int(value),
        )
    else:
        await db.run(Repository.set_chat_reply_level, msg.chat.id, axis, int(value))
    await query.answer("Сохранено")
    await edit_panel(msg, *await settings_view(db, msg.chat.id, view))


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


async def cmd_sticker_activity(message: types.Message, db: Database):
    arg = command_args(message).casefold()
    if not arg:
        await show_settings(message, db, "sticker")
        return
    if arg not in ("0", "1", "2", "3", "default"):
        await message.reply("Укажи 0–3 или default: /автостикеры 0")
        return
    if message.chat.type != ChatType.PRIVATE and (
        not message.from_user
        or not await check_is_admin(message.chat, message.from_user.id)
    ):
        await message.reply("В группе настройки меняют только админы.")
        return
    await db.run(
        Repository.set_chat_sticker_level,
        message.chat.id,
        None if arg == "default" else int(arg),
    )
    await show_settings(message, db, "sticker")
