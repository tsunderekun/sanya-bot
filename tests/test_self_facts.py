import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram.enums import ChatType, MessageEntityType

from sanya.database import Database
from sanya.handlers.chat import handle_message
from sanya.observations import extract_self_facts, message_self_facts
from sanya.repository import Repository
from sanya.services.dialogue_notes import _collect


class SelfFactParserTests(unittest.TestCase):
    def test_explicit_statements(self):
        samples = {
            "У меня RTX 3070 + Ryzen 5 5600": {"hardware_gpu", "hardware_cpu"},
            "У меня стоит B550": {"hardware_platform"},
            "Купила себе Galaxy S23 Ultra!": {"device"},
            "Поставил себе Windows 11": {"os"},
            "Играю в CS 1.6.": {"game"},
            "Люблю кофе": {"preference:кофе"},
            "Я ненавижу пиццу с ананасами!": {"preference:ананасы в пицце"},
            "Предпочитаю Debian": {"preference:debian"},
            "Мне нравятся документалки": {"preference:документалки"},
            "Мне не нравится джаз": {"preference:джаз"},
            "Коллекционирую марки": {"activity:коллекционирую:марки"},
            "Играю в бадминтон": {"activity:играю в:бадминтон"},
            "Часто читаю фантастику": {"activity:читаю:фантастику"},
            "Умею чинить велосипеды": {"activity:умею:чинить велосипеды"},
        }
        for text, expected in samples.items():
            with self.subTest(text=text):
                self.assertEqual(set(dict(extract_self_facts(text))), expected)

    def test_rejects_ambiguous_or_unrelated_text(self):
        for text in (
            "У друга RTX 3070",
            "У @another стоит RTX 3070",
            "У меня будет RTX 3070",
            "У меня не RTX 3070",
            "Хочу купить RTX 3070",
            "У меня RTX 3070 или RTX 3080",
            "У меня RTX 3070?",
            "У меня RTX 3070, а у друга RX 570",
            "«У меня RTX 3070»",
            "/setkent у меня RTX 3070",
            "У меня RTX 3070\nу друга RX 570",
            "Я работаю в компании",
            "Люблю кофе?",
            "Люблю кофе, а вот друг — нет",
            "Он любит кофе",
            "Не знаю, люблю ли я кофе",
            "Ненавижу @somebody",
            "Люблю кофе у себя на улице X",
            "Люблю музыку, но не джаз",
            "Увлекаюсь джазом?",
            "Мне нравится Анна Примерова",
            "Коллекционирую номера банковских карт",
            "Мне нравится интимная переписка",
            "Смотрю договоры под NDA",
            "Слушаю коллегу",
            "Занимаюсь своим лечением",
        ):
            with self.subTest(text=text):
                self.assertEqual(extract_self_facts(text), ())

    def test_ignores_telegram_quotes_and_forwards(self):
        for metadata in (
            {"forward_origin": object()},
            {"sender_chat": object()},
            {"entities": [SimpleNamespace(type=MessageEntityType.BLOCKQUOTE)]},
        ):
            self.assertEqual(
                message_self_facts(SimpleNamespace(**metadata), "У меня RTX 3070"), ()
            )


class SelfFactIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_member_is_learned_quietly_and_only_in_the_group(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        async with Database(root / "memory.db") as db:
            user_id, chat_id = 42, -1001
            message = SimpleNamespace(
                from_user=SimpleNamespace(
                    id=user_id, is_bot=False, username="new_member", first_name="Кент"
                ),
                chat=SimpleNamespace(
                    id=chat_id, type=ChatType.SUPERGROUP, title="Тест"
                ),
                message_id=1,
                reply_to_message=None,
                text="У меня RTX 3070",
                caption=None,
                photo=None,
                sticker=None,
            )
            bot = SimpleNamespace(
                get_me=AsyncMock(
                    return_value=SimpleNamespace(id=99, username="test_bot")
                )
            )
            requests = SimpleNamespace(enqueue=AsyncMock())
            with patch("sanya.handlers.chat.try_react_whale", AsyncMock()) as react:
                await handle_message(message, bot, db, requests)
                message.text, message.message_id = "Ненавижу пиццу с ананасами", 2
                await handle_message(message, bot, db, requests)
                message.text, message.message_id = "Коллекционирую марки", 3
                message.reply_to_message = SimpleNamespace(
                    message_id=99, from_user=SimpleNamespace(id=99), photo=None
                )
                await handle_message(message, bot, db, requests)
                react.assert_not_awaited()
                requests.enqueue.assert_not_awaited()
                message.chat = SimpleNamespace(id=user_id, type=ChatType.PRIVATE)
                message.reply_to_message = None
                message.text = "У меня RTX 4080"
                await handle_message(message, bot, db, requests)
                requests.enqueue.assert_awaited_once()

            await db.run(
                Repository.record_auto_fact,
                "user",
                user_id,
                "hardware_gpu",
                "Старый автофакт",
            )
            profile = await db.run(Repository.get_user_profile, user_id)
            self.assertEqual(profile["user_name"], "new_member")
            self.assertFalse(profile["persona_notes"])
            self.assertEqual(
                await db.run(Repository.get_kent_fact_entries, user_id), []
            )
            facts = await db.run(Repository.get_self_facts, chat_id, user_id)
            self.assertCountEqual(
                [(f["fact_text"], f["mention_count"]) for f in facts],
                [
                    ("Видеокарта: RTX 3070", 1),
                    ("Ненавидит: ананасы в пицце", 1),
                    ("Коллекционирует: марки", 1),
                ],
            )
            self.assertEqual(
                await db.run(Repository.get_self_facts, user_id, user_id), []
            )
            group_notes, _ = await db.run(_collect, user_id, chat_id, "", True)
            other_notes, _ = await db.run(_collect, user_id, -1002, "", True)
            self.assertIn("RTX 3070", " ".join(group_notes))
            self.assertNotIn("Старый автофакт", " ".join(group_notes))
            self.assertNotIn("RTX 3070", " ".join(other_notes))

    async def test_preference_updates_and_forget_keeps_manual_lore(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        async with Database(root / "memory.db") as db:
            for text in (
                "Мне нравится джаз",
                "Терпеть не могу джаз",
                "Люблю кофе",
                "Не люблю кофе",
            ):
                await db.run(
                    Repository.record_self_facts, -1001, 42, extract_self_facts(text)
                )
            await db.run(Repository.add_kent_fact_entry, 42, "Играет в шахматы")
            notes, grounded = await db.run(_collect, 42, -1001, "Что я люблю?", True)
            self.assertTrue(grounded)
            self.assertIn("Не любит: кофе", " ".join(notes))
            self.assertIn("Терпеть не может: джаз", " ".join(notes))
            self.assertNotIn("Любит: джаз", " ".join(notes))
            for chat, user in ((-1002, 42), (-1001, 43), (42, 42)):
                notes, _ = await db.run(_collect, user, chat, "Что я люблю?", True)
                self.assertEqual(notes, [])
            await db.run(Repository.clear_user_data, 42)
            self.assertEqual(await db.run(Repository.get_self_facts, -1001, 42), [])
            self.assertEqual(
                (await db.run(Repository.get_kent_fact_entries, 42))[0]["content"],
                "Играет в шахматы",
            )
