import re
from html import unescape

from aiogram import types


def command_args(message: types.Message) -> str:
    parts = (message.text or getattr(message, "caption", None) or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else ""


async def reply_html_lines(message: types.Message, lines: list[str], **kwargs):
    page = []
    size = 0
    for line in lines:
        plain = unescape(re.sub(r"<[^>]*>", "", line))
        length = len(plain.encode("utf-16-le")) // 2
        if page and size + length + 1 > 3900:
            await message.reply("\n".join(page), parse_mode="HTML", **kwargs)
            page, size = [], 0
        if length > 3900:
            for offset in range(0, len(plain), 1900):
                await message.reply(
                    plain[offset : offset + 1900], parse_mode=None, **kwargs
                )
        else:
            page.append(line)
            size += length + 1
    if page:
        await message.reply("\n".join(page), parse_mode="HTML", **kwargs)
