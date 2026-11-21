import os
from pathlib import Path
from typing import Set

from dotenv import load_dotenv

load_dotenv()

SLOIPER_DIR = Path(
    os.getenv(
        "SLOIPER_DIR", str(Path(__file__).resolve().parents[1] / "data" / "sloiper")
    )
)
SLOIPER_CHANCE = max(0.0, min(1.0, float(os.getenv("SLOIPER_CHANCE", "0.2"))))
CHAT_STICKER_CHANCE = max(0.0, min(1.0, float(os.getenv("CHAT_STICKER_CHANCE", "0.2"))))
MEME_DONOR_CHANNEL_IDS = {
    int(item)
    for raw in os.getenv("MEME_DONOR_CHANNEL_IDS", "").split(",")
    if (item := raw.strip()).removeprefix("-").isdecimal()
}
TESSERACT_CMD = os.getenv("TESSERACT_CMD", "tesseract")
OCR_ENABLED = os.getenv("OCR_ENABLED", "true").lower() in ("1", "true", "yes")
PIPER_MODEL_PATH = os.getenv("PIPER_MODEL_PATH", "").strip()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "sanya:latest")
VISION_MODEL_NAME = os.getenv("VISION_MODEL_NAME", "moondream:latest")
MAX_HISTORY = max(1, min(250, int(os.getenv("MAX_HISTORY_LENGTH", "100"))))
MAX_CONTEXT_CHARS = max(1, min(14000, int(os.getenv("MAX_CONTEXT_CHARS", "14000"))))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "20"))
USER_COOLDOWN_SECONDS = float(os.getenv("USER_COOLDOWN_SECONDS", "5.0"))
MAX_PROACTIVE_PER_HOUR = int(os.getenv("MAX_PROACTIVE_PER_HOUR", "3"))
NUM_PREDICT_CHAT = int(os.getenv("NUM_PREDICT_CHAT", "400"))
ENABLE_THINKING = os.getenv("ENABLE_THINKING", "true").lower() in ("1", "true", "yes")
NUM_PREDICT_THINK = int(os.getenv("NUM_PREDICT_THINK", "1500"))
TELEGRAM_PROXY = os.getenv("TELEGRAM_PROXY", "")
SEARCH_PROXY = os.getenv("SEARCH_PROXY", TELEGRAM_PROXY)
RULE34_USER_ID = os.getenv("RULE34_USER_ID", "").strip()
RULE34_API_KEY = os.getenv("RULE34_API_KEY", "").strip()
ENABLE_WEB_SEARCH = os.getenv("ENABLE_WEB_SEARCH", "true").lower() in (
    "1",
    "true",
    "yes",
)

# Конфигурация поискового движка: 'searxng' или 'duckduckgo'
SEARCH_ENGINE = os.getenv("SEARCH_ENGINE", "searxng").lower()
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080")
DB_PATH = os.getenv(
    "DB_PATH", os.path.join(str(Path(__file__).resolve().parents[1]), "sanya_memory.db")
)
ORIGINAL_SANYA_ID = int(os.getenv("ORIGINAL_SANYA_ID", "368426071"))
ADMIN_IDS: Set[int] = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", f"302072457,{ORIGINAL_SANYA_ID}").split(",")
    if x.strip().isdigit()
}

# Настройки реакции кита 🐳
WHALE_REACTION_CHANCE = float(os.getenv("WHALE_REACTION_CHANCE", "0.08"))
# Шанс использования строго самых популярных стикеров Сани (по умолчанию 98.5%, на остальные — 1.5% ультраминимальный шанс)
TOP_STICKERS_CHANCE = float(os.getenv("TOP_STICKERS_CHANCE", "0.985"))
