import re

from aiogram import F, Router, types
from aiogram.filters import Command

from ..intent import Mode
from ..services.booru_confirmation import (
    IDEA_PREFIX,
    PREFIX,
    ask_prompt_idea_confirmation,
    confirm_booru,
    confirm_prompt_idea,
)
from ..services.queue import RequestQueue
from ..services.safe_mode import IDEA_DISABLED, safe_mode
from ..services.stickers import try_react_whale
from .command_args import command_args


async def enqueue_command(message: types.Message, requests: RequestQueue, **kwargs):
    result = await requests.enqueue(message, **kwargs)
    if error := result.rejection_message():
        await message.reply(error)


async def cmd_search(message: types.Message, requests: RequestQueue, db=None):
    if db is None or not await safe_mode(db, message.chat.id):
        await try_react_whale(message)
    args = command_args(message)
    if not args:
        await message.reply("Напиши че искать то\n/s 4070 авито или /поиск 4070 авито")
        return
    await enqueue_command(message, requests, force_search_query=args)


async def cmd_pic(message: types.Message, requests: RequestQueue, db=None):
    if db is None or not await safe_mode(db, message.chat.id):
        await try_react_whale(message)
    args = command_args(message)
    if not args:
        await message.reply(
            "Напиши че за картинку искать то\n/pic микротик или /пикча микротик"
        )
        return
    if re.search(r"\b(booru|буру|r34|р34|derpi|дерпи|rule34)\b", args, re.IGNORECASE):
        await enqueue_command(message, requests, force_booru_query=args)
    else:
        await enqueue_command(message, requests, force_image_query=args)


async def cmd_booru(message: types.Message, requests: RequestQueue, db=None):
    if db is None or not await safe_mode(db, message.chat.id):
        await try_react_whale(message)
    args = command_args(message)
    if not args:
        await message.reply(
            "Напиши че искать на буру/р34:\n/booru fluttershy или /р34 hatsune miku"
        )
        return
    await enqueue_command(message, requests, force_booru_query=args)


async def cmd_slop(message: types.Message, requests: RequestQueue):
    args = command_args(message)
    if not args:
        await message.reply("Укажи теги через запятую: /slop landscape, general")
        return
    await enqueue_command(message, requests, force_booru_query=args, slop=True)


async def cmd_code(message: types.Message, requests: RequestQueue):
    args = command_args(message)
    if not args:
        await message.reply(
            "Напиши задачу после команды: /код Python, убрать дубли из списка"
        )
        return
    await enqueue_command(message, requests, force_generation=(Mode.CODE, args))


async def cmd_prompt(message: types.Message, requests: RequestQueue):
    args = command_args(message)
    if not args:
        await message.reply(
            "Опиши картинку после команды: /промпт пони ждёт трамвай ночью, акварель"
        )
        return
    await enqueue_command(message, requests, force_generation=(Mode.PROMPT, args))


async def cmd_prompt_idea(message: types.Message, db=None):
    if db is not None and await safe_mode(db, message.chat.id):
        await message.reply(IDEA_DISABLED)
        return
    await ask_prompt_idea_confirmation(message)


def make_router() -> Router:
    router = Router(name=__name__)
    router.callback_query.register(confirm_booru, F.data.startswith(PREFIX))
    router.callback_query.register(confirm_prompt_idea, F.data.startswith(IDEA_PREFIX))
    router.message.register(cmd_slop, Command("slop", "слоп"))
    router.message.register(cmd_code, Command("code", "код"))
    router.message.register(cmd_prompt, Command("prompt", "промпт"))
    router.message.register(cmd_prompt_idea, Command("promptidea", "идея"))
    router.message.register(
        cmd_search,
        Command("search", "s", "поиск", "гугл", "найти", "гугли", "ищи", "с"),
    )
    router.message.register(
        cmd_pic,
        Command(
            "pic",
            "img",
            "image",
            "photo",
            "картинка",
            "пикча",
            "пик",
            "фото",
            "фотка",
            "картинку",
        ),
    )
    router.message.register(
        cmd_booru, Command("booru", "r34", "derpi", "буру", "р34", "дерпи")
    )
    return router
