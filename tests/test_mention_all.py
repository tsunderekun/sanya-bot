import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from sanya.database import Database
from sanya.handlers.mention_all import _CALL, mention_all
from sanya.repository import Repository


def member(uid, status="member", *, name="Кент", is_bot=False, is_member=False):
    return NS(
        status=status,
        is_member=is_member,
        user=NS(id=uid, first_name=name, is_bot=is_bot),
    )


class MentionAllTests(unittest.IsolatedAsyncioTestCase):
    def test_call_is_a_separate_first_word(self):
        self.assertTrue(_CALL.match(" @ALL сбор"))
        self.assertFalse(_CALL.match("@all123"))
        self.assertFalse(_CALL.match("пример @all"))

    async def test_scope_and_membership_confirmation(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "test.db"
        chat = NS(id=-100, type="supergroup")
        message = NS(
            chat=chat,
            from_user=member(1).user,
            sender_chat=None,
            forward_origin=None,
            reply=AsyncMock(),
        )
        members = {
            3: member(3, name="<Гость&>"),
            4: member(4, "left"),
            5: member(5, is_bot=True),
            6: member(6, "restricted", is_member=True),
            7: member(7, "restricted"),
        }
        bot = NS(
            get_chat_administrators=AsyncMock(return_value=[member(1), member(2)]),
            get_chat_member=AsyncMock(side_effect=lambda _, uid: members[uid]),
        )
        async with Database(path) as db:
            for mid, (cid, uid, role) in enumerate(
                [(-100, uid, "user") for uid in members]
                + [(-200, 8, "user"), (-100, 9, "assistant"), (-100, 3, "user")],
                start=1,
            ):
                await db.run(
                    Repository.save_message, cid, mid, None, uid, "test", role, "Привет"
                )
            cooldowns = {}
            await mention_all(message, bot, db, cooldowns)
            text = message.reply.await_args.args[0]
            for uid in (2, 3, 6):
                self.assertIn(f'tg://user?id={uid}"', text)
            self.assertIn("&lt;Гость&amp;&gt;", text)
            self.assertEqual(text.count("tg://user?id="), 3)
            self.assertEqual(bot.get_chat_member.await_count, 5)
            await mention_all(message, bot, db, cooldowns)
            self.assertIn("5 минут", message.reply.await_args.args[0])
            self.assertEqual(bot.get_chat_member.await_count, 5)

    async def test_nonadmin_cannot_ping(self):
        message = NS(
            chat=NS(id=-100, type="supergroup"),
            from_user=member(2).user,
            sender_chat=None,
            forward_origin=None,
            reply=AsyncMock(),
        )
        bot = NS(get_chat_administrators=AsyncMock(return_value=[member(1)]))
        db = NS(run=AsyncMock())
        await mention_all(message, bot, db, {})
        self.assertIn("только админы", message.reply.await_args.args[0])
        db.run.assert_not_awaited()

    async def test_anonymous_admin_and_multiple_batches(self):
        chat = NS(id=-100, type="supergroup")
        message = NS(
            chat=chat,
            from_user=member(1, is_bot=True).user,
            sender_chat=chat,
            forward_origin=None,
            reply=AsyncMock(),
        )
        bot = NS(
            get_chat_administrators=AsyncMock(
                return_value=[member(i) for i in range(2, 23)]
            )
        )
        await mention_all(message, bot, NS(run=AsyncMock(return_value=[])), {})
        self.assertEqual(
            [
                call.args[0].count("tg://user?id=")
                for call in message.reply.await_args_list
            ],
            [20, 1],
        )
