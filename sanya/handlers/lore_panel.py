import re

from aiogram import types
from aiogram.exceptions import TelegramAPIError

from ..database import Database
from ..lore import INITIAL_KENTS_PROFILES
from ..repository import Repository
from .access import check_is_admin
from .reply_style import button, edit_panel, panel_keyboard

PREFIX = "settings:facts:"
PAGE_SIZE = 6
CHAT_INPUT = "Ответь на это сообщение новым фактом для лора этой конфы."
KENT_INPUT = "Ответь на это сообщение новым фактом о кенте"


def control(label, action, target, value=0):
    return button(label, f"facts:{action}:{target}:{value}")


def kent_name(profile, target):
    if profile:
        return str(profile["user_name"] or profile["first_name"] or target)[:80]
    return str(target)


def page_slice(rows, page):
    last = max(0, (len(rows) - 1) // PAGE_SIZE)
    page = min(page, last)
    return rows[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, last


def add_navigation(rows, action, target, page, last):
    nav = []
    if page:
        nav.append(control("Назад", action, target, page - 1))
    if page < last:
        nav.append(control("Дальше", action, target, page + 1))
    if nav:
        rows.append(nav)


async def kent_list(db, page):
    kents = await db.run(Repository.get_all_kents)
    visible, page, last = page_slice(kents, page)
    rows = [
        [control(kent_name(kent, kent["user_id"]), "view", kent["user_id"])]
        for kent in visible
    ]
    add_navigation(rows, "list", 0, page, last)
    text = f"Кенты · {page + 1}/{last + 1}\n\n"
    if not kents:
        text += "Записанных кентов пока нет.\n"
    text += "Выбери кента или напиши /кент @username. Можно вызвать /кент реплаем."
    return text, panel_keyboard(rows)


async def facts_view(db, chat, actor, target, page=0, *, picking=False, name=None):
    if target:
        facts = await db.run(Repository.get_kent_fact_entries, target)
        if not name:
            profile = await db.run(Repository.get_user_profile, target)
            name = kent_name(profile, target)
        heading = f"Досье кента {str(name)[:80]}"
    else:
        facts = await db.run(Repository.get_chat_lore_entries, chat.id)
        heading = "Лор конфы"
    visible, page, last = page_slice(facts, page)
    lines = [f"{heading} · {page + 1}/{last + 1}", ""]
    rows = []
    for fact in visible:
        content = fact["content"]
        preview = content[:285] + ("…" if len(content) > 285 else "")
        lines.append(f"[#{fact['id']}] {preview}")
        if picking:
            rows.append([control(f"Выбрать №{fact['id']}", "pick", target, fact["id"])])
    if not facts:
        lines.append("Фактов пока нет.")
    elif picking:
        lines.append("\nВыбери факт для удаления.")
    else:
        lines.append("\nНажми номер, чтобы открыть факт.")
        rows.append(
            [control(f"№{fact['id']}", "read", target, fact["id"]) for fact in visible]
        )
    add_navigation(rows, "delete" if picking else "view", target, page, last)
    if picking:
        rows.append([control("Отмена", "view", target, page)])
    elif await check_is_admin(chat, actor):
        actions = [control("Добавить факт", "add", target)]
        if facts:
            actions.append(control("Удалить факт", "delete", target, page))
        rows.append(actions)
        if facts:
            rows.append([control("Удалить всё", "wipe", target)])
        if target in INITIAL_KENTS_PROFILES and not facts:
            rows.append([control("Восстановить базовый лор", "restore", target)])
    if target:
        rows.append([control("К списку кентов", "list", 0)])
    return "\n".join(lines), panel_keyboard(rows)


async def show_kent_panel(message, db, target=None, name=None):
    if target:
        text, keyboard = await facts_view(
            db,
            message.chat,
            message.from_user.id if message.from_user else 0,
            target,
            name=name,
        )
    else:
        text, keyboard = await kent_list(db, 0)
    await message.reply(text, parse_mode=None, reply_markup=keyboard)


async def show_chat_lore_panel(message, db):
    text, keyboard = await facts_view(
        db, message.chat, message.from_user.id if message.from_user else 0, 0
    )
    await message.reply(text, parse_mode=None, reply_markup=keyboard)


def _input_target(text):
    if text == CHAT_INPUT:
        return 0
    match = re.fullmatch(rf"{KENT_INPUT} \(ID ([0-9]{{1,19}})\)\.", text or "")
    if match and 0 < int(match[1]) < 2**63:
        return int(match[1])
    return None


def is_lore_panel_reply(message, bot_user):
    parent = message.reply_to_message
    return bool(
        isinstance(parent, types.Message)
        and parent.from_user
        and parent.from_user.id == bot_user.id
        and parent.chat.id == message.chat.id
        and _input_target(parent.text) is not None
    )


async def save_lore_panel_reply(message, db):
    parent = message.reply_to_message
    target = _input_target(parent.text)
    if target is None:
        return
    actor = message.from_user.id if message.from_user and not message.sender_chat else 0
    if not await check_is_admin(message.chat, actor):
        await message.reply("Менять лор могут только админы.")
        return
    content = (message.text or "").strip()
    if not content or content.startswith("/"):
        await message.reply("Нужен факт обычным текстом. Ответь на подсказку ещё раз.")
        return
    if target:
        saved = await db.run(
            Repository.add_kent_fact_entry, target, content, added_by=actor
        )
    else:
        saved = await db.run(
            Repository.add_chat_lore_entry,
            message.chat.id,
            message.chat.title or "Текущий чат",
            content,
            actor,
        )
    if not saved:
        await message.reply("Не удалось сохранить факт.")
        return
    try:
        await parent.delete()
    except TelegramAPIError:
        pass
    await message.reply(f"Добавил факт №{saved}.")


async def lore_panel_button(query, db: Database):
    msg = query.message
    match = re.fullmatch(
        r"([a-z]+):([0-9]{1,19}):([0-9]{1,19})", (query.data or "").removeprefix(PREFIX)
    )
    if not isinstance(msg, types.Message) or not match:
        await query.answer("Открой меню заново.")
        return
    action, target, value = match[1], int(match[2]), int(match[3])
    if (
        action
        not in {
            "list",
            "view",
            "read",
            "add",
            "delete",
            "pick",
            "confirm",
            "restore",
            "wipe",
            "wipeconfirm",
        }
        or max(target, value) >= 2**63
    ):
        await query.answer("Кнопка устарела.")
        return
    actor = query.from_user.id
    if action in {
        "add",
        "delete",
        "pick",
        "confirm",
        "restore",
        "wipe",
        "wipeconfirm",
    } and not await check_is_admin(msg.chat, actor):
        await query.answer("Менять лор могут только админы.", show_alert=True)
        return
    if action == "wipe":
        getter = (
            Repository.get_kent_fact_entries
            if target
            else Repository.get_chat_lore_entries
        )
        facts = await db.run(getter, target or msg.chat.id)
        if not facts:
            await query.answer("Удалять нечего.")
            await edit_panel(msg, *await facts_view(db, msg.chat, actor, target))
            return
        scope = (
            "Удалить все факты из досье этого кента? Досье общее для всех чатов."
            if target
            else "Удалить весь ручной лор этой конфы?"
        )
        rows = [
            [
                control("Да, удалить", "wipeconfirm", target, facts[-1]["id"]),
                control("Отмена", "view", target),
            ]
        ]
        await query.answer()
        await edit_panel(
            msg,
            f"{scope}\nЗаписей к удалению: {len(facts)}. Автофакты и история не затронуты.",
            panel_keyboard(rows),
        )
        return
    if action == "wipeconfirm":
        remover = Repository.clear_kent_facts if target else Repository.clear_chat_lore
        deleted = await db.run(remover, target or msg.chat.id, value)
        await query.answer(f"Удалено записей: {deleted}.")
        await edit_panel(msg, *await facts_view(db, msg.chat, actor, target))
        return
    if action == "restore":
        restored = target in INITIAL_KENTS_PROFILES and await db.run(
            Repository.restore_single_kent, target
        )
        await query.answer(
            "Базовый лор восстановлен." if restored else "Не удалось восстановить лор."
        )
        await edit_panel(msg, *await facts_view(db, msg.chat, actor, target))
        return
    if action == "add":
        await query.answer()
        await msg.reply(
            f"{KENT_INPUT} (ID {target})." if target else CHAT_INPUT,
            reply_markup=types.ForceReply(input_field_placeholder="Новый факт"),
            parse_mode=None,
        )
        return
    if action in {"read", "pick", "confirm"}:
        getter = (
            Repository.get_kent_fact_entries
            if target
            else Repository.get_chat_lore_entries
        )
        entries = await db.run(getter, target or msg.chat.id)
        index = next(
            (i for i, entry in enumerate(entries) if entry["id"] == value), None
        )
        if index is None:
            await query.answer("Этого факта больше нет.", show_alert=True)
            await edit_panel(msg, *await facts_view(db, msg.chat, actor, target))
            return
        page = index // PAGE_SIZE
        content = entries[index]["content"]
        if action == "confirm":
            remover = (
                Repository.delete_kent_fact_entries
                if target
                else Repository.delete_chat_lore_entries
            )
            deleted = await db.run(remover, [value], target or msg.chat.id)
            await query.answer("Факт удалён." if deleted else "Факт не удалён.")
            await edit_panel(msg, *await facts_view(db, msg.chat, actor, target, page))
            return
        if action == "pick":
            heading = f"Удалить факт №{value}?"
            rows = [
                [
                    control("Удалить", "confirm", target, value),
                    control("Отмена", "delete", target, page),
                ]
            ]
        else:
            heading = f"Факт №{value}"
            rows = [[control("К фактам", "view", target, page)]]
        # Telegram messages are limited to 4096 characters. Legacy entries may be longer.
        excerpt = content[:1930] + (
            "…\nДлинный факт можно посмотреть старой командой с аргументом."
            if len(content) > 1930
            else ""
        )
        await query.answer()
        await edit_panel(msg, f"{heading}\n\n{excerpt}", panel_keyboard(rows))
        return
    await query.answer()
    if action == "list":
        view = await kent_list(db, value)
    else:
        view = await facts_view(
            db, msg.chat, actor, target, value, picking=action == "delete"
        )
    await edit_panel(msg, *view)
