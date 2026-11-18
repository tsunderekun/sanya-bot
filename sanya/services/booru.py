import asyncio
import random
import re
from itertools import chain
from urllib.parse import urlsplit

import httpx

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


def parse_tags(query: str) -> tuple[list[str], bool]:
    query = re.sub(
        r"\b(?:booru|r34|rule34|derpi|буру|р34|дерпи)\b\s*",
        "",
        query[:400],
        flags=re.IGNORECASE,
    )
    parts = [
        PONY_TAG_SYNONYMS.get(tag, tag)
        for part in query.casefold().split(",")
        if (tag := " ".join(part.split()))
    ]
    return parts, len(parts) == 1


def _allowed_url(url: str, source: str) -> bool:
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        base = {
            "Derpibooru": "derpicdn.net",
            "Gelbooru": "gelbooru.com",
            "Konachan": "konachan.net",
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
    client, source: str, tags: list[str], single: bool, page: int, size: int
):
    ratings = _RATINGS.intersection(tags)
    safe = not ratings or ratings <= {"safe", "general"}
    common = [tag for tag in tags if tag not in _RATINGS]
    if source == "Derpibooru":
        query = common + (["safe"] if safe else sorted(ratings))
        if single:
            query.append("score.gt:25")
        response = await client.get(
            "https://derpibooru.org/api/v1/json/search/images",
            params={"q": ", ".join(query), "page": page, "per_page": size},
        )
        response.raise_for_status()
        posts = response.json().get("images", [])
    else:
        if not safe:
            return []
        # Translate comma-separated tags and enforce the expected rating locally too.
        query = [
            tag.replace(" ", "_") for tag in common if re.fullmatch(r"[\w ()'-]+", tag)
        ]
        if len(query) != len(common):
            return []
        query.append("rating:general" if source == "Gelbooru" else "rating:safe")
        if single:
            query.append("score:>25")
        params = {"tags": " ".join(query), "limit": size}
        if source == "Gelbooru":
            params.update(page="dapi", s="post", q="index", json=1, pid=page - 1)
            response = await client.get("https://gelbooru.com/index.php", params=params)
            response.raise_for_status()
            payload = response.json()
            posts = payload.get("post", []) if isinstance(payload, dict) else payload
        else:
            response = await client.get(
                "https://konachan.net/post.json", params={**params, "page": page}
            )
            response.raise_for_status()
            posts = response.json()
    candidates = []
    for post in posts[:size]:
        if not isinstance(post, dict) or not str(post.get("id", "")).isdigit():
            continue
        if source == "Derpibooru":
            if safe and "safe" not in (post.get("tags") or []):
                continue
            images = post.get("representations") or {}
            url = images.get("large") or images.get("medium") or post.get("view_url")
        else:
            if post.get("rating") not in (
                {"general", "g"} if source == "Gelbooru" else {"s", "safe"}
            ):
                continue
            url = post.get("sample_url") or post.get("file_url")
        if single and (
            not str(post.get("score", "")).lstrip("-").isdigit()
            or int(post["score"]) <= 25
        ):
            continue
        if isinstance(url, str) and _allowed_url(url, source):
            candidates.append((f"{source}:{post['id']}", url, source))
    return candidates


async def search_and_download_booru_image(
    query: str, *, seen: set[str], max_candidates: int = 15
):
    tags, single = parse_tags(query)
    if not tags:
        return None
    async with httpx.AsyncClient(
        timeout=5,
        follow_redirects=False,
        headers={"User-Agent": "SanyaTelegramBot/1.0"},
    ) as client:

        async def fetch(source, page):
            try:
                return await _candidates(
                    client, source, tags, single, page, max_candidates
                )
            except (httpx.HTTPError, ValueError, TypeError, AttributeError, KeyError):
                return []

        sources = ("Derpibooru", "Gelbooru", "Konachan")
        attempted = set(seen)

        async def search():
            for page in (random.randint(2, 5), 1):
                candidates = list(
                    chain.from_iterable(
                        await asyncio.gather(
                            *(fetch(source, page) for source in sources)
                        )
                    )
                )
                random.shuffle(candidates)
                for image_id, url, source in candidates:
                    if image_id in attempted:
                        continue
                    attempted.add(image_id)
                    try:
                        async with client.stream("GET", url) as response:
                            if response.status_code != 200 or response.headers.get(
                                "content-type", ""
                            ).split(";")[0] not in {
                                "image/jpeg",
                                "image/png",
                                "image/webp",
                            }:
                                continue
                            data = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                data.extend(chunk)
                                if len(data) > _MAX_BYTES:
                                    break
                            if 0 < len(data) <= _MAX_BYTES:
                                return bytes(data), url, source, image_id, single
                    except httpx.HTTPError:
                        continue

        try:
            return await asyncio.wait_for(search(), timeout=15)
        except (TimeoutError, asyncio.TimeoutError):  # noqa: UP041 — Python 3.10 compatibility
            return None
