import asyncio
import base64
import json
import logging
import re
from datetime import datetime, timezone

import httpx
from aiogram import types

from ..config import ADMIN_IDS
from ..database import Database
from ..llm import Ollama
from ..repository import ImageQuota, Repository
from .safe_mode import safe_mode

log = logging.getLogger(__name__)
MAX_PROMPT = 1000
DAILY_LIMIT = 50
USER_DAILY_LIMIT = 3
ADMIN_DAILY_LIMIT = 10
MAX_RESPONSE = 8_000_000
MODEL = "@cf/black-forest-labs/flux-1-schnell"


class ImageProviderError(Exception):
    def __init__(self, status: int | None = None):
        super().__init__("Image generation failed")
        self.status = status


class FluxClient:
    def __init__(self, client: httpx.AsyncClient, account: str, token: str):
        if not re.fullmatch(r"[a-fA-F0-9]{32}", account) or not re.fullmatch(
            r"[A-Za-z0-9_-]+", token
        ):
            raise ValueError("Invalid Workers AI credentials")
        self._client = client
        self._url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
        self._token = token

    async def generate(self, prompt: str) -> bytes:
        body = bytearray()
        try:
            async with asyncio.timeout(40):
                async with self._client.stream(
                    "POST",
                    self._url,
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"prompt": prompt, "steps": 4},
                    timeout=httpx.Timeout(35, connect=5),
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise ImageProviderError(response.status_code)
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        if len(body) + len(chunk) > MAX_RESPONSE:
                            raise ImageProviderError()
                        body.extend(chunk)
        except httpx.TimeoutException:
            raise TimeoutError() from None
        except httpx.HTTPError:
            raise ImageProviderError() from None

        try:
            data = json.loads(body)
            if data["success"] is not True:
                raise ValueError
            image = base64.b64decode(data["result"]["image"], validate=True)
        except (KeyError, TypeError, ValueError):
            raise ImageProviderError() from None
        if not image.startswith(b"\xff\xd8\xff") or not image.endswith(b"\xff\xd9"):
            raise ImageProviderError()
        return image


class ImageGeneration:
    """One background job, no backlog. Failed attempts still use the local budget."""

    def __init__(self, provider: FluxClient, db: Database, llm: Ollama):
        self._provider = provider
        self._db = db
        self._llm = llm
        self._task: asyncio.Task | None = None
        self._closed = False

    def start(self, message: types.Message, prompt: str) -> bool:
        if self._closed or (self._task is not None and not self._task.done()):
            return False
        self._task = asyncio.create_task(self._run(message, prompt))
        return True

    async def close(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self, message: types.Message, prompt: str) -> None:
        try:
            if await safe_mode(self._db, message.chat.id):
                await message.reply("В сейф-режиме генерация картинок выключена.")
                return
            day = datetime.now(timezone.utc).date().isoformat()
            user_id = message.from_user.id
            personal_limit = ADMIN_DAILY_LIMIT if user_id in ADMIN_IDS else USER_DAILY_LIMIT
            quota = await self._db.run(
                Repository.reserve_image_generation,
                day, DAILY_LIMIT, user_id, personal_limit,
            )
            if quota is ImageQuota.USER_LIMIT:
                await message.reply(
                    f"Твой дневной лимит исчерпан: {personal_limit} из {personal_limit}. Сброс в 00:00 UTC."
                )
                return
            if quota is ImageQuota.GLOBAL_LIMIT:
                await message.reply("Общий дневной лимит картинок закончился. Сброс в 00:00 UTC.")
                return
            await message.reply("Рисую. Это может занять до 45 секунд.")
            formatted = await self._llm.rewrite_image_prompt(prompt) or prompt
            if await safe_mode(self._db, message.chat.id):
                return
            try:
                image = await self._provider.generate(formatted)
            except TimeoutError:
                await message.reply("Генератор не успел ответить. Попробуй позже.")
                return
            except ImageProviderError as exc:
                log.warning("FLUX request failed: status=%s", exc.status)
                if exc.status == 429:
                    await message.reply("Cloudflare ограничил генерацию. Попробуй позже.")
                else:
                    await message.reply("Не получилось сгенерировать картинку. Попробуй позже.")
                return
            if not await safe_mode(self._db, message.chat.id):
                await message.reply_photo(types.BufferedInputFile(image, filename="sanya-flux.jpg"))
        except Exception as exc:
            # Do not emit exception text: it can contain an API request or chat data.
            log.warning("Image job failed: %s", type(exc).__name__)
