import asyncio
import logging
import os
from functools import partial

import httpx
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.types import BotCommand

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
from .handlers import basic, chat, hardware, media, memes, memory, mention_all, stickers
from .llm import Ollama
from .services.chat import process_chat_message
from .services.queue import RequestQueue
from .services.stickers import sync_sticker_packs


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_routers(
        basic.make_router(),
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
        worker = asyncio.create_task(
            requests.work(partial(process_chat_message, bot=bot, db=db, llm=llm))
        )
        sync = asyncio.create_task(sync_sticker_packs(bot, db))
        try:
            await build_dispatcher().start_polling(
                bot,
                close_bot_session=False,
                handle_as_tasks=False,
                db=db,
                llm=llm,
                requests=requests,
            )
        finally:
            worker.cancel()
            sync.cancel()
            await asyncio.gather(worker, sync, return_exceptions=True)


async def register_commands(bot: Bot):
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="help", description="Все команды и гайд по лору"),
                BotCommand(command="all", description="Позвать известных участников группы (админ)"),
                BotCommand(command="settings", description="Настройки чата с кнопками"),
                BotCommand(command="mode", description="Режим чата: норм или рофл"),
                BotCommand(command="tone", description="Тон чата: дружелюбно или поострее, 0–3"),
                BotCommand(command="lore", description="Частота отсылок к лору, 0–3"),
                BotCommand(command="dict", description="Словарь конфы: посмотреть, добавить, удалить"),
                BotCommand(command="pic", description="Найти картинку"),
                BotCommand(command="meme", description="Случайный канальный мем"),
                BotCommand(
                    command="precache", description="Загрузить мемы из канала в базу"
                ),
                BotCommand(command="hardware", description="Показать конфиг железа"),
                BotCommand(
                    command="price", description="Прикинуть цену железки по Авито"
                ),
                BotCommand(command="booru", description="Поиск артов Booru / R34"),
                BotCommand(command="s", description="Поиск в интернете"),
                BotCommand(
                    command="kents", description="Нейро-саммари по кентам конфы"
                ),
                BotCommand(
                    command="facts", description="Подмеченные факты о тебе и чате"
                ),
                BotCommand(
                    command="delfact", description="Удалить автофакты по ID (админ)"
                ),
                BotCommand(command="stickers", description="Стикерпаки Сани Пониёба"),
                BotCommand(
                    command="sticker", description="Кинуть фирменный стикер Сани"
                ),
                BotCommand(
                    command="clear",
                    description="Очистить историю чата (в группе — админ)",
                ),
                BotCommand(command="mydata", description="Мои данные и статус кента"),
                BotCommand(command="forgetme", description="Очистить данные из базы"),
                BotCommand(
                    command="setkent",
                    description="Добавить/глянуть факты о кенте (админ)",
                ),
                BotCommand(
                    command="delkent", description="Удалить факт о кенте по ID (админ)"
                ),
                BotCommand(
                    command="setchat", description="Добавить/глянуть лор конфы (админ)"
                ),
                BotCommand(
                    command="delchat", description="Удалить факт конфы по ID (админ)"
                ),
            ]
        )
    except Exception as e:
        logging.warning(f"Не удалось зарегистрировать команды бота: {e}")
