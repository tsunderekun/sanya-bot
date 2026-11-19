from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic

from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError

from .queue import RequestQueue

PREFIX = "booru-confirm:"
_TTL = 180
_LIMIT = 256


@dataclass(frozen=True)
class _Pending:
    message: types.Message
    prompt_id: int
    query: str
    expires: float


_pending: dict[str, _Pending] = {}


async def ask_booru_confirmation(message: types.Message, query: str):
    if not message.from_user or message.sender_chat:
        await message.reply("Подтверди поиск от своего аккаунта, не от имени чата.")
        return
    token = token_urlsafe(12)
    prompt = await message.reply(
        "Kontent mozhet byt nebezopasen (18+). Nazhmi Vizhu, esli ponimaesh."
        if message.chat.type == ChatType.PRIVATE
        else "В выдаче может быть контент 18+. Нажми Vizhu, если понимаешь. Картинка придёт под спойлером.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [types.InlineKeyboardButton(text="Vizhu", callback_data=PREFIX + token)]
            ]
        ),
    )
    now = monotonic()
    for old, item in tuple(_pending.items()):
        if item.expires <= now:
            del _pending[old]
    if len(_pending) >= _LIMIT:
        del _pending[next(iter(_pending))]
    _pending[token] = _Pending(message, prompt.message_id, query, now + _TTL)


async def confirm_booru(callback: types.CallbackQuery, requests: RequestQueue):
    token = (callback.data or "").removeprefix(PREFIX)
    item = _pending.get(token)
    prompt = callback.message
    private = prompt is not None and prompt.chat.type == ChatType.PRIVATE
    if (
        not item
        or item.expires <= monotonic()
        or not isinstance(prompt, types.Message)
        or prompt.chat.id != item.message.chat.id
        or prompt.message_id != item.prompt_id
    ):
        await callback.answer("Zapros ustarel." if private else "Запрос устарел.")
        return
    if not item.message.from_user or callback.from_user.id != item.message.from_user.id:
        await callback.answer("Подтвердить может только автор запроса.")
        return
    del _pending[token]
    queued = await requests.enqueue(
        item.message, force_booru_query=item.query, booru_confirmed=True
    )
    await callback.answer(
        ("Ishchu." if private else "Ищу.")
        if queued
        else ("Povtori komandu pozzhe." if private else "Повтори команду чуть позже.")
    )
    try:
        await prompt.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
