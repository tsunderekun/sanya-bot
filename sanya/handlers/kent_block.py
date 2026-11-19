from aiogram import BaseMiddleware, Router, types
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject

from ..database import Database
from ..repository import Repository
from .access import check_is_admin, resolve_target_user

GROUPS = (ChatType.GROUP, ChatType.SUPERGROUP)


class KentBlockMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, types.CallbackQuery) else event
        user = event.from_user
        if (
            message is not None
            and message.chat.type in GROUPS
            and user is not None
            and await data["db"].run(
                Repository.is_kent_blocked, message.chat.id, user.id
            )
        ):
            if isinstance(event, types.CallbackQuery):
                await event.answer()
            return None
        return await handler(event, data)


async def cmd_kent_block(message: types.Message, command: CommandObject, db: Database):
    if message.chat.type not in GROUPS:
        await message.reply("Эта команда работает только в группе.")
        return
    actor = message.from_user
    if (
        not actor
        or message.sender_chat
        or not await check_is_admin(message.chat, actor.id)
    ):
        await message.reply("Только админы могут менять блокировку ответов.")
        return
    args = command.args or ""
    if message.reply_to_message and message.reply_to_message.sender_chat and not args:
        await message.reply(
            "Ответь на сообщение участника, а не канала или анонимного админа."
        )
        return
    target, _, remaining, error = await resolve_target_user(db, message, args)
    if not target or remaining or error:
        await message.reply(
            error or "Ответь командой на сообщение участника или укажи @ник либо ID.",
            parse_mode=None,
        )
        return
    blocked = command.command.casefold() == "blockkent"
    if blocked and (target == actor.id or await check_is_admin(message.chat, target)):
        await message.reply("Нельзя заблокировать себя или админа.")
        return
    changed = await db.run(
        Repository.set_kent_blocked, message.chat.id, target, blocked
    )
    if blocked:
        text = (
            "Бот больше не отвечает участнику в этой группе."
            if changed
            else "Ответы уже заблокированы."
        )
    else:
        text = (
            "Бот снова отвечает участнику в этой группе."
            if changed
            else "Ответы не были заблокированы."
        )
    await message.reply(text)


def make_router():
    router = Router(name=__name__)
    router.message.register(cmd_kent_block, Command("blockkent", "unblockkent"))
    return router
