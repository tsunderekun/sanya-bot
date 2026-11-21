from dataclasses import dataclass

DEFAULT_LEVEL = 1
TONE_LABELS = ("дружелюбно", "как раньше", "поострее", "жёсткий стёб")
LORE_LABELS = ("только по запросу", "к месту", "активно", "максимум по теме")
STICKER_LABELS = ("выкл", "10%", "25%", "50%")


@dataclass(frozen=True)
class ReplyStyle:
    tone: int = DEFAULT_LEVEL
    lore: int = DEFAULT_LEVEL


@dataclass(frozen=True)
class ChatReplySettings:
    mode: str = "normal"
    style: ReplyStyle = ReplyStyle()
    sticker_level: int | None = None
    voice_chance: int = 0
