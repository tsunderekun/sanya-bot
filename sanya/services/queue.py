import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from aiogram.types import Message

from ..config import ADMIN_IDS, MAX_QUEUE_SIZE, USER_COOLDOWN_SECONDS
from ..intent import OS_COMMAND_REQUEST_PATTERN, PROMPT_INJECTION_PATTERN, Mode
from .search import SearchEvidence

logger = logging.getLogger(__name__)


class EnqueueResult(Enum):
    ACCEPTED = "accepted"
    FULL = "full"
    COOLDOWN = "cooldown"
    NO_AUTHOR = "no_author"

    def rejection_message(self) -> str | None:
        return {
            EnqueueResult.FULL: "Бот сейчас занят: очередь заполнена. Попробуй чуть позже.",
            EnqueueResult.COOLDOWN: "Слишком частые запросы. Подожди немного и повтори.",
            EnqueueResult.NO_AUTHOR: "Сейчас не могу принять запрос.",
        }.get(self)


@dataclass(frozen=True, slots=True)
class Request:
    message: Message
    enqueued_at: float
    force_search_query: str | None = None
    force_image_query: str | None = None
    force_booru_query: str | None = None
    is_proactive: bool = False
    force_gpu_query: str | None = None
    booru_confirmed: bool = False
    slop: bool = False
    force_generation: tuple[Mode, str] | None = None
    prompt_idea: bool = False
    custom_trigger: bool = False
    prefetched_search: asyncio.Task[SearchEvidence] | None = None

    @property
    def kind(self) -> str:
        if self.prompt_idea:
            return "prompt_idea"
        if self.force_gpu_query:
            return "gpu_review"
        if self.force_booru_query:
            return "slop" if self.slop else "booru"
        if self.force_image_query:
            return "image"
        if self.force_search_query:
            return "search"
        if self.force_generation:
            return "code" if self.force_generation[0] is Mode.CODE else "prompt"
        return "proactive" if self.is_proactive else "chat"


class RequestQueue:
    def __init__(self, search: Callable[[str], Awaitable[SearchEvidence]] | None = None):
        self._queue: asyncio.Queue[Request] = asyncio.Queue(maxsize=max(1, MAX_QUEUE_SIZE))
        self._search = search
        self._search_slots = asyncio.Semaphore(2)
        self._search_tasks: set[asyncio.Task[SearchEvidence]] = set()
        self._last_request: dict[int, float] = {}
        self.last_proactive: dict[int, float] = {}
        self.proactive_history: dict[int, list[float]] = {}

    async def enqueue(self, message: Message, **kwargs) -> EnqueueResult:
        if not message.from_user:
            return EnqueueResult.NO_AUTHOR
        if self._queue.full():
            return EnqueueResult.FULL
        uid = message.from_user.id
        now = monotonic()
        # A confirmation resumes an already rate-limited request.
        limited = uid not in ADMIN_IDS and not (
            kwargs.get("is_proactive") or kwargs.get("booru_confirmed")
        )
        if (
            limited
            and now - self._last_request.get(uid, float("-inf")) < USER_COOLDOWN_SECONDS
        ):
            return EnqueueResult.COOLDOWN
        query = kwargs.get("force_search_query")
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        # Leave commands with mentions in their arguments to normal chat validation.
        if (
            self._search is not None
            and query
            and "@" not in query
            and not OS_COMMAND_REQUEST_PATTERN.search(text)
            and not PROMPT_INJECTION_PATTERN.search(text)
        ):
            task = asyncio.create_task(self._prefetch(self._search, query))
            self._search_tasks.add(task)
            task.add_done_callback(self._search_tasks.discard)
            kwargs["prefetched_search"] = task
        self._queue.put_nowait(Request(message, enqueued_at=now, **kwargs))
        if limited:
            self._last_request[uid] = now
        return EnqueueResult.ACCEPTED

    async def _prefetch(
        self, search: Callable[[str], Awaitable[SearchEvidence]], query: str
    ) -> SearchEvidence:
        async with self._search_slots:
            started = monotonic()
            outcome = "ok"
            try:
                result = await search(query)
                if not (result.snippets or result.official_answer):
                    outcome = "empty"
                return result
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception:
                outcome = "error"
                return SearchEvidence("")
            finally:
                logger.info(
                    "request_search outcome=%s lookup_ms=%.1f",
                    outcome, (monotonic() - started) * 1000,
                )

    async def close(self):
        tasks = tuple(self._search_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def work(self, process):
        while True:
            item = await self._queue.get()
            started_at = monotonic()
            outcome = "ok"
            try:
                await process(
                    message=item.message,
                    force_search_query=item.force_search_query,
                    force_image_query=item.force_image_query,
                    force_booru_query=item.force_booru_query,
                    is_proactive=item.is_proactive,
                    **({"booru_confirmed": True} if item.booru_confirmed else {}),
                    **({"slop": True} if item.slop else {}),
                    **(
                        {"force_generation": item.force_generation}
                        if item.force_generation
                        else {}
                    ),
                    **(
                        {"force_gpu_query": item.force_gpu_query}
                        if item.force_gpu_query
                        else {}
                    ),
                    **({"prompt_idea": True} if item.prompt_idea else {}),
                    **({"custom_trigger": True} if item.custom_trigger else {}),
                    **(
                        {"prefetched_search": item.prefetched_search}
                        if item.prefetched_search is not None
                        else {}
                    ),
                )
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as exc:
                outcome = "error"
                # Exception messages may contain request text or URLs.
                logger.error("Ошибка обработки запроса: %s", type(exc).__name__)
            finally:
                if item.prefetched_search is not None and not item.prefetched_search.done():
                    item.prefetched_search.cancel()
                self._queue.task_done()
                logger.info(
                    "request_queue kind=%s outcome=%s wait_ms=%.1f processing_ms=%.1f",
                    item.kind,
                    outcome,
                    (started_at - item.enqueued_at) * 1000,
                    (monotonic() - started_at) * 1000,
                )
