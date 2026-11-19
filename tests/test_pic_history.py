import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from sanya.database import Database
from sanya.repository import Repository
from sanya.services import media


class PicHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_exhausted_primary_search_falls_back_without_repeating(self):
        seen = "https://image.test/seen.jpg"
        fresh = "https://image.test/fresh.jpg"

        def handle(request):
            if request.url.host == "search.test":
                return httpx.Response(200, json={"results": [{"img_src": seen}]})
            self.assertEqual(str(request.url), fresh)
            return httpx.Response(200, content=b"b" * 2500)

        client = httpx.AsyncClient
        history = (media.pic_identity(b"a" * 2500, seen),)
        with (
            patch.object(media, "SEARCH_ENGINE", "searxng"),
            patch.object(media, "SEARXNG_URL", "https://search.test"),
            patch.object(
                media.httpx,
                "AsyncClient",
                side_effect=lambda **kw: client(
                    transport=httpx.MockTransport(handle), **kw
                ),
            ),
            patch.object(
                media.asyncio,
                "to_thread",
                side_effect=[[{"image": fresh}], [{"image": seen}]],
            ),
        ):
            self.assertEqual(
                await media.search_and_download_image("tf2", history=history),
                (b"b" * 2500, fresh),
            )
            self.assertIsNone(
                await media.search_and_download_image("tf2", history=history)
            )

    async def test_history_is_saved_and_scoped(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = root / "pic.db"
        key = media.pic_query_key(" MGE   TF2 ")
        image = media.pic_identity(b"pic", "https://example.test/pic.jpg")
        async with Database(path) as db:
            await db.run(Repository.remember_pic, -100, key, *image)
        async with Database(path) as db:
            self.assertEqual(
                await db.run(
                    Repository.get_pic_history, -100, media.pic_query_key("mge tf2")
                ),
                [image],
            )
            self.assertEqual(await db.run(Repository.get_pic_history, -101, key), [])
            self.assertEqual(
                await db.run(
                    Repository.get_pic_history, -100, media.pic_query_key("other")
                ),
                [],
            )

    async def test_repeat_skips_original_and_exact_copy_then_tries_next_page(self):
        original = "https://image.test/original.jpg"
        copy = "https://image.test/copy.jpg"
        fresh = "https://image.test/fresh.jpg"
        requests = []

        def handle(request):
            requests.append(str(request.url))
            if request.url.host == "search.test":
                urls = (
                    [original, copy] if request.url.params["pageno"] == "1" else [fresh]
                )
                return httpx.Response(
                    200, json={"results": [{"img_src": url} for url in urls]}
                )
            return httpx.Response(
                200,
                content=(b"b" if str(request.url) == fresh else b"a") * 2500,
                headers={"Content-Type": "image/jpeg"},
            )

        client = httpx.AsyncClient
        with (
            patch.object(media, "SEARCH_ENGINE", "searxng"),
            patch.object(media, "SEARXNG_URL", "https://search.test"),
            patch.object(
                media.httpx,
                "AsyncClient",
                side_effect=lambda **kw: client(
                    transport=httpx.MockTransport(handle), **kw
                ),
            ),
        ):
            first = await media.search_and_download_image("mge tf2")
            self.assertEqual(first, (b"a" * 2500, original))
            requests.clear()
            second = await media.search_and_download_image(
                "mge tf2", history=(media.pic_identity(*first),)
            )
        self.assertEqual(second, (b"b" * 2500, fresh))
        self.assertNotIn(original, requests)
        self.assertIn(copy, requests)
