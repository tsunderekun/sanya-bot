import logging
import random
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from .config import MAX_CONTEXT_CHARS, MAX_HISTORY, TOP_STICKERS_CHANCE
from .lore import DEFAULT_STICKER_PACKS, INITIAL_KENTS_PROFILES


class Repository:
    """Управление постоянной памятью диалогов в SQLite с WAL-режимом и автосбросом при перегрузе."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def add_sticker_pack(
        self, pack_name: str, title: str, is_default: bool = False, added_by: int = 0
    ):
        """Добавляет или обновляет стикерпак в БД."""
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO sticker_packs (pack_name, title, is_default, added_by, created_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(pack_name) DO UPDATE SET
                        title = excluded.title,
                        is_default = CASE WHEN sticker_packs.is_default = 1 THEN 1 ELSE excluded.is_default END;
                """,
                    (pack_name, title, 1 if is_default else 0, added_by),
                )
        except Exception as e:
            logging.error(f"Ошибка сохранения стикерпака {pack_name}: {e}")

    def remove_sticker_pack(self, pack_name: str) -> bool:
        """Удаляет добавленный пользователями стикерпак и его кэш (базовые защищены)."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "DELETE FROM sticker_packs WHERE pack_name = ? AND is_default = 0",
                    (pack_name,),
                )
                if cur.rowcount > 0:
                    conn.execute(
                        "DELETE FROM cached_stickers WHERE pack_name = ?", (pack_name,)
                    )
                    return True
                return False
        except Exception as e:
            logging.error(f"Ошибка удаления стикерпака {pack_name}: {e}")
            return False

    def is_default_sticker_pack(self, pack_name: str) -> bool:
        """Проверяет, является ли стикерпак базовым (неудаляемым)."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT is_default FROM sticker_packs WHERE pack_name = ?",
                    (pack_name,),
                )
                row = cur.fetchone()
                if row:
                    return bool(row["is_default"])
        except Exception:
            pass
        return pack_name in DEFAULT_STICKER_PACKS

    def get_sticker_packs(self) -> List[sqlite3.Row]:
        """Возвращает список всех стикерпаков в базе."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT pack_name, title, is_default, added_by, created_at FROM sticker_packs ORDER BY is_default DESC, pack_name ASC"
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка чтения стикерпаков: {e}")
            return []

    def save_cached_stickers(self, stickers: List[Dict]):
        """Кэширует список стикеров с сохранением весов популярности."""
        if not stickers:
            return
        try:
            with self.connection as conn:
                for st in stickers:
                    conn.execute(
                        """
                        INSERT INTO cached_stickers (file_id, file_unique_id, pack_name, emoji, file_size, width, height, sanya_weight, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(file_id) DO UPDATE SET
                            emoji = excluded.emoji,
                            file_size = excluded.file_size,
                            width = excluded.width,
                            height = excluded.height,
                            sanya_weight = CASE WHEN excluded.sanya_weight >= 5.0 THEN excluded.sanya_weight ELSE cached_stickers.sanya_weight END;
                    """,
                        (
                            st["file_id"],
                            st.get("file_unique_id", ""),
                            st["pack_name"],
                            st.get("emoji", ""),
                            st.get("file_size", 0),
                            st.get("width", 0),
                            st.get("height", 0),
                            st.get("sanya_weight", 0.005),
                        ),
                    )
        except Exception as e:
            logging.error(f"Ошибка сохранения стикеров в кэш: {e}")

    def get_cached_stickers_count(self, pack_name: Optional[str] = None) -> int:
        """Возвращает число кэшированных стикеров."""
        try:
            with self.connection as conn:
                if pack_name:
                    cur = conn.execute(
                        "SELECT COUNT(*) as cnt FROM cached_stickers WHERE pack_name = ?",
                        (pack_name,),
                    )
                else:
                    cur = conn.execute("SELECT COUNT(*) as cnt FROM cached_stickers")
                row = cur.fetchone()
                return row["cnt"] if row else 0
        except Exception:
            return 0

    def get_random_sticker(
        self, emoji: Optional[str] = None, pack_name: Optional[str] = None
    ) -> Optional[sqlite3.Row]:
        """
        Возвращает подходящий стикер для Сани Пониёба:
        - В 98.5% случаев (TOP_STICKERS_CHANCE) выбирает строго из коронных топовых стикеров Сани (sanya_weight >= 9.0).
        - В 1.5% случаев (ультраминимальный шанс) допускается любой другой стикер из кэша базы.
        """
        try:
            with self.connection as conn:
                # Если явно запрошен конкретный пак
                if pack_name:
                    query = "SELECT file_id, file_unique_id, pack_name, emoji, sanya_weight FROM cached_stickers WHERE pack_name = ?"
                    params = [pack_name]
                    if emoji:
                        clean_emoji = emoji.strip()
                        query += " AND (emoji LIKE ? OR emoji = ?)"
                        params.extend([f"%{clean_emoji}%", clean_emoji])
                    cur = conn.execute(query, tuple(params))
                    rows = cur.fetchall()
                    if not rows and emoji:
                        cur = conn.execute(
                            "SELECT file_id, file_unique_id, pack_name, emoji, sanya_weight FROM cached_stickers WHERE pack_name = ?",
                            (pack_name,),
                        )
                        rows = cur.fetchall()
                    if not rows:
                        return None
                    weights = [
                        max(0.005, float(r["sanya_weight"] or 0.005)) for r in rows
                    ]
                    return random.choices(rows, weights=weights, k=1)[0]

                # 98.5% времени Саня использует СТРОГО свои коронные топовые стикеры
                prefer_top = random.random() < TOP_STICKERS_CHANCE

                if prefer_top:
                    # Топ-стикеры Сани (file_id из ORIGINAL_SANYA_TOP_STICKERS или sanya_weight >= 9.0)
                    top_query = "SELECT file_id, file_unique_id, pack_name, emoji, sanya_weight FROM cached_stickers WHERE sanya_weight >= 9.0"
                    if emoji:
                        clean_emoji = emoji.strip()
                        cur = conn.execute(
                            top_query + " AND (emoji LIKE ? OR emoji = ?)",
                            (f"%{clean_emoji}%", clean_emoji),
                        )
                        matching_top = cur.fetchall()
                        if matching_top:
                            weights = [float(r["sanya_weight"]) for r in matching_top]
                            return random.choices(matching_top, weights=weights, k=1)[0]

                    # Если точного совпадения по эмодзи среди топа нет или эмодзи не задан:
                    # Саня в 98.5% случаев выбирает любой из своих коронных топовых стикеров с учетом их весов!
                    cur = conn.execute(top_query)
                    top_rows = cur.fetchall()
                    if top_rows:
                        weights = [float(r["sanya_weight"]) for r in top_rows]
                        return random.choices(top_rows, weights=weights, k=1)[0]

                # Ультраминимальный шанс (~1.5%): поиск по эмодзи среди всей базы
                if emoji:
                    clean_emoji = emoji.strip()
                    cur = conn.execute(
                        "SELECT file_id, file_unique_id, pack_name, emoji, sanya_weight FROM cached_stickers WHERE emoji LIKE ? OR emoji = ?",
                        (f"%{clean_emoji}%", clean_emoji),
                    )
                    rows = cur.fetchall()
                    if rows:
                        weights = [
                            max(0.005, float(r["sanya_weight"] or 0.005)) for r in rows
                        ]
                        return random.choices(rows, weights=weights, k=1)[0]

                # Фоллбэк на любой стикер из базы с ультраминимальными весами для не-топа
                cur = conn.execute(
                    "SELECT file_id, file_unique_id, pack_name, emoji, sanya_weight FROM cached_stickers"
                )
                all_rows = cur.fetchall()
                if not all_rows:
                    return None
                weights = [
                    max(0.005, float(r["sanya_weight"] or 0.005)) for r in all_rows
                ]
                return random.choices(all_rows, weights=weights, k=1)[0]
        except Exception as e:
            logging.error(f"Ошибка выбора стикера: {e}")
            return None

    def get_chat_profile(self, chat_id: int) -> Optional[sqlite3.Row]:
        """Возвращает профиль и лор чата/конфы из БД."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT chat_id, title, chat_lore, updated_at FROM chat_profiles WHERE chat_id = ?",
                    (chat_id,),
                )
                return cur.fetchone()
        except Exception as e:
            logging.error(f"Ошибка получения профиля чата {chat_id}: {e}")
            return None

    def _sync_chat_lore_column(self, conn, chat_id: int):
        """Пересобирает chat_profiles.chat_lore из всех записей chat_lore_entries."""
        cur = conn.execute(
            "SELECT content FROM chat_lore_entries WHERE chat_id = ? ORDER BY id ASC",
            (chat_id,),
        )
        entries = [
            r["content"].strip()
            for r in cur.fetchall()
            if r["content"] and r["content"].strip()
        ]
        combined = "\n".join(entries)
        conn.execute(
            "UPDATE chat_profiles SET chat_lore = ?, updated_at = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (combined, chat_id),
        )

    def add_chat_lore_entry(
        self, chat_id: int, title: str, content: str, added_by: int = 0
    ) -> int:
        """Добавляет новый факт в лор конфы (аппенд) и возвращает присвоенный ID."""
        clean_content = content.strip()
        if not clean_content:
            return 0
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO chat_profiles (chat_id, title, chat_lore, updated_at)
                    VALUES (?, ?, '', CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE chat_profiles.title END;
                """,
                    (chat_id, title),
                )

                cur = conn.execute(
                    "INSERT INTO chat_lore_entries (chat_id, content, added_by) VALUES (?, ?, ?)",
                    (chat_id, clean_content, added_by),
                )
                entry_id = cur.lastrowid
                self._sync_chat_lore_column(conn, chat_id)
                return entry_id
        except Exception as e:
            logging.error(f"Ошибка добавления факта в лор чата {chat_id}: {e}")
            return 0

    def get_chat_lore_entries(self, chat_id: int) -> List[sqlite3.Row]:
        """Возвращает список всех отдельных фактов лора конфы с их ID."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT id, chat_id, content, added_by, created_at FROM chat_lore_entries WHERE chat_id = ? ORDER BY id ASC",
                    (chat_id,),
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения записей лора чата {chat_id}: {e}")
            return []

    def delete_chat_lore_entry(
        self, entry_id: int, chat_id: Optional[int] = None
    ) -> Optional[Tuple[int, str]]:
        """Удаляет отдельный факт лора конфы по ID. Возвращает (chat_id, content) при успехе."""
        res = self.delete_chat_lore_entries([entry_id], chat_id)
        if res:
            return res[0][1], res[0][2]
        return None

    def delete_chat_lore_entries(
        self, entry_ids: List[int], chat_id: Optional[int] = None
    ) -> List[Tuple[int, int, str]]:
        """
        Удаляет пачку записей лора конфы по списку ID.
        Возвращает список кортежей (entry_id, chat_id, content) для всех успешно удаленных записей.
        """
        if not entry_ids:
            return []
        deleted = []
        affected_chats = set()
        try:
            with self.connection as conn:
                for eid in entry_ids:
                    if chat_id is not None:
                        cur = conn.execute(
                            "SELECT chat_id, content FROM chat_lore_entries WHERE id = ? AND chat_id = ?",
                            (eid, chat_id),
                        )
                    else:
                        cur = conn.execute(
                            "SELECT chat_id, content FROM chat_lore_entries WHERE id = ?",
                            (eid,),
                        )
                    row = cur.fetchone()
                    if row:
                        c_id = row["chat_id"]
                        content = row["content"]
                        conn.execute(
                            "DELETE FROM chat_lore_entries WHERE id = ?", (eid,)
                        )
                        deleted.append((eid, c_id, content))
                        affected_chats.add(c_id)
                for c_id in affected_chats:
                    self._sync_chat_lore_column(conn, c_id)
        except Exception as e:
            logging.error(f"Ошибка массового удаления записей лора {entry_ids}: {e}")
        return deleted

    def set_chat_lore(self, chat_id: int, title: str, chat_lore: str):
        """Задает или обновляет лор/факты конфы целиком (перезапись)."""
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO chat_profiles (chat_id, title, chat_lore, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        chat_lore = excluded.chat_lore,
                        title = CASE WHEN excluded.title != '' THEN excluded.title ELSE chat_profiles.title END,
                        updated_at = CURRENT_TIMESTAMP;
                """,
                    (chat_id, title, chat_lore),
                )
                conn.execute(
                    "DELETE FROM chat_lore_entries WHERE chat_id = ?", (chat_id,)
                )
                if chat_lore.strip():
                    conn.execute(
                        "INSERT INTO chat_lore_entries (chat_id, content, added_by) VALUES (?, ?, 0)",
                        (chat_id, chat_lore.strip()),
                    )
        except Exception as e:
            logging.error(f"Ошибка сохранения лора чата {chat_id}: {e}")

    def update_chat_title(self, chat_id: int, title: str):
        """Обновляет название чата при получении сообщений."""
        if not title:
            return
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO chat_profiles (chat_id, title, chat_lore, updated_at)
                    VALUES (?, ?, '', CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id) DO UPDATE SET
                        title = excluded.title;
                """,
                    (chat_id, title),
                )
        except Exception as e:
            logging.error(f"Ошибка обновления названия чата {chat_id}: {e}")

    def find_user_by_username(self, username: str) -> Optional[sqlite3.Row]:
        """Ищет пользователя по username (с @ или без) в user_profiles и messages."""
        clean_name = username.strip().lstrip("@").lower()
        if not clean_name:
            return None
        try:
            with self.connection as conn:
                # 1. Поиск в таблице user_profiles
                cur = conn.execute(
                    "SELECT user_id, user_name, first_name, persona_notes, message_count FROM user_profiles WHERE lower(replace(user_name, '@', '')) = ? LIMIT 1",
                    (clean_name,),
                )
                row = cur.fetchone()
                if row:
                    return row

                # 2. Если не найден в профилях, ищем в истории сообщений messages
                cur = conn.execute(
                    "SELECT user_id, user_name FROM messages WHERE lower(replace(user_name, '@', '')) = ? ORDER BY id DESC LIMIT 1",
                    (clean_name,),
                )
                msg_row = cur.fetchone()
                if msg_row:
                    return self.get_user_profile(msg_row["user_id"])
                return None
        except Exception as e:
            logging.error(f"Ошибка поиска пользователя по нику '{username}': {e}")
            return None

    def update_user_profile(self, user_id: int, user_name: str, first_name: str = ""):
        """Обновляет профиль пользователя в БД (счетчик сообщений, ник, время последней активности)."""
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, user_name, first_name, first_seen, last_seen, message_count)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        user_name = excluded.user_name,
                        first_name = excluded.first_name,
                        last_seen = CURRENT_TIMESTAMP,
                        message_count = message_count + 1;
                """,
                    (user_id, user_name, first_name),
                )
        except Exception as e:
            logging.error(f"Ошибка обновления профиля пользователя {user_id}: {e}")

    def get_user_profile(self, user_id: int) -> Optional[sqlite3.Row]:
        """Возвращает сохраненный профиль пользователя из БД."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT user_id, user_name, first_name, first_seen, last_seen, message_count, facts, persona_notes FROM user_profiles WHERE user_id = ?",
                    (user_id,),
                )
                return cur.fetchone()
        except Exception as e:
            logging.error(f"Ошибка получения профиля пользователя {user_id}: {e}")
            return None

    def get_all_kents(self) -> List[sqlite3.Row]:
        """Возвращает список всех кентов, у которых заполнены persona_notes."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT user_id, user_name, first_name, persona_notes, message_count FROM user_profiles WHERE persona_notes IS NOT NULL AND persona_notes != '' ORDER BY message_count DESC"
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения списка кентов: {e}")
            return []

    def _sync_user_persona_notes_column(self, conn, user_id: int):
        """Пересобирает user_profiles.persona_notes из всех записей kent_facts_entries."""
        cur = conn.execute(
            "SELECT content FROM kent_facts_entries WHERE user_id = ? ORDER BY id ASC",
            (user_id,),
        )
        entries = [
            r["content"].strip()
            for r in cur.fetchall()
            if r["content"] and r["content"].strip()
        ]
        combined = "\n".join(entries)
        conn.execute(
            "UPDATE user_profiles SET persona_notes = ? WHERE user_id = ?",
            (combined, user_id),
        )

    def add_kent_fact_entry(
        self,
        user_id: int,
        content: str,
        user_name: str = "",
        first_name: str = "",
        added_by: int = 0,
    ) -> int:
        """Добавляет новый факт о кенте (аппенд) и возвращает присвоенный ID."""
        clean_content = content.strip()
        if not clean_content:
            return 0
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, user_name, first_name, persona_notes, first_seen, last_seen, message_count)
                    VALUES (?, ?, ?, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE user_profiles.user_name END,
                        first_name = CASE WHEN excluded.first_name != '' THEN excluded.first_name ELSE user_profiles.first_name END;
                """,
                    (user_id, user_name, first_name),
                )

                cur = conn.execute(
                    "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, ?)",
                    (user_id, clean_content, added_by),
                )
                entry_id = cur.lastrowid
                self._sync_user_persona_notes_column(conn, user_id)
                return entry_id
        except Exception as e:
            logging.error(f"Ошибка добавления факта о кенте {user_id}: {e}")
            return 0

    def get_kent_fact_entries(self, user_id: int) -> List[sqlite3.Row]:
        """Возвращает список всех отдельных фактов о кенте с их ID."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT id, user_id, content, added_by, created_at FROM kent_facts_entries WHERE user_id = ? ORDER BY id ASC",
                    (user_id,),
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения фактов о кенте {user_id}: {e}")
            return []

    def delete_kent_fact_entry(
        self, entry_id: int, user_id: Optional[int] = None
    ) -> Optional[Tuple[int, str]]:
        """Удаляет отдельный факт о кенте по ID. Возвращает (user_id, content) при успехе."""
        res = self.delete_kent_fact_entries([entry_id], user_id)
        if res:
            return res[0][1], res[0][2]
        return None

    def delete_kent_fact_entries(
        self, entry_ids: List[int], user_id: Optional[int] = None
    ) -> List[Tuple[int, int, str]]:
        """
        Удаляет пачку фактов о кентах по списку ID.
        Возвращает список кортежей (entry_id, user_id, content) для всех успешно удаленных записей.
        """
        if not entry_ids:
            return []
        deleted = []
        affected_users = set()
        try:
            with self.connection as conn:
                for eid in entry_ids:
                    if user_id is not None:
                        cur = conn.execute(
                            "SELECT user_id, content FROM kent_facts_entries WHERE id = ? AND user_id = ?",
                            (eid, user_id),
                        )
                    else:
                        cur = conn.execute(
                            "SELECT user_id, content FROM kent_facts_entries WHERE id = ?",
                            (eid,),
                        )
                    row = cur.fetchone()
                    if row:
                        u_id = row["user_id"]
                        content = row["content"]
                        conn.execute(
                            "DELETE FROM kent_facts_entries WHERE id = ?", (eid,)
                        )
                        deleted.append((eid, u_id, content))
                        affected_users.add(u_id)
                for u_id in affected_users:
                    self._sync_user_persona_notes_column(conn, u_id)
        except Exception as e:
            logging.error(f"Ошибка массового удаления фактов о кентах {entry_ids}: {e}")
        return deleted

    def set_persona_notes(
        self,
        user_id: int,
        persona_notes: str,
        user_name: str = "",
        first_name: str = "",
    ):
        """Задает или обновляет персональные заметки о кенте целиком (перезапись)."""
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, user_name, first_name, persona_notes, first_seen, last_seen, message_count)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)
                    ON CONFLICT(user_id) DO UPDATE SET
                        persona_notes = excluded.persona_notes,
                        user_name = CASE WHEN excluded.user_name != '' THEN excluded.user_name ELSE user_profiles.user_name END,
                        first_name = CASE WHEN excluded.first_name != '' THEN excluded.first_name ELSE user_profiles.first_name END;
                """,
                    (user_id, user_name, first_name, persona_notes),
                )
                conn.execute(
                    "DELETE FROM kent_facts_entries WHERE user_id = ?", (user_id,)
                )
                if persona_notes.strip():
                    conn.execute(
                        "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                        (user_id, persona_notes.strip()),
                    )
        except Exception as e:
            logging.error(f"Ошибка сохранения persona_notes для {user_id}: {e}")

    def restore_initial_kents(self) -> int:
        """Восстанавливает базовые профили и лор всех 8 исходных кентов (INITIAL_KENTS_PROFILES) в SQLite."""
        try:
            with self.connection as conn:
                count = 0
                for uid, pdata in INITIAL_KENTS_PROFILES.items():
                    conn.execute(
                        """
                        INSERT INTO user_profiles (user_id, user_name, first_name, persona_notes, first_seen, last_seen, message_count)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 100)
                        ON CONFLICT(user_id) DO UPDATE SET
                            persona_notes = excluded.persona_notes,
                            user_name = CASE WHEN user_profiles.user_name IS NULL OR user_profiles.user_name = '' THEN excluded.user_name ELSE user_profiles.user_name END,
                            first_name = CASE WHEN user_profiles.first_name IS NULL OR user_profiles.first_name = '' THEN excluded.first_name ELSE user_profiles.first_name END;
                    """,
                        (uid, pdata["user_name"], pdata["first_name"], pdata["notes"]),
                    )
                    conn.execute(
                        "DELETE FROM kent_facts_entries WHERE user_id = ?", (uid,)
                    )
                    conn.execute(
                        "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                        (uid, pdata["notes"]),
                    )
                    count += 1
                return count
        except Exception as e:
            logging.error(f"Ошибка восстановления базовых кентов: {e}")
            return 0

    def restore_single_kent(self, user_id: int) -> bool:
        """Восстанавливает одного базового кента из INITIAL_KENTS_PROFILES."""
        if user_id not in INITIAL_KENTS_PROFILES:
            return False
        pdata = INITIAL_KENTS_PROFILES[user_id]
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO user_profiles (user_id, user_name, first_name, persona_notes, first_seen, last_seen, message_count)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 100)
                    ON CONFLICT(user_id) DO UPDATE SET
                        persona_notes = excluded.persona_notes,
                        user_name = CASE WHEN user_profiles.user_name IS NULL OR user_profiles.user_name = '' THEN excluded.user_name ELSE user_profiles.user_name END,
                        first_name = CASE WHEN user_profiles.first_name IS NULL OR user_profiles.first_name = '' THEN excluded.first_name ELSE user_profiles.first_name END;
                """,
                    (user_id, pdata["user_name"], pdata["first_name"], pdata["notes"]),
                )
                conn.execute(
                    "DELETE FROM kent_facts_entries WHERE user_id = ?", (user_id,)
                )
                conn.execute(
                    "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                    (user_id, pdata["notes"]),
                )
            return True
        except Exception as e:
            logging.error(f"Ошибка восстановления кента {user_id}: {e}")
            return False

    def clear_user_data(
        self, user_id: int, delete_profile: bool = False
    ) -> Tuple[int, bool]:
        """
        Удаляет сообщения пользователя из базы данных.
        Если delete_profile=False:
            Удаляются только сообщения из messages, профиль и persona_notes сохраняются
            (сбрасывается только счетчик сообщений message_count = 0).
        Если delete_profile=True (только по подтвержденному решению админа):
            Удаляются и сообщения, и профиль из user_profiles.
        """
        try:
            with self.connection as conn:
                cur_msg = conn.execute(
                    "DELETE FROM messages WHERE user_id = ?", (user_id,)
                )
                deleted_messages = cur_msg.rowcount

                if delete_profile:
                    conn.execute(
                        "DELETE FROM kent_facts_entries WHERE user_id = ?", (user_id,)
                    )
                    cur_prof = conn.execute(
                        "DELETE FROM user_profiles WHERE user_id = ?", (user_id,)
                    )
                    deleted_profile = cur_prof.rowcount > 0
                else:
                    conn.execute(
                        "UPDATE user_profiles SET message_count = 0 WHERE user_id = ?",
                        (user_id,),
                    )
                    deleted_profile = False

                return deleted_messages, deleted_profile
        except Exception as e:
            logging.error(f"Ошибка очистки данных пользователя {user_id}: {e}")
            return 0, False

    def save_message(
        self,
        chat_id: int,
        message_id: int,
        reply_to_id: Optional[int],
        user_id: int,
        user_name: str,
        role: str,
        content: str,
    ):
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO messages (chat_id, message_id, reply_to_id, user_id, user_name, role, content)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        chat_id,
                        message_id,
                        reply_to_id,
                        user_id,
                        user_name,
                        role,
                        content,
                    ),
                )
        except Exception as e:
            logging.error(f"Ошибка сохранения сообщения в SQLite: {e}")

    def get_message(self, chat_id: int, message_id: int) -> Optional[sqlite3.Row]:
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "SELECT user_name, content, role FROM messages WHERE chat_id = ? AND message_id = ?",
                    (chat_id, message_id),
                )
                return cur.fetchone()
        except Exception as e:
            logging.error(f"Ошибка чтения сообщения из SQLite: {e}")
            return None

    def get_recent_messages(
        self,
        chat_id: int,
        is_group: bool = True,
        limit: int = MAX_HISTORY,
        max_chars: int = MAX_CONTEXT_CHARS,
        before_message_id: int | None = None,
    ) -> Tuple[List[Dict[str, str]], bool]:
        """
        Возвращает историю сообщений.
        Если накопленный объем текста превышает max_chars (слишком много инфы),
        производится автоматический сброс/усечение старой части истории (sliding window),
        чтобы не перегружать контекст и вычислительные ресурсы ноутбука.
        """
        try:
            with self.connection as conn:
                cur = conn.execute(
                    """
                    SELECT role, user_name, content
                    FROM messages
                    WHERE chat_id = ? AND (? IS NULL OR message_id = 0 OR message_id < ?)
                    ORDER BY id DESC
                    LIMIT ?
                """,
                    (chat_id, before_message_id, before_message_id, limit),
                )
                rows = cur.fetchall()

            rows = list(reversed(rows))
            formatted: List[Dict[str, str]] = []
            for r in rows:
                role = r["role"]
                content = r["content"]
                user_name = r["user_name"] or "Кореш"
                if role == "assistant":
                    formatted.append({"role": "assistant", "content": content})
                else:
                    if is_group:
                        formatted.append(
                            {"role": "user", "content": f"[{user_name}]: {content}"}
                        )
                    else:
                        formatted.append({"role": "user", "content": content})

            total_chars = sum(len(m["content"]) for m in formatted)
            was_trimmed = False

            # Автоматический сброс старых сообщений при переполнении контекста
            if total_chars > max_chars:
                logging.warning(
                    f"[{chat_id}] Слишком много инфы в контексте ({total_chars} > {max_chars} симв.). "
                    f"Автоматический сброс старой части истории..."
                )
                was_trimmed = True
                while formatted and total_chars > max_chars:
                    dropped = formatted.pop(0)
                    total_chars -= len(dropped["content"])

            return formatted, was_trimmed
        except Exception as e:
            logging.error(f"Ошибка чтения истории из SQLite: {e}")
            return [], False

    def clear_history(self, chat_id: int):
        try:
            with self.connection as conn:
                conn.execute("DELETE FROM messages WHERE chat_id = ?", (chat_id,))
        except Exception as e:
            logging.error(f"Ошибка очистки истории в SQLite: {e}")

    def prune_old_messages(self, chat_id: int, keep_last: int = 250):
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    DELETE FROM messages
                    WHERE chat_id = ? AND id <= (
                        SELECT id FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET ?
                    )
                """,
                    (chat_id, chat_id, keep_last),
                )
        except Exception as e:
            logging.error(f"Ошибка обрезки старых сообщений: {e}")

    def record_auto_fact(
        self,
        entity_type: str,
        entity_id: int,
        fact_key: str,
        fact_text: str,
        confidence: float = 1.0,
    ):
        """Сохраняет или обновляет авто-подмеченный факт о пользователе или чате."""
        try:
            with self.connection as conn:
                conn.execute(
                    """
                    INSERT INTO auto_observed_facts (entity_type, entity_id, fact_key, fact_text, mention_count, last_seen, confidence)
                    VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP, ?)
                    ON CONFLICT(entity_type, entity_id, fact_key) DO UPDATE SET
                        mention_count = auto_observed_facts.mention_count + 1,
                        fact_text = excluded.fact_text,
                        last_seen = CURRENT_TIMESTAMP,
                        confidence = MAX(auto_observed_facts.confidence, excluded.confidence);
                """,
                    (entity_type, entity_id, fact_key, fact_text, confidence),
                )
        except Exception as e:
            logging.error(
                f"Ошибка сохранения auto_fact ({entity_type}:{entity_id}): {e}"
            )

    def get_auto_facts(
        self, entity_type: str, entity_id: int, limit: int = 6
    ) -> List[sqlite3.Row]:
        """Возвращает список авто-подмеченных фактов для пользователя или чата."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    """
                    SELECT id, fact_key, fact_text, mention_count, last_seen
                    FROM auto_observed_facts
                    WHERE entity_type = ? AND entity_id = ?
                    ORDER BY mention_count DESC, last_seen DESC
                    LIMIT ?;
                """,
                    (entity_type, entity_id, limit),
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения auto_facts: {e}")
            return []

    def delete_auto_facts(self, fact_ids: List[int]) -> List[Tuple[int, str]]:
        """
        Удаляет авто-факты по списку ID из auto_observed_facts.
        Возвращает список кортежей (fact_id, fact_text).
        """
        if not fact_ids:
            return []
        deleted = []
        try:
            with self.connection as conn:
                for fid in fact_ids:
                    cur = conn.execute(
                        "SELECT id, fact_text FROM auto_observed_facts WHERE id = ?",
                        (fid,),
                    )
                    row = cur.fetchone()
                    if row:
                        conn.execute(
                            "DELETE FROM auto_observed_facts WHERE id = ?", (fid,)
                        )
                        deleted.append((fid, row["fact_text"]))
        except Exception as e:
            logging.error(f"Ошибка удаления авто-фактов {fact_ids}: {e}")
        return deleted

    def record_chat_phrase(self, chat_id: int, phrase: str) -> int:
        """Инкрементирует частоту употребления фразы в чате."""
        try:
            norm_phrase = phrase.lower().strip()
            with self.connection as conn:
                cur = conn.execute(
                    """
                    INSERT INTO chat_phrase_frequency (chat_id, phrase, frequency, last_seen)
                    VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                    ON CONFLICT(chat_id, phrase) DO UPDATE SET
                        frequency = chat_phrase_frequency.frequency + 1,
                        last_seen = CURRENT_TIMESTAMP
                    RETURNING frequency;
                """,
                    (chat_id, norm_phrase),
                )
                row = cur.fetchone()
                return row[0] if row else 1
        except Exception as e:
            logging.error(f"Ошибка record_chat_phrase: {e}")
            return 1

    def get_top_chat_phrases(self, chat_id: int, limit: int = 4) -> List[sqlite3.Row]:
        """Возвращает самые частые фразы/темы конфы (с частотой >= 3)."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    """
                    SELECT phrase, frequency, last_seen
                    FROM chat_phrase_frequency
                    WHERE chat_id = ? AND frequency >= 3
                    ORDER BY frequency DESC, last_seen DESC
                    LIMIT ?;
                """,
                    (chat_id, limit),
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка get_top_chat_phrases: {e}")
            return []

    def analyze_message_for_auto_facts(
        self, chat_id: int, user_id: int, user_name: str, text: str
    ):
        """
        Фоновый анализатор входящего сообщения:
        1. Извлекает факты о железе юзера (GPU, CPU, платформа, ноут, ОС, игры).
        2. Отслеживает повторяющиеся ключевые фразы/темы в конфе.
        3. При накоплении частоты продвигает их в долговременную память конфы.
        """
        if not text or len(text.strip()) < 3:
            return

        text_clean = text.strip()
        t_lower = text_clean.lower()

        # 1. Авто-детект железа и системы пользователя
        gpu_match = re.search(
            r"\b(rtx\s*\d{3,4}(?:\s*ti|\s*super)?|gtx\s*\d{3,4}(?:\s*ti)?|rx\s*\d{3,4}(?:\s*xt)?|arc\s*a\d{3}|uhd\s*graphics\s*\d+)\b",
            t_lower,
        )
        if gpu_match:
            gpu_name = gpu_match.group(0).upper()
            self.record_auto_fact(
                "user", user_id, "hardware_gpu", f"Видюха: {gpu_name}"
            )

        cpu_match = re.search(
            r"\b(ryzen\s*\d\s*\d{4}[a-z]*|core\s*i[3579][-\s]\d{4,5}[a-z]*|i[3579][-\s]\d{4,5}[a-z]*|xeon\s*e[357][-\s]\d{4}[a-z0-9]*|e5[-\s]\d{4}[a-z0-9]*|2690v\d|2680v\d|2678v\d|2666v\d|2670v\d|2650v\d|pentium|celeron|phenom|athlon)\b",
            t_lower,
        )
        if cpu_match:
            cpu_name = cpu_match.group(0)
            self.record_auto_fact("user", user_id, "hardware_cpu", f"Проц: {cpu_name}")

        mb_match = re.search(
            r"\b(x99|x79|b450|b550|b650|z[3-7]90|lga\s*115[015]|lga\s*1200|lga\s*1700|lga\s*2011(?:-?v[34])?|am4|am5|хуанан(?:джи)?|huananzhi)\b",
            t_lower,
        )
        if mb_match:
            mb_name = mb_match.group(0)
            self.record_auto_fact(
                "user", user_id, "hardware_platform", f"Платформа/мать: {mb_name}"
            )

        os_match = re.search(
            r"\b(arch(?:\s*linux)?|арч|ubuntu|убунт[а-я]|debian|дебиан|fedora|федор[а-я]|bazzite|баззит|gentoo|виндовс\s*1[01]|вин[даеы]\s*1[01]|windows\s*1[01]|macos|хакинтош)\b",
            t_lower,
        )
        if os_match:
            os_name = os_match.group(0)
            self.record_auto_fact(
                "user", user_id, "user_os", f"ОС / дистрибутив: {os_name}"
            )

        lap_match = re.search(
            r"\b(thinkpad|legion|macbook|эйр|прошка|ноутбук|ноут)\b(?:\s+([a-zA-Z0-9\-]+))?",
            t_lower,
        )
        if lap_match:
            lap_full = lap_match.group(0)
            if len(lap_full.split()) > 1 or lap_match.group(1) in (
                "thinkpad",
                "legion",
                "macbook",
            ):
                self.record_auto_fact(
                    "user", user_id, "user_laptop", f"Ноут: {lap_full}"
                )

        game_match = re.search(
            r"\b(cs\s*1\.6|кс\s*1\.6|cs\s*2|кс\s*2|dota\s*2|дот[аеу]|дотк[аеу]|валорант|valorant|майнкр[а-я]+|minecraft|сталкер|stalker|тарков|tarkov|battlefield\s*3|бф3|батл[аеу])\b",
            t_lower,
        )
        if game_match:
            game_name = game_match.group(0)
            self.record_auto_fact(
                "user", user_id, "favorite_game", f"Катает в: {game_name}"
            )

        buy_match = re.search(
            r"\bя\s+(?:купил|урвал|взял|заказал)\s+([^,.;!?\n]{3,40})", t_lower
        )
        if buy_match:
            item_bought = buy_match.group(1).strip()
            self.record_auto_fact(
                "user", user_id, "recent_buy", f"Недавняя покупка: {item_bought}"
            )

        job_match = re.search(
            r"\bя\s+(?:работаю|пашу|устроился)\s+(?:в|на|кем)?\s*([^,.;!?\n]{3,40})",
            t_lower,
        )
        if job_match:
            job_text = job_match.group(1).strip()
            self.record_auto_fact(
                "user", user_id, "user_job", f"Работа/занятость: {job_text}"
            )

        loc_match = re.search(
            r"\bя\s+(?:живу|переехал)\s+в\s+([а-яёa-z\-]{3,25})", t_lower
        )
        if loc_match:
            loc_name = loc_match.group(1).title()
            self.record_auto_fact(
                "user", user_id, "user_location", f"Город / локация: {loc_name}"
            )

        # 2. Анализ частоты фраз и тем в конфе
        # Игнорируем команды бота (/), цитаты, реплаи и служебные теги
        if text_clean.startswith("/") or "[" in text_clean or "]" in text_clean:
            return

        words = [w for w in re.findall(r"[a-zа-яё0-9\-]+", t_lower) if len(w) >= 2]
        stop_words = {
            "это",
            "что",
            "как",
            "так",
            "все",
            "всё",
            "там",
            "тут",
            "для",
            "или",
            "если",
            "просто",
            "потом",
            "меня",
            "тебя",
            "тоже",
            "было",
            "будет",
            "когда",
            "надо",
            "только",
            "может",
            "даже",
            "свой",
            "свои",
            "себе",
            "саня",
            "санек",
            "сань",
            "пониеб",
            "пониёб",
            "спермер",
            "бот",
            "bot",
            "ponyeb",
            "ponyeb228",
            "давай",
            "расскажи",
            "почему",
            "зачем",
            "сколько",
            "че",
            "чё",
            "кто",
            "где",
            "куда",
            "откуда",
            "есть",
            "нет",
            "стал",
            "были",
            "im",
            "start",
            "end",
            "assistant",
            "system",
            "user",
            "orexus",
            "fstikbot",
            "стикер",
            "набор",
            "pack",
            "sticker",
            "на",
            "по",
            "из",
            "за",
            "под",
            "над",
            "шах",
            "мат",
            "ход",
            "e4",
            "e5",
            "d4",
            "d5",
            "nf3",
            "nc6",
            "nxe4",
            "qxf6",
            "cde8",
        }
        filtered_words = [w for w in words if w not in stop_words and not w.isdigit()]

        for n in (2, 3):
            for i in range(len(filtered_words) - n + 1):
                phrase = " ".join(filtered_words[i : i + n])
                if 8 <= len(phrase) <= 35:
                    freq = self.record_chat_phrase(chat_id, phrase)
                    if freq >= 5:
                        fact_key = f"freq_topic:{phrase}"
                        fact_desc = (
                            f"В чате часто обсуждают «{phrase}» (упомянуто {freq} раз)"
                        )
                        self.record_auto_fact("chat", chat_id, fact_key, fact_desc)
