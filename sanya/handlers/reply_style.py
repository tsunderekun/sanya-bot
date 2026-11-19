from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest

from ..database import Database
from ..reply_style import LORE_LABELS, STICKER_LABELS, TONE_LABELS
from ..repository import Repository
from ..trigger_phrases import valid_trigger_phrase
from .access import check_is_admin
from .command_args import command_args

MODE_LABELS = {"normal": "норм", "rofl": "рофл", "safe": "сейф"}
MODE_ARGS = {label: mode for mode, label in MODE_LABELS.items()} | {
    mode: mode for mode in MODE_LABELS
}
SETTINGS_PREFIX = "settings:"
OWNED_CLOSE_PREFIX = SETTINGS_PREFIX + "close:"
LEVELS = {"tone": ("/тон", TONE_LABELS), "lore": ("/лор", LORE_LABELS)}
VIEWS = {"all", "mode", "sticker", "triggers", *LEVELS}
TRIGGER_HEADING = "Фразы обращения к боту"


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
                *(
                    [[button("Фразы обращения", "view:triggers")]]
                    if chat_id < 0
                    else []
                ),
            ]
        )
    text = []
    rows = []
    if view == "mode":
        text.append(f"Режим этого чата: {MODE_LABELS[settings.mode]}")
        text.append(
            "Сейф: без мата, подколов и фоновых реплик; поиск картинок отключён."
        )
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
    if view == "triggers":
        phrases = await db.run(Repository.get_chat_trigger_phrases, chat_id)
        text.append(
            TRIGGER_HEADING + "\n"
            "Ответь на эту панель фразой (2–48 знаков, до 8 слов), чтобы добавить её. "
            "В группе добавляют только админы. Нажатие включает или выключает фразу."
        )
        if not phrases:
            text.append("Фраз пока нет.")
        for phrase in phrases:
            enabled = bool(phrase["enabled"])
            label = ("✓ " if enabled else "○ ") + phrase["phrase"]
            rows.append([button(label[:60], f"trigger:toggle:{phrase['id']}")])
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
        if navigation == "triggers" and msg.chat.type not in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        ):
            await query.answer("Фразы настраиваются в группе.")
            return
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
    if data.startswith("trigger:toggle:"):
        raw_id = data.removeprefix("trigger:toggle:")
        if not raw_id.isascii() or not raw_id.isdecimal() or len(raw_id) > 12:
            await query.answer("Кнопка устарела")
            return
        if msg.chat.type not in (
            ChatType.GROUP,
            ChatType.SUPERGROUP,
        ) or not await check_is_admin(msg.chat, query.from_user.id):
            await query.answer("В группе фразы меняют только админы", show_alert=True)
            return
        if not await db.run(
            Repository.toggle_chat_trigger_phrase, msg.chat.id, int(raw_id)
        ):
            await query.answer("Фраза уже удалена")
            return
        await query.answer("Сохранено")
        await edit_panel(msg, *await settings_view(db, msg.chat.id, "triggers"))
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


def is_trigger_panel_reply(message: types.Message, bot: types.User) -> bool:
    panel = message.reply_to_message
    return bool(
        panel is not None
        and panel.from_user
        and panel.from_user.id == bot.id
        and getattr(getattr(panel, "chat", None), "id", None) == message.chat.id
        and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP)
        and (panel.text or "").startswith(TRIGGER_HEADING)
        and panel.reply_markup
    )


async def save_trigger_panel_reply(message: types.Message, db: Database):
    text = (message.text or message.caption or "").strip()
    if (
        message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP)
        or not message.from_user
        or getattr(message, "sender_chat", None)
        or not await check_is_admin(message.chat, message.from_user.id)
    ):
        await message.reply("В группе фразы добавляют только админы.")
        return
    if not valid_trigger_phrase(text):
        await message.reply("Фраза должна быть длиной 2–48 знаков и не больше 8 слов.")
        return
    result = await db.run(
        Repository.add_chat_trigger_phrase,
        message.chat.id,
        text,
        message.from_user.id,
    )
    answers = {
        "added": "Фразу добавил.",
        "exists": "Такая фраза уже есть.",
        "limit": "В этом чате уже максимум 20 фраз.",
        "invalid": "Не получилось добавить эту фразу.",
    }
    await message.reply(answers[result])
    if result == "added":
        panel = message.reply_to_message
        text, keyboard = await settings_view(db, message.chat.id, "triggers")
        owner = _panel_owner(panel)
        if owner is not None:
            keyboard = _with_panel_owner(keyboard, owner)
        await edit_panel(panel, text, keyboard)


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
