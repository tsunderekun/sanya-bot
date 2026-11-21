import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Dict, List
from urllib.parse import urlsplit

import httpx
from ddgs import DDGS

from ..config import SEARCH_ENGINE, SEARCH_PROXY, SEARXNG_URL
from .nvidia_specs import RTX_3070_PAGE, fetch_specs, requested_model


@dataclass(frozen=True, slots=True)
class SearchEvidence:
    snippets: str
    urls: tuple[str, ...] = ()
    official_answer: str = ""
    official_unconfirmed: bool = False


def _source_url(value: str) -> str:
    if not value or len(value) > 800 or re.search(r"[\s\x00-\x1f\x7f<>\\]", value):
        return ""
    try:
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and parsed.hostname and not (
            parsed.username is not None or parsed.password is not None
        ):
            return value
    except ValueError:
        pass
    return ""


async def lookup_search_evidence(query: str) -> SearchEvidence:
    model = requested_model(query)
    if model and (answer := await fetch_specs(model)):
        return SearchEvidence("", (RTX_3070_PAGE,), official_answer=answer)
    rows = await perform_web_search(query[:500], max_results=3)
    urls = []
    snippets = []
    for row in rows:
        if not (body := row.get("body")):
            continue
        url = _source_url(row.get("url") or row.get("href") or "")
        if url and url not in urls:
            urls.append(url)
        domain = urlsplit(url).hostname if url else ""  # No raw URLs in model output.
        label = f"Источник {urls.index(url) + 1} ({domain}): " if url else ""
        snippets.append(f"{label}{(row.get('title') or '')[:120]}: {body[:400]}")
    return SearchEvidence("\n".join(snippets), tuple(urls), official_unconfirmed=bool(model))


async def _search_searxng(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Поиск через локальный инстанс SearXNG (формат json)."""
    endpoint = f"{SEARXNG_URL.rstrip('/')}/search"
    params = {"q": query, "format": "json", "language": "ru-RU", "safesearch": 0}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SanyaPonyebBot/1.0",
        "X-Forwarded-For": "127.0.0.1",
        "X-Real-IP": "127.0.0.1",
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(endpoint, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
            raw = data.get("results", [])
            results = []
            for item in raw[:max_results]:
                title = (item.get("title") or "").strip()
                content = (item.get("content") or "").strip()
                if content:
                    results.append(
                        {"title": title, "body": content, "url": item.get("url", "")}
                    )
            return results
    except Exception as e:
        logging.warning("Ошибка запроса к SearXNG: %s", type(e).__name__)
        return []


def _sync_ddgs_search(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Поиск в сети через DuckDuckGo с фоллбэками."""
    queries_to_try = [query]
    if "авито" in query.lower():
        queries_to_try.append(re.sub(r"авито", "", query, flags=re.IGNORECASE).strip())

    proxy = SEARCH_PROXY if SEARCH_PROXY else None

    for q in queries_to_try:
        if not q:
            continue
        try:
            with DDGS(proxy=proxy) as ddgs:
                results = ddgs.text(q, max_results=max_results)
                if results:
                    return results
        except Exception:
            pass
    return []


async def perform_web_search(query: str, max_results: int = 3) -> List[Dict[str, str]]:
    """Асинхронная обертка веб-поиска (SearXNG с фоллбэком на DuckDuckGo)."""
    if SEARCH_ENGINE == "searxng" and SEARXNG_URL:
        results = await _search_searxng(query, max_results=max_results)
        if results:
            return results
        logging.info(
            "SearXNG не вернул результатов, переключаемся на DuckDuckGo фоллбэк"
        )

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_sync_ddgs_search, query, max_results), timeout=9.0
        )
    except Exception as e:
        logging.warning("Ошибка DDG поиска: %s", type(e).__name__)
        return []
