import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.dialogue_notes import _collect
from sanya.services.meme_lore import meme_lore_reply


class MemeLoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_chat_and_person_lore_is_relevant_and_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "lore.db") as db:
                await db.run(
                    Repository.add_chat_lore_entry,
                    -100,
                    "Тест",
                    "Пельменный турнир был мемом.",
                    42,
                )
                await db.run(
                    Repository.add_chat_lore_entry,
                    -200,
                    "Другая группа",
                    "Чужая традиция.",
                    42,
                )
                await db.run(
                    Repository.add_kent_fact_entry,
                    43,
                    "Любит шахматы.",
                    user_name="sample",
                )
                await db.run(
                    Repository.save_message,
                    -100,
                    1,
                    None,
                    43,
                    "sample",
                    "user",
                    "Привет",
                )
                for text in (
                    "Спермер, расскажи про конфу",
                    "Расскажи о пельменном турнире",
                    "А про турнир?",
                ):
                    notes, grounded = await db.run(_collect, 42, -100, text, True)
                    self.assertTrue(grounded)
                    self.assertIn("Пельменный турнир", " ".join(notes))
                    self.assertNotIn("Чужая традиция", " ".join(notes))
                for chat, expected in ((-100, True), (-200, False), (42, False)):
                    notes, grounded = await db.run(
                        _collect, 42, chat, "Кто такой @sample?", True
                    )
                    self.assertTrue(grounded)
                    self.assertEqual("шахматы" in " ".join(notes), expected)

    async def test_rofl_uses_saved_lore_instead_of_improvisation(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "lore.db") as db:
                await db.run(Repository.set_chat_reply_mode, -100, "rofl")
                await db.run(
                    Repository.add_chat_lore_entry,
                    -100,
                    "Тест",
                    "Турнир по шашкам был мемом.",
                    42,
                )
                message = SimpleNamespace(
                    text="Спермер, расскажи про конфу",
                    message_id=10,
                    chat=SimpleNamespace(id=-100, type="supergroup"),
                    from_user=SimpleNamespace(id=42, username="reader"),
                    reply_to_message=None,
                    photo=None,
                    sticker=None,
                    caption=None,
                )
                bot = SimpleNamespace(
                    get_me=AsyncMock(
                        return_value=SimpleNamespace(id=9, username="ponyeb228_bot")
                    )
                )
                llm = SimpleNamespace(
                    generate=AsyncMock(return_value="Турнир по шашкам был нашим мемом.")
                )
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[11]),
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.lookup_snippets", AsyncMock()) as search,
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                search.assert_not_awaited()
                args = llm.generate.await_args
                self.assertIn("Турнир по шашкам", args.args[0][-1]["content"])
                self.assertIn("по справочным заметкам", args.kwargs["system_prompt"])
                self.assertNotIn("РОФЛ:", args.kwargs["system_prompt"])
                saved = await db.run(Repository.get_message, -100, 11)
                self.assertEqual(saved["content"], send.await_args.args[3])

    def test_meme_questions_and_unrelated_mentions(self):
        for question in (
            "Расскажи про матрас",
            "Спермер, а про матрас?",
            "Спермер, матрас?",
            "В чем прикол с матрасом?",
        ):
            with self.subTest(question=question):
                self.assertIn("до самых пружин", meme_lore_reply(question))
        self.assertIn("4PDA", meme_lore_reply("Что значит слойпер?"))
        self.assertIn("Дискорд", meme_lore_reply("Объясни, что такое кезднуть"))
        self.assertEqual(meme_lore_reply("Надо купить матрас"), "")
        self.assertEqual(meme_lore_reply("Какой матрас купить?"), "")

    async def test_rofl_mode_delivers_lore_without_inventing_it(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "lore.db") as db:
                await db.run(Repository.set_chat_reply_mode, -100, "rofl")
                message = SimpleNamespace(
                    text="Спермер, расскажи про матрас",
                    message_id=10,
                    chat=SimpleNamespace(id=-100, type="supergroup"),
                    from_user=SimpleNamespace(id=42, username="reader"),
                    reply_to_message=None,
                    photo=None,
                    sticker=None,
                    caption=None,
                )
                bot = SimpleNamespace(
                    get_me=AsyncMock(
                        return_value=SimpleNamespace(id=9, username="ponyeb228_bot")
                    )
                )
                llm = SimpleNamespace(generate=AsyncMock())
                with patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[11]),
                ) as send:
                    await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertIn("до самых пружин", send.await_args.args[3])
                saved = await db.run(Repository.get_message, -100, 11)
                self.assertEqual(saved["content"], send.await_args.args[3])
