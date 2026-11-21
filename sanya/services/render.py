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
# Known prompt echo: drop only the whole line, not ordinary talk about байки.
_CHAT_PROMPT_ECHO = re.compile(
    r"^[ \t]*байку обозначь словами[.!…]*[ \t]*(?:\r?\n|\Z)", re.I | re.M
)
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
_CELL_SEPARATOR = re.compile(r"\\(.)|(\|)")
_ALIGNMENT = re.compile(r":?-{3,}:?")
_TABLE_INLINE = re.compile(
    r"\\(?P<literal>[`*\\])|`(?P<code>[^`\n]+)`"
    r"|(?<!\*)\*\*(?!\s)(?P<bold>[^*\n]+?)(?<!\s)\*\*(?!\*)"
    r"|(?<!\*)\*(?!\s)(?P<italic>[^*\n]+?)(?<!\s)\*(?!\*)"
)
_TABLE_DIVIDER = re.compile(r"(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,}")
_TABLE_BULLET = re.compile(r"[ \t]*[*+-][ \t]+(.+)")


def _table_inline(text: str, *, rich: bool = True) -> str:
    escape = html.escape if rich else str
    parts = []
    start = 0
    for match in _TABLE_INLINE.finditer(text):
        parts.append(escape(text[start : match.start()]))
        key = match.lastgroup
        value = escape(match[key])
        tag = {"code": "code", "bold": "b", "italic": "i"}.get(key)
        parts.append(f"<{tag}>{value}</{tag}>" if rich and tag else value)
        start = match.end()
    parts.append(escape(text[start:]))
    return "".join(parts)


def render_compact_reply(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if _TABLE_DIVIDER.fullmatch(line.strip()):
            continue
        if item := _TABLE_BULLET.fullmatch(line):
            lines.append("• " + _table_inline(item[1]))
        else:
            lines.append(_table_inline(line))
    return "\n".join(lines).strip()


def _table_prose(text: str) -> tuple[str, str]:
    rich, plain, paragraph, bullets = [], [], [], []

    def flush():
        if paragraph:
            rich.append("<p>" + "<br>".join(map(_table_inline, paragraph)) + "</p>")
            paragraph.clear()
        if bullets:
            items = "".join(f"<li>{_table_inline(item)}</li>" for item in bullets)
            rich.append(f"<ul>{items}</ul>")
            bullets.clear()

    for line in text.strip().splitlines():
        if _TABLE_DIVIDER.fullmatch(line.strip()):
            flush()
            rich.append("<hr/>")
            plain.append("")
        elif item := _TABLE_BULLET.fullmatch(line):
            if paragraph:
                flush()
            bullets.append(item[1])
            plain.append("• " + _table_inline(item[1], rich=False))
        elif line.strip():
            if bullets:
                flush()
            paragraph.append(line)
            plain.append(_table_inline(line, rich=False))
        else:
            flush()
            plain.append("")
    flush()
    return "".join(rich), "\n".join(plain).strip()


def _table_cells(line: str) -> list[str]:
    line = line.strip().removeprefix("|")
    cells = []
    start = 0
    for match in _CELL_SEPARATOR.finditer(line):
        if match[2]:
            cells.append(line[start : match.start()].strip())
            start = match.end()
    if start < len(line) or not cells:
        cells.append(line[start:].strip())
    return [cell.replace(r"\|", "|") for cell in cells]


def _table_parts(text: str):
    """Yield prose, fenced code, and complete Markdown tables separately."""
    for lang, body in _parts(text):
        if lang is not None:
            yield "code", (lang, body)
            continue
        lines = body.splitlines(keepends=True)
        start = index = 0
        while index + 2 < len(lines):
            header = _table_cells(lines[index])
            separator = _table_cells(lines[index + 1])
            if not (
                len(header) >= 2
                and len(separator) == len(header)
                and all(_ALIGNMENT.fullmatch(cell) for cell in separator)
            ):
                index += 1
                continue
            end = index + 2
            rows = []
            while end < len(lines) and "|" in lines[end]:
                row = _table_cells(lines[end])
                if len(row) != len(header):
                    break
                rows.append(row)
                end += 1
            if not rows:
                index += 1
                continue
            yield "text", "".join(lines[start:index])
            yield "table", (header, rows)
            start = index = end
        yield "text", "".join(lines[start:])


def render_table_reply(text: str) -> tuple[str, str] | None:
    """Return safe Rich HTML and a plain list fallback for a table reply."""
    parts = list(_table_parts(text))
    if not any(kind == "table" for kind, _ in parts):
        return None
    rich, fallback = [], []
    for kind, body in parts:
        if kind == "table":
            header, rows = body
            rich.append(
                "<table><tr>"
                + "".join(f"<th>{_table_inline(c)}</th>" for c in header)
                + "</tr>"
            )
            for row in rows:
                rich.append(
                    "<tr>"
                    + "".join(f"<td>{_table_inline(c)}</td>" for c in row)
                    + "</tr>"
                )
                fields = "; ".join(
                    f"{_table_inline(name, rich=False)}: {_table_inline(value, rich=False)}"
                    for name, value in zip(header, row)
                )
                fallback.append(f"• {fields}")
            rich.append("</table>")
        elif kind == "code":
            lang, code = body
            rich.append(
                f'<pre><code class="language-{lang}">{html.escape(code)}</code></pre>'
            )
            fallback.append(f"```{lang}\n{code}```")
        else:
            rendered, plain = _table_prose(body)
            rich.append(rendered)
            if plain:
                fallback.append(plain)
    return "".join(rich), "\n\n".join(fallback)


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
            body = _CHAT_PROMPT_ECHO.sub("", body)
        if mode is Mode.CHAT and not any(
            kind == "table" for kind, _ in _table_parts(body)
        ):
            # Older prompt examples used slashes between separate replies.
            body = _CHAT_SLASH.sub(lambda m: m["literal"] or "\n", body)
        body = body.translate(EXCLAMATION)
        body = re.sub(r"(?m)^\s*#{1,6}\s+", "", body)
        clean.append(re.sub(r"(?m)[.。…]+(?=[ \t]*\r?$)", "", body))
    result = "".join(clean).strip()
    if mode is Mode.CODE and code_only and len(list(FENCE.finditer(result))) != 1:
        return "", None, False
    if mode is Mode.CHAT and not any(
        kind == "table" for kind, _ in _table_parts(result)
    ):
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


_MESSAGE_BUDGET = 3800


def _utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _split_prose(text: str):
    while _utf16_length(text) > _MESSAGE_BUDGET:
        # Slice before escaping to keep HTML entities and tags intact.
        units = end = 0
        for char in text[:_MESSAGE_BUDGET]:
            units += 1 + (ord(char) > 0xFFFF)
            if units > _MESSAGE_BUDGET:
                break
            end += 1
        cut = text.rfind("\n", end // 2, end + 1)
        if cut < 0:
            cut = text.rfind(" ", end // 2, end + 1)
        if cut < 0:
            cut = end
        for match in _TABLE_INLINE.finditer(text):
            if match.start() >= cut:
                break
            if match.start() and match.start() < cut < match.end():
                cut = match.start()
                break
        yield text[:cut].rstrip()
        text = text[cut:].lstrip()
    if text:
        yield text


def _pack_prose(body: str) -> list[str]:
    chunks = []
    page = []
    used = 0
    for paragraph in re.split(r"\n\s*\n", body.strip()):
        for part in _split_prose(paragraph):
            if not (rendered := render_compact_reply(part)):
                continue
            size = _utf16_length(part)
            if page and used + 2 + size > _MESSAGE_BUDGET:
                chunks.append("\n\n".join(page))
                page = []
                used = 0
            used += size + (2 if page else 0)
            page.append(rendered)
    if page:
        chunks.append("\n\n".join(page))
    return chunks


def render_messages(text: str, *, detailed: bool) -> list[str]:
    if not detailed:
        return [line for line in render_compact_reply(text).splitlines() if line.strip()][:4]
    chunks = []
    for lang, body in _parts(text):
        if lang is None:
            chunks.extend(_pack_prose(body))
        elif _utf16_length(body) <= _MESSAGE_BUDGET:
            chunks.append(
                f'<pre><code class="language-{lang}">{html.escape(body)}</code></pre>'
            )
    return chunks


def needs_document(text: str) -> bool:
    return any(
        lang is not None and _utf16_length(body) > _MESSAGE_BUDGET
        for lang, body in _parts(text)
    )
