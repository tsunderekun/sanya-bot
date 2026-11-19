"""Public summaries of local memes; never read private logs at runtime."""

import re

_LORE_QUESTION = re.compile(
    r"\b(?:расскажи|поясни|объясни|что\s+за|ч[её]\s+за|что\s+значит|"
    r"что\s+такое|в\s+ч[её]м\s+(?:мем|рофл|прикол)|откуда|помнишь|"
    r"истори[яю]|лор|смысл)\b",
    re.IGNORECASE,
)
_SHORT_QUESTION = re.compile(
    r"(?:спермер[\s,:]+)?(?:а\s+)?(?:про\s+)?([а-яё]+)\s*\?+",
    re.IGNORECASE,
)
_MEMES = (
    (
        r"матрас(?:а|е|у|ом|ы|ов)?",
        "«Хрустящий матрас» — мем конфы про матрас, который якобы впитал всё насквозь, до самых пружин. Отсюда и «хрустящий»: преувеличили так, будто он уже не мягкий, а сухарь.",
    ),
    (
        r"слойпер\w*",
        "Слойпер — это «спойлер» с опечаткой на 4PDA. Потом так стали звать кривые сборки, брак и ИИ-слоп. Не путай со scatter.",
    ),
    (
        r"кезднуть",
        "Кезднуть — по контексту либо зайти в войс, либо пукнуть, либо взорваться. Если говорят «минут через 30 можно кезднуть», речь про Дискорд.",
    ),
)


def asks_about_lore(text: str) -> bool:
    return bool(_SHORT_QUESTION.fullmatch(text.strip()) or _LORE_QUESTION.search(text))


def meme_lore_reply(text: str) -> str:
    if short := _SHORT_QUESTION.fullmatch(text.strip()):
        text = short[1]
    elif not _LORE_QUESTION.search(text):
        return ""
    stories = [
        story
        for pattern, story in _MEMES
        if re.search(rf"\b{pattern}\b", text, re.IGNORECASE)
    ]
    return "\n\n".join(stories)
