import re

from aiogram.enums import MessageEntityType
from aiogram.types import Message


def self_fact_prefix(chat_id: int) -> str:
    return f"self_report:{chat_id}:"


_GPU = r"(?:rtx\s*\d{4}(?:\s*ti)?(?:\s*super)?|gtx\s*\d{3,4}(?:\s*ti)?|rx\s*\d{3,4}(?:\s*xtx?)?|arc\s*[ab]\d{3})"
_CPU = r"(?:ryzen\s*[3579]\s*\d{4}(?:x3d|[a-z]{1,2})?|(?:core\s*)?i[3579][-\s]\d{4,5}[a-z]{0,2})"
_PLATFORM = r"(?:a[35]20|b[34567][56]0|x(?:79|99|[345678][67]0e?)|[zh][345678][179]0|am[45]|lga\s*(?:115[0156]|1200|1700|1851|2011(?:-v[34])?))"
_DEVICE = r"(?:thinkpad\s+[texlp]\d{2,3}(?:s)?(?:\s+gen\s*\d{1,2})?|galaxy\s+s\d{2}(?:\s+ultra)?|iphone\s+\d{1,2}(?:\s+pro(?:\s+max)?|\s+plus)?|macbook\s+(?:air|pro)\s+m[1-5])"
_OS = r"(?:windows\s*1[01]|вин(?:да|довс)\s*1[01]|arch(?:\s+linux)?|ubuntu(?:\s+\d{2}\.\d{2})?|debian|fedora|bazzite|macos)"
_GAME = r"(?:cs\s*1\.6|кс\s*1\.6|cs\s*2|кс\s*2|cs\s*:\s*s|dota\s*2|valorant|валорант|minecraft|майнкрафт|stalker|сталкер|tarkov|тарков|beamng(?:\.drive)?)"

_MODELS = tuple(
    (re.compile(pattern, re.I), key, label)
    for pattern, key, label in (
        (_GPU, "hardware_gpu", "Видеокарта"),
        (_CPU, "hardware_cpu", "Процессор"),
        (_PLATFORM, "hardware_platform", "Платформа"),
        (_DEVICE, "device", "Устройство"),
        (_OS, "os", "ОС"),
        (_GAME, "game", "Играет в"),
    )
)
_SETUP = re.compile(
    r"(?:у меня(?:\s+(?:стоит|стоят|сейчас))?|(?:я\s+)?(?:поставил[аи]?|установил[аи]?|купил[аи]?|взял[аи]?)\s+себе)"
    r"(?:\s+|\s*[:—-]\s*)(.+)",
    re.I,
)
_PLAY = re.compile(r"(?:я\s+)?(?:играю|катаю|гоняю)\s+в\s+(.+)", re.I)
_SEPARATOR = re.compile(r"\s*(?:[,+]|\s+и\s+)\s*", re.I)
_QUOTE_ENTITIES = {
    MessageEntityType.BLOCKQUOTE,
    MessageEntityType.EXPANDABLE_BLOCKQUOTE,
    MessageEntityType.CODE,
    MessageEntityType.PRE,
}


def extract_self_facts(text: str) -> tuple[tuple[str, str], ...]:
    """Recognize only complete, explicit self-reports with allowlisted values."""
    text = text.strip()
    if not text or len(text) > 180 or any(c in text for c in "\r\n?？"):
        return ()
    text = text.removesuffix(".").removesuffix("!").strip()
    play = _PLAY.fullmatch(text)
    match = play or _SETUP.fullmatch(text)
    if not match:
        return ()
    models = _SEPARATOR.split(match[1])
    if len(models) > 4:
        return ()
    facts = {}
    for model in models:
        model = " ".join(model.split()).lower()
        for pattern, key, label in _MODELS:
            if (key == "game") == bool(play) and pattern.fullmatch(model):
                if key in facts:
                    return ()
                value = model.upper() if key == "hardware_gpu" else model
                facts[key] = f"{label}: {value}"
                break
        else:
            return ()
    return tuple(facts.items())


def message_self_facts(message: Message, text: str) -> tuple[tuple[str, str], ...]:
    if (
        getattr(message, "forward_origin", None)
        or getattr(message, "sender_chat", None)
        or getattr(message, "via_bot", None)
        or any(
            entity.type in _QUOTE_ENTITIES
            for entity in (
                getattr(message, "entities", None)
                or getattr(message, "caption_entities", None)
                or ()
            )
        )
    ):
        return ()
    return extract_self_facts(text)
