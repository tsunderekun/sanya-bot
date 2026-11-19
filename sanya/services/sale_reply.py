import re

_SALE = re.compile(
    r"\b(?:сли(?:ть|вать|ваешь)|соль[её]шь|прод(?:ать|авать|ашь|адим)|выстав(?:ить|лять|ишь)|выставляешь)\b",
    re.IGNORECASE,
)
_MARKET = re.compile(r"\b(?:авито|avito)\b", re.IGNORECASE)
_VAGUE = r"(?:эту\s+хуйню|хуйню|эту\s+штуку|это|вот\s+это)"
_PRICE = r"\d+(?:[.,]\d+)?\s*(?:к|k|тыс\.?|₽|рублей)?"
_ON_MARKET = r"на\s+(?:авито|avito)"
_TERMS = rf"(?:за\s+{_PRICE}\s+)?(?:кебекам\s+)?"
_UNKNOWN = re.compile(
    rf"(?:спермер[\s,:]+)?(?:когда|как|стоит\s+ли|можно\s+ли)\s+"
    rf"(?:{_VAGUE}\s+{_TERMS}(?:{_SALE.pattern}\s+{_ON_MARKET}|{_ON_MARKET}\s+{_SALE.pattern})|"
    rf"{_SALE.pattern}\s+{_VAGUE}(?:\s+за\s+{_PRICE})?\s+{_ON_MARKET})[\s?!.,]*",
    re.IGNORECASE,
)
_DISK = re.compile(
    r"\b(?:seagate|с[ие]гейт\w*|hdd|ж[её]стк\w+\s+диск\w*|винчестер\w*)\b",
    re.IGNORECASE,
)
_BROKEN = re.compile(
    r"\b(?:дохл\w*|нерабоч\w*|мертв\w*|мёртв\w*|не\s+работа\w*)\b", re.IGNORECASE
)
_WHEN = re.compile(r"^(?:спермер[\s,:]+)?когда\s+", re.IGNORECASE)
_BROKEN_DISK = re.compile(
    rf"(?:{_BROKEN.pattern}\s+{_DISK.pattern}|{_DISK.pattern}\s+{_BROKEN.pattern})",
    re.IGNORECASE,
)
_CORRECTION = re.compile(
    r"(?:ты\s+)?(?:не\s+попал\s+в\s+тему|ответил\s+не\s+по\s+теме|не\s+про\s+то|не\s+о\s+том)[\s.!?]*",
    re.IGNORECASE,
)


def grounded_sale_reply(
    text: str, *, original: str = "", has_context: bool = False
) -> str:
    """Handle narrow sale questions that need no diagnosis or model guess."""
    text = text.strip()
    if original and _CORRECTION.fullmatch(text):
        text = original.strip()
        # The original question, not our previous answer, must name the item.
        has_context = False
    if not has_context and _UNKNOWN.fullmatch(text):
        return "А что именно продаёшь?"
    if (
        _WHEN.match(text)
        and _MARKET.search(text)
        and _SALE.search(text)
        and _BROKEN_DISK.search(text)
        and not re.search(
            r"[,;:]|\b(?:или|а\s+если)\b", _WHEN.sub("", text, count=1), re.IGNORECASE
        )
    ):
        return "Да хоть сейчас, но как нерабочие\nВ объяве напиши, что проверял\nПричину не знаешь — так и укажи"
    return ""
