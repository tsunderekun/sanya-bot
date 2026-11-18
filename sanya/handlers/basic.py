from aiogram import Router, types
from aiogram.enums import ChatType
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository
from .access import check_is_admin


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
        "• <code>/s &lt;запрос&gt;</code> (или <code>/поиск</code>, <code>/найти</code>, <code>/гугл</code>) — поиск в сети через SearXNG\n"
        "• <code>/pic &lt;запрос&gt;</code> (или <code>/пикча</code>, <code>/картинка</code>, <code>/фото</code>) — найти и закинуть сочную картинку в чат\n"
        "• <code>/booru &lt;теги&gt;</code> (или <code>/r34</code>, <code>/derpi</code>, <code>/буру</code>, <code>/р34</code>) — поиск артов на Derpibooru и аниме-бордах\n"
        "• <code>/clear</code> — очистить историю текущего чата в базе бота (в группе только админ)\n"
        "• <code>/mydata</code> (или <code>/профиль</code>, <code>/досье</code>, <code>/моиданные</code>) — посмотреть свое досье в базе, счетчик сообщений и статус кента\n"
        "• <code>/facts</code> — посмотреть свои автофакты в этой группе и частые темы чата.\n"
        "• <code>/forgetme</code> — удалить свои сообщения из базы бота. Полное удаление и выбор другого пользователя доступны только админам бота.\n\n"
        "<b>Лор и факты конфы:</b>\n"
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
        "«У меня RTX 3070», «Поставил себе Windows 11», «Играю в CS 1.6» — бот запомнит со слов автора без ответа и кита. "
        "Вопросы, планы, цитаты и сведения о других не идут в автофакты. Из лички ничего не берётся в тихую память. "
        "Наблюдения остаются в этой группе, видны через <code>/facts</code> и не меняют ручной лор.\n\n"
        "<b>Фото и код:</b>\n"
        "• Кидай любые фото и скрины (платы, сокеты, ошибки, поняш, мемы) — Саня видит и разбирает их через Vision.\n"
        "• Можно попросить скрипт или промпт для Stable Diffusion.\n\n"
        "<b>Стикеры конфы:</b>\n"
        "• <code>/stickers</code> (или <code>/паки</code>, <code>/стикеры</code>) — список подключенных стикерпаков Сани\n"
        "• <code>/sticker [эмодзи]</code> (или <code>/стикер</code>) — прислать настоящий стикер. Эмодзи в аргументе помогает выбрать стикер.\n"
        "• <code>/addstickers &lt;ссылка/имя&gt;</code> (или <code>/добавитьстикеры</code>) — добавить новый стикерпак в базу (для админов)\n"
        "• <code>/delstickers &lt;имя&gt;</code> (или <code>/удалитьстикеры</code>) — удалить кастомный стикерпак (для админов)\n"
        "Реакция кита значит «угарнул»."
    )
    await message.reply(help_text, parse_mode="HTML")


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
