import asyncio
import logging
import random
import re
from collections import OrderedDict
from html import unescape
from pathlib import Path
from time import monotonic

import httpx
from aiogram import Bot, Router, types
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
_recent_public_urls: OrderedDict[int, list[str]] = OrderedDict()
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


def extract_preview_posts(page: str) -> list[tuple[int, str, str]]:
    result = []
    for block in re.split(r'<div class="tgme_widget_message_wrap\b', page)[1:]:
        post_match = re.search(r'data-post="[\w_]+/(\d+)"', block)
        if not post_match or re.search(
            r"data-(?:nopforwards|no-forward)|tgme_widget_message_protected|\bblured\b|tgme_widget_message_media_spoiler",
            block,
            re.IGNORECASE,
        ):
            continue
        msg_id = int(post_match[1])
        video = re.search(r"<video\b[^>]*\bsrc=[\'\"]([^\'\"]+)", block, re.IGNORECASE)
        if video:
            match = _MEDIA_URL.fullmatch(unescape(video[1]))
            if match and match[1].lower() == "mp4":
                result.append(
                    (
                        msg_id,
                        "animation" if "tgme_widget_message_anim" in block else "video",
                        match[0],
                    )
                )
                continue
        for tag in re.findall(
            r"<a\s[^>]*tgme_widget_message_photo_wrap[^>]*>", block, re.IGNORECASE
        ):
            if match := _MEDIA_URL.search(tag):
                result.append((msg_id, "photo", match[0]))
                break
    return result


def preview_media(page: str) -> list[tuple[str, str]]:
    # Telegram puts actual media inside the widget message; ignore avatars and posters.
    return [(kind, url) for _, kind, url in extract_preview_posts(page)]


async def public_memes(channel: str = "mestodm") -> list[tuple[str, str]]:
    try:
        proxy = TELEGRAM_PROXY or SEARCH_PROXY or None
        async with httpx.AsyncClient(
            proxy=proxy, timeout=6, follow_redirects=False
        ) as client:
            response = await client.get(f"https://t.me/s/{channel}")
            response.raise_for_status()
        return preview_media(response.text)
    except httpx.HTTPError:
        return []


async def precache_channel_memes(
    bot: Bot,
    db: Database,
    channel: str = "mestodm",
    max_pages: int = 10,
) -> dict[str, int]:
    clean_channel = channel.strip().lstrip("@").split("/")[-1]
    channel_id = -1003333441777 if clean_channel.lower() == "mestodm" else None
    if channel_id is None:
        try:
            chat = await bot.get_chat(f"@{clean_channel}")
            channel_id = chat.id
        except Exception:
            channel_id = -abs(hash(clean_channel))

    proxy = TELEGRAM_PROXY or SEARCH_PROXY or None
    url = f"https://t.me/s/{clean_channel}"
    new_saved = 0
    total_found = 0
    seen_msg_ids = set()

    async with httpx.AsyncClient(
        proxy=proxy, timeout=12, follow_redirects=True
    ) as client:
        for _ in range(max_pages):
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    break
            except httpx.HTTPError:
                break
            posts = extract_preview_posts(resp.text)
            for msg_id, kind, media_url in posts:
                if msg_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(msg_id)
                total_found += 1
                saved = await db.run(
                    Repository.save_channel_meme,
                    channel_id,
                    msg_id,
                    kind,
                    media_url,
                    f"web_{msg_id}",
                )
                if saved:
                    new_saved += 1
            all_posts = [
                int(m)
                for m in re.findall(r'data-post="[\w_]+/(\d+)"', resp.text)
                if m.isdigit()
            ]
            if not all_posts:
                break
            earliest_id = min(all_posts)
            url = f"https://t.me/s/{clean_channel}?before={earliest_id}"
            await asyncio.sleep(0.2)

    total_pool = await db.run(Repository.count_channel_memes)
    return {"new": new_saved, "found": total_found, "total": total_pool}


async def cmd_precache(message: types.Message, bot: Bot, db: Database):
    args = (message.text or "").split()[1:]
    channel = "mestodm"
    pages = 10
    if args:
        if args[0].isdigit():
            pages = int(args[0])
        else:
            channel = args[0]
            if len(args) > 1 and args[1].isdigit():
                pages = int(args[1])
    pages = max(1, min(30, pages))
    clean_channel = channel.strip().lstrip("@").split("/")[-1]
    status_msg = await message.reply(
        f"⏳ Шуршу по каналу @{clean_channel}, тяну мемы в базу ({pages} стр.)..."
    )
    try:
        stats = await precache_channel_memes(
            bot, db, channel=clean_channel, max_pages=pages
        )
        await status_msg.edit_text(
            f"Просмотрел {stats['found']} постов с медиа из @{clean_channel}; "
            f"новых добавил: {stats['new']}\n"
            f"Всего в пуле: {stats['total']} мемов. Уже сохранённые посты повторно не добавляю."
        )
    except Exception as e:
        logger.exception("Ошибка прекэша мемов: %s", e)
        await status_msg.edit_text(f"Чет не вышло затянуть мемы братан: {e}")


async def cmd_meme(message: types.Message, db: Database):
    chat_id = message.chat.id
    rows = await db.run(Repository.get_channel_memes_with_penalty, chat_id, 8)
    candidates = [
        (
            row.get("id")
            if isinstance(row, dict) or hasattr(row, "get")
            else (row["id"] if "id" in row.keys() else None),
            row["media_type"],
            row["file_id"],
        )
        for row in rows
    ]
    if not candidates:
        raw_public = await public_memes()
        recent_urls = _recent_public_urls.get(chat_id, [])
        unseen = [c for c in raw_public if c[1] not in recent_urls]
        seen = [c for c in raw_public if c[1] in recent_urls]
        random.shuffle(unseen)
        random.shuffle(seen)
        candidates = [(None, kind, url) for kind, url in (unseen + seen)]

    for meme_id, kind, media in candidates[:6]:
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
                ext = (
                    "mp4"
                    if kind == "video"
                    else ("gif" if kind == "animation" else "jpg")
                )
                payload = types.BufferedInputFile(resp.content, filename=f"meme.{ext}")
            else:
                payload = media
            sent_msg = await send(payload)
            if meme_id is not None:
                await db.run(Repository.record_meme_history, chat_id, meme_id)
                if media.startswith(("http://", "https://")) and sent_msg:
                    new_fid, new_uniq = None, None
                    if sent_msg.photo:
                        new_fid = sent_msg.photo[-1].file_id
                        new_uniq = sent_msg.photo[-1].file_unique_id
                    elif sent_msg.video:
                        new_fid = sent_msg.video.file_id
                        new_uniq = sent_msg.video.file_unique_id
                    elif sent_msg.animation:
                        new_fid = sent_msg.animation.file_id
                        new_uniq = sent_msg.animation.file_unique_id
                    if new_fid and new_uniq:
                        await db.run(
                            Repository.update_channel_meme_file_id,
                            meme_id,
                            new_fid,
                            new_uniq,
                        )
            else:
                recent = _recent_public_urls.setdefault(chat_id, [])
                recent.append(media)
                if len(recent) > 100:
                    recent.pop(0)
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
    router.message.register(
        cmd_precache,
        Command("precache", "прекэш", "прекеш", "кэшмемов", "кешмемов"),
    )
    return router
