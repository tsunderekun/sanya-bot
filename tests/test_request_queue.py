import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sanya.services.queue import EnqueueResult, RequestQueue
from sanya.services.search import SearchEvidence


class RequestQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_prefetch_overlaps_chat_but_keeps_reply_order_and_cooldown(self):
        started = asyncio.Event()
        release = asyncio.Event()
        fetched = asyncio.Event()
        replies = []
        first = SimpleNamespace(from_user=SimpleNamespace(id=1))
        second = SimpleNamespace(from_user=SimpleNamespace(id=2), text="/s test")
        third = SimpleNamespace(from_user=SimpleNamespace(id=3), text="/s rejected")

        async def lookup(query):
            fetched.set()
            return SearchEvidence("sources", ("https://example.com/item",))

        async def process(*, message, prefetched_search=None, **kwargs):
            if message is first:
                started.set()
                await release.wait()
            else:
                self.assertTrue(prefetched_search.done())
                self.assertEqual(
                    await prefetched_search,
                    SearchEvidence("sources", ("https://example.com/item",)),
                )
            replies.append(message)

        search = AsyncMock(side_effect=lookup)
        with (
            patch("sanya.services.queue.MAX_QUEUE_SIZE", 1),
            patch("sanya.services.queue.USER_COOLDOWN_SECONDS", 60),
            patch("sanya.services.queue.ADMIN_IDS", set()),
        ):
            queue = RequestQueue(search=search)
            self.assertIs(await queue.enqueue(first), EnqueueResult.ACCEPTED)
            worker = asyncio.create_task(queue.work(process))
            try:
                await asyncio.wait_for(started.wait(), 2)
                self.assertIs(await queue.enqueue(first, force_search_query="cooldown"), EnqueueResult.COOLDOWN)
                self.assertIs(await queue.enqueue(second, force_search_query="test"), EnqueueResult.ACCEPTED)
                self.assertIs(await queue.enqueue(third, force_search_query="full"), EnqueueResult.FULL)
                await asyncio.wait_for(fetched.wait(), 2)
                self.assertEqual(replies, [])
                release.set()
                await asyncio.wait_for(queue._queue.join(), 2)
                self.assertEqual(replies, [first, second])
                search.assert_awaited_once_with("test")
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)
                await queue.close()

    async def test_slow_request_preserves_order_and_reports_wait_and_rejections(self):
        started = asyncio.Event()
        release = asyncio.Event()
        now = 10.0
        first = SimpleNamespace(from_user=SimpleNamespace(id=1), text="private text")
        second = SimpleNamespace(from_user=SimpleNamespace(id=2))
        third = SimpleNamespace(from_user=SimpleNamespace(id=3))

        async def process(**kwargs):
            nonlocal now
            if kwargs["message"] is first:
                started.set()
                await release.wait()
            else:
                now += 0.25

        processor = AsyncMock(side_effect=process)
        with (
            patch("sanya.services.queue.MAX_QUEUE_SIZE", 1),
            patch("sanya.services.queue.USER_COOLDOWN_SECONDS", 5),
            patch("sanya.services.queue.ADMIN_IDS", set()),
            patch("sanya.services.queue.monotonic", side_effect=lambda: now),
            self.assertLogs("sanya.services.queue", level="INFO") as logs,
        ):
            queue = RequestQueue()
            self.assertIs(await queue.enqueue(first), EnqueueResult.ACCEPTED)
            worker = asyncio.create_task(queue.work(processor))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                now = 11.0
                self.assertIs(
                    await queue.enqueue(first, force_gpu_query="RTX 3070"),
                    EnqueueResult.COOLDOWN,
                )
                self.assertIs(
                    await queue.enqueue(second, force_gpu_query="RTX 3070"),
                    EnqueueResult.ACCEPTED,
                )
                self.assertIs(await queue.enqueue(third), EnqueueResult.FULL)
                processor.assert_awaited_once()
                now = 13.0
                release.set()
                await asyncio.wait_for(queue._queue.join(), timeout=2)
                self.assertEqual(
                    [call.kwargs["message"] for call in processor.await_args_list],
                    [first, second],
                )
                self.assertEqual(processor.await_args.kwargs["force_gpu_query"], "RTX 3070")
            finally:
                worker.cancel()
                await asyncio.gather(worker, return_exceptions=True)

        self.assertEqual(
            [record.getMessage() for record in logs.records],
            [
                "request_queue kind=chat outcome=ok wait_ms=0.0 processing_ms=3000.0",
                "request_queue kind=gpu_review outcome=ok wait_ms=2000.0 processing_ms=250.0",
            ],
        )
