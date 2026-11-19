import html
import logging
import re

from aiogram import Bot, Router, types
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository
from ..services.stickers import send_sanya_sticker
from .access import check_is_admin
from .command_args import command_args, reply_html_lines

_PACK = re.compile(
    r"(?:(?:https?://)?t\.me/addstickers(?:/|\?set=))?([a-z0-9_]{1,64})/?", re.I
)


def pack_name_from_arg(arg: str) -> str | None:
    match = _PACK.fullmatch(arg)
    return match[1] if match else None


async def cmd_stickers(message: types.Message, db: Database):
    packs = await db.run(Repository.get_sticker_packs)
    total_stk = sum(p["sticker_count"] for p in packs)
    if not packs:
        await message.reply(
            "В базе пока нет стикерпаков\nСинхронизирую при старте бота"
        )
        return
    lines = [
        f"<b>Стикерпаки Сани Пониёба:</b> (всего {len(packs)} наборов, {total_stk} стикеров в кэше)\n"
    ]
    for p in packs:
        is_def = " <i>(базовый пак)</i>" if p["is_default"] else ""
        cnt = p["sticker_count"]
        name = html.escape(p["pack_name"], quote=True)
        title = html.escape(p["title"] or p["pack_name"])
        lines.append(
            f'• <a href="https://t.me/addstickers/{name}">{title}</a> ({cnt} шт.){is_def}'
        )
    lines.append(
        "\n<i>Команды: /sticker [эмодзи] — кинуть стикер, /addstickers &lt;ссылка&gt; — добавить пак</i>"
    )
    await reply_html_lines(message, lines, disable_web_page_preview=True)


async def cmd_sticker(message: types.Message, bot: Bot, db: Database):
    if await db.run(Repository.get_chat_reply_mode, message.chat.id) == "safe":
        await message.reply("Отправка стикеров отключена в режиме «сейф».")
        return
    args = command_args(message)
    target_emoji = args if args else None
    reply_to = (
        message.reply_to_message.message_id
        if message.reply_to_message
        else message.message_id
    )
    success = await send_sanya_sticker(
        bot, message.chat.id, reply_to_id=reply_to, emoji=target_emoji, db=db
    )
    if not success:
        await message.reply("Не нашел подходящего стикера\nЧе-нить другое напиши")


async def cmd_addstickers(message: types.Message, bot: Bot, db: Database):
    user_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, user_id)
    if not is_admin:
        await message.reply(
            "Куда лезешь, паки добавлять могут только админы конфы\nГуляй"
        )
        return

    pack_name = pack_name_from_arg(command_args(message))
    if not pack_name:
        await message.reply(
            "Кинь ссылку на стикерпак или его имя\nПример: /addstickers https://t.me/addstickers/anon_pony"
        )
        return

    try:
        s = await bot.get_sticker_set(name=pack_name)
        pack_name = s.name
    except Exception:
        logging.exception("Не удалось загрузить стикерпак %s", pack_name)
        await message.reply(
            "Не смог загрузить стикерпак из Телеги. Проверь название или попробуй позже."
        )
        return

    stk_items = []
    for st in s.stickers:
        stk_items.append(
            {
                "file_id": st.file_id,
                "file_unique_id": st.file_unique_id,
                "pack_name": pack_name,
                "emoji": st.emoji or "",
                "file_size": st.file_size or 0,
                "width": st.width or 0,
                "height": st.height or 0,
                "sanya_weight": 1.0,
            }
        )
    if not await db.run(
        Repository.add_sticker_pack,
        pack_name,
        s.title,
        added_by=user_id,
        stickers=stk_items,
    ):
        await message.reply("Не удалось сохранить стикерпак в базу")
        return
    await message.reply(
        f'Зашибись, добавил стикерпак <b><a href="https://t.me/addstickers/{pack_name}">{html.escape(s.title)}</a></b> ({len(stk_items)} стикеров)!\n'
        f"Теперь буду его тоже юзать в чате.",
        parse_mode="HTML",
    )


async def cmd_delstickers(message: types.Message, db: Database):
    user_id = message.from_user.id if message.from_user else 0
    is_admin = await check_is_admin(message.chat, user_id)
    if not is_admin:
        await message.reply("Сносить паки могут только админы конфы\nНе трогай чужое")
        return

    pack_name = pack_name_from_arg(command_args(message))
    if not pack_name:
        await message.reply(
            "Укажи имя стикерпака для удаления\nПример: /delstickers custom_pack"
        )
        return

    is_def = await db.run(Repository.is_default_sticker_pack, pack_name)
    if is_def:
        await message.reply(
            "Ты че еблан, это базовый стикерпак конфы!\nЕго сносить нельзя, на нем весь лор держится"
        )
        return

    removed = await db.run(Repository.remove_sticker_pack, pack_name)
    if removed:
        await message.reply(f"Снес стикерпак {pack_name} под ноль\nБольше не юзаю")
    else:
        await message.reply(
            f"Не нашел пак {pack_name} в базе\n/stickers напиши чтоб глянуть че есть"
        )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(
        cmd_stickers, Command("stickers", "stickerpacks", "паки", "стикеры")
    )
    router.message.register(cmd_sticker, Command("sticker", "stk", "стикер", "стик"))
    router.message.register(
        cmd_addstickers,
        Command(
            "addstickers",
            "addsticker",
            "добавитьстикеры",
            "добавьстикеры",
            "плюсстикеры",
        ),
    )
    router.message.register(
        cmd_delstickers,
        Command(
            "delstickers",
            "removestickers",
            "удалитьстикеры",
            "удалистикеры",
            "минусстикеры",
        ),
    )
    return router
