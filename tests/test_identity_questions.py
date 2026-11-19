import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.observations import extract_self_facts
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.dialogue_notes import _collect


class IdentityQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_identity_questions_use_the_right_subject_even_in_rofl(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "identity.db"
        async with Database(path) as db:
            await db.run(Repository.set_chat_reply_mode, -100, "rofl")
            await db.run(Repository.set_chat_reply_level, -100, "lore", 0)
            await db.run(Repository.add_kent_fact_entry, 42, "Играет в шашки", "author")
            await db.run(
                Repository.record_self_facts, -100, 42, extract_self_facts("Люблю кофе")
            )
            await db.run(
                Repository.add_kent_fact_entry, 43, "Собирает пазлы", "guest", "Миша"
            )
            await db.run(
                Repository.save_message, -100, 1, None, 43, "guest", "user", "Привет"
            )
            bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")))
            message = NS(
                chat=NS(id=-100, type="supergroup"),
                from_user=NS(id=42, username="author"),
                caption=None,
                photo=None,
                sticker=None,
                reply_to_message=None,
            )
            llm = NS(generate=AsyncMock(return_value="Вот что записано."))
            with (
                patch("sanya.services.chat.keep_typing", AsyncMock()),
                patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[]),
                ) as send,
                patch("sanya.services.chat.lookup_snippets", AsyncMock()) as search,
            ):
                for i, text, expected, excluded in (
                    (2, "спермер кто я", "Играет в шашки", "Собирает пазлы"),
                    (3, "Спермер, кто такой МИША?", "Собирает пазлы", "Играет в шашки"),
                    (4, "Спермер кто такой @guest", "Собирает пазлы", "Любит: кофе"),
                    (
                        5,
                        "Спермер кто такой Колян",
                        "Лор о кенте Nikolay",
                        "Играет в шашки",
                    ),
                    (
                        6,
                        "Спермер кто такой Спермер",
                        "Лор о кенте Спермер",
                        "Играет в шашки",
                    ),
                ):
                    with self.subTest(text=text):
                        message.text, message.message_id = text, i
                        await process_chat_message(message, bot, db=db, llm=llm)
                        call = llm.generate.await_args
                        turns = call.args[0]
                        self.assertEqual(len(turns), 1)
                        self.assertIn(expected, turns[0]["content"])
                        self.assertNotIn(excluded, turns[0]["content"])
                        self.assertNotIn("РОФЛ:", call.kwargs["system_prompt"])
                        if i == 2:
                            self.assertIn("Любит: кофе", turns[0]["content"])

                llm.generate.reset_mock()
                message.text, message.message_id = "Спермер кто такой Незнакомец", 7
                await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                search.assert_not_awaited()
                self.assertIn("ничего не записано", send.await_args.args[3])

    async def test_name_scope_ambiguity_and_self_without_profile(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "identity.db"
        async with Database(path) as db:
            await db.run(
                Repository.record_self_facts, -100, 42, extract_self_facts("Люблю кофе")
            )
            for uid in (43, 44):
                await db.run(
                    Repository.add_kent_fact_entry,
                    uid,
                    "Любит пазлы",
                    first_name="Миша",
                )
                await db.run(
                    Repository.save_message,
                    -100,
                    uid,
                    None,
                    uid,
                    "test",
                    "user",
                    "Привет",
                )
            for text, chat in (
                ("Кто такой Миша", -100),
                ("Кто такой Миша", -200),
                ("Кто я?", -200),
            ):
                self.assertEqual(
                    await db.run(_collect, 42, chat, text, True), ([], True)
                )
            notes, grounded = await db.run(_collect, 42, -100, "Кто я такой?", True)
            self.assertTrue(grounded)
            self.assertIn("Любит: кофе", " ".join(notes))
            self.assertFalse((await db.run(_collect, 42, -100, "Кто ты?", True))[1])
