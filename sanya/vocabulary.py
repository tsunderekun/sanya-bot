"""Built-in slang plus helpers shared by commands and chat."""

import re
import unicodedata

BUILTIN_WORDS = (
    (
        "слойпер",
        "опечатка «спойлер» на 4PDA; теперь кривой комп, брак или ИИ-слоп, не scatter",
    ),
    ("антон / алексей", "сдохло"),
    ("галька", "слабый офисный процессор или ноут"),
    ("щит", "хлам или бухой"),
    ("кезднуть", "зайти в войс, пукнуть или взорваться, по контексту"),
    ("жодик", "HDD"),
    ("защеку", "легко"),
    ("заеболлс / заеболл", "отлично"),
    ("фимоз", "абсурд"),
    ("спермочки", "шок"),
    ("генераторы спермеров / DLSS / длсс", "DLSS Frame Generation"),
    ("полик", "клей B-7000"),
    ("выебать", "починить или прошить"),
    ("поебаться", "повозиться"),
    ("запаяли", "закрыли доступ или зафиксировали"),
    ("слить дрова", "продать неликвид"),
    ("в хуй свистнули", "исчезли или подорожали"),
    ("кам / в сперме", "угар"),
    ("кусок плиточки", "скол при разборе"),
    ("кофе / очко на взлёт", "утренний рофл «очко на взлёт»"),
    ("соскочи буйвол", "отстань"),
    ("хорс", "пони или конь, не обращение"),
    ("гарантия до порога", "шутка после продажи, не условие сделки"),
    ("ухрап", "вырубился спать"),
    ("срать", "в контексте Дискорда — зависать в войсе или играть в кооператив"),
)


def word_key(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("ё", "е")
    return " ".join(text.translate(str.maketrans("‐‑‒–—−", "------")).split())


BUILTIN_INDEX = {
    word_key(alias): meaning
    for label, meaning in BUILTIN_WORDS
    for alias in label.split(" / ")
}


def valid_word(word: str) -> bool:
    return (
        len(word) <= 40
        and re.fullmatch(r"[^\W_]+(?:[ -][^\W_]+)*", word) is not None
        and any(c.isalpha() for c in word)
    )


def valid_meaning(meaning: str) -> bool:
    return 0 < len(meaning) <= 160 and meaning.isprintable()


def select_words(rows, text: str, *, rofl: bool) -> list[tuple[str, str]]:
    text = word_key(text)
    found, other = [], []
    for row in rows:
        destination = (
            found
            if re.search(rf"(?<!\w){re.escape(row['word'])}(?!\w)", text)
            else other
        )
        destination.append((row["word"], row["meaning"]))
    return (found + (other[:4] if rofl else []))[:6]


def custom_definition(rows, text: str) -> str:
    match = re.search(
        r"\b(?:что значит|что означает|что такое|что за слово)\s+(.+?)\s*[?!.]*$",
        text,
        re.I,
    )
    if not match:
        return ""
    term = word_key(match[1].strip(" \"'«»“”`"))
    return next(
        (f"{row['word']} — {row['meaning']}" for row in rows if row["word"] == term), ""
    )
