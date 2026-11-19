import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from sanya.database import Database
from sanya.llm import Ollama
from sanya.memory import compact_dialogue, fit_prompt
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class DialogueContextTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory())
        self.db = await self.enterAsyncContext(Database(Path(temp) / "context.sqlite"))

    async def user_turn(self, chat, mid, author, content, reply=None):
        await self.db.run(
            Repository.save_message,
            chat,
            mid,
            reply,
            author,
            f"user{author}",
            "user",
            content,
        )

    async def answer(self, chat, ids, question, content):
        await self.db.run(Repository.save_chat_answer, chat, ids, question, 99, content)

    async def test_interleaved_users_and_chats_are_isolated_in_model_prompt(self):
        await self.user_turn(-100, 1, 42, "Мой разговор про акварель")
        await self.user_turn(-100, 2, 43, "Чужая тема про турнир")
        await self.answer(-100, [3], 1, "Совет про акварель")
        await self.answer(-100, [4], 2, "Совет про турнир")
        await self.user_turn(-101, 1, 42, "Другая конфа про трамваи")
        await self.user_turn(42, 1, 42, "Личка про велосипед")
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            message_id=5,
            from_user=SimpleNamespace(id=42, username="test"),
            text="Спермер, как продолжить?",
            caption=None,
            photo=None,
            sticker=None,
            reply_to_message=None,
        )
        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(id=99, username="ponyeb228_bot")
            )
        )
        llm = SimpleNamespace(
            generate=AsyncMock(return_value="Давай продолжим с наброска")
        )
        with (
            patch("sanya.services.chat.keep_typing", AsyncMock()),
            patch(
                "sanya.services.chat.send_burst_messages", AsyncMock(return_value=[6])
            ),
        ):
            await process_chat_message(message, bot, db=self.db, llm=llm)
        contents = "\n".join(
            turn["content"] for turn in llm.generate.await_args.args[0]
        )
        self.assertIn("Мой разговор про акварель", contents)
        self.assertIn("Совет про акварель", contents)
        for unrelated in ("турнир", "трамваи", "велосипед"):
            self.assertNotIn(unrelated, contents)

    async def test_legacy_answer_does_not_enter_personal_timeline(self):
        await self.user_turn(-100, 1, 42, "Старый вопрос")
        await self.db.run(
            Repository.save_message,
            -100,
            2,
            1,
            99,
            "Бот",
            "assistant",
            "Старый смешанный ответ",
        )
        rows = await self.db.run(Repository.get_dialogue_rows, -100, 42, 3)
        self.assertEqual([row["content"] for row in rows], ["Старый вопрос"])

    async def test_joining_only_loads_ancestors_and_never_merges_timelines(self):
        await self.user_turn(-100, 1, 42, "Отдельный разговор первого")
        await self.user_turn(-100, 2, 43, "Отдельный разговор второго")
        await self.user_turn(-100, 3, 42, "Исходный вопрос")
        await self.answer(-100, [4, 5], 3, "Разбитый ответ")
        await self.user_turn(-100, 6, 43, "Поправка второго", reply=5)
        await self.answer(-100, [7], 6, "Уточнение Сани")
        await self.user_turn(-100, 8, 42, "Соседняя ветка", reply=4)
        await self.answer(-100, [9], 8, "Ответ в соседней ветке")

        async def texts(author, **kwargs):
            rows = await self.db.run(
                Repository.get_dialogue_rows, -100, author, 10, **kwargs
            )
            return [row["content"] for row in rows]

        self.assertEqual(
            await texts(44, reply_to_id=7),
            ["Исходный вопрос", "Разбитый ответ", "Поправка второго", "Уточнение Сани"],
        )
        self.assertEqual(await texts(43), ["Отдельный разговор второго"])
        await self.db.run(Repository.clear_user_data, 42)
        self.assertEqual(await texts(44, reply_to_id=7), [])
        await self.db.run(Repository.clear_history, -100)
        self.assertEqual(await texts(43), [])

    async def test_compaction_keeps_recent_text_and_uses_only_bounded_history(self):
        def turn(text):
            return {"role": "user", "content": text}

        history = [turn(f"Реплика {i} " + "т" * 580) for i in range(24)]
        history[10] = turn("Важная поправка: дождевик жёлтый, а не синий. " + "т" * 525)
        system = {"role": "system", "content": "с" * 900}
        current = turn("Куда дальше идём?")
        summarize = AsyncMock(return_value="По поправке дождевик жёлтый, а не синий.")
        with patch("sanya.memory.MAX_CONTEXT_CHARS", 14000):
            self.assertEqual(
                await compact_dialogue(history[-3:], system, current, summarize),
                history[-3:],
            )
            summarize.assert_not_awaited()
            result = await compact_dialogue(history, system, current, summarize)
            summarize.assert_awaited_once()
            self.assertTrue(
                any(
                    "Важная поправка" in x["content"]
                    for x in summarize.await_args.args[0]
                )
            )
            self.assertIn("дождевик жёлтый", result[0]["content"])
            self.assertEqual(result[-3:], history[-3:])
            self.assertEqual(
                fit_prompt(system, [*result, current]), [system, *result, current]
            )
            summarize.return_value = ""
            fallback = await compact_dialogue(history, system, current, summarize)
            self.assertEqual(fallback, fit_prompt(system, [*history, current])[1:-1])

    async def test_summary_backend_rejects_malformed_and_truncated_output(self):
        async def respond(request):
            payload = json.loads(request.content)
            self.assertFalse(payload["think"])
            self.assertEqual(payload["format"], "json")
            content, reason = next(outputs)
            return httpx.Response(
                200, json={"message": {"content": content}, "done_reason": reason}
            )

        outputs = iter(
            [
                ('{"summary":"Уточнили цвет дождевика"}', "stop"),
                ('{"summary":"Оборванный ответ"}', "length"),
                ("не JSON", "stop"),
            ]
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            llm = Ollama(client)
            turns = [{"role": "user", "content": "Цвет жёлтый"}]
            self.assertEqual(
                await llm.summarize_dialogue(turns), "Уточнили цвет дождевика"
            )
            self.assertEqual(await llm.summarize_dialogue(turns), "")
            self.assertEqual(await llm.summarize_dialogue(turns), "")
