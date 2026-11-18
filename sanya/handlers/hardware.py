import re
from statistics import median_low
from urllib.parse import urlsplit

from aiogram import Router, types
from aiogram.filters import Command

from ..config import ENABLE_WEB_SEARCH
from ..database import Database
from ..lore import CONFIRMED_HARDWARE, INITIAL_KENTS_PROFILES, KENT_ALIASES
from ..repository import Repository
from ..services.queue import RequestQueue
from ..services.search import perform_web_search
from .access import resolve_target_user
from .command_args import command_args
from .media import enqueue_command

_PRICE = re.compile(
    r"(?<![\d.,])([1-9]\d{2,6}|[1-9]\d{0,2}(?:[ \u00a0\u202f]\d{3}){1,2})\s*(?:₽|руб(?:лей|ля)?\.?)(?!\w)",
    re.IGNORECASE,
)


async def cmd_hardware(message: types.Message, db: Database):
    args = command_args(message)
    if not args:
        if not message.from_user:
            return
        rows = await db.run(
            Repository.get_self_facts, message.chat.id, message.from_user.id, 8
        )
        facts = [
            row["fact_text"]
            for row in rows
            if row["fact_key"].rsplit(":", 1)[-1]
            in {"hardware_cpu", "hardware_gpu", "hardware_platform", "device"}
        ]
        await message.reply(
            "С твоих слов в этом чате:\n" + "\n".join(facts)
            if facts
            else "Ты мне ещё не хвастался своим хламом. Напиши в группе, например: «У меня RTX 3070»."
        )
        return
    key = " ".join(args.casefold().split())
    matches = {
        uid
        for uid, aliases in KENT_ALIASES.items()
        if key in {a.casefold() for a in aliases}
    }
    if args.startswith("@") or args.isascii() and args.isdecimal():
        uid, _, remaining, error = await resolve_target_user(
            db, message, args, allow_kent_aliases=True
        )
        if error or remaining:
            await message.reply(error or "Укажи одного кента.")
            return
    else:
        uid = next(iter(matches)) if len(matches) == 1 else None
    if uid not in INITIAL_KENTS_PROFILES:
        await message.reply("Не нашёл кента с таким именем в базовом лоре.")
        return
    await message.reply(
        CONFIRMED_HARDWARE.get(uid, "Подтверждённого конфига у этого кента нет.")
    )


def listing_prices(results: list[dict], model: str) -> list[tuple[int, str]]:
    prices = {}
    terms = re.findall(r"\w+", model.casefold())
    for row in results:
        url = row.get("url") or row.get("href") or ""
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"avito.ru", "www.avito.ru"}
            or parsed.netloc.lower() not in {"avito.ru", "www.avito.ru"}
            or not re.search(r"_\d{6,}/?$", parsed.path)
        ):
            continue
        title = (row.get("title") or "").casefold()
        normalized_title = re.findall(r"\w+", title)
        if not terms or not any(
            normalized_title[i : i + len(terms)] == terms
            for i in range(len(normalized_title))
        ):
            continue
        if any(
            re.search(rf"\b{variant}\b", title) and variant not in terms
            for variant in ("ti", "super", "xt", "xtx", "mobile", "laptop")
        ):
            continue
        if re.search(
            r"\b(?:куплю|обменяю|нерабоч\w*|не\s+работает|на запчасти|коробка для|водоблок|кулер для|чехол)\b",
            title,
        ):
            continue
        matches = {
            int(re.sub(r"\s+", "", match))
            for match in _PRICE.findall(title + " " + (row.get("body") or ""))
        }
        if len(matches) == 1:
            canonical = f"https://www.avito.ru{parsed.path.rstrip('/')}"
            prices[canonical] = matches.pop()
    return sorted((price, url) for url, price in prices.items())


async def cmd_price(message: types.Message, requests: RequestQueue):
    model = command_args(message)
    if not model or len(model) > 100:
        await message.reply("Напиши точную модель: /оцени RTX 3070")
        return
    await enqueue_command(message, requests, force_price_query=model)


async def send_price_estimate(message: types.Message, model: str):
    if not ENABLE_WEB_SEARCH:
        await message.reply(
            "Поиск отключён. Без свежих объявлений цену с потолка не возьму."
        )
        return
    results = await perform_web_search(f"site:avito.ru {model} б/у ₽", max_results=20)
    listings = listing_prices(results, model)
    if len(listings) < 3:
        await message.reply(
            "Мало сопоставимых объявлений Авито с видимой ценой. Уточни модель и объём памяти, вилку выдумывать не буду."
        )
        return
    low = listings[: max(3, (len(listings) + 1) // 2)]
    fast = median_low([price for price, _ in low])

    def rub(value: int) -> str:
        return f"{value:,}".replace(",", " ") + " ₽"

    links = "\n".join(f"{rub(price)} — {url}" for price, url in low[:3])
    await message.reply(
        f"По низу Авито: {rub(low[0][0])} – {rub(low[-1][0])}. Быстро слить — ориентир {rub(fast)}.\n"
        f"Сопоставимых объявлений: {len(listings)}. Это цены предложения, не сделок.\n"
        "Если нет коробки/гарантии, есть следы ремонта или карта была в майнинге — смотри тест под нагрузкой и торгуйся по состоянию.\n\n"
        + links,
        link_preview_options=types.LinkPreviewOptions(is_disabled=True),
    )


def make_router() -> Router:
    router = Router(name=__name__)
    router.message.register(cmd_hardware, Command("hardware", "железо", "сетап"))
    router.message.register(cmd_price, Command("price", "оцени", "барыга"))
    return router
