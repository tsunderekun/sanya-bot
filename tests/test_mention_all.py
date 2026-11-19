import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.methods import GetChatMember

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

    async def test_partial_lookup_tags_only_confirmed_people_and_keeps_cooldown(self):
        chat = NS(id=-100, type="supergroup")
        message = NS(
            chat=chat,
            from_user=member(1).user,
            sender_chat=None,
            forward_origin=None,
            reply=AsyncMock(),
        )
        stale = TelegramBadRequest(
            GetChatMember(chat_id=chat.id, user_id=3), "PARTICIPANT_ID_INVALID"
        )
        limited = TelegramRetryAfter(
            GetChatMember(chat_id=chat.id, user_id=4), "Flood control", 30
        )
        bot = NS(
            get_chat_administrators=AsyncMock(return_value=[member(1)]),
            get_chat_member=AsyncMock(
                side_effect=[member(2), stale, limited, member(5, "left")]
            ),
        )
        db = NS(run=AsyncMock(return_value=[1, 2, 3, 4, 5, 6]))
        cooldowns = {}
        with (
            patch("sanya.handlers.mention_all.monotonic", return_value=100),
            self.assertLogs("sanya.handlers.mention_all", "WARNING"),
        ):
            await mention_all(message, bot, db, cooldowns)
            self.assertEqual(bot.get_chat_member.await_count, 4)
            text = message.reply.await_args.args[0]
            self.assertIn('tg://user?id=2"', text)
            self.assertEqual(text.count("tg://user?id="), 1)
            self.assertIn("не всех", text)
            await mention_all(message, bot, db, cooldowns)
            self.assertIn("5:00", message.reply.await_args.args[0])

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
            with patch("sanya.handlers.mention_all.monotonic", return_value=100):
                await mention_all(message, bot, db, cooldowns)
            text = message.reply.await_args.args[0]
            for uid in (2, 3, 6):
                self.assertIn(f'tg://user?id={uid}"', text)
            self.assertIn("&lt;Гость&amp;&gt;", text)
            self.assertEqual(text.count("tg://user?id="), 3)
            self.assertEqual(bot.get_chat_member.await_count, 5)
            with patch("sanya.handlers.mention_all.monotonic", return_value=100):
                await mention_all(message, bot, db, cooldowns)
            self.assertIn("5:00", message.reply.await_args.args[0])
            self.assertEqual(bot.get_chat_member.await_count, 5)

    async def test_group_cooldown_is_shared_and_depends_on_callers_role(self):
        message = NS(
            chat=NS(id=-100, type="supergroup"),
            from_user=member(2).user,
            sender_chat=None,
            forward_origin=None,
            reply=AsyncMock(),
        )
        bot = NS(get_chat_administrators=AsyncMock(return_value=[member(1), member(4)]))
        db = NS(run=AsyncMock(return_value=[]))
        cooldowns = {}
        for now, chat_id, uid, expected in (
            (0, -100, 2, 'tg://user?id=1"'),
            (299, -100, 1, "0:01"),
            (300, -100, 1, 'tg://user?id=4"'),
            (301, -100, 3, "14:59"),
            (301, -200, 3, 'tg://user?id=1"'),
            (1199, -100, 3, "0:01"),
            (1200, -100, 3, 'tg://user?id=1"'),
        ):
            with self.subTest(now=now, chat=chat_id, uid=uid):
                message.chat.id = chat_id
                message.from_user = member(uid).user
                with patch("sanya.handlers.mention_all.monotonic", return_value=now):
                    await mention_all(message, bot, db, cooldowns)
                self.assertIn(expected, message.reply.await_args.args[0])

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
