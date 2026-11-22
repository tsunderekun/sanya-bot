import os
import re
import secrets

from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandObject

from ..database import Database
from ..repository import Repository
from ..services.safe_mode import safe_mode

GROUPS = (ChatType.GROUP, ChatType.SUPERGROUP)
PREFIX = "draw-access:"


def owner_id() -> int | None:
    value = os.getenv("IMAGE_GENERATION_APPROVER_ID", "").strip()
    return int(value) if re.fullmatch(r"[1-9][0-9]{0,15}", value) else None


def _keyboard(*buttons: tuple[str, str], token: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=label, callback_data=f"{PREFIX}{action}:{token}")
        for label, action in buttons
    ]])


async def draw_settings_rows(db: Database, page: int):
    size = 5
    entries = await db.run(Repository.list_active_draw_access, page * size, size + 1)
    lines = []
    rows = []
    for i, (chat_id, user_id, token, state) in enumerate(entries[:size], page * size + 1):
        pending = state == "pending"
        lines.append(f"{i}. {'Заявка' if pending else 'Разрешён'} · группа {chat_id} · участник {user_id}")
        actions = (("Разрешить", "allow"), ("Отказать", "deny")) if pending else (("Отозвать", "revoke"),)
        rows.append(_keyboard(*((f"{i}. {label}", action) for label, action in actions), token=token).inline_keyboard[0])
    return lines, rows, len(entries) > size


async def authorize_group_draw(message: types.Message, db: Database) -> bool:
    owner = owner_id()
    if owner is None:
        await message.reply("Генерация в группах пока выключена.")
        return False
    chat_id, user_id = message.chat.id, message.from_user.id
    if await safe_mode(db, chat_id):
        await message.reply("В сейф-режиме генерация картинок выключена.")
        return False
    state = await db.run(Repository.get_draw_access, chat_id, user_id)
    if state == "allowed":
        return True
    if state in ("denied", "revoked"):
        await message.reply("У тебя нет доступа к генерации в этой группе.")
        return False
    token = secrets.token_urlsafe(16)
    if state == "pending" or not await db.run(Repository.request_draw_access, chat_id, user_id, token):
        await message.reply("Запрос на доступ уже ждёт решения.")
        return False
    user = message.from_user
    try:
        await message.bot.send_message(
            owner,
            f"Запрос на /нарисуй\nГруппа: {message.chat.title or 'Без названия'} ({chat_id})\n"
            f"Участник: {user.full_name} ({user_id})",
            parse_mode=None,
            reply_markup=_keyboard(("Разрешить", "allow"), ("Отказать", "deny"), token=token),
        )
    except TelegramAPIError:
        await db.run(Repository.discard_draw_request, token)
        await message.reply("Не смог отправить запрос владельцу бота. Попробуй позже.")
    else:
        await message.reply("Запрос отправил. После одобрения отправь /нарисуй ещё раз.")
    return False


async def decide_group_draw(callback: types.CallbackQuery, db: Database):
    message = callback.message
    owner = owner_id()
    if (owner is None or callback.from_user.id != owner or not isinstance(message, types.Message)
            or message.chat.type != ChatType.PRIVATE or message.chat.id != owner):
        await callback.answer("Эта кнопка только для владельца бота.", show_alert=True)
        return
    match = re.fullmatch(r"draw-access:(allow|deny|revoke):([\w-]{22})", callback.data or "", re.ASCII)
    if not match:
        await callback.answer("Неверный запрос.")
        return
    action, token = match.groups()
    row = await db.run(Repository.decide_draw_access, token, action)
    if row is None:
        await callback.answer("Это решение уже неактуально.")
        return
    status = {"allow": "Доступ разрешён", "deny": "Доступ отклонён", "revoke": "Доступ отозван"}[action]
    await callback.answer(status)
    keyboard = _keyboard(("Отозвать доступ", "revoke"), token=token) if action == "allow" else None
    # Settings controls and notification controls share the same one-use decisions.
    if any(
        (button.callback_data or "").startswith("settings:")
        for row_buttons in (message.reply_markup.inline_keyboard if message.reply_markup else [])
        for button in row_buttons
    ):
        from .reply_style import edit_panel, settings_view

        try:
            await edit_panel(message, *await settings_view(db, owner, "draw"))
        except TelegramAPIError:
            pass
        return
    try:
        await message.edit_text(
            f"{status}\nГруппа: {row[0]}\nУчастник: {row[1]}",
            parse_mode=None,
            reply_markup=keyboard,
        )
    except TelegramAPIError:
        # The stored decision is authoritative even if Telegram cannot edit the old message.
        pass


async def cmd_drawrevoke(message: types.Message, command: CommandObject, db: Database):
    if (message.chat.type != ChatType.PRIVATE or message.from_user is None
            or message.from_user.id != owner_id()):
        return
    match = re.fullmatch(r"(-[1-9][0-9]{0,15})\s+([1-9][0-9]{0,15})", command.args or "")
    if not match:
        await message.reply("Используй /drawrevoke ID_группы ID_участника (оба ID есть в запросе).")
        return
    changed = await db.run(Repository.revoke_draw_access, *map(int, match.groups()))
    await message.reply("Доступ отозван." if changed else "Действующего доступа или запроса нет.")
