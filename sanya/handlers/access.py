import logging
import re
from typing import Optional, Tuple

from aiogram import types
from aiogram.enums import ChatType

from ..config import ADMIN_IDS
from ..database import Database
from ..lore import KENT_ALIASES
from ..repository import AmbiguousUsername, Repository

_KENT_TAGS = {
    alias.casefold(): uid
    for uid, aliases in KENT_ALIASES.items()
    for alias in aliases
    if " " not in alias
}


async def resolve_target_user(
    db: Database, message: types.Message, args: str, *, allow_kent_aliases: bool = False
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
        uname = first_token[1:].rstrip(",:")
        reply = message.reply_to_message
        author = reply.from_user if reply else None
        if uname and author and (author.username or "").casefold() == uname.casefold():
            return author.id, author.username, remaining, None
        try:
            prof = await db.run(
                Repository.find_user_by_username, uname, current_only=True
            )
        except AmbiguousUsername:
            return (
                None,
                uname,
                remaining,
                f"Тег @{uname} записан у нескольких профилей. Укажи ID или ответь реплаем без тега.",
            )
        except Exception:
            return (
                None,
                uname,
                remaining,
                "Не удалось проверить тег в базе. Попробуй позже или укажи ID.",
            )
        if prof:
            t_id = prof["user_id"]
            t_name = prof["user_name"] or f"@{uname}"
            return t_id, t_name, remaining.strip(), None
        if allow_kent_aliases and (uid := _KENT_TAGS.get(uname.casefold())):
            profile = await db.run(Repository.get_user_profile, uid)
            name = (profile["user_name"] or profile["first_name"]) if profile else None
            return uid, name or uname, remaining, None
        return (
            None,
            uname,
            remaining,
            f"Не могу подтвердить, кому принадлежит @{uname}. "
            "Укажи числовой ID или вызови команду реплаем без тега.",
        )

    # 2. Если первым аргументом передан числовой ID
    if re.fullmatch(r"[0-9]+", first_token):
        if len(first_token) > 19 or not 0 < int(first_token) < 2**63:
            return None, "", remaining, "Некорректный ID пользователя"
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
