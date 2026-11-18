import asyncio
import logging
from dataclasses import dataclass
from time import monotonic

from aiogram.types import Message

from ..config import ADMIN_IDS, MAX_QUEUE_SIZE, USER_COOLDOWN_SECONDS


@dataclass(frozen=True, slots=True)
class Request:
    message: Message
    force_search_query: str | None = None
    force_image_query: str | None = None
    force_booru_query: str | None = None
    is_proactive: bool = False
    force_price_query: str | None = None


class RequestQueue:
    def __init__(self):
        self._queue: asyncio.Queue[Request] = asyncio.Queue(
            maxsize=max(1, MAX_QUEUE_SIZE)
        )
        self._last_request: dict[int, float] = {}
        self.last_proactive: dict[int, float] = {}
        self.proactive_history: dict[int, list[float]] = {}

    async def enqueue(self, message: Message, **kwargs) -> bool:
        if not message.from_user or self._queue.full():
            return False
        uid = message.from_user.id
        now = monotonic()
        limited = uid not in ADMIN_IDS and not kwargs.get("is_proactive")
        if (
            limited
            and now - self._last_request.get(uid, float("-inf")) < USER_COOLDOWN_SECONDS
        ):
            return False
        self._queue.put_nowait(Request(message, **kwargs))
        if limited:
            self._last_request[uid] = now
        return True

    async def work(self, process):
        while True:
            item = await self._queue.get()
            try:
                await process(
                    message=item.message,
                    force_search_query=item.force_search_query,
                    force_image_query=item.force_image_query,
                    force_booru_query=item.force_booru_query,
                    is_proactive=item.is_proactive,
                    **(
                        {"force_price_query": item.force_price_query}
                        if item.force_price_query
                        else {}
                    ),
                )
            except Exception:
                logging.exception("Ошибка обработки сообщения из очереди")
            finally:
                self._queue.task_done()
