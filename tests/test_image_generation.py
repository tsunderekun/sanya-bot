import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import httpx

from sanya.database import Database
from sanya.handlers.media import cmd_draw
from sanya.llm import Ollama
from sanya.repository import ImageQuota, Repository
from sanya.services.image_generation import (
    FluxClient,
    ImageGeneration,
    ImageProviderError,
)


class ImageGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(folder) / "test.db"
        self.db = await self.enterAsyncContext(Database(self.path))
        self.message = NS(
            chat=NS(id=42, type="private"), from_user=NS(id=42),
            text='/нарисуй трамвай с надписью «Депо»', caption="не отправлять",
            reply_to_message=NS(text="это тоже не отправлять"),
            reply=AsyncMock(), reply_photo=AsyncMock(),
        )
        self.description = self.message.text.split(maxsplit=1)[1]

    async def test_command_guards(self):
        service = Mock()
        await cmd_draw(self.message)
        self.assertIn("выключена", self.message.reply.await_args.args[0])
        self.message.chat.type = "supergroup"
        await cmd_draw(self.message, service)
        self.message.chat.type = "private"
        self.message.from_user = None
        await cmd_draw(self.message, service)
        self.message.from_user = NS(id=42)
        self.message.text = "/draw"
        await cmd_draw(self.message, service)
        self.message.text = "/draw " + "x" * 1001
        await cmd_draw(self.message, service)
        service.start.assert_not_called()

    async def test_rewrite_then_flux_with_no_chat_data(self):
        panel = 'A tram with the lettering "Депо".'
        jpeg = b"\xff\xd8\xffmock\xff\xd9"
        requests = []

        def respond(request):
            payload = json.loads(request.content)
            requests.append(request)
            if request.url.path == "/api/chat":
                self.assertEqual(len(payload["messages"]), 2)
                self.assertEqual(json.loads(payload["messages"][1]["content"]), {
                    "description": self.description,
                })
                self.assertFalse(payload["think"])
                return httpx.Response(200, json={"message": {
                    "content": json.dumps({"prompt": panel}), "thinking": "DO NOT LOG",
                }})
            self.assertEqual(request.url.host, "api.cloudflare.com")
            self.assertEqual(payload, {"prompt": panel, "steps": 4})
            return httpx.Response(200, json={"success": True, "result": {
                "image": base64.b64encode(jpeg).decode(),
            }})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            service = ImageGeneration(FluxClient(client, "0" * 32, "dummy"), self.db, Ollama(client))
            self.addAsyncCleanup(service.close)
            with patch("sanya.llm.thought_log") as log:
                await cmd_draw(self.message, service)
                self.assertFalse(service.start(self.message, "another request"))
                await service._task
                log.debug.assert_not_called()
        self.assertEqual(len(requests), 2)
        self.assertEqual(self.message.reply_photo.await_args.args[0].data, jpeg)

    async def test_busy_invalid_or_missing_lettering_uses_original(self):
        for user_id, (busy, response, error) in enumerate((
            (True, '{"prompt":"unused"}', None),
            (False, '{"prompt":"A tram without the requested lettering."}', None),
            (False, "not JSON", None),
            (False, "", httpx.ReadTimeout("test timeout")),
        ), start=100):
            with self.subTest(busy=busy, response=response, error=error):
                self.message.from_user.id = user_id
                llm = Ollama(Mock())
                llm._post = AsyncMock(return_value=(response, "stop"), side_effect=error)
                if busy:
                    await llm._lock.acquire()
                provider = NS(generate=AsyncMock(return_value=b"image"))
                service = ImageGeneration(provider, self.db, llm)
                self.assertTrue(service.start(self.message, self.description))
                await asyncio.wait_for(service._task, 1)
                provider.generate.assert_awaited_once_with(self.description)
                if busy:
                    llm._post.assert_not_called()

    async def test_safe_mode_skips_rewrite_and_provider(self):
        await self.db.run(Repository.set_chat_reply_mode, 42, "safe")
        llm = NS(rewrite_image_prompt=AsyncMock())
        provider = NS(generate=AsyncMock())
        service = ImageGeneration(provider, self.db, llm)
        self.assertTrue(service.start(self.message, self.description))
        await service._task
        llm.rewrite_image_prompt.assert_not_called()
        provider.generate.assert_not_called()

    async def test_quota_rejection_skips_rewrite_and_provider(self):
        from datetime import datetime, timezone

        day = datetime.now(timezone.utc).date().isoformat()
        self.assertIs(
            await self.db.run(Repository.reserve_image_generation, day, 1, 42, None),
            ImageQuota.RESERVED,
        )
        llm = NS(rewrite_image_prompt=AsyncMock())
        provider = NS(generate=AsyncMock())
        service = ImageGeneration(provider, self.db, llm)
        with patch("sanya.services.image_generation.DAILY_LIMIT", 1):
            self.assertTrue(service.start(self.message, self.description))
            await service._task
        self.assertIn("Общий дневной лимит", self.message.reply.await_args.args[0])
        llm.rewrite_image_prompt.assert_not_called()
        provider.generate.assert_not_called()

    async def test_sqlite_budget_survives_reopen_and_resets_next_day(self):
        day = "2026-11-22"
        self.assertIs(
            await self.db.run(Repository.reserve_image_generation, day, 2, 42, 3),
            ImageQuota.RESERVED,
        )
        async with Database(self.path) as reopened:
            results = await asyncio.gather(*(
                db.run(Repository.reserve_image_generation, day, 2, 42, 3)
                for db in (self.db, reopened)
            ))
            self.assertCountEqual(results, [ImageQuota.GLOBAL_LIMIT, ImageQuota.RESERVED])
            self.assertIs(
                await reopened.run(Repository.reserve_image_generation, "2026-11-23", 2, 42, 3),
                ImageQuota.RESERVED,
            )

    async def test_three_per_user_ten_per_admin_and_global_limit(self):
        llm = NS(rewrite_image_prompt=AsyncMock(return_value=""))
        provider = NS(generate=AsyncMock(return_value=b"image"))
        service = ImageGeneration(provider, self.db, llm)
        with patch("sanya.services.image_generation.ADMIN_IDS", {44, 45, 46, 47, 48}):
            for _ in range(4):
                await cmd_draw(self.message, service)
                await service._task
            self.assertEqual(provider.generate.await_count, 3)
            self.assertIn("3 из 3", self.message.reply.await_args.args[0])
            self.message.from_user.id = 43
            await cmd_draw(self.message, service)
            await service._task
            self.assertEqual(provider.generate.await_count, 4)
            self.message.from_user.id = 44
            for _ in range(11):
                await cmd_draw(self.message, service)
                await service._task
            self.assertEqual(provider.generate.await_count, 14)
            self.assertIn("10 из 10", self.message.reply.await_args.args[0])
            for user_id in (45, 46, 47, 48):
                self.message.from_user.id = user_id
                for _ in range(10 if user_id != 48 else 7):
                    await cmd_draw(self.message, service)
                    await service._task
            self.assertEqual(provider.generate.await_count, 50)
            self.assertIn("Общий дневной лимит", self.message.reply.await_args.args[0])

    async def test_flux_does_not_follow_redirect_or_retry(self):
        respond = Mock(return_value=httpx.Response(302, headers={"Location": "https://example.com/"}))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with self.assertRaises(ImageProviderError) as error:
                await FluxClient(client, "0" * 32, "dummy").generate("tram")
        self.assertEqual(error.exception.status, 302)
        self.assertEqual(respond.call_count, 1)
