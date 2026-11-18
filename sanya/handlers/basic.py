from aiogram import Router, types
from aiogram.filters import Command

from ..database import Database
from ..repository import Repository


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
        "💬 <b>Основные команды:</b>\n"
        "• <code>/s &lt;запрос&gt;</code> (или <code>/поиск</code>, <code>/найти</code>, <code>/гугл</code>) — поиск в сети через SearXNG\n"
        "• <code>/pic &lt;запрос&gt;</code> (или <code>/пикча</code>, <code>/картинка</code>, <code>/фото</code>) — найти и закинуть сочную картинку в чат\n"
        "• <code>/booru &lt;теги&gt;</code> (или <code>/r34</code>, <code>/derpi</code>, <code>/буру</code>, <code>/р34</code>) — поиск артов на Derpibooru и аниме-бордах\n"
        "• <code>/clear</code> (или <code>/сброс</code>, <code>/чисти</code>) — сбросить контекст диалога в текущем чате\n"
        "• <code>/mydata</code> (или <code>/профиль</code>, <code>/досье</code>, <code>/моиданные</code>) — посмотреть свое досье в базе, счетчик сообщений и статус кента\n"
        "• <code>/forgetme [@user / id / reply]</code> (или <code>/забудьменя</code>, <code>/забудь</code>) — меню подчистки данных (выбор: только сообщения или снос под ноль)\n\n"
        "🏛 <b>Лор и факты конфы:</b>\n"
        "• <code>/setchat &lt;факты&gt;</code> (или <code>/конфа</code>, <code>/лорконфы</code>, <code>/чат</code>) — добавить новый факт в лор текущего чата (добавляется с уникальным [#ID])\n"
        "• <code>/setchat</code> (или <code>/конфа</code>) — посмотреть список всех сохраненных фактов конфы с номерами [#ID]\n"
        "• <code>/delchat &lt;ID&gt;</code> (или <code>/удаличат &lt;ID&gt;</code>, <code>/setchat del &lt;ID&gt;</code>) — выборочно удалить запись из лора конфы по её ID\n"
        "<i>Добавлять и удалять лор конфы могут админы чата и создатель бота. Саня учитывает этот лор во всех ответах в группе!</i>\n\n"
        "👥 <b>Управление кентами:</b>\n"
        "• <b>Саммари по кентам:</b> <code>/kents</code> (или <code>/кенты</code>, <code>/саммари</code>) — нейросаня выдаст сочный расклад по всей банде на основе фактов из базы\n"
        "• <b>Пояснить за кента:</b> <code>/ктотакой @username</code> (или <code>/поясниза @user / id / reply</code>) — Саня пояснит за конкретного кореша\n"
        "• <b>Добавить факт о кенте:</b> <code>/setkent @username &lt;инфа&gt;</code> (или <code>/кент @username ...</code>)\n"
        "  <i>(Факты добавляются аппендом с уникальным [#ID], старые не затираются!)</i>\n"
        "• <b>Удалить факт о кенте:</b> <code>/delkent &lt;ID&gt;</code> (или <code>/delkent @username &lt;ID&gt;</code>, <code>/setkent @user del &lt;ID&gt;</code>)\n"
        "• <b>Через реплай:</b> ответь <code>/setkent &lt;инфа&gt;</code> (или <code>/кент &lt;инфа&gt;</code>) на любое сообщение кента\n"
        "• <b>Через ID:</b> <code>/setkent 123456789 &lt;инфа&gt;</code>\n"
        "• <b>Глянуть досье:</b> <code>/setkent @username</code> (или <code>/кент @username</code>) — список всех фактов кента с номерами [#ID]\n"
        "• <b>Восстановление базы кентов:</b> <code>/restorekents</code> (или <code>/восстановитькентов</code>) — восстановить исходный лор базовых кентов конфы (для админов конфы и бота)\n\n"
        "📝 <b>Как правильно расписывать инфу о кенте:</b>\n"
        "Чтобы Саня реалистично общался с кентом и подкалывал его в тему, пиши по схеме:\n"
        "1. <b>Имя и конфовая кличка:</b> <i>Паша (Армор, Паштет)</i>\n"
        "2. <b>Роль / специализация:</b> <i>шарит за Linux, сидит на скуфе, играет в CS 1.6</i>\n"
        "3. <b>2-3 ключевых мема конфы:</b> <i>купил SSD Kingston за 0 руб, сдохшие уши за 2 года, диспенис</i>\n"
        "4. <b>Как обращаться и подкалывать:</b> <i>Обращайся «Паша» или «Армор». Подкалывай за скуфство и диспенис.</i>\n\n"
        "⚙️ <b>Мультимодальность и код:</b>\n"
        "• Кидай любые фото и скрины (платы, сокеты, ошибки, поняш, мемы) — Саня видит и разбирает их через Vision.\n"
        "• Проси скрипты на Python / Bash или промпты для Stable Diffusion — выдаст рабочий код блоком.\n\n"
        "🖼 <b>Стикеры конфы:</b>\n"
        "• <code>/stickers</code> (или <code>/паки</code>, <code>/стикеры</code>) — список подключенных стикерпаков Сани\n"
        "• <code>/sticker [эмодзи]</code> (или <code>/стикер</code>, <code>/стик</code>) — закинуть фирменный стикер Сани (напр. <code>/sticker 😱</code>)\n"
        "• <code>/addstickers &lt;ссылка/имя&gt;</code> (или <code>/добавитьстикеры</code>) — добавить новый стикерпак в базу (для админов)\n"
        "• <code>/delstickers &lt;имя&gt;</code> (или <code>/удалитьстикеры</code>) — удалить кастомный стикерпак (для админов)\n"
        "<i>Саня понимает присылаемые стикеры, периодически отвечает коронными стикерами оригинала (😱, 😵‍💫, 🙂, 😁, 🥹, 👄) и ставит реакцию кита 🐳 на сообщения!</i>"
    )
    await message.reply(help_text, parse_mode="HTML")


async def cmd_clear(message: types.Message, db: Database):
    await db.run(Repository.clear_history, message.chat.id)
    await message.reply("Память чата сбросил\nЧе хотел")


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
