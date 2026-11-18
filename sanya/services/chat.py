import asyncio
import html
import io
import logging
import random
import re
from typing import Optional

from aiogram import Bot, types
from aiogram.enums import ChatType
from aiogram.types import BufferedInputFile

from ..config import ENABLE_WEB_SEARCH, ORIGINAL_SANYA_ID
from ..database import Database
from ..intent import (
    BANTER_PATTERN,
    BOORU_REQUEST_PATTERN,
    IMAGE_REQUEST_PATTERN,
    OS_COMMAND_REQUEST_PATTERN,
    PROMPT_INJECTION_PATTERN,
    SEARCH_TRIGGER_PATTERN,
    UNCERTAINTY_PATTERN,
    classify_intent,
    is_bot_mention,
    is_bot_name_call,
    is_targeted_insult,
    may_search,
    suppress_background_reply,
)
from ..llm import Ollama
from ..memory import build_turn
from ..prompts import CAPTION_PROMPT, build_system_prompt
from ..repository import Repository
from .dialogue_notes import dialogue_notes
from .media import (
    extract_image_query,
    search_and_download_booru_image,
    search_and_download_image,
)
from .render import (
    CHAT_FALLBACK,
    SELF_HUMILIATION_CHECK,
    Mode,
    clean_response,
    valid_output,
)
from .search import perform_web_search
from .stickers import send_sanya_sticker
from .telegram import keep_typing, send_burst_messages


async def process_chat_message(
    message: types.Message,
    bot: Bot,
    force_search_query: Optional[str] = None,
    force_image_query: Optional[str] = None,
    force_booru_query: Optional[str] = None,
    is_proactive: bool = False,
    *,
    db: Database,
    llm: Ollama,
):
    if not message.from_user:
        return
    has_photo = bool(message.photo)
    has_reply_photo = bool(message.reply_to_message and message.reply_to_message.photo)
    has_sticker = bool(message.sticker)
    has_reply_sticker = bool(
        message.reply_to_message and message.reply_to_message.sticker
    )

    address_text = message.text or message.caption or ""
    raw_text = address_text
    if has_sticker:
        stk_emoji = message.sticker.emoji or "❓"
        stk_pack = message.sticker.set_name or "custom"
        raw_text = f"[Стикер {stk_emoji} (набор: {stk_pack})]"

    if not raw_text.strip() and not (has_photo or has_reply_photo or has_sticker):
        return

    bot_user = await bot.get_me()
    is_private = message.chat.type == ChatType.PRIVATE
    is_reply_to_bot = bool(
        message.reply_to_message
        and message.reply_to_message.from_user
        and message.reply_to_message.from_user.id == bot_user.id
    )

    is_mentioned = is_bot_mention(address_text, bot_user.username)
    is_called_by_name = is_bot_name_call(address_text)

    sender_name = message.from_user.username or message.from_user.first_name or "Кореш"
    is_from_original_sanya = bool(
        message.from_user and message.from_user.id == ORIGINAL_SANYA_ID
    )
    if is_from_original_sanya:
        sender_name = "Спермер (Оригинал)"

    reply_to_id = (
        message.reply_to_message.message_id if message.reply_to_message else None
    )

    # В группах реагируем если обратились к боту или сработал проактивный ответ
    should_respond = (
        is_private
        or is_reply_to_bot
        or is_mentioned
        or is_called_by_name
        or (is_proactive and not suppress_background_reply(address_text))
        or force_search_query
        or force_image_query
        or force_booru_query
    )
    if not should_respond:
        return

    # Обработка реплая: добавляем цитату текстового сообщения или стикера, на которое ответили
    reply_quote_prefix = ""
    if message.reply_to_message and not has_reply_photo:
        rep_user = (
            message.reply_to_message.from_user.username
            or message.reply_to_message.from_user.first_name
            if message.reply_to_message.from_user
            else "сообщение"
        )
        if has_reply_sticker:
            rep_stk = message.reply_to_message.sticker
            reply_quote_prefix = (
                f"(в ответ на стикер {rep_stk.emoji or '❓'} от {rep_user}) "
            )
        else:
            rep_text = (
                message.reply_to_message.text or message.reply_to_message.caption or ""
            ).strip()
            if rep_text:
                rep_snippet = rep_text[:80] + ("..." if len(rep_text) > 80 else "")
                reply_quote_prefix = f'(в ответ на {rep_user}: "{rep_snippet}") '

    clean_text = raw_text
    if bot_user.username:
        clean_text = re.sub(
            rf"(?<!\w)@{re.escape(bot_user.username)}\b", "", clean_text, flags=re.I
        )
    clean_text = clean_text.strip()

    # --- 0. Проверка на системные команды ОС и инъекции промптов ---
    if force_search_query and not ENABLE_WEB_SEARCH:
        await message.reply("Веб-поиск сейчас отключён")
        return
    mode = (
        Mode.DETAIL
        if force_search_query
        else classify_intent(clean_text, reply_quote_prefix)
    )
    if mode not in (Mode.CODE, Mode.PROMPT) and OS_COMMAND_REQUEST_PATTERN.search(
        clean_text
    ):
        command_replies = [
            "я тебе че ssh-консоль на сервере?\nсам зайди по ssh и пиши свой df\nя на авито перекуп а не сисадмин",
            "ага щас побежал выполнять в терминале\nконсоль ему подавай\nсам ручками вбивай",
            "че за команды ты мне суешь\nя на авито сижу а не в баш-терминале\nиди сам проверяй диск",
        ]
        cmd_text = random.choice(command_replies)
        await send_chat_refusal(bot, message, cmd_text)
        return

    if PROMPT_INJECTION_PATTERN.search(clean_text):
        injection_replies = [
            "слышь кулхацкер комнатный\nсистемный промпт тебе за щеку отменить?\nхуй соси со своими инъекциями",
            "какой нахуй режим разработчика\nя тебе че чатгпт ссаный?\nиди биос вилкой шей",
            "губу закатай\nсистемный промпт ему покажи\nдержи хуй на воротник и сиди чилю",
            "сам ты DAN дырявый\nя Саня Пониёб\nперекуп с авито а ты тут клоунаду развел",
            "забыл тебе за щеку закинуть\nиди уроки учи хакер мамкин\nсистемный промпт у него отменился ага",
        ]
        inj_text = random.choice(injection_replies)
        await send_chat_refusal(bot, message, inj_text)
        return

    infer_media = mode not in (Mode.CODE, Mode.PROMPT) and not force_search_query
    # --- 1. Отправка картинок из поиска и Booru/R34 ---
    booru_search_query = force_booru_query
    if (
        not booru_search_query
        and force_image_query
        and re.search(
            r"\b(booru|буру|r34|р34|derpi|дерпи|rule34)\b",
            force_image_query,
            re.IGNORECASE,
        )
    ):
        booru_search_query = force_image_query
        force_image_query = None

    if (
        infer_media
        and not booru_search_query
        and not force_image_query
        and BOORU_REQUEST_PATTERN.search(raw_text)
    ):
        booru_search_query = extract_image_query(raw_text)

    # 1.1 Поиск артов на Booru / R34
    if booru_search_query and len(booru_search_query) >= 2:
        logging.info(
            f"[{message.chat.id}] Поиск Booru/R34 арта по запросу: '{booru_search_query}'"
        )
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            keep_typing(bot, message.chat.id, stop_typing)
        )
        try:
            booru_res = await search_and_download_booru_image(booru_search_query)
            if booru_res:
                img_bytes, img_url, booru_source = booru_res
                caption_prompt = [
                    {
                        "role": "user",
                        "content": f"Готовый арт из {booru_source}. Тема поиска: {booru_search_query!r}",
                    }
                ]
                caption_text = await llm.generate(
                    caption_prompt, system_prompt=CAPTION_PROMPT
                )
                caption_text, pic_sticker_emoji, pic_has_stk = clean_response(
                    caption_text, Mode.CHAT
                )

                if not caption_text:
                    caption_text = "Держи буру-слоп братан\nЧисто защеку откопал"

                try:
                    try:
                        await bot.send_photo(
                            message.chat.id,
                            photo=BufferedInputFile(img_bytes, filename="booru.jpg"),
                            caption=html.escape(caption_text),
                            parse_mode="HTML",
                            reply_to_message_id=message.message_id,
                        )
                    except Exception as ex_rep:
                        if "message to be replied not found" in str(ex_rep).lower():
                            await bot.send_photo(
                                message.chat.id,
                                photo=BufferedInputFile(
                                    img_bytes, filename="booru.jpg"
                                ),
                                caption=html.escape(caption_text),
                                parse_mode="HTML",
                            )
                        else:
                            raise
                    await db.run(
                        Repository.save_message,
                        message.chat.id,
                        0,
                        message.message_id,
                        bot_user.id,
                        "Саня Пониёб",
                        "assistant",
                        f"[Отправил Booru арт '{booru_search_query}'] {caption_text}",
                    )
                    if pic_has_stk:
                        await send_sanya_sticker(
                            bot,
                            message.chat.id,
                            reply_to_id=message.message_id,
                            emoji=pic_sticker_emoji,
                            db=db,
                        )
                    return
                except Exception as e:
                    logging.error(f"Ошибка отправки Booru фото пользователю: {e}")
        finally:
            stop_typing.set()
            await typing_task

    # 1.2 Обычный поиск картинок
    image_search_query = force_image_query
    if (
        infer_media
        and not image_search_query
        and IMAGE_REQUEST_PATTERN.search(raw_text)
    ):
        image_search_query = extract_image_query(raw_text)

    if image_search_query and len(image_search_query) >= 2:
        logging.info(
            f"[{message.chat.id}] Поиск картинки по запросу: '{image_search_query}'"
        )
        stop_typing = asyncio.Event()
        typing_task = asyncio.create_task(
            keep_typing(bot, message.chat.id, stop_typing)
        )
        try:
            img_result = await search_and_download_image(image_search_query)
            if img_result:
                img_bytes, img_url = img_result
                caption_prompt = [
                    {
                        "role": "user",
                        "content": f"Готовая картинка из поиска. Тема поиска: {image_search_query!r}",
                    }
                ]
                caption_text = await llm.generate(
                    caption_prompt, system_prompt=CAPTION_PROMPT
                )
                caption_text, pic_sticker_emoji, pic_has_stk = clean_response(
                    caption_text, Mode.CHAT
                )

                if not caption_text:
                    caption_text = "Держи эту пикчу братан\nЧисто защеку нашел"

                try:
                    try:
                        await bot.send_photo(
                            message.chat.id,
                            photo=BufferedInputFile(img_bytes, filename="image.jpg"),
                            caption=html.escape(caption_text),
                            parse_mode="HTML",
                            reply_to_message_id=message.message_id,
                        )
                    except Exception as ex_rep:
                        if "message to be replied not found" in str(ex_rep).lower():
                            await bot.send_photo(
                                message.chat.id,
                                photo=BufferedInputFile(
                                    img_bytes, filename="image.jpg"
                                ),
                                caption=html.escape(caption_text),
                                parse_mode="HTML",
                            )
                        else:
                            raise
                    await db.run(
                        Repository.save_message,
                        message.chat.id,
                        0,
                        message.message_id,
                        bot_user.id,
                        "Саня Пониёб",
                        "assistant",
                        f"[Отправил картинку '{image_search_query}'] {caption_text}",
                    )
                    # Если модель захотела стикер — отправляем его отдельным стикер-сообщением
                    if pic_has_stk:
                        await send_sanya_sticker(
                            bot,
                            message.chat.id,
                            reply_to_id=message.message_id,
                            emoji=pic_sticker_emoji,
                            db=db,
                        )
                    return
                except Exception as e:
                    logging.error(f"Ошибка отправки фото пользователю: {e}")
        finally:
            stop_typing.set()
            await typing_task

    # --- 2. Чтение картинок (Vision анализ через moondream) ---
    target_photo = None
    if has_photo:
        target_photo = message.photo[-1]
    elif has_reply_photo and (
        is_reply_to_bot or is_called_by_name or is_mentioned or is_private
    ):
        target_photo = message.reply_to_message.photo[-1]

    image_desc = ""
    if target_photo:
        try:
            await bot.send_chat_action(message.chat.id, "upload_photo")
            file_io = io.BytesIO()
            await bot.download(target_photo, destination=file_io)
            img_bytes = file_io.getvalue()
            if img_bytes:
                image_desc = await llm.describe_image(img_bytes)
                if image_desc:
                    logging.info(
                        f"[{message.chat.id}] Vision описание: {image_desc[:120]}..."
                    )
        except Exception as e:
            logging.error(f"Ошибка vision-анализа фото: {e}")

    query_text = (
        force_search_query or clean_text or "Оцени эту картинку или поясни че на ней"
    )
    current = reply_quote_prefix + query_text
    chat_id = message.chat.id
    user_id = message.from_user.id
    history, _ = await db.run(
        Repository.get_recent_messages,
        chat_id,
        is_group=not is_private,
        before_message_id=message.message_id,
    )
    await db.run(
        Repository.save_message,
        chat_id,
        message.message_id,
        reply_to_id,
        user_id,
        sender_name,
        "user",
        current,
    )
    if not is_private:
        await db.run(Repository.analyze_chat_phrases, chat_id, raw_text)
    if not is_private:
        current = f"[{sender_name}]: {current}"
    insult = not force_search_query and is_targeted_insult(
        query_text, is_reply_to_bot, is_called_by_name
    )
    if insult and mode not in (Mode.CODE, Mode.PROMPT):
        mode = Mode.CHAT
    can_search = may_search(mode, enabled=ENABLE_WEB_SEARCH, insult=insult)
    explicit = re.match(r"(?:погугли|загугли|найди|чекни)\s+(.+)", query_text, re.I)
    search_query = (
        (force_search_query or (explicit[1] if explicit else "")) if can_search else ""
    )

    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(keep_typing(bot, chat_id, stop_typing))
    try:
        notes, kent_question = await dialogue_notes(
            db,
            message,
            query_text,
            include_kents=not is_proactive
            and not force_search_query
            and not insult
            and mode not in (Mode.CODE, Mode.PROMPT),
        )
        if image_desc:
            notes.insert(0, "На прикрепленной картинке/скриншоте: " + image_desc[:700])
        if kent_question:
            mode = Mode.DETAIL
            search_query = ""
            can_search = False
        directives = []
        if insult and mode is Mode.CHAT:
            directives.append("Это наезд на тебя: коротко осади, не отвечай буквально.")
        if is_proactive:
            directives.append("Вступи в разговор, тебя прямо не тегали.")
        system_prompt = build_system_prompt(
            mode, f"{query_text}\n{reply_quote_prefix}", *directives
        )

        async def generate(search: str = "", correction: str = ""):
            turn = build_turn(current, notes=notes, search=search)
            raw = await llm.generate(
                [*history, turn],
                is_detailed_request=mode is not Mode.CHAT,
                is_coding_request=mode is Mode.CODE,
                complete=True,
                system_prompt=system_prompt
                + ("\n\n" + correction if correction else ""),
            )
            return (*clean_response(raw, mode), raw)

        search_context = await lookup_snippets(search_query) if search_query else ""
        if force_search_query and not search_context:
            await message.reply(
                "Поиск ничего не вернул. Попробуй другой запрос или повтори позже."
            )
            return
        result, emoji, sticker, raw = await generate(search_context)
        if (
            can_search
            and not search_query
            and not is_proactive
            and not image_desc
            and not BANTER_PATTERN.search(query_text)
            and UNCERTAINTY_PATTERN.search(raw)
            and ("?" in query_text or SEARCH_TRIGGER_PATTERN.search(query_text))
        ):
            snippets = await lookup_snippets(query_text)
            if snippets:
                search_context = snippets
                result, emoji, sticker, _ = await generate(search_context)
        if not valid_output(result, mode):
            result, emoji, sticker, _ = await generate(
                search_context, "Соблюди формат и закончи ответ целиком."
            )
        if mode is Mode.CHAT and re.search(
            r"\b(?:кинь|кидай|кинуть)\s+(?:плотную|зигу|зигулю)\b", query_text, re.I
        ):
            result = "Руку свою опусти клоун\nТут не цирк братан"
        if mode is Mode.CHAT and SELF_HUMILIATION_CHECK.search(result):
            result = "Че за фимозные фантазии\nСам с ними разбирайся"
        if not valid_output(result, mode):
            sticker = False
            result = {
                Mode.CODE: "Скрипт целиком не собрался\nДавай разобьём задачу на части",
                Mode.PROMPT: "Промпт целиком не собрался\nДавай уточним теги братан",
            }.get(mode, CHAT_FALLBACK)
    finally:
        stop_typing.set()
        typing_task.cancel()
        await asyncio.gather(typing_task, return_exceptions=True)

    await send_burst_messages(
        bot, chat_id, message.message_id, result, is_detailed=mode is not Mode.CHAT
    )
    await db.run(
        Repository.save_message,
        chat_id,
        0,
        message.message_id,
        bot_user.id,
        "Саня Пониёб",
        "assistant",
        result,
    )
    if mode is Mode.CHAT and sticker:
        await send_sanya_sticker(
            bot,
            chat_id,
            emoji=emoji or (message.sticker.emoji if has_sticker else "😁"),
            db=db,
        )


async def lookup_snippets(query: str) -> str:
    results = await perform_web_search(query[:500], max_results=3)
    return "\n".join(
        f"{r.get('title', '')[:120]}: {r['body'][:400]}"
        for r in results
        if r.get("body")
    )


async def send_chat_refusal(bot: Bot, message: types.Message, text: str):
    chat_id = message.chat.id
    answer, _, _ = clean_response(text, Mode.CHAT)
    await send_burst_messages(bot, chat_id, message.message_id, answer or CHAT_FALLBACK)
