import html
import re

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
NEGATIVE = re.compile(r"(?im)\bNegative prompt\s*:\s*\S")
PROMPT_TAIL = re.compile(
    r"(?im)^\s*(?:negative\s+prompt|steps|sampler|cfg\s+scale)\s*:[\s\S]*"
)
EXCLAMATION = str.maketrans("", "", "!！‼❗❕")
SELF_HUMILIATION_CHECK = re.compile(
    r"\b(?:я\s+сос[ау]\w*|сосу\s+(?:хуй|член|соседск\w*)|(?:беру|брал|возьму)\s+в\s+рот|на\s+бутылке\s+сижу|глотаю\s+сперм\w*)\b",
    re.I,
)
CHAT_FALLBACK = "Чет мысль не докрутил\nДавай еще разок братан"


def _parts(text: str):
    start = 0
    for match in FENCE.finditer(text):
        yield None, text[start : match.start()]
        yield match[1], match[2]
        start = match.end()
    yield None, text[start:]


def clean_response(text: str, mode: Mode) -> tuple[str, str | None, bool]:
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
            is_prompt = lang.lower() == "prompt"
            if (mode is Mode.PROMPT and is_prompt) or (
                mode is Mode.CODE and not is_prompt
            ):
                clean.append(f"```{lang}\n{body}```")
            continue
        # Drop a dangling fence and its body, not just the backticks.
        body = re.split(r"(?m)^```", body, maxsplit=1)[0]
        body = MEDIA_TAG.sub(remove_tag, body.replace(r"\r", "").replace(r"\n", "\n"))
        if mode is not Mode.PROMPT:
            body = PROMPT_TAIL.sub("", body)
            body = TEXT_EMOJI.sub("", body)
        body = body.translate(EXCLAMATION)
        body = re.sub(r"(?m)^\s*#{1,6}\s+", "", body)
        clean.append(re.sub(r"(?m)[.。…]+(?=[ \t]*\r?$)", "", body))
    result = "".join(clean).strip()
    if mode is Mode.CHAT:
        lines = [line.strip() for line in result.splitlines() if line.strip()]
        lines = [line[0].upper() + line[1:] for line in lines]
        result = "\n".join(lines[:4])
        if not valid_burst(result):
            result = ""
    return result, emoji, has_sticker


def valid_burst(text: str) -> bool:
    lines = text.splitlines()
    return 2 <= len(lines) <= 4 and all(
        3 <= len(line.split()) <= 8 and len(line) <= 220 for line in lines
    )


def valid_output(text: str, mode: Mode) -> bool:
    if mode not in (Mode.CODE, Mode.PROMPT):
        return bool(text)
    blocks = [(lang, body) for lang, body in _parts(text) if lang is not None]
    if mode is Mode.PROMPT:
        return any(
            lang.lower() == "prompt" and body.strip() for lang, body in blocks
        ) and any(NEGATIVE.search(body) for lang, body in _parts(text) if lang is None)
    for lang, body in blocks:
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
