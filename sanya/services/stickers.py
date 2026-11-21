import asyncio
import logging
import random
import re
from typing import Optional

from aiogram import Bot, types
from aiogram.types import ReactionTypeEmoji

from ..config import WHALE_REACTION_CHANCE
from ..conversation_cues import DEADPAN_PUNCHLINE, DOUBLE_ENTENDRE
from ..database import Database
from ..lore import DEFAULT_STICKER_PACKS, ORIGINAL_SANYA_TOP_STICKERS
from ..repository import Repository

WHALE_TRIGGER_PATTERN = re.compile(
    r"\b(кит|кита|киту|китом|ките|киты|китов|китам|китами|китах)\b|🐳", re.IGNORECASE
)


def addressed_reaction(text: str) -> str | None:
    if text.lstrip().startswith("/") or WHALE_TRIGGER_PATTERN.search(text):
        return None
    if DOUBLE_ENTENDRE.search(text):
        return "🌚"
    if DEADPAN_PUNCHLINE.search(text):
        return "🗿"
    return None


async def try_react(message: types.Message, emoji: str) -> None:
    try:
        await asyncio.wait_for(message.react([ReactionTypeEmoji(emoji=emoji)]), timeout=1.5)
    except Exception:
        logging.debug("Telegram reaction unavailable: %s", emoji)


async def try_react_whale(message: types.Message, force: bool = False):
    """Ставит реакцию кита 🐳 на сообщение (при упоминании кита или с шансом WHALE_REACTION_CHANCE)."""
    if not message or not message.chat:
        return
    if message.from_user and message.from_user.is_bot:
        return

    raw_text = message.text or message.caption or ""
    should_react = force
    if not should_react:
        if WHALE_TRIGGER_PATTERN.search(raw_text):
            should_react = True
        elif random.random() < WHALE_REACTION_CHANCE:
            should_react = True

    if should_react:
        try:
            await message.react([ReactionTypeEmoji(emoji="🐳")])
        except Exception:
            logging.debug("Telegram reaction unavailable: 🐳")


async def send_sanya_sticker(
    bot: Bot,
    chat_id: int,
    reply_to_id: Optional[int] = None,
    emoji: Optional[str] = None,
    pack_name: Optional[str] = None,
    *,
    db: Database,
) -> bool:
    """Отправляет подходящий стикер Сани в чат с логированием в базу SQLite."""
    stk_row = await db.run(Repository.get_random_sticker, emoji, pack_name)
    if not stk_row:
        return False
    try:
        await bot.send_chat_action(chat_id, "choose_sticker")
        await asyncio.sleep(0.3)
        msg = await bot.send_sticker(
            chat_id=chat_id, sticker=stk_row["file_id"], reply_to_message_id=reply_to_id
        )
        bot_user = await bot.get_me()
        await db.run(
            Repository.save_message,
            chat_id,
            msg.message_id,
            reply_to_id,
            bot_user.id,
            "Саня Пониёб",
            "assistant",
            f"[Отправил стикер {stk_row['emoji']} из набора '{stk_row['pack_name']}']",
        )
        logging.info(
            f"[{chat_id}] Отправлен стикер: {stk_row['emoji']} ({stk_row['pack_name']})"
        )
        return True
    except Exception as e:
        logging.error(f"Ошибка отправки стикера в чат {chat_id}: {e}")
        return False


async def sync_sticker_packs(bot: Bot, db: Database):
    """Фоновая синхронизация и кэширование стикеров из Telegram API при старте."""
    logging.info("[*] Проверка и синхронизация стикерпаков...")
    for pack_name in DEFAULT_STICKER_PACKS:
        try:
            cnt = await db.run(Repository.get_cached_stickers_count, pack_name)
            if cnt > 0:
                continue
            s = await bot.get_sticker_set(name=pack_name)
            top_weights = {
                item["file_id"]: item["weight"]
                for item in ORIGINAL_SANYA_TOP_STICKERS
                if item["pack"] == pack_name
            }

            stk_items = []
            for st in s.stickers:
                weight = float(top_weights.get(st.file_id, 0.005))
                for top_item in ORIGINAL_SANYA_TOP_STICKERS:
                    if top_item["pack"] == pack_name and top_item["emoji"] == st.emoji:
                        weight = max(weight, float(top_item["weight"]))

                stk_items.append(
                    {
                        "file_id": st.file_id,
                        "file_unique_id": st.file_unique_id,
                        "pack_name": pack_name,
                        "emoji": st.emoji or "",
                        "file_size": st.file_size or 0,
                        "width": st.width or 0,
                        "height": st.height or 0,
                        "sanya_weight": weight,
                    }
                )
            if not await db.run(
                Repository.add_sticker_pack,
                pack_name,
                s.title,
                is_default=True,
                stickers=stk_items,
            ):
                continue
            logging.info(
                f"[*] Закэширован базовый стикерпак '{pack_name}' ({len(stk_items)} стикеров)"
            )
        except Exception as e:
            logging.warning(f"Ошибка загрузки стикерпака '{pack_name}': {e}")

    total_packs = len(await db.run(Repository.get_sticker_packs))
    total_stk = await db.run(Repository.get_cached_stickers_count)
    logging.info(
        f"[*] Стикерпаки готовы к использованию: {total_packs} наборов, {total_stk} стикеров в базе"
    )
