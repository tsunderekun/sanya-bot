import logging
from typing import Optional, Tuple

from aiogram import types
from aiogram.enums import ChatType

from ..config import ADMIN_IDS
from ..database import Database
from ..repository import Repository


async def resolve_target_user(
    db: Database, message: types.Message, args: str
) -> Tuple[Optional[int], str, str, Optional[str]]:
    """
    Разрешает целевого пользователя из реплая, @username тега или числового ID.
    Возвращает кортеж: (target_id, target_name, remaining_args, error_msg)
    """
    raw = (args or "").strip()
    first_token = ""
    remaining = ""

    if raw:
        parts = raw.split(maxsplit=1)
        first_token = parts[0]
        remaining = parts[1] if len(parts) > 1 else ""

    # 1. Если первым аргументом передан @username
    if first_token.startswith("@"):
        uname = first_token.lstrip("@")
        prof = await db.run(Repository.find_user_by_username, uname)
        if prof:
            t_id = prof["user_id"]
            t_name = prof["user_name"] or f"@{uname}"
            return t_id, t_name, remaining.strip(), None
        else:
            return (
                None,
                uname,
                remaining.strip(),
                (
                    f"Не нашел юзера @{uname} в базе (он еще ни разу не писал при мне в чатах).\n"
                    f"Пусть черканет хоть одно сообщение в конфу, либо укажи его числовой user_id."
                ),
            )

    # 2. Если первым аргументом передан числовой ID
    if first_token.isdigit():
        t_id = int(first_token)
        prof = await db.run(Repository.get_user_profile, t_id)
        t_name = prof["user_name"] if (prof and prof["user_name"]) else str(t_id)
        return t_id, t_name, remaining.strip(), None

    # 3. Если передан реплай на сообщение пользователя
    if message.reply_to_message and message.reply_to_message.from_user:
        t_id = message.reply_to_message.from_user.id
        t_name = (
            message.reply_to_message.from_user.username
            or message.reply_to_message.from_user.first_name
            or str(t_id)
        )
        return t_id, t_name, raw, None

    # Ничего не подошло
    return None, "", raw, None


async def check_is_admin(chat: Optional[types.Chat], user_id: int) -> bool:
    """
    Динамически проверяет права админа:
    1. Суперадмин бота (ADMIN_IDS: kunetska, Спермер).
    2. В группах и супергруппах: создатель (creator) или администратор (administrator) текущего чата.
    """
    if not user_id:
        return False
    if user_id in ADMIN_IDS:
        return True
    if chat and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
        try:
            member = await chat.get_member(user_id)
            return member.status in ("creator", "administrator")
        except Exception as e:
            logging.debug(
                f"Ошибка динамической проверки прав админа для {user_id} в чате {chat.id}: {e}"
            )
            return False
    return False
