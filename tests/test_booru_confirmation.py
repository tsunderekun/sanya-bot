import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import DeleteMessage
from aiogram.types import Chat, Message, User

from sanya.services import booru, booru_confirmation, chat


class BooruConfirmationTests(unittest.IsolatedAsyncioTestCase):
    def test_explicit_safe_rating_does_not_require_confirmation(self):
        for query in ("cat, safe", "cat, general", "cat rating:safe"):
            self.assertFalse(booru.requires_confirmation(query))
        for query in ("cat", "cat, explicit", "cat, safe, questionable"):
            self.assertTrue(booru.requires_confirmation(query))

    async def test_rule34_omits_default_rating_only_after_confirmation(self):
        def respond(request):
            tags = request.url.params["tags"]
            expected.append(tags)
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "rating": "questionable",
                        "file_url": "https://wimg.rule34.xxx/1.jpg",
                    }
                ],
            )

        expected = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for tags, confirmed, found in (
                (["cat", "solo"], False, False),
                (["cat", "solo"], True, True),
                (["cat", "safe"], True, False),
                (["cat", "general"], False, False),
                (["cat", "questionable"], True, True),
            ):
                result = await booru._candidates(
                    client, "Rule34", tags, False, 1, 15, confirmed=confirmed
                )
                self.assertEqual(bool(result), found)
        self.assertEqual(
            expected,
            [
                "cat solo rating:safe",
                "cat solo",
                "cat rating:safe",
                "cat rating:general",
                "cat rating:questionable",
            ],
        )

    async def test_only_original_author_can_confirm_once(self):
        group = Chat(id=-10, type="group")
        author = User(id=100, is_bot=False, first_name="Author")
        prompt = Message(message_id=8, date=datetime.now(timezone.utc), chat=group)
        message = SimpleNamespace(
            chat=group,
            from_user=author,
            sender_chat=None,
            reply=AsyncMock(return_value=prompt),
        )
        requests = SimpleNamespace(enqueue=AsyncMock(return_value=True))
        with (
            patch.object(booru_confirmation, "_pending", {}),
            patch.object(Message, "delete", new_callable=AsyncMock) as delete,
            patch.object(Message, "edit_reply_markup", new_callable=AsyncMock) as edit,
        ):
            await booru_confirmation.ask_booru_confirmation(
                message, "cat, solo", slop=True
            )
            button = message.reply.call_args.kwargs["reply_markup"].inline_keyboard[0][
                0
            ]
            self.assertEqual(button.text, "Да, я понимаю")
            self.assertEqual(
                message.reply.await_args.args[0],
                "Результат может быть небезопасным. Продолжить поиск?",
            )
            callback = SimpleNamespace(
                data=button.callback_data,
                message=prompt,
                from_user=SimpleNamespace(id=101),
                answer=AsyncMock(),
            )
            await booru_confirmation.confirm_booru(callback, requests)
            requests.enqueue.assert_not_called()
            callback.from_user = author
            with patch.object(
                booru_confirmation, "monotonic", return_value=float("inf")
            ):
                await booru_confirmation.confirm_booru(callback, requests)
            requests.enqueue.assert_not_called()
            delete.assert_not_called()
            await booru_confirmation.confirm_booru(callback, requests)
            await booru_confirmation.confirm_booru(callback, requests)
            requests.enqueue.assert_awaited_once_with(
                message, force_booru_query="cat, solo", booru_confirmed=True, slop=True
            )
            delete.assert_awaited_once()
            edit.assert_not_called()

    async def test_confirmation_removes_button_if_warning_cannot_be_deleted(self):
        group = Chat(id=-10, type="group")
        author = User(id=100, is_bot=False, first_name="Author")
        prompt = Message(message_id=8, date=datetime.now(timezone.utc), chat=group)
        message = SimpleNamespace(
            chat=group,
            from_user=author,
            sender_chat=None,
            reply=AsyncMock(return_value=prompt),
        )
        requests = SimpleNamespace(enqueue=AsyncMock(return_value=True))
        error = TelegramAPIError(
            DeleteMessage(chat_id=group.id, message_id=8), "Denied"
        )
        with (
            patch.object(booru_confirmation, "_pending", {}),
            patch.object(Message, "delete", AsyncMock(side_effect=error)),
            patch.object(Message, "edit_reply_markup", new_callable=AsyncMock) as edit,
        ):
            await booru_confirmation.ask_booru_confirmation(message, "cat, solo")
            button = message.reply.await_args.kwargs["reply_markup"].inline_keyboard[0][
                0
            ]
            callback = SimpleNamespace(
                data=button.callback_data,
                message=prompt,
                from_user=author,
                answer=AsyncMock(),
            )
            await booru_confirmation.confirm_booru(callback, requests)
            edit.assert_awaited_once_with(reply_markup=None)

    async def test_chat_waits_for_confirmation_and_spoils_result(self):
        message = SimpleNamespace(
            text="/booru cat, solo",
            photo=None,
            sticker=None,
            reply_to_message=None,
            chat=SimpleNamespace(id=-10, type="group"),
            from_user=SimpleNamespace(id=100, username="reader", first_name="Reader"),
            message_id=7,
        )
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="bot")),
            send_chat_action=AsyncMock(),
            send_photo=AsyncMock(),
        )
        db = SimpleNamespace(run=AsyncMock(return_value=set()))
        llm = SimpleNamespace(generate=AsyncMock(return_value="Держи"))
        with (
            patch.object(chat, "ask_booru_confirmation", new_callable=AsyncMock) as ask,
            patch.object(
                chat, "search_and_download_booru_image", new_callable=AsyncMock
            ) as search,
        ):
            search.return_value = (b"photo", "", "Rule34", "Rule34:1", False)
            args = dict(db=db, llm=llm, force_booru_query="cat, solo")
            await chat.process_chat_message(message, bot, **args)
            ask.assert_awaited_once_with(message, "cat, solo")
            search.assert_not_called()
            await chat.process_chat_message(message, bot, booru_confirmed=True, **args)
            search.assert_awaited_once_with("cat, solo", seen=set(), confirmed=True)
            self.assertTrue(bot.send_photo.call_args.kwargs["has_spoiler"])

    async def test_r34_resolves_description_without_changing_confirmation_or_rating(
        self,
    ):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="bot")),
            send_chat_action=AsyncMock(),
        )
        db = SimpleNamespace(run=AsyncMock(return_value=set()))
        for query, confirmed, model_output, expected_calls in (
            ("кот в шляпе", False, '["cat", "hat"]', []),
            ("кот в шляпе", True, '["cat", "hat"]', ["cat, hat"]),
            ("кот в шляпе, rating:g", False, '["cat", "hat"]', ["cat, hat, general"]),
            ("кот в шляпе, safe", False, '["cat", "explicit"]', []),
            ("кот в шляпе, safe", False, '["cat) OR (dog"]', []),
            ("cat, hat, safe", False, "", ["cat, hat, safe"]),
            (
                "cat hat, safe",
                False,
                '["cat", "hat"]',
                ["cat hat, safe", "cat, hat, safe"],
            ),
        ):
            with self.subTest(query=query, confirmed=confirmed, output=model_output):
                message = SimpleNamespace(
                    text=f"/р34@bot {query}",
                    photo=None,
                    sticker=None,
                    reply_to_message=None,
                    chat=SimpleNamespace(id=-10, type="group"),
                    from_user=SimpleNamespace(
                        id=100, username="reader", first_name="Reader"
                    ),
                    message_id=7,
                    reply=AsyncMock(),
                )
                llm = SimpleNamespace(generate=AsyncMock(return_value=model_output))
                with (
                    patch.object(
                        chat, "ask_booru_confirmation", new_callable=AsyncMock
                    ) as ask,
                    patch.object(
                        chat,
                        "search_and_download_booru_image",
                        AsyncMock(return_value=None),
                    ) as search,
                ):
                    await chat.process_chat_message(
                        message,
                        bot,
                        db=db,
                        llm=llm,
                        force_booru_query=query,
                        booru_confirmed=confirmed,
                    )
                self.assertEqual(
                    [c.args[0] for c in search.await_args_list], expected_calls
                )
                if query == "кот в шляпе" and not confirmed:
                    ask.assert_awaited_once_with(message, query)
                    llm.generate.assert_not_awaited()
                else:
                    ask.assert_not_awaited()
                    if query == "cat, hat, safe":
                        llm.generate.assert_not_awaited()
                    else:
                        llm.generate.assert_awaited_once()
