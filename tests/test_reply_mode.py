import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.handlers.basic import cmd_reply_mode
from sanya.intent import Mode
from sanya.prompts import ROFL_PROMPT, build_system_prompt
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class ReplyModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_switch_permissions_persistence_and_isolation(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mode.db"
            chat = NS(id=-100, type="supergroup")
            msg = NS(
                chat=chat, from_user=NS(id=42), reply=AsyncMock(), text="/режим рофл"
            )
            async with Database(path) as db:
                with patch(
                    "sanya.handlers.basic.check_is_admin", AsyncMock(return_value=False)
                ):
                    await cmd_reply_mode(msg, db)
                    self.assertIn("только админ", msg.reply.await_args.args[0])
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, chat.id), "normal"
                )
                with patch(
                    "sanya.handlers.basic.check_is_admin", AsyncMock(return_value=True)
                ):
                    await cmd_reply_mode(msg, db)
                msg.text = "/режим"
                await cmd_reply_mode(msg, db)
                self.assertIn("Режим этого чата: рофл", msg.reply.await_args.args[0])
            async with Database(path) as db:
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, chat.id), "rofl"
                )
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, 42), "normal"
                )
                with patch(
                    "sanya.handlers.basic.check_is_admin", AsyncMock(return_value=True)
                ):
                    msg.text = "/mode normal"
                    await cmd_reply_mode(msg, db)
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, chat.id), "normal"
                )

    async def test_rofl_bypasses_sale_template_and_marks_history(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "mode.db") as db:
                msg = NS(
                    text="Спермер, когда дохлые сигейты на Авито сольёшь?",
                    chat=NS(id=-100, type="supergroup"),
                    from_user=NS(id=42, username="kent"),
                    photo=None,
                    sticker=None,
                    caption=None,
                    reply_to_message=None,
                    message_id=15,
                )
                bot = NS(
                    get_me=AsyncMock(return_value=NS(id=9, username="ponyeb228_bot"))
                )
                llm = NS(
                    generate=AsyncMock(
                        return_value="Жодик уже в космосе торгуется\nТам защеку забрали без кабеля"
                    )
                )
                await db.run(Repository.set_chat_reply_mode, -100, "rofl")
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[16]),
                    ),
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(msg, bot, db=db, llm=llm)
                self.assertIn(
                    ROFL_PROMPT, llm.generate.await_args.kwargs["system_prompt"]
                )
                saved = await db.run(Repository.get_message, -100, 16)
                self.assertTrue(
                    saved["content"].startswith("[Вымысел из режима рофла]")
                )

    def test_code_and_plain_prompts_keep_their_contracts(self):
        self.assertNotIn(ROFL_PROMPT, build_system_prompt(Mode.CODE, "код", rofl=True))
        self.assertNotIn(ROFL_PROMPT, build_system_prompt(Mode.CHAT, "привет"))
