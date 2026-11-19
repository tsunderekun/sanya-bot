from hashlib import sha256
from html import escape

from aiogram import types
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest

from ..database import Database
from ..repository import Repository
from ..vocabulary import BUILTIN_INDEX, BUILTIN_WORDS, word_key
from .access import check_is_admin
from .command_args import command_args, reply_html_lines
from .reply_style import button

HELP = "/словарь добавить слово = значение\n/словарь удалить слово"
ADD_PROMPT = "Ответь на это сообщение: слово = значение"
PAGE_SIZE = 6


async def show_dictionary(message: types.Message, db: Database):
    rows = await db.run(Repository.list_chat_words, message.chat.id)
    lines = ["<b>Встроенные — не удаляются и не заменяются:</b>"]
    lines.extend(
        f"{escape(word)} — {escape(meaning)}" for word, meaning in BUILTIN_WORDS
    )
    lines.append("\n<b>Добавленные в этом чате:</b>")
    lines.extend(f"{escape(r['word'])} — {escape(r['meaning'])}" for r in rows)
    if not rows:
        lines.append("Пока пусто.")
    lines.append("\n" + escape(HELP))
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                button("Добавить слово", "word:add"),
                button("Удалить слово", "word:page:0"),
            ],
            [button("Все настройки", "open")],
        ]
    )
    await reply_html_lines(message, lines, last_markup=keyboard)


def _word_id(row):
    return sha256(f"{row['word']}\0{row['meaning']}".encode()).hexdigest()[:24]


async def dictionary_button(query: types.CallbackQuery, db: Database):
    msg = query.message
    if not isinstance(msg, types.Message):
        await query.answer("Открой /dict заново")
        return
    if msg.chat.type != ChatType.PRIVATE and not await check_is_admin(
        msg.chat, query.from_user.id
    ):
        await query.answer("В группе словарь меняют только админы", show_alert=True)
        return
    action, _, value = (query.data or "").removeprefix("settings:word:").partition(":")
    if action == "add" and not value:
        await query.answer()
        await msg.reply(
            ADD_PROMPT,
            reply_markup=types.ForceReply(input_field_placeholder="слово = значение"),
        )
        return
    rows = await db.run(Repository.list_chat_words, msg.chat.id)
    if action == "page" and value.isascii() and value.isdecimal() and len(value) <= 8:
        page = min(int(value), max(0, (len(rows) - 1) // PAGE_SIZE))
        start = page * PAGE_SIZE
        buttons = [
            [button(row["word"], f"word:pick:{_word_id(row)}")]
            for row in rows[start : start + PAGE_SIZE]
        ]
        nav = []
        if page:
            nav.append(button("Назад", f"word:page:{page - 1}"))
        if start + PAGE_SIZE < len(rows):
            nav.append(button("Дальше", f"word:page:{page + 1}"))
        if nav:
            buttons.append(nav)
        buttons.append([button("Словарь", "dict")])
        text = (
            f"Что удалить? Страница {page + 1}"
            if rows
            else "Добавленных слов пока нет."
        )
        await query.answer()
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
        if msg.text and (
            msg.text.startswith("Что удалить?")
            or msg.text.startswith("Удалить слово «")
        ):
            try:
                await msg.edit_text(text, reply_markup=keyboard, parse_mode=None)
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
        else:
            await msg.reply(text, reply_markup=keyboard, parse_mode=None)
        return
    row = next((r for r in rows if _word_id(r) == value), None)
    if row is None or action not in {"pick", "confirm"}:
        await query.answer(
            "Запись уже изменилась. Открой /dict заново", show_alert=True
        )
        return
    if action == "pick":
        await query.answer()
        keyboard = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    button("Удалить", f"word:confirm:{value}"),
                    button("Отмена", "word:page:0"),
                ],
            ]
        )
        await msg.edit_text(
            f"Удалить слово «{row['word']}»?", parse_mode=None, reply_markup=keyboard
        )
        return
    removed = await db.run(
        Repository.delete_chat_word,
        msg.chat.id,
        row["word"],
        expected_meaning=row["meaning"],
    )
    await query.answer("Удалено" if removed == "deleted" else "Запись уже изменилась")
    await msg.edit_text(
        "Слово удалено." if removed == "deleted" else "Запись уже изменилась.",
        reply_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[button("Словарь", "dict")]]
        ),
    )


def is_dictionary_reply(message: types.Message, bot: types.User) -> bool:
    parent = message.reply_to_message
    return bool(
        message.text
        and not message.text.lstrip().startswith("/")
        and getattr(parent, "text", None) == ADD_PROMPT
        and parent.from_user
        and parent.from_user.id == bot.id
    )


async def dictionary_reply(message: types.Message, db: Database):
    if (
        not message.from_user
        or message.from_user.is_bot
        or (
            message.chat.type != ChatType.PRIVATE
            and not await check_is_admin(message.chat, message.from_user.id)
        )
    ):
        return
    word, sep, meaning = (message.text or "").partition("=")
    if not sep:
        await message.reply(
            "Нужен формат: слово = значение. Ответь на подсказку ещё раз."
        )
        return
    status = await db.run(Repository.add_chat_word, message.chat.id, word, meaning)
    await message.reply(WORD_STATUS[status])


WORD_STATUS = {
    "builtin": "Это встроенное слово. Его нельзя удалить или заменить.",
    "invalid": "Нужно слово или фраза до 40 символов и значение в одну строку до 160 символов.",
    "exists": "Слово уже есть. Чтобы поменять значение, сначала удали его.",
    "added": "Добавил в словарь этого чата.",
    "deleted": "Удалил из словаря этого чата.",
    "missing": "Такого слова в словаре этого чата нет.",
}


async def cmd_vocabulary(message: types.Message, db: Database):
    raw = command_args(message).strip()
    chat_id = message.chat.id
    if not raw:
        await show_dictionary(message, db)
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
        await message.reply(WORD_STATUS[status])
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
