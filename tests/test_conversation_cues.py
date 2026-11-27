import unittest

from sanya.conversation_cues import (
    fresh_topic_reply_is_on_topic,
    punchline_targeted_at_person,
)


class ConversationCueTests(unittest.TestCase):
    def test_neutral_reply_to_a_joke_is_accepted(self):
        text = "Вот это шутка с подъёмом"
        self.assertFalse(punchline_targeted_at_person(text))
        self.assertTrue(fresh_topic_reply_is_on_topic(text, is_hint=False))

    def test_generic_or_insulting_reply_is_rejected(self):
        for text in (
            "вот как",
            "ага",
            "понятно",
            "ну и прикол ты выдал",
            "это была тупая шутка",
            "ты зануда",
        ):
            with self.subTest(text=text):
                self.assertFalse(fresh_topic_reply_is_on_topic(text, is_hint=False))

    def test_hint_only_blocks_insults(self):
        self.assertTrue(
            fresh_topic_reply_is_on_topic("тут дело в другом", is_hint=True)
        )
        self.assertFalse(
            fresh_topic_reply_is_on_topic("это была тупая шутка", is_hint=True)
        )
