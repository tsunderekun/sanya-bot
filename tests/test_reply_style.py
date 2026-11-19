import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.handlers.reply_style import cmd_lore_level, cmd_tone
from sanya.intent import Mode
from sanya.prompts import SYSTEM_PROMPT, build_system_prompt
from sanya.reply_style import ChatReplySettings, ReplyStyle
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class ReplyStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_commands_scope_permissions_and_persistence(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "style.db"
        msg = NS(
            chat=NS(id=-100, type="supergroup"), from_user=NS(id=42), reply=AsyncMock()
        )
        async with Database(path) as db:
            await db.run(Repository.set_chat_reply_mode, -100, "rofl")
            msg.text = "/тон 0"
            with patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=False),
            ):
                await cmd_tone(msg, db)
                self.assertIn("только админы", msg.reply.await_args.args[0])
            with patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=True),
            ):
                await cmd_tone(msg, db)
                msg.text = "/lore 3"
                await cmd_lore_level(msg, db)
                msg.text = "/лор 4"
                await cmd_lore_level(msg, db)
                self.assertIn("от 0 до 3", msg.reply.await_args.args[0])
            msg.text = "/лор"
            await cmd_lore_level(msg, db)
            self.assertIn("сейчас 3", msg.reply.await_args.args[0])
        async with Database(path) as db:
            self.assertEqual(
                await db.run(Repository.get_chat_reply_settings, -100),
                ChatReplySettings("rofl", ReplyStyle(0, 3)),
            )
            self.assertEqual(
                await db.run(Repository.get_chat_reply_settings, -101),
                ChatReplySettings(),
            )

    def test_prompt_keeps_identity_and_explicit_lore_independent_of_style(self):
        topic = "Саня, BF3"
        quiet = build_system_prompt(Mode.CHAT, topic, rofl=True, style=ReplyStyle(0, 0))
        loud = build_system_prompt(Mode.CHAT, topic, rofl=True, style=ReplyStyle(3, 3))
        for prompt in (quiet, loud):
            self.assertTrue(prompt.startswith(SYSTEM_PROMPT))
            self.assertIn("РОФЛ:", prompt)
        self.assertNotIn("апельсинку будешь", quiet)
        self.assertNotIn("Словарь для панча", quiet)
        self.assertIn("апельсинку будешь", loud)
        self.assertIn("Без травли", loud)
        explicit = build_system_prompt(
            Mode.CHAT, topic, style=ReplyStyle(0, 0), explicit_lore=True
        )
        self.assertIn("апельсинку будешь", explicit)
        code = build_system_prompt(Mode.CODE, topic, style=ReplyStyle(3, 3))
        self.assertEqual(code, build_system_prompt(Mode.CODE, topic))

    async def test_chat_respects_levels_without_losing_direct_lore(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "style.db"
        async with Database(path) as db:
            await db.run(Repository.set_chat_reply_level, -100, "tone", 0)
            await db.run(Repository.set_chat_reply_level, -100, "lore", 0)
            await db.run(
                Repository.add_chat_word, -100, "пылеплюй", "шумный вентилятор"
            )
            await db.run(
                Repository.add_chat_lore_entry,
                -100,
                "Тест",
                "Шахматы — тема турнира.",
                42,
            )
            msg = NS(
                text="Спермер, шахматы и пылеплюй",
                message_id=10,
                chat=NS(id=-100, type="supergroup"),
                from_user=NS(id=42, username="reader"),
                photo=None,
                caption=None,
                sticker=None,
                reply_to_message=None,
            )
            bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot")))
            llm = NS(generate=AsyncMock(return_value="Знакомая тема"))
            with (
                patch("sanya.services.chat.keep_typing", AsyncMock()),
                patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[]),
                ),
            ):
                await process_chat_message(msg, bot, db=db, llm=llm)
                first = llm.generate.await_args
                self.assertIn("Тон: дружелюбно", first.kwargs["system_prompt"])
                self.assertNotIn("шумный вентилятор", first.args[0][-1]["content"])
                self.assertNotIn("тема турнира", first.args[0][-1]["content"])
                msg.text, msg.message_id = "Спермер, расскажи про конфу", 11
                await process_chat_message(msg, bot, db=db, llm=llm)
                direct = llm.generate.await_args.args[0]
                self.assertEqual(len(direct), 1)
                self.assertIn("тема турнира", direct[0]["content"])
                await db.run(Repository.set_chat_reply_level, -100, "lore", 3)
                msg.text, msg.message_id = "Спермер, ну и денёк", 12
                await process_chat_message(msg, bot, db=db, llm=llm)
                self.assertIn(
                    "шумный вентилятор", llm.generate.await_args.args[0][-1]["content"]
                )
