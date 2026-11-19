import asyncio
import logging
import random
import re
import urllib.parse
from hashlib import sha256
from typing import Optional, Tuple

import httpx
from ddgs import DDGS

from ..config import SEARCH_ENGINE, SEARCH_PROXY, SEARXNG_URL


def extract_image_query(text: str) -> str:
    """Извлекает ключевые слова для поиска картинок из текста или команды."""
    q = re.sub(r"@\w+", "", text)
    q = re.sub(
        r"^/(pic|img|image|photo|картинка|пикча|booru|r34|derpi|буру|р34|дерпи)\s*",
        "",
        q,
        flags=re.IGNORECASE,
    )
    q = re.sub(
        r"\b(саня|санек|сань|спермер|пониеб|пониёб|пожалуйста|плиз|скинь|покажи|найди|отправь|кинь|вруби|дай|запили|"
        r"картинку|фотку|пикчу|фото|изображение|мем|скрин|как выглядит|на\s+буру|на\s+booru|на\s+р34|на\s+r34|арт|артик)\b",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    q = re.sub(r"[?!.,]+$", "", q).strip()
    return q if len(q) > 1 else text.strip()


def pic_query_key(query: str) -> str:
    return sha256(" ".join(query.casefold().split()).encode()).hexdigest()


def pic_identity(content: bytes, url: str) -> tuple[str, str]:
    return _pic_url_key(url), sha256(content).hexdigest()


def _pic_url_key(url: str) -> str:
    return sha256(url.split("#", 1)[0].encode()).hexdigest()


async def search_and_download_image(
    query: str, max_candidates: int = 6, *, history: tuple[tuple[str, str], ...] = ()
) -> Optional[Tuple[bytes, str]]:
    """Try unseen results in search order, then fall back to another source."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    excluded_urls = {url for url, _ in history}
    excluded_digests = {digest for _, digest in history}
    attempted = set()

    async with httpx.AsyncClient(
        headers=headers, timeout=7.0, follow_redirects=True
    ) as client:

        async def select(urls):
            for url in urls:
                if not isinstance(url, str) or not url.startswith(
                    ("http://", "https://")
                ):
                    continue
                url_key = _pic_url_key(url)
                if url_key in excluded_urls or url_key in attempted:
                    continue
                attempted.add(url_key)
                try:
                    resp = await client.get(url)
                except httpx.HTTPError:
                    continue
                if resp.status_code != 200 or len(resp.content) <= 2000:
                    continue
                content_type = resp.headers.get("content-type", "").lower()
                if "image" not in content_type and not url.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                ):
                    continue
                if sha256(resp.content).hexdigest() not in excluded_digests:
                    return resp.content, url
            return None

        if SEARCH_ENGINE == "searxng" and SEARXNG_URL:
            for page in range(1, 3 if history else 2):
                try:
                    response = await client.get(
                        f"{SEARXNG_URL.rstrip('/')}/search",
                        params={
                            "q": query,
                            "categories": "images",
                            "format": "json",
                            "pageno": page,
                        },
                    )
                    response.raise_for_status()
                    items = response.json().get("results", [])[:max_candidates]
                except (httpx.HTTPError, ValueError) as exc:
                    logging.warning("Ошибка поиска картинок SearXNG: %s", exc)
                    break
                if image := await select(
                    item.get("img_src") or item.get("url") for item in items
                ):
                    return image

        def _sync_ddgs_images():
            with DDGS(proxy=SEARCH_PROXY or None) as ddgs:
                limit = max_candidates * (2 if history else 1)
                return list(ddgs.images(query, max_results=limit))[:limit]

        try:
            items = await asyncio.to_thread(_sync_ddgs_images)
        except Exception as exc:
            logging.warning("Ошибка поиска картинок DDG: %s", exc)
            return None
        return await select(item.get("image") for item in items)


PONY_TAG_SYNONYMS = {
    "флаттершай": "fluttershy",
    "радуга дэш": "rainbow dash",
    "дэш": "rainbow dash",
    "радуга": "rainbow dash",
    "пинки пай": "pinkie pie",
    "пинки": "pinkie pie",
    "эпплджек": "applejack",
    "эплджек": "applejack",
    "рарити": "rarity",
    "рэрити": "rarity",
    "луна": "princess luna",
    "селестия": "princess celestia",
    "твайлайт": "twilight sparkle",
    "искорка": "twilight sparkle",
    "дерпи": "derpy hooves",
    "пони": "pony",
    "кони": "pony",
}


async def search_and_download_booru_image(
    query: str, max_candidates: int = 15
) -> Optional[Tuple[bytes, str, str]]:
    """
    Поиск артов на booru и r34:
    1. Derpibooru (MLP / пони / фурри)
    2. Konachan (аниме booru)
    3. SearXNG images (поиск по booru / rule34)
    4. DuckDuckGo fallback
    Возвращает (bytes, url, source) или None.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    clean_q = re.sub(
        r"^/(booru|r34|derpi|буру|р34|дерпи|pic|пикча)\s*",
        "",
        query.strip(),
        flags=re.IGNORECASE,
    ).strip()
    clean_q = re.sub(
        r"\b(найди|скинь|покажи|дай|нарой|откопай|арт|на\s+буру|на\s+booru|на\s+р34|на\s+r34|на\s+дерпи)\b",
        "",
        clean_q,
        flags=re.IGNORECASE,
    ).strip()
    if not clean_q:
        clean_q = "safe"

    q_lower = clean_q.lower()
    for ru_name, en_tag in PONY_TAG_SYNONYMS.items():
        if ru_name in q_lower:
            q_lower = q_lower.replace(ru_name, en_tag)
    tag_query = re.sub(r"\s+", "+", q_lower.strip())
    kona_tag = re.sub(r"\s+", "_", q_lower.strip())

    candidates = []

    # 1. Derpibooru (MLP / Ponybooru)
    try:
        derpi_url = f"https://derpibooru.org/api/v1/json/search/images?q={urllib.parse.quote_plus(tag_query)}&per_page={max_candidates}"
        async with httpx.AsyncClient(
            headers=headers, timeout=6.0, follow_redirects=True
        ) as client:
            r = await client.get(derpi_url)
            if r.status_code == 200:
                data = r.json()
                for img in data.get("images", []):
                    rep = img.get("representations", {})
                    url = (
                        rep.get("large")
                        or rep.get("medium")
                        or rep.get("full")
                        or img.get("view_url")
                    )
                    if (
                        url
                        and url.startswith("http")
                        and not url.lower().endswith((".webm", ".mp4"))
                    ):
                        candidates.append((url, "Derpibooru"))
    except Exception as e:
        logging.warning(f"Ошибка Derpibooru: {e}")

    # 2. Konachan (Anime Booru)
    if not candidates:
        try:
            kona_url = f"https://konachan.net/post.json?tags={urllib.parse.quote_plus(kona_tag)}&limit={max_candidates}"
            async with httpx.AsyncClient(
                headers=headers, timeout=6.0, follow_redirects=True
            ) as client:
                r = await client.get(kona_url)
                if r.status_code == 200:
                    data = r.json()
                    for post in data:
                        url = post.get("file_url") or post.get("sample_url")
                        if (
                            url
                            and url.startswith("http")
                            and not url.lower().endswith((".webm", ".mp4"))
                        ):
                            candidates.append((url, "Konachan"))
        except Exception as e:
            logging.warning(f"Ошибка Konachan: {e}")

    # 3. SearXNG images (поиск по booru / rule34)
    if not candidates and SEARCH_ENGINE == "searxng" and SEARXNG_URL:
        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=6.0, follow_redirects=True
            ) as client:
                r = await client.get(
                    f"{SEARXNG_URL.rstrip('/')}/search",
                    params={
                        "q": f"{clean_q} booru",
                        "categories": "images",
                        "format": "json",
                    },
                )
                if r.status_code == 200:
                    for item in r.json().get("results", [])[:max_candidates]:
                        url = item.get("img_src") or item.get("url")
                        if (
                            url
                            and url.startswith("http")
                            and not url.lower().endswith((".webm", ".mp4"))
                        ):
                            candidates.append((url, "SearXNG Booru"))
        except Exception as e:
            logging.warning(f"Ошибка SearXNG Booru: {e}")

    # 4. DuckDuckGo Booru / Art Images Fallback
    if not candidates:
        try:

            def _sync_ddgs_booru():
                with DDGS(proxy=SEARCH_PROXY if SEARCH_PROXY else None) as ddgs:
                    return list(
                        ddgs.images(f"{clean_q} booru art", max_results=max_candidates)
                    )

            ddg_items = await asyncio.to_thread(_sync_ddgs_booru)
            for item in ddg_items:
                url = item.get("image")
                if (
                    url
                    and url.startswith("http")
                    and not url.lower().endswith((".webm", ".mp4"))
                ):
                    candidates.append((url, "DDG Booru"))
        except Exception as e:
            logging.warning(f"Ошибка DDG Booru: {e}")

    if not candidates:
        return None

    random.shuffle(candidates)
    async with httpx.AsyncClient(
        headers=headers, timeout=8.0, follow_redirects=True
    ) as client:
        for url, src in candidates[:7]:
            try:
                resp = await client.get(url)
                if resp.status_code == 200 and len(resp.content) > 3000:
                    ctype = resp.headers.get("content-type", "").lower()
                    if "image" in ctype or url.lower().endswith(
                        (".jpg", ".jpeg", ".png", ".webp")
                    ):
                        return resp.content, url, src
            except Exception:
                continue

    return None
