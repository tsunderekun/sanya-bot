import asyncio
import logging
import random
import re
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

import httpx

from ..config import RULE34_API_KEY, RULE34_USER_ID, SEARCH_PROXY
from .media import PONY_TAG_SYNONYMS

SINGLE_TAG_HINT = "Слышь, накинь ещё тегов через запятую: например solo, cute, safe."
_RATINGS = frozenset(
    {
        "safe",
        "general",
        "suggestive",
        "questionable",
        "explicit",
        "semi-grimdark",
        "grimdark",
        "grotesque",
    }
)
_MAX_BYTES = 9_000_000
_DOWNLOAD_BATCH = 4
_DOWNLOAD_TIMEOUT = 3.0
log = logging.getLogger(__name__)
_FRANCHISE_ALIASES = {"mlp", "млп", "май литл пони", "мой маленький пони"}
_PONY_MENTION = re.compile(r"(?<![^\W_])(?:pony|ponies|mlp|млп|пони)(?![^\W_])")
_PONY_CHARACTERS = frozenset(PONY_TAG_SYNONYMS.values())
_SAFE_RATINGS = {
    "Gelbooru": {"general", "g"},
    "Konachan": {"safe", "s"},
    "e621": {"s"},
    "Rule34": {"safe", "s", "general", "g"},
}
_SAFE = {"safe", "general"}
_RATING_SYNTAX = r"rating:(general|safe|suggestive|questionable|explicit|semi-grimdark|grimdark|grotesque|g|s|q|e)"
_RATING_ALIASES = {"g": "general", "s": "safe", "q": "questionable", "e": "explicit"}


def _normalize_tag(tag: str) -> str:
    spaced = " ".join(tag.replace("_", " ").split())
    if spaced in _FRANCHISE_ALIASES:
        return "my little pony"
    if spaced in PONY_TAG_SYNONYMS:
        return PONY_TAG_SYNONYMS[spaced]
    if spaced in _PONY_CHARACTERS or _PONY_MENTION.search(spaced):
        return spaced
    return tag


def _sources(tags: list[str]) -> tuple[str, ...]:
    if any(_PONY_MENTION.search(tag) or tag in _PONY_CHARACTERS for tag in tags):
        return ("Derpibooru",)
    sources = ("Gelbooru", "Konachan", "e621")
    if RULE34_API_KEY and RULE34_USER_ID.isascii() and RULE34_USER_ID.isdecimal():
        sources += ("Rule34",)
    return sources


def parse_tags(query: str) -> tuple[list[str], bool]:
    query = re.sub(
        r"^\s*/(?:booru|r34|rule34|derpi|буру|р34|дерпи)(?:@\w+)?(?=\s|$)\s*",
        "",
        query[:400],
        flags=re.IGNORECASE,
    )
    query = re.sub(r"\b(?:booru|r34|rule34|derpi|буру|р34)\b\s*", "", query, flags=re.I)
    parts = []
    for part in query.casefold().split(","):
        tag = " ".join(part.split())
        rating = re.fullmatch(rf"(?:(.+) )?{_RATING_SYNTAX}", tag)
        if rating:
            tag, value = rating.groups()
            if tag:
                parts.append(_normalize_tag(tag))
            parts.append(_RATING_ALIASES.get(value, value))
        elif tag:
            parts.append(_normalize_tag(tag))
    return parts, len(parts) == 1


def requires_confirmation(query: str) -> bool:
    tags, _ = parse_tags(query)
    ratings = _RATINGS.intersection(tags)
    return not ratings or bool(ratings - _SAFE)


def _allowed_url(url: str, source: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        base = {
            "Derpibooru": "derpicdn.net",
            "Gelbooru": "gelbooru.com",
            "Konachan": "konachan.net",
            "e621": "e621.net",
            "Rule34": "rule34.xxx",
            "AIBooru": "cdn.aibooru.download",
        }[source]
        return (
            parsed.scheme == "https"
            and parsed.netloc.lower() == host
            and (host == base or host.endswith("." + base))
            and parsed.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        )
    except ValueError:
        return False


async def _candidates(
    client,
    source: str,
    tags: list[str],
    single: bool,
    page: int,
    size: int,
    *,
    confirmed: bool = False,
):
    ratings = _RATINGS.intersection(tags)
    if ratings - _SAFE and not confirmed:
        return []
    safe = bool(ratings and ratings <= _SAFE) or not confirmed
    common = [tag for tag in tags if tag not in _RATINGS]
    if not common:
        return []
    if source == "Derpibooru":
        selected = {"safe"} if safe else ratings
        query = common + (
            ["(" + " OR ".join(sorted(selected)) + ")"]
            if len(selected) > 1
            else sorted(selected)
        )
        if single:
            query.append("score.gt:25")
        response = await client.get(
            "https://derpibooru.org/api/v1/json/search/images",
            params={"q": ", ".join(query), "page": page, "per_page": size},
        )
        response.raise_for_status()
        posts = response.json().get("images", [])
    elif source == "AIBooru":
        query = [
            tag.replace(" ", "_")
            for tag in common
            if re.fullmatch(r"[\w ()'-]+", tag) and not tag.startswith("-")
        ]
        if len(query) != len(common):
            return []
        native = {
            "safe": "g",
            "general": "g",
            "suggestive": "s",
            "questionable": "q",
            "explicit": "e",
        }
        if ratings - native.keys():
            return []
        allowed = {"g"} if safe else {native[value] for value in ratings}
        if len(allowed) == 1:
            query.append("rating:" + next(iter(allowed)))
        response = await client.get(
            "https://aibooru.online/posts.json",
            params={"tags": " ".join(query), "page": page, "limit": size},
        )
        response.raise_for_status()
        posts = response.json()
    else:
        # Translate comma-separated tags and enforce the expected rating locally too.
        query = [
            tag.replace(" ", "_") for tag in common if re.fullmatch(r"[\w ()'-]+", tag)
        ]
        if len(query) != len(common):
            return []
        selected = ratings - _SAFE
        allowed = _SAFE_RATINGS[source] if safe else set()
        if safe:
            native = {"Gelbooru": "general", "e621": "s"}.get(source, "safe")
            if source == "Rule34" and ratings == {"general"}:
                native, allowed = "general", {"general", "g"}
            elif source == "Rule34" and ratings == {"safe"}:
                allowed = {"safe", "s"}
            query.append(f"rating:{native}")
        elif ratings:
            if not selected <= {"questionable", "explicit"}:
                return []
            names = {"questionable": "q", "explicit": "e"}
            allowed = selected | {names[value] for value in selected}
            if len(selected) == 1 and not ratings & _SAFE:
                native = next(iter(selected))
                query.append(
                    f"rating:{names[native] if source in {'Konachan', 'e621'} else native}"
                )
            if ratings & _SAFE:
                allowed |= _SAFE_RATINGS[source]
        if single:
            query.append("score:>25")
        params = {"tags": " ".join(query), "limit": size}
        if source in {"Gelbooru", "Rule34"}:
            params.update(page="dapi", s="post", q="index", json=1, pid=page - 1)
            url = "https://gelbooru.com/index.php"
            if source == "Rule34":
                url = "https://api.rule34.xxx/index.php"
                params.update(user_id=RULE34_USER_ID, api_key=RULE34_API_KEY)
            response = await client.get(url, params=params)
            response.raise_for_status()
            if not response.content.strip():
                return []
            payload = response.json()
            posts = payload.get("post", []) if isinstance(payload, dict) else payload
        elif source == "e621":
            response = await client.get(
                "https://e621.net/posts.json", params={**params, "page": page}
            )
            response.raise_for_status()
            posts = response.json().get("posts", [])
        else:
            response = await client.get(
                "https://konachan.net/post.json", params={**params, "page": page}
            )
            response.raise_for_status()
            posts = response.json()
    if not isinstance(posts, list):
        return []
    candidates = []
    for post in posts[:size]:
        if not isinstance(post, dict) or not str(post.get("id", "")).isdecimal():
            continue
        if source == "Derpibooru":
            if selected and not selected.intersection(post.get("tags") or []):
                continue
            images = post.get("representations") or {}
            url = images.get("large") or images.get("medium") or post.get("view_url")
        else:
            if (safe or ratings) and post.get("rating") not in allowed:
                continue
            if source == "AIBooru":
                url = post.get("large_file_url") or post.get("file_url")
            elif source == "e621":
                url = (post.get("sample") or {}).get("url") or (
                    post.get("file") or {}
                ).get("url")
            else:
                url = post.get("sample_url") or post.get("file_url")
        score = post.get("score", "")
        if source == "e621" and isinstance(score, dict):
            score = score.get("total", "")
        if single and (
            not str(score).removeprefix("-").isdecimal() or int(score) <= 25
        ):
            continue
        if isinstance(url, str) and _allowed_url(url, source):
            candidates.append((f"{source}:{post['id']}", url, source))
    return candidates


class BooruUnavailable(Exception):
    def __init__(self, stage: str):
        super().__init__(stage)
        self.stage = stage


async def search_and_download_booru_image(
    query: str,
    *,
    seen: set[str],
    max_candidates: int = 15,
    confirmed: bool = False,
    slop: bool = False,
):
    tags, single = parse_tags(query)
    if not tags:
        return None
    sources = ("AIBooru",) if slop else _sources(tags)
    client_options = dict(
        timeout=5,
        follow_redirects=False,
        headers={"User-Agent": "SanyaTelegramBot/1.0 (Telegram: @ponyeb228_bot)"},
    )
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                **client_options,
                **({"proxy": SEARCH_PROXY} if slop and SEARCH_PROXY else {}),
            )
        )
        rule34_api = client
        rule34_download = client
        if "Rule34" in sources and SEARCH_PROXY:
            # Keep authenticated API traffic off proxies, including ambient HTTP_PROXY.
            rule34_api = await stack.enter_async_context(
                httpx.AsyncClient(**client_options, trust_env=False)
            )
            rule34_download = await stack.enter_async_context(
                httpx.AsyncClient(**client_options, proxy=SEARCH_PROXY, trust_env=False)
            )

        async def fetch(source, page):
            try:
                return await _candidates(
                    rule34_api if source == "Rule34" else client,
                    source,
                    tags,
                    single,
                    page,
                    max_candidates,
                    confirmed=confirmed,
                )
            except (
                httpx.HTTPError,
                ValueError,
                TypeError,
                AttributeError,
                KeyError,
            ) as exc:
                log.debug(
                    "Booru source=%s page=%s error=%s", source, page, type(exc).__name__
                )
                return None

        attempted = set(seen)

        async def download(image_id, url, source):
            download_client = rule34_download if source == "Rule34" else client
            async with download_client.stream("GET", url) as response:
                if response.status_code != 200 or response.headers.get(
                    "content-type", ""
                ).split(";")[0] not in {"image/jpeg", "image/png", "image/webp"}:
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    data.extend(chunk)
                    if len(data) > _MAX_BYTES:
                        return None
                if data:
                    return bytes(data), url, source, image_id, single
            return None

        async def try_download(row):
            try:
                return await asyncio.wait_for(download(*row), _DOWNLOAD_TIMEOUT)
            except (httpx.HTTPError, asyncio.TimeoutError):
                return None

        stage = "search"

        async def search():
            nonlocal stage

            async def fetch_page(page):
                return await asyncio.gather(
                    *(fetch(source, page) for source in sources)
                )

            pages = await asyncio.gather(
                fetch_page(random.randint(2, 5)), fetch_page(1)
            )
            candidates = []
            for batches in pages:
                page_rows = [row for batch in batches if batch for row in batch]
                random.shuffle(page_rows)
                candidates.append(page_rows)
            # Alternate pages so a slow CDN on the random page can't starve page one.
            fresh = []
            for i in range(max(map(len, candidates))):
                for rows in candidates:
                    if i < len(rows) and rows[i][0] not in attempted:
                        attempted.add(rows[i][0])
                        fresh.append(rows[i])
            if not fresh:
                if all(batch is None for page in pages for batch in page):
                    raise BooruUnavailable("search")
                return None
            stage = "download"
            for offset in range(0, len(fresh), _DOWNLOAD_BATCH):
                tasks = [
                    asyncio.create_task(try_download(row))
                    for row in fresh[offset : offset + _DOWNLOAD_BATCH]
                ]
                try:
                    for task in asyncio.as_completed(tasks):
                        if result := await task:
                            return result
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
            raise BooruUnavailable("download")

        try:
            return await asyncio.wait_for(search(), timeout=15)
        except asyncio.TimeoutError as exc:
            raise BooruUnavailable(stage) from exc
