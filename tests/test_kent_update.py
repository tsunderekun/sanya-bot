import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from sanya.lore import INITIAL_KENTS_PROFILES, KENT_ALIASES
from sanya.schema import initialize
from update_kents_db import PREVIOUS, RETIRED, digest, update


class KentUpdateTests(unittest.TestCase):
    def test_retired_lore_is_removed_without_deleting_profile_or_history(self):
        uid = 970668834
        old = "retired built-in note"
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            self.assertNotIn(uid, KENT_ALIASES)
            self.assertNotIn(uid, INITIAL_KENTS_PROFILES)
            conn.execute(
                "INSERT INTO user_profiles (user_id, persona_notes) VALUES (?, ?)",
                (uid, old),
            )
            conn.execute(
                "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                (uid, old),
            )
            conn.execute(
                "INSERT INTO messages (chat_id, message_id, user_id, role, content) VALUES (1, 2, ?, 'user', 'history')",
                (uid,),
            )
            with patch.dict(RETIRED, {uid: digest(old)}):
                self.assertEqual(update(conn)[uid], "remove retired built-in lore")
                self.assertEqual(update(conn)[uid], "unchanged")
            self.assertEqual(
                conn.execute(
                    "SELECT persona_notes FROM user_profiles WHERE user_id=?", (uid,)
                ).fetchone()[0],
                "",
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM kent_facts_entries WHERE user_id=?", (uid,)
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute("SELECT content FROM messages").fetchone()[0], "history"
            )

    def test_updates_only_recognized_defaults_and_is_idempotent(self):
        uid = 288125599
        with sqlite3.connect(":memory:") as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            old = "previous defaults"
            entry_id = conn.execute(
                "SELECT id FROM kent_facts_entries WHERE user_id=?", (uid,)
            ).fetchone()[0]
            conn.execute(
                "UPDATE kent_facts_entries SET content=? WHERE id=?", (old, entry_id)
            )
            manual_id = conn.execute(
                "INSERT INTO kent_facts_entries (user_id,content,added_by) VALUES (?,?,1)",
                (uid, "manual"),
            ).lastrowid
            conn.execute(
                "UPDATE user_profiles SET persona_notes=?, message_count=83 WHERE user_id=?",
                (old + "\nmanual", uid),
            )
            conn.execute(
                "INSERT INTO messages (chat_id,message_id,user_id,role,content) VALUES (1,2,?,'user','message')",
                (uid,),
            )
            with patch.dict(PREVIOUS, {uid: hashlib.sha256(old.encode()).hexdigest()}):
                self.assertEqual(update(conn)[uid], "replace built-in notes")
                self.assertEqual(update(conn)[uid], "unchanged")
            note = INITIAL_KENTS_PROFILES[uid]["notes"]
            self.assertEqual(
                [
                    tuple(r)
                    for r in conn.execute(
                        "SELECT id,content FROM kent_facts_entries WHERE user_id=? ORDER BY id",
                        (uid,),
                    )
                ],
                [(entry_id, note), (manual_id, "manual")],
            )
            self.assertEqual(
                tuple(
                    conn.execute(
                        "SELECT persona_notes,message_count FROM user_profiles WHERE user_id=?",
                        (uid,),
                    ).fetchone()
                ),
                (note + "\nmanual", 83),
            )
            self.assertEqual(
                conn.execute("SELECT content FROM messages").fetchone()[0], "message"
            )

    def test_identity_split(self):
        self.assertIn("жидкер", KENT_ALIASES[288125599])
        self.assertIn("nikolay", KENT_ALIASES[207620287])
        self.assertNotIn("nikolay", KENT_ALIASES[288125599])
