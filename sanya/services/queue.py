import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from time import monotonic

from aiogram.types import Message

from ..config import ADMIN_IDS, MAX_QUEUE_SIZE, USER_COOLDOWN_SECONDS
from ..intent import Mode

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
    def __init__(self):
        self._queue: asyncio.Queue[Request] = asyncio.Queue(maxsize=max(1, MAX_QUEUE_SIZE))
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
        self._queue.put_nowait(Request(message, enqueued_at=now, **kwargs))
        if limited:
            self._last_request[uid] = now
        return EnqueueResult.ACCEPTED

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
                )
            except asyncio.CancelledError:
                outcome = "cancelled"
                raise
            except Exception as exc:
                outcome = "error"
                # Exception messages may contain request text or URLs.
                logger.error("Ошибка обработки запроса: %s", type(exc).__name__)
            finally:
                self._queue.task_done()
                logger.info(
                    "request_queue kind=%s outcome=%s wait_ms=%.1f processing_ms=%.1f",
                    item.kind,
                    outcome,
                    (started_at - item.enqueued_at) * 1000,
                    (monotonic() - started_at) * 1000,
                )
