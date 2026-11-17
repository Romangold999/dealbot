"""Ozon-only adaptive random-walk scanner.

Each search worker owns one Playwright session for its entire lifetime. Results are
returned to the async coordinator as soon as an individual search finishes.
"""
import asyncio
import logging
import queue
import re
import threading
from pathlib import Path
import sys

import db

VENDOR = Path(__file__).parent / "vendor" / "Marketplace-Parser"
sys.path.insert(0, str(VENDOR))
from parsers import ozon  # noqa: E402

log = logging.getLogger("dealbot.scanner")

_STOP = {
    "и", "в", "на", "для", "с", "по", "от", "до", "из", "под", "the", "new",
    "новый", "новая", "новое", "новые", "товар", "купить", "оригинал",
    "официальный", "доставка", "россия", "шт", "штук", "цвет", "модель",
    "артикул", "версия", "подарок", "скидка", "хит", "продаж",
}


def _number(value):
    if isinstance(value, (int, float)):
        return float(value)
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return float(digits) if digits else None


def _ozon_items(session, query: str, limit: int):
    records = ozon.search(session, "/search/", query, limit)
    found = []
    for record in records:
        price = _number(record.get("price"))
        link = record.get("url")
        if link and not link.startswith("http"):
            link = "https://www.ozon.ru" + link
        name = (record.get("name") or "").strip()
        if price and price > 0 and link and name:
            found.append({
                "marketplace": "Ozon",
                "sku": record.get("sku"),
                "name": name,
                "link": link,
                "price_value": price,
                "old_price_value": _number(record.get("oldPrice")),
                "image_url": record.get("image"),
                "source_query": query,
            })
    return found


def _search_worker(worker_id, tasks, results, stopped, limit):
    """One dedicated thread and one non-shared Playwright session per worker."""
    session = None
    try:
        session = ozon.OzonSession()
        log.info("Ozon search worker %s ready", worker_id)
        while not stopped.is_set():
            task = tasks.get()
            if task is None:
                tasks.task_done()
                break
            query, direction_id = task
            try:
                products = _ozon_items(session, query, limit)
                results.put((worker_id, query, direction_id, products, None))
            except Exception as exc:
                log.warning("Ozon search failed for '%s': %s", query, type(exc).__name__)
                results.put((worker_id, query, direction_id, [], f"{type(exc).__name__}: {exc}"))
            finally:
                tasks.task_done()
    except Exception as exc:
        results.put((worker_id, None, None, [], f"worker startup: {type(exc).__name__}: {exc}"))
    finally:
        if session is not None:
            try:
                session.shutdown()
            except Exception:
                log.exception("Could not close Ozon session for worker %s", worker_id)
        log.info("Ozon search worker %s stopped", worker_id)


def _derive_directions(products, parent_query: str, max_new: int = 24):
    """Branch from actual brands/model identifiers and distinctive title phrases."""
    candidates = []
    for product in products:
        tokens = re.findall(r"[a-zа-яё0-9]+", product.get("name", "").lower().replace("ё", "е"))
        tokens = [t for t in tokens if t not in _STOP and len(t) >= 2]
        if not tokens:
            continue

        # Model codes are especially useful exploration anchors.
        model_positions = [i for i, token in enumerate(tokens) if re.search(r"\\d", token)]
        for i in model_positions:
            if i > 0:
                candidates.append(" ".join(tokens[max(0, i - 2):i + 1]))
            candidates.append(tokens[i])

        # Brand/model and adjacent product phrases; avoid generating one-word noise.
        for i in range(min(len(tokens) - 1, 5)):
            candidates.append(" ".join(tokens[i:i + 2]))
        if len(tokens) >= 3:
            candidates.append(" ".join(tokens[:3]))

    parent = set(re.findall(r"[a-zа-яё0-9]+", parent_query.lower().replace("ё", "е")))
    unique = []
    seen = set()
    for phrase in candidates:
        phrase = " ".join(phrase.split()).strip()
        key = phrase.casefold()
        words = set(key.split())
        if len(phrase) < 3 or len(phrase) > 70 or key in seen:
            continue
        if key == parent_query.casefold() or (len(words) == 1 and not re.search(r"\\d", phrase)):
            continue
        seen.add(key)
        unique.append(phrase)
    return unique[:max_new]


async def random_walk(on_batch, workers: int = 2, limit: int = 12):
    """Run continuously until cancelled; process every completed query immediately.

    on_batch(query, products, error) may be async and should return a reward in 0..5.
    """
    workers = max(1, min(3, int(workers)))
    limit = max(3, min(30, int(limit)))
    tasks = queue.Queue(maxsize=workers * 2)
    results = queue.Queue()
    stopped = threading.Event()
    threads = [
        threading.Thread(
            target=_search_worker,
            args=(i + 1, tasks, results, stopped, limit),
            name=f"ozon-search-{i + 1}",
            daemon=True,
        )
        for i in range(workers)
    ]
    for thread in threads:
        thread.start()

    active = 0
    try:
        while True:
            # Keep all workers busy, but never enqueue an unbounded backlog.
            while active < workers:
                query = db.next_direction()
                if not query:
                    await asyncio.sleep(2)
                    continue
                tasks.put((query, active + 1))
                active += 1
                log.info("Random walk -> '%s'", query)

            worker_id, query, direction_id, products, error = await asyncio.to_thread(results.get)
            active = max(0, active - 1)

            if query is None:
                log.error("Search worker %s failed to start: %s", worker_id, error)
                await asyncio.sleep(5)
                continue

            generated = _derive_directions(products, query)
            added = db.add_directions(generated, parent_query=query, depth=1)
            if added:
                log.info("Direction '%s' generated %d new branches", query, added)

            reward = 0.0
            try:
                reward = float(await on_batch(query, products, error) or 0.0)
            except Exception:
                log.exception("Batch handler failed for direction '%s'", query)
            db.finish_direction(query, reward)

            if error:
                log.warning("Direction '%s' completed with error: %s", query, error)
            else:
                log.info("Direction '%s': %d offers; reward %.2f", query, len(products), reward)
    finally:
        stopped.set()
        for _ in threads:
            try:
                tasks.put_nowait(None)
            except queue.Full:
                pass
        await asyncio.gather(
            *(asyncio.to_thread(thread.join, 15) for thread in threads),
            return_exceptions=True,
        )


def scan_query(query: str, limit: int = 12):
    """One-shot utility for diagnostics; production uses random_walk()."""
    session = ozon.OzonSession()
    try:
        return _ozon_items(session, query, limit)
    finally:
        session.shutdown()


def fetch_product_details(product: dict) -> dict:
    """Fetch full Ozon characteristics for a candidate; never share sessions."""
    session = ozon.OzonSession()
    try:
        return ozon.details(session, product.get("link") or product.get("sku"))
    finally:
        session.shutdown()
