import re

from aiogram import Router, types
from aiogram.filters import Command

from ..services.queue import RequestQueue
from ..services.stickers import try_react_whale


async def cmd_search(message: types.Message, requests: RequestQueue):
    await try_react_whale(message)
    args = message.text.partition(" ")[2].strip()
    if not args:
        await message.reply("Напиши че искать то\n/s 4070 авито или /поиск 4070 авито")
        return
    await requests.enqueue(message, force_search_query=args)


async def cmd_pic(message: types.Message, requests: RequestQueue):
    await try_react_whale(message)
    args = message.text.partition(" ")[2].strip()
    if not args:
        await message.reply(
            "Напиши че за картинку искать то\n/pic микротик или /пикча микротик"
        )
        return
    if re.search(r"\b(booru|буру|r34|р34|derpi|дерпи|rule34)\b", args, re.IGNORECASE):
        await requests.enqueue(message, force_booru_query=args)
    else:
        await requests.enqueue(message, force_image_query=args)


async def cmd_booru(message: types.Message, requests: RequestQueue):
    await try_react_whale(message)
    args = message.text.partition(" ")[2].strip()
    if not args:
        await message.reply(
            "Напиши че искать на буру/р34:\n/booru fluttershy или /р34 hatsune miku"
        )
        return
    await requests.enqueue(message, force_booru_query=args)


def make_router() -> Router:
    router = Router(name=__name__)
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
