import logging

from aiogram import F, Router, types
from aiogram.enums import ChatType
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository
from .access import check_is_admin
from .command_args import command_args
from .lore_panel import PREFIX as LORE_PANEL_PREFIX
from .lore_panel import lore_panel_button
from .reply_style import (
    MODE_ARGS,
    MODE_LABELS,
    SETTINGS_PREFIX,
    cmd_lore_level,
    cmd_settings,
    cmd_sticker_activity,
    cmd_tone,
    show_settings,
    update_settings,
)
from .vocabulary import cmd_vocabulary, dictionary_button

log = logging.getLogger(__name__)
RELEASE_ACK = "reply-mode:seen"


async def cmd_reply_mode(message: types.Message, db: Database):
    arg = command_args(message).casefold()
    chat = message.chat
    if not arg:
        await show_settings(message, db, "mode")
        return
    if arg not in MODE_ARGS:
        await message.reply("Выбери /режим норм или /режим рофл")
        return
    if chat.type != ChatType.PRIVATE and (
        not message.from_user or not await check_is_admin(chat, message.from_user.id)
    ):
        await message.reply("В группе режим может поменять только админ.")
        return
    mode = MODE_ARGS[arg]
    await db.run(Repository.set_chat_reply_mode, chat.id, mode)
    await message.reply(f"Режим этого чата: {MODE_LABELS[mode]}")


async def confirm_release(query: types.CallbackQuery):
    msg = query.message
    if (
        not isinstance(msg, types.Message)
        or msg.chat.type != ChatType.PRIVATE
        or msg.chat.id != query.from_user.id
    ):
        await query.answer()
        return
    await query.answer("Vizhu, podtverzhdenie prinyato")
    await msg.edit_reply_markup(reply_markup=None)
    log.info("Reply mode release acknowledged")


async def cmd_start(message: types.Message, db: Database):
    if message.from_user:
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or ""
        await db.run(Repository.update_user_profile, message.from_user.id, uname, fname)
    await message.reply(
        "Здарова\nЯ Саня Пониёб\n"
        "Че надо — спрашивай, по железу, сетям, стейблу поясню защеку\n\n"
        "Список всех команд: /help (или /помощь)",
        parse_mode="HTML",
    )


async def cmd_clear(message: types.Message, db: Database):
    uid = message.from_user.id if message.from_user else 0
    if message.chat.type != ChatType.PRIVATE and not await check_is_admin(
        message.chat, uid
    ):
        await message.reply("Историю группы может очистить только админ")
        return
    count = await db.run(Repository.clear_history, message.chat.id)
    if count is None:
        await message.reply("Не удалось очистить историю")
        return
    await message.reply(
        f"Историю чата в базе очистил ({count} сообщений). Лор и профили не трогал."
    )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(cmd_start, Command("start", "старт"))
    router.message.register(cmd_settings, Command("settings", "настройки"))
    router.message.register(
        cmd_sticker_activity, Command("stickeractivity", "автостикеры")
    )
    router.message.register(cmd_reply_mode, Command("mode", "режим"))
    router.message.register(cmd_tone, Command("tone", "тон", "агрессия"))
    router.message.register(cmd_lore_level, Command("lore", "лор"))
    router.message.register(cmd_vocabulary, Command("dict", "словарь"))
    router.callback_query.register(confirm_release, F.data == RELEASE_ACK)
    router.callback_query.register(
        dictionary_button, F.data.startswith(SETTINGS_PREFIX + "word:")
    )
    router.callback_query.register(
        lore_panel_button, F.data.startswith(LORE_PANEL_PREFIX)
    )
    from .help_pages import help_page

    router.callback_query.register(
        help_page, F.data.startswith(SETTINGS_PREFIX + "page:")
    )
    router.callback_query.register(update_settings, F.data.startswith(SETTINGS_PREFIX))
    router.message.register(
        cmd_clear,
        Command(
            "clear", "reset", "сброс", "очистить", "чисти", "ресет", "забытьдиалог"
        ),
    )
    return router
