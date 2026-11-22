import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.handlers.vocabulary import cmd_vocabulary
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class VocabularyTests(unittest.IsolatedAsyncioTestCase):
    async def test_permissions_protected_words_and_persistence(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "words.db"
        message = NS(
            chat=NS(id=-100, type="supergroup"), from_user=NS(id=42), reply=AsyncMock()
        )
        async with Database(path) as db:
            message.text = "/словарь добавить пылеплюй = шумный вентилятор"
            with patch(
                "sanya.handlers.vocabulary.check_is_admin",
                AsyncMock(return_value=False),
            ):
                await cmd_vocabulary(message, db)
            self.assertEqual(await db.run(Repository.list_chat_words, -100), [])
            with patch(
                "sanya.handlers.vocabulary.check_is_admin", AsyncMock(return_value=True)
            ):
                await cmd_vocabulary(message, db)
            self.assertIn("Добавил", message.reply.await_args.args[0])
            for word in ("  Слойпер ", "ДЛСС", "  Очко  на взлёт "):
                self.assertEqual(
                    await db.run(Repository.add_chat_word, -100, word, "другое"),
                    "builtin",
                )
                self.assertEqual(
                    await db.run(Repository.delete_chat_word, -100, word), "builtin"
                )
            self.assertEqual(
                await db.run(Repository.add_chat_word, -100, "Пылеплюй", "другое"),
                "exists",
            )
        async with Database(path) as db:
            self.assertEqual(await db.run(Repository.list_chat_words, -200), [])
            message.text = "/dict Пылеплюй"
            await cmd_vocabulary(message, db)
            self.assertIn("шумный вентилятор", message.reply.await_args.args[0])
            message.text = "/dict del ПЫЛЕПЛЮЙ"
            with patch(
                "sanya.handlers.vocabulary.check_is_admin", AsyncMock(return_value=True)
            ):
                await cmd_vocabulary(message, db)
            self.assertEqual(await db.run(Repository.list_chat_words, -100), [])

    def test_builtin_definition_matches_explicit_questions_only(self):
        from sanya.vocabulary import builtin_definition

        self.assertEqual(builtin_definition("что за жодик?"), ("жодик", "HDD"))
        self.assertEqual(builtin_definition("что значит гальку?")[0], "галька")
        self.assertEqual(builtin_definition("что за антона?")[0], "антон")
        self.assertIsNone(builtin_definition("что за принтер?"))
        self.assertIsNone(builtin_definition("как настроить dns?"))
        self.assertEqual(builtin_definition("что за слойпер?")[0], "слойпер")

    async def test_builtin_definition_is_paraphrased_in_voice(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "words.db"
        async with Database(path) as db:
            message = NS(
                text="Спермер, что за жодик?",
                chat=NS(id=-100, type="supergroup"),
                from_user=NS(id=42, username="reader"),
                message_id=10,
                reply_to_message=None,
                photo=None,
                sticker=None,
                caption=None,
            )
            bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")))
            llm = NS(generate=AsyncMock(return_value="Жодик — это жёсткий диск, куда сваливаешь хлам"))
            with (
                patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[11]),
                ) as send,
                patch("sanya.services.chat.keep_typing", AsyncMock()),
            ):
                await process_chat_message(message, bot, db=db, llm=llm)
        llm.generate.assert_awaited_once()
        self.assertIn("жодик", llm.generate.await_args.args[0][0]["content"])
        self.assertIn("HDD", llm.generate.await_args.args[0][0]["content"])
        self.assertIn("не переспрашивай", llm.generate.await_args.kwargs["system_prompt"])
        self.assertEqual(
            send.await_args.args[3],
            "Жодик — это жёсткий диск, куда сваливаешь хлам",
        )

    async def test_builtin_definition_falls_back_when_model_asks_back(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "words.db"
        async with Database(path) as db:
            message = NS(
                text="что за жодик?",
                chat=NS(id=1, type="private"),
                from_user=NS(id=42, username="reader"),
                message_id=10,
                reply_to_message=None,
                photo=None,
                sticker=None,
                caption=None,
            )
            bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")))
            llm = NS(generate=AsyncMock(return_value="Какой именно жодик ты имеешь в виду?"))
            with (
                patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[11]),
                ) as send,
                patch("sanya.services.chat.keep_typing", AsyncMock()),
            ):
                await process_chat_message(message, bot, db=db, llm=llm)
        self.assertEqual(send.await_args.args[3], "жодик — HDD")

    async def test_custom_definition_and_rofl_context(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "words.db"
        async with Database(path) as db:
            await db.run(
                Repository.add_chat_word, -100, "пылеплюй", "шумный вентилятор"
            )
            message = NS(
                text="Спермер, что значит «пылеплюй»?",
                chat=NS(id=-100, type="supergroup"),
                from_user=NS(id=42, username="reader"),
                message_id=10,
                reply_to_message=None,
                photo=None,
                sticker=None,
                caption=None,
            )
            bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")))
            llm = NS(generate=AsyncMock(return_value="Пылеплюй взлетел"))
            with (
                patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[11]),
                ) as send,
                patch("sanya.services.chat.keep_typing", AsyncMock()),
            ):
                await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertEqual(
                    send.await_args.args[3], "пылеплюй — шумный вентилятор"
                )
                await db.run(Repository.set_chat_reply_mode, -100, "rofl")
                message.text, message.message_id = "Спермер, ну и денёк", 12
                send.return_value = [13]
                await process_chat_message(message, bot, db=db, llm=llm)
                self.assertIn(
                    "пылеплюй — шумный вентилятор",
                    llm.generate.await_args.args[0][-1]["content"],
                )
