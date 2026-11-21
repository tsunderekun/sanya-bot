from aiogram import Router, types
from aiogram.filters import Command

from .reply_style import button

PAGES = {
    "start": (
        "Справка · 1/6\n\nВ группе позови «Спермер», тегни @ponyeb228_bot или ответь на сообщение бота. В личке пиши как обычно. Спроси «Что ты умеешь?», если хочешь короткий обзор своими словами, или выбери раздел:",
        "Старт",
    ),
    "chat": (
        "<b>Общение и настройки · 2/6</b>\n\n"
        "• <code>/настройки</code> — режим, тон, лор, автостикеры, словарь и фразы обращения.\n"
        "• <code>/режим норм|рофл|сейф</code> — переключить режим; в группе меняют админы. «Сейф» отключает стёб, фоновую болтовню и непроверенные медиа.\n"
        "• <code>/тон 0–3</code>, <code>/лор 0–3</code> — настроить тон и частоту отсылок.\n"
        "• <code>/словарь</code> — посмотреть или изменить словарь чата.\n"
        "• <code>/all</code> / <code>@all</code> — позвать админов и известных боту участников.\n"
        "• <code>/автостикеры</code> — частота автоматических стикеров.\n"
        "• <code>/голосовые 25</code> — шанс озвучить короткий ответ или добавить вывод к разбору; <code>/голосовые выкл</code> — выключить. В группе меняют админы.",
        "Чат",
    ),
    "search": (
        "<b>Поиск и картинки · 3/6</b>\n\n"
        "• <code>/поиск запрос</code> — веб-поиск; <code>/пикча запрос</code> — поиск картинок.\n"
        "• <code>/booru теги</code> / <code>/r34</code> — поиск арта. В <code>/r34</code> можно описать арт фразой вместо тегов. Для безопасного поиска добавь <code>safe</code> или <code>general</code> через запятую; иначе потребуется подтверждение.\n"
        "• <code>/слоп теги</code> — поиск только на AIBooru.\n"
        "• <code>/промпт описание</code> — промпты на английском; <code>/идея</code> — случайная сцена после подтверждения.\n"
        "• <code>/мем</code> — случайный мем из пула.",
        "Арты",
    ),
    "tools": (
        "<b>Код и железо · 4/6</b>\n\n"
        "• <code>/код задача</code> — написать код.\n"
        "• <code>/железо</code> / <code>/сетап</code> — известная конфигурация.\n"
        "• <code>/оцени RTX 3070</code> — на что способна видеокарта в играх и рабочих задачах, без оценки цены.\n"
        "• Фото и скриншоты можно отправить Сане на разбор; OCR зависит от доступности.",
        "Код",
    ),
    "memory": (
        "<b>Лор и память · 5/6</b>\n\n"
        "• <code>/лорконфы</code> — факты этой группы; <code>/кент</code> — записи о кентах.\n"
        "• <code>/kents</code> — сводка; <code>/ктотакой @ник</code> — профиль участника.\n"
        "• <code>/досье</code> / <code>/mydata</code> — свои данные; <code>/facts</code> — свои автофакты группы.\n"
        "• <code>/forgetme</code> — удалить свои сообщения и автофакты. <code>/clear</code> — очистить историю чата (в группе админ).\n"
        "• В панелях <code>/кент</code> и <code>/лорконфы</code> админы могут удалить все записи с подтверждением. Досье кента общее для всех чатов.\n"
        "История диалога разделена по чатам и авторам.",
        "Лор",
    ),
    "stickers": (
        "<b>Стикеры и прочее · 6/6</b>\n\n"
        "• <code>/стикеры</code> / <code>/stickers</code> — список паков; <code>/стикер</code> — отправить стикер.\n"
        "• <code>/addstickers ссылка</code> и <code>/delstickers имя</code> — управление паками (админы).\n"
        "• <code>/прекэш [канал] [страниц]</code> — обновить пул мемов.\n"
        "• <code>/delfact ID</code>, <code>/delchat ID</code>, <code>/delkent ID</code> — удаление записей с проверкой прав.\n\n"
        "• <code>/blockkent @ник</code> и <code>/unblockkent @ник</code> — отключить или вернуть ответы бота участнику в этой группе. Можно реплаем; только админы.\n\n"
        "Не сохраняй в лор телефоны, адреса, реквизиты и другие конфиденциальные данные.",
        "Стикеры",
    ),
}


def keyboard(page: str):
    keys = list(PAGES)
    index = keys.index(page)
    categories = [button(label, f"page:{key}") for key, (_, label) in PAGES.items()]
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            categories[:3],
            categories[3:],
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
