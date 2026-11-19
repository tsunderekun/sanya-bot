import sqlite3
import subprocess
import tempfile
import unittest
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from sanya.handlers.hardware import (
    cmd_hardware,
    cmd_price,
    listing_prices,
    send_price_estimate,
)
from sanya.handlers.memes import (
    cmd_meme,
    collect_channel_post,
    maybe_send_horse,
    preview_media,
)
from sanya.intent import Mode
from sanya.prompts import build_system_prompt
from sanya.repository import Repository
from sanya.schema import initialize
from sanya.services.booru import parse_tags, search_and_download_booru_image
from sanya.services.ocr import recognize_image_text


class FeatureTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_media_without_caption_and_protected_post_skipped(self):
        db = SimpleNamespace(run=AsyncMock())
        message = SimpleNamespace(
            chat=SimpleNamespace(
                id=-100, username="test_donor", has_protected_content=False
            ),
            message_id=7,
            has_protected_content=True,
            is_paid_post=False,
            has_media_spoiler=False,
            animation=SimpleNamespace(file_id="a", file_unique_id="unique"),
            video=None,
            photo=None,
            answer_photo=AsyncMock(),
            answer_video=AsyncMock(),
            answer_animation=AsyncMock(),
            reply=AsyncMock(),
        )
        await collect_channel_post(message, db)
        db.run.assert_not_awaited()
        message.has_protected_content = False
        await collect_channel_post(message, db)
        db.run.assert_awaited_once_with(
            Repository.save_channel_meme, -100, 7, "animation", "a", "unique"
        )
        db.run.return_value = [{"media_type": "animation", "file_id": "a"}]
        await cmd_meme(message, db)
        message.answer_animation.assert_awaited_once_with("a")

    async def test_horse_art_has_cooldown_and_ignores_commands(self):
        message = SimpleNamespace(
            chat=SimpleNamespace(id=-22222), answer_photo=AsyncMock()
        )
        with tempfile.TemporaryDirectory() as root:
            file = Path(root) / "horse.png"
            file.write_bytes(b"sample")
            with (
                patch("sanya.handlers.memes.SLOIPER_DIR", Path(root)),
                patch("sanya.handlers.memes.random.random", return_value=0),
                patch("sanya.handlers.memes._cooldowns", OrderedDict()),
            ):
                self.assertFalse(await maybe_send_horse(message, "/help хорс"))
                self.assertTrue(await maybe_send_horse(message, "про хорса"))
                self.assertFalse(await maybe_send_horse(message, "horse"))
            self.assertEqual(Path(message.answer_photo.await_args.args[0].path), file)

    async def test_booru_skips_seen_and_uses_safe_single_tag_query(self):
        def respond(request):
            if request.url.host == "derpibooru.org":
                self.assertEqual(
                    request.url.params["q"], "fluttershy, safe, score.gt:25"
                )
                self.assertIn(request.url.params["page"], {"3", "1"})
                return httpx.Response(
                    200,
                    json={
                        "images": [
                            {
                                "id": i,
                                "score": 42,
                                "tags": tags,
                                "view_url": f"https://cdn.derpicdn.net/{i}.jpg",
                            }
                            for i, tags in (
                                (1, ["safe"]),
                                (2, ["safe"]),
                                (3, ["explicit"]),
                            )
                        ]
                    },
                )
            if request.url.host == "cdn.derpicdn.net":
                self.assertEqual(request.url.path, "/2.jpg")
                return httpx.Response(
                    200, content=b"photo", headers={"Content-Type": "image/jpeg"}
                )
            return httpx.Response(503)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with (
            patch("sanya.services.booru.httpx.AsyncClient", return_value=client),
            patch("sanya.services.booru.random.randint", return_value=3),
        ):
            result = await search_and_download_booru_image(
                "флаттершай", seen={"Derpibooru:1"}
            )
        self.assertEqual(result[3:], ("Derpibooru:2", True))
        self.assertEqual(
            parse_tags("hatsune miku, solo"), (["hatsune miku", "solo"], False)
        )

    async def test_ocr_local_russian_and_english_or_empty_on_failure(self):
        with (
            patch("sanya.services.ocr.OCR_ENABLED", True),
            patch(
                "sanya.services.ocr.subprocess.run",
                return_value=SimpleNamespace(stdout="Ошибка BSOD\n".encode()),
            ) as run,
        ):
            self.assertEqual(await recognize_image_text(b"image"), "Ошибка BSOD")
            self.assertIn("rus+eng", run.call_args.args[0])
            run.side_effect = subprocess.TimeoutExpired("tesseract", 6)
            self.assertEqual(await recognize_image_text(b"image"), "")

    async def test_hardware_current_chat_and_confirmed_kent(self):
        message = SimpleNamespace(
            text="/железо",
            reply=AsyncMock(),
            chat=SimpleNamespace(id=-100),
            from_user=SimpleNamespace(id=7),
        )
        db = SimpleNamespace(
            run=AsyncMock(
                return_value=[
                    {
                        "fact_key": "self_report:-100:hardware_gpu",
                        "fact_text": "RTX 3070",
                    }
                ]
            )
        )
        await cmd_hardware(message, db)
        db.run.assert_awaited_once_with(Repository.get_self_facts, -100, 7, 8)
        self.assertIn("RTX 3070", message.reply.await_args.args[0])
        message.text = "/железо витек"
        await cmd_hardware(message, db)
        self.assertIn("i5-10400F", message.reply.await_args.args[0])

    async def test_price_refuses_to_guess_without_enough_listings(self):
        message = SimpleNamespace(text="/оцени RTX 3070", reply=AsyncMock())
        requests = SimpleNamespace(enqueue=AsyncMock(return_value=True))
        await cmd_price(message, requests)
        requests.enqueue.assert_awaited_once_with(message, force_price_query="RTX 3070")
        with (
            patch("sanya.handlers.hardware.ENABLE_WEB_SEARCH", True),
            patch(
                "sanya.handlers.hardware.perform_web_search", AsyncMock(return_value=[])
            ),
        ):
            await send_price_estimate(message, "RTX 3070")
        self.assertIn("выдумывать не буду", message.reply.await_args.args[0])


class FeatureUnitTests(unittest.TestCase):
    def test_preview_only_standalone_media_from_public_posts(self):
        wrap = '<div class="tgme_widget_message_wrap"><div data-post="mestodm/1">'
        page = (
            wrap
            + '<a class="tgme_widget_message_photo_wrap" style="background-image:url(\'https://cdn4.telesco.pe/file/art.jpg\')">'
            + wrap
            + '<video src="https://cdn4.telesco.pe/file/movie.mp4?token=abc&amp;x=1">'
            + wrap
            + '<video class="blured" src="https://cdn4.telesco.pe/file/spoiler.mp4">'
        )
        self.assertEqual(
            preview_media(page),
            [
                ("photo", "https://cdn4.telesco.pe/file/art.jpg"),
                ("video", "https://cdn4.telesco.pe/file/movie.mp4?token=abc&x=1"),
            ],
        )

    def test_expert_mode_chance_and_scope(self):
        with patch("sanya.prompts.random", return_value=0.09):
            self.assertIn(
                "Q4_K_M", build_system_prompt(Mode.DETAIL, "как ollama запустить")
            )
            self.assertNotIn("Q4_K_M", build_system_prompt(Mode.CODE, "ollama"))
            self.assertNotIn("Q4_K_M", build_system_prompt(Mode.CHAT, "здорово"))
        with patch("sanya.prompts.random", return_value=0.1):
            self.assertNotIn(
                "Q4_K_M", build_system_prompt(Mode.CHAT, "как ollama запустить")
            )

    def test_listing_parser_excludes_variants_duplicates_and_other_sites(self):
        url = "https://www.avito.ru/test/rtx_3070_123456"
        result = listing_prices(
            [
                {
                    "href": url + "?tracking=123",
                    "title": "MSI RTX 3070",
                    "body": "25 000 ₽",
                },
                {"url": url, "title": "RTX 3070 25 000 ₽"},
                {"url": url + "7", "title": "RTX 3070 Ti 30 000 ₽"},
                {"url": url + "8", "title": "Кулер для RTX 3070 2000 ₽"},
                {
                    "url": "https://avito.ru.example.com/rtx_3070_999999",
                    "title": "RTX 3070 18 000 ₽",
                },
            ],
            "RTX 3070",
        )
        self.assertEqual(result, [(25000, url)])

    def test_database_schema_channel_dedup_and_200_per_chat(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            initialize(conn)
            repo = Repository(conn)
            repo.save_channel_meme(-100, 1, "photo", "file", "u")
            repo.save_channel_meme(-100, 1, "photo", "duplicate", "u")
            self.assertEqual(
                [row["file_id"] for row in repo.random_channel_memes()], ["file"]
            )
            for i in range(201):
                repo.remember_booru_image(-100, f"Derpibooru:{i}")
            repo.remember_booru_image(-101, "Derpibooru:0")
            self.assertEqual(len(repo.recent_booru_ids(-100)), 200)
            self.assertNotIn("Derpibooru:0", repo.recent_booru_ids(-100))
            self.assertEqual(repo.recent_booru_ids(-101), {"Derpibooru:0"})

    def test_meme_repetition_penalty_and_history(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.row_factory = sqlite3.Row
            initialize(conn)
            repo = Repository(conn)
            repo.save_channel_meme(-100, 101, "photo", "file_a", "u_a")
            repo.save_channel_meme(-100, 102, "video", "file_b", "u_b")
            repo.save_channel_meme(-100, 103, "animation", "file_c", "u_c")

            all_memes = repo.random_channel_memes(limit=10)
            id_a = next(m["id"] for m in all_memes if m["file_id"] == "file_a")
            id_b = next(m["id"] for m in all_memes if m["file_id"] == "file_b")
            id_c = next(m["id"] for m in all_memes if m["file_id"] == "file_c")

            # Show A and B in chat 50
            repo.record_meme_history(50, id_a)
            repo.record_meme_history(50, id_b)

            # C has never been shown in chat 50, so C must be first candidate
            candidates = repo.get_channel_memes_with_penalty(50, limit=1)
            self.assertEqual(candidates[0]["id"], id_c)

            # Show A a second time -> A has show_count=2, B has show_count=1, C has show_count=0
            repo.record_meme_history(50, id_a)
            candidates_all = repo.get_channel_memes_with_penalty(50, limit=3)
            # A should be last (highest penalty)
            self.assertEqual(candidates_all[-1]["id"], id_a)
