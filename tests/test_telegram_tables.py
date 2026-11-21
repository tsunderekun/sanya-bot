import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import types
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from sanya.intent import Mode
from sanya.services.render import clean_response, render_messages, render_table_reply
from sanya.services.telegram import send_burst_messages

TABLE = (
    "Сравнение накопителей\n\n"
    "| Тип | Применение |\n"
    "| :--- | ---: |\n"
    "| HDD | Архив |\n"
    "| SATA SSD | Офис |\n"
    "| NVMe | Игры / Работа |\n"
    "| Внешний SSD | Резервные копии |\n\n"
    "Выбирай по бюджету"
)


class TableTests(unittest.IsolatedAsyncioTestCase):
    def test_formats_markdown_without_tables_and_escapes_html(self):
        text = "**Плюсы:**\n* `x < 5` & *быстро*\n***\n1. **Итог** <tag>"
        expected = ["<b>Плюсы:</b>", "• <code>x &lt; 5</code> &amp; <i>быстро</i>", "1. <b>Итог</b> &lt;tag&gt;"]
        self.assertEqual(render_messages(text, detailed=False), expected)
        self.assertEqual(render_messages(text, detailed=True), ["\n".join(expected)])
        self.assertEqual(render_messages("***\n\n**Итог**", detailed=True), ["<b>Итог</b>"])

    def test_chat_cleanup_preserves_all_rows_and_surrounding_text(self):
        cleaned, _, _ = clean_response(TABLE, Mode.CHAT)
        self.assertEqual(cleaned, TABLE)

    def test_fenced_code_is_not_a_table(self):
        self.assertIsNone(render_table_reply(f"```text\n{TABLE}\n```"))

    def test_formats_markdown_in_cells_and_recommendations_without_touching_code(self):
        rich, fallback = render_table_reply(
            "| Тип | Характеристика |\n| --- | --- |\n"
            "| **Скорость** | *Быстро* |\n\n***\nРекомендации:\n"
            "* **Что брать:** SSD\n* **Пример:** `2 * 3` или 2 * 4 * 5"
        )
        self.assertIn("<td><b>Скорость</b></td><td><i>Быстро</i></td>", rich)
        self.assertIn("<hr/><p>Рекомендации:</p><ul>", rich)
        self.assertIn("<li><b>Что брать:</b> SSD</li>", rich)
        self.assertIn("<code>2 * 3</code> или 2 * 4 * 5", rich)
        self.assertIn("• Что брать: SSD", fallback)
        self.assertNotIn("**", fallback)

    def test_rich_html_escapes_generated_markup_and_preserves_escaped_pipe(self):
        rich, fallback = render_table_reply(
            "Пояснение <img src='x'>\n\n| A | B |\n| --- | --- |\n"
            r"| B\|C | 2 < 3 & 3 > 2 |"
        )
        self.assertNotIn("<img", rich)
        self.assertIn("<td>B|C</td><td>2 &lt; 3 &amp; 3 &gt; 2</td>", rich)
        self.assertIn("• A: B|C; B: 2 < 3 & 3 > 2", fallback)

    async def test_sends_one_rich_reply(self):
        sent = SimpleNamespace(message_id=10)
        bot = SimpleNamespace(
            send_rich_message=AsyncMock(return_value=sent), send_message=AsyncMock()
        )
        with patch.object(types, "InputRichMessage", SimpleNamespace, create=True):
            self.assertEqual(await send_burst_messages(bot, 1, 2, TABLE), [10])
        bot.send_message.assert_not_awaited()
        _, payload = bot.send_rich_message.await_args.args
        self.assertTrue(payload.skip_entity_detection)
        self.assertIn("<td>Внешний SSD</td>", payload.html)
        self.assertEqual(
            bot.send_rich_message.await_args.kwargs["reply_parameters"].message_id, 2
        )

    async def test_compact_reply_formats_all_paragraphs_in_one_message(self):
        send = AsyncMock(return_value=SimpleNamespace(message_id=10))
        text = "**Игры:** 1440p\n\n**Ограничения:** 8 ГБ\n\n**Работа:** Blender & <монтаж>"
        ids = await send_burst_messages(SimpleNamespace(send_message=send), 1, 2, text, compact=True)
        self.assertEqual(ids, [10])
        send.assert_awaited_once()
        self.assertEqual(
            send.await_args.args[1],
            "<b>Игры:</b> 1440p\n\n<b>Ограничения:</b> 8 ГБ\n\n<b>Работа:</b> Blender &amp; &lt;монтаж&gt;",
        )
        self.assertEqual(send.await_args.kwargs["reply_parameters"].message_id, 2)

    async def test_unsupported_or_rejected_rich_message_falls_back_to_full_list(self):
        for unsupported in (True, False):
            with self.subTest(unsupported=unsupported):
                send = AsyncMock(return_value=SimpleNamespace(message_id=10))
                bot = SimpleNamespace(send_message=send)
                if not unsupported:
                    bot.send_rich_message = AsyncMock(
                        side_effect=TelegramBadRequest(
                            SendMessage(chat_id=1, text="placeholder"),
                            "invalid rich message",
                        )
                    )
                with patch.object(
                    types, "InputRichMessage", SimpleNamespace, create=True
                ):
                    await send_burst_messages(bot, 1, 2, TABLE)
                if not unsupported:
                    bot.send_rich_message.assert_awaited_once()
                rendered = "\n".join(call.args[1] for call in send.await_args_list)
                self.assertIn("• Тип: HDD; Применение: Архив", rendered)
                self.assertIn(
                    "• Тип: Внешний SSD; Применение: Резервные копии", rendered
                )
                self.assertIn("Выбирай по бюджету", rendered)
                self.assertEqual(
                    send.await_args_list[0].kwargs["reply_parameters"].message_id, 2
                )
