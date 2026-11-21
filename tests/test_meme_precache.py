import asyncio
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import Chat, Message, Update, User

from sanya.handlers.memes import MemePrecache
from sanya.main import build_dispatcher


class PreviewDeduplicationTests(unittest.TestCase):
    def test_import_deduplicates_across_processes_and_channel_name_case(self):
        script = textwrap.dedent('''
            import asyncio
            import json
            import sys
            from types import SimpleNamespace
            from unittest.mock import AsyncMock, patch

            from sanya.database import Database
            from sanya.handlers.memes import precache_channel_memes

            async def run():
                bot = SimpleNamespace(get_chat=AsyncMock(side_effect=OSError))
                preview = (
                    '<div class="tgme_widget_message_wrap" data-post="sample/123">'
                    '<a class="tgme_widget_message_photo_wrap" '
                    'style="background-image:url(https://cdn.telegram-cdn.org/file/test.jpg)">'
                )
                client = AsyncMock()
                client.__aenter__.return_value.get.return_value = SimpleNamespace(
                    status_code=200, text=preview
                )
                with patch("sanya.handlers.memes.httpx.AsyncClient", return_value=client):
                    async with Database(sys.argv[1]) as db:
                        print(json.dumps(await precache_channel_memes(
                            bot, db, channel=sys.argv[2], max_pages=1
                        )))

            asyncio.run(run())
        ''')
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "memes.db"

            def run_import(seed, channel):
                output = subprocess.check_output(
                    [sys.executable, "-c", script, str(db_path), channel],
                    cwd=Path(__file__).resolve().parents[1],
                    env={**os.environ, "PYTHONHASHSEED": seed},
                    text=True,
                    timeout=20,
                )
                return json.loads(output)

            self.assertEqual(
                run_import("1", "@Sample"), {"found": 1, "new": 1, "total": 1}
            )
            self.assertEqual(
                run_import("2", "https://t.me/sample"),
                {"found": 1, "new": 0, "total": 1},
            )
            self.assertEqual(
                run_import("2", "@another_channel"),
                {"found": 1, "new": 1, "total": 2},
            )


class MemePrecacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.bot = Bot("123:TEST")
        self.addAsyncCleanup(self.bot.session.close)
        self.precache = MemePrecache()
        self.addAsyncCleanup(self.precache.close)
        self.dispatcher = build_dispatcher()
        self.db = SimpleNamespace(run=AsyncMock(return_value=False))

    async def send(self, text, *, user_id=42, chat_id=-10001):
        message = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="supergroup"),
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            text=text,
        )
        await asyncio.wait_for(
            self.dispatcher.feed_update(
                self.bot,
                Update(update_id=1, message=message),
                db=self.db,
                meme_precache=self.precache,
            ),
            timeout=2,
        )

    async def test_help_and_duplicate_request_do_not_wait_for_import(self):
        started = asyncio.Event()
        release = asyncio.Event()
        finished = asyncio.Event()

        async def slow_import(*args, **kwargs):
            started.set()
            await release.wait()
            return {"found": 12, "new": 0, "total": 92}

        async def mark_finished(text):
            finished.set()

        status = SimpleNamespace(edit_text=AsyncMock(side_effect=mark_finished))
        with (
            patch("sanya.handlers.access.ADMIN_IDS", {42}),
            patch(
                "sanya.handlers.memes.precache_channel_memes", side_effect=slow_import
            ) as load,
            patch.object(Message, "reply", AsyncMock(return_value=status)) as reply,
        ):
            try:
                await self.send("/прекэш @mestodm 99")
                await asyncio.wait_for(started.wait(), timeout=2)
                load.assert_awaited_once_with(
                    self.bot, self.db, channel="mestodm", max_pages=30
                )
                reply.reset_mock()
                await self.send("/help", user_id=43, chat_id=-10002)
                reply.assert_awaited_once()
                self.assertIsNotNone(reply.await_args.kwargs.get("reply_markup"))
                status.edit_text.assert_not_awaited()

                await self.send("/precache @other_channel", chat_id=-10002)
                self.assertIn("Прекэш уже идёт", reply.await_args.args[0])
                load.assert_awaited_once()

                release.set()
                await asyncio.wait_for(finished.wait(), timeout=2)
                text = status.edit_text.await_args.args[0]
                self.assertIn("Просмотрел 12 постов", text)
                self.assertIn("новых добавил: 0", text)
                self.assertIn("Всего в пуле: 92", text)
            finally:
                await self.precache.close()

    async def test_only_bot_admins_can_start_import(self):
        with (
            patch("sanya.handlers.access.ADMIN_IDS", {42}),
            patch.object(self.precache, "start") as start,
            patch.object(Message, "reply", AsyncMock()) as reply,
        ):
            await self.send("/precache", user_id=43)
            start.assert_not_called()
            self.assertIn("администраторы бота", reply.await_args.args[0])

    async def test_shutdown_cancels_active_import(self):
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def slow_import(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        status = SimpleNamespace(edit_text=AsyncMock())
        with (
            patch("sanya.handlers.access.ADMIN_IDS", {42}),
            patch(
                "sanya.handlers.memes.precache_channel_memes", side_effect=slow_import
            ),
            patch.object(Message, "reply", AsyncMock(return_value=status)),
        ):
            await self.send("/precache")
            await asyncio.wait_for(started.wait(), timeout=2)
            await asyncio.wait_for(self.precache.close(), timeout=2)
            self.assertTrue(stopped.is_set())
            status.edit_text.assert_not_awaited()
