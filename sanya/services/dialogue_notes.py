import re

from aiogram.types import Message

from ..database import Database
from ..lore import KENT_ALIASES

KENTS_QUESTION = re.compile(
    r"\b(?:саммари|поясни|расскажи|че там|кто такие)\b.*?\b(?:кент|пацан|банд|кореш)\w*",
    re.I,
)
PERSON_QUESTION = re.compile(
    r"\b(?:кто|поясни|че за|расскажи|знаешь|че думаешь|как тебе|что скажешь|мнение|база)\b",
    re.I,
)
ALIASES = {alias: uid for uid, names in KENT_ALIASES.items() for alias in names}
ALIAS_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True)))
    + r")(?!\w)",
    re.I,
)

_GENERIC_WORDS = {
    "авито",
    "продать",
    "продажа",
    "спермер",
    "саня",
    "железо",
    "кент",
    "братан",
    "когда",
    "какой",
    "какая",
    "какие",
    "почему",
    "теперь",
    "сегодня",
    "вообще",
}


def _anchors(text):
    return (
        set(re.findall(r"\b\w{4,}\b", text.casefold().replace("ё", "е")))
        - _GENERIC_WORDS
    )


def _collect(repo, uid, chat_id, text, include_kents):
    notes = []
    kent_question = False
    if include_kents and KENTS_QUESTION.search(text):
        people = repo.get_all_kents()[:8]
        notes.extend(
            f"Кент {p['first_name'] or p['user_name']}: {p['persona_notes'][:180]}"
            for p in people
        )
        kent_question = bool(people)
    elif include_kents and PERSON_QUESTION.search(text):
        target = None
        for name in re.findall(r"@([a-zA-Z0-9_]+)", text):
            candidate = repo.find_user_by_username(name)
            if candidate and candidate["persona_notes"]:
                target = candidate
                break
        if target is None and (match := ALIAS_PATTERN.search(text)):
            target = repo.get_user_profile(ALIASES[match[0].lower()])
        if target and target["persona_notes"]:
            notes.append(
                f"Факты о кенте {target['first_name'] or target['user_name']}: {target['persona_notes'][:750]}"
            )
            kent_question = True
    if kent_question:
        return notes, True
    anchors = _anchors(text)

    def relevant(note):
        return bool(anchors & _anchors(note))

    chat = repo.get_chat_profile(chat_id)
    if chat and chat["chat_lore"]:
        relevant_lines = [
            part
            for part in re.split(r"\n+|(?<=[.!?])\s+", chat["chat_lore"])
            if relevant(part)
        ]
        if relevant_lines:
            notes.append(f"Лор конфы: {' '.join(relevant_lines)[:400]}")
    self_facts = repo.get_self_facts(chat_id, uid, 3)
    if text and not re.search(
        r"\b(?:у меня|мой|моя|мо[иеё]|мою|мне|про меня)\b", text, re.I
    ):
        self_facts = [f for f in self_facts if relevant(f["fact_text"])]
    if self_facts:
        notes.append(
            "Со слов собеседника в этом чате (при расхождении верь ручному лору): "
            + "; ".join(f["fact_text"][:100] for f in self_facts)
        )
    chat_facts = [
        f for f in repo.get_auto_facts("chat", chat_id, 3) if relevant(f["fact_text"])
    ]
    if chat_facts:
        notes.append(
            "Подмечено в чате: " + "; ".join(f["fact_text"][:100] for f in chat_facts)
        )
    return notes, False


async def dialogue_notes(
    db: Database, message: Message, text: str, *, include_kents: bool
):
    return await db.run(
        _collect, message.from_user.id, message.chat.id, text, include_kents
    )
