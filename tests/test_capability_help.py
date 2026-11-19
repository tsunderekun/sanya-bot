import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.repository import Repository
from sanya.services.capability_help import generate_help, help_kind
from sanya.services.chat import process_chat_message


class CapabilityHelpTests(unittest.IsolatedAsyncioTestCase):
    def test_recognizes_overviews_without_matching_action_requests(self):
        for text in (
            "Что ты умеешь?",
            "Спермер, ЧЁ  ТЫ ВООБЩЕ УМЕЕШЬ?!",
            "А чем можешь помочь?",
            "Какие у тебя есть фичи?",
            "Для чего нужен бот?",
        ):
            with self.subTest(text=text):
                self.assertEqual(help_kind(text), "overview")
        for text in (
            "/help",
            "/r34 cat",
            "можешь найти арт?",
            "что ты можешь сделать с файлом?",
            "почему мем смешной?",
            "как зайти в /home",
        ):
            with self.subTest(text=text):
                self.assertEqual(help_kind(text), "")
        self.assertFalse(help_kind("А как?"))
        self.assertEqual(help_kind("А как?", follow_up=True), "feature")
        self.assertEqual(help_kind("Как пользоваться /r34?"), "feature")

    async def test_uses_model_and_keeps_help_in_the_dialogue_branch(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "help.db") as db:
                bot_user = NS(id=9, username="sanya_bot")
                bot = NS(get_me=AsyncMock(return_value=bot_user))
                llm = NS(
                    generate=AsyncMock(
                        side_effect=[
                            "Могу общаться и искать арт. Полный список в /help.",
                            "В этой группе сейф отключает поиск картинок.",
                        ]
                    )
                )
                msg = NS(
                    text="Что ты умеешь?",
                    chat=NS(id=-100, type="supergroup"),
                    from_user=NS(id=42, username="tester"),
                    photo=None,
                    sticker=None,
                    caption=None,
                    reply_to_message=None,
                    message_id=10,
                )
                await db.run(Repository.set_chat_reply_mode, -100, "safe")
                await db.run(
                    Repository.save_message,
                    -100,
                    8,
                    None,
                    42,
                    "tester",
                    "user",
                    "Посторонняя реплика",
                )
                with (
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(side_effect=[[11], [13], [15]]),
                    ) as send,
                    patch(
                        "sanya.services.chat.ask_booru_confirmation", AsyncMock()
                    ) as confirm,
                    patch(
                        "sanya.services.chat.search_and_download_booru_image",
                        AsyncMock(),
                    ) as booru,
                    patch("sanya.services.chat.lookup_snippets", AsyncMock()) as web,
                ):
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    llm.generate.assert_not_awaited()  # No reply to an unaddressed group question.
                    msg.text = "@sanya_bot, Что ты умеешь?"
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    self.assertIn(
                        "своими словами",
                        llm.generate.await_args.kwargs["system_prompt"],
                    )
                    self.assertIn("Полный список в /help", send.await_args.args[3])

                    msg.message_id = 12
                    msg.text = "Как пользоваться /r34?"
                    msg.reply_to_message = NS(
                        from_user=bot_user,
                        message_id=11,
                        photo=None,
                        sticker=None,
                        text="Могу искать арт",
                        caption=None,
                    )
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    llm.generate.assert_awaited_once()
                    self.assertIn(
                        "включая /r34 и Booru, отключены", send.await_args.args[3]
                    )

                    msg.message_id = 14
                    msg.text = "А как это работает?"
                    msg.reply_to_message.message_id = 13
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    turns = llm.generate.await_args.args[0]
                    self.assertIn(
                        "Текущий чат в режиме «сейф»",
                        llm.generate.await_args.kwargs["system_prompt"],
                    )
                    self.assertIn(
                        {"role": "user", "content": "Как пользоваться /r34?"}, turns
                    )
                    self.assertNotIn("Посторонняя реплика", str(turns))
                    confirm.assert_not_awaited()
                    booru.assert_not_awaited()
                    web.assert_not_awaited()

    async def test_model_failure_points_to_static_help(self):
        llm = NS(generate=AsyncMock(return_value=""))
        answer = await generate_help(llm, "Что ты умеешь?", "overview", [], safe=False)
        self.assertIn("/help", answer)
