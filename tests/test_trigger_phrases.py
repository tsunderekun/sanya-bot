import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType
from aiogram.types import (
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from sanya.database import Database
from sanya.handlers.chat import handle_message
from sanya.handlers.reply_style import (
    is_trigger_panel_reply,
    save_trigger_panel_reply,
    settings_view,
    update_settings,
)
from sanya.repository import Repository
from sanya.services.queue import EnqueueResult
from sanya.trigger_phrases import matches_trigger_phrase, normalize_phrase


class TriggerPhraseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(temp) / "trigger.sqlite"
        self.db = await self.enterAsyncContext(Database(self.path))

    def test_matching_normalizes_case_and_spacing_but_respects_word_boundaries(self):
        self.assertEqual(normalize_phrase("  Ну   Чё,   Саня? "), "ну чё, саня?")
        self.assertTrue(matches_trigger_phrase("Эй, НУ   ЧЁ, САНЯ?!", "ну чё, саня?"))
        self.assertFalse(matches_trigger_phrase("предну чё, саня?", "ну чё, саня?"))
        self.assertFalse(matches_trigger_phrase("ну чё, саня?extra", "ну чё, саня?"))

    async def test_storage_toggle_chat_isolation_and_persistence(self):
        self.assertEqual(
            await self.db.run(Repository.add_chat_trigger_phrase, -100, "Эй, брат", 42),
            "added",
        )
        self.assertEqual(
            await self.db.run(
                Repository.add_chat_trigger_phrase, -100, " эй,   БРАТ ", 42
            ),
            "exists",
        )
        self.assertEqual(
            await self.db.run(
                Repository.add_chat_trigger_phrase, -101, "Другая фраза", 42
            ),
            "added",
        )
        self.assertTrue(
            await self.db.run(
                Repository.matches_chat_trigger_phrase, -100, "ЭЙ,   БРАТ!"
            )
        )
        self.assertFalse(
            await self.db.run(Repository.matches_chat_trigger_phrase, -101, "ЭЙ, БРАТ!")
        )
        row = (await self.db.run(Repository.get_chat_trigger_phrases, -100))[0]
        self.assertTrue(
            await self.db.run(Repository.toggle_chat_trigger_phrase, -100, row["id"])
        )
        self.assertFalse(
            await self.db.run(Repository.matches_chat_trigger_phrase, -100, "Эй, брат!")
        )
        async with Database(self.path) as reopened:
            stored = await reopened.run(Repository.get_chat_trigger_phrases, -100)
            self.assertEqual(
                [(r["phrase"], r["enabled"]) for r in stored], [("Эй, брат", 0)]
            )

    async def test_reply_to_trigger_panel_adds_phrase_and_refreshes_menu(self):
        panel = Message(
            message_id=9,
            date=datetime.now(timezone.utc),
            text="Фразы обращения к боту\nОтветь на эту панель",
            chat=Chat(id=-100, type=ChatType.SUPERGROUP),
            from_user=User(id=99, is_bot=True, first_name="Bot"),
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Фразы", callback_data="settings:view:triggers"
                        )
                    ]
                ]
            ),
        )
        incoming = Message(
            message_id=10,
            date=datetime.now(timezone.utc),
            chat=panel.chat,
            from_user=User(id=42, is_bot=False, first_name="User"),
            reply_to_message=panel,
            text="Эй, братан",
        )
        self.assertTrue(
            is_trigger_panel_reply(incoming, User(id=99, is_bot=True, first_name="Bot"))
        )
        message = NS(
            chat=panel.chat,
            from_user=NS(id=42),
            text="Эй, братан",
            caption=None,
            reply_to_message=panel,
            reply=AsyncMock(),
        )
        with (
            patch(
                "sanya.handlers.reply_style.check_is_admin",
                AsyncMock(return_value=True),
            ),
            patch("sanya.handlers.reply_style.edit_panel", AsyncMock()) as edit_panel,
        ):
            await save_trigger_panel_reply(message, self.db)
        self.assertIn("добавил", message.reply.await_args.args[0])
        edit_panel.assert_awaited_once()
        self.assertTrue(
            await self.db.run(
                Repository.matches_chat_trigger_phrase, -100, "ЭЙ,   БРАТАН!"
            )
        )

    async def test_regular_member_cannot_add_or_toggle_group_trigger(self):
        panel = Message(
            message_id=5,
            date=datetime.now(timezone.utc),
            chat=Chat(id=-100, type=ChatType.SUPERGROUP),
        )
        message = NS(
            chat=panel.chat,
            from_user=NS(id=42),
            text="Эй, брат",
            caption=None,
            reply_to_message=panel,
            reply=AsyncMock(),
        )
        with patch(
            "sanya.handlers.reply_style.check_is_admin", AsyncMock(return_value=False)
        ):
            await save_trigger_panel_reply(message, self.db)
        self.assertIn("только админы", message.reply.await_args.args[0])
        await self.db.run(Repository.add_chat_trigger_phrase, -100, "Эй, брат", 1)
        row = (await self.db.run(Repository.get_chat_trigger_phrases, -100))[0]
        query = NS(
            message=panel,
            from_user=NS(id=42),
            data=f"settings:trigger:toggle:{row['id']}",
            answer=AsyncMock(),
        )
        with patch(
            "sanya.handlers.reply_style.check_is_admin", AsyncMock(return_value=False)
        ):
            await update_settings(query, self.db)
        query.answer.assert_awaited_once()
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])
        self.assertTrue(
            await self.db.run(Repository.matches_chat_trigger_phrase, -100, "Эй, брат")
        )

    async def test_custom_trigger_enqueues_only_in_own_group(self):
        bot = NS(get_me=AsyncMock(return_value=NS(id=99, username="ponyeb228_bot")))
        requests = NS(
            enqueue=AsyncMock(return_value=EnqueueResult.ACCEPTED),
            last_proactive={},
            proactive_history={},
        )
        message = NS(
            from_user=NS(id=42, is_bot=False, username="reader", first_name="Reader"),
            chat=NS(id=-100, type=ChatType.SUPERGROUP, title=None),
            message_id=10,
            reply_to_message=None,
            text="ЭЙ,   БРАТАН!",
            caption=None,
            photo=None,
            sticker=None,
        )
        await self.db.run(Repository.add_chat_trigger_phrase, -100, "Эй, братан", 1)
        await self.db.run(Repository.add_chat_trigger_phrase, -101, "Другая фраза", 1)
        with patch("sanya.handlers.chat.try_react_whale", AsyncMock()):
            await handle_message(message, bot, self.db, requests)
        requests.enqueue.assert_awaited_once_with(
            message, is_proactive=False, custom_trigger=True
        )
        requests.enqueue.reset_mock()
        message.chat.id = -101
        message.text = "Эй, братан!"
        with patch("sanya.handlers.chat.try_react_whale", AsyncMock()):
            await handle_message(message, bot, self.db, requests)
        requests.enqueue.assert_not_awaited()

    async def test_settings_panel_lists_trigger_controls(self):
        await self.db.run(Repository.add_chat_trigger_phrase, -100, "Эй, брат", 42)
        text, keyboard = await settings_view(self.db, -100, "triggers")
        self.assertIn("Ответь на эту панель фразой", text)
        self.assertEqual(keyboard.inline_keyboard[0][0].text, "✓ Эй, брат")
        self.assertEqual(
            keyboard.inline_keyboard[0][0].callback_data, "settings:trigger:toggle:1"
        )


if __name__ == "__main__":
    unittest.main()
