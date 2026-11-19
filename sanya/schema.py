from .lore import INITIAL_CHATS_LORE, INITIAL_KENTS_PROFILES


def initialize(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_reply_styles (
            chat_id INTEGER PRIMARY KEY,
            tone INTEGER NOT NULL DEFAULT 1 CHECK (tone BETWEEN 0 AND 3),
            lore INTEGER NOT NULL DEFAULT 1 CHECK (lore BETWEEN 0 AND 3)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_vocabulary (
            chat_id INTEGER NOT NULL,
            word TEXT NOT NULL,
            meaning TEXT NOT NULL,
            PRIMARY KEY (chat_id, word)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_reply_modes (
            chat_id INTEGER PRIMARY KEY,
            mode TEXT NOT NULL CHECK (mode IN ('normal', 'rofl'))
        )
    """)
    original_tables = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for alternate, actual in (
        ("kent_fact_entries", "kent_facts_entries"),
        ("auto_facts", "auto_observed_facts"),
    ):
        if alternate in original_tables and actual not in original_tables:
            raise RuntimeError(
                f"Unexpected legacy table {alternate}; expected {actual}. Check DB_PATH and schema."
            )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS channel_memes (
            id INTEGER PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            media_type TEXT NOT NULL CHECK(media_type IN ('photo', 'video', 'animation')),
            file_id TEXT NOT NULL,
            file_unique_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (channel_id, message_id)
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS booru_history (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            image_id TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (chat_id, image_id)
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_booru_recent ON booru_history(chat_id, id DESC)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS meme_history (
            id INTEGER PRIMARY KEY,
            chat_id INTEGER NOT NULL,
            meme_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_meme_history_chat ON meme_history(chat_id, meme_id)"
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            reply_to_id INTEGER,
            user_id INTEGER NOT NULL,
            user_name TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_id_id ON messages(chat_id, id DESC);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_msg ON messages(chat_id, message_id);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_user_id ON messages(user_id);")

    # Таблица профилей пользователей и персональной памяти
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id INTEGER PRIMARY KEY,
            user_name TEXT,
            first_name TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_count INTEGER DEFAULT 1,
            facts TEXT DEFAULT '',
            persona_notes TEXT DEFAULT ''
        );
    """)

    # Авто-миграция для существующей БД
    if "persona_notes" not in {
        row[1] for row in conn.execute("PRAGMA table_info(user_profiles)")
    }:
        conn.execute(
            "ALTER TABLE user_profiles ADD COLUMN persona_notes TEXT DEFAULT '';"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_name_key ON user_profiles(LOWER(REPLACE(user_name, '@', '')));"
    )

    # Таблица профилей чатов и постоянного лора конф
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_profiles (
            chat_id INTEGER PRIMARY KEY,
            title TEXT DEFAULT '',
            chat_lore TEXT DEFAULT '',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)

    # Таблица отдельных фактов/записей лора конф (append-only с ID)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_lore_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            added_by INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_chat_lore_chat_id ON chat_lore_entries(chat_id);"
    )

    # Таблица отдельных фактов о кентах (append-only с ID)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kent_facts_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            added_by INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_kent_facts_user_id ON kent_facts_entries(user_id);"
    )

    # Таблица авто-подмеченных фактов о пользователях и чатах
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auto_observed_facts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT NOT NULL,
            entity_id INTEGER NOT NULL,
            fact_key TEXT NOT NULL,
            fact_text TEXT NOT NULL,
            mention_count INTEGER DEFAULT 1,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            confidence REAL DEFAULT 1.0,
            UNIQUE(entity_type, entity_id, fact_key)
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auto_facts ON auto_observed_facts(entity_type, entity_id);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_auto_facts_rank ON auto_observed_facts(entity_type, entity_id, mention_count DESC, last_seen DESC);"
    )

    # Таблица частоты употребления фраз в конфе
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_phrase_frequency (
            chat_id INTEGER NOT NULL,
            phrase TEXT NOT NULL,
            frequency INTEGER DEFAULT 1,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (chat_id, phrase)
        );
    """)

    # Авто-миграция существующих данных chat_profiles -> chat_lore_entries
    cur_chat = (
        conn.execute(
            "SELECT chat_id, chat_lore FROM chat_profiles WHERE chat_lore IS NOT NULL AND chat_lore != ''"
        )
        if "chat_lore_entries" not in original_tables
        else ()
    )
    for r in list(cur_chat):
        c_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM chat_lore_entries WHERE chat_id = ?",
            (r["chat_id"],),
        ).fetchone()["cnt"]
        if c_cnt == 0:
            conn.execute(
                "INSERT INTO chat_lore_entries (chat_id, content, added_by) VALUES (?, ?, 0)",
                (r["chat_id"], r["chat_lore"]),
            )

    # Авто-миграция существующих данных user_profiles -> kent_facts_entries
    cur_user = (
        conn.execute(
            "SELECT user_id, persona_notes FROM user_profiles WHERE persona_notes IS NOT NULL AND persona_notes != ''"
        )
        if "kent_facts_entries" not in original_tables
        else ()
    )
    for r in list(cur_user):
        k_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM kent_facts_entries WHERE user_id = ?",
            (r["user_id"],),
        ).fetchone()["cnt"]
        if k_cnt == 0:
            conn.execute(
                "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                (r["user_id"], r["persona_notes"]),
            )

    # Сиды/перенос запускаются один раз: удалённые факты не должны воскресать.
    for uid, pdata in (
        INITIAL_KENTS_PROFILES.items()
        if "kent_facts_entries" not in original_tables
        else ()
    ):
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, user_name, first_name, persona_notes, first_seen, last_seen, message_count)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 100)
            ON CONFLICT(user_id) DO UPDATE SET
                persona_notes = CASE WHEN user_profiles.persona_notes IS NULL OR user_profiles.persona_notes = '' THEN excluded.persona_notes ELSE user_profiles.persona_notes END,
                user_name = CASE WHEN user_profiles.user_name IS NULL OR user_profiles.user_name = '' THEN excluded.user_name ELSE user_profiles.user_name END,
                first_name = CASE WHEN user_profiles.first_name IS NULL OR user_profiles.first_name = '' THEN excluded.first_name ELSE user_profiles.first_name END;
        """,
            (uid, pdata["user_name"], pdata["first_name"], pdata["notes"]),
        )
        k_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM kent_facts_entries WHERE user_id = ?", (uid,)
        ).fetchone()["cnt"]
        if k_cnt == 0:
            conn.execute(
                "INSERT INTO kent_facts_entries (user_id, content, added_by) VALUES (?, ?, 0)",
                (uid, pdata["notes"]),
            )

    # Инициализация / обновление базового лора ключевых конф
    for cid, cdata in (
        INITIAL_CHATS_LORE.items() if "chat_lore_entries" not in original_tables else ()
    ):
        conn.execute(
            """
            INSERT INTO chat_profiles (chat_id, title, chat_lore, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(chat_id) DO UPDATE SET
                chat_lore = CASE WHEN chat_profiles.chat_lore IS NULL OR chat_profiles.chat_lore = '' THEN excluded.chat_lore ELSE chat_profiles.chat_lore END,
                title = CASE WHEN chat_profiles.title IS NULL OR chat_profiles.title = '' THEN excluded.title ELSE chat_profiles.title END;
        """,
            (cid, cdata["title"], cdata["lore"]),
        )
        c_cnt = conn.execute(
            "SELECT COUNT(*) as cnt FROM chat_lore_entries WHERE chat_id = ?", (cid,)
        ).fetchone()["cnt"]
        if c_cnt == 0:
            conn.execute(
                "INSERT INTO chat_lore_entries (chat_id, content, added_by) VALUES (?, ?, 0)",
                (cid, cdata["lore"]),
            )

    # Таблицы стикерпаков и кэшированных стикеров
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sticker_packs (
            pack_name TEXT PRIMARY KEY,
            title TEXT DEFAULT '',
            is_default BOOLEAN DEFAULT 0,
            added_by INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cached_stickers (
            file_id TEXT PRIMARY KEY,
            file_unique_id TEXT,
            pack_name TEXT NOT NULL,
            emoji TEXT DEFAULT '',
            file_size INTEGER DEFAULT 0,
            width INTEGER DEFAULT 0,
            height INTEGER DEFAULT 0,
            sanya_weight REAL DEFAULT 1.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stickers_pack ON cached_stickers(pack_name);"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stickers_emoji ON cached_stickers(emoji);"
    )
