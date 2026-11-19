import sqlite3
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.handlers.basic import cmd_reply_mode
from sanya.handlers.media import cmd_prompt_idea
from sanya.handlers.memes import cmd_meme
from sanya.intent import Mode
from sanya.prompts import ROFL_PROMPT, build_system_prompt
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.safe_mode import SAFE_VOICE


class ReplyModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_migrates_existing_modes_and_persists(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "mode.db"
            conn = sqlite3.connect(path)
            with conn:
                conn.execute(
                    "CREATE TABLE chat_reply_modes (chat_id INTEGER PRIMARY KEY, mode TEXT NOT NULL CHECK (mode IN ('normal', 'rofl')))"
                )
                conn.execute("INSERT INTO chat_reply_modes VALUES (-100, 'rofl')")
            conn.close()
            async with Database(path) as db:
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, -100), "rofl"
                )
                await db.run(Repository.set_chat_reply_mode, -200, "safe")
                with self.assertRaises(sqlite3.IntegrityError):
                    await db.run(Repository.set_chat_reply_mode, -100, "invalid")
            async with Database(path) as db:
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, -200), "safe"
                )
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, -100), "rofl"
                )

    async def test_safe_warns_once_ignores_background_and_retries_rough_output(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "mode.db") as db:
                msg = NS(
                    text="Спермер, ты и клоун",
                    chat=NS(id=-100, type="supergroup"),
                    from_user=NS(id=42, username="kent"),
                    photo=None,
                    sticker=None,
                    caption=None,
                    reply_to_message=None,
                    message_id=15,
                )
                bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="bot")))
                llm = NS(
                    generate=AsyncMock(
                        side_effect=["Ты идиот", "Давай разберёмся вместе"]
                    )
                )
                await db.run(Repository.set_chat_reply_mode, -100, "safe")
                await db.run(Repository.set_chat_reply_level, -100, "tone", 3)
                with (
                    patch(
                        "sanya.services.safe_mode._warnings", OrderedDict()
                    ) as warnings,
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[16]),
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch(
                        "sanya.services.chat.send_sanya_sticker", AsyncMock()
                    ) as sticker,
                ):
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    self.assertIn("Подколы и оскорбления", send.await_args.args[3])
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    msg.text = "Ну ты и клоун"
                    await process_chat_message(
                        msg, bot, db=db, llm=llm, is_proactive=True
                    )
                    send.assert_awaited_once()
                    llm.generate.assert_not_awaited()
                    self.assertEqual(len(warnings), 1)
                    msg.text = "Спермер, ну ты и клоун, а как прошла игра?"
                    await process_chat_message(msg, bot, db=db, llm=llm)
                    self.assertEqual(send.await_args.args[3], "Давай разберёмся вместе")
                    self.assertEqual(send.await_count, 2)
                    self.assertEqual(llm.generate.await_count, 2)
                    current = llm.generate.await_args.args[0][-1]["content"]
                    self.assertIn("как прошла игра?", current)
                    self.assertNotIn("клоун", current)
                    self.assertIn(
                        SAFE_VOICE, llm.generate.await_args.kwargs["system_prompt"]
                    )
                    sticker.assert_not_awaited()

    async def test_safe_restricts_media_even_for_previously_confirmed_requests(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "mode.db") as db:
                msg = NS(
                    text="/r34 cat",
                    chat=NS(id=-100, type="supergroup"),
                    from_user=NS(id=42, username="kent"),
                    photo=None,
                    sticker=None,
                    caption=None,
                    reply_to_message=None,
                    message_id=15,
                    reply=AsyncMock(),
                )
                bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="bot")))
                llm = NS(generate=AsyncMock())
                await db.run(Repository.set_chat_reply_mode, -100, "safe")
                with (
                    patch(
                        "sanya.handlers.media.ask_prompt_idea_confirmation", AsyncMock()
                    ) as confirm_idea,
                    patch("sanya.handlers.memes.public_memes", AsyncMock()) as memes,
                    patch(
                        "sanya.services.chat._process_prompt_idea", AsyncMock()
                    ) as idea,
                    patch(
                        "sanya.services.chat.ask_booru_confirmation", AsyncMock()
                    ) as ask,
                    patch(
                        "sanya.services.chat.search_and_download_booru_image",
                        AsyncMock(return_value=None),
                    ) as search,
                    patch(
                        "sanya.services.chat.search_and_download_image", AsyncMock()
                    ) as pic,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await cmd_prompt_idea(msg, db)
                    await cmd_meme(msg, db)
                    await process_chat_message(
                        msg, bot, db=db, llm=llm, prompt_idea=True
                    )
                    await process_chat_message(
                        msg, bot, db=db, llm=llm, force_image_query="cat"
                    )
                    await process_chat_message(
                        msg,
                        bot,
                        db=db,
                        llm=llm,
                        force_booru_query="cat, explicit",
                        booru_confirmed=True,
                    )
                    search.assert_not_awaited()
                    await process_chat_message(
                        msg,
                        bot,
                        db=db,
                        llm=llm,
                        force_booru_query="cat",
                        booru_confirmed=True,
                    )
                    await process_chat_message(
                        msg, bot, db=db, llm=llm, force_booru_query="cat, safe"
                    )
                    for blocked in (
                        confirm_idea,
                        memes,
                        idea,
                        ask,
                        pic,
                        search,
                        llm.generate,
                    ):
                        blocked.assert_not_awaited()

    async def test_safe_uses_saved_lore_as_context_not_as_a_direct_reply(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "mode.db") as db:
                msg = NS(
                    text="Спермер, расскажи про конфу",
                    chat=NS(id=-100, type="supergroup"),
                    from_user=NS(id=42, username="kent"),
                    photo=None,
                    sticker=None,
                    caption=None,
                    reply_to_message=None,
                    message_id=17,
                )
                bot = NS(get_me=AsyncMock(return_value=NS(id=9, username="bot")))
                llm = NS(
                    generate=AsyncMock(
                        return_value="В группе обсуждают настольные игры."
                    )
                )
                await db.run(Repository.set_chat_reply_mode, -100, "safe")
                await db.run(
                    Repository.add_chat_lore_entry,
                    -100,
                    "Test",
                    "Лор: еженедельные настольные игры.",
                    42,
                )
                with (
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[18]),
                    ) as send,
                ):
                    await process_chat_message(msg, bot, db=db, llm=llm)
                self.assertIn(
                    "еженедельные настольные игры",
                    llm.generate.await_args.args[0][-1]["content"],
                )
                self.assertIn(
                    "Перескажи лор нейтрально",
                    llm.generate.await_args.kwargs["system_prompt"],
                )
                self.assertEqual(
                    send.await_args.args[3], "В группе обсуждают настольные игры"
                )

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
                    msg.text = "/режим сейф"
                    await cmd_reply_mode(msg, db)
                self.assertEqual(
                    await db.run(Repository.get_chat_reply_mode, chat.id), "safe"
                )
                self.assertIn("Режим этого чата: сейф", msg.reply.await_args.args[0])
                msg.text = "/режим рофл"
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
                        return_value="[Вымысел из режима рофла] [Жодик уже в космосе торгуется]"
                    )
                )
                await db.run(Repository.set_chat_reply_mode, -100, "rofl")
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[16]),
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(msg, bot, db=db, llm=llm)
                llm.generate.assert_awaited_once()
                self.assertEqual(
                    send.await_args.args[3], "Жодик уже в космосе торгуется"
                )
                self.assertIn(
                    ROFL_PROMPT, llm.generate.await_args.kwargs["system_prompt"]
                )
                saved = await db.run(Repository.get_message, -100, 16)
                self.assertEqual(
                    saved["content"],
                    "[Вымысел из режима рофла] Жодик уже в космосе торгуется",
                )

    def test_code_and_plain_prompts_keep_their_contracts(self):
        self.assertNotIn(ROFL_PROMPT, build_system_prompt(Mode.CODE, "код", rofl=True))
        self.assertNotIn(ROFL_PROMPT, build_system_prompt(Mode.CHAT, "привет"))
