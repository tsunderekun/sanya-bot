import asyncio
import base64
import logging
import re

import httpx

from .config import (
    ENABLE_THINKING,
    MODEL_NAME,
    NUM_PREDICT_CHAT,
    NUM_PREDICT_THINK,
    OPENAI_BASE_URL,
    VISION_MODEL_NAME,
)
from .memory import TOKEN_TARGET, Turn, fit_prompt
from .prompts import SYSTEM_PROMPT

log = logging.getLogger(__name__)
thought_log = logging.getLogger("sanya.thinking")
THINK_TAG = re.compile(
    r"<\s*(/?)\s*think\b[^>]*>|<\|channel>[^\r\n]*?(?=<channel\|>|\r?\n|$)|<channel\|>|<\s*/?\s*think\b[^>]*$",
    re.I,
)


def isolate_thinking(text: str) -> tuple[str, str]:
    visible, hidden = [], []
    depth = 0
    channel = None
    offset = 0
    has_open = False
    for mark in THINK_TAG.finditer(text):
        fragment = text[offset : mark.start()]
        tag = mark[0].lower()
        if mark[1] and not depth and not has_open:
            hidden.extend(visible)
            visible.clear()
            hidden.append(fragment)
            has_open = True
        else:
            (hidden if depth or channel == "thought" else visible).append(fragment)
        if tag.startswith("<|channel>"):
            name = tag.removeprefix("<|channel>").strip()
            channel = "final" if name == "final" else "thought"
        elif tag == "<channel|>":
            if channel == "final":
                channel = None
            # A thought channel without an explicit final marker fails closed.
        elif mark[1]:
            depth = max(0, depth - 1)
        else:
            depth += 1
            has_open = True
        offset = mark.end()
    (hidden if depth or channel == "thought" else visible).append(text[offset:])
    return "".join(visible).strip(), "".join(hidden).strip()


class Ollama:
    def __init__(self, client: httpx.AsyncClient):
        self._client = client
        self._lock = asyncio.Lock()
        self._endpoint = OPENAI_BASE_URL.rstrip("/").removesuffix("/v1") + "/api/chat"

    async def _post(self, payload: dict, timeout: float) -> tuple[str, str]:
        response = await self._client.post(
            self._endpoint, json=payload, timeout=timeout
        )
        response.raise_for_status()
        data = response.json()
        message = data["message"]
        content, inline = isolate_thinking(message.get("content") or "")
        thought = message.get("thinking") or ""
        if thought or inline:
            thought_log.debug(
                "model=%s thinking=%r inline=%r", payload["model"], thought, inline
            )
        count = data.get("prompt_eval_count") or 0
        log.info(
            "Ollama model=%s think=%s prompt_eval=%s eval=%s done=%s",
            payload["model"],
            payload.get("think"),
            count,
            data.get("eval_count"),
            data.get("done_reason"),
        )
        if count > TOKEN_TARGET and payload["model"] == MODEL_NAME:
            log.warning("Ollama prompt_eval_count=%s exceeds %s", count, TOKEN_TARGET)
        return content, data.get("done_reason") or ""

    async def generate(
        self,
        messages: list[Turn],
        is_detailed_request: bool = False,
        is_coding_request: bool = False,
        *,
        complete: bool = False,
    ) -> str:
        try:
            context = fit_prompt({"role": "system", "content": SYSTEM_PROMPT}, messages)
        except ValueError:
            log.warning("Текущий запрос не помещается в бюджет")
            return "Запрос целиком не влезает\nКинь его частями тогда"
        on, off, temp = (
            (4500, 3500, 0.60)
            if is_coding_request
            else (
                (3200, 2400, 0.70)
                if is_detailed_request
                else (NUM_PREDICT_THINK, NUM_PREDICT_CHAT, 0.75)
            )
        )
        async with self._lock:
            try:
                for think in (True, False) if ENABLE_THINKING else (False,):
                    content, reason = await self._post(
                        {
                            "model": MODEL_NAME,
                            "messages": context,
                            "stream": False,
                            "think": think,
                            "options": {
                                "num_ctx": 16384,
                                "num_predict": on if think else off,
                                "temperature": temp,
                                "top_p": 0.90,
                                "repeat_penalty": 1.20,
                                "repeat_last_n": 160,
                            },
                        },
                        timeout=180.0,
                    )
                    if content:
                        return "" if complete and reason == "length" else content
                    if think:
                        log.info("Пустой видимый ответ Ollama, повтор с think=False")
            except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
                log.exception("Ошибка запроса Ollama")
                return "Там сетка антон походу\nПусть сначала сетка очухается"
        return ""

    async def describe_image(self, image: bytes) -> str:
        payload = {
            "model": VISION_MODEL_NAME,
            "stream": False,
            "keep_alive": 0,
            "messages": [
                {
                    "role": "user",
                    "content": "Describe visible text, objects, hardware, damage, people and memes in this image.",
                    "images": [base64.b64encode(image).decode("ascii")],
                }
            ],
            "options": {"num_ctx": 2048, "temperature": 0.2, "num_predict": 300},
        }
        try:
            async with self._lock:
                content, _ = await self._post(payload, timeout=45.0)
            return content
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            log.exception("Ошибка vision-анализа")
            return ""
