import asyncio
import logging
import random
import re
from collections import OrderedDict
from html import unescape
from pathlib import Path
from time import monotonic

import httpx
from aiogram import Router, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command

from ..config import (
    MEME_DONOR_CHANNEL_IDS,
    SEARCH_PROXY,
    SLOIPER_CHANCE,
    SLOIPER_DIR,
    TELEGRAM_PROXY,
)
from ..database import Database
from ..repository import Repository

HORSE = re.compile(r"\b(?:хорс[а-яё]*|horse[a-z]*)\b", re.IGNORECASE)
logger = logging.getLogger(__name__)
_cooldowns: OrderedDict[int, float] = OrderedDict()
_MEDIA_URL = re.compile(
    r"https://cdn\d*\.(?:telegram-cdn\.org|telesco\.pe)/file/[\w-]+\.(jpg|jpeg|png|mp4|gif)(?:\?[\w=&%+.-]+)?",
    re.IGNORECASE,
)


async def collect_channel_post(message: types.Message, db: Database):
    if (
        message.has_protected_content
        or getattr(message.chat, "has_protected_content", False)
        or getattr(message, "is_paid_post", False)
        or message.has_media_spoiler
    ):
        return
    if MEME_DONOR_CHANNEL_IDS and message.chat.id not in MEME_DONOR_CHANNEL_IDS:
        return
    for kind, media in (
        ("animation", message.animation),
        ("video", message.video),
        ("photo", message.photo[-1] if message.photo else None),
    ):
        if media:
            await db.run(
                Repository.save_channel_meme,
                message.chat.id,
                message.message_id,
                kind,
                media.file_id,
                media.file_unique_id,
            )
            logger.info(
                "Captured %s meme from channel %s (message %s)",
                kind,
                message.chat.id,
                message.message_id,
            )
            return


def preview_media(page: str) -> list[tuple[str, str]]:
    # Telegram puts actual media inside the widget message; ignore avatars and posters.
    result = []
    for block in re.split(r'<div class="tgme_widget_message_wrap\b', page)[1:]:
        if 'data-post="mestodm/' not in block or re.search(
            r"data-(?:nopforwards|no-forward)|tgme_widget_message_protected|\bblured\b|tgme_widget_message_media_spoiler",
            block,
            re.IGNORECASE,
        ):
            continue
        video = re.search(r"<video\b[^>]*\bsrc=[\'\"]([^\'\"]+)", block, re.IGNORECASE)
        if video:
            match = _MEDIA_URL.fullmatch(unescape(video[1]))
            if match and match[1].lower() == "mp4":
                result.append(
                    (
                        "animation" if "tgme_widget_message_anim" in block else "video",
                        match[0],
                    )
                )
            continue
        for tag in re.findall(
            r"<a\s[^>]*tgme_widget_message_photo_wrap[^>]*>", block, re.IGNORECASE
        ):
            if match := _MEDIA_URL.search(tag):
                result.append(("photo", match[0]))
    return list(dict.fromkeys(result))


async def public_memes() -> list[tuple[str, str]]:
    try:
        proxy = TELEGRAM_PROXY or SEARCH_PROXY or None
        async with httpx.AsyncClient(proxy=proxy, timeout=6, follow_redirects=False) as client:
            response = await client.get("https://t.me/s/mestodm")
            response.raise_for_status()
        return preview_media(response.text)
    except httpx.HTTPError:
        return []


async def cmd_meme(message: types.Message, db: Database):
    rows = await db.run(Repository.random_channel_memes)
    candidates = [(row["media_type"], row["file_id"]) for row in rows]
    if not candidates:
        candidates = await public_memes()
        random.shuffle(candidates)
    for kind, media in candidates[:5]:
        send = {
            "photo": message.answer_photo,
            "video": message.answer_video,
            "animation": message.answer_animation,
        }[kind]
        try:
            if media.startswith(("http://", "https://")):
                proxy = TELEGRAM_PROXY or SEARCH_PROXY or None
                async with httpx.AsyncClient(proxy=proxy, timeout=12) as client:
                    resp = await client.get(media)
                    resp.raise_for_status()
                ext = "mp4" if kind == "video" else ("gif" if kind == "animation" else "jpg")
                payload = types.BufferedInputFile(resp.content, filename=f"meme.{ext}")
            else:
                payload = media
            await send(payload)
            return
        except (TelegramBadRequest, TelegramForbiddenError, httpx.HTTPError) as err:
            logger.debug("Telegram не принял медиа из пула мемов: %s", err)
    await message.reply("Не нашёл мем, который могу отправить.")


def local_horse_art(folder: Path) -> Path | None:
    try:
        files = [
            p
            for p in folder.rglob("*")
            if p.suffix.lower() == ".png"
            and not p.is_symlink()
            and p.is_file()
            and p.stat().st_size <= 10_000_000
        ]
    except OSError:
        return None
    return random.choice(files) if files else None


async def maybe_send_horse(message: types.Message, text: str) -> bool:
    if not text or text.lstrip().startswith("/") or not HORSE.search(text):
        return False
    chat_id, now = message.chat.id, monotonic()
    if now < _cooldowns.get(chat_id, 0) or random.random() >= SLOIPER_CHANCE:
        return False
    path = await asyncio.to_thread(local_horse_art, SLOIPER_DIR)
    if path is None:
        return False
    _cooldowns[chat_id] = now + 150
    _cooldowns.move_to_end(chat_id)
    if len(_cooldowns) > 2048:
        _cooldowns.popitem(last=False)
    try:
        await message.answer_photo(types.FSInputFile(path))
        return True
    except (TelegramBadRequest, TelegramForbiddenError, OSError):
        return False


def make_router() -> Router:
    router = Router(name=__name__)
    router.channel_post.register(collect_channel_post)
    router.message.register(cmd_meme, Command("meme", "мем"))
    return router
