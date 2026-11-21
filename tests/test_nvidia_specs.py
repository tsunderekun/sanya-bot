import unittest
from unittest.mock import AsyncMock, patch

from sanya.services.nvidia_specs import RTX_3070_PAGE, parse_specs, requested_model
from sanya.services.search import lookup_search_evidence

TABLE = """
<table>
<tr><td></td><td></td><td>GeForce RTX 3070 Ti</td><td>GeForce RTX 3070</td></tr>
<tr><td>GPU</td><td>NVIDIA CUDA &reg; Cores</td><td>6144</td><td>5888</td></tr>
<tr><td></td><td>Boost Clock (GHz)</td><td>1.77</td><td>1.73</td></tr>
<tr><td>Memory</td><td>Standard Memory Config</td><td>8 GB GDDR6X</td><td>8 GB GDDR6</td></tr>
</table>
"""


class NvidiaSpecsTests(unittest.IsolatedAsyncioTestCase):
    def test_selects_correct_column(self):
        base = parse_specs(TABLE, "RTX 3070")
        self.assertIn("5888", base)
        self.assertIn("8 ГБ GDDR6\n", base)
        self.assertNotIn("6144", base)
        self.assertIn("6144", parse_specs(TABLE, "RTX 3070 TI"))
        self.assertEqual(parse_specs(TABLE, "RTX 4070"), "")

    def test_limits_fast_path_to_supported_spec_questions(self):
        self.assertEqual(requested_model("характеристики RTX 3070 на сайте NVIDIA"), "RTX 3070")
        self.assertEqual(requested_model("NVIDIA RTX3070 Ti specs"), "RTX 3070 TI")
        for query in (
            "RTX 3070 specs", "NVIDIA RTX 3070 laptop specs",
            "NVIDIA RTX 3070 and RTX 4070 specs", "сравни характеристики RTX 3070 и RTX 3070 Ti NVIDIA",
        ):
            self.assertIsNone(requested_model(query), query)

    async def test_official_answer_skips_general_search(self):
        with (
            patch("sanya.services.search.fetch_specs", AsyncMock(return_value="official specs")) as fetch,
            patch("sanya.services.search.perform_web_search", AsyncMock()) as search,
        ):
            result = await lookup_search_evidence("NVIDIA RTX 3070 specs")
        fetch.assert_awaited_once_with("RTX 3070")
        search.assert_not_awaited()
        self.assertEqual(result.official_answer, "official specs")
        self.assertEqual(result.urls, (RTX_3070_PAGE,))

    async def test_missing_official_table_keeps_third_party_source_distinct(self):
        with (
            patch("sanya.services.search.fetch_specs", AsyncMock(return_value="")),
            patch("sanya.services.search.perform_web_search", AsyncMock(return_value=[
                {"url": "https://example.org/item", "body": "specs from another site"},
            ])),
        ):
            result = await lookup_search_evidence("NVIDIA RTX 3070 specs")
        self.assertTrue(result.official_unconfirmed)
        self.assertFalse(result.official_answer)
        self.assertIn("example.org", result.snippets)
