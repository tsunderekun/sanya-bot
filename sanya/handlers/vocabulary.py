from hashlib import sha256

from aiogram import types
from aiogram.enums import ChatType

from ..database import Database
from ..repository import Repository
from ..vocabulary import BUILTIN_INDEX, BUILTIN_WORDS, word_key
from .access import check_is_admin
from .command_args import command_args
from .reply_style import button, edit_panel, panel_keyboard

HELP = "/словарь добавить слово = значение\n/словарь удалить слово"
ADD_PROMPT = "Ответь на это сообщение: слово = значение"
PAGE_SIZE = 6


async def show_dictionary(message: types.Message, db: Database):
    text, keyboard = await dictionary_view(db, message.chat.id, 0)
    await message.reply(text, reply_markup=keyboard, parse_mode=None)


async def dictionary_view(db: Database, chat_id: int, page: int):
    rows = await db.run(Repository.list_chat_words, chat_id)
    entries = [(word, meaning, "встроенное") for word, meaning in BUILTIN_WORDS]
    entries.extend((row["word"], row["meaning"], "из этого чата") for row in rows)
    last = max(0, (len(entries) - 1) // PAGE_SIZE)
    page = min(page, last)
    start = page * PAGE_SIZE
    lines = [f"Словарь · {page + 1}/{last + 1}", ""]
    lines.extend(
        f"{word} — {meaning} ({kind})"
        for word, meaning, kind in entries[start : start + PAGE_SIZE]
    )
    nav = []
    if page:
        nav.append(button("Назад", f"dict:{page - 1}"))
    if page < last:
        nav.append(button("Дальше", f"dict:{page + 1}"))
    buttons = [nav] if nav else []
    buttons.extend(
        [
            [
                button("Добавить слово", "word:add"),
                button("Удалить слово", "word:page:0"),
            ],
            [button("Все настройки", "view:all")],
        ]
    )
    return "\n".join(lines), panel_keyboard(buttons)


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
        keyboard = panel_keyboard(buttons)
        await edit_panel(msg, text, keyboard)
        return
    row = next((r for r in rows if _word_id(r) == value), None)
    if row is None or action not in {"pick", "confirm"}:
        await query.answer(
            "Запись уже изменилась. Открой /dict заново", show_alert=True
        )
        return
    if action == "pick":
        await query.answer()
        keyboard = panel_keyboard(
            [
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
        reply_markup=panel_keyboard([[button("Словарь", "dict")]]),
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
