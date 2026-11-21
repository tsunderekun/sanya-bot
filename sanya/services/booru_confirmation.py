from dataclasses import dataclass
from secrets import token_urlsafe
from time import monotonic

from aiogram import types
from aiogram.exceptions import TelegramAPIError

from .queue import RequestQueue

PREFIX = "booru-confirm:"
_TTL = 180
_LIMIT = 256
IDEA_PREFIX = "prompt-idea-confirm:"
_idea_pending: dict[str, tuple[types.Message, int, float]] = {}


@dataclass(frozen=True)
class _Pending:
    message: types.Message
    prompt_id: int
    query: str
    expires: float
    slop: bool


_pending: dict[str, _Pending] = {}


async def ask_booru_confirmation(
    message: types.Message, query: str, *, slop: bool = False
):
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
    _pending[token] = _Pending(message, prompt.message_id, query, now + _TTL, slop)


async def ask_prompt_idea_confirmation(message: types.Message):
    if not message.from_user or message.sender_chat:
        await message.reply("Запусти /идея от своего аккаунта, не от имени чата.")
        return
    token = token_urlsafe(12)
    prompt = await message.reply(
        "Идея может быть откровенной. Продолжить поиск тегов и генерацию?",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Да, я понимаю", callback_data=IDEA_PREFIX + token
                    )
                ]
            ]
        ),
    )
    now = monotonic()
    for old, (_, _, expiry) in tuple(_idea_pending.items()):
        if expiry <= now:
            del _idea_pending[old]
    if len(_idea_pending) >= _LIMIT:
        del _idea_pending[next(iter(_idea_pending))]
    _idea_pending[token] = (message, prompt.message_id, now + _TTL)


async def confirm_prompt_idea(callback: types.CallbackQuery, requests: RequestQueue):
    token = (callback.data or "").removeprefix(IDEA_PREFIX)
    item = _idea_pending.get(token)
    prompt = callback.message
    if (
        not item
        or item[2] <= monotonic()
        or not isinstance(prompt, types.Message)
        or prompt.chat.id != item[0].chat.id
        or prompt.message_id != item[1]
    ):
        await callback.answer("Запрос устарел.")
        return
    message = item[0]
    if not message.from_user or callback.from_user.id != message.from_user.id:
        await callback.answer("Подтвердить может только автор запроса.")
        return
    del _idea_pending[token]
    queued = await requests.enqueue(message, prompt_idea=True)
    await callback.answer(queued.rejection_message() or "Собираю идею.")
    try:
        await prompt.delete()
    except TelegramAPIError:
        try:
            await prompt.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass


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
        item.message,
        force_booru_query=item.query,
        booru_confirmed=True,
        **({"slop": True} if item.slop else {}),
    )
    await callback.answer(queued.rejection_message() or "Ищу.")
    try:
        await prompt.delete()
    except TelegramAPIError:
        try:
            await prompt.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass
