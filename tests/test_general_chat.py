import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.intent import Mode, classify_intent, is_targeted_insult
from sanya.prompts import build_system_prompt
from sanya.repository import Repository
from sanya.schema import initialize
from sanya.services.chat import process_chat_message
from sanya.services.dialogue_notes import _collect
from sanya.services.sale_reply import grounded_sale_reply


class GeneralChatTests(unittest.TestCase):
    def test_games_and_banter_do_not_turn_into_hostility(self):
        for text in (
            "Спермер, как катка?",
            "Ты сам вчера слился, лошара",
            "Витя сказал «иди нахуй», и мы ржали",
            "Что ты думаешь про тачки?",
        ):
            with self.subTest(text=text):
                self.assertIs(classify_intent(text), Mode.CHAT)
                self.assertFalse(is_targeted_insult(text, addressed=True))
        self.assertFalse(is_targeted_insult("иди нахуй"))
        self.assertTrue(is_targeted_insult("Спермер, завали рот!", addressed=True))

    def test_general_chat_prompt_encourages_on_topic_banter(self):
        prompt = build_system_prompt(Mode.CHAT, "Спермер, как катка?")
        self.assertIn("не своди разговор к железу", prompt)
        self.assertIn("Дружеский стёб подхвати", prompt)
        self.assertIn("не склеивай мат и мемы", prompt)

    def test_dossier_count_excludes_other_chats_users_and_bot_replies(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            for msg, chat, user, role in (
                (1, -100, 42, "user"),
                (2, -101, 42, "user"),
                (3, -100, 43, "user"),
                (4, -100, 42, "assistant"),
            ):
                repo.save_message(chat, msg, None, user, "test", role, "test")
            self.assertEqual(repo.count_user_chat_messages(-100, 42), 1)

    def test_avito_alone_does_not_pull_unrelated_hardware_lore(self):
        repo = SimpleNamespace(
            get_chat_profile=lambda _: {
                "chat_lore": "На Авито восстановили RX 570. Seagate этой серии шумит.",
            },
            get_self_facts=lambda *_: [{"fact_text": "Видеокарта: RTX 3070"}],
            get_auto_facts=lambda *_: [{"fact_text": "Авито и прошивки"}],
        )
        notes, kent_question = _collect(
            repo, 42, -100, "Когда дохлые Seagate на Авито выставлять?", True
        )
        self.assertEqual(notes, ["Лор конфы: Seagate этой серии шумит."])
        self.assertFalse(kent_question)


class ChatContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_to_later_burst_line_gets_original_question_not_group_noise(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "context.db") as db:
                await db.run(
                    Repository.save_message,
                    -100,
                    1,
                    None,
                    42,
                    "kent",
                    "user",
                    "Когда Seagate на Авито выставлять?",
                )
                await db.run(
                    Repository.save_chat_answer,
                    -100,
                    [2, 3],
                    1,
                    9,
                    "Не понял вопрос\nПошли про тачки",
                )
                await db.run(
                    Repository.save_message,
                    -100,
                    4,
                    None,
                    43,
                    "someone",
                    "user",
                    "Посторонний разговор про RX 570",
                )
                await db.run(
                    Repository.save_message,
                    -101,
                    1,
                    None,
                    42,
                    "kent",
                    "user",
                    "Текст из другого чата",
                )
                self.assertIsNone(await db.run(Repository.get_answer_question, -101, 3))
                prior, _ = await db.run(
                    Repository.get_recent_messages, -100, before_message_id=4
                )
                self.assertEqual(sum(turn["role"] == "assistant" for turn in prior), 1)
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=-100, type="supergroup"),
                    message_id=5,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Ты не попал в тему",
                    caption=None,
                    photo=None,
                    sticker=None,
                    reply_to_message=SimpleNamespace(
                        from_user=SimpleNamespace(id=9, username="ponyeb228_bot"),
                        message_id=3,
                        text="Пошли про тачки",
                        photo=None,
                        sticker=None,
                    ),
                )
                bot = SimpleNamespace(
                    get_me=AsyncMock(return_value=message.reply_to_message.from_user)
                )
                llm = SimpleNamespace(
                    generate=AsyncMock(
                        return_value="Диски выставляй только на запчасти\nДефект укажи сразу в описании"
                    )
                )
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[6]),
                    ),
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                turns = llm.generate.await_args.args[0]
                self.assertEqual(len(turns), 1)
                self.assertIn("Исходный вопрос: Когда Seagate", turns[0]["content"])
                self.assertTrue(
                    turns[0]["content"].endswith("[kent]: Ты не попал в тему")
                )
                self.assertNotIn("RX 570", turns[0]["content"])
                self.assertNotIn("Текст из другого чата", turns[0]["content"])
                self.assertEqual(
                    (await db.run(Repository.get_answer_question, -100, 6))["content"],
                    message.text,
                )

                message.message_id = 7
                message.reply_to_message = None
                message.text = "Спермер, когда это за 4к на Авито выставлять?"
                llm.generate.reset_mock()
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[8]),
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertEqual(send.await_args.args[3], "А что именно продаёшь?")
                self.assertEqual(
                    (await db.run(Repository.get_answer_question, -100, 8))["content"],
                    message.text,
                )

                message.message_id = 9
                message.text = "Спермер, когда сигейты дохлые на Авито сольешь?"
                with patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[10, 11, 12]),
                ) as send:
                    await process_chat_message(message, bot, db=db, llm=llm)
                disk_answer = send.await_args.args[3]
                self.assertIn("Причину не знаешь", disk_answer)
                bot_user = await bot.get_me()
                message.message_id = 13
                message.text = "Ты не попал в тему"
                message.reply_to_message = SimpleNamespace(
                    from_user=bot_user,
                    message_id=12,
                    text="Старая реплика не по теме",
                    photo=None,
                    sticker=None,
                )
                with patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[14]),
                ) as send:
                    await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertEqual(send.await_args.args[3], disk_answer)

    def test_sale_replies_match_only_supported_questions(self):
        unknown = "спермер когда хуйню за 4к кебекам сливать на авито?"
        disk = "когда сигейты дохлые на авито сольешь"
        self.assertEqual(grounded_sale_reply(unknown), "А что именно продаёшь?")
        self.assertEqual(grounded_sale_reply(unknown, has_context=True), "")
        self.assertIn("как нерабочие", grounded_sale_reply(disk))
        self.assertIn("1 ТБ тут просто пример", grounded_sale_reply(disk))
        known = grounded_sale_reply("Когда дохлый Seagate на 4 ТБ выставишь на Авито?")
        self.assertIn("как нерабочие", known)
        self.assertNotIn("1 ТБ", known)
        self.assertEqual(
            grounded_sale_reply("ты не попал в тему", original=disk, has_context=True),
            grounded_sale_reply(disk),
        )
        for other in (
            "Когда рабочий Seagate на Авито выставлять?",
            "Как проверить дохлый Seagate перед продажей на Авито?",
            "Когда на Авито продашь рабочую RX 570, а дохлый HDD оставишь?",
            "Ты не попал в тему",
        ):
            with self.subTest(other=other):
                self.assertEqual(grounded_sale_reply(other), "")
