import logging
import random
import re
import sqlite3
from typing import Dict, List, Optional, Tuple

from .config import MAX_CONTEXT_CHARS, MAX_HISTORY, TOP_STICKERS_CHANCE
from .lore import DEFAULT_STICKER_PACKS, INITIAL_KENTS_PROFILES
from .observations import SELF_FACT_PREFIX, self_fact_prefix
from .reply_style import DEFAULT_LEVEL, ChatReplySettings, ReplyStyle
from .trigger_phrases import (
    matches_trigger_phrase,
    normalize_phrase,
    valid_trigger_phrase,
)
from .vocabulary import BUILTIN_INDEX, valid_meaning, valid_word, word_key


class AmbiguousUsername(ValueError):
    pass


class Repository:
    """Управление постоянной памятью диалогов в SQLite с WAL-режимом и автосбросом при перегрузе."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def save_channel_meme(
        self, channel_id, message_id, media_type, file_id, file_unique_id
    ):
        with self.connection as conn:
            cur = conn.execute(
                "INSERT INTO channel_memes (channel_id, message_id, media_type, file_id, file_unique_id) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(channel_id, message_id) DO NOTHING",
                (channel_id, message_id, media_type, file_id, file_unique_id),
            )
            return cur.rowcount > 0

    def random_channel_memes(self, limit=3):
        return self.connection.execute(
            "SELECT id, media_type, file_id FROM channel_memes ORDER BY RANDOM() LIMIT ?",
            (limit,),
        ).fetchall()

    def get_channel_memes_with_penalty(self, chat_id, limit=5):
        return self.connection.execute(
            """
            SELECT cm.id, cm.media_type, cm.file_id,
                   COUNT(mh.id) AS show_count,
                   COALESCE(MAX(mh.id), 0) AS last_shown_id
            FROM channel_memes cm
            LEFT JOIN meme_history mh ON mh.meme_id = cm.id AND mh.chat_id = ?
            GROUP BY cm.id
            ORDER BY show_count ASC, last_shown_id ASC, RANDOM()
            LIMIT ?
            """,
            (chat_id, limit),
        ).fetchall()

    def record_meme_history(self, chat_id, meme_id):
        with self.connection as conn:
            conn.execute(
                "INSERT INTO meme_history (chat_id, meme_id) VALUES (?, ?)",
                (chat_id, meme_id),
            )
            conn.execute(
                "DELETE FROM meme_history WHERE id IN ("
                "  SELECT id FROM meme_history WHERE chat_id = ? "
                "  ORDER BY id DESC LIMIT -1 OFFSET 500"
                ")",
                (chat_id,),
            )

    def update_channel_meme_file_id(self, meme_id, file_id, file_unique_id):
        with self.connection as conn:
            conn.execute(
                "UPDATE channel_memes SET file_id = ?, file_unique_id = ? WHERE id = ?",
                (file_id, file_unique_id, meme_id),
            )

    def count_channel_memes(self, channel_id=None):
        if channel_id is not None:
            row = self.connection.execute(
                "SELECT count(*) FROM channel_memes WHERE channel_id = ?",
                (channel_id,),
            ).fetchone()
        else:
            row = self.connection.execute(
                "SELECT count(*) FROM channel_memes"
            ).fetchone()
        return row[0] if row else 0

    def recent_booru_ids(self, chat_id):
        return {
            row[0]
            for row in self.connection.execute(
                "SELECT image_id FROM booru_history WHERE chat_id = ?", (chat_id,)
            )
        }

    def remember_booru_image(self, chat_id, image_id):
        with self.connection as conn:
            conn.execute(
                "DELETE FROM booru_history WHERE chat_id = ? AND image_id = ?",
                (chat_id, image_id),
            )
            conn.execute(
                "INSERT INTO booru_history (chat_id, image_id) VALUES (?, ?)",
                (chat_id, image_id),
            )
            conn.execute(
                "DELETE FROM booru_history WHERE chat_id = ? AND id <= ("
                "SELECT id FROM booru_history WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET 200)",
                (chat_id, chat_id),
            )

    def add_sticker_pack(
        self,
        pack_name: str,
        title: str,
        is_default: bool = False,
        added_by: int = 0,
        *,
        stickers: List[Dict] | None = None,
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
                if stickers is not None:
                    self._save_cached_stickers(conn, stickers)
            return True
        except Exception as e:
            logging.error(f"Ошибка сохранения стикерпака {pack_name}: {e}")
            return False

    def remove_sticker_pack(self, pack_name: str) -> bool:
        """Удаляет добавленный пользователями стикерпак и его кэш (базовые защищены)."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    "DELETE FROM sticker_packs WHERE pack_name = ? COLLATE NOCASE AND is_default = 0",
                    (pack_name,),
                )
                if cur.rowcount > 0:
                    conn.execute(
                        "DELETE FROM cached_stickers WHERE pack_name = ? COLLATE NOCASE",
                        (pack_name,),
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
                    "SELECT MAX(is_default) AS is_default FROM sticker_packs WHERE pack_name = ? COLLATE NOCASE",
                    (pack_name,),
                )
                row = cur.fetchone()
                if row and row["is_default"] is not None:
                    return bool(row["is_default"])
        except Exception:
            pass
        return any(
            pack_name.casefold() == name.casefold() for name in DEFAULT_STICKER_PACKS
        )

    def get_sticker_packs(self) -> List[sqlite3.Row]:
        """Возвращает список всех стикерпаков в базе."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    """SELECT p.*, COALESCE(c.cnt, 0) AS sticker_count
                       FROM sticker_packs p LEFT JOIN (
                           SELECT pack_name, COUNT(*) AS cnt FROM cached_stickers GROUP BY pack_name
                       ) c USING (pack_name) ORDER BY p.is_default DESC, p.pack_name ASC"""
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка чтения стикерпаков: {e}")
            return []

    @staticmethod
    def _save_cached_stickers(conn, stickers: List[Dict]):
        conn.executemany(
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
                (
                    st["file_id"],
                    st.get("file_unique_id", ""),
                    st["pack_name"],
                    st.get("emoji", ""),
                    st.get("file_size", 0),
                    st.get("width", 0),
                    st.get("height", 0),
                    st.get("sanya_weight", 0.005),
                )
                for st in stickers
            ),
        )

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

    def clear_chat_lore(self, chat_id: int, through_id: int) -> int:
        with self.connection as conn:
            deleted = conn.execute(
                "DELETE FROM chat_lore_entries WHERE chat_id = ? AND id <= ?",
                (chat_id, through_id),
            ).rowcount
            self._sync_chat_lore_column(conn, chat_id)
        return deleted

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

    def find_user_by_username(
        self, username: str, *, current_only: bool = False
    ) -> Optional[sqlite3.Row]:
        """Ищет пользователя по username (с @ или без) в user_profiles и messages."""
        clean_name = username.strip().lstrip("@").lower()
        if not clean_name:
            return None
        try:
            with self.connection as conn:
                # 1. Поиск в таблице user_profiles
                cur = conn.execute(
                    "SELECT user_id, user_name, first_name, persona_notes, message_count FROM user_profiles WHERE lower(ltrim(user_name, '@')) = ? ORDER BY last_seen DESC, user_id LIMIT 2",
                    (clean_name,),
                )
                rows = cur.fetchall()
                if rows:
                    if current_only and len(rows) > 1:
                        raise AmbiguousUsername(clean_name)
                    return rows[0]
                if current_only:
                    return None

                # 2. Если не найден в профилях, ищем в истории сообщений messages
                cur = conn.execute(
                    "SELECT user_id, user_name FROM messages WHERE lower(replace(user_name, '@', '')) = ? ORDER BY id DESC LIMIT 1",
                    (clean_name,),
                )
                msg_row = cur.fetchone()
                if msg_row:
                    return self.get_user_profile(msg_row["user_id"])
                return None
        except AmbiguousUsername:
            raise
        except Exception as e:
            logging.error(f"Ошибка поиска пользователя по нику '{username}': {e}")
            if current_only:
                raise
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

    def list_chat_words(self, chat_id: int):
        return self.connection.execute(
            "SELECT word, meaning FROM chat_vocabulary WHERE chat_id = ? ORDER BY rowid DESC",
            (chat_id,),
        ).fetchall()

    def add_chat_word(self, chat_id: int, word: str, meaning: str) -> str:
        word, meaning = word_key(word), meaning.strip()
        if word in BUILTIN_INDEX:
            return "builtin"
        if not valid_word(word) or not valid_meaning(meaning):
            return "invalid"
        with self.connection as conn:
            inserted = conn.execute(
                "INSERT INTO chat_vocabulary (chat_id, word, meaning) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, word) DO NOTHING",
                (chat_id, word, meaning),
            ).rowcount
        return "added" if inserted else "exists"

    def delete_chat_word(
        self, chat_id: int, word: str, *, expected_meaning: str | None = None
    ) -> str:
        word = word_key(word)
        if word in BUILTIN_INDEX:
            return "builtin"
        with self.connection as conn:
            deleted = conn.execute(
                "DELETE FROM chat_vocabulary WHERE chat_id = ? AND word = ? "
                "AND (? IS NULL OR meaning = ?)",
                (chat_id, word, expected_meaning, expected_meaning),
            ).rowcount
        return "deleted" if deleted else "missing"

    def get_chat_reply_settings(self, chat_id: int) -> ChatReplySettings:
        row = self.connection.execute(
            "SELECT coalesce(m.mode, 'normal'), coalesce(s.tone, ?), coalesce(s.lore, ?), "
            "k.level, coalesce(v.chance, 0) "
            "FROM (SELECT ? AS chat_id) AS c "
            "LEFT JOIN chat_reply_modes AS m USING (chat_id) "
            "LEFT JOIN chat_reply_styles AS s USING (chat_id) "
            "LEFT JOIN chat_sticker_levels AS k USING (chat_id) "
            "LEFT JOIN chat_voice_settings AS v USING (chat_id)",
            (DEFAULT_LEVEL, DEFAULT_LEVEL, chat_id),
        ).fetchone()
        return ChatReplySettings(row[0], ReplyStyle(row[1], row[2]), row[3], row[4])

    def set_chat_voice_chance(self, chat_id: int, chance: int) -> None:
        if type(chance) is not int or not 0 <= chance <= 100:
            raise ValueError("Invalid voice chance")
        with self.connection as conn:
            conn.execute(
                "INSERT INTO chat_voice_settings (chat_id, chance) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET chance = excluded.chance",
                (chat_id, chance),
            )

    def get_chat_trigger_phrases(self, chat_id: int):
        return self.connection.execute(
            "SELECT rowid AS id, phrase_key, phrase, enabled FROM chat_trigger_phrases "
            "WHERE chat_id = ? ORDER BY created_at, phrase_key",
            (chat_id,),
        ).fetchall()

    def add_chat_trigger_phrase(self, chat_id: int, phrase: str, added_by: int) -> str:
        phrase = " ".join(phrase.split())
        if not valid_trigger_phrase(phrase):
            return "invalid"
        key = normalize_phrase(phrase)
        with self.connection as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM chat_trigger_phrases WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()[0]
            if count >= 20:
                return "limit"
            inserted = conn.execute(
                "INSERT INTO chat_trigger_phrases "
                "(chat_id, phrase_key, phrase, enabled, added_by) VALUES (?, ?, ?, 1, ?) "
                "ON CONFLICT(chat_id, phrase_key) DO NOTHING",
                (chat_id, key, phrase, added_by),
            ).rowcount
        return "added" if inserted else "exists"

    def toggle_chat_trigger_phrase(self, chat_id: int, phrase_id: int) -> bool:
        with self.connection as conn:
            changed = conn.execute(
                "UPDATE chat_trigger_phrases SET enabled = 1 - enabled "
                "WHERE chat_id = ? AND rowid = ?",
                (chat_id, phrase_id),
            ).rowcount
        return bool(changed)

    def matches_chat_trigger_phrase(self, chat_id: int, text: str) -> bool:
        phrases = self.connection.execute(
            "SELECT phrase FROM chat_trigger_phrases WHERE chat_id = ? AND enabled = 1",
            (chat_id,),
        )
        return any(matches_trigger_phrase(text, row[0]) for row in phrases)

    def set_chat_sticker_level(self, chat_id: int, level: int | None) -> None:
        with self.connection as conn:
            if level is None:
                conn.execute(
                    "DELETE FROM chat_sticker_levels WHERE chat_id = ?", (chat_id,)
                )
            elif type(level) is int and level in range(4):
                conn.execute(
                    "INSERT INTO chat_sticker_levels VALUES (?, ?) "
                    "ON CONFLICT(chat_id) DO UPDATE SET level = excluded.level",
                    (chat_id, level),
                )
            else:
                raise ValueError("Invalid sticker level")

    def set_chat_reply_level(self, chat_id: int, axis: str, level: int) -> None:
        if (
            axis not in ("tone", "lore")
            or type(level) is not int
            or level not in range(4)
        ):
            raise ValueError("Invalid reply style setting")
        with self.connection as conn:
            conn.execute(
                f"INSERT INTO chat_reply_styles (chat_id, {axis}) VALUES (?, ?) "
                f"ON CONFLICT(chat_id) DO UPDATE SET {axis} = excluded.{axis}",
                (chat_id, level),
            )

    def get_chat_reply_mode(self, chat_id: int) -> str:
        row = self.connection.execute(
            "SELECT mode FROM chat_reply_modes WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return row[0] if row else "normal"

    def set_chat_reply_mode(self, chat_id: int, mode: str) -> None:
        with self.connection as conn:
            conn.execute(
                "INSERT INTO chat_reply_modes (chat_id, mode) VALUES (?, ?) "
                "ON CONFLICT(chat_id) DO UPDATE SET mode = excluded.mode",
                (chat_id, mode),
            )

    def get_pic_history(self, chat_id: int, query_key: str) -> list[tuple[str, str]]:
        return [
            tuple(row)
            for row in self.connection.execute(
                "SELECT url_key, digest FROM pic_history WHERE chat_id = ? AND query_key = ?",
                (chat_id, query_key),
            )
        ]

    def remember_pic(self, chat_id: int, query_key: str, url_key: str, digest: str):
        with self.connection as conn:
            conn.execute(
                "INSERT INTO pic_history (chat_id, query_key, url_key, digest) VALUES (?, ?, ?, ?)",
                (chat_id, query_key, url_key, digest),
            )
            conn.execute(
                "DELETE FROM pic_history WHERE chat_id = ? AND id <= ("
                "SELECT id FROM pic_history WHERE chat_id = ? ORDER BY id DESC LIMIT 1 OFFSET 200)",
                (chat_id, chat_id),
            )

    def count_user_chat_messages(self, chat_id: int, user_id: int) -> int:
        return self.connection.execute(
            "SELECT COUNT(*) FROM messages WHERE chat_id = ? AND user_id = ? AND role = 'user'",
            (chat_id, user_id),
        ).fetchone()[0]

    def list_chat_speakers(self, chat_id: int) -> list[int]:
        rows = self.connection.execute(
            "SELECT DISTINCT user_id FROM messages "
            "WHERE chat_id = ? AND role = 'user' AND user_id > 0 ORDER BY user_id",
            (chat_id,),
        )
        return [row[0] for row in rows]

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

    def find_scoped_people_by_name(self, chat_id: int, author_id: int, name: str):
        """Exact display-name lookup for identity questions, not a global search."""
        known_ids = tuple(INITIAL_KENTS_PROFILES)
        placeholders = ", ".join("?" for _ in known_ids)
        rows = self.connection.execute(
            "SELECT p.user_id, p.user_name, p.first_name, p.persona_notes "
            "FROM user_profiles AS p "
            f"WHERE p.user_id = ? OR p.user_id IN ({placeholders}) OR "
            "(? < 0 AND EXISTS (SELECT 1 FROM messages AS m "
            "WHERE m.chat_id = ? AND m.user_id = p.user_id AND m.role = 'user'))",
            (author_id, *known_ids, chat_id, chat_id),
        )
        key = name.casefold()
        matches = []
        for row in rows:
            if any(
                (row[field] or "").strip().lstrip("@").casefold() == key
                for field in ("user_name", "first_name")
            ):
                matches.append(row)
                if len(matches) == 2:
                    break
        return matches

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

    def clear_kent_facts(self, user_id: int, through_id: int) -> int:
        with self.connection as conn:
            deleted = conn.execute(
                "DELETE FROM kent_facts_entries WHERE user_id = ? AND id <= ?",
                (user_id, through_id),
            ).rowcount
            self._sync_user_persona_notes_column(conn, user_id)
        return deleted

    def is_kent_blocked(self, chat_id: int, user_id: int) -> bool:
        return (
            self.connection.execute(
                "SELECT 1 FROM chat_blocked_kents WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            is not None
        )

    def set_kent_blocked(self, chat_id: int, user_id: int, blocked: bool) -> bool:
        with self.connection as conn:
            sql = (
                "INSERT OR IGNORE INTO chat_blocked_kents (chat_id, user_id) VALUES (?, ?)"
                if blocked
                else "DELETE FROM chat_blocked_kents WHERE chat_id = ? AND user_id = ?"
            )
            return conn.execute(sql, (chat_id, user_id)).rowcount > 0

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
        """Добавляет отсутствующие базовые заметки, не удаляя существующие."""
        try:
            with self.connection as conn:
                for uid, pdata in INITIAL_KENTS_PROFILES.items():
                    self._restore_kent(conn, uid, pdata)
                return len(INITIAL_KENTS_PROFILES)
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
                self._restore_kent(conn, user_id, pdata)
            return True
        except Exception as e:
            logging.error(f"Ошибка восстановления кента {user_id}: {e}")
            return False

    def _restore_kent(self, conn, user_id, pdata):
        conn.execute(
            "INSERT INTO user_profiles (user_id, user_name, first_name) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, pdata["user_name"], pdata["first_name"]),
        )
        mirror = (
            conn.execute(
                "SELECT persona_notes FROM user_profiles WHERE user_id=?", (user_id,)
            ).fetchone()[0]
            or ""
        ).strip()
        facts = [
            row[0]
            for row in conn.execute(
                "SELECT content FROM kent_facts_entries WHERE user_id=? ORDER BY id",
                (user_id,),
            )
        ]
        # Preserve legacy notes that were never copied into the fact entries.
        if mirror and mirror not in (
            pdata["notes"],
            "\n".join(f.strip() for f in facts if f and f.strip()),
        ):
            conn.execute(
                "INSERT INTO kent_facts_entries (user_id, content) VALUES (?, ?)",
                (user_id, mirror),
            )
        conn.execute(
            "INSERT INTO kent_facts_entries (user_id, content, added_by) "
            "SELECT ?, ?, 0 WHERE NOT EXISTS (SELECT 1 FROM kent_facts_entries WHERE user_id=? AND content=?)",
            (user_id, pdata["notes"], user_id, pdata["notes"]),
        )
        self._sync_user_persona_notes_column(conn, user_id)

    def clear_user_data(
        self, user_id: int, delete_profile: bool = False
    ) -> Tuple[int, bool] | None:
        """
        Удаляет сообщения пользователя из базы данных.
        Если delete_profile=False:
            Удаляются сообщения и тихие самоописания, профиль и persona_notes сохраняются
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

                if not delete_profile:
                    conn.execute(
                        "DELETE FROM auto_observed_facts WHERE entity_type='user' "
                        "AND entity_id=? AND substr(fact_key, 1, ?) = ?",
                        (user_id, len(SELF_FACT_PREFIX), SELF_FACT_PREFIX),
                    )
                if delete_profile:
                    conn.execute(
                        "DELETE FROM auto_observed_facts WHERE entity_type='user' AND entity_id=?",
                        (user_id,),
                    )
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
            return None

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

    def save_chat_answer(
        self,
        chat_id: int,
        message_ids: list[int],
        question_id: int,
        bot_id: int,
        text: str,
    ) -> None:
        if not message_ids:
            return
        # Follow-up replies can target any part of a burst. Store its text once.
        with self.connection as conn:
            conn.executemany(
                "INSERT INTO messages (chat_id, message_id, reply_to_id, user_id, user_name, role, content, scoped_answer) "
                "VALUES (?, ?, ?, ?, 'Саня Пониёб', 'assistant', ?, 1)",
                (
                    (chat_id, mid, question_id, bot_id, text if i == 0 else "")
                    for i, mid in enumerate(message_ids)
                ),
            )

    def get_answer_question(self, chat_id: int, answer_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT q.content FROM messages a JOIN messages q "
            "ON q.chat_id = a.chat_id AND q.message_id = a.reply_to_id AND q.role = 'user' "
            "WHERE a.chat_id = ? AND a.message_id = ? AND a.role = 'assistant' "
            "ORDER BY q.id DESC LIMIT 1",
            (chat_id, answer_id),
        ).fetchone()

    def get_dialogue_rows(
        self,
        chat_id: int,
        user_id: int,
        before: int,
        *,
        reply_to_id: int | None = None,
        limit: int = MAX_HISTORY,
    ) -> list[sqlite3.Row]:
        """Read one personal timeline or walk explicit Telegram reply links, never both."""
        if reply_to_id is not None:
            branch = []
            cursor = before
            for _ in range(limit):
                if not 0 < reply_to_id < cursor:
                    break
                row = self.connection.execute(
                    "SELECT * FROM messages WHERE chat_id = ? AND message_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (chat_id, reply_to_id),
                ).fetchone()
                if row is None:
                    # A missing ancestor may have been forgotten or cleared. Do not
                    # reuse surviving replies that could repeat its contents.
                    return []
                # Burst continuations share their parent; fetch the response body once.
                if row["role"] == "assistant" and not row["content"]:
                    body = self.connection.execute(
                        "SELECT * FROM messages WHERE chat_id = ? AND reply_to_id = ? "
                        "AND role = 'assistant' AND content != '' AND message_id > 0 "
                        "AND message_id <= ? ORDER BY message_id DESC LIMIT 1",
                        (chat_id, row["reply_to_id"], row["message_id"]),
                    ).fetchone()
                    if body:
                        branch.append(body)
                elif row["role"] in ("assistant", "user") and row["content"]:
                    branch.append(row)
                cursor, reply_to_id = row["message_id"], row["reply_to_id"] or 0
            return branch[::-1]
        # Root turns only. An answer to a reply can contain another participant's
        # words, and must not reappear in the personal timeline outside that branch.
        questions = self.connection.execute(
            "SELECT * FROM messages WHERE chat_id = ? AND user_id = ? AND role = 'user' "
            "AND message_id > 0 AND message_id < ? AND COALESCE(reply_to_id, 0) = 0 "
            "AND content != '' ORDER BY message_id DESC, id DESC LIMIT ?",
            (chat_id, user_id, before, limit),
        ).fetchall()
        rows = []
        for question in reversed(questions):
            rows.append(question)
            answer = self.connection.execute(
                "SELECT * FROM messages WHERE chat_id = ? AND reply_to_id = ? "
                "AND role = 'assistant' AND scoped_answer = 1 AND content != '' "
                "AND message_id > ? AND message_id < ? "
                "ORDER BY message_id DESC LIMIT 1",
                (chat_id, question["message_id"], question["message_id"], before),
            ).fetchone()
            if answer:
                rows.append(answer)
        return sorted(rows, key=lambda row: (row["message_id"], row["id"]))[-limit:]

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
                    WHERE chat_id = ? AND content != ''
                        AND (? IS NULL OR COALESCE(NULLIF(message_id, 0), reply_to_id, 0) < ?)
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

    def clear_history(self, chat_id: int) -> int | None:
        try:
            with self.connection as conn:
                return conn.execute(
                    "DELETE FROM messages WHERE chat_id = ?", (chat_id,)
                ).rowcount
        except sqlite3.Error:
            logging.exception("Ошибка очистки истории в SQLite")
            return None

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
        self, entity_type: str, entity_id: int, limit: int = 6, *, key_prefix: str = ""
    ) -> List[sqlite3.Row]:
        """Возвращает список авто-подмеченных фактов для пользователя или чата."""
        try:
            with self.connection as conn:
                cur = conn.execute(
                    """
                    SELECT id, fact_key, fact_text, mention_count, last_seen
                    FROM auto_observed_facts
                    WHERE entity_type = ? AND entity_id = ?
                        AND substr(fact_key, 1, length(?)) = ?
                    ORDER BY mention_count DESC, last_seen DESC
                    LIMIT ?;
                """,
                    (entity_type, entity_id, key_prefix, key_prefix, limit),
                )
                return cur.fetchall()
        except Exception as e:
            logging.error(f"Ошибка получения auto_facts: {e}")
            return []

    def record_self_facts(
        self, chat_id: int, user_id: int, facts: tuple[tuple[str, str], ...]
    ):
        prefix = self_fact_prefix(chat_id)
        for key, value in facts:
            self.record_auto_fact("user", user_id, prefix + key, value)

    def get_self_facts(self, chat_id: int, user_id: int, limit: int = 6):
        return self.get_auto_facts(
            "user", user_id, limit, key_prefix=self_fact_prefix(chat_id)
        )

    def delete_auto_facts(
        self, fact_ids: List[int], *, chat_id: int | None = None
    ) -> List[Tuple[int, str]]:
        """
        Удаляет авто-факты по списку ID из auto_observed_facts.
        Возвращает список кортежей (fact_id, fact_text).
        """
        if not fact_ids:
            return []
        try:
            with self.connection as conn:
                placeholders = ",".join("?" for _ in fact_ids)
                scope = (
                    " AND entity_type = 'chat' AND entity_id = ?"
                    if chat_id is not None
                    else ""
                )
                params = (
                    (*fact_ids, chat_id) if chat_id is not None else tuple(fact_ids)
                )
                rows = conn.execute(
                    f"DELETE FROM auto_observed_facts WHERE id IN ({placeholders}){scope} RETURNING id, fact_text",
                    params,
                ).fetchall()
                return sorted((row[0], row[1]) for row in rows)
        except Exception as e:
            logging.error(f"Ошибка удаления авто-фактов {fact_ids}: {e}")
        return []

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

    def analyze_chat_phrases(self, chat_id: int, text: str):
        """Отслеживает повторяющиеся темы конфы отдельно от фактов об участниках."""
        if not text or len(text.strip()) < 3:
            return

        text_clean = text.strip()
        t_lower = text_clean.lower()

        # Игнорируем команды бота и служебные теги
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
