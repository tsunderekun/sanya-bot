#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Установочник Telegram-бота «Саня Пониёб» (@ponyeb228_bot)
Автоматическая настройка окружения, зависимостей, Ollama-моделей и конфигурации.
"""

import os
import sys
import platform
import subprocess
import shutil
import json
import urllib.request
import urllib.error
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
VENV_DIR = ROOT_DIR / "venv"
IS_WINDOWS = platform.system() == "Windows"
PYTHON_EXE = sys.executable

VENV_PYTHON = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("python.exe" if IS_WINDOWS else "python")
VENV_PIP = VENV_DIR / ("Scripts" if IS_WINDOWS else "bin") / ("pip.exe" if IS_WINDOWS else "pip")


def print_banner():
    banner = r"""
======================================================================
     ____   _    _  _ __   __    _       ____   ___  _____ 
    / ___| / \  | \| |\ \ / /   / \     | __ ) / _ \|_   _|
    \___ \/ _ \ | .` | \ V /   / _ \    |  _ \| | | | | |  
     ___) / ___ \| |\ |  | |   / ___ \   | |_) | |_| | | |  
    |____/_/   \_\_| \_|  |_|  /_/   \_\  |____/ \___/  |_|  
                                                               
      Телеграм-бот «Саня Пониёб» — Установочник франкенштейна
======================================================================
"""
    print(banner)


def check_python_version():
    print("[1/6] Проверка версии Python...")
    v = sys.version_info
    print(f"      Обнаружен Python {v.major}.{v.minor}.{v.micro} ({platform.system()})")
    if v < (3, 10):
        print(f"\n[!] Ошибка: Требуется Python версии 3.10 или выше!")
        print(f"    Текущая версия: {v.major}.{v.minor}. Пожалуйста, обновите Python.")
        sys.exit(1)
    print("      [OK] Версия Python подходит.\n")


def setup_virtualenv():
    print("[2/6] Настройка виртуального окружения (venv)...")
    if not VENV_DIR.exists():
        print(f"      Создание venv в {VENV_DIR}...")
        res = subprocess.run([PYTHON_EXE, "-m", "venv", str(VENV_DIR)])
        if res.returncode != 0:
            print("[!] Ошибка при создании venv!")
            sys.exit(1)
        print("      [OK] Виртуальное окружение создано.")
    else:
        print(f"      [OK] Виртуальное окружение уже существует ({VENV_DIR}).")

    # Проверяем исполняемый файл venv
    if not VENV_PYTHON.exists():
        print(f"[!] Ошибка: {VENV_PYTHON} не найден!")
        sys.exit(1)

    print("      Установка / обновление зависимостей из requirements.txt...")
    req_file = ROOT_DIR / "requirements.txt"
    if not req_file.exists():
        print(f"[!] Файл {req_file} не найден!")
        sys.exit(1)

    # Обновляем pip
    subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip", "-q"])
    # Ставим requirements
    res = subprocess.run([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(req_file)])
    if res.returncode != 0:
        print("[!] Ошибка установки зависимостей!")
        sys.exit(1)
    print("      [OK] Все Python-зависимости успешно установлены.\n")


def check_ollama():
    print("[3/6] Проверка подключения к Ollama...")
    ollama_url = "http://localhost:11434"
    models = []
    is_running = False

    try:
        req = urllib.request.Request(f"{ollama_url}/api/tags", headers={"User-Agent": "SanyaInstaller/1.0"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                is_running = True
    except Exception:
        is_running = False

    if not is_running:
        print(f"      [!] Внимание: Сервер Ollama не запущен или недоступен на {ollama_url}")
        print("          Для работы нейросети установите и запустите Ollama (https://ollama.com)")
        print("          Команда запуска: ollama serve\n")
        return False, []

    print(f"      [OK] Ollama активна на {ollama_url}")
    print(f"      Доступные локальные модели: {', '.join(models) if models else 'пока нет моделей'}\n")
    return True, models


def setup_models(ollama_running, existing_models):
    print("[4/6] Настройка нейросетевых моделей...")
    if not ollama_running:
        print("      Пропуск настройки моделей (Ollama не запущена).")
        print("      Позже вы сможете собрать модель командой: ollama create sanya -f Modelfile\n")
        return

    has_sanya = any("sanya" in m.lower() for m in existing_models)
    has_moondream = any("moondream" in m.lower() for m in existing_models)

    # 1. Vision модель moondream
    if has_moondream:
        print("      [OK] Модель Vision (moondream) уже установлена.")
    else:
        print("      Модель Vision (moondream) не найдена.")
        choice = input("      Скачать moondream для анализа фото и скриншотов плат/железа? [Y/n]: ").strip().lower()
        if choice in ("", "y", "yes", "д", "да"):
            print("      Загрузка moondream (около 1.7 GB)...")
            subprocess.run(["ollama", "pull", "moondream"])

    # 2. Основная модель sanya
    if has_sanya:
        print("      [OK] Модель 'sanya' уже зарегистрирована в Ollama.\n")
        return

    print("      Основная модель 'sanya' не найдена в Ollama.")
    print("      Выберите вариант создания модели:")
    print("        1) Собрать 'sanya' на базе gemma2:9b (рекомендуется, баланс скорости и угара)")
    print("        2) Собрать 'sanya' на базе qwen2.5:7b (легче, отлично для слабых карт/ноутбуков)")
    print("        3) Указать путь к своему .gguf файлу (например, gemma 12b)")
    print("        4) Использовать существующую модель без пересборки (указать имя в .env)")
    print("        5) Пропустить настройку модели")

    ans = input("      Ваш выбор [1]: ").strip()
    if ans in ("", "1"):
        base_model = "gemma2:9b"
        print(f"      Подготовка базовой модели {base_model}...")
        subprocess.run(["ollama", "pull", base_model])
        create_custom_modelfile(base_model)
    elif ans == "2":
        base_model = "qwen2.5:7b"
        print(f"      Подготовка базовой модели {base_model}...")
        subprocess.run(["ollama", "pull", base_model])
        create_custom_modelfile(base_model)
    elif ans == "3":
        gguf_path = input("      Введите полный путь к .gguf файлу: ").strip().strip('"').strip("'")
        if os.path.exists(gguf_path):
            create_custom_modelfile(gguf_path, is_file=True)
        else:
            print(f"      [!] Файл {gguf_path} не найден! Пропуск сборки.")
    else:
        print("      Пропуск создания модели 'sanya'. Укажите нужную модель в .env.")

    print()


def create_custom_modelfile(source: str, is_file: bool = False):
    modelfile_path = ROOT_DIR / "Modelfile.generated"
    content = f"""FROM {source}

TEMPLATE \"\"\"{{{{ if .System }}}}<|turn>system
{{{{ .System }}}}<turn|>
{{{{ end }}}}{{{{ range .Messages }}}}{{{{ if eq .Role "user" }}}}<|turn>user
{{{{ .Content }}}}<turn|>
{{{{ else if eq .Role "assistant" }}}}<|turn>model
{{{{ .Content }}}}<turn|>
{{{{ end }}}}{{{{ end }}}}<|turn>model
<|channel>thought
<channel|>
\"\"\"

PARAMETER stop "<turn|>"
PARAMETER stop "<|turn>"
PARAMETER temperature 0.70
PARAMETER top_p 0.90
PARAMETER repeat_penalty 1.15
PARAMETER repeat_last_n 160
PARAMETER num_ctx 8192
"""
    with open(modelfile_path, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"      Сборка модели 'sanya' через Ollama...")
    res = subprocess.run(["ollama", "create", "sanya", "-f", str(modelfile_path)])
    if res.returncode == 0:
        print("      [OK] Модель 'sanya' успешно создана в Ollama!")
    else:
        print("      [!] Не удалось создать модель через ollama create.")


def configure_env():
    print("[5/6] Настройка конфигурации (.env)...")
    env_file = ROOT_DIR / ".env"
    env_example = ROOT_DIR / ".env.example"

    config = {}
    if env_file.exists():
        print(f"      Обнаружен существующий {env_file}.")
        reconf = input("      Перенастроить заново? [y/N]: ").strip().lower()
        if reconf not in ("y", "yes", "д", "да"):
            print("      [OK] Используется текущий .env файл.\n")
            return

    # Запрашиваем токен
    token = ""
    while not token:
        token = input("      Введи TELEGRAM_BOT_TOKEN от @BotFather: ").strip()
        if not token:
            print("      [!] Токен обязателен для работы бота!")

    # Запрашиваем ID админа
    admin_id = input("      Введи свой Telegram ID (админ бота, узнать у @userinfobot): ").strip()
    if not admin_id:
        admin_id = "0"

    # Имя модели
    model_name = input("      Имя модели в Ollama [sanya]: ").strip()
    if not model_name:
        model_name = "sanya"

    # Прокси
    proxy = input("      Прокси для Telegram (SOCKS5/HTTP, нажми Enter если не нужен): ").strip()

    # Поиск
    enable_search = input("      Включить веб-поиск (цены на Авито, спеки, новости)? [Y/n]: ").strip().lower()
    search_on = "true" if enable_search in ("", "y", "yes", "д", "да") else "false"

    search_engine = "duckduckgo"
    searxng_url = "http://localhost:8080"
    if search_on == "true":
        engine_choice = input("      Поисковый движок: 1 - DuckDuckGo (без настройки), 2 - SearXNG [1]: ").strip()
        if engine_choice == "2":
            search_engine = "searxng"
            searxng_url = input("      URL SearXNG [http://localhost:8080]: ").strip() or "http://localhost:8080"

    content = f"""# Токен бота от @BotFather
TELEGRAM_BOT_TOKEN={token}

# Адрес локального LLM-сервера (Ollama)
OPENAI_BASE_URL=http://localhost:11434
OPENAI_API_KEY=ollama
MODEL_NAME={model_name}
VISION_MODEL_NAME=moondream

# ID суперадминов бота через запятую
ADMIN_IDS={admin_id}

# Длина сохраняемой истории контекста
MAX_HISTORY_LENGTH=25
MAX_CONTEXT_CHARS=22000

# Настройки прокси (оставь пустым, если Telegram работает напрямую)
TELEGRAM_PROXY={proxy}
SEARCH_PROXY={proxy}

# Веб-поиск
ENABLE_WEB_SEARCH={search_on}
SEARCH_ENGINE={search_engine}
SEARXNG_URL={searxng_url}

# Настройки реакций и стикеров
WHALE_REACTION_CHANCE=0.08
TOP_STICKERS_CHANCE=0.985
"""
    with open(env_file, "w", encoding="utf-8") as f:
        f.write(content)

    print(f"      [OK] Конфигурация сохранена в {env_file}.\n")


def create_launchers():
    print("[6/6] Создание скриптов быстрого запуска...")

    # start.sh для Linux / macOS
    start_sh = ROOT_DIR / "start.sh"
    with open(start_sh, "w", encoding="utf-8") as f:
        f.write("""#!/usr/bin/env bash
cd "$(dirname "$0")"
if [ -d "venv" ]; then
    source venv/bin/activate
fi
python3 bot.py
""")
    try:
        start_sh.chmod(0o755)
    except Exception:
        pass

    # start.bat для Windows
    start_bat = ROOT_DIR / "start.bat"
    with open(start_bat, "w", encoding="utf-8") as f:
        f.write("""@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist venv\\Scripts\\activate.bat (
    call venv\\Scripts\\activate.bat
)
python bot.py
pause
""")

    print(f"      [OK] Созданы: {start_sh.name} и {start_bat.name}")

    # Генерация systemd service на Linux
    if platform.system() == "Linux":
        service_file = ROOT_DIR / "sanya-bot.service"
        current_user = os.environ.get("USER", "root")
        with open(service_file, "w", encoding="utf-8") as f:
            f.write(f"""[Unit]
Description=Sanya Ponyeb Telegram Bot
After=network.target

[Service]
Type=simple
WorkingDirectory={ROOT_DIR}
ExecStart={VENV_PYTHON} {ROOT_DIR / 'bot.py'}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")
        print(f"      [OK] Сгенерирован шаблон systemd службы: {service_file.name}")
        print("          Для автозапуска в фоновом режиме:")
        print(f"          mkdir -p ~/.config/systemd/user && cp {service_file.name} ~/.config/systemd/user/")
        print("          systemctl --user daemon-reload && systemctl --user enable --now sanya-bot.service")

    print("\n======================================================================")
    print("   [+] Установка завершена успешно! Саня готов к общению.")
    print("======================================================================")
    if IS_WINDOWS:
        print("   Запуск бота: дважды кликни на start.bat")
    else:
        print("   Запуск бота: ./start.sh (или python3 bot.py)")
    print("======================================================================\n")


def main():
    print_banner()
    check_python_version()
    setup_virtualenv()
    ollama_ok, models = check_ollama()
    setup_models(ollama_ok, models)
    configure_env()
    create_launchers()


if __name__ == "__main__":
    main()
