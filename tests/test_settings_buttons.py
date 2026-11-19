import re
import tempfile
import unittest
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, User

from sanya.database import Database
from sanya.handlers.basic import cmd_help
from sanya.handlers.reply_style import cmd_settings, update_settings
from sanya.handlers.vocabulary import (
    ADD_PROMPT,
    _word_id,
    dictionary_button,
    is_dictionary_reply,
)
from sanya.main import register_commands
from sanya.repository import Repository
from sanya.services.chat import automatic_sticker


class AutomaticStickerTests(unittest.TestCase):
    def test_levels_and_context_default(self):
        for level, default, threshold in (
            (0, 1.0, 0.0),
            (1, 1.0, 0.1),
            (2, 1.0, 0.25),
            (3, 1.0, 0.5),
            (None, 0.2, 0.2),
            (None, 1.0, 1.0),
        ):
            with self.subTest(level=level, default=default):
                if threshold:
                    with patch(
                        "sanya.services.chat.random.random",
                        return_value=threshold - 0.001,
                    ):
                        self.assertTrue(automatic_sticker(level, default))
                with patch("sanya.services.chat.random.random", return_value=threshold):
                    self.assertFalse(automatic_sticker(level, default))


class SettingsButtonsTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_opened_from_help_can_be_closed_by_opener_after_navigation(
        self,
    ):
        author = User(id=42, is_bot=False, first_name="Test")
        outsider = User(id=43, is_bot=False, first_name="Other")
        bot = User(id=99, is_bot=True, first_name="Bot")
        chat = Chat(id=-100, type="supergroup")
        now = datetime.now(timezone.utc)
        help_message = Message(message_id=1, date=now, chat=chat, from_user=bot)
        query = SimpleNamespace(
            message=help_message,
            from_user=author,
            data="settings:open",
            answer=AsyncMock(),
        )
        temp = self.enterContext(tempfile.TemporaryDirectory())
        db = await self.enterAsyncContext(Database(Path(temp) / "settings.sqlite"))
        with patch.object(Message, "reply", AsyncMock()) as reply:
            await update_settings(query, db)
        panel = Message(
            message_id=2,
            date=now,
            chat=chat,
            from_user=bot,
            reply_to_message=help_message,
            reply_markup=reply.await_args.kwargs["reply_markup"],
        )
        query.message = panel
        query.from_user = outsider
        query.data = "settings:dict"
        with patch.object(Message, "edit_text", AsyncMock()) as edit:
            await update_settings(query, db)
        query.message = panel.model_copy(
            update={"reply_markup": edit.await_args.kwargs["reply_markup"]}
        )
        close = query.message.reply_markup.inline_keyboard[-1][0]
        self.assertEqual(close.style, "danger")
        query.data = close.callback_data
        with (
            patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(side_effect=[False, True]),
            ) as admin,
            patch.object(Message, "delete", AsyncMock(return_value=True)) as delete,
        ):
            await update_settings(query, db)
            delete.assert_not_awaited()
            self.assertTrue(query.answer.await_args.kwargs["show_alert"])
            await update_settings(query, db)
            delete.assert_awaited_once()
            delete.reset_mock()
            query.from_user = author
            await update_settings(query, db)
            delete.assert_awaited_once()
            self.assertEqual(admin.await_count, 2)

    async def test_close_panel_only_deletes_the_menu(self):
        author = User(id=42, is_bot=False, first_name="Test")
        outsider = User(id=43, is_bot=False, first_name="Other")
        chat = Chat(id=-100, type="supergroup")
        now = datetime.now(timezone.utc)
        parent = Message(message_id=1, date=now, chat=chat, from_user=author)
        panel = Message(message_id=2, date=now, chat=chat, reply_to_message=parent)
        query = SimpleNamespace(
            message=panel, from_user=outsider, data="settings:close", answer=AsyncMock()
        )
        with (
            patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=False),
            ) as admin,
            patch.object(
                Message,
                "delete",
                autospec=True,
                side_effect=AsyncMock(return_value=True),
            ) as delete,
            patch.object(Message, "edit_text", AsyncMock()) as edit,
        ):
            await update_settings(query, None)
            delete.assert_not_called()
            self.assertTrue(query.answer.await_args.kwargs["show_alert"])
            query.from_user = author
            await update_settings(query, None)
            delete.assert_called_once_with(panel)
            edit.assert_not_awaited()
            admin.assert_awaited_once_with(chat, outsider.id)

    async def test_close_panel_falls_back_to_removing_controls(self):
        msg = Message(
            message_id=2,
            date=datetime.now(timezone.utc),
            chat=Chat(id=42, type="private"),
        )
        query = SimpleNamespace(
            message=msg,
            from_user=User(id=42, is_bot=False, first_name="Test"),
            data="settings:close",
            answer=AsyncMock(),
        )
        error = TelegramBadRequest(
            method=DeleteMessage(chat_id=42, message_id=2),
            message="message can't be deleted",
        )
        with (
            patch.object(Message, "delete", AsyncMock(side_effect=error)),
            patch.object(Message, "edit_text", AsyncMock()) as edit,
        ):
            await update_settings(query, None)
        edit.assert_awaited_once_with(
            "Меню закрыто.", reply_markup=None, parse_mode=None
        )
        query.answer.assert_awaited_once_with()

    async def test_command_menus_match_chat_scope(self):
        bot = SimpleNamespace(set_my_commands=AsyncMock())
        await register_commands(bot)
        menus = {
            call.kwargs["scope"].type: [cmd.command for cmd in call.args[0]]
            for call in bot.set_my_commands.await_args_list
        }
        self.assertEqual(
            set(menus),
            {
                "default",
                "all_private_chats",
                "all_group_chats",
                "all_chat_administrators",
            },
        )
        private, group, admins = (
            menus[key]
            for key in (
                "all_private_chats",
                "all_group_chats",
                "all_chat_administrators",
            )
        )
        self.assertIn("forgetme", private)
        self.assertIn("promptidea", private)
        self.assertNotIn("all", private)
        self.assertNotIn("setkent", group)
        self.assertIn("all", group)
        self.assertIn("promptidea", group)
        self.assertIn("setkent", admins)
        self.assertTrue(set(group) <= set(admins))

    async def test_help_is_split_and_has_settings_button(self):
        msg = SimpleNamespace(reply=AsyncMock())
        await cmd_help(msg)
        self.assertGreater(msg.reply.await_count, 1)
        for call in msg.reply.await_args_list:
            plain = unescape(re.sub(r"<[^>]*>", "", call.args[0]))
            self.assertLessEqual(len(plain.encode("utf-16-le")) // 2, 4096)
        keyboard = msg.reply.await_args.kwargs["reply_markup"]
        self.assertEqual(keyboard.inline_keyboard[0][0].callback_data, "settings:open")

    async def test_menu_and_callback_permissions(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "test.db"
        msg = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100, type="supergroup"),
        )
        query = SimpleNamespace(
            message=msg,
            from_user=User(id=42, is_bot=False, first_name="Test"),
            data="settings:all:tone:3",
            answer=AsyncMock(),
        )
        async with Database(path) as db:
            with patch.object(Message, "reply", AsyncMock()) as reply:
                await cmd_settings(msg, db)
                keyboard = reply.await_args.kwargs["reply_markup"]
                data = {
                    b.callback_data for row in keyboard.inline_keyboard for b in row
                }
                self.assertIn("settings:view:mode", data)
                self.assertIn("settings:view:lore", data)
                self.assertIn("settings:view:sticker", data)
                self.assertIn("settings:view:tone", data)
                close = keyboard.inline_keyboard[-1][0]
                self.assertEqual(close.callback_data, "settings:close")
                self.assertEqual(close.model_dump()["style"], "danger")
            with (
                patch.object(Message, "reply", AsyncMock()) as reply,
                patch.object(Message, "edit_text", AsyncMock()) as edit,
            ):
                for callback, heading in (
                    ("view:tone", "/тон:"),
                    ("view:all", "Настройки"),
                    ("dict", "Словарь"),
                    ("dict:1", "Словарь"),
                    ("view:all", "Настройки"),
                ):
                    query.data = f"settings:{callback}"
                    await update_settings(query, db)
                    self.assertTrue(edit.await_args.args[0].startswith(heading))
                    self.assertEqual(
                        edit.await_args.kwargs["reply_markup"]
                        .inline_keyboard[-1][0]
                        .callback_data,
                        "settings:close",
                    )
                reply.assert_not_awaited()
                self.assertEqual(edit.await_count, 5)
            query.data = "settings:tone:tone:3"
            with patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=False),
            ):
                await update_settings(query, db)
            self.assertEqual(
                (await db.run(Repository.get_chat_reply_settings, -100)).style.tone, 1
            )
            with (
                patch(
                    "sanya.handlers.reply_style.check_is_admin",
                    AsyncMock(return_value=True),
                ),
                patch.object(Message, "edit_text", AsyncMock()) as edit,
            ):
                await update_settings(query, db)
                self.assertIn("сейчас 3", edit.await_args.args[0])
            self.assertEqual(
                (await db.run(Repository.get_chat_reply_settings, -100)).style.tone, 3
            )
            self.assertEqual(
                (await db.run(Repository.get_chat_reply_settings, -200)).style.tone, 1
            )
            query.data = "settings:all:sticker:0"
            with patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=False),
            ):
                await update_settings(query, db)
            self.assertIsNone(
                (await db.run(Repository.get_chat_reply_settings, -100)).sticker_level
            )
            with (
                patch(
                    "sanya.handlers.reply_style.check_is_admin",
                    AsyncMock(return_value=True),
                ),
                patch.object(Message, "edit_text", AsyncMock()),
            ):
                await update_settings(query, db)
                self.assertEqual(
                    (
                        await db.run(Repository.get_chat_reply_settings, -100)
                    ).sticker_level,
                    0,
                )
                self.assertIsNone(
                    (
                        await db.run(Repository.get_chat_reply_settings, -200)
                    ).sticker_level
                )
                query.data = "settings:all:sticker:default"
                await update_settings(query, db)
                self.assertIsNone(
                    (
                        await db.run(Repository.get_chat_reply_settings, -100)
                    ).sticker_level
                )

    async def test_dictionary_buttons_and_matching_reply(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "test.db"
        bot = User(id=99, is_bot=True, first_name="Bot")
        msg = Message(
            message_id=1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=42, type="private"),
            from_user=bot,
            text=ADD_PROMPT,
        )
        author = User(id=42, is_bot=False, first_name="Test")
        query = SimpleNamespace(
            message=msg, from_user=author, data="settings:word:add", answer=AsyncMock()
        )
        async with Database(path) as db:
            with patch.object(Message, "reply", AsyncMock()) as reply:
                await dictionary_button(query, db)
                self.assertTrue(reply.await_args.kwargs["reply_markup"].force_reply)
            self.assertTrue(
                is_dictionary_reply(
                    SimpleNamespace(text="шумелка = вентилятор", reply_to_message=msg),
                    bot,
                )
            )
            await db.run(Repository.add_chat_word, 42, "шумелка", "вентилятор")
            row = (await db.run(Repository.list_chat_words, 42))[0]
            query.data = f"settings:word:pick:{_word_id(row)}"
            with patch.object(Message, "edit_text", AsyncMock()) as edit:
                await dictionary_button(query, db)
                query.data = (
                    edit.await_args.kwargs["reply_markup"]
                    .inline_keyboard[0][0]
                    .callback_data
                )
                self.assertEqual(len(await db.run(Repository.list_chat_words, 42)), 1)
                await dictionary_button(query, db)
            self.assertEqual(await db.run(Repository.list_chat_words, 42), [])
