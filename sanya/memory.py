"""Скользящее окно и бюджет рендера Ollama, без удаления записей из базы."""

import json
from collections.abc import Iterable

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


def build_turn(text: str, *, notes: list[str], search: str = "") -> Turn:
    parts = ["Сообщение:\n" + text]
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
