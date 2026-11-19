import re

from aiogram.types import Message

from ..database import Database
from ..lore import INITIAL_KENTS_PROFILES, KENT_ALIASES
from ..repository import AmbiguousUsername

KENTS_QUESTION = re.compile(
    r"\b(?:саммари|поясни|расскажи|че там|кто такие)\b.*?\b(?:кент|пацан|банд|кореш)\w*",
    re.I,
)
PERSON_QUESTION = re.compile(
    r"\b(?:кто|поясни|объясни|ч[еёо] за|что за|расскажи|знаешь|помнишь|"
    r"ч[её] думаешь|как тебе|что скажешь|мнение|база|факты|лор)\b",
    re.I,
)
CHAT_QUESTION = re.compile(
    r"\b(?:лор|истори[яию]|факт(?:ы|ов|ах))\s+(?:(?:про|этой|этого|о)\s+)?"
    r"(?:конф[ауеыой]+|чат(?:е|а)?)\b|"
    r"\b(?:расскажи|поясни|что знаешь|ч[её] знаешь)\s+(?:про|о|за)\s+"
    r"(?:(?:этой|эту|этот|этом|нашей|нашу|наш)\s+)?(?:конф[ауеыой]+|чат(?:е|а)?)\b|"
    r"^(?:спермер[\s,:]+)?(?:расскажи\s+)?(?:лор|история|факты)\s*[?!.]*$",
    re.I,
)
SHORT_TOPIC = re.compile(r"(?:а\s+)?(?:про|за)\s+[^?\n]+\?+\s*$", re.I)
EXPLICIT_MEME = re.compile(
    r"\b(?:что за мем|в ч[её]м (?:мем|рофл|прикол)|лор (?:про|о))\b", re.I
)
_TAG = re.compile(r"(?<!\w)@([a-zA-Z0-9_]+)")
_LEADING_CALL = re.compile(r"^спермер(?:[\s,:—–-]+|$)", re.I)
_SELF_IDENTITY = re.compile(r"кто\s+(?:я(?:\s+так(?:ой|ая))?|так(?:ой|ая)\s+я)", re.I)
_NAMED_IDENTITY = re.compile(r"кто\s+(?:такой|такая)\s+(.+)", re.I)
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
    "расскажи",
    "поясни",
    "объясни",
    "знаешь",
    "помнишь",
    "конфа",
    "конфу",
    "конфы",
    "конфе",
    "факты",
    "мемом",
    "прикол",
}


def _anchors(text):
    return (
        set(re.findall(r"\b\w{4,}\b", text.casefold().replace("ё", "е")))
        - _GENERIC_WORDS
    )


def _topic_words(text):
    # Loose inflection matching only for topical lore, not for ordinary chatter.
    return {word[:4] if word.isalpha() else word for word in _anchors(text)}


def _pick_rows(rows, text, *, overview=False, limit=4):
    topic = _topic_words(text)
    scored = [(len(topic & _topic_words(row)), i, row) for i, row in enumerate(rows)]
    ranked = sorted(scored, key=lambda item: (-item[0], -item[1]))
    return [
        row.strip() for score, _, row in ranked if row.strip() and (score or overview)
    ][:limit]


def _in_scope(repo, profile, chat_id, uid):
    target = profile["user_id"]
    return (
        target == uid
        or target in INITIAL_KENTS_PROFILES
        or (chat_id < 0 and repo.count_user_chat_messages(chat_id, target) > 0)
    )


def _person(repo, uid, chat_id, text, reply_user):
    tags = set(_TAG.findall(text.casefold()))
    if tags:
        if len(tags) != 1:
            return None
        tag = next(iter(tags))
        try:
            target = repo.find_user_by_username(tag, current_only=True)
        except AmbiguousUsername:
            return None
        if not target and tag in ALIASES:
            target = repo.get_user_profile(ALIASES[tag])
    else:
        aliases = {ALIASES[m[0].casefold()] for m in ALIAS_PATTERN.finditer(text)}
        if len(aliases) > 1:
            return None
        target_id = next(iter(aliases), None)
        if target_id is None and re.search(
            r"\b(?:про меня|обо мне|кто я)\b", text, re.I
        ):
            target_id = uid
        if target_id is None and re.search(
            r"\b(?:это кто|кто это|про него|про неё|про нее|за него)\b", text, re.I
        ):
            target_id = reply_user
        target = repo.get_user_profile(target_id) if target_id is not None else None
    return target if target and _in_scope(repo, target, chat_id, uid) else None


def _person_note(repo, profile, chat_id, uid, text, *, brief=False, about_author=False):
    name = profile["user_name"] or "Кент"
    if profile["user_id"] in INITIAL_KENTS_PROFILES:
        name = INITIAL_KENTS_PROFILES[profile["user_id"]]["user_name"]
    if (
        profile["user_id"] in INITIAL_KENTS_PROFILES
        and profile["user_id"] != uid
        and (
            chat_id >= 0
            or not repo.count_user_chat_messages(chat_id, profile["user_id"])
        )
    ):
        rows = [
            entry["content"]
            for entry in repo.get_kent_fact_entries(profile["user_id"])
            if entry["content"] == INITIAL_KENTS_PROFILES[profile["user_id"]]["notes"]
        ]
    else:
        rows = (profile["persona_notes"] or "").splitlines()
    selected = _pick_rows(rows, text, overview=True, limit=2 if brief else 5)
    budget = 155 if brief else 1180
    parts = []
    for row in selected:
        if budget <= 0:
            break
        text_part = row[:budget].rsplit(" ", 1)[0] + "…" if len(row) > budget else row
        parts.append(text_part)
        budget -= len(text_part) + 2
    label = "Лор о собеседнике" if about_author else f"Лор о кенте {name}"
    return f"{label}: " + "; ".join(parts) if parts else ""


def _self_note(facts):
    return (
        "Со слов собеседника в этом чате (при расхождении верь ручному лору): "
        + "; ".join(f["fact_text"][:100] for f in facts[:4])
    )


def _identity_notes(repo, uid, chat_id, text):
    question = text.rstrip(" ?!.")
    if _SELF_IDENTITY.fullmatch(question):
        notes = []
        profile = repo.get_user_profile(uid)
        if profile:
            if note := _person_note(
                repo, profile, chat_id, uid, text, about_author=True
            ):
                notes.append(note)
        if facts := repo.get_self_facts(chat_id, uid, 4):
            notes.append(_self_note(facts))
        return notes

    match = _NAMED_IDENTITY.fullmatch(question)
    if not match:
        return None
    name = " ".join(match[1].strip(' «»"“”[]').split())
    if name.casefold() in {"ты", "вы"}:
        return None  # The user really asked about Sanya.
    if _TAG.fullmatch(name):
        profile = _person(repo, uid, chat_id, name, None)
    elif target_id := ALIASES.get(name.casefold()):
        profile = repo.get_user_profile(target_id)
    else:
        matches = repo.find_scoped_people_by_name(chat_id, uid, name)
        profile = matches[0] if len(matches) == 1 else None
    if profile:
        if note := _person_note(repo, profile, chat_id, uid, text):
            return [note]
    return []


def _collect(
    repo,
    uid,
    chat_id,
    text,
    include_kents,
    reply_user=None,
    include_notes=True,
    *,
    lore_level=1,
):
    if not include_notes:
        return [], False
    # A leading call addresses the bot; the same word after "кто такой" is a subject.
    text = _LEADING_CALL.sub("", text.lstrip(" ,:—–-\t"), count=1)
    if include_kents:
        identity = _identity_notes(repo, uid, chat_id, text)
        if identity is not None:
            return identity, True
    notes = []
    lore_question = False
    direct = bool(PERSON_QUESTION.search(text) or SHORT_TOPIC.search(text))
    meme_question = bool(include_kents and EXPLICIT_MEME.search(text))
    if include_kents and KENTS_QUESTION.search(text):
        people = [p for p in repo.get_all_kents() if _in_scope(repo, p, chat_id, uid)][
            :8
        ]
        notes.extend(
            note
            for p in people
            if (note := _person_note(repo, p, chat_id, uid, text, brief=True))
        )
        lore_question = True
    elif include_kents and direct:
        target = _person(repo, uid, chat_id, text, reply_user)
        if target:
            if note := _person_note(repo, target, chat_id, uid, text):
                notes.append(note)
            lore_question = True
        elif ALIAS_PATTERN.search(text) or _TAG.search(text):
            lore_question = True
    anchors = _anchors(text)

    def relevant(note):
        return bool(anchors & _anchors(note))

    overview = bool(include_kents and CHAT_QUESTION.search(text))
    lore_question |= overview or meme_question
    chat = (
        repo.get_chat_profile(chat_id)
        if lore_level or (include_kents and (direct or overview or meme_question))
        else None
    )
    if chat and chat["chat_lore"]:
        if include_kents and (direct or overview or meme_question):
            relevant_lines = _pick_rows(
                chat["chat_lore"].splitlines(), text, overview=overview
            )
            lore_question |= bool(relevant_lines)
        elif lore_level:
            relevant_lines = [
                part
                for part in re.split(r"\n+|(?<=[.!?])\s+", chat["chat_lore"])
                if relevant(part)
            ]
        else:
            relevant_lines = []
        if relevant_lines:
            budget = 850 if lore_question else (0, 400, 550, 700)[lore_level]
            notes.append(f"Лор конфы: {' '.join(relevant_lines)[:budget]}")
    self_facts = repo.get_self_facts(chat_id, uid, 40)
    own_question = re.search(
        r"\b(?:про меня|обо мне|что я (?:люблю|не люблю|ненавижу|предпочитаю)|"
        r"како[йея] у меня|что у меня|чем я пользуюсь|во что я играю)\b",
        text,
        re.I,
    )
    topic = _topic_words(text)
    if not text or own_question:
        if own_question and re.search(
            r"\b(?:люблю|ненавижу|предпочитаю)\b", text, re.I
        ):
            self_facts = [f for f in self_facts if ":preference:" in f["fact_key"]]
        self_facts = sorted(
            self_facts, key=lambda f: not bool(topic & _topic_words(f["fact_text"]))
        )
        lore_question |= bool(include_kents and own_question)
    else:
        self_facts = [f for f in self_facts if relevant(f["fact_text"])]
    if self_facts:
        notes.append(_self_note(self_facts))
    return notes, lore_question


async def dialogue_notes(
    db: Database,
    message: Message,
    text: str,
    *,
    include_kents: bool,
    include_notes: bool = True,
    lore_level: int = 1,
):
    author = getattr(message.reply_to_message, "from_user", None)
    reply_user = author.id if author and not getattr(author, "is_bot", False) else None
    return await db.run(
        _collect,
        message.from_user.id,
        message.chat.id,
        text,
        include_kents,
        reply_user,
        include_notes,
        lore_level=lore_level,
    )
