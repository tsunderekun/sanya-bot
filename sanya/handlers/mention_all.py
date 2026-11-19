import asyncio
import re
from html import escape
from time import monotonic

from aiogram import Bot, F, Router, types
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository

_CALL = re.compile(r"^\s*@all(?=\s|$)", re.I)
_COOLDOWN = 300
_BATCH_SIZE = 20
_LOOKUP_CONCURRENCY = 4


def _is_present(member) -> bool:
    return member.status in {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    } or (member.status == ChatMemberStatus.RESTRICTED and member.is_member)


def _mention(user: types.User) -> str:
    label = " ".join(user.first_name.split())[:64] or "Участник"
    return f'<a href="tg://user?id={user.id}">{escape(label)}</a>'


async def mention_all(
    message: types.Message, bot: Bot, db: Database, cooldowns: dict[int, float]
):
    if message.chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        await message.reply("@all работает только в группе.")
        return
    chat_id = message.chat.id
    author = message.from_user
    anonymous_admin = (
        message.sender_chat is not None and message.sender_chat.id == chat_id
    )
    if message.forward_origin or (
        not anonymous_admin and (message.sender_chat or not author or author.is_bot)
    ):
        return
    try:
        admins = await bot.get_chat_administrators(chat_id)
    except TelegramAPIError:
        await message.reply("Не получилось проверить админов группы.")
        return
    people = {m.user.id: m.user for m in admins if not m.user.is_bot}
    if not anonymous_admin and author.id not in people:
        await message.reply("Звать всех могут только админы группы.")
        return

    now = monotonic()
    for key in [key for key, expiry in cooldowns.items() if expiry <= now]:
        del cooldowns[key]
    if chat_id in cooldowns:
        await message.reply("Уже звали всех. Между вызовами должно пройти 5 минут.")
        return
    cooldowns[chat_id] = now + _COOLDOWN

    ids = await db.run(Repository.list_chat_speakers, chat_id)

    async def lookup(user_id: int):
        try:
            member = await bot.get_chat_member(chat_id, user_id)
        except TelegramBadRequest:
            return None
        return member.user if _is_present(member) and not member.user.is_bot else None

    pending = [uid for uid in ids if uid not in people]
    try:
        for start in range(0, len(pending), _LOOKUP_CONCURRENCY):
            members = await asyncio.gather(
                *(lookup(uid) for uid in pending[start : start + _LOOKUP_CONCURRENCY]),
                return_exceptions=True,
            )
            for member in members:
                if isinstance(member, BaseException):
                    raise member
                if member:
                    people[member.id] = member
    except TelegramAPIError:
        cooldowns.pop(chat_id, None)
        await message.reply("Не получилось проверить участников. Попробуй позже.")
        return

    if author:
        people.pop(author.id, None)
    if not people:
        cooldowns.pop(chat_id, None)
        await message.reply("Не нашёл других известных участников этой группы.")
        return
    mentions = [_mention(user) for user in people.values()]
    for start in range(0, len(mentions), _BATCH_SIZE):
        header = f"Зову известных участников: {len(mentions)}\n" if not start else ""
        await message.reply(
            header + ", ".join(mentions[start : start + _BATCH_SIZE]),
            parse_mode="HTML",
        )


def make_router() -> Router:
    router = Router(name=__name__)
    cooldowns: dict[int, float] = {}

    async def handle(message: types.Message, bot: Bot, db: Database):
        await mention_all(message, bot, db, cooldowns)

    router.message.register(handle, Command("all"))
    router.message.register(handle, F.text.regexp(_CALL))
    return router
