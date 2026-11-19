import re

from aiogram import F, Router, types
from aiogram.filters import Command

from ..services.booru_confirmation import PREFIX, confirm_booru
from ..services.queue import RequestQueue
from ..services.stickers import try_react_whale
from .command_args import command_args


async def enqueue_command(message: types.Message, requests: RequestQueue, **kwargs):
    if not await requests.enqueue(message, **kwargs):
        await message.reply("Сейчас не могу принять запрос. Попробуй чуть позже.")


async def cmd_search(message: types.Message, requests: RequestQueue):
    await try_react_whale(message)
    args = command_args(message)
    if not args:
        await message.reply("Напиши че искать то\n/s 4070 авито или /поиск 4070 авито")
        return
    await enqueue_command(message, requests, force_search_query=args)


async def cmd_pic(message: types.Message, requests: RequestQueue):
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


async def cmd_booru(message: types.Message, requests: RequestQueue):
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


def make_router() -> Router:
    router = Router(name=__name__)
    router.callback_query.register(confirm_booru, F.data.startswith(PREFIX))
    router.message.register(cmd_slop, Command("slop", "слоп"))
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
