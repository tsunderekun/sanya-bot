import asyncio
import logging
import os
from functools import partial

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllChatAdministrators,
    BotCommandScopeAllGroupChats,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeDefault,
)

from .config import (
    BOT_TOKEN,
    DB_PATH,
    ENABLE_WEB_SEARCH,
    MAX_CONTEXT_CHARS,
    MAX_HISTORY,
    MODEL_NAME,
    OPENAI_BASE_URL,
    SEARCH_ENGINE,
    SEARXNG_URL,
    TELEGRAM_PROXY,
)
from .database import Database
from .handlers import (
    basic,
    chat,
    hardware,
    help_pages,
    kent_block,
    media,
    memes,
    memory,
    mention_all,
    stickers,
)
from .llm import Ollama
from .services.chat import process_chat_message
from .services.queue import RequestQueue
from .services.stickers import sync_sticker_packs


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.message.outer_middleware(kent_block.KentBlockMiddleware())
    dp.callback_query.outer_middleware(kent_block.KentBlockMiddleware())
    dp.include_routers(
        kent_block.make_router(),
        basic.make_router(),
        help_pages.make_router(),
        memory.make_router(),
        media.make_router(),
        memes.make_router(),
        hardware.make_router(),
        stickers.make_router(),
        mention_all.make_router(),
        chat.make_router(),
    )
    return dp


async def main():
    if not BOT_TOKEN or BOT_TOKEN.startswith("1234567890"):
        print("\n[!] ОШИБКА: Задай TELEGRAM_BOT_TOKEN в файле .env!\n")
        return

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    # Rule34 authenticates in the query string; HTTPX's request log includes it.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if os.getenv("LOG_OLLAMA_THINKING", "").lower() in ("1", "true", "yes"):
        logging.getLogger("sanya.thinking").setLevel(logging.DEBUG)
    session = AiohttpSession(proxy=TELEGRAM_PROXY or None)
    if TELEGRAM_PROXY:
        print(
            f"[*] Используется прокси для Telegram: {TELEGRAM_PROXY.split('@')[-1] if '@' in TELEGRAM_PROXY else TELEGRAM_PROXY}"
        )

    bot = Bot(token=BOT_TOKEN, session=session)
    print(
        f"[*] Бот запущен! Подключение к LLM: {OPENAI_BASE_URL} (модель: {MODEL_NAME})"
    )
    print(
        f"[*] Веб-поиск активен: {ENABLE_WEB_SEARCH} (движок: {SEARCH_ENGINE}, searxng: {SEARXNG_URL})"
    )
    print(
        f"[*] Постоянная память SQLite: {DB_PATH} (лимит контекста: {MAX_HISTORY} сообщений / {MAX_CONTEXT_CHARS} симв.)"
    )

    async with (
        session,
        Database(DB_PATH) as db,
        httpx.AsyncClient(trust_env=False) as client,
    ):
        await register_commands(bot)
        llm = Ollama(client)
        requests = RequestQueue()
        meme_precache = memes.MemePrecache()
        process = partial(process_chat_message, bot=bot, db=db, llm=llm)
        worker = asyncio.create_task(requests.work(process))
        sync = asyncio.create_task(sync_sticker_packs(bot, db))
        try:
            await build_dispatcher().start_polling(
                bot,
                close_bot_session=False,
                handle_as_tasks=False,
                db=db,
                llm=llm,
                requests=requests,
                meme_precache=meme_precache,
            )
        finally:
            worker.cancel()
            sync.cancel()
            await asyncio.gather(
                worker,
                sync,
                meme_precache.close(),
                return_exceptions=True,
            )


async def register_commands(bot: Bot):
    commands = {
        command.command: command
        for command in [
            BotCommand(command="help", description="Все команды и гайд по лору"),
            BotCommand(
                command="all", description="Позвать известных участников группы"
            ),
            BotCommand(command="settings", description="Настройки чата с кнопками"),
            BotCommand(
                command="dict",
                description="Словарь конфы: посмотреть, добавить, удалить",
            ),
            BotCommand(command="pic", description="Найти картинку"),
            BotCommand(command="meme", description="Случайный канальный мем"),
            BotCommand(command="booru", description="Поиск артов Booru / R34"),
            BotCommand(command="slop", description="Поиск артов на AIBooru"),
            BotCommand(command="code", description="Написать код по задаче"),
            BotCommand(command="prompt", description="Написать промпт для картинки"),
            BotCommand(command="promptidea", description="Предложить идею для промпта"),
            BotCommand(
                command="facts", description="Посмотреть свои сохранённые факты"
            ),
            BotCommand(
                command="clear",
                description="Очистить историю чата (в группе — админ)",
            ),
            BotCommand(command="forgetme", description="Очистить данные из базы"),
            BotCommand(
                command="setkent",
                description="Добавить/глянуть факты о кенте (админ)",
            ),
            BotCommand(
                command="setchat", description="Добавить/глянуть лор конфы (админ)"
            ),
            BotCommand(command="blockkent", description="Отключить ответы участнику"),
            BotCommand(command="unblockkent", description="Вернуть ответы участнику"),
        ]
    }
    common = (
        "help",
        "pic",
        "meme",
        "code",
        "prompt",
        "promptidea",
        "booru",
        "slop",
    )
    group = (
        "help",
        "all",
        "meme",
        "pic",
        "code",
        "prompt",
        "promptidea",
        "settings",
        "dict",
    )
    menus = (
        (BotCommandScopeDefault(), common),
        (BotCommandScopeAllPrivateChats(), (*common, "settings", "facts", "forgetme")),
        (BotCommandScopeAllGroupChats(), group),
        (
            BotCommandScopeAllChatAdministrators(),
            (*group, "setchat", "setkent", "clear", "blockkent", "unblockkent"),
        ),
    )
    for scope, names in menus:
        try:
            await bot.set_my_commands([commands[name] for name in names], scope=scope)
        except Exception as exc:
            logging.warning(
                "Не удалось зарегистрировать меню %s: %s",
                scope.type,
                type(exc).__name__,
            )
