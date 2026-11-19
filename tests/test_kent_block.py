import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiogram import Bot
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from sanya.database import Database
from sanya.handlers.kent_block import KentBlockMiddleware
from sanya.main import build_dispatcher
from sanya.repository import Repository
from sanya.services.chat import process_chat_message


class KentBlockTests(unittest.IsolatedAsyncioTestCase):
    def message(self, user_id, text, *, chat_id=-10042, parent=None):
        return Message(
            message_id=2 if parent else 1,
            date=datetime.now(timezone.utc),
            chat=Chat(id=chat_id, type="supergroup"),
            from_user=User(id=user_id, is_bot=False, first_name="Test"),
            text=text,
            reply_to_message=parent,
        )

    async def test_admin_can_block_by_reply_and_unblock_by_id(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        path = Path(directory) / "test.db"
        bot = await self.enterAsyncContext(Bot("123:TEST").context())
        dispatcher = build_dispatcher()
        target = self.message(22, "Саня, ответь")
        check = AsyncMock(side_effect=lambda chat, user_id: user_id == 33)
        with (
            patch("sanya.handlers.kent_block.check_is_admin", check),
            patch.object(Message, "reply", AsyncMock()) as reply,
            patch.object(CallbackQuery, "answer", AsyncMock()) as answer,
        ):
            async with Database(path) as db:

                async def send(message):
                    return await dispatcher.feed_update(
                        bot, Update(update_id=1, message=message), db=db
                    )

                await send(self.message(44, "/blockkent", parent=target))
                self.assertIn("Только админы", reply.await_args.args[0])
                self.assertFalse(await db.run(Repository.is_kent_blocked, -10042, 22))
                await send(self.message(33, "/blockkent", parent=target))
                self.assertTrue(await db.run(Repository.is_kent_blocked, -10042, 22))
                reply.reset_mock()
                await send(self.message(22, "/help"))
                reply.assert_not_awaited()
                await send(self.message(22, "/help", chat_id=-10043))
                reply.assert_awaited_once()
                callback = CallbackQuery(
                    id="test",
                    from_user=target.from_user,
                    chat_instance="test",
                    message=target,
                    data="settings:open",
                )
                next_handler = AsyncMock()
                await KentBlockMiddleware()(next_handler, callback, {"db": db})
                answer.assert_awaited_once()
                next_handler.assert_not_awaited()
                llm = AsyncMock()
                with patch(
                    "sanya.services.chat._process_prompt_idea", AsyncMock()
                ) as idea:
                    await process_chat_message(
                        target, bot, db=db, llm=llm, prompt_idea=True
                    )
                    idea.assert_not_awaited()
            async with Database(path) as db:
                self.assertTrue(await db.run(Repository.is_kent_blocked, -10042, 22))
                await send(self.message(33, "/unblockkent 22"))
                self.assertFalse(await db.run(Repository.is_kent_blocked, -10042, 22))
                reply.reset_mock()
                await send(self.message(22, "/help"))
                reply.assert_awaited_once()
