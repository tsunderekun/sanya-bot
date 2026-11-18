@echo off
chcp 65001 >nul
:: =========================================================
:: Установочник Telegram-бота «Саня Пониёб» для Windows
:: =========================================================

cd /d "%~dp0"

where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [!] Ошибка: Python не найден в системе!
    echo Скачайте и установите Python 3.10+ с сайта python.org,
    echo обязательно отметив галочку "Add Python to PATH".
    pause
    exit /b 1
)

python install.py %*
pause
