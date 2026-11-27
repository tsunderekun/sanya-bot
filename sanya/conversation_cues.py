"""Explicit conversational cues shared by replies and reactions."""

import re

_PUNCHLINE_DETAIL = re.compile(
    r"\b(?:ну\s+и\s+прикол\s+ты\s+выдал|"
    r"это\s+(?:была\s+)?(?:тупая|глупая|плохая|неудачная)\s+шутка\b)"
    r"|\bты\s+(?:зануда|достал\w*|надоел\w*)\b"
    r"|\b(?:твой|твоя|твои|твоё|твоего)\s+(?:квартир\w*|иде\w*|шутк\w*|вещ\w*)"
    r"|\bскорость\s+обслуживания\b",
    re.I,
)

DEADPAN_PUNCHLINE = re.compile(
    r"\b(?:тупая\s+шутка\s+дня|плохой\s+каламбур\s+дня)\s*:\s*\w"
    r"|\bчто\s+сказал\s+один\s+[^?!.\n]{1,60}\s+другому\s*\?\s*[—–-]\s*\w",
    re.I,
)
DOUBLE_ENTENDRE = re.compile(
    r"\b(?:если\s+(?:ты\s+)?понимаешь|ну\s+ты\s+понял[аи]?)\s*,\s*"
    r"(?:о\s+ч[её]м|на\s+что\s+намекаю)\b"
    r"|\bне\s+спрашивай\s*,\s*что\s+было\s+дальше\s*[;:]-?\)",
    re.I,
)

_TOPIC_PIVOT = re.compile(
    r"^(?:сменим тему|давай о другом|другой вопрос)\s*[:.!?—-]\s*\w", re.I
)
_FOLLOW_UP = re.compile(
    r"^(?:а|и|но|ну|тогда|если|так|вот|это|он[аи]?|оно|там|потом|поэтому|"
    r"после\s+этого|с\s+тех\s+пор)\b",
    re.I,
)
_HINT_CUE = re.compile(r"если\s+(?:ты\s+)?понимаешь|ну\s+ты\s+понял[аи]?", re.I)


def starts_new_topic(text: str) -> bool:
    """Ignore old turns only when there is a strong cue, not by message length."""
    text = text.strip()
    if _TOPIC_PIVOT.match(text) or DEADPAN_PUNCHLINE.match(text):
        return True
    hint = DOUBLE_ENTENDRE.search(text)
    return bool(
        hint
        and not _FOLLOW_UP.match(text)
        and len(re.findall(r"\w+", text[:hint.start()])) >= 4
    )


def punchline_targeted_at_person(text: str) -> bool:
    """Flag a joke reply that insults its author rather than playing with the setup."""
    return bool(_PUNCHLINE_DETAIL.search(text))


def fresh_topic_reply_is_on_topic(text: str, *, is_hint: bool) -> bool:
    """Reject generic clarifications or insults when the user made a joke/hint."""
    if _PUNCHLINE_DETAIL.search(text):
        return False
    if is_hint:
        return True
    normalized = re.sub(r"[^\w\s]", " ", text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if normalized in {"вот как", "ага", "понятно", "ясно", "хорошо"}:
        return False
    words = re.findall(r"\w+", normalized)
    if len(words) < 5:
        return False
    return True
