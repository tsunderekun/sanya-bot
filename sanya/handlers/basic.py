import logging

from aiogram import F, Router, types
from aiogram.enums import ChatType
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository
from .access import check_is_admin
from .command_args import command_args, reply_html_lines
from .reply_style import (
    MODE_ARGS,
    MODE_LABELS,
    SETTINGS_PREFIX,
    button,
    cmd_lore_level,
    cmd_settings,
    cmd_tone,
    show_settings,
    update_settings,
)
from .vocabulary import cmd_vocabulary, dictionary_button

log = logging.getLogger(__name__)
RELEASE_ACK = "reply-mode:seen"


async def cmd_reply_mode(message: types.Message, db: Database):
    arg = command_args(message).casefold()
    chat = message.chat
    if not arg:
        await show_settings(message, db, "mode")
        return
    if arg not in MODE_ARGS:
        await message.reply("Выбери /режим норм или /режим рофл")
        return
    if chat.type != ChatType.PRIVATE and (
        not message.from_user or not await check_is_admin(chat, message.from_user.id)
    ):
        await message.reply("В группе режим может поменять только админ.")
        return
    mode = MODE_ARGS[arg]
    await db.run(Repository.set_chat_reply_mode, chat.id, mode)
    await message.reply(f"Режим этого чата: {MODE_LABELS[mode]}")


async def confirm_release(query: types.CallbackQuery):
    msg = query.message
    if (
        not isinstance(msg, types.Message)
        or msg.chat.type != ChatType.PRIVATE
        or msg.chat.id != query.from_user.id
    ):
        await query.answer()
        return
    await query.answer("Vizhu, podtverzhdenie prinyato")
    await msg.edit_reply_markup(reply_markup=None)
    log.info("Reply mode release acknowledged")


async def cmd_start(message: types.Message, db: Database):
    if message.from_user:
        uname = message.from_user.username or ""
        fname = message.from_user.first_name or ""
        await db.run(Repository.update_user_profile, message.from_user.id, uname, fname)
    await message.reply(
        "Здарова\nЯ Саня Пониёб\n"
        "Че надо — спрашивай, по железу, сетям, стейблу поясню защеку\n\n"
        "Список всех команд: /help (или /помощь)",
        parse_mode="HTML",
    )


async def cmd_help(message: types.Message):
    help_text = (
        "<b>Команды и возможности Сани Пониёба:</b>\n\n"
        "В группе позови «Спермер», тегни <code>@ponyeb228_bot</code> или ответь на сообщение бота. «Саня» и тег живого Сани бота не зовут. В личке пиши как обычно.\n\n"
        "<b>Основные команды:</b>\n"
        "• <code>@all</code> или <code>/all</code> — позвать админов и известных боту участников группы. После последнего вызова в группе админы ждут 5 минут, остальные — 15. Если Telegram не передаёт боту обычные сообщения, используй <code>/all</code>.\n"
        "• <code>/настройки</code> или <code>/settings</code> — меню с кнопками: режим, тон, лор и словарь.\n"
        "• <code>/тон</code> (<code>/tone</code>) и <code>/лор</code> (<code>/lore</code>) — показать уровень; добавь число 0–3, чтобы изменить. 0 — дружелюбно / лор только по запросу, 1 — как раньше, 3 — жёсткий стёб / больше отсылок по теме. Настройки свои в каждом чате, в группе меняют админы.\n"
        "• <code>/словарь</code> или <code>/dict</code> — посмотреть слова. <code>/словарь слово</code> — значение. <code>/словарь добавить слово = значение</code> и <code>/словарь удалить слово</code> — правки словаря этого чата (в группе только админы). Встроенные слова защищены. Не добавляй личные данные.\n"
        "• <code>/режим</code> или <code>/mode</code> — текущий режим; <code>/режим норм</code> или <code>/режим рофл</code> — переключить (в группе только админ).\n"
        "• <code>/s &lt;запрос&gt;</code> (или <code>/поиск</code>, <code>/найти</code>, <code>/гугл</code>) — поиск в сети через SearXNG\n"
        "• <code>/pic &lt;запрос&gt;</code> (или <code>/пикча</code>, <code>/картинка</code>, <code>/фото</code>) — найти и закинуть сочную картинку в чат\n"
        "• <code>/booru &lt;теги&gt;</code> (или <code>/r34</code>, <code>/derpi</code>, <code>/буру</code>, <code>/р34</code>) — поиск артов на Derpibooru и аниме-бордах\n"
        "• <code>/мем</code> или <code>/meme</code> — случайный канальный мем без подписи. На «хорс» иногда прилетает арт.\n"
        "• <code>/precache [канал] [страниц]</code> (или <code>/прекэш</code>) — затянуть пачку мемов из канала в базу, чтобы не повторялись\n"
        "• <code>/железо</code> или <code>/сетап</code> — твоё железо со слов в этом чате. <code>/железо витек</code> — подтверждённый конфиг кента.\n"
        "• <code>/оцени RTX 3070</code> (или <code>/барыга</code>) — ориентир по объявлениям Авито, если их достаточно.\n"
        "• <code>/clear</code> — очистить историю текущего чата в базе бота (в группе только админ)\n"
        "• <code>/досье</code> (или <code>/mydata</code>, <code>/профиль</code>) — своё досье. <code>/досье @ник</code>, ID или реплай — профиль участника текущей группы без личных заметок\n"
        "• <code>/facts</code> — посмотреть свои автофакты в этой группе и частые темы чата.\n"
        "• <code>/forgetme</code> — удалить свои сообщения и тихие факты. Полное удаление и выбор другого пользователя доступны только админам бота.\n\n"
        "<b>Лор и факты конфы:</b>\n"
        "Спроси «Спермер, расскажи про конфу», «Спермер, что за &lt;мем&gt;?» или «Спермер, кто такой @ник?» — ответ по сохранённым записям даже в режиме рофла.\n"
        "• <code>/setchat &lt;факты&gt;</code> (или <code>/конфа</code>, <code>/лорконфы</code>, <code>/чат</code>) — добавить новый факт в лор текущего чата (добавляется с уникальным [#ID])\n"
        "• <code>/setchat</code> (или <code>/конфа</code>) — посмотреть список всех сохраненных фактов конфы с номерами [#ID]\n"
        "• <code>/delchat &lt;ID&gt;</code> (или <code>/удаличат &lt;ID&gt;</code>, <code>/setchat del &lt;ID&gt;</code>) — выборочно удалить запись из лора конфы по её ID\n"
        "• <code>/delfact &lt;ID&gt;</code> — удалить автофакт чата. Админ группы может удалять только автофакты своей группы; у админов бота доступ шире.\n\n"
        "<b>Управление кентами:</b>\n"
        "• <b>Саммари по кентам:</b> <code>/kents</code> (или <code>/кенты</code>, <code>/саммари</code>) — нейросаня выдаст сочный расклад по всей банде на основе фактов из базы\n"
        "• <b>Пояснить за кента:</b> <code>/ктотакой @username</code> (или <code>/поясниза @user / id / reply</code>) — Саня пояснит за конкретного кореша\n"
        "• <b>Добавить факт о кенте:</b> <code>/setkent @username &lt;инфа&gt;</code> (или <code>/кент @username ...</code>)\n"
        "  <i>Добавлять и удалять ручные факты могут админы. Каждая запись получает свой номер; старые не затираются.</i>\n"
        "• <b>Удалить факт о кенте:</b> <code>/delkent &lt;ID&gt;</code> (или <code>/delkent @username &lt;ID&gt;</code>, <code>/setkent @user del &lt;ID&gt;</code>)\n"
        "• <b>Новый кент или неизвестный тег?</b> Ответь <code>/setkent &lt;факт&gt;</code> на его сообщение. Если профиля ещё нет, бот его создаст.\n"
        "• <b>Через ID:</b> <code>/setkent 123456789 &lt;инфа&gt;</code>\n"
        "• <b>Глянуть досье:</b> <code>/setkent @username</code> (или <code>/кент @username</code>) — список всех фактов кента с номерами [#ID]\n"
        "• <code>/restorekents</code> — вернуть недостающие базовые заметки, не трогая ручные (для админов).\n\n"
        "<b>Тихая память в группе:</b>\n"
        "«У меня RTX 3070», «Играю в CS 1.6», «Люблю кофе», «Ненавижу пиццу с ананасами» — бот запомнит со слов автора без ответа и кита. "
        "Вкусы, увлечения и привычки не ограничены словарём: например, «Мне нравится джаз» или «Коллекционирую марки». "
        "Чувствительные сведения бот старается отсеивать. "
        "Вопросы, планы, цитаты и сведения о других не идут в автофакты. Из лички ничего не берётся в тихую память. "
        "Наблюдения остаются в этой группе, видны через <code>/facts</code> и не меняют ручной лор.\n\n"
        "<b>Фото и код:</b>\n"
        "• Кидай фото и скрины — Саня разбирает изображение, а если доступен OCR, читает русский и английский текст.\n"
        "• Можно попросить скрипт или промпт для Stable Diffusion.\n\n"
        "<b>Стикеры конфы:</b>\n"
        "• <code>/stickers</code> (или <code>/паки</code>, <code>/стикеры</code>) — список подключенных стикерпаков Сани\n"
        "• <code>/sticker [эмодзи]</code> (или <code>/стикер</code>) — прислать настоящий стикер. Эмодзи в аргументе помогает выбрать стикер.\n"
        "• <code>/addstickers &lt;ссылка/имя&gt;</code> (или <code>/добавитьстикеры</code>) — добавить новый стикерпак в базу (для админов)\n"
        "• <code>/delstickers &lt;имя&gt;</code> (или <code>/удалитьстикеры</code>) — удалить кастомный стикерпак (для админов)\n"
        "Реакция кита значит «угарнул»."
    )
    await reply_html_lines(
        message,
        help_text.splitlines(),
        last_markup=types.InlineKeyboardMarkup(
            inline_keyboard=[[button("Настройки", "open")]]
        ),
    )


async def cmd_clear(message: types.Message, db: Database):
    uid = message.from_user.id if message.from_user else 0
    if message.chat.type != ChatType.PRIVATE and not await check_is_admin(
        message.chat, uid
    ):
        await message.reply("Историю группы может очистить только админ")
        return
    count = await db.run(Repository.clear_history, message.chat.id)
    if count is None:
        await message.reply("Не удалось очистить историю")
        return
    await message.reply(
        f"Историю чата в базе очистил ({count} сообщений). Лор и профили не трогал."
    )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(cmd_start, Command("start", "старт"))
    router.message.register(cmd_settings, Command("settings", "настройки"))
    router.message.register(cmd_reply_mode, Command("mode", "режим"))
    router.message.register(cmd_tone, Command("tone", "тон", "агрессия"))
    router.message.register(cmd_lore_level, Command("lore", "лор"))
    router.message.register(cmd_vocabulary, Command("dict", "словарь"))
    router.callback_query.register(confirm_release, F.data == RELEASE_ACK)
    router.callback_query.register(
        dictionary_button, F.data.startswith(SETTINGS_PREFIX + "word:")
    )
    router.callback_query.register(update_settings, F.data.startswith(SETTINGS_PREFIX))
    router.message.register(
        cmd_help,
        Command("help", "commands", "хелп", "помощь", "команды", "инфо", "справка"),
    )
    router.message.register(
        cmd_clear,
        Command(
            "clear", "reset", "сброс", "очистить", "чисти", "ресет", "забытьдиалог"
        ),
    )
    return router
