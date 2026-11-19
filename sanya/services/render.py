import html
import re
from textwrap import wrap

from ..intent import Mode

FENCE = re.compile(r"^```([\w+.#-]*)[ \t]*\r?\n(.*?)^```[ \t]*(?=\r?$)", re.M | re.S)
MEDIA_TAG = re.compile(
    r"\[(?:(?:отправил|кидает|кидаю|вставил)\s+)?(?P<tag>стикер|sticker|картинк\w*|фото|изображени\w*|пикч\w*|мем|скрин\w*|prompt)\b[^\]\n]*(?:\]|$)",
    re.I | re.M,
)
EMOJI = re.compile(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\u200D\uFE0F]+")
TEXT_EMOJI = re.compile(
    r"[#*0-9]\uFE0F?\u20E3|[\U0001F000-\U0001FAFF\u2600-\u2653\u2660-\u27BF\u200D\uFE0F]+"
)
INTERNAL_TAG = re.compile(
    r"\[\s*(?:вымысел из режима рофла|(?:справочные заметки|контекст реплая|"
    r"текущее сообщение|данные из сети|мысли|thinking|analysis)\s*:[^\]\n]*)\]",
    re.I,
)
# Strip leaked formatting, including escaped tags, without decoding normal text.
_TAG_NAME = r"(?:blockquote|b|strong|i|em|u|ins|s|strike|del|a|span|code|pre|br|p|div|tg-spoiler|tg-emoji)"
_HTML_WRAPPER = re.compile(
    rf"(?:</?{_TAG_NAME}(?:[ \t]+[^<>\n]*)?[ \t]*/?>|"
    rf"&lt;/?{_TAG_NAME}(?:[ \t]+(?:(?!&[lg]t;)[^<>\n])*)?[ \t]*/?&gt;)",
    re.I,
)
BRACKETS = str.maketrans("", "", "[]")
_PROMPT_LABEL = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+)?\*{0,2}"
    r"(?:(?P<positive>positive prompt|позитивный промпт|положительный промпт|prompt)"
    r"|(?P<negative>negative prompt|негативный промпт|отрицательный промпт))"
    r"[ \t]*\*{0,2}:[ \t]*\*{0,2}[ \t]*",
    re.I | re.M,
)
PROMPT_TAIL = re.compile(
    r"(?im)^\s*(?:negative\s+prompt|steps|sampler|cfg\s+scale)\s*:[\s\S]*"
)
EXCLAMATION = str.maketrans("", "", "!！‼❗❕")
SELF_HUMILIATION_CHECK = re.compile(
    r"\b(?:я\s+сос[ау]\w*|сосу\s+(?:хуй|член|соседск\w*)|(?:беру|брал|возьму)\s+в\s+рот|на\s+бутылке\s+сижу|глотаю\s+сперм\w*)\b",
    re.I,
)
CHAT_FALLBACK = "Чёт ответ не собрался"
_CHAT_SLASH = re.compile(
    r"(?P<literal>`[^`\n]*`|(?:https?://|www\.)[^\s]+)"
    r"|(?<=[А-Яа-яЁё.!?])[ \t]+/[ \t]+(?=[А-ЯЁ][а-яё]*\b)"
    r"|(?<=[а-яё])/(?=(?:А|Но|Если|Когда|Зато|Поэтому)[ \t]+)"
)


def _parts(text: str):
    start = 0
    for match in FENCE.finditer(text):
        yield None, text[start : match.start()]
        yield match[1], match[2]
        start = match.end()
    yield None, text[start:]


def prompt_sections(text: str) -> dict[str, str]:
    sections = {}
    for lang, body in _parts(text):
        if lang is not None:
            if lang.lower() == "prompt" and body.strip():
                sections.setdefault("positive", body.strip())
            continue
        body = re.split(r"(?m)^```", body, maxsplit=1)[0]
        labels = list(_PROMPT_LABEL.finditer(body))
        for i, label in enumerate(labels):
            end = labels[i + 1].start() if i + 1 < len(labels) else len(body)
            content = body[label.end() : end].strip()
            key = label.lastgroup
            if key and any(char.isalnum() for char in content):
                sections.setdefault(key, content)
    return sections


def english_prompt_sections(sections: dict[str, str]) -> bool:
    return all(
        (body := sections.get(key, ""))
        and re.search(r"[a-zA-Z]", body)
        and not re.search(r"[^\W\d_a-zA-Z]", body)
        for key in ("positive", "negative")
    )


def clean_response(
    text: str, mode: Mode, *, code_only: bool = False
) -> tuple[str, str | None, bool]:
    if mode is Mode.PROMPT:
        sections = prompt_sections(text)
        if english_prompt_sections(sections):
            return (
                f"```prompt\n{sections['positive']}\n```\nNegative prompt: {sections['negative']}",
                None,
                False,
            )
        return "", None, False
    emoji = None
    has_sticker = False

    def remove_tag(match):
        nonlocal emoji, has_sticker
        if match["tag"].lower() in ("sticker", "стикер"):
            has_sticker = True
            symbol = EMOJI.search(match[0])
            if symbol:
                emoji = symbol[0]
        return ""

    clean = []
    for lang, body in _parts(text):
        if lang is not None:
            if mode is Mode.CODE and lang.lower() != "prompt":
                clean.append(f"```{lang}\n{body}```")
            continue
        if mode is Mode.CODE and code_only:
            clean.append("\n")
            continue
        # Drop a dangling fence and its body, not just the backticks.
        body = re.split(r"(?m)^```", body, maxsplit=1)[0]
        body = MEDIA_TAG.sub(remove_tag, body.replace(r"\r", "").replace(r"\n", "\n"))
        body = PROMPT_TAIL.sub("", body)
        body = TEXT_EMOJI.sub("", body)
        if mode in (Mode.CHAT, Mode.DETAIL):
            body = INTERNAL_TAG.sub("", body)
            body = _HTML_WRAPPER.sub("", body)
            # Keep the actual joke or explanation, not its formatting wrappers.
            body = re.sub(r"\[([^\[\]\n]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", body)
            body = body.translate(BRACKETS)
        if mode is Mode.CHAT:
            # Older prompt examples used slashes between separate replies.
            body = _CHAT_SLASH.sub(lambda m: m["literal"] or "\n", body)
        body = body.translate(EXCLAMATION)
        body = re.sub(r"(?m)^\s*#{1,6}\s+", "", body)
        clean.append(re.sub(r"(?m)[.。…]+(?=[ \t]*\r?$)", "", body))
    result = "".join(clean).strip()
    if mode is Mode.CODE and code_only and len(list(FENCE.finditer(result))) != 1:
        return "", None, False
    if mode is Mode.CHAT:
        lines = [
            chunk
            for line in result.splitlines()
            if any(char.isalnum() for char in line)
            for chunk in wrap(line.strip(), width=220, break_on_hyphens=False)
        ]
        if len(lines) > 4:
            lines[3] = lines[3][:219].rstrip() + "…"
        lines = [line[0].upper() + line[1:] for line in lines]
        result = "\n".join(lines[:4])
    return result, emoji, has_sticker


def valid_output(text: str, mode: Mode) -> bool:
    if mode not in (Mode.CODE, Mode.PROMPT):
        return bool(text)
    if mode is Mode.PROMPT:
        return english_prompt_sections(prompt_sections(text))
    for lang, body in _parts(text):
        if (
            not lang
            or not body.strip()
            or lang.lower() in ("prompt", "text", "console", "output")
        ):
            continue
        if lang.lower() in ("python", "py", "python3"):
            try:
                compile(body, "<ollama>", "exec")
            except (SyntaxError, ValueError):
                return False
        return True
    return False


def render_messages(text: str, *, detailed: bool) -> list[str]:
    if not detailed:
        return [html.escape(line) for line in text.splitlines() if line.strip()][:4]
    chunks = []
    for lang, body in _parts(text):
        if lang is None:
            for paragraph in re.split(r"\n\s*\n", body.strip()):
                chunks.extend(
                    html.escape(paragraph[i : i + 1900])
                    for i in range(0, len(paragraph), 1900)
                )
        elif len(body.encode("utf-16-le")) // 2 <= 3800:
            chunks.append(
                f'<pre><code class="language-{lang}">{html.escape(body)}</code></pre>'
            )
    return chunks


def needs_document(text: str) -> bool:
    return any(
        lang is not None and len(body.encode("utf-16-le")) // 2 > 3800
        for lang, body in _parts(text)
    )
