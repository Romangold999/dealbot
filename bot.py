import asyncio
import html
import logging
import os
import re
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)
from dotenv import load_dotenv

import db
from detector import compare_products, evaluate_peer_deal, history_anomaly, title_similarity
from scanner import fetch_product_details, random_walk

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "").strip()
RESULTS_LIMIT = max(3, min(30, int(os.getenv("RESULTS_PER_MARKETPLACE", "12"))))
HISTORY_THRESHOLD = max(0.10, min(0.80, float(os.getenv("HISTORY_DROP_THRESHOLD", "0.30"))))
MIN_DEAL_SCORE = max(65, min(95, int(os.getenv("MIN_DEAL_SCORE", "78"))))
MAX_ALERTS_PER_DAY = max(1, int(os.getenv("MAX_ALERTS_PER_DAY", "30")))
SEARCH_WORKERS = max(1, min(3, int(os.getenv("SEARCH_WORKERS", "2"))))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dealbot")

dp = Dispatcher()
scan_wakeup = asyncio.Event()


@dp.message(CommandStart())
async def start(message: Message):
    added = db.subscribe(message.chat.id)
    scan_wakeup.set()
    status = (
        "Подписка включена. Я непрерывно исследую каталог Ozon, "
        "нахожу новые направления по брендам и моделям и присылаю проверенные ценовые аномалии."
        if added else
        "Подписка уже активна. Продолжаю исследовать Ozon и присылать проверенные находки."
    )
    await message.answer(
        "Привет! " + status + "\n\n"
        "Поиск работает постоянно, без фиксированного списка категорий. "
        "Остановить уведомления: /stop."
    )


@dp.message(Command("stop"))
async def stop_alerts(message: Message):
    removed = db.unsubscribe(message.chat.id)
    await message.answer(
        "Отключил уведомления." if removed
        else "Уведомления уже отключены. Чтобы включить: /start"
    )


@dp.message()
async def ignore_all_other_messages(message: Message):
    return


def _card(product: dict, reason: str, score: int):
    safe_name = html.escape(product["name"][:180])
    price = f"{product['price_value']:,.0f}".replace(",", " ")
    text = (
        f"🔥 <b>Возможная выгодная находка · Ozon</b>\n\n"
        f"<b>{safe_name}</b>\n\n"
        f"💰 <b>{price} ₽</b>\n"
        f"📉 {html.escape(reason)}\n"
        f"🎯 Надёжность отбора: {score}/100"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть на Ozon", url=product["link"])
    ]])
    return text, keyboard


def _detail_title_matches(product: dict, details: dict) -> bool:
    detail_name = str(details.get("name") or "").strip()
    if not detail_name:
        return False
    match = compare_products(product, {"name": detail_name})
    return bool(match["comparable"] and match["confidence"] >= 0.78)


def _detail_price(details: dict) -> float | None:
    value = str(details.get("price") or "")
    match = re.search(r"(\\d[\\d\\s\\u00a0\\u202f]*)\\s*₽", value)
    if not match:
        return None
    digits = re.sub(r"\\D", "", match.group(1))
    return float(digits) if digits else None


async def _send_deal(bot: Bot, product: dict, reason: str, score: int) -> int:
    subscribers = db.all_subscribers()
    if not subscribers:
        return 0
    text, markup = _card(product, reason, score)
    delivered = 0
    for chat_id in subscribers:
        if db.deliveries_in_last_hours(chat_id, 24) >= MAX_ALERTS_PER_DAY:
            continue
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
            db.record_delivery(chat_id, product["link"])
            delivered += 1
            log.info("Sent Ozon deal to chat %s: %s", chat_id, product["name"])
            await asyncio.sleep(0.8)
        except Exception:
            log.exception("Could not send deal to chat %s", chat_id)
    if delivered:
        db.mark_alerted(product["link"], product["price_value"])
    return delivered


async def _process_batch(bot: Bot, query: str, products: list[dict], error: str | None):
    """Handle each completed Ozon search immediately while other workers continue."""
    if error:
        log.warning("Search '%s' returned error: %s", query, error)
    if not products:
        return 0.0

    sent_links = set()
    qualified = 0
    reward = 0.0

    for product in products:
        link = product.get("link")
        price = product.get("price_value")
        if not link or not price or link in sent_links:
            continue
        sent_links.add(link)

        previous = db.price_history(link, days=3)
        initial_peer = evaluate_peer_deal(product, products)
        initial_history = history_anomaly(float(price), previous, HISTORY_THRESHOLD)
        if not (initial_peer and initial_peer["score"] >= MIN_DEAL_SCORE) and not initial_history:
            continue
        if not db.should_send_alert(link, float(price)):
            continue

        # Re-open the exact product page. The current card price, not oldPrice,
        # must still support the alert before anything is sent.
        try:
            details = await asyncio.to_thread(fetch_product_details, product)
        except Exception as exc:
            log.warning("Could not verify candidate '%s': %s", product.get("name"), type(exc).__name__)
            continue
        if not _detail_title_matches(product, details):
            log.info("Candidate rejected after detail identity verification: %s", product.get("name"))
            continue

        current_price = _detail_price(details)
        if not current_price or abs(current_price - float(price)) / float(price) > 0.10:
            log.info("Candidate rejected: live price differs from search price for %s", product.get("name"))
            continue

        product["name"] = details.get("name") or product["name"]
        product["price_value"] = current_price
        product["characteristics"] = details.get("characteristics") or {}

        # Recalculate the anomaly using the verified live price.
        peer_deal = evaluate_peer_deal(product, products)
        history_reason = history_anomaly(current_price, previous, HISTORY_THRESHOLD)
        peer_qualified = bool(peer_deal and peer_deal["score"] >= MIN_DEAL_SCORE)
        if not peer_qualified and not history_reason:
            continue
        if not db.should_send_alert(link, current_price):
            continue

        db.observe_price(link, current_price)
        score = peer_deal["score"] if peer_qualified else 80
        reason = peer_deal["reason"] if peer_qualified else history_reason
        delivered = await _send_deal(bot, product, reason, score)
        if delivered:
            qualified += 1
            reward += min(2.5, score / 40)

    return min(5.0, reward)


async def _scheduler(bot: Bot):
    last_cleanup = time.monotonic()
    while True:
        if not db.all_subscribers():
            try:
                await asyncio.wait_for(scan_wakeup.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass
            scan_wakeup.clear()
            continue

        try:
            log.info("Starting continuous Ozon random walk (%d search workers)", SEARCH_WORKERS)
            await random_walk(
                lambda query, products, error: _process_batch(bot, query, products, error),
                workers=SEARCH_WORKERS,
                limit=RESULTS_LIMIT,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Random-walk scanner stopped unexpectedly; retrying")
            await asyncio.sleep(10)

        if time.monotonic() - last_cleanup >= 6 * 60 * 60:
            try:
                db.cleanup_storage()
                last_cleanup = time.monotonic()
            except Exception:
                log.exception("Storage cleanup failed")


async def main():
    if not TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в файле .env (токен выдаёт @BotFather)")

    db.init_db()
    db.cleanup_storage()

    bot = Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="Включить уведомления"),
            BotCommand(command="stop", description="Отключить уведомления"),
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
