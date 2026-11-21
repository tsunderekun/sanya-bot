"""Explicit conversational cues shared by replies and reactions."""

import re

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
