import re


def normalize_phrase(value: str) -> str:
    return " ".join(value.casefold().split())


def valid_trigger_phrase(value: str) -> bool:
    normalized = normalize_phrase(value)
    return (
        2 <= len(normalized) <= 48
        and len(normalized.split()) <= 8
        and any(char.isalnum() for char in normalized)
        and not normalized.startswith("/")
    )


def matches_trigger_phrase(text: str, phrase: str) -> bool:
    normalized_text = normalize_phrase(text)
    normalized_phrase = normalize_phrase(phrase)
    if not normalized_phrase:
        return False
    pattern = re.escape(normalized_phrase).replace(r"\ ", r"\s+")
    pattern = r"(?<!\w)" + pattern + r"(?!\w)"
    return bool(re.search(pattern, normalized_text, re.UNICODE))
