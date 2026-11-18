import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.database import Database
from sanya.handlers.access import resolve_target_user
from sanya.handlers.memory import cmd_setkent
from sanya.repository import Repository

VITYA_ID = 1005211563


class KentTargetTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = self.enterContext(tempfile.TemporaryDirectory())
        self.db = await self.enterAsyncContext(Database(Path(root) / "memory.db"))
        self.message = SimpleNamespace(
            text="",
            chat=SimpleNamespace(id=-1001),
            from_user=SimpleNamespace(id=7),
            reply_to_message=None,
            reply=AsyncMock(),
        )

    async def test_known_tag_and_reply_use_same_lore_without_overwriting_username(self):
        self.message.text = "/setkent @MURK227 Факт по тегу"
        with patch(
            "sanya.handlers.memory.check_is_admin", AsyncMock(return_value=True)
        ):
            await cmd_setkent(self.message, self.db)
            self.message.reply_to_message = SimpleNamespace(
                from_user=SimpleNamespace(
                    id=VITYA_ID, username="new_handle", first_name="Витя"
                )
            )
            self.message.text = "/setkent Факт по реплаю"
            await cmd_setkent(self.message, self.db)
            self.message.reply_to_message = None
            self.message.text = "/setkent @murk227 Ещё факт по алиасу"
            await cmd_setkent(self.message, self.db)
        rows = await self.db.run(Repository.get_kent_fact_entries, VITYA_ID)
        self.assertEqual(
            [row["content"] for row in rows[-3:]],
            [
                "Факт по тегу",
                "Факт по реплаю",
                "Ещё факт по алиасу",
            ],
        )
        profile = await self.db.run(Repository.get_user_profile, VITYA_ID)
        self.assertEqual(profile["user_name"], "new_handle")
        self.message.text = "/setkent @new_handle"
        await cmd_setkent(self.message, self.db)
        self.assertIn("Факт по тегу", self.message.reply.await_args.args[0])

    async def test_explicit_unknown_tag_does_not_use_unrelated_reply(self):
        self.message.reply_to_message = SimpleNamespace(
            from_user=SimpleNamespace(id=22, username="real_tag", first_name="Кент")
        )
        result = await resolve_target_user(
            self.db, self.message, "@unknown_tag факт", allow_kent_aliases=True
        )
        self.assertIsNone(result[0])
        self.assertIsNotNone(result[3])
        result = await resolve_target_user(
            self.db, self.message, "@REAL_TAG факт", allow_kent_aliases=True
        )
        self.assertEqual((result[0], result[2], result[3]), (22, "факт", None))

    async def test_current_profile_takes_priority_and_duplicates_need_id(self):
        await self.db.run(Repository.update_user_profile, 22, "murk227")
        result = await resolve_target_user(
            self.db, self.message, "@murk227", allow_kent_aliases=True
        )
        self.assertEqual(result[0], 22)
        await self.db.run(Repository.update_user_profile, 23, "Murk227")
        result = await resolve_target_user(
            self.db, self.message, "@murk227", allow_kent_aliases=True
        )
        self.assertIsNone(result[0])
        self.assertIn("нескольких профилей", result[3])

    async def test_alias_fallback_is_not_used_for_global_delete(self):
        result = await resolve_target_user(self.db, self.message, "@murk227")
        self.assertIsNone(result[0])
