from dataclasses import dataclass

DEFAULT_LEVEL = 1
TONE_LABELS = ("дружелюбно", "как раньше", "поострее", "жёсткий стёб")
LORE_LABELS = ("только по запросу", "к месту", "активно", "максимум по теме")


@dataclass(frozen=True)
class ReplyStyle:
    tone: int = DEFAULT_LEVEL
    lore: int = DEFAULT_LEVEL


@dataclass(frozen=True)
class ChatReplySettings:
    mode: str = "normal"
    style: ReplyStyle = ReplyStyle()
