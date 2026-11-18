import asyncio
import html
import logging
import re

from aiogram import Bot, F, Router, types
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from ..database import Database
from ..llm import Ollama
from ..lore import INITIAL_KENTS_PROFILES
from ..repository import Repository
from ..services.render import Mode, clean_response
from ..services.telegram import keep_typing, send_burst_messages
from .access import check_is_admin, resolve_target_user
from .command_args import command_args, reply_html_lines


def parse_positive_ids(text: str) -> list[int]:
    parts = text.split()
    if not all(
        re.fullmatch(r"[0-9]{1,19}", token) and 0 < int(token) < 2**63
        for token in parts
    ):
        return []
    return list(dict.fromkeys(map(int, parts)))


async def cmd_forgetme(message: types.Message, db: Database):
    caller_id = message.from_user.id if message.from_user else 0
    if not caller_id:
        return
    is_admin = await check_is_admin(None, caller_id)

    raw_args = command_args(message)
    target_id = caller_id
    target_name = message.from_user.username or message.from_user.first_name or "кореш"

    # Только админ бота может указать другого пользователя: операция затрагивает всю БД.
    if raw_args and not is_admin:
        await message.reply(
            "Чужие данные может удалять только админ бота. Для своих напиши /forgetme без аргументов."
        )
        return
    if is_admin and (raw_args or message.reply_to_message):
        res_id, res_name, remaining, err = await resolve_target_user(
            db, message, raw_args
        )
        if err:
            await message.reply(err)
            return
        if raw_args and (not res_id or remaining):
            await message.reply("Укажи только @username или числовой ID")
            return
        if res_id:
            target_id = res_id
            target_name = res_name

    prof = await db.run(Repository.get_user_profile, target_id)
    is_kent = bool(
        prof and "persona_notes" in prof.keys() and prof["persona_notes"]
    ) or (target_id in INITIAL_KENTS_PROFILES)
    safe_target_name = html.escape(str(target_name))

    if is_admin:
        if target_id == caller_id:
            prompt_text = (
                "Слышь, админ, ты собрался сносить свои данные из базы.\n"
                "Че делаем перед тем как тереть?"
            )
        else:
            kent_badge = " (наш кент из базы)" if is_kent else " (обычный юзер)"
            prompt_text = (
                f"Слышь, админ, запрос на сброс данных для <b>{safe_target_name}</b> (ID: <code>{target_id}</code>){kent_badge}.\n"
                f"Че именно сносим?"
            )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Только сообщения",
                        callback_data=f"forget:msgs:{target_id}:{caller_id}",
                    ),
                    InlineKeyboardButton(
                        text="💥 Стереть ВСЁ нахуй",
                        callback_data=f"forget:all:{target_id}:{caller_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"forget:cancel:{target_id}:{caller_id}",
                    )
                ],
            ]
        )
    else:
        # Для не-админов: выбора стереть всё нет — кенты защищены!
        if is_kent:
            prompt_text = (
                f"слышь, <b>{safe_target_name}</b>, ты ж наш кент!\n"
                f"твой статус кента и лор в базе защищены от удаления намертво.\n"
                f"но могу подчистить твои сообщения из чата. сносим сообщения?"
            )
        else:
            prompt_text = f"слышь, <b>{safe_target_name}</b>, реально подчистить твои сообщения из базы?"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑 Да, снести сообщения",
                        callback_data=f"forget:msgs:{target_id}:{caller_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Отмена",
                        callback_data=f"forget:cancel:{target_id}:{caller_id}",
                    ),
                ]
            ]
        )

    await message.reply(prompt_text, reply_markup=keyboard, parse_mode="HTML")


async def cb_forgetme(query: CallbackQuery, db: Database):
    match = re.fullmatch(
        r"forget:(cancel|msgs|all):([0-9]{1,19}):([0-9]{1,19})", query.data or ""
    )
    if (
        not isinstance(query.message, types.Message)
        or not match
        or not parse_positive_ids(f"{match[2]} {match[3]}")
    ):
        await query.answer("Ошибка запроса", show_alert=True)
        return
    action, target_id_str, caller_id_str = match.groups()
    target_id, caller_id = int(target_id_str), int(caller_id_str)
    clicker_id = query.from_user.id
    if clicker_id != caller_id:
        await query.answer("слышь, не твоя кнопка, руки убрал!", show_alert=True)
        return

    if (action == "all" or target_id != caller_id) and not await check_is_admin(
        None, clicker_id
    ):
        await query.answer("Для этого нужны права админа бота", show_alert=True)
        return

    if action == "cancel":
        await query.answer("Отменено")
        await query.message.edit_text(
            "Отменил сброс, база цела. Живем дальше.", reply_markup=None
        )
        return

    if action == "all":
        result = await db.run(
            Repository.clear_user_data, target_id, delete_profile=True
        )
        if result is None:
            await query.answer("Не удалось очистить данные", show_alert=True)
            return
        deleted_msgs, deleted_prof = result
        logging.info(
            f"Админ {clicker_id} полностью снес пользователя {target_id}: msgs={deleted_msgs}, prof={deleted_prof}"
        )
        await query.answer("Стерто под ноль")
        res_text = (
            f"💥 Стерто под ноль нахуй!\n"
            f"Пользователь <code>{target_id}</code> полностью выпилен из базы.\n"
            f"Удалено сообщений: {deleted_msgs}, профиль и статус кента снесены."
        )
        await query.message.edit_text(res_text, reply_markup=None, parse_mode="HTML")
        return

    if action == "msgs":
        result = await db.run(
            Repository.clear_user_data, target_id, delete_profile=False
        )
        if result is None:
            await query.answer("Не удалось очистить данные", show_alert=True)
            return
        deleted_msgs, _ = result
        logging.info(
            f"Очищены сообщения пользователя {target_id}: msgs={deleted_msgs} (инициатор: {clicker_id})"
        )
        await query.answer("Сообщения подчищены")
        if target_id == clicker_id:
            res_text = (
                f"Сообщения твои подчистил ({deleted_msgs} шт).\n"
                f"Профиль и статус кента в базе не трогал, всё на месте железобетонно."
            )
        else:
            res_text = (
                f"Сообщения пользователя <code>{target_id}</code> подчищены ({deleted_msgs} шт).\n"
                f"Профиль и статус кента сохранены."
            )
        await query.message.edit_text(res_text, reply_markup=None, parse_mode="HTML")
        return


async def cmd_mydata(message: types.Message, db: Database):
    if not message.from_user:
        return
    user_id = message.from_user.id
    prof = await db.run(Repository.get_user_profile, user_id)
    if not prof:
        await message.reply("в базе про тебя вообще ничего нет\nты призрак нахуй")
        return

    first_seen = prof["first_seen"]
    last_seen = prof["last_seen"]
    msg_count = prof["message_count"]
    uname = prof["user_name"] or "без ника"
    persona_notes = (
        prof["persona_notes"]
        if "persona_notes" in prof.keys() and prof["persona_notes"]
        else ""
    )
    notes_str = (
        f"\nстатус кента: <i>{html.escape(persona_notes[:2000])}</i>"
        if persona_notes
        else ""
    )

    user_auto_facts = await db.run(
        Repository.get_self_facts, message.chat.id, user_id, 5
    )
    auto_facts_str = ""
    if user_auto_facts:
        auto_facts_str = "\n\nавто-подмечено:\n" + "\n".join(
            [f"• {html.escape(f['fact_text'][:160])}" for f in user_auto_facts]
        )

    reply_text = (
        f"че я про тебя помню в базе:\n"
        f"id: <code>{user_id}</code> (@{html.escape(uname)})\n"
        f"сообщений накропал: {msg_count}\n"
        f"первый раз вылез: {first_seen}\n"
        f"крайний раз: {last_seen}"
        f"{notes_str}"
        f"{auto_facts_str}\n\n"
        "Удалить свои сообщения из базы: /forgetme или /забудь"
    )
    await message.reply(reply_text, parse_mode="HTML")


async def cmd_autofacts(message: types.Message, db: Database):
    if not message.from_user:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    user_facts = await db.run(Repository.get_self_facts, chat_id, user_id, 8)
    chat_facts = await db.run(Repository.get_auto_facts, "chat", chat_id, 8)
    top_phrases = await db.run(Repository.get_top_chat_phrases, chat_id, 8)

    lines = ["<b>Автоматически подмеченные факты Сани Пониёба:</b>\n"]
    if user_facts:
        lines.append("<b>С твоих слов в этом чате:</b>")
        for f in user_facts:
            lines.append(
                f"• [#{f['id']}] {html.escape(f['fact_text'][:140])} (упомянуто: {f['mention_count']})"
            )
    else:
        lines.append(
            "<i>Про тебя пока ничего конкретного не подмечено (железо, ОС, игры).</i>"
        )

    lines.append("")
    if chat_facts or top_phrases:
        lines.append("<b>По конфе / чату:</b>")
        for f in chat_facts:
            lines.append(f"• [#{f['id']}] {html.escape(f['fact_text'][:140])}")
        if top_phrases:
            lines.append("<b>Частые фразы конфы:</b>")
            for p in top_phrases:
                lines.append(
                    f"• «{html.escape(p['phrase'][:100])}» (повторов: {p['frequency']})"
                )
    else:
        lines.append(
            "<i>По этой конфе частых повторяющихся тем пока не накопилось.</i>"
        )

    lines.append(
        "\n<i>Админ чата может удалить факты этого чата: <code>/delfact &lt;ID 1&gt; [ID 2] ...</code></i>"
    )
    await message.reply("\n".join(lines), parse_mode="HTML")


async def cmd_delfact(message: types.Message, db: Database):
    """Удаляет авто-подмеченные факты по ID (одному или через пробел): /delfact <ID 1> <ID 2> ..."""
    caller_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь, удалять авто-факты могут только админы конфы или бота!\nруки убрал"
        )
        return

    raw_args = command_args(message)
    fact_ids = parse_positive_ids(raw_args)
    if not fact_ids:
        await message.reply(
            "Укажи один или несколько ID авто-фактов через пробел!\n"
            "Пример: <code>/delfact 109 110 111</code>\n"
            "(номера фактов можно глянуть через <code>/facts</code>)",
            parse_mode="HTML",
        )
        return

    global_admin = await check_is_admin(None, caller_id)
    deleted = await db.run(
        Repository.delete_auto_facts,
        fact_ids,
        chat_id=None if global_admin else message.chat.id,
    )
    if deleted:
        out_lines = [f"Снес {len(deleted)} авто-факт(ов):"]
        for fid, ftext in deleted:
            snippet = ftext[:80] + ("..." if len(ftext) > 80 else "")
            out_lines.append(f"• <b>[#{fid}]</b> <i>{html.escape(snippet)}</i>")
        await reply_html_lines(message, out_lines)
    else:
        await message.reply(
            "Ни один из указанных ID авто-фактов не найден в базе.\nГлянь актуальные ID через /facts"
        )


async def cmd_setkent(message: types.Message, db: Database):
    """Команда для добавления/просмотра/удаления фактов о кенте: /setkent <@username / id / reply> [факт]"""
    raw_args = command_args(message)
    target_id, target_name, notes, err = await resolve_target_user(
        db, message, raw_args, allow_kent_aliases=True
    )

    if err:
        await message.reply(err)
        return

    caller_id = message.from_user.id if message.from_user else 0

    if not target_id:
        help_msg = (
            "<b>Как использовать /setkent (или /кент):</b>\n\n"
            "• <b>Добавить факт о кенте (аппендом):</b> <code>/setkent @username &lt;инфа&gt;</code> (или <code>/кент @username ...</code>)\n"
            "• <b>Через реплай:</b> ответь <code>/setkent &lt;инфа&gt;</code> на любое сообщение кента\n"
            "• <b>Через ID:</b> <code>/setkent 123456789 &lt;инфа&gt;</code>\n"
            "• <b>Посмотреть все факты с номерами:</b> <code>/setkent @username</code> (или <code>/кент @username</code>)\n"
            "• <b>Удалить конкретный факт по номеру:</b> <code>/delkent &lt;ID&gt;</code> (или <code>/setkent @username del &lt;ID&gt;</code>)\n\n"
            "💡 <i>Подробный гайд как правильно расписывать инфу о кентах:</i> <code>/help</code>"
        )
        await message.reply(help_msg, parse_mode="HTML")
        return

    # Проверка на удаление факта через /setkent @user del <ID 1> [ID 2] ...
    del_match = (
        re.match(
            r"^(?:del|rm|delete|удали|удалить|-)\s+([\d\s]+)$",
            notes.strip(),
            re.IGNORECASE,
        )
        if notes
        else None
    )
    if del_match:
        entry_ids = parse_positive_ids(del_match.group(1))
        if not entry_ids:
            await message.reply("Укажи хотя бы один ID для удаления!")
            return
        is_admin = await check_is_admin(message.chat, caller_id)
        if not is_admin:
            await message.reply(
                "слышь, удалять факты из досье кента могут только админы конфы или бота!\nруки убрал"
            )
            return
        deleted = await db.run(
            Repository.delete_kent_fact_entries, entry_ids, target_id
        )
        if deleted:
            out_lines = [
                f"Удалил {len(deleted)} факт(ов) из досье кента <b>{html.escape(str(target_name))}</b>:"
            ]
            for eid, _, content in deleted:
                snippet = content[:80] + ("..." if len(content) > 80 else "")
                out_lines.append(f"• <b>[#{eid}]</b> <i>{html.escape(snippet)}</i>")
            await reply_html_lines(message, out_lines)
        else:
            await message.reply(
                f"Указанные ID фактов не найдены у кента {target_name}.\n/setkent {target_id} напиши чтоб глянуть все факты с номерами"
            )
        return

    # Если заметок нет — режим просмотра
    if not notes:
        entries = await db.run(Repository.get_kent_fact_entries, target_id)
        is_initial_kent = target_id in INITIAL_KENTS_PROFILES

        if entries:
            lines = [
                f"Досье кента <b>{html.escape(str(target_name))}</b> (ID: <code>{target_id}</code>):\n"
            ]
            for e in entries:
                lines.append(f"<b>[#{e['id']}]</b> {html.escape(e['content'])}")
            lines.append(
                f"\n➕ <i>Добавить факт:</i> <code>/setkent {target_id} &lt;новый факт&gt;</code>"
            )
            lines.append(
                f"❌ <i>Удалить факт:</i> <code>/delkent &lt;ID&gt;</code> (или <code>/setkent {target_id} del &lt;ID&gt;</code>)"
            )
            lines.append(
                f"💡 <i>Пояснить за кента:</i> <code>/ктотакой {target_id}</code>"
            )
            await reply_html_lines(message, lines)
        else:
            if is_initial_kent:
                kb = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="♻️ Восстановить базовый лор",
                                callback_data=f"restore:one:{target_id}:{caller_id}",
                            )
                        ]
                    ]
                )
                await message.reply(
                    f"Про кента <b>{html.escape(str(target_name))}</b> (<code>{target_id}</code>) заметок в базе сейчас нет, "
                    f"но он есть в списке исходных кентов бота.\nМожешь восстановить его лор по кнопке ниже:",
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            else:
                await message.reply(
                    f"Про кента <b>{html.escape(str(target_name))}</b> (<code>{target_id}</code>) заметок пока нет.\n"
                    f"Добавить: <code>/setkent {target_id} &lt;инфа&gt;</code>",
                    parse_mode="HTML",
                )
        return

    # Защита: менять статус кентов могут админы чата и админы бота
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь ты кто такой чтобы кентов переписывать\nправа не доросли (только для админов конфы и бота)"
        )
        return

    reply = message.reply_to_message
    author = reply.from_user if reply else None
    if author and author.id != target_id:
        author = None
    new_id = await db.run(
        Repository.add_kent_fact_entry,
        target_id,
        notes,
        user_name=(author.username or "") if author else "",
        first_name=(author.first_name or "") if author else "",
        added_by=caller_id,
    )
    if not new_id:
        await message.reply("Не удалось сохранить факт о кенте")
        return
    await message.reply(
        f"Добавил факт <b>[#{new_id}]</b> кенту <b>{html.escape(str(target_name))}</b> (<code>{target_id}</code>):\n\n"
        f"<i>{html.escape(notes[:500])}</i>\n\n"
        f"💡 <i>Все факты кента:</i> <code>/setkent {target_id}</code>\n"
        f"❌ <i>Удалить этот факт:</i> <code>/delkent {new_id}</code>",
        parse_mode="HTML",
    )


async def cmd_delkent(message: types.Message, db: Database):
    """Удаляет факт(ы) о кенте по ID (одному или через пробел): /delkent <ID 1> <ID 2>... или /delkent @username <ID 1> <ID 2>..."""
    caller_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь, удалять факты о кентах могут только админы конфы или бота!\nруки убрал"
        )
        return

    raw_args = command_args(message)
    target_id = None
    id_source = raw_args
    if raw_args.startswith("@"):
        target_id, _, id_source, error = await resolve_target_user(
            db, message, raw_args, allow_kent_aliases=True
        )
        if error or target_id is None:
            await message.reply(error or "Кента не нашел")
            return
    entry_ids = parse_positive_ids(id_source)

    if not entry_ids:
        await message.reply(
            "Укажи один или несколько ID фактов кента для удаления через пробел!\n"
            "Пример: <code>/delkent 5</code> или <code>/delkent 5 6 7</code> или <code>/delkent @username 5 6 7</code>\n"
            "(список фактов с номерами: <code>/setkent @username</code>)",
            parse_mode="HTML",
        )
        return

    deleted = await db.run(Repository.delete_kent_fact_entries, entry_ids, target_id)
    if deleted:
        out_lines = [f"Снес {len(deleted)} факт(ов) из базы кентов:"]
        for eid, uid, content in deleted:
            snippet = content[:80] + ("..." if len(content) > 80 else "")
            out_lines.append(
                f"• <b>[#{eid}]</b> (кент {uid}): <i>{html.escape(snippet)}</i>"
            )
        await reply_html_lines(message, out_lines)
    else:
        await message.reply(
            f"Ни один из указанных ID ({entry_ids}) не найден в базе кентов.\n/setkent @user напиши чтоб глянуть номера фактов"
        )


async def cmd_restorekents(message: types.Message):
    """Команда для восстановления базовых кентов конфы (INITIAL_KENTS_PROFILES)."""
    caller_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь, восстанавливать кентов могут только админы конфы или создатель бота!\nруки убрал"
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Восстановить базовый лор ({len(INITIAL_KENTS_PROFILES)})",
                    callback_data=f"restore:all:{caller_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена", callback_data=f"restore:cancel:{caller_id}"
                )
            ],
        ]
    )
    await message.reply(
        "<b>Восстановление базы кентов:</b>\n"
        "Добавлю недостающие базовые заметки. Ручные факты останутся на месте.\n\n"
        "Восстанавливаем?",
        reply_markup=keyboard,
        parse_mode="HTML",
    )


async def cb_restorekents(query: CallbackQuery, db: Database):
    match = re.fullmatch(
        r"restore:(?:(all|cancel)|one:([0-9]{1,19})):([0-9]{1,19})", query.data or ""
    )
    if not isinstance(query.message, types.Message) or not match:
        await query.answer("Ошибка запроса", show_alert=True)
        return
    action = match[1] or "one"
    target_uid = int(match[2]) if match[2] else None
    if query.from_user.id != int(match[3]):
        await query.answer("Это не твоя кнопка", show_alert=True)
        return

    clicker_id = query.from_user.id
    chat = query.message.chat if query.message else None
    clicker_is_admin = await check_is_admin(chat, clicker_id)

    if not clicker_is_admin:
        await query.answer(
            "Руки убрал, восстанавливать могут только админы чата или бота!",
            show_alert=True,
        )
        return

    if action == "cancel":
        await query.answer("Отменено")
        await query.message.edit_text(
            "Отменил восстановление. База осталась как была.", reply_markup=None
        )
        return

    if action == "all":
        count = await db.run(Repository.restore_initial_kents)
        if not count:
            await query.answer("Не удалось восстановить базовый лор", show_alert=True)
            return
        await query.answer("Кенты восстановлены!")
        await query.message.edit_text(
            f"Базовые заметки проверены для {count} кентов. Ручные факты на месте.",
            reply_markup=None,
            parse_mode="HTML",
        )
        return

    if action == "one" and target_uid:
        success = await db.run(Repository.restore_single_kent, target_uid)
        if success:
            await query.answer("Лор кента восстановлен!")
            pdata = INITIAL_KENTS_PROFILES[target_uid]
            await query.message.edit_text(
                f"✅ <b>Восстановлен базовый лор кента {pdata['user_name']} (<code>{target_uid}</code>)!</b>\n\n"
                f"<i>{pdata['notes']}</i>",
                reply_markup=None,
                parse_mode="HTML",
            )
        else:
            await query.answer("Не найден в исходном списке", show_alert=True)
        return


async def cmd_kentsummary(message: types.Message, bot: Bot, db: Database, llm: Ollama):
    """Генерирует живое нейро-саммари по всей банде или по конкретному кенту на основе фактов из БД."""
    raw_args = command_args(message)

    target_id = None
    target_name = ""

    # Проверяем, указан ли конкретный кент через аргумент (@username, id) или реплай
    if raw_args or message.reply_to_message:
        t_id, t_name, _, err = await resolve_target_user(
            db, message, raw_args, allow_kent_aliases=True
        )
        if err:
            await message.reply(err)
            return
        if t_id:
            target_id = t_id
            target_name = t_name

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(bot, message.chat.id, stop_typing))

    try:
        if target_id:
            # Саммари по конкретному кенту
            prof = await db.run(Repository.get_user_profile, target_id)
            if not prof or not prof["persona_notes"]:
                stop_typing.set()
                await typing_task
                m_cnt = prof["message_count"] if prof else 0
                await message.reply(
                    f"Про кента <b>{html.escape(str(target_name))}</b> (<code>{target_id}</code>) пока нет сохраненных заметок в базе (сообщений: {m_cnt}).\n"
                    f"Админ может добавить лор: <code>/setkent @{target_name} &lt;факты&gt;</code>",
                    parse_mode="HTML",
                )
                return

            prompt = [
                {
                    "role": "user",
                    "content": (
                        f"Вот сохраненные факты о кенте из базы данных:\n"
                        f"Имя/Ник: {prof['first_name'] or prof['user_name'] or target_name} (@{prof['user_name'] or target_name}), сообщений в чате: {prof['message_count']}\n"
                        f"Факты и лор: {prof['persona_notes']}\n\n"
                        "Инструкция: Поясни за этого кента живым ответом Сани Пониёба! "
                        "Расскажи в паре смачных пацанских предложений кто он такой, какие за ним мемы и как ты к нему относишься. "
                        "Отвечай в своем авторском стиле (перекуп, сленг, мат, без точек на конце, сочно и с юмором, без знаков ###)."
                    ),
                }
            ]
            summary_text = await llm.generate(prompt, is_detailed_request=False)
            summary_text, _, _ = clean_response(summary_text, Mode.DETAIL)

        else:
            # Общее саммари по всей банде кентов
            kents = await db.run(Repository.get_all_kents)
            if not kents:
                stop_typing.set()
                await typing_task
                await message.reply("В базе пока нет кентов с заполненным лором.")
                return

            kents_list = []
            for k in kents:
                k_name = k["first_name"] or k["user_name"] or f"Юзер {k['user_id']}"
                k_uname = f" (@{k['user_name']})" if k["user_name"] else ""
                kents_list.append(f"• {k_name}{k_uname}: {k['persona_notes'][:180]}")
            kents_text = "\n".join(kents_list)

            prompt = [
                {
                    "role": "user",
                    "content": (
                        f"Вот список кентов нашей конфы и сохраненные факты о них из базы данных:\n{kents_text}\n\n"
                        "Инструкция: Напиши сочное, угарное саммари по всей нашей банде/кентам от первого лица Сани Пониёба. "
                        "Пройдись по каждому пацану коротко и смачно (по 1-2 хлестких пацанских предложения без точек на конце, со сленгом, матом и конфовыми мемами). "
                        "Никаких списков из википедии и знаков ###! Живой монолог перекупа и брони."
                    ),
                }
            ]
            summary_text = await llm.generate(prompt, is_detailed_request=True)
            summary_text, _, _ = clean_response(summary_text, Mode.DETAIL)

    finally:
        stop_typing.set()
        await typing_task

    await send_burst_messages(
        bot,
        message.chat.id,
        message.message_id,
        summary_text,
        is_detailed=True,
    )


async def cmd_setchat(message: types.Message, db: Database):
    """Команда для добавления/просмотра/удаления лора конфы: /setchat <текст> или /setchat <chat_id> <текст>"""
    raw_args = command_args(message)
    caller_id = message.from_user.id if message.from_user else 0

    target_chat_id = message.chat.id
    target_chat_title = message.chat.title or "Текущий чат"
    new_lore = raw_args

    # Поддержка явного указания chat_id первым аргументом (например: /setchat -1003672879438 факты...)
    if raw_args:
        first_token, *rest = raw_args.split(maxsplit=1)
        if re.fullmatch(r"-100[0-9]+", first_token):
            parsed = parse_positive_ids(first_token[1:])
            if not parsed:
                await message.reply("Некорректный ID чата")
                return
            target_chat_id = -parsed[0]
            if target_chat_id != message.chat.id and not await check_is_admin(
                None, caller_id
            ):
                await message.reply(
                    "Работать с лором другого чата может только админ бота"
                )
                return
            new_lore = rest[0] if rest else ""
            saved_prof = await db.run(Repository.get_chat_profile, target_chat_id)
            if saved_prof and saved_prof["title"]:
                target_chat_title = saved_prof["title"]
            else:
                target_chat_title = f"Чат {target_chat_id}"

    # Если текста нет — режим просмотра списка записей лора
    if not new_lore:
        entries = await db.run(Repository.get_chat_lore_entries, target_chat_id)
        if entries:
            entries_lines = [
                f"🏛 <b>Лор конфы «{html.escape(target_chat_title)}»</b> (<code>{target_chat_id}</code>):\n"
            ]
            for e in entries:
                entries_lines.append(f"<b>[#{e['id']}]</b> {html.escape(e['content'])}")
            entries_lines.append(
                "\n➕ <i>Добавить факт:</i> <code>/setchat &lt;новый факт/правило&gt;</code>"
            )
            entries_lines.append(
                "❌ <i>Удалить факт:</i> <code>/delchat &lt;ID&gt;</code> (или <code>/setchat del &lt;ID&gt;</code>)"
            )
            await reply_html_lines(message, entries_lines)
        else:
            await message.reply(
                f"Для конфы <b>{html.escape(target_chat_title)}</b> (<code>{target_chat_id}</code>) факты еще не записаны.\n\n"
                f"Чтобы добавить факт о конфе, напиши: <code>/setchat &lt;факты о конфе, правила, приколы&gt;</code> (или <code>/конфа &lt;факты&gt;</code>)",
                parse_mode="HTML",
            )
        return

    # Проверка на удаление факта через /setchat del <ID 1> [ID 2] ...
    del_match = (
        re.match(
            r"^(?:del|rm|delete|удали|удалить|-)\s+([\d\s]+)$",
            new_lore.strip(),
            re.IGNORECASE,
        )
        if new_lore
        else None
    )
    if del_match:
        entry_ids = parse_positive_ids(del_match.group(1))
        if not entry_ids:
            await message.reply("Укажи хотя бы один ID для удаления!")
            return
        is_admin = await check_is_admin(message.chat, caller_id)
        if not is_admin:
            await message.reply(
                "слышь, удалять факты из лора конфы могут только админы чата или создатель!\nруки убрал"
            )
            return
        deleted = await db.run(
            Repository.delete_chat_lore_entries, entry_ids, target_chat_id
        )
        if deleted:
            out_lines = [
                f"Удалил {len(deleted)} запись(ей) из лора конфы «{html.escape(target_chat_title)}» ({target_chat_id}):"
            ]
            for eid, _, content in deleted:
                snippet = content[:80] + ("..." if len(content) > 80 else "")
                out_lines.append(f"• <b>[#{eid}]</b> <i>{html.escape(snippet)}</i>")
            await reply_html_lines(message, out_lines)
        else:
            await message.reply(
                "Указанные ID записей не найдены в лоре этой конфы.\n"
                "Напиши <code>/setchat</code> чтобы глянуть список фактов с номерами.",
                parse_mode="HTML",
            )
        return

    # Проверка прав: менять лор конфы могут админы бота либо админы группы
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь, менять лор конфы могут только админы чата или создатель!\nруки убрал"
        )
        return

    entry_id = await db.run(
        Repository.add_chat_lore_entry,
        target_chat_id,
        target_chat_title,
        new_lore,
        caller_id,
    )
    if entry_id is None:
        await message.reply("Не удалось сохранить лор чата")
        return
    await message.reply(
        f"Добавил факт <b>[#{entry_id}]</b> в лор конфы <b>{html.escape(target_chat_title)}</b> (<code>{target_chat_id}</code>):\n\n"
        f"<i>{html.escape(new_lore[:500])}</i>\n\n"
        f"Теперь Саня будет учитывать этот факт во всех беседах здесь.\n\n"
        f"💡 <i>Все факты конфы:</i> <code>/setchat</code>\n"
        f"❌ <i>Удалить этот факт:</i> <code>/delchat {entry_id}</code>",
        parse_mode="HTML",
    )


async def cmd_delchat(message: types.Message, db: Database):
    """Удаляет отдельный факт или пачку фактов из лора конфы по ID: /delchat <ID 1> <ID 2> ..."""
    caller_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, caller_id)
    if not is_admin:
        await message.reply(
            "слышь, удалять факты из лора конфы могут только админы чата или бота!\nруки убрал"
        )
        return

    raw_args = command_args(message)
    target_chat_id = message.chat.id

    parts = raw_args.split()
    if parts and re.fullmatch(r"-100[0-9]+", parts[0]):
        parsed = parse_positive_ids(parts[0][1:])
        if not parsed:
            await message.reply("Некорректный ID чата")
            return
        target_chat_id = -parsed[0]
        if target_chat_id != message.chat.id and not await check_is_admin(
            None, caller_id
        ):
            await message.reply("Работать с лором другого чата может только админ бота")
            return
        entry_ids = parse_positive_ids(" ".join(parts[1:]))
    else:
        entry_ids = parse_positive_ids(" ".join(parts))

    if not entry_ids:
        await message.reply(
            "Укажи один или несколько ID записей лора конфы для удаления через пробел!\n"
            "Пример: <code>/delchat 3</code> или <code>/delchat 3 4 5</code> или <code>/delchat -1001626651838 3 4 5</code>\n"
            "(список всех записей с номерами: <code>/setchat</code>)",
            parse_mode="HTML",
        )
        return

    deleted = await db.run(
        Repository.delete_chat_lore_entries, entry_ids, target_chat_id
    )
    if deleted:
        out_lines = [
            f"Снес {len(deleted)} запись(ей) из лора конфы (<code>{target_chat_id}</code>):"
        ]
        for eid, _, content in deleted:
            snippet = content[:80] + ("..." if len(content) > 80 else "")
            out_lines.append(f"• <b>[#{eid}]</b> <i>{html.escape(snippet)}</i>")
        await reply_html_lines(message, out_lines)
    else:
        await message.reply(
            f"Ни одна из записей с ID {entry_ids} не найдена в лоре конфы.\n"
            f"Напиши <code>/setchat</code> чтоб глянуть список фактов и их ID",
            parse_mode="HTML",
        )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(
        cmd_forgetme,
        Command(
            "forgetme",
            "clearmydata",
            "clear_my_memory",
            "забудьменя",
            "забудь",
            "стеретьменя",
            "удалименя",
            "очистименя",
        ),
    )
    router.callback_query.register(cb_forgetme, F.data.startswith("forget:"))
    router.message.register(
        cmd_mydata,
        Command("mydata", "profile", "моиданные", "профиль", "досье", "стата", "ктоя"),
    )
    router.message.register(
        cmd_autofacts, Command("facts", "autofacts", "факты", "автофакты", "подмечено")
    )
    router.message.register(
        cmd_delfact,
        Command(
            "delfact",
            "delfacts",
            "удалифакт",
            "удалитьфакт",
            "минусфакт",
            "delautofact",
            "delautofacts",
        ),
    )
    router.message.register(
        cmd_setkent,
        Command(
            "setkent", "kent", "кент", "сеткент", "запомникента", "кореш", "досьекента"
        ),
    )
    router.message.register(
        cmd_delkent, Command("delkent", "delkentfact", "удалитькента", "delkentnote")
    )
    router.message.register(
        cmd_restorekents,
        Command(
            "restorekents",
            "resetkents",
            "восстановитькентов",
            "вернутькентов",
            "сброскентов",
        ),
    )
    router.callback_query.register(cb_restorekents, F.data.startswith("restore:"))
    router.message.register(
        cmd_kentsummary,
        Command(
            "kentsummary",
            "kents",
            "кенты",
            "саммари",
            "саммарипокентам",
            "базакентов",
            "пояснизакентов",
            "ктотакой",
            "поясниза",
        ),
    )
    router.message.register(
        cmd_setchat,
        Command(
            "setchat",
            "setconf",
            "chatlore",
            "conflore",
            "конфа",
            "чат",
            "лорконфы",
            "лорчата",
            "сетчат",
            "сетконф",
            "фактыконфы",
        ),
    )
    router.message.register(
        cmd_delchat,
        Command(
            "delchat",
            "delchatlore",
            "delconflore",
            "удаличат",
            "удалитьлор",
            "минуслор",
            "удалилор",
        ),
    )
    return router
