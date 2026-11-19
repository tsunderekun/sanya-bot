from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic

from aiogram import types
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
        "Результат может быть небезопасным. Продолжить поиск?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Да, я понимаю", callback_data=PREFIX + token
                    )
                ]
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
    if (
        not item
        or item.expires <= monotonic()
        or not isinstance(prompt, types.Message)
        or prompt.chat.id != item.message.chat.id
        or prompt.message_id != item.prompt_id
    ):
        await callback.answer("Запрос устарел.")
        return
    if not item.message.from_user or callback.from_user.id != item.message.from_user.id:
        await callback.answer("Подтвердить может только автор запроса.")
        return
    del _pending[token]
    queued = await requests.enqueue(
        item.message, force_booru_query=item.query, booru_confirmed=True
    )
    await callback.answer("Ищу." if queued else "Повтори команду чуть позже.")
    try:
        await prompt.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
