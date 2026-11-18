import unittest

from sanya.intent import Mode
from sanya.memory import build_turn, estimate_tokens, fit_prompt
from sanya.prompts import CAPTION_PROMPT, build_system_prompt


class PromptTests(unittest.TestCase):
    def test_chat_loads_only_matching_lore(self):
        chat = build_system_prompt(Mode.CHAT, "что за слойпер?")
        self.assertIn("4PDA", chat)
        self.assertIn("[стикер: 😁]", chat)
        self.assertNotIn("Galaxy S23 Ultra", chat)
        self.assertNotIn("Negative prompt:", chat)
        self.assertNotIn("ZOV", build_system_prompt(Mode.CHAT, "своё мнение"))

    def test_specialized_contracts_do_not_inherit_chat_format(self):
        code = build_system_prompt(Mode.CODE, "напиши скрипт про X99 и Авито")
        self.assertIn("полный рабочий код", code)
        self.assertNotIn("Xeon", code)
        self.assertNotIn("[стикер:", code)
        prompt = build_system_prompt(Mode.PROMPT, "дай промпт для Rainbow Dash")
        self.assertIn("Negative prompt:", prompt)
        self.assertIn("Саня брони", prompt)
        self.assertNotIn("2–4", prompt)
        self.assertIn("2 строки по 3–5 слов", CAPTION_PROMPT)

    def test_repair_and_buyer_context_can_coexist(self):
        detail = build_system_prompt(Mode.DETAIL, "Покупатель пишет: плата не работает")
        self.assertIn("Покупателю с проблемой отвечай без подкола", detail)
        self.assertIn("Если чинить дороже замены", detail)

    def test_budget_retains_history_and_separates_instructions(self):
        system = {
            "role": "system",
            "content": build_system_prompt(Mode.DETAIL, "как настроить DNS"),
        }
        history = [{"role": "assistant", "content": "тест " * 300}]
        current = build_turn("как настроить DNS", notes=["факт"], search="сниппет")
        self.assertNotIn("Формат:", current["content"])
        context = fit_prompt(system, [*history, current])
        self.assertEqual(context, [system, *history, current])
        self.assertLess(estimate_tokens([system]), 2000)
