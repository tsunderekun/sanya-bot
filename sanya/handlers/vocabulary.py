from html import escape

from aiogram import types
from aiogram.enums import ChatType

from ..database import Database
from ..repository import Repository
from ..vocabulary import BUILTIN_INDEX, BUILTIN_WORDS, word_key
from .access import check_is_admin
from .command_args import command_args, reply_html_lines

HELP = "/словарь добавить слово = значение\n/словарь удалить слово"


async def cmd_vocabulary(message: types.Message, db: Database):
    raw = command_args(message).strip()
    chat_id = message.chat.id
    if not raw:
        rows = await db.run(Repository.list_chat_words, chat_id)
        lines = ["<b>Встроенные — не удаляются и не заменяются:</b>"]
        lines.extend(
            f"{escape(word)} — {escape(meaning)}" for word, meaning in BUILTIN_WORDS
        )
        lines.append("\n<b>Добавленные в этом чате:</b>")
        lines.extend(
            f"{escape(row['word'])} — {escape(row['meaning'])}" for row in rows
        )
        if not rows:
            lines.append("Пока пусто.")
        lines.append("\n" + escape(HELP))
        await reply_html_lines(message, lines)
        return

    action, *args = raw.split(maxsplit=1)
    action = action.casefold()
    adding = action in {"добавить", "add"}
    if adding or action in {"удалить", "del", "delete"}:
        if message.chat.type != ChatType.PRIVATE and (
            not message.from_user
            or not await check_is_admin(message.chat, message.from_user.id)
        ):
            await message.reply("В группе словарь меняют только админы.")
            return
        if not args:
            await message.reply(HELP)
            return
        if adding:
            word, sep, meaning = args[0].partition("=")
            if not sep:
                await message.reply(HELP)
                return
            status = await db.run(Repository.add_chat_word, chat_id, word, meaning)
        else:
            status = await db.run(Repository.delete_chat_word, chat_id, args[0])
        await message.reply(
            {
                "builtin": "Это встроенное слово. Его нельзя удалить или заменить.",
                "invalid": "Нужно слово или фраза до 40 символов и значение в одну строку до 160 символов.",
                "exists": "Слово уже есть. Чтобы поменять значение, сначала удали его.",
                "added": "Добавил в словарь этого чата.",
                "deleted": "Удалил из словаря этого чата.",
                "missing": "Такого слова в словаре этого чата нет.",
            }[status]
        )
        return

    word = word_key(raw)
    meaning = BUILTIN_INDEX.get(word)
    if meaning is None:
        rows = await db.run(Repository.list_chat_words, chat_id)
        meaning = next((r["meaning"] for r in rows if r["word"] == word), None)
    await message.reply(
        f"{word} — {meaning}" if meaning else "Такого слова нет.\n" + HELP,
        parse_mode=None,
    )
