import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType

from sanya.handlers.chat import handle_message
from sanya.intent import is_bot_name_call, strip_leading_bot_call
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class ChatAddressingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=99, username="Ponyeb228"))
        )
        self.db = SimpleNamespace(run=AsyncMock())
        self.requests = SimpleNamespace(
            enqueue=AsyncMock(), last_proactive={}, proactive_history={}
        )
        self.message = SimpleNamespace(
            from_user=SimpleNamespace(
                id=1, is_bot=False, username="user", first_name="User"
            ),
            chat=SimpleNamespace(id=2, type=ChatType.GROUP, title=None),
            message_id=3,
            reply_to_message=None,
            text=None,
            caption=None,
            photo=None,
            sticker=None,
        )

    async def test_old_names_stay_in_history_without_proactive_answer(self):
        with (
            patch("sanya.handlers.chat.random.random", return_value=0),
            patch(
                "sanya.handlers.chat.try_react_whale", new_callable=AsyncMock
            ) as react,
        ):
            for address in ("@PONYEB228", "Саня", "Санёк", "Пониёб"):
                with self.subTest(address=address):
                    self.db.run.reset_mock()
                    self.message.caption = f"{address}, глянь цены на Авито"
                    await handle_message(self.message, self.bot, self.db, self.requests)
                    self.assertTrue(
                        any(
                            call.args[0] == Repository.save_message
                            for call in self.db.run.await_args_list
                        )
                    )
            self.requests.enqueue.assert_not_awaited()
            react.assert_not_awaited()

    async def test_spermer_and_actual_reply_still_enqueue(self):
        with patch("sanya.handlers.chat.try_react_whale", new_callable=AsyncMock):
            self.message.text = "СПЕРМЕР, глянь цены"
            await handle_message(self.message, self.bot, self.db, self.requests)
            self.requests.enqueue.assert_awaited_once_with(
                self.message, is_proactive=False
            )
            self.requests.enqueue.reset_mock()
            self.message.text = "Саня, глянь цены"
            self.message.reply_to_message = SimpleNamespace(
                photo=None, from_user=SimpleNamespace(id=99)
            )
            await handle_message(self.message, self.bot, self.db, self.requests)
            self.requests.enqueue.assert_awaited_once_with(
                self.message, is_proactive=False
            )
        self.assertFalse(is_bot_name_call("спермерский @спермер"))

    async def test_worker_drops_ignored_proactive_message(self):
        self.message.text = "@ponyeb228, глянь цены на Авито"
        llm = SimpleNamespace(generate=AsyncMock())
        await process_chat_message(
            self.message, self.bot, db=self.db, llm=llm, is_proactive=True
        )
        llm.generate.assert_not_awaited()
        self.db.run.assert_not_awaited()

    async def test_exact_bot_tag_enqueues(self):
        self.bot.get_me.return_value.username = "ponyeb228_bot"
        with patch("sanya.handlers.chat.try_react_whale", new_callable=AsyncMock):
            self.message.text = "@PONYEB228_BOT_extra, ты тут?"
            await handle_message(self.message, self.bot, self.db, self.requests)
            self.requests.enqueue.assert_not_awaited()
            self.message.text = "@PONYEB228_BOT, ты тут?"
            await handle_message(self.message, self.bot, self.db, self.requests)
            self.requests.enqueue.assert_awaited_once_with(
                self.message, is_proactive=False
            )

    def test_strip_address_without_removing_names_from_the_question(self):
        self.assertEqual(
            strip_leading_bot_call("СПЕРМЕР, какое слово?"), "какое слово?"
        )
        self.assertEqual(
            strip_leading_bot_call("Кто такой Спермер?"), "Кто такой Спермер?"
        )
        self.assertEqual(strip_leading_bot_call("спермерский мем"), "спермерский мем")
