import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest

from sanya.database import Database
from sanya.handlers.basic import cmd_voice
from sanya.intent import Mode
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.voice import PiperVoiceReplies, detail_voice_text, voice_text


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        folder = self.enterContext(tempfile.TemporaryDirectory())
        self.db = await self.enterAsyncContext(Database(Path(folder) / "test.db"))
        self.message = NS(
            chat=NS(id=42, type="private"), message_id=1,
            from_user=NS(id=42, username="kent"), text="Саня, как катка?",
            caption=None, photo=None, sticker=None, reply_to_message=None,
            reply=AsyncMock(),
        )

    async def test_setting_defaults_off_and_is_scoped_to_chat(self):
        self.assertEqual((await self.db.run(Repository.get_chat_reply_settings, 42)).voice_chance, 0)
        with patch("sanya.handlers.basic.PIPER_MODEL_PATH", "denis.onnx"):
            self.message.text = "/голосовые 25%"
            await cmd_voice(self.message, self.db)
            self.assertEqual((await self.db.run(Repository.get_chat_reply_settings, 42)).voice_chance, 25)
            self.assertEqual((await self.db.run(Repository.get_chat_reply_settings, -100)).voice_chance, 0)
            self.message.text = "/голосовые выкл"
            await cmd_voice(self.message, self.db)
        self.assertEqual((await self.db.run(Repository.get_chat_reply_settings, 42)).voice_chance, 0)

    async def test_group_requires_admin(self):
        self.message.chat = NS(id=-100, type="supergroup")
        self.message.text = "/voice 100"
        with patch("sanya.handlers.basic.check_is_admin", AsyncMock(return_value=False)):
            await cmd_voice(self.message, self.db)
        self.assertIn("только админ", self.message.reply.await_args.args[0])
        self.assertEqual((await self.db.run(Repository.get_chat_reply_settings, -100)).voice_chance, 0)

    async def test_voice_delivery_and_text_fallbacks(self):
        result = "Катка бодрая, но рано расслабились"
        llm = NS(generate=AsyncMock(return_value=result))
        for chance, mode, audio, rejected, expect_render, expect_voice in (
            (0, "normal", b"audio", False, False, False),
            (100, "safe", b"audio", False, False, False),
            (100, "normal", None, False, True, False),
            (100, "normal", b"audio", True, True, False),
            (100, "normal", b"audio", False, True, True),
        ):
            with self.subTest(chance=chance, mode=mode, audio=audio, rejected=rejected):
                await self.db.run(Repository.set_chat_voice_chance, 42, chance)
                await self.db.run(Repository.set_chat_reply_mode, 42, mode)
                voice = NS(render=AsyncMock(return_value=audio))
                bot = NS(
                    get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")),
                    send_voice=AsyncMock(return_value=NS(message_id=2)),
                )
                if rejected:
                    bot.send_voice.side_effect = TelegramBadRequest(method=None, message="test")
                with (
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[])) as send,
                    patch("sanya.services.chat.automatic_sticker", return_value=False),
                ):
                    await process_chat_message(self.message, bot, db=self.db, llm=llm, voice=voice)
                self.assertEqual(voice.render.await_count, int(expect_render))
                self.assertEqual(bot.send_voice.await_count, int(expect_voice or rejected))
                self.assertEqual(send.await_count, int(not expect_voice))
                if expect_voice:
                    params = bot.send_voice.await_args.kwargs["reply_parameters"]
                    self.assertEqual(params.message_id, self.message.message_id)
                    rows = await self.db.run(Repository.get_dialogue_rows, 42, 42, 3, reply_to_id=2)
                    self.assertEqual(rows[-1]["content"], result)
                self.message.message_id += 2

    async def test_cancelled_request_does_not_start_second_synthesis(self):
        renderer = PiperVoiceReplies(Path("denis.onnx"))
        started, finish = asyncio.Event(), asyncio.Event()

        async def job(*args):
            started.set()
            await finish.wait()
            return b"audio"

        with patch("sanya.services.voice.asyncio.to_thread", side_effect=job) as launch:
            pending = asyncio.create_task(renderer.render("Проверка"))
            await started.wait()
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertIsNone(await renderer.render("Ещё проверка"))
            launch.assert_awaited_once()
            finish.set()
            await renderer._job

    async def test_detail_voice_supplements_text_and_preserves_reply_context(self):
        self.message.text = "Подробно объясни, стоит ли менять видеокарту?"
        conclusion = "Менять стоит, только если не хватает производительности в играх"
        result = "Сначала измерь FPS в нужных играх\n\nИтог: " + conclusion
        for test, chance, audio, gpu in (
            ("off", 0, b"audio", False),
            ("synthesis failed", 100, None, False),
            ("ordinary detail", 100, b"audio", False),
            ("gpu command", 100, b"audio", True),
        ):
            with self.subTest(test=test):
                await self.db.run(Repository.set_chat_voice_chance, 42, chance)
                events = []
                question_id = self.message.message_id

                async def send_text(*args, **kwargs):
                    events.append("text")
                    self.assertEqual(args[3], result)
                    self.assertTrue(kwargs["is_detailed"])
                    return [question_id + 1]

                async def render(text):
                    events.append("render")
                    self.assertEqual(text, conclusion)
                    return audio

                async def send_voice(*args, **kwargs):
                    events.append("voice")
                    self.assertEqual(kwargs["reply_parameters"].message_id, question_id + 1)
                    return NS(message_id=question_id + 2)

                bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")),
                         send_voice=send_voice)
                llm = NS(generate=AsyncMock(return_value=result))
                with (
                    patch("sanya.services.chat.classify_intent", return_value=Mode.DETAIL),
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.send_burst_messages", side_effect=send_text),
                ):
                    await process_chat_message(
                        self.message, bot, db=self.db, llm=llm, voice=NS(render=render),
                        force_gpu_query="RTX 3070" if gpu else None,
                    )
                llm.generate.assert_awaited_once()
                expected = ["text"] + (["render"] if chance else [])
                if chance and audio:
                    expected.append("voice")
                    rows = await self.db.run(
                        Repository.get_dialogue_rows, 42, 42, question_id + 3,
                        reply_to_id=question_id + 2,
                    )
                    self.assertEqual(rows[-1]["content"], result)
                self.assertEqual(events, expected)
                self.message.message_id += 3

    def test_detail_voice_extracts_conclusion_without_adding_or_truncating(self):
        conclusion = "Бери, только если цена устраивает"
        self.assertEqual(detail_voice_text(f"| Модель | Цена |\n\n**Итог:** {conclusion}"), conclusion)
        self.assertEqual(detail_voice_text(f"Вывод:\n{conclusion}"), conclusion)
        self.assertEqual(detail_voice_text(conclusion), conclusion)
        self.assertEqual(detail_voice_text(f"Итог: {conclusion}\nОговорка: не для 4K"),
                         f"{conclusion} Оговорка: не для 4K")
        self.assertEqual(detail_voice_text("Слишком длинный разбор. " * 20), "")

    def test_keeps_formatted_or_long_text_unspoken(self):
        self.assertEqual(voice_text("Ну да\n \nПоехали"), "Ну да Поехали")
        for text in ("Смотри example.org", "Вот `код`", "[ссылка]", "English only", "я" * 241):
            self.assertEqual(voice_text(text), "")
