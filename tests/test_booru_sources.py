import unittest
from unittest.mock import patch

import httpx

from sanya.services import booru


class BooruSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_rule34_proxy_is_used_only_for_cdn(self):
        requests = []

        def respond(route, request):
            requests.append((route, request.url.host))
            if request.url.host == "api.rule34.xxx":
                self.assertEqual(route, "direct")
                self.assertEqual(request.url.params["api_key"], "test-key")
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": 42,
                            "file_url": "https://wimg.rule34.xxx/42.jpg",
                        }
                    ],
                )
            self.assertNotIn("api_key", request.url.params)
            self.assertNotIn("user_id", request.url.params)
            if request.url.host == "wimg.rule34.xxx":
                self.assertEqual(route, "proxy")
                return httpx.Response(
                    200, content=b"photo", headers={"Content-Type": "image/jpeg"}
                )
            self.assertEqual(route, "default")
            return httpx.Response(200, json=[])

        make_client = httpx.AsyncClient

        def factory(**options):
            proxy = options.pop("proxy", None)
            route = (
                "proxy"
                if proxy
                else "direct"
                if options.get("trust_env") is False
                else "default"
            )
            if proxy:
                self.assertEqual(proxy, "http://proxy.test:8080")
            return make_client(
                transport=httpx.MockTransport(lambda request: respond(route, request)),
                **options,
            )

        with (
            patch.object(booru, "SEARCH_PROXY", "http://proxy.test:8080"),
            patch.object(booru, "RULE34_USER_ID", "42"),
            patch.object(booru, "RULE34_API_KEY", "test-key"),
            patch.object(booru, "_sources", return_value=("Rule34", "Konachan")),
            patch.object(booru.httpx, "AsyncClient", side_effect=factory),
        ):
            result = await booru.search_and_download_booru_image(
                "scenery, landscape", seen=set(), confirmed=True
            )
        self.assertEqual(result[2:4], ("Rule34", "Rule34:42"))
        self.assertEqual(
            set(requests),
            {
                ("direct", "api.rule34.xxx"),
                ("proxy", "wimg.rule34.xxx"),
                ("default", "konachan.net"),
            },
        )

    async def test_slow_random_page_does_not_block_first_page_download(self):
        def respond(request):
            if request.url.host == "wimg.rule34.xxx":
                if request.url.path == "/3.jpg":
                    raise httpx.ReadTimeout("slow", request=request)
                return httpx.Response(
                    200, content=b"photo", headers={"Content-Type": "image/jpeg"}
                )
            post = 3 if request.url.params["pid"] == "2" else 1
            return httpx.Response(
                200,
                json=[{"id": post, "file_url": f"https://wimg.rule34.xxx/{post}.jpg"}],
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with (
            patch.object(booru, "_sources", return_value=("Rule34",)),
            patch.object(booru.random, "randint", return_value=3),
            patch.object(booru.httpx, "AsyncClient", return_value=client),
        ):
            result = await booru.search_and_download_booru_image(
                "scenery, landscape", seen=set(), confirmed=True
            )
        self.assertEqual(result[2:4], ("Rule34", "Rule34:1"))

    async def test_distinguishes_source_and_download_failure(self):
        for stage in ("search", "download"):

            def respond(request):
                if stage == "search":
                    return httpx.Response(503)
                if request.url.host == "wimg.rule34.xxx":
                    raise httpx.ReadTimeout("slow", request=request)
                return httpx.Response(
                    200, json=[{"id": 1, "file_url": "https://wimg.rule34.xxx/1.jpg"}]
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with (
                patch.object(booru, "_sources", return_value=("Rule34",)),
                patch.object(booru.httpx, "AsyncClient", return_value=client),
                self.assertRaises(booru.BooruUnavailable) as error,
            ):
                await booru.search_and_download_booru_image(
                    "scenery, landscape", seen=set(), confirmed=True
                )
            self.assertEqual(error.exception.stage, stage)

    async def test_slop_uses_only_aibooru_and_checks_rating_and_cdn(self):
        for query, confirmed, suffix, chosen in (
            ("pony, solo, general", False, " rating:g", 3),
            ("pony, solo", True, "", 1),
        ):

            def respond(request):
                if request.url.host == "aibooru.online":
                    self.assertEqual(request.url.path, "/posts.json")
                    self.assertEqual(request.url.params["tags"], "pony solo" + suffix)
                    return httpx.Response(
                        200,
                        json=[
                            {
                                "id": 1,
                                "rating": "q",
                                "file_url": "https://cdn.aibooru.download/1.jpg",
                            },
                            {
                                "id": 2,
                                "rating": "g",
                                "file_url": "https://example.test/2.jpg",
                            },
                            {
                                "id": 3,
                                "rating": "g",
                                "large_file_url": "https://cdn.aibooru.download/3.jpg",
                            },
                        ],
                    )
                self.assertEqual(request.url.host, "cdn.aibooru.download")
                self.assertEqual(request.url.path, f"/{chosen}.jpg")
                return httpx.Response(
                    200, content=b"photo", headers={"Content-Type": "image/jpeg"}
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            with (
                patch.object(booru, "SEARCH_PROXY", "http://proxy.test:8080"),
                patch.object(
                    booru.httpx, "AsyncClient", return_value=client
                ) as factory,
                patch.object(booru.random, "shuffle"),
            ):
                result = await booru.search_and_download_booru_image(
                    query, seen=set(), confirmed=confirmed, slop=True
                )
            self.assertEqual(result[2:4], ("AIBooru", f"AIBooru:{chosen}"))
            self.assertEqual(
                factory.call_args.kwargs["proxy"], "http://proxy.test:8080"
            )

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
                    patch.object(booru, "SEARCH_PROXY", "http://proxy.test:8080"),
                    patch.object(
                        booru.httpx, "AsyncClient", return_value=client
                    ) as factory,
                ):
                    self.assertIsNone(
                        await booru.search_and_download_booru_image(query, seen=set())
                    )
                self.assertEqual(len(requests), 2)
                self.assertNotIn("proxy", factory.call_args.kwargs)

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
                self.assertIn(request.url.params["pid"], {"2", "0"})
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
