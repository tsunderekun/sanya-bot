import unittest
from unittest.mock import patch

import httpx

from sanya.services import booru


class BooruSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_pony_queries_only_contact_derpibooru(self):
        for query, expected in (
            ("/r34@ponyeb228_bot МЛП, solo", "my little pony"),
            ("my_little_pony, solo", "my little pony"),
            ("пони, solo", "pony"),
            ("twilight_sparkle, solo", "twilight sparkle"),
            ("дерпи, solo", "derpy hooves"),
        ):
            with self.subTest(query=query):
                requests = []

                def respond(request):
                    requests.append(request)
                    self.assertEqual(request.url.host, "derpibooru.org")
                    self.assertEqual(request.url.params["q"], f"{expected}, solo, safe")
                    return httpx.Response(200, json={"images": []})

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with (
                    patch.object(booru, "RULE34_USER_ID", "42"),
                    patch.object(booru, "RULE34_API_KEY", "test-key"),
                    patch.object(booru.httpx, "AsyncClient", return_value=client),
                ):
                    self.assertIsNone(
                        await booru.search_and_download_booru_image(query, seen=set())
                    )
                self.assertEqual(len(requests), 2)

    async def test_e621_filters_rating_score_and_file_host(self):
        def respond(request):
            self.assertEqual(request.url.host, "e621.net")
            self.assertEqual(request.url.path, "/posts.json")
            self.assertEqual(request.url.params["tags"], "red_fox rating:s score:>25")
            self.assertEqual(request.url.params["page"], "2")
            return httpx.Response(
                200,
                json={
                    "posts": [
                        {
                            "id": i,
                            "rating": rating,
                            "score": {"total": score},
                            "sample": None,
                            "file": {"url": f"https://{host}/{i}.jpg"},
                        }
                        for i, rating, score, host in (
                            (1, "s", 26, "static1.e621.net"),
                            (2, "e", 26, "static1.e621.net"),
                            (3, "s", 25, "static1.e621.net"),
                            (4, "s", 26, "static1.e621.net.example.org"),
                        )
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            result = await booru._candidates(client, "e621", ["red fox"], True, 2, 15)
        self.assertEqual(result, [("e621:1", "https://static1.e621.net/1.jpg", "e621")])

    async def test_rule34_credentials_only_go_to_its_api(self):
        def respond(request):
            if request.url.host == "api.rule34.xxx":
                self.assertEqual(request.url.params["api_key"], "test-key")
                self.assertEqual(request.url.params["user_id"], "42")
                self.assertEqual(request.url.params["pid"], "2")
                self.assertEqual(
                    request.url.params["tags"], "hatsune_miku solo rating:safe"
                )
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 1,
                            "rating": "e",
                            "file_url": "https://wimg.rule34.xxx/1.jpg",
                        },
                        {
                            "id": 2,
                            "rating": "s",
                            "file_url": "https://wimg.rule34.xxx/2.jpg",
                        },
                    ],
                )
            self.assertNotIn("api_key", request.url.params)
            self.assertNotIn("user_id", request.url.params)
            if request.url.host == "wimg.rule34.xxx":
                self.assertEqual(request.url.path, "/2.jpg")
                return httpx.Response(
                    200, content=b"photo", headers={"Content-Type": "image/jpeg"}
                )
            self.assertIn(
                request.url.host, {"konachan.net", "gelbooru.com", "e621.net"}
            )
            return httpx.Response(503)

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with (
            patch.object(booru, "RULE34_USER_ID", "42"),
            patch.object(booru, "RULE34_API_KEY", "test-key"),
            patch.object(booru.random, "randint", return_value=3),
            patch.object(booru.httpx, "AsyncClient", return_value=client),
        ):
            result = await booru.search_and_download_booru_image(
                "hatsune miku, solo", seen=set()
            )
        self.assertEqual(result[2:], ("Rule34", "Rule34:2", False))

    def test_without_rule34_key_non_pony_search_keeps_other_sources(self):
        with patch.object(booru, "RULE34_API_KEY", ""):
            self.assertEqual(
                booru._sources(["ponytail"]), ("Gelbooru", "Konachan", "e621")
            )
