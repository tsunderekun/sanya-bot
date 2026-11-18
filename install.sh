#!/usr/bin/env bash
# =========================================================
# Установочник Telegram-бота «Саня Пониёб» для Linux / macOS
# =========================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if command -v python3 &>/dev/null; then
    PY_CMD="python3"
elif command -v python &>/dev/null; then
    PY_CMD="python"
else
    echo "[!] Ошибка: Python не найден! Установите Python 3.10+."
    exit 1
fi

$PY_CMD install.py "$@"
