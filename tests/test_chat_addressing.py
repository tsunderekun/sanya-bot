import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType

from sanya.handlers.chat import handle_message
from sanya.intent import is_bot_name_call, strip_leading_bot_call
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.queue import EnqueueResult
from sanya.services.stickers import addressed_reaction


class ChatAddressingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=99, username="Ponyeb228"))
        )
        self.db = SimpleNamespace(run=AsyncMock())
        self.requests = SimpleNamespace(
            enqueue=AsyncMock(return_value=EnqueueResult.ACCEPTED),
            last_proactive={},
            proactive_history={},
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
        self.db.run.assert_awaited_once_with(
            Repository.is_kent_blocked, self.message.chat.id, self.message.from_user.id
        )

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

    async def test_addressed_reactions_respect_cooldown_and_keep_chat_request(self):
        enqueue_counts = []

        async def reject_reaction(reactions):
            enqueue_counts.append(self.requests.enqueue.await_count)
            raise RuntimeError("Reaction unavailable")

        self.message.react = AsyncMock(side_effect=reject_reaction)
        self.message.reply = AsyncMock()
        for text, private, result, safe, expected in (
            ("Спермер, что сказал один лифт другому? — Поднимайся", False, EnqueueResult.ACCEPTED, False, "🗿"),
            ("Мы задержались, если понимаешь, о чём я", True, EnqueueResult.ACCEPTED, False, "🌚"),
            ("Спермер, если понимаешь, о чём я", False, EnqueueResult.COOLDOWN, False, None),
            ("Спермер, если понимаешь, о чём я", False, EnqueueResult.ACCEPTED, True, None),
            ("Друг, если понимаешь, о чём я", False, EnqueueResult.ACCEPTED, False, None),
            ("Спермер, кит задержался, если понимаешь, о чём я", False, EnqueueResult.ACCEPTED, False, "🐳"),
        ):
            with self.subTest(text=text, result=result, safe=safe):
                self.requests.enqueue.reset_mock()
                enqueue_counts.clear()
                self.requests.enqueue.return_value = result
                self.message.react.reset_mock()
                self.message.reply.reset_mock()
                self.message.chat.type = ChatType.PRIVATE if private else ChatType.GROUP
                self.message.text = text
                with (
                    patch("sanya.handlers.chat.safe_mode", AsyncMock(return_value=safe)),
                    patch("sanya.services.stickers.WHALE_REACTION_CHANCE", 0),
                ):
                    await handle_message(self.message, self.bot, self.db, self.requests)
                if text.startswith("Друг"):
                    self.requests.enqueue.assert_not_awaited()
                else:
                    self.requests.enqueue.assert_awaited_once_with(self.message, is_proactive=False)
                if expected is None:
                    self.message.react.assert_not_awaited()
                else:
                    self.assertEqual(self.message.react.await_count, 1)
                    self.assertEqual(enqueue_counts, [0 if expected == "🐳" else 1])
                    self.assertEqual(
                        [item.emoji for item in self.message.react.await_args.args[0]],
                        [expected],
                    )
                if result is EnqueueResult.COOLDOWN:
                    self.message.reply.assert_awaited_once_with(result.rejection_message())
                else:
                    self.message.reply.assert_not_awaited()

    def test_reaction_requires_a_complete_cue(self):
        for text in (
            "Что сказал один лифт другому?",
            "Ну ты понял, что денег не хватит?",
            "Вот тупая шутка дня",
            "/s если понимаешь, о чём я",
            "Просто 🗿 и 🌚",
        ):
            with self.subTest(text=text):
                self.assertIsNone(addressed_reaction(text))
        self.assertEqual(
            addressed_reaction("Тупая шутка дня: лифт уехал, если понимаешь, о чём я"),
            "🌚",
        )

    def test_strip_address_without_removing_names_from_the_question(self):
        self.assertEqual(
            strip_leading_bot_call("СПЕРМЕР, какое слово?"), "какое слово?"
        )
        self.assertEqual(
            strip_leading_bot_call("Кто такой Спермер?"), "Кто такой Спермер?"
        )
        self.assertEqual(strip_leading_bot_call("спермерский мем"), "спермерский мем")
