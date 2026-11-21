from aiogram import Router, types
from aiogram.filters import Command

from ..database import Database
from ..lore import CONFIRMED_HARDWARE, INITIAL_KENTS_PROFILES, KENT_ALIASES
from ..repository import Repository
from ..services.queue import RequestQueue
from .access import resolve_target_user
from .command_args import command_args
from .media import enqueue_command


async def cmd_hardware(message: types.Message, db: Database):
    args = command_args(message)
    if not args:
        if not message.from_user:
            return
        rows = await db.run(
            Repository.get_self_facts, message.chat.id, message.from_user.id, 8
        )
        facts = [
            row["fact_text"]
            for row in rows
            if row["fact_key"].rsplit(":", 1)[-1]
            in {"hardware_cpu", "hardware_gpu", "hardware_platform", "device"}
        ]
        await message.reply(
            "С твоих слов в этом чате:\n" + "\n".join(facts)
            if facts
            else "Ты мне ещё не хвастался своим хламом. Напиши в группе, например: «У меня RTX 3070»."
        )
        return
    key = " ".join(args.casefold().split())
    matches = {
        uid
        for uid, aliases in KENT_ALIASES.items()
        if key in {a.casefold() for a in aliases}
    }
    if args.startswith("@") or args.isascii() and args.isdecimal():
        uid, _, remaining, error = await resolve_target_user(
            db, message, args, allow_kent_aliases=True
        )
        if error or remaining:
            await message.reply(error or "Укажи одного кента.")
            return
    else:
        uid = next(iter(matches)) if len(matches) == 1 else None
    if uid not in INITIAL_KENTS_PROFILES:
        await message.reply("Не нашёл кента с таким именем в базовом лоре.")
        return
    await message.reply(
        CONFIRMED_HARDWARE.get(uid, "Подтверждённого конфига у этого кента нет.")
    )


async def cmd_gpu_review(message: types.Message, requests: RequestQueue):
    model = command_args(message)
    if not model or len(model) > 100:
        await message.reply("Укажи модель видеокарты, например: /оцени RTX 3070")
        return
    await enqueue_command(message, requests, force_gpu_query=model)


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(cmd_hardware, Command("hardware", "железо", "сетап"))
    router.message.register(cmd_gpu_review, Command("оцени", "price", "барыга"))
    return router
