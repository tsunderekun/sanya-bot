import re

from aiogram.enums import MessageEntityType
from aiogram.types import Message

SELF_FACT_PREFIX = "self_report:"


def self_fact_prefix(chat_id: int) -> str:
    return f"{SELF_FACT_PREFIX}{chat_id}:"


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
_PREFERENCE = re.compile(
    r"(?:я\s+)?(не\s+люблю|ненавижу|терпеть\s+не\s+могу|люблю|обожаю|предпочитаю|"
    r"мне\s+(?:не\s+)?нрав(?:ится|ятся)|мне\s+по\s+душе|не\s+переношу|"
    r"фанатею\s+от|кайфую\s+от|не\s+выношу)\s+(.+)",
    re.I,
)
# Keep existing keys for common aliases; this is not a topic allowlist.
_PREFERENCE_TOPICS = tuple(
    (re.compile(pattern), label)
    for pattern, label in (
        (r"кофе", "кофе"),
        (r"ча[йя]", "чай"),
        (r"пицц[ау]", "пиццу"),
        (r"суши", "суши"),
        (r"роллы", "роллы"),
        (r"гречк[ау]", "гречку"),
        (r"ананасы", "ананасы"),
        (r"ананасы\s+в\s+пицце|пиццу\s+с\s+ананасами", "ананасы в пицце"),
        (r"рыбалк[ау]", "рыбалку"),
        (r"футбол", "футбол"),
        (r"настолки|настольные игры", "настольные игры"),
        (r"аниме", "аниме"),
    )
)
_GAME_VALUE = re.compile(_GAME, re.I)
_ACTIVITY = re.compile(
    r"(?:я\s+)?(увлекаюсь|интересуюсь|занимаюсь|коллекционирую|собираю|"
    r"умею|изучаю|пишу|мастерю|"
    r"(?:часто|обычно|регулярно)\s+(?:читаю|смотрю|слушаю|катаюсь|играю|готовлю)|"
    r"(?:читаю|слушаю|смотрю|рисую|готовлю|бегаю|катаюсь|играю\s+в))\s+(.+)",
    re.I,
)
_ACTIVITY_LABELS = {
    "увлекаюсь": "Увлекается",
    "интересуюсь": "Интересуется",
    "занимаюсь": "Занимается",
    "коллекционирую": "Коллекционирует",
    "собираю": "Собирает",
    "читаю": "Читает",
    "смотрю": "Смотрит",
    "слушаю": "Слушает",
    "катаюсь": "Катается",
    "играю": "Играет",
    "играю в": "Играет в",
    "готовлю": "Готовит",
    "рисую": "Рисует",
    "бегаю": "Бегает",
    "умею": "Умеет",
    "изучаю": "Изучает",
    "пишу": "Пишет",
    "мастерю": "Мастерит",
}
_SUBJECT = re.compile(r"[а-яёa-z][а-яёa-z0-9 -]{0,89}", re.I)
_UNSAFE_OR_AMBIGUOUS = re.compile(
    r"\b(?:а|но|или|либо|если|когда|что|чтобы|потому|наверное|возможно|буду|будет|"
    r"хочу|планирую|собираюсь|раньше|вчера|сегодня|завтра|сейчас|вроде|не|"
    r"я|ты|мы|вы|он[аи]?|мой|мо[яеёи]|твой|тва?оя|сво[яеёи]|себя|тебя|его|е[её]|их|"
    r"друг\w*|коллег\w*|родител\w*|сын\w*|доч\w*|мам\w*|пап\w*|"
    r"улиц\w*|адрес\w*|квартир\w*|проспект\w*|переул\w*|живу|работаю|"
    r"паспорт\w*|телефон\w*|номер\w*|реквизит\w*|парол\w*|банковск\w*|"
    r"болезн\w*|болею|диагноз\w*|лечени\w*|лекарств\w*|депресс\w*|психиатр\w*|"
    r"секс\w*|порн\w*|эрот\w*|интим\w*|nda|нд[аa]|секрет\w*|конфиденциаль\w*|"
    r"политик\w*|религи\w*|вероисповед\w*|национальност\w*|ориентаци\w*|"
    r"имя|фамили\w*|подпис\w*|договор\w*|зарплат\w*|доход\w*|"
    r"моего|моей|моему|своего|своей|своему|чуж\w*)\b",
    re.I,
)
_SEPARATOR = re.compile(r"\s*(?:[,+]|\s+и\s+)\s*", re.I)
_QUOTE_ENTITIES = {
    MessageEntityType.BLOCKQUOTE,
    MessageEntityType.EXPANDABLE_BLOCKQUOTE,
    MessageEntityType.CODE,
    MessageEntityType.PRE,
}


def _plain_subject(raw: str) -> str:
    subject = " ".join(raw.split())
    if (
        not _SUBJECT.fullmatch(subject)
        or len(subject.split()) > 8
        or sum(c.isdigit() for c in subject) > 2
        or _UNSAFE_OR_AMBIGUOUS.search(subject)
        # Proper names in free text can identify people or organizations.
        or any(word[0].isupper() for word in subject.split() if word[0].isalpha())
    ):
        return ""
    return subject.casefold()


def extract_self_facts(text: str) -> tuple[tuple[str, str], ...]:
    """Recognize complete, explicit self-reports without storing whole messages."""
    text = text.strip()
    if not text or len(text) > 180 or any(c in text for c in "\r\n?？"):
        return ()
    text = text.removesuffix(".").removesuffix("!").strip()
    if preference := _PREFERENCE.fullmatch(text):
        verb, subject = (
            " ".join(part.casefold().split()) for part in preference.groups()
        )
        topic = subject.removeprefix("играть в ")
        if _GAME_VALUE.fullmatch(topic) or any(
            pattern.fullmatch(topic) for pattern, key, _ in _MODELS if key != "game"
        ):
            label = topic
        else:
            label = next(
                (
                    label
                    for pattern, label in _PREFERENCE_TOPICS
                    if pattern.fullmatch(subject)
                ),
                None,
            )
        if not label:
            label = _plain_subject(preference[2])
        if not label:
            return ()
        sentiment = {
            "не люблю": "Не любит",
            "ненавижу": "Ненавидит",
            "терпеть не могу": "Терпеть не может",
            "люблю": "Любит",
            "обожаю": "Обожает",
            "предпочитаю": "Предпочитает",
            "мне нравится": "Любит",
            "мне нравятся": "Любит",
            "мне не нравится": "Не любит",
            "мне не нравятся": "Не любит",
            "мне по душе": "Любит",
            "не переношу": "Не любит",
            "не выношу": "Не любит",
            "фанатею от": "Любит",
            "кайфую от": "Любит",
        }[verb]
        return ((f"preference:{label}", f"{sentiment}: {label}"),)
    play = _PLAY.fullmatch(text)
    match = play or _SETUP.fullmatch(text)
    models = _SEPARATOR.split(match[1]) if match else []
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
            break
    else:
        if facts:
            return tuple(facts.items())
    if activity := _ACTIVITY.fullmatch(text):
        verb = " ".join(activity[1].casefold().split())
        if subject := _plain_subject(activity[2]):
            frequency, _, action = verb.partition(" ")
            if frequency not in {"часто", "обычно", "регулярно"}:
                frequency, action = "", verb
            label = _ACTIVITY_LABELS[action]
            if frequency:
                label = f"{frequency.capitalize()} {label.lower()}"
            return ((f"activity:{action}:{subject}", f"{label}: {subject}"),)
    return ()


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
