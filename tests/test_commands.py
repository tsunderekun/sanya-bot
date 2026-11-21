import asyncio
import sqlite3
import unittest
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType

from sanya.handlers.basic import cmd_clear
from sanya.handlers.command_args import reply_html_lines
from sanya.handlers.media import cmd_search
from sanya.handlers.memory import cmd_delchat, cmd_mydata, cmd_setchat
from sanya.handlers.stickers import pack_name_from_arg
from sanya.lore import INITIAL_KENTS_PROFILES
from sanya.repository import Repository
from sanya.schema import initialize
from sanya.services.chat import process_chat_message
from sanya.services.queue import EnqueueResult
from sanya.services.search import SearchEvidence


class CommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.message = SimpleNamespace(
            text="/clear",
            reply=AsyncMock(),
            from_user=SimpleNamespace(id=7),
            reply_to_message=None,
            chat=SimpleNamespace(id=-1001, type=ChatType.SUPERGROUP, title="chat"),
        )
        self.db = SimpleNamespace(run=AsyncMock())

    async def test_mydata_without_target_returns_own_profile(self):
        self.message.text = "/досье"
        self.db.run.side_effect = [
            dict(
                user_name="caller",
                first_seen="first",
                last_seen="last",
                message_count=4,
                persona_notes="own note",
            ),
            [{"fact_text": "own fact"}],
        ]
        await cmd_mydata(self.message, self.db)
        self.db.run.assert_any_await(Repository.get_user_profile, 7)
        self.db.run.assert_any_await(Repository.get_self_facts, -1001, 7, 5)
        text = self.message.reply.await_args.args[0]
        for fragment in ("про тебя", "own note", "own fact", "/forgetme"):
            self.assertIn(fragment, text)

    async def test_mydata_target_only_shows_current_group_count(self):
        self.message.text = "/досье@ponyeb228_bot\n@other_user"
        profile = dict(user_id=8, user_name="other_user", persona_notes="private notes")
        self.db.run.side_effect = [profile, 12, profile]
        await cmd_mydata(self.message, self.db)
        self.db.run.assert_any_await(
            Repository.find_user_by_username, "other_user", current_only=True
        )
        self.db.run.assert_any_await(Repository.count_user_chat_messages, -1001, 8)
        self.db.run.assert_any_await(Repository.get_user_profile, 8)
        self.assertEqual(self.db.run.await_count, 3)
        text = self.message.reply.await_args.args[0]
        self.assertIn("<b>other_user</b>", text)
        self.assertIn("<code>8</code>", text)
        self.assertIn("этого чата: 12", text)
        self.assertNotIn("private notes", text)
        self.assertNotIn("/forgetme", text)

    async def test_mydata_unknown_tag_never_falls_back_to_reply_or_self(self):
        self.message.text = "/досье @unknown"
        self.message.reply_to_message = SimpleNamespace(
            from_user=SimpleNamespace(id=8, username="someone")
        )
        self.db.run.return_value = None
        await cmd_mydata(self.message, self.db)
        self.db.run.assert_awaited_once_with(
            Repository.find_user_by_username, "unknown", current_only=True
        )
        self.message.reply.assert_awaited_once_with(
            "В базе про @unknown ничего нет\nНе нарыл инфы братан"
        )

    async def test_mydata_reply_and_id_use_the_other_profile(self):
        profile = dict(user_id=8, user_name="other_user")
        self.message.reply_to_message = SimpleNamespace(
            from_user=SimpleNamespace(id=8, username="other_user")
        )
        for text, results in (
            ("/досье", [0, profile]),
            ("/досье 8", [profile, 0, profile]),
        ):
            with self.subTest(text=text):
                self.message.text = text
                self.db.run.side_effect = results
                await cmd_mydata(self.message, self.db)
                self.assertIn("<code>8</code>", self.message.reply.await_args.args[0])

    async def test_mydata_checks_membership_when_group_history_is_missing(self):
        self.message.text = "/досье @other_user"
        self.message.chat.get_member = AsyncMock(
            return_value=SimpleNamespace(status="left")
        )
        self.db.run.side_effect = [dict(user_id=8, user_name="other_user"), 0]
        await cmd_mydata(self.message, self.db)
        self.message.chat.get_member.assert_awaited_once_with(8)
        self.assertIn("ничего нет", self.message.reply.await_args.args[0])

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
        for result, expected in (
            (EnqueueResult.FULL, "очередь заполнена"),
            (EnqueueResult.COOLDOWN, "Слишком частые запросы"),
        ):
            with self.subTest(result=result):
                self.message.reply.reset_mock()
                queue = SimpleNamespace(enqueue=AsyncMock(return_value=result))
                with patch("sanya.handlers.media.try_react_whale", AsyncMock()):
                    await cmd_search(self.message, queue)
                queue.enqueue.assert_awaited_once_with(
                    self.message, force_search_query="цены на RTX 3070"
                )
                self.message.reply.assert_awaited_once()
                self.assertIn(expected, self.message.reply.await_args.args[0])

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
                "sanya.services.chat.lookup_search_evidence",
                AsyncMock(return_value=SearchEvidence("")),
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

    async def test_search_reuses_empty_prefetch_without_repeating_lookup(self):
        self.message.text = "/s test"
        self.message.photo = self.message.sticker = None
        self.message.from_user.username = "reader"
        self.message.message_id = 9
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="bot")))
        llm = SimpleNamespace(generate=AsyncMock())
        prefetched = asyncio.create_task(AsyncMock(return_value=SearchEvidence(""))())
        with (
            patch("sanya.services.chat.ENABLE_WEB_SEARCH", True),
            patch("sanya.services.chat.lookup_search_evidence", AsyncMock()) as search,
            patch("sanya.services.chat.dialogue_notes", AsyncMock(return_value=([], False))),
            patch("sanya.services.chat.keep_typing", AsyncMock()),
        ):
            await process_chat_message(
                self.message, bot, db=self.db, llm=llm,
                force_search_query="test", prefetched_search=prefetched,
            )
        search.assert_not_awaited()
        llm.generate.assert_not_awaited()
        self.assertIn("Поиск ничего не вернул", self.message.reply.await_args.args[0])

    async def test_search_appends_real_urls_to_grounded_answer(self):
        self.message.text = "/s характеристики RTX 3070 на сайте NVIDIA"
        self.message.photo = self.message.sticker = None
        self.message.from_user.username = "reader"
        self.message.message_id = 9
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="bot")))
        answer = "Во фрагменте NVIDIA указан Ampere, но нет объёма видеопамяти."
        llm = SimpleNamespace(generate=AsyncMock(return_value=answer))
        sources = "Источник 1 (www.nvidia.com): RTX 3070: Ampere"
        url = "https://www.nvidia.com/geforce/graphics-cards/30-series/rtx-3070-3070ti/"
        prefetched = asyncio.create_task(AsyncMock(return_value=SearchEvidence(sources, (url,)))())
        with (
            patch("sanya.services.chat.ENABLE_WEB_SEARCH", True),
            patch("sanya.services.chat.lookup_search_evidence", AsyncMock()) as search,
            patch("sanya.services.chat.dialogue_notes", AsyncMock(return_value=([], False))),
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[10])) as send,
        ):
            await process_chat_message(
                self.message, bot, db=self.db, llm=llm,
                force_search_query=self.message.text[3:], prefetched_search=prefetched,
            )
        search.assert_not_awaited()
        prompt = llm.generate.await_args.kwargs["system_prompt"]
        self.assertIn("прямо скажи, чего не хватает", prompt)
        self.assertIn(sources, llm.generate.await_args.args[0][-1]["content"])
        self.assertEqual(send.await_args.args[3], answer.rstrip(".") + f"\n\nСсылки из выдачи:\n1. {url}")

    async def test_search_sends_official_specs_without_generation(self):
        self.message.text = "/s характеристики RTX 3070 NVIDIA"
        self.message.photo = self.message.sticker = None
        self.message.from_user.username = "reader"
        self.message.message_id = 9
        bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="bot")))
        llm = SimpleNamespace(generate=AsyncMock())
        evidence = SearchEvidence("", ("https://www.nvidia.com/",), official_answer="5888 CUDA-ядер")
        with (
            patch("sanya.services.chat.ENABLE_WEB_SEARCH", True),
            patch("sanya.services.chat.lookup_search_evidence", AsyncMock(return_value=evidence)),
            patch("sanya.services.chat.dialogue_notes", AsyncMock(return_value=([], False))),
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[10])) as send,
        ):
            await process_chat_message(
                self.message, bot, db=self.db, llm=llm, force_search_query=self.message.text[3:]
            )
        llm.generate.assert_not_awaited()
        send.assert_awaited_once_with(
            bot, -1001, 9, "5888 CUDA-ядер\nИсточник: https://www.nvidia.com/", is_detailed=True
        )

    async def test_gpu_review_in_group_generates_technical_answer_without_search(self):
        self.message.text = "/оцени RTX 3070"
        self.message.photo = self.message.sticker = None
        self.message.from_user.username = "reader"
        self.message.message_id = 9
        self.db.run.return_value = []
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="ponyeb228_bot"))
        )
        answer = "RTX 3070 подходит для 1440p, но в тяжёлых играх может не хватать видеопамяти."
        llm = SimpleNamespace(generate=AsyncMock(return_value=answer))
        with (
            patch("sanya.services.chat.ENABLE_WEB_SEARCH", True),
            patch("sanya.services.chat.lookup_snippets", AsyncMock()) as search,
            patch("sanya.services.chat.dialogue_notes", AsyncMock(return_value=([], False))),
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[10])) as send,
        ):
            await process_chat_message(
                self.message, bot, db=self.db, llm=llm, force_gpu_query="RTX 3070"
            )
        search.assert_not_awaited()
        llm.generate.assert_awaited_once()
        self.assertIn("RTX 3070", llm.generate.await_args.args[0][-1]["content"])
        prompt = llm.generate.await_args.kwargs["system_prompt"]
        self.assertIn("цен и объявлений", prompt)
        self.assertIn("8 ГБ GDDR6", prompt)
        self.assertTrue(llm.generate.await_args.kwargs["short_answer"])
        send.assert_awaited_once_with(
            bot, -1001, 9, answer.rstrip("."), is_detailed=True, compact=True
        )

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
