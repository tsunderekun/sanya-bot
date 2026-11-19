import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.meme_lore import meme_lore_reply


class MemeLoreTests(unittest.IsolatedAsyncioTestCase):
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
