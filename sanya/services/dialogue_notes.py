import random
import re

from aiogram.types import Message

from ..database import Database
from ..lore import KENTS_NAME_ALIASES

KENTS_QUESTION = re.compile(
    r"\b(?:саммари|поясни|расскажи|че там|кто такие)\b.*?\b(?:кент|пацан|банд|кореш)\w*",
    re.I,
)
PERSON_QUESTION = re.compile(
    r"\b(?:кто|поясни|че за|расскажи|знаешь|че думаешь|как тебе|что скажешь|мнение|база)\b",
    re.I,
)
ALIASES = {alias: uid for uid, names in KENTS_NAME_ALIASES.items() for alias in names}
ALIAS_PATTERN = re.compile(
    r"(?<!\w)(?:"
    + "|".join(map(re.escape, sorted(ALIASES, key=len, reverse=True)))
    + r")(?!\w)",
    re.I,
)


def _collect(repo, uid, chat_id, replied, text, include_kents):
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
    profile = repo.get_user_profile(uid)
    if profile and profile["persona_notes"]:
        notes.append(f"Собеседник — кент: {profile['persona_notes'][:400]}")
    elif profile and profile["message_count"] >= 25:
        notes.append("Собеседник — старый кент")
    if replied and random.random() < 0.20:
        profile = repo.get_user_profile(replied)
        if profile and profile["persona_notes"]:
            notes.append(f"Реплай на кента: {profile['persona_notes'][:180]}")
    chat = repo.get_chat_profile(chat_id)
    if chat and chat["chat_lore"]:
        notes.append(f"Лор конфы {chat['title']}: {chat['chat_lore'][:400]}")
    for kind, key in (("user", uid), ("chat", chat_id)):
        facts = repo.get_auto_facts(kind, key, 3)
        if facts:
            notes.append("Подмечено: " + "; ".join(f["fact_text"][:100] for f in facts))
    phrases = repo.get_top_chat_phrases(chat_id, 3)
    if phrases:
        notes.append("Частые темы: " + ", ".join(p["phrase"][:60] for p in phrases))
    return notes, False


async def dialogue_notes(
    db: Database, message: Message, text: str, *, include_kents: bool
):
    reply = message.reply_to_message
    replied = reply.from_user.id if reply and reply.from_user else None
    return await db.run(
        _collect, message.from_user.id, message.chat.id, replied, text, include_kents
    )
