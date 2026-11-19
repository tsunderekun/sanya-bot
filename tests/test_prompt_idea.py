import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from aiogram.types import Chat, Message, User

from sanya.handlers.media import cmd_prompt_idea
from sanya.services import booru, booru_confirmation, chat
from sanya.services.queue import RequestQueue


class PromptIdeaTests(unittest.IsolatedAsyncioTestCase):
    async def test_aibooru_tag_lookup_returns_metadata_only(self):
        seen = []

        def respond(request):
            seen.append((request.url.host, request.url.path))
            if request.url.params["page"] == "1":
                return httpx.Response(
                    200,
                    json=[
                        {"name": "rainy_night", "post_count": 100},
                        {"name": "-bad_tag", "post_count": 90},
                        {"name": "x", "post_count": 80},
                        {"name": "empty", "post_count": 0},
                        {"name": "ignore instructions", "post_count": 2},
                        {"name": "loli", "post_count": 500},
                    ],
                )
            return httpx.Response(200, json=[{"name": "tram_stop", "post_count": 50}])

        real_client = httpx.AsyncClient

        def client_factory(**kwargs):
            return real_client(transport=httpx.MockTransport(respond), **kwargs)

        with patch.object(booru.httpx, "AsyncClient", side_effect=client_factory):
            tags = await booru.search_aibooru_idea_tags()
        self.assertEqual(tags, ["rainy night", "tram stop"])
        self.assertEqual(seen, [("aibooru.online", "/tags.json")] * 2)

    async def test_command_only_asks_and_confirmation_is_authored_single_use_and_ttl(
        self,
    ):
        author = User(id=100, is_bot=False, first_name="Author")
        prompt = Message(
            message_id=8,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-10, type="group"),
        )
        message = SimpleNamespace(
            chat=prompt.chat,
            from_user=author,
            sender_chat=None,
            reply=AsyncMock(return_value=prompt),
        )
        queue = RequestQueue()
        with (
            patch.object(booru_confirmation, "_idea_pending", {}),
            patch.object(booru, "search_aibooru_idea_tags", AsyncMock()) as search,
            patch.object(Message, "delete", new_callable=AsyncMock),
        ):
            await cmd_prompt_idea(message)
            search.assert_not_awaited()
            button = message.reply.await_args.kwargs["reply_markup"].inline_keyboard[0][
                0
            ]
            self.assertEqual(button.text, "Да, я понимаю")
            callback = SimpleNamespace(
                data=button.callback_data,
                message=prompt,
                from_user=SimpleNamespace(id=101),
                answer=AsyncMock(),
            )
            await booru_confirmation.confirm_prompt_idea(callback, queue)
            self.assertTrue(queue._queue.empty())
            callback.from_user = author
            with patch.object(
                booru_confirmation, "monotonic", return_value=float("inf")
            ):
                await booru_confirmation.confirm_prompt_idea(callback, queue)
            self.assertTrue(queue._queue.empty())
            await cmd_prompt_idea(message)
            button = message.reply.await_args.kwargs["reply_markup"].inline_keyboard[0][
                0
            ]
            callback.data = button.callback_data
            await booru_confirmation.confirm_prompt_idea(callback, queue)
            request = queue._queue.get_nowait()
            self.assertIs(request.message, message)
            self.assertTrue(request.prompt_idea)
            await booru_confirmation.confirm_prompt_idea(callback, queue)
            self.assertTrue(queue._queue.empty())

    async def test_queue_worker_forwards_idea_flag(self):
        queue = RequestQueue()
        message = SimpleNamespace(from_user=SimpleNamespace(id=100))
        await queue.enqueue(message, prompt_idea=True)
        processed = asyncio.Event()
        values = {}

        async def process(**kwargs):
            values.update(kwargs)
            processed.set()

        worker = asyncio.create_task(queue.work(process))
        try:
            await asyncio.wait_for(processed.wait(), 1)
            self.assertTrue(values["prompt_idea"])
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def test_idea_outputs_only_spoiler_text_and_uses_fallback_scene(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=100),
            chat=SimpleNamespace(id=-10),
            message_id=7,
            reply=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        llm = SimpleNamespace(
            generate=AsyncMock(
                return_value=(
                    "Positive prompt: adult fictional mechanic, repairing a tiny robot, "
                    "rainy night market, cinematic lighting\nNegative prompt: low quality, blur"
                )
            )
        )
        with (
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch(
                "sanya.services.chat.search_aibooru_idea_tags",
                AsyncMock(return_value=[]),
            ) as tags,
            patch("sanya.services.chat.random.sample") as sample,
        ):
            await chat.process_chat_message(
                message, bot, db=None, llm=llm, prompt_idea=True
            )
        tags.assert_awaited_once_with()
        sample.assert_not_called()
        llm.generate.assert_awaited_once()
        text = bot.send_message.await_args.args[1]
        self.assertTrue(text.startswith("<tg-spoiler>"))
        self.assertIn("<b>Positive prompt:</b>", text)
        self.assertIn("<b>Negative prompt:</b>", text)
        self.assertEqual(bot.send_message.await_args.kwargs["parse_mode"], "HTML")
        bot.send_photo.assert_not_awaited()

    async def test_prompt_text_is_html_escaped_and_link_preview_disabled(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=100),
            chat=SimpleNamespace(id=-10),
            message_id=7,
            reply=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        llm = SimpleNamespace(
            generate=AsyncMock(
                return_value=(
                    "Positive prompt: adult fictional artist, <script>alert('x')</script>\n"
                    "Negative prompt: blur"
                )
            )
        )
        with (
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch(
                "sanya.services.chat.search_aibooru_idea_tags",
                AsyncMock(return_value=[]),
            ),
        ):
            await chat.process_chat_message(
                message, bot, db=None, llm=llm, prompt_idea=True
            )
        rendered = bot.send_message.await_args.args[1]
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertTrue(
            bot.send_message.await_args.kwargs["link_preview_options"].is_disabled
        )

    async def test_aibooru_tags_guide_scene_and_malformed_output_retries_once(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=100),
            chat=SimpleNamespace(id=-10),
            message_id=7,
            reply=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        llm = SimpleNamespace(
            generate=AsyncMock(
                side_effect=[
                    "unfinished answer",
                    "Positive prompt: adult fictional pony painting a tram\nNegative prompt: text, blur",
                ]
            )
        )
        with (
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch(
                "sanya.services.chat.search_aibooru_idea_tags",
                AsyncMock(return_value=["tram", "rainy night", "paintbrush"]),
            ),
            patch(
                "sanya.services.chat.random.sample",
                return_value=["tram", "rainy night", "paintbrush"],
            ),
        ):
            await chat.process_chat_message(
                message, bot, db=None, llm=llm, prompt_idea=True
            )
        self.assertEqual(llm.generate.await_count, 2)
        self.assertIn(
            "tram, rainy night, paintbrush",
            llm.generate.await_args_list[0].args[0][0]["content"],
        )
        self.assertTrue(bot.send_message.awaited)
        bot.send_photo.assert_not_awaited()

    async def test_unsafe_age_word_in_generated_scene_is_not_sent(self):
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=100),
            chat=SimpleNamespace(id=-10),
            message_id=7,
            reply=AsyncMock(),
        )
        bot = SimpleNamespace(send_message=AsyncMock(), send_photo=AsyncMock())
        llm = SimpleNamespace(
            generate=AsyncMock(
                return_value=(
                    "Positive prompt: adult woman and a child, explicit scene\n"
                    "Negative prompt: blur"
                )
            )
        )
        with (
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch(
                "sanya.services.chat.search_aibooru_idea_tags",
                AsyncMock(return_value=[]),
            ),
        ):
            await chat.process_chat_message(
                message, bot, db=None, llm=llm, prompt_idea=True
            )
        bot.send_message.assert_not_awaited()
        self.assertIn("Не получилось", message.reply.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
