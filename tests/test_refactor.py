import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from sanya.database import Database
from sanya.handlers.memory import cmd_delchat, cmd_delfact, cmd_delkent
from sanya.intent import Mode, classify_intent, may_search
from sanya.llm import Ollama, isolate_thinking
from sanya.main import build_dispatcher
from sanya.memory import estimate_tokens, fit_prompt
from sanya.repository import Repository
from sanya.services.chat import process_chat_message
from sanya.services.render import (
    clean_response,
    needs_document,
    render_messages,
    valid_output,
)


def turn(text, role="user"):
    return {"role": role, "content": text}


class GenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_thinking_fallback_keeps_code_budget(self):
        requests = []

        def server(request):
            requests.append(json.loads(request.content))
            first = len(requests) == 1
            return httpx.Response(200, json={"message": {
                "thinking": "private secret",
                "content": "<think>not closed" if first else "<THINK>debug</THINK>```python\nprint('ok')\n```",
            }, "done_reason": "length" if first else "stop", "prompt_eval_count": 1500})

        async with httpx.AsyncClient(transport=httpx.MockTransport(server)) as client:
            with patch("sanya.llm.ENABLE_THINKING", True), self.assertLogs("sanya.thinking", "DEBUG") as logs:
                answer = await Ollama(client).generate([turn("напиши код")], is_coding_request=True, complete=True)
        self.assertEqual(answer, "```python\nprint('ok')\n```")
        self.assertIn("private secret", "\n".join(logs.output))
        self.assertEqual([p["think"] for p in requests], [True, False])
        self.assertEqual(requests[1]["options"]["num_predict"], 3500)
        self.assertEqual(requests[0]["options"]["num_ctx"], 16384)
        self.assertEqual(sum(m["role"] == "system" for m in requests[0]["messages"]), 1)

    def test_hidden_channels(self):
        cases = {
            "<think>hidden<think>nested</think>more</think>visible": "visible",
            "hidden</think>visible": "visible",
            "<think>never closed": "",
            "<THINK": "",
            "<|channel>thought\nsecret<channel|><|channel>final\nvisible<channel|>": "visible",
            "<|channel>thought\nsecret<channel|>": "",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(isolate_thinking(text)[0], expected)

    def test_render_preserves_code_and_suppresses_stray_blocks(self):
        code = '# do not rewrite\nif 4 != 2:\n    print("[стикер: 😁]!\\n")\n'
        fenced = f"Держи тут скрипт!\n```python\n{code}```"
        clean, _, sticker = clean_response(fenced, Mode.CODE)
        self.assertFalse(sticker)
        self.assertTrue(valid_output(clean, Mode.CODE))
        self.assertIn(code, clean)
        self.assertTrue(needs_document(f"```python\n{'x' * 4000}\n```"))
        self.assertTrue(valid_output("```prompt\npony, moon\n```\nNegative prompt: blur", Mode.PROMPT))
        chat, emoji, sticker = clean_response("держи вот братан!\nмысль теперь закончена. [стикер: 😱]\n```prompt\nsecret\n```", Mode.CHAT)
        self.assertEqual((chat, emoji, sticker), ("Держи вот братан\nМысль теперь закончена", "😱", True))
        self.assertEqual(len(render_messages(chat, detailed=False)), 2)
        self.assertFalse(valid_output("```python\nfor x in:\n```", Mode.CODE))

    def test_render_uses_emoji_only_as_sticker_hint(self):
        reply = "ну вот приехали 😁\nдавай покажи что случилось 1️⃣\n[стикер: 😵‍💫]"
        self.assertEqual(
            clean_response(reply, Mode.CHAT),
            ("Ну вот приехали\nДавай покажи что случилось", "😵‍💫", True),
        )
        self.assertEqual(clean_response("Проверь кабель 🔌", Mode.DETAIL)[0], "Проверь кабель")

    def test_render_keeps_short_replies_and_removes_internal_tags(self):
        reply = "[Вымысел из режима рофла] [жодик ушёл в астрал]\n[Справочные заметки: скрыть]\n[стикер: 😁]"
        self.assertEqual(clean_response(reply, Mode.CHAT), ("Жодик ушёл в астрал", "😁", True))
        self.assertEqual(clean_response("Да!", Mode.CHAT)[0], "Да")
        self.assertEqual(clean_response("[С этим матрасом всё понятно]", Mode.DETAIL)[0], "С этим матрасом всё понятно")
        self.assertEqual(clean_response("[Вымысел из режима рофла]\n[стикер: 😁]", Mode.CHAT)[0], "")

    def test_render_bounds_long_chat_replies_without_discarding_them(self):
        text = "Это обычная законченная реплика, в которой больше восьми слов и это нормально."
        self.assertEqual(clean_response(text, Mode.CHAT)[0], text[:-1])
        chunks = clean_response((text + " ") * 30, Mode.CHAT)[0].splitlines()
        self.assertEqual(len(chunks), 4)
        self.assertTrue(all(len(chunk) <= 220 for chunk in chunks))

    def test_intents_and_budget(self):
        for text, quote, mode in (
            ("напиши bash скрипт для поиска картинок", "", Mode.CODE),
            ("дай промпт для генерации картинки пони", "", Mode.PROMPT),
            ("допиши", "вот код", Mode.CODE),
            ("write a PowerShell script to find images", "", Mode.CODE),
            ("write a prompt for an image of a pony", "", Mode.PROMPT),
        ):
            self.assertIs(classify_intent(text, quote), mode)
            self.assertFalse(may_search(mode, enabled=True, insult=False))
        current = turn("настоящий запрос")
        fitted = fit_prompt(turn("системный", "system"), [turn("ж" * 14_000), turn("свежий"), current])
        self.assertEqual(fitted[1:], [turn("свежий"), current])
        self.assertLessEqual(estimate_tokens(fitted), 8500)

    def test_router_builds_twice(self):
        self.assertEqual(build_dispatcher().resolve_used_update_types(), ["callback_query", "channel_post", "message"])
        self.assertEqual(build_dispatcher().resolve_used_update_types(), ["callback_query", "channel_post", "message"])


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_pipeline_never_searches_for_code_or_prompts(self):
        with tempfile.TemporaryDirectory() as root, patch("sanya.services.chat.ENABLE_WEB_SEARCH", True):
            async with Database(Path(root) / "memory.db") as db:
                message = SimpleNamespace(
                    from_user=SimpleNamespace(id=91, username="reader", first_name="Reader"),
                    chat=SimpleNamespace(id=22, type="private"), reply_to_message=None,
                    photo=None, sticker=None, caption=None, message_id=40,
                )
                bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=100, username="sanya")),
                                      send_chat_action=AsyncMock())
                for query, answer in (
                    ("напиши скрипт для поиска картинок, как это сделать?", "```python\nprint('ok')\n```"),
                    ("дай промпт для картинки пони, какой лучше?", "```prompt\npony, moon\n```\nNegative prompt: blur"),
                ):
                    message.text = query
                    llm = SimpleNamespace(generate=AsyncMock(side_effect=["Хз не шарю", answer]))
                    with (
                        patch("sanya.services.chat.lookup_snippets", AsyncMock()) as search,
                        patch("sanya.services.chat.search_and_download_image", AsyncMock()) as image_search,
                        patch("sanya.services.chat.search_and_download_booru_image", AsyncMock()) as booru,
                        patch("sanya.services.chat.send_burst_messages", AsyncMock()) as send,
                    ):
                        await process_chat_message(message, bot, db=db, llm=llm)
                    search.assert_not_called()
                    image_search.assert_not_called()
                    booru.assert_not_called()
                    self.assertEqual(send.await_args.args[3], answer)
                    self.assertEqual(llm.generate.await_count, 2)
                    self.assertIn(query, llm.generate.await_args.args[0][-1]["content"])
                    system = llm.generate.await_args.kwargs["system_prompt"]
                    expected_format = "Negative prompt:" if "промпт" in query else "полный рабочий код"
                    self.assertIn(expected_format, system)
                    self.assertIn("Соблюди формат", system)
                    self.assertNotIn("Соблюди формат", llm.generate.await_args.args[0][-1]["content"])
                    message.message_id += 1

    async def test_legacy_migration_and_current_turn_filter(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "memory.db"
            with sqlite3.connect(path) as legacy:
                legacy.execute("CREATE TABLE user_profiles (user_id INTEGER PRIMARY KEY, user_name TEXT, first_name TEXT, first_seen TIMESTAMP, last_seen TIMESTAMP, message_count INTEGER, facts TEXT)")
                legacy.execute("INSERT INTO user_profiles VALUES (91,'legacy','Legacy',NULL,NULL,11,'preserve')")
            legacy.close()
            async with Database(path) as db:
                self.assertEqual((await db.run(Repository.get_user_profile, 91))["message_count"], 11)
                for mid, text in [(10, "x" * 30), (11, "старый"), (13, "будущий"), (12, "текущий")]:
                    await db.run(Repository.save_message, 1, mid, None, 91, "legacy", "user", text)
                history, trimmed = await db.run(Repository.get_recent_messages, 1, False, max_chars=12, before_message_id=12)
                self.assertTrue(trimmed)
                self.assertEqual(history, [turn("старый")])
                plan = await db.run(lambda repo: repo.connection.execute(
                    "EXPLAIN QUERY PLAN SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 50", (1,)
                ).fetchall())
                self.assertIn("idx_chat_id_id", str([tuple(r) for r in plan]))

    async def test_multi_delete_commands_and_no_reseed(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "memory.db"
            async with Database(path) as db:
                uid, chat_id = 1005211563, -1003672879438
                await db.run(Repository.add_kent_fact_entry, uid, "another")
                await db.run(Repository.add_chat_lore_entry, chat_id, "title", "another", uid)
                await db.run(Repository.record_auto_fact, "chat", chat_id, "first", "first")
                await db.run(Repository.record_auto_fact, "chat", chat_id, "second", "second")
                for command, name, entries in (
                    (cmd_delkent, "delkent", await db.run(Repository.get_kent_fact_entries, uid)),
                    (cmd_delchat, "delchat", await db.run(Repository.get_chat_lore_entries, chat_id)),
                    (cmd_delfact, "delfact", await db.run(Repository.get_auto_facts, "chat", chat_id)),
                ):
                    self.assertEqual(len(entries), 2)
                    ids = " ".join(str(row["id"]) for row in entries)
                    message = SimpleNamespace(text=f"/{name} {ids}", from_user=SimpleNamespace(id=uid),
                                              chat=SimpleNamespace(id=chat_id, title="title"), reply=AsyncMock())
                    with patch("sanya.handlers.memory.check_is_admin", AsyncMock(return_value=True)):
                        await command(message, db)
                    message.reply.assert_awaited_once()
            async with Database(path) as db:
                self.assertEqual(await db.run(Repository.get_kent_fact_entries, uid), [])
                self.assertEqual(await db.run(Repository.get_chat_lore_entries, chat_id), [])
                self.assertEqual(await db.run(Repository.get_auto_facts, "chat", chat_id), [])
