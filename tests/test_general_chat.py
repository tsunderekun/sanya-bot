import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.conversation_cues import starts_new_topic
from sanya.database import Database
from sanya.intent import Mode, classify_intent, is_targeted_insult
from sanya.prompts import (
    CHAT_SOCIAL,
    CHAT_TOPIC_GUIDANCE,
    TOPIC_GUIDANCE,
    build_system_prompt,
)
from sanya.repository import Repository
from sanya.schema import initialize
from sanya.services.chat import process_chat_message
from sanya.services.dialogue_notes import _collect
from sanya.services.render import CHAT_FAILURE_REPLIES
from sanya.services.sale_reply import grounded_sale_reply


class GeneralChatTests(unittest.TestCase):
    def test_topic_boundary_cues_do_not_include_short_followups(self):
        for text, expected in (
            ("Другой вопрос: как сварить рис?", True),
            ("Что сказал один лифт другому? — Поднимайся", True),
            ("После вечеринки мы задержались ещё на час, если понимаешь, о чём я", True),
            ("А если крышки нет?", False),
            ("А потом задержались ещё на час, если понимаешь, о чём я", False),
            ("После этого мы задержались ещё на час, если понимаешь, о чём я", False),
            ("Ну ты понял, о чём я", False),
        ):
            with self.subTest(text=text):
                self.assertEqual(starts_new_topic(text), expected)

    def test_failure_replies_are_short_self_ironic_bursts(self):
        self.assertGreaterEqual(len(CHAT_FAILURE_REPLIES), 6)
        self.assertEqual(len(set(CHAT_FAILURE_REPLIES)), len(CHAT_FAILURE_REPLIES))
        for reply in CHAT_FAILURE_REPLIES:
            lines = reply.splitlines()
            with self.subTest(reply=reply):
                self.assertTrue(1 <= len(lines) <= 3)
                self.assertTrue(all(line.strip() for line in lines))
                self.assertTrue(all(len(line) <= 40 for line in lines))
                self.assertNotIn("ты ", reply.casefold())
                self.assertNotIn("твой", reply.casefold())

    def test_games_and_banter_do_not_turn_into_hostility(self):
        for text in (
            "Спермер, как катка?",
            "Ты сам вчера слился, лошара",
            "Витя сказал «иди нахуй», и мы ржали",
            "Что ты думаешь про тачки?",
        ):
            with self.subTest(text=text):
                self.assertIs(classify_intent(text), Mode.CHAT)
                self.assertFalse(is_targeted_insult(text, addressed=True))
        self.assertFalse(is_targeted_insult("иди нахуй"))
        self.assertTrue(is_targeted_insult("Спермер, завали рот!", addressed=True))

    def test_general_chat_prompt_encourages_on_topic_banter(self):
        prompt = build_system_prompt(Mode.CHAT, "Спермер, как катка?")
        self.assertIn("Не своди разговор к железу", prompt)
        self.assertIn("Дружеский стёб подхвати", prompt)
        self.assertIn("панч по ситуации, не по человеку", prompt)
        self.assertIn("не склеивай мат и мемы", prompt)
        self.assertIn("Репетирует взлёт", prompt)

    def test_topic_and_uncertainty_guidance_is_limited_to_conversation(self):
        for mode in Mode:
            with self.subTest(mode=mode):
                prompt = build_system_prompt(mode, "А если вечером похолодает?")
                expected = (
                    (CHAT_TOPIC_GUIDANCE,)
                    if mode is Mode.CHAT
                    else (TOPIC_GUIDANCE,)
                    if mode is Mode.DETAIL
                    else ()
                )
                for guidance in (CHAT_TOPIC_GUIDANCE, TOPIC_GUIDANCE):
                    self.assertEqual(guidance in prompt, guidance in expected)
        self.assertNotIn(CHAT_TOPIC_GUIDANCE, build_system_prompt(Mode.CHAT, safe_voice="Без мата."))

    def test_detail_keeps_installed_social_and_topic_contract(self):
        detail = build_system_prompt(Mode.DETAIL, "А если вечером похолодает?")
        self.assertIn(TOPIC_GUIDANCE, detail)
        self.assertIn("По фото отделяй видимое от предположений", detail)
        self.assertNotIn(CHAT_SOCIAL, detail)
        self.assertNotIn(CHAT_TOPIC_GUIDANCE, detail)

    def test_dossier_count_excludes_other_chats_users_and_bot_replies(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            for msg, chat, user, role in (
                (1, -100, 42, "user"),
                (2, -101, 42, "user"),
                (3, -100, 43, "user"),
                (4, -100, 42, "assistant"),
            ):
                repo.save_message(chat, msg, None, user, "test", role, "test")
            self.assertEqual(repo.count_user_chat_messages(-100, 42), 1)

    def test_avito_alone_does_not_pull_unrelated_hardware_lore(self):
        repo = SimpleNamespace(
            get_chat_profile=lambda _: {
                "chat_lore": "На Авито восстановили RX 570. Seagate этой серии шумит.",
            },
            get_self_facts=lambda *_: [{"fact_text": "Видеокарта: RTX 3070"}],
            get_auto_facts=lambda *_: [{"fact_text": "Авито и прошивки"}],
        )
        notes, kent_question = _collect(
            repo, 42, -100, "Когда дохлые Seagate на Авито выставлять?", True
        )
        self.assertEqual(notes, ["Лор конфы: Seagate этой серии шумит."])
        self.assertFalse(kent_question)


class ChatContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_topic_boundary_excludes_old_turns_but_keeps_new_followup(self):
        joke = "Что сказал один лифт другому? — Поднимайся"
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "topic.db") as db:
                await db.run(
                    Repository.save_message, 1, 1, None, 42, "kent", "user",
                    "Как приготовить глазурь?",
                )
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
                    id=9, username="ponyeb228_bot",
                )))
                llm = SimpleNamespace(generate=AsyncMock(side_effect=[
                    "Вот это шутка с подъёмом", "Скажет: ну вот и приехали",
                ]))
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1, type="private"), message_id=2,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text=joke, caption=None, photo=None, sticker=None, reply_to_message=None,
                )
                with (
                    patch("sanya.services.chat.send_burst_messages", AsyncMock(side_effect=[[3], [5]])),
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                    message.message_id = 4
                    message.text = "А если он застрянет?"
                    await process_chat_message(message, bot, db=db, llm=llm)
                stored = await db.run(Repository.get_dialogue_rows, 1, 42, 6)
                self.assertEqual(stored[0]["content"], "Как приготовить глазурь?")
                first_call, second_call = llm.generate.await_args_list
                first, second = first_call.args[0], second_call.args[0]
                self.assertEqual(len(first), 1)
                self.assertIn(joke, first[0]["content"])
                self.assertIn("подхвати панчлайн", first_call.kwargs["system_prompt"])
                self.assertNotIn("подхвати панчлайн", second_call.kwargs["system_prompt"])
                self.assertEqual(second[0]["content"], joke)
                self.assertEqual(second[1]["content"], "Вот это шутка с подъёмом")
                self.assertIn("А если он застрянет?", second[-1]["content"])
                self.assertNotIn("глазурь", str(second))

    def test_live_printer_reply_is_flagged_and_trimmed(self):
        from sanya.services.chat import drop_unrequested_advice, unrequested_advice

        text = (
            "Твои принтеры — это перфекционисты в области белых пятен\n"
            "Проверь уровень чернил и запусти цикл очистки печатающей головки если у тебя струйник "
            "иначе сбрось настройки драйвера полностью попробуй переустановить его вот так чаще всего "
            "помогает от этих причудливых фиаско"
        )
        self.assertTrue(
            unrequested_advice(text, "Принтер опять три пустых листа выдал")
        )
        self.assertEqual(
            drop_unrequested_advice(text),
            "Твои принтеры — это перфекционисты в области белых пятен",
        )
        self.assertFalse(
            unrequested_advice(text, "Принтер печатает пустые листы, что делать?")
        )

    async def test_joke_does_not_get_unrequested_diagnostics(self):
        joke = "Твои принтеры — это перфекционисты в области белых пятен"
        advice = (
            "Проверь уровень чернил и запусти цикл очистки печатающей головки. "
            "Если не поможет, переустанови драйвер полностью и сбрось настройки печати."
        )
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "joke.db") as db:
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
                    id=9, username="ponyeb228_bot",
                )))
                llm = SimpleNamespace(generate=AsyncMock(side_effect=[
                    joke + "\n" + advice,
                    joke,
                ]))
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1, type="private"), message_id=2,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Принтер опять три пустых листа выдал",
                    caption=None, photo=None, sticker=None, reply_to_message=None,
                )
                with (
                    patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[3])) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.lookup_snippets", AsyncMock(return_value="")),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                self.assertEqual(llm.generate.await_count, 2)
                self.assertEqual(send.await_args.args[3], joke)

    async def test_joke_advice_is_trimmed_when_retry_repeats_it(self):
        joke = "Твои принтеры — это перфекционисты в области белых пятен"
        advice = (
            "Проверь уровень чернил и запусти цикл очистки печатающей головки. "
            "Если не поможет, переустанови драйвер полностью и сбрось настройки печати."
        )
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "joke.db") as db:
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
                    id=9, username="ponyeb228_bot",
                )))
                llm = SimpleNamespace(generate=AsyncMock(side_effect=[
                    joke + "\n" + advice,
                    joke + "\n" + advice,
                ]))
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1, type="private"), message_id=2,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Принтер опять три пустых листа выдал",
                    caption=None, photo=None, sticker=None, reply_to_message=None,
                )
                with (
                    patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[3])) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.lookup_snippets", AsyncMock(return_value="")),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                self.assertEqual(llm.generate.await_count, 2)
                self.assertEqual(send.await_args.args[3], joke)

    async def test_explicit_repair_request_keeps_diagnostics(self):
        advice = (
            "Проверь уровень чернил и запусти цикл очистки печатающей головки. "
            "Если не поможет, переустанови драйвер и сбрось настройки печати."
        )
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "joke.db") as db:
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
                    id=9, username="ponyeb228_bot",
                )))
                llm = SimpleNamespace(generate=AsyncMock(return_value=advice))
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1, type="private"), message_id=2,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Принтер печатает пустые листы, что делать?",
                    caption=None, photo=None, sticker=None, reply_to_message=None,
                )
                with (
                    patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[3])) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch("sanya.services.chat.lookup_snippets", AsyncMock(return_value="")),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                self.assertEqual(llm.generate.await_count, 1)
                self.assertIn("Проверь уровень чернил", send.await_args.args[3])

    async def test_topic_deflection_retries_once_without_old_history(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "topic.db") as db:
                await db.run(
                    Repository.save_message, 1, 1, None, 42, "kent", "user",
                    "Стоит ли менять RTX 3070?",
                )
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=1, type="private"), message_id=2,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Как сварить рис рассыпчатым?", caption=None,
                    photo=None, sticker=None, reply_to_message=None,
                )
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(
                    id=9, username="ponyeb228_bot",
                )))
                off_topic = "У меня башка только на железо настроена"
                on_topic = "Промой рис и вари на слабом огне под крышкой"
                for first_reply, retry, expected in (
                    (off_topic, on_topic, on_topic),
                    (
                        "Слушай ну ты меня достал уже своими кулинарными изысканиями "
                        "а я тут про железо и графику\nТочных пропорций не знаю",
                        on_topic,
                        on_topic,
                    ),
                    ("Я про железо спросил", off_topic, None),
                ):
                    with self.subTest(retry=retry):
                        llm = SimpleNamespace(generate=AsyncMock(side_effect=[first_reply, retry]))
                        with (
                            patch("sanya.services.chat.send_burst_messages", AsyncMock(return_value=[])) as send,
                            patch("sanya.services.chat.keep_typing", AsyncMock()),
                            patch("sanya.services.chat.lookup_snippets", AsyncMock()) as search,
                        ):
                            await process_chat_message(message, bot, db=db, llm=llm)
                        first, second = llm.generate.await_args_list
                        self.assertIn("RTX 3070", first.args[0][0]["content"])
                        self.assertEqual(second.args[0], first.args[0][-1:])
                        if expected is None:
                            self.assertIn(send.await_args.args[3], CHAT_FAILURE_REPLIES)
                        else:
                            self.assertEqual(send.await_args.args[3], expected)
                        search.assert_not_awaited()
                        message.message_id += 1

    async def test_auto_stickers_roll_once_per_chat_answer_without_model_hint(self):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "stickers.db") as db:
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=-100, type="supergroup"),
                    message_id=1,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Спермер, как катка?",
                    caption=None,
                    photo=None,
                    sticker=None,
                    reply_to_message=None,
                )
                bot = SimpleNamespace(
                    get_me=AsyncMock(
                        return_value=SimpleNamespace(id=9, username="ponyeb228_bot")
                    )
                )
                text = "Первый раунд бодро пошёл\nА дальше сами себе враги"
                llm = SimpleNamespace(generate=AsyncMock())
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages", AsyncMock()
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                    patch(
                        "sanya.services.chat.send_sanya_sticker", AsyncMock()
                    ) as sticker,
                    patch(
                        "sanya.services.chat.automatic_sticker", return_value=True
                    ) as roll,
                ):
                    for level, hint, delivered, expected_roll, expected_emoji in (
                        (3, "", True, True, None),
                        (3, "\n[стикер: 😁]", True, True, "😁"),
                        (None, "", True, False, None),
                        (None, "\n[стикер: 😁]", True, True, "😁"),
                        (3, "", False, False, None),
                    ):
                        with self.subTest(level=level, hint=hint, delivered=delivered):
                            await db.run(Repository.set_chat_sticker_level, -100, level)
                            llm.generate.return_value = text + hint
                            send.return_value = (
                                [message.message_id + 1, message.message_id + 2]
                                if delivered
                                else []
                            )
                            roll.reset_mock()
                            sticker.reset_mock()
                            await process_chat_message(message, bot, db=db, llm=llm)
                            self.assertEqual(roll.call_count, int(expected_roll))
                            self.assertEqual(sticker.await_count, int(expected_roll))
                            if expected_roll:
                                self.assertEqual(
                                    sticker.await_args.kwargs["emoji"], expected_emoji
                                )
                            message.message_id += 3

    async def test_reply_to_later_burst_line_gets_original_question_not_group_noise(
        self,
    ):
        with tempfile.TemporaryDirectory() as root:
            async with Database(Path(root) / "context.db") as db:
                await db.run(
                    Repository.save_message,
                    -100,
                    1,
                    None,
                    42,
                    "kent",
                    "user",
                    "Когда Seagate на Авито выставлять?",
                )
                await db.run(
                    Repository.save_chat_answer,
                    -100,
                    [2, 3],
                    1,
                    9,
                    "Не понял вопрос\nПошли про тачки",
                )
                await db.run(
                    Repository.save_message,
                    -100,
                    4,
                    None,
                    43,
                    "someone",
                    "user",
                    "Посторонний разговор про RX 570",
                )
                await db.run(
                    Repository.save_message,
                    -101,
                    1,
                    None,
                    42,
                    "kent",
                    "user",
                    "Текст из другого чата",
                )
                self.assertIsNone(await db.run(Repository.get_answer_question, -101, 3))
                prior, _ = await db.run(
                    Repository.get_recent_messages, -100, before_message_id=4
                )
                self.assertEqual(sum(turn["role"] == "assistant" for turn in prior), 1)
                message = SimpleNamespace(
                    chat=SimpleNamespace(id=-100, type="supergroup"),
                    message_id=5,
                    from_user=SimpleNamespace(id=42, username="kent"),
                    text="Ты не попал в тему",
                    caption=None,
                    photo=None,
                    sticker=None,
                    reply_to_message=SimpleNamespace(
                        from_user=SimpleNamespace(id=9, username="ponyeb228_bot"),
                        message_id=3,
                        text="Пошли про тачки",
                        photo=None,
                        sticker=None,
                    ),
                )
                bot = SimpleNamespace(
                    get_me=AsyncMock(return_value=message.reply_to_message.from_user)
                )
                llm = SimpleNamespace(
                    generate=AsyncMock(
                        return_value="Диски выставляй только на запчасти\nДефект укажи сразу в описании"
                    )
                )
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[6]),
                    ),
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                turns = llm.generate.await_args.args[0]
                self.assertEqual(len(turns), 1)
                self.assertIn("Исходный вопрос: Когда Seagate", turns[0]["content"])
                self.assertTrue(
                    turns[0]["content"].endswith("[kent]: Ты не попал в тему")
                )
                self.assertNotIn("RX 570", turns[0]["content"])
                self.assertNotIn("Текст из другого чата", turns[0]["content"])
                self.assertEqual(
                    (await db.run(Repository.get_answer_question, -100, 6))["content"],
                    message.text,
                )

                message.message_id = 7
                message.reply_to_message = None
                message.text = "Спермер, когда это за 4к на Авито выставлять?"
                llm.generate.reset_mock()
                with (
                    patch(
                        "sanya.services.chat.send_burst_messages",
                        AsyncMock(return_value=[8]),
                    ) as send,
                    patch("sanya.services.chat.keep_typing", AsyncMock()),
                ):
                    await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertEqual(send.await_args.args[3], "А что именно продаёшь?")
                self.assertEqual(
                    (await db.run(Repository.get_answer_question, -100, 8))["content"],
                    message.text,
                )

                message.message_id = 9
                message.text = "Спермер, когда сигейты дохлые на Авито сольешь?"
                with patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[10, 11, 12]),
                ) as send:
                    await process_chat_message(message, bot, db=db, llm=llm)
                disk_answer = send.await_args.args[3]
                self.assertIn("Причину не знаешь", disk_answer)
                bot_user = await bot.get_me()
                message.message_id = 13
                message.text = "Ты не попал в тему"
                message.reply_to_message = SimpleNamespace(
                    from_user=bot_user,
                    message_id=12,
                    text="Старая реплика не по теме",
                    photo=None,
                    sticker=None,
                )
                with patch(
                    "sanya.services.chat.send_burst_messages",
                    AsyncMock(return_value=[14]),
                ) as send:
                    await process_chat_message(message, bot, db=db, llm=llm)
                llm.generate.assert_not_awaited()
                self.assertEqual(send.await_args.args[3], disk_answer)

    def test_sale_replies_match_only_supported_questions(self):
        unknown = "спермер когда хуйню за 4к кебекам сливать на авито?"
        disk = "когда сигейты дохлые на авито сольешь"
        self.assertEqual(grounded_sale_reply(unknown), "А что именно продаёшь?")
        self.assertEqual(grounded_sale_reply(unknown, has_context=True), "")
        self.assertIn("как нерабочие", grounded_sale_reply(disk))
        self.assertIn("1 ТБ тут просто пример", grounded_sale_reply(disk))
        known = grounded_sale_reply("Когда дохлый Seagate на 4 ТБ выставишь на Авито?")
        self.assertIn("как нерабочие", known)
        self.assertNotIn("1 ТБ", known)
        self.assertEqual(
            grounded_sale_reply("ты не попал в тему", original=disk, has_context=True),
            grounded_sale_reply(disk),
        )
        for other in (
            "Когда рабочий Seagate на Авито выставлять?",
            "Как проверить дохлый Seagate перед продажей на Авито?",
            "Когда на Авито продашь рабочую RX 570, а дохлый HDD оставишь?",
            "Ты не попал в тему",
        ):
            with self.subTest(other=other):
                self.assertEqual(grounded_sale_reply(other), "")
