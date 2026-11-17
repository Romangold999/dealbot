
import asyncio
import html
import logging
import mimetypes
import os
import re
import time
from urllib.parse import urlsplit

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from dotenv import load_dotenv

import db
from detector import (
    has_close_price_peers,
    history_anomaly,
    listed_discount_anomaly,
    peer_price_anomaly,
    title_similarity,
)
from scanner import AUTO_SEARCHES, AUTO_SEARCH_GROUPS, QUERY_GROUP, scan_queries

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
INTERVAL = max(1, int(os.getenv("SCAN_INTERVAL_MINUTES", "10")))
RESULTS_LIMIT = max(
    3,
    min(30, int(os.getenv("RESULTS_PER_MARKETPLACE", "12"))),
)
PEER_THRESHOLD = max(
    0.15,
    min(0.8, float(os.getenv("PEER_DISCOUNT_THRESHOLD", "0.40"))),
)
HISTORY_THRESHOLD = max(
    0.1,
    min(0.8, float(os.getenv("HISTORY_DROP_THRESHOLD", "0.30"))),
)
LISTED_THRESHOLD = max(
    0.3,
    min(0.9, float(os.getenv("LISTED_DISCOUNT_THRESHOLD", "0.60"))),
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dealbot")

dp = Dispatcher()
scan_wakeup = asyncio.Event()


@dp.message(CommandStart())
async def start(message: Message):
    added = db.subscribe(message.chat.id)
    scan_wakeup.set()

    status = (
        "Подписка включена. Я сам ищу большие скидки на Ozon "
        "по основным категориям и пришлю карточки сюда."
        if added
        else
        "Подписка уже активна. Я сам ищу скидки и пришлю найденные товары сюда."
    )

    await message.answer(
        "Привет! " + status + "\n\n"
        f"Проверка проходит примерно раз в {INTERVAL} мин. "
        "Остановить сообщения: /stop."
    )


@dp.message(Command("stop"))
async def stop_alerts(message: Message):
    removed = db.unsubscribe(message.chat.id)
    await message.answer(
        "Отключил уведомления."
        if removed
        else "Уведомления уже отключены. Чтобы включить: /start"
    )


@dp.message()
async def ignore_all_other_messages(message: Message):
    return


def _card(product: dict, reason: str) -> tuple[str, InlineKeyboardMarkup]:
    safe_name = html.escape(product["name"][:180])
    price = f"{product['price_value']:,.0f}".replace(",", " ")

    old_price = product.get("old_price_value")
    old_line = ""

    if old_price and old_price > product["price_value"]:
        old = f"{old_price:,.0f}".replace(",", " ")
        old_line = f"  <s>{old} ₽</s>"

    text = (
        f"🔥 <b>Выгодная находка · {html.escape(product['marketplace'])}</b>\n\n"
        f"<b>{safe_name}</b>\n\n"
        f"💰 <b>{price} ₽</b>{old_line}\n"
        f"📉 {html.escape(reason)}"
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text=f"Открыть на {product['marketplace']}",
                url=product["link"],
            )
        ]]
    )

    return text, keyboard


async def _download_photo(url: str) -> BufferedInputFile:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()

    trusted = (
        host == "ozon.ru"
        or host.endswith(".ozon.ru")
        or host == "ozone.ru"
        or host.endswith(".ozone.ru")
    )

    if parsed.scheme != "https" or not trusted:
        raise ValueError("untrusted image host")

    timeout = aiohttp.ClientTimeout(total=12)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/131.0 Safari/537.36"
        ),
        "Referer": "https://www.ozon.ru/",
    }

    async with aiohttp.ClientSession(
        timeout=timeout,
        headers=headers,
    ) as session:
        async with session.get(
            url,
            allow_redirects=True,
        ) as response:
            response.raise_for_status()

            final_host = (
                urlsplit(str(response.url)).hostname or ""
            ).lower()

            if not (
                final_host.endswith(".ozon.ru")
                or final_host.endswith(".ozone.ru")
            ):
                raise ValueError("image redirected to an untrusted host")

            mime = (
                response.headers.get("Content-Type", "")
                .split(";", 1)[0]
                .lower()
            )

            if not mime.startswith("image/"):
                raise ValueError("marketplace did not return an image")

            if response.content_length and response.content_length > 10_000_000:
                raise ValueError("image is too large")

            data = await response.read()

            if not data or len(data) > 10_000_000:
                raise ValueError("image is empty or too large")

            extension = mimetypes.guess_extension(mime) or ".jpg"

            return BufferedInputFile(
                data,
                filename=f"product{extension}",
            )


async def _scan_cycle(bot: Bot):
    subscribers = db.all_subscribers()

    if not subscribers:
        log.info("No subscribers; skipping automatic scan")
        return

    log.info(
        "Starting automatic scan for %d subscribers",
        len(subscribers),
    )

    try:
        products_by_query, errors = await asyncio.to_thread(
            scan_queries,
            AUTO_SEARCHES,
            RESULTS_LIMIT,
        )

        if errors:
            log.warning("Scan errors: %s", "; ".join(errors))

        candidates = {}
        seen_links = set()

        for query, products in products_by_query.items():
            for product in products:
                if product["link"] in seen_links:
                    continue

                seen_links.add(product["link"])

                previous = db.observe_price(
                    product["link"],
                    product["price_value"],
                )

                peer_reason = peer_price_anomaly(
                    product,
                    products,
                    PEER_THRESHOLD,
                )

                history_reason = history_anomaly(
                    product["price_value"],
                    previous,
                    HISTORY_THRESHOLD,
                )

                listed_reason = listed_discount_anomaly(
                    product["price_value"],
                    product.get("old_price_value"),
                    LISTED_THRESHOLD,
                )

                reason = peer_reason or history_reason

                if (
                    not reason
                    and listed_reason
                    and not has_close_price_peers(product, products)
                ):
                    reason = listed_reason

                if (
                    not reason
                    or not db.should_send_alert(
                        product["link"],
                        product["price_value"],
                    )
                ):
                    continue

                score = 0.0
                old_price = product.get("old_price_value")

                if old_price and old_price > product["price_value"]:
                    score = 1 - product["price_value"] / old_price

                match = re.search(r"(\d+)%", reason)

                if match:
                    score = max(
                        score,
                        int(match.group(1)) / 100,
                    )

                candidates[product["link"]] = (
                    score,
                    QUERY_GROUP.get(query, "Другое"),
                    product,
                    reason,
                )

        ordered = sorted(
            candidates.values(),
            key=lambda item: item[0],
            reverse=True,
        )

        ranked = []

        for candidate in ordered:
            product = candidate[2]

            duplicate = any(
                title_similarity(
                    product["name"],
                    kept[2]["name"],
                ) >= 0.84
                and abs(
                    product["price_value"] - kept[2]["price_value"]
                )
                <= max(
                    product["price_value"],
                    kept[2]["price_value"],
                ) * 0.07
                for kept in ranked
            )

            if not duplicate:
                ranked.append(candidate)

        # Больше нет ограничения количества товаров.
        # Отправляем все товары, прошедшие фильтрацию.
        selected = ranked

        total_sent = 0

        for chat_id in subscribers:
            for _, _, product, reason in selected:
                text, markup = _card(product, reason)
                delivered = False

                try:
                    if product.get("image_url"):
                        photo = await _download_photo(
                            product["image_url"]
                        )

                        await bot.send_photo(
                            chat_id,
                            photo=photo,
                            caption=text,
                            reply_markup=markup,
                        )
                    else:
                        await bot.send_message(
                            chat_id,
                            text + f"\n\n{html.escape(product['link'])}",
                            reply_markup=markup,
                            link_preview_options=LinkPreviewOptions(
                                is_disabled=False,
                                prefer_large_media=True,
                            ),
                        )

                    delivered = True

                except Exception:
                    if product.get("image_url"):
                        log.warning(
                            "Product photo unavailable; sending text card"
                        )

                        try:
                            await bot.send_message(
                                chat_id,
                                text + f"\n\n{html.escape(product['link'])}",
                                reply_markup=markup,
                                link_preview_options=LinkPreviewOptions(
                                    is_disabled=False,
                                    prefer_large_media=True,
                                ),
                            )
                            delivered = True

                        except Exception:
                            log.exception(
                                "Could not send deal card to chat %s",
                                chat_id,
                            )
                    else:
                        log.exception(
                            "Could not send deal card to chat %s",
                            chat_id,
                        )

                if delivered:
                    db.mark_alerted(
                        product["link"],
                        product["price_value"],
                    )

                    db.record_delivery(
                        chat_id,
                        product["link"],
                    )

                    total_sent += 1

                    log.info(
                        "Sent product to chat %s: %s",
                        chat_id,
                        product["name"],
                    )

                    # Небольшая пауза для защиты от ограничения Telegram.
                    await asyncio.sleep(1)

        log.info(
            "Qualified deals after similarity filtering: %d; sent: %d",
            len(ranked),
            total_sent,
        )

    except Exception:
        log.exception("Automatic scan cycle failed")


async def _scheduler(bot: Bot):
    while True:
        cycle_started = time.monotonic()

        await _scan_cycle(bot)

        wait_seconds = max(
            0,
            INTERVAL * 60 - (time.monotonic() - cycle_started),
        )

        if wait_seconds == 0:
            log.warning(
                "Scan took longer than %d minutes; "
                "starting the next cycle without overlap",
                INTERVAL,
            )
            continue

        try:
            await asyncio.wait_for(
                scan_wakeup.wait(),
                timeout=wait_seconds,
            )
        except asyncio.TimeoutError:
            pass

        scan_wakeup.clear()


async def main():
    if not TOKEN:
        raise SystemExit(
            "Укажи BOT_TOKEN в файле .env (токен выдаёт @BotFather)"
        )

    db.init_db()

    bot = Bot(
        TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    try:
        await bot.set_my_commands([
            BotCommand(
                command="start",
                description="Включить уведомления",
            ),
            BotCommand(
                command="stop",
                description="Отключить уведомления",
            ),
        ])
    except Exception:
        log.warning("Could not update the bot command menu")

    scheduler = asyncio.create_task(_scheduler(bot))

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.cancel()

        try:
            await scheduler
        except asyncio.CancelledError:
            pass

        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
