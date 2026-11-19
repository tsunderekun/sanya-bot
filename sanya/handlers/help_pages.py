from aiogram import Router, types
from aiogram.filters import Command

from .reply_style import button

PAGES = {
    "start": (
        "Справка · 1/6\n\nВ группе позови «Спермер», тегни @ponyeb228_bot или ответь на сообщение бота. В личке пиши как обычно. Выбери раздел:",
        "Начало",
    ),
    "chat": (
        "<b>Общение и настройки · 2/6</b>\n\n"
        "• <code>/настройки</code> — режим, тон, лор, автостикеры, словарь и фразы обращения.\n"
        "• <code>/режим норм|рофл</code> — переключить стиль; в группе админы.\n"
        "• <code>/тон 0–3</code>, <code>/лор 0–3</code> — настроить тон и частоту отсылок.\n"
        "• <code>/словарь</code> — посмотреть или изменить словарь чата.\n"
        "• <code>/all</code> / <code>@all</code> — позвать админов и известных боту участников.\n"
        "• <code>/автостикеры</code> — частота автоматических стикеров.",
        "Общение",
    ),
    "search": (
        "<b>Поиск и картинки · 3/6</b>\n\n"
        "• <code>/поиск запрос</code> — веб-поиск; <code>/пикча запрос</code> — поиск картинок.\n"
        "• <code>/booru теги</code> / <code>/r34</code> — поиск арта. Для безопасного поиска добавь <code>safe</code> или <code>general</code>; иначе может потребоваться подтверждение.\n"
        "• <code>/слоп теги</code> — поиск только на AIBooru.\n"
        "• <code>/промпт описание</code> — текст для генерации; <code>/идея</code> — случайная сцена после подтверждения.\n"
        "• <code>/мем</code> — случайный мем из пула.",
        "Поиск",
    ),
    "tools": (
        "<b>Код и железо · 4/6</b>\n\n"
        "• <code>/код задача</code> — написать код.\n"
        "• <code>/железо</code> / <code>/сетап</code> — известная конфигурация.\n"
        "• <code>/оцени RTX 3070</code> / <code>/барыга</code> — ориентир цены по объявлениям.\n"
        "• Фото и скриншоты можно отправить Сане на разбор; OCR зависит от доступности.",
        "Инструменты",
    ),
    "memory": (
        "<b>Лор и память · 5/6</b>\n\n"
        "• <code>/лорконфы</code> — факты этой группы; <code>/кент</code> — записи о кентах.\n"
        "• <code>/kents</code> — сводка; <code>/ктотакой @ник</code> — профиль участника.\n"
        "• <code>/досье</code> / <code>/mydata</code> — свои данные; <code>/facts</code> — свои автофакты группы.\n"
        "• <code>/forgetme</code> — удалить свои сообщения и автофакты. <code>/clear</code> — очистить историю чата (в группе админ).\n"
        "История диалога разделена по чатам и авторам.",
        "Память",
    ),
    "stickers": (
        "<b>Стикеры и прочее · 6/6</b>\n\n"
        "• <code>/стикеры</code> / <code>/stickers</code> — список паков; <code>/стикер</code> — отправить стикер.\n"
        "• <code>/addstickers ссылка</code> и <code>/delstickers имя</code> — управление паками (админы).\n"
        "• <code>/прекэш [канал] [страниц]</code> — обновить пул мемов.\n"
        "• <code>/delfact ID</code>, <code>/delchat ID</code>, <code>/delkent ID</code> — удаление записей с проверкой прав.\n\n"
        "Не сохраняй в лор телефоны, адреса, реквизиты и другие конфиденциальные данные.",
        "Стикеры",
    ),
}


def keyboard(page: str):
    keys = list(PAGES)
    index = keys.index(page)
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [button(label, f"page:{key}") for key, (_, label) in PAGES.items()],
            [
                button("←", f"page:{keys[max(0, index - 1)]}"),
                button(f"{index + 1}/{len(keys)}", f"page:{page}"),
                button("→", f"page:{keys[min(len(keys) - 1, index + 1)]}"),
            ],
            [button("Настройки", "open")],
        ]
    )


async def cmd_help(message: types.Message):
    text, _ = PAGES["start"]
    await message.reply(text, reply_markup=keyboard("start"), parse_mode="HTML")


async def help_page(query: types.CallbackQuery):
    message = query.message
    key = (query.data or "").removeprefix("settings:page:")
    if message is None or not hasattr(message, "edit_text") or key not in PAGES:
        await query.answer("Страница справки устарела")
        return
    text, _ = PAGES[key]
    await query.answer()
    await message.edit_text(text, reply_markup=keyboard(key), parse_mode="HTML")


def make_router():
    router = Router(name=__name__)
    router.message.register(
        cmd_help,
        Command("help", "commands", "хелп", "помощь", "команды", "инфо", "справка"),
    )
    return router
