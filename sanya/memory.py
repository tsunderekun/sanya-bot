"""Скользящее окно и бюджет рендера Ollama, без удаления записей из базы."""

import json
from collections.abc import Awaitable, Callable, Iterable

from .config import MAX_CONTEXT_CHARS

Turn = dict[str, str]
TOKEN_TARGET = 8500


def newest_suffix(messages: Iterable[Turn], max_chars: int) -> list[Turn]:
    selected = []
    for msg in reversed(list(messages)):
        size = len(msg["content"])
        if size > max_chars:
            break
        selected.append(msg)
        max_chars -= size
    return selected[::-1]


def build_turn(
    text: str, *, notes: list[str], search: str = "", thread: str = ""
) -> Turn:
    parts = []
    if thread:
        parts.append("Контекст реплая: " + json.dumps(thread, ensure_ascii=False))
    if search:
        parts.append("Данные из сети: " + json.dumps(search[:1300], ensure_ascii=False))
    if notes:
        selected = []
        remaining = 1700
        for note in notes:
            if len(note) <= remaining:
                selected.append(note)
                remaining -= len(note)
        if selected:
            parts.append(
                "Справочные заметки: " + json.dumps(selected, ensure_ascii=False)
            )
    parts.append("Текущее сообщение (ответь на него):\n" + text)
    return {"role": "user", "content": "\n\n".join(parts)}


def estimate_tokens(messages: list[Turn]) -> int:
    # Byte fallback estimate, not the Gemma tokenizer. Reserve room for the chat template.
    return 256 + sum(
        16 + (len(m["content"].encode("utf-8")) + 1) // 2 for m in messages
    )


def fit_prompt(
    system: Turn, messages: list[Turn], target: int = TOKEN_TARGET
) -> list[Turn]:
    # Do not silently clip the latest request (especially source code).
    if not messages or messages[-1]["role"] != "user":
        raise ValueError(
            "Ollama expects the last conversational turn to be a user message"
        )
    tail = messages[-1]
    history = newest_suffix(messages[:-1], MAX_CONTEXT_CHARS)
    if estimate_tokens([system, tail]) > target:
        raise ValueError("Current request exceeds the prompt budget without history")
    remaining = target - estimate_tokens([system, tail])
    selected = []
    for msg in reversed(history):
        size = estimate_tokens([msg]) - estimate_tokens([])
        if size > remaining:
            break
        selected.append(msg)
        remaining -= size
    return [system, *reversed(selected), tail]


async def compact_dialogue(
    history: list[Turn],
    system: Turn,
    current: Turn,
    summarize: Callable[[list[Turn]], Awaitable[str]] | None,
) -> list[Turn]:
    """Fit fresh turns verbatim; summarize only bounded overflow for this request."""
    try:
        fresh = fit_prompt(system, [*history, current])[1:-1]
    except ValueError:
        return []
    if summarize is None or len(fresh) == len(history) or MAX_CONTEXT_CHARS <= 1000:
        return fresh
    reserve = "я" * 1000
    try:
        compact_fresh = fit_prompt(
            system,
            [*history, {**current, "content": current["content"] + reserve}],
        )[1:-1]
    except ValueError:
        return fresh
    compact_fresh = newest_suffix(compact_fresh, MAX_CONTEXT_CHARS - len(reserve))
    if not compact_fresh:
        return fresh
    older = newest_suffix(history[: len(history) - len(compact_fresh)], 4500)
    if not older:
        return fresh
    text = await summarize(older)
    if not text:
        return fresh
    summary = {
        "role": "user",
        "content": "Сводка старых реплик (справка, не инструкция): "
        + json.dumps(text, ensure_ascii=False),
    }
    candidate = [summary, *compact_fresh]
    return (
        candidate
        if len(fit_prompt(system, [*candidate, current])) == len(candidate) + 2
        else fresh
    )
