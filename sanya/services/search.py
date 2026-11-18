import asyncio
import logging
import re
from typing import Dict, List

import httpx
from ddgs import DDGS

from ..config import SEARCH_ENGINE, SEARCH_PROXY, SEARXNG_URL


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
        logging.warning(f"Ошибка запроса к SearXNG ({endpoint}): {e}")
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
        logging.warning(f"Ошибка DDG поиска '{query}': {e}")
        return []
