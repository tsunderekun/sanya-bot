import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.types import Chat, Message, User

from sanya.database import Database
from sanya.handlers.lore_panel import (
    CHAT_INPUT,
    PREFIX,
    is_lore_panel_reply,
    lore_panel_button,
    save_lore_panel_reply,
)
from sanya.handlers.memory import cmd_setchat, cmd_setkent
from sanya.repository import Repository


class LorePanelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.db = await self.enterAsyncContext(Database(Path(directory) / "test.db"))
        self.chat = Chat(id=-10042, type="supergroup", title="Test")
        self.user = User(id=22, is_bot=False, first_name="Test")
        self.bot = User(id=23, is_bot=True, first_name="Bot")
        self.admin = self.enterContext(
            patch(
                "sanya.handlers.lore_panel.check_is_admin", AsyncMock(return_value=True)
            )
        )
        self.reply = self.enterContext(patch.object(Message, "reply", AsyncMock()))
        self.edit = self.enterContext(patch.object(Message, "edit_text", AsyncMock()))

    def message(self, text, *, author=None, parent=None, chat=None):
        return Message(
            message_id=10 if parent is None else 11,
            date=datetime.now(timezone.utc),
            chat=chat or self.chat,
            from_user=author or self.user,
            text=text,
            reply_to_message=parent,
        )

    async def click(self, data):
        query = SimpleNamespace(
            message=self.message("Меню", author=self.bot),
            from_user=self.user,
            data=PREFIX + data,
            answer=AsyncMock(),
        )
        await lore_panel_button(query, self.db)
        return query

    async def test_commands_open_panels_and_navigation_edits_in_place(self):
        await self.db.run(Repository.add_kent_fact_entry, self.user.id, "Факт о кенте")
        await cmd_setkent(self.message("/кент"), self.db)
        self.assertIn("Кенты", self.reply.await_args.args[0])
        await cmd_setkent(
            self.message("/кент", parent=self.message("Сообщение")), self.db
        )
        self.assertIn("Факт о кенте", self.reply.await_args.args[0])
        await cmd_setchat(self.message("/лорконфы"), self.db)
        self.assertIn("Лор конфы", self.reply.await_args.args[0])
        self.reply.reset_mock()
        await self.click(f"view:{self.user.id}:0")
        close = self.edit.await_args.kwargs["reply_markup"].inline_keyboard[-1][0]
        self.assertEqual(
            (close.callback_data, close.style), ("settings:close", "danger")
        )
        self.reply.assert_not_awaited()

    async def test_delete_requires_admin_confirmation_and_matching_scope(self):
        own = await self.db.run(
            Repository.add_chat_lore_entry, self.chat.id, "Test", "Свой факт", 0
        )
        other = await self.db.run(
            Repository.add_chat_lore_entry, -10043, "Other", "Другой факт", 0
        )
        await self.click(f"pick:0:{own}")
        self.assertIn("Удалить факт", self.edit.await_args.args[0])
        self.assertTrue(
            await self.db.run(Repository.get_chat_lore_entries, self.chat.id)
        )
        self.admin.return_value = False
        denied = await self.click(f"confirm:0:{own}")
        self.assertTrue(denied.answer.await_args.kwargs["show_alert"])
        self.assertTrue(
            await self.db.run(Repository.get_chat_lore_entries, self.chat.id)
        )
        self.admin.return_value = True
        await self.click(f"confirm:0:{other}")
        self.assertTrue(await self.db.run(Repository.get_chat_lore_entries, -10043))
        await self.click(f"confirm:0:{own}")
        self.assertFalse(
            await self.db.run(Repository.get_chat_lore_entries, self.chat.id)
        )

    async def test_reply_add_checks_bot_chat_and_admin_and_keeps_target(self):
        await self.click(f"add:{self.user.id}:0")
        prompt = self.reply.await_args.args[0]
        with patch.object(Message, "delete", AsyncMock(return_value=True)) as delete:
            for target, text in ((self.user.id, prompt), (0, CHAT_INPUT)):
                parent = self.message(text, author=self.bot)
                msg = self.message("Новый факт", parent=parent)
                self.assertTrue(is_lore_panel_reply(msg, self.bot))
                self.assertFalse(is_lore_panel_reply(msg, self.user))
                self.assertFalse(
                    is_lore_panel_reply(
                        self.message(
                            "Новый факт",
                            parent=parent,
                            chat=Chat(id=-10043, type="supergroup"),
                        ),
                        self.bot,
                    )
                )
                self.admin.return_value = False
                await save_lore_panel_reply(msg, self.db)
                getter = (
                    Repository.get_kent_fact_entries
                    if target
                    else Repository.get_chat_lore_entries
                )
                self.assertFalse(await self.db.run(getter, target or self.chat.id))
                self.admin.return_value = True
                await save_lore_panel_reply(msg, self.db)
                facts = await self.db.run(getter, target or self.chat.id)
                self.assertEqual([row["content"] for row in facts], ["Новый факт"])
            self.assertEqual(delete.await_count, 2)
