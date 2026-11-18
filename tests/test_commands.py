import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType

from sanya.handlers.basic import cmd_clear
from sanya.handlers.command_args import reply_html_lines
from sanya.handlers.media import cmd_search
from sanya.handlers.memory import cmd_delchat, cmd_setchat
from sanya.handlers.stickers import pack_name_from_arg
from sanya.lore import INITIAL_KENTS_PROFILES
from sanya.repository import Repository
from sanya.schema import initialize
from sanya.services.chat import process_chat_message


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = SimpleNamespace(
            text="/clear",
            reply=AsyncMock(),
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=-1001, type=ChatType.SUPERGROUP, title="chat"),
        )
        self.db = SimpleNamespace(run=AsyncMock())

    async def test_clear_requires_group_admin_and_reports_database_failure(self):
        with patch(
            "sanya.handlers.basic.check_is_admin", AsyncMock(return_value=False)
        ):
            await cmd_clear(self.message, self.db)
        self.db.run.assert_not_awaited()
        self.message.chat.type = ChatType.PRIVATE
        self.db.run.return_value = None
        await cmd_clear(self.message, self.db)
        self.db.run.assert_awaited_once_with(Repository.clear_history, -1001)
        self.assertIn("Не удалось", self.message.reply.await_args.args[0])

    async def test_search_parses_newline_and_reports_queue_rejection(self):
        self.message.text = "/s@ponyeb228_bot\nцены на RTX 3070"
        queue = SimpleNamespace(enqueue=AsyncMock(return_value=False))
        with patch("sanya.handlers.media.try_react_whale", AsyncMock()):
            await cmd_search(self.message, queue)
        queue.enqueue.assert_awaited_once_with(
            self.message, force_search_query="цены на RTX 3070"
        )
        self.message.reply.assert_awaited_once()

    async def test_search_does_not_invent_results_if_backend_returns_nothing(self):
        self.message.text = "/s напиши скрипт"
        self.message.photo = self.message.sticker = self.message.reply_to_message = None
        self.message.from_user.username = self.message.from_user.first_name = "reader"
        self.message.message_id = 9
        self.db.run.return_value = ([], None)
        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(id=1, username="ponyeb228_bot")
            )
        )
        llm = SimpleNamespace(generate=AsyncMock())
        with (
            patch("sanya.services.chat.ENABLE_WEB_SEARCH", True),
            patch(
                "sanya.services.chat.lookup_snippets", AsyncMock(return_value="")
            ) as search,
            patch(
                "sanya.services.chat.dialogue_notes",
                AsyncMock(return_value=([], False)),
            ),
            patch("sanya.services.chat.keep_typing", AsyncMock()),
        ):
            await process_chat_message(
                self.message,
                bot,
                db=self.db,
                llm=llm,
                force_search_query="напиши скрипт",
            )
        search.assert_awaited_once_with("напиши скрипт")
        llm.generate.assert_not_awaited()

    async def test_chat_admin_cannot_access_another_chats_lore(self):
        async def is_admin(chat, user_id):
            return chat is self.message.chat

        with patch("sanya.handlers.memory.check_is_admin", is_admin):
            for handler, text in (
                (cmd_setchat, "/setchat -1002"),
                (cmd_delchat, "/delchat -1002 7"),
            ):
                self.message.text = text
                await handler(self.message, self.db)
        self.db.run.assert_not_awaited()

    async def test_long_html_output_keeps_valid_pages(self):
        await reply_html_lines(
            self.message, ["<b>Заголовок</b>", "<i>" + "😱" * 2500 + "</i>"]
        )
        self.assertEqual(self.message.reply.await_count, 3)
        self.assertEqual(
            self.message.reply.await_args_list[0].kwargs["parse_mode"], "HTML"
        )
        self.assertIsNone(self.message.reply.await_args_list[1].kwargs["parse_mode"])

    def test_pack_name_parser(self):
        self.assertEqual(
            pack_name_from_arg("https://t.me/addstickers/Hihehovskoe"), "Hihehovskoe"
        )
        self.assertIsNone(pack_name_from_arg("https://example.org/addstickers/test"))


class CommandRepositoryTests(unittest.TestCase):
    def test_restore_is_idempotent_and_keeps_manual_facts(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            uid = next(iter(INITIAL_KENTS_PROFILES))
            conn.execute("DELETE FROM kent_facts_entries WHERE user_id=?", (uid,))
            conn.execute(
                "UPDATE user_profiles SET persona_notes='', message_count=42 WHERE user_id=?",
                (uid,),
            )
            manual_id = repo.add_kent_fact_entry(uid, "Ручной факт", added_by=7)
            self.assertEqual(repo.restore_initial_kents(), len(INITIAL_KENTS_PROFILES))
            self.assertTrue(repo.restore_single_kent(uid))
            rows = repo.get_kent_fact_entries(uid)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["id"], manual_id)
            self.assertEqual(repo.get_user_profile(uid)["message_count"], 42)

    def test_autofact_deletion_scopes_and_full_forget_removes_user_facts(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            for kind, uid in (("chat", -1001), ("chat", -1002), ("user", 7)):
                repo.record_auto_fact(kind, uid, "key", "test")
            ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM auto_observed_facts WHERE fact_key='key' ORDER BY id"
                )
            ]
            self.assertEqual(
                repo.delete_auto_facts(ids, chat_id=-1001), [(ids[0], "test")]
            )
            self.assertIsNotNone(repo.clear_user_data(7, delete_profile=True))
            self.assertFalse(repo.get_auto_facts("user", 7))
            self.assertEqual(len(repo.get_auto_facts("chat", -1002)), 1)

    def test_sticker_pack_and_cache_write_together(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            self.assertTrue(
                repo.add_sticker_pack(
                    "TestPack",
                    "test",
                    stickers=[{"file_id": "sample", "pack_name": "TestPack"}],
                )
            )
            self.assertEqual(
                next(
                    p for p in repo.get_sticker_packs() if p["pack_name"] == "TestPack"
                )["sticker_count"],
                1,
            )
            with self.assertLogs(level="ERROR"):
                self.assertFalse(
                    repo.add_sticker_pack("BrokenPack", "test", stickers=[{}])
                )
            self.assertNotIn(
                "BrokenPack", {p["pack_name"] for p in repo.get_sticker_packs()}
            )
