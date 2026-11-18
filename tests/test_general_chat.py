import sqlite3
import unittest
from contextlib import closing

from sanya.intent import Mode, classify_intent, is_targeted_insult
from sanya.prompts import build_system_prompt
from sanya.repository import Repository
from sanya.schema import initialize


class GeneralChatTests(unittest.TestCase):
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
        self.assertIn("не своди разговор к железу", prompt)
        self.assertIn("Дружеский стёб подхвати", prompt)
        self.assertIn("не склеивай мат и мемы", prompt)

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
