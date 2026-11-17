import sqlite3
import random
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).with_name("deals.db")


@contextmanager
def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS searches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            query TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(chat_id, query)
        );
        CREATE TABLE IF NOT EXISTS subscribers (
            chat_id INTEGER PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS migrations (
            name TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS prices (
            link TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            price REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS prices_link_time ON prices(link, observed_at);
        CREATE TABLE IF NOT EXISTS alerts (
            link TEXT PRIMARY KEY,
            sent_price REAL NOT NULL,
            sent_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS deliveries (
            chat_id INTEGER NOT NULL,
            link TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY(chat_id, link)
        );
        CREATE INDEX IF NOT EXISTS deliveries_chat_time ON deliveries(chat_id, sent_at);
        CREATE TABLE IF NOT EXISTS search_directions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL UNIQUE,
            parent_query TEXT,
            depth INTEGER NOT NULL DEFAULT 0,
            reward REAL NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_attempt TEXT,
            last_success TEXT,
            enabled INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS directions_ready
            ON search_directions(enabled, last_attempt, reward);
        """)
        # Bootstrap only the first run. These are temporary entry points, not
        # a recurring category/search list; all later directions are discovered.
        if db.execute("SELECT COUNT(*) FROM search_directions").fetchone()[0] == 0:
            now = datetime.now(timezone.utc).isoformat()
            db.executemany(
                "INSERT OR IGNORE INTO search_directions(query, depth, created_at) VALUES (?, 0, ?)",
                [(q, now) for q in ("популярные товары", "новинки", "хиты продаж")],
            )
        migrated = db.execute(
            "INSERT OR IGNORE INTO migrations(name, applied_at) VALUES (?, ?)",
            ("searches_to_subscribers", datetime.now(timezone.utc).isoformat()),
        ).rowcount
        if migrated:
            db.execute(
                "INSERT OR IGNORE INTO subscribers(chat_id, created_at) "
                "SELECT chat_id, COALESCE(MIN(created_at), ?) FROM searches GROUP BY chat_id",
                (datetime.now(timezone.utc).isoformat(),),
            )


def subscribe(chat_id: int) -> bool:
    with connect() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id, created_at) VALUES (?, ?)",
            (chat_id, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount > 0


def unsubscribe(chat_id: int) -> bool:
    with connect() as db:
        cur = db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
        return cur.rowcount > 0


def all_subscribers() -> list[int]:
    with connect() as db:
        return [r[0] for r in db.execute("SELECT chat_id FROM subscribers ORDER BY created_at")]


def deliveries_in_last_hours(chat_id: int, hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with connect() as db:
        return db.execute(
            "SELECT COUNT(*) FROM deliveries WHERE chat_id=? AND sent_at>=?",
            (chat_id, cutoff),
        ).fetchone()[0]


def record_delivery(chat_id: int, link: str):
    with connect() as db:
        db.execute(
            "INSERT OR IGNORE INTO deliveries(chat_id, link, sent_at) VALUES (?, ?, ?)",
            (chat_id, link, datetime.now(timezone.utc).isoformat()),
        )


def add_search(chat_id: int, query: str) -> bool:
    with connect() as db:
        cur = db.execute(
            "INSERT OR IGNORE INTO searches(chat_id, query, created_at) VALUES (?, ?, ?)",
            (chat_id, query, datetime.now(timezone.utc).isoformat()),
        )
        return cur.rowcount > 0


def remove_search(chat_id: int, search_id: int) -> bool:
    with connect() as db:
        cur = db.execute("DELETE FROM searches WHERE chat_id=? AND id=?", (chat_id, search_id))
        return cur.rowcount > 0


def searches_for_chat(chat_id: int):
    with connect() as db:
        return db.execute("SELECT id, query FROM searches WHERE chat_id=? ORDER BY id", (chat_id,)).fetchall()


def all_searches():
    with connect() as db:
        return db.execute("SELECT DISTINCT query FROM searches ORDER BY query").fetchall()


def chats_for_query(query: str):
    with connect() as db:
        return [r[0] for r in db.execute("SELECT chat_id FROM searches WHERE query=?", (query,))]


def price_history(link: str, days: int = 3) -> list[float]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    with connect() as db:
        return [r[0] for r in db.execute(
            "SELECT price FROM prices WHERE link=? AND observed_at>=? ORDER BY observed_at",
            (link, cutoff),
        )]


def observe_price(link: str, price: float):
    """Return earlier 3-day samples, then append the new observation."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=3)).isoformat()
    with connect() as db:
        previous = [r[0] for r in db.execute(
            "SELECT price FROM prices WHERE link=? AND observed_at>=? ORDER BY observed_at",
            (link, cutoff),
        )]
        db.execute(
            "INSERT INTO prices(link, observed_at, price) VALUES (?, ?, ?)",
            (link, now.isoformat(), price),
        )
    return previous


def should_send_alert(link: str, price: float) -> bool:
    with connect() as db:
        row = db.execute("SELECT sent_price FROM alerts WHERE link=?", (link,)).fetchone()
        if row is None:
            return True
        # Re-alert only for a further meaningful drop.
        return price <= row["sent_price"] * 0.95


def mark_alerted(link: str, price: float):
    with connect() as db:
        db.execute(
            "INSERT INTO alerts(link, sent_price, sent_at) VALUES (?, ?, ?) "
            "ON CONFLICT(link) DO UPDATE SET sent_price=excluded.sent_price, sent_at=excluded.sent_at",
            (link, price, datetime.now(timezone.utc).isoformat()),
        )


def add_directions(queries: list[str], parent_query: str = "", depth: int = 1) -> int:
    """Persist newly discovered search directions without resetting prior learning."""
    now = datetime.now(timezone.utc).isoformat()
    cleaned = []
    for query in queries:
        query = " ".join(str(query or "").split()).strip()
        if 2 <= len(query) <= 90:
            cleaned.append((query, parent_query or None, max(0, int(depth)), now))
    if not cleaned:
        return 0
    with connect() as db:
        before = db.total_changes
        db.executemany(
            "INSERT OR IGNORE INTO search_directions(query, parent_query, depth, created_at) "
            "VALUES (?, ?, ?, ?)",
            cleaned,
        )
        return db.total_changes - before


def next_direction(cooldown_minutes: int = 90) -> str | None:
    """Weighted random selection: reward matters, but fresh directions stay discoverable."""
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(minutes=max(1, cooldown_minutes))).isoformat()
    with connect() as db:
        rows = db.execute(
            "SELECT query, reward, attempts, successes, last_attempt "
            "FROM search_directions WHERE enabled=1 "
            "AND (last_attempt IS NULL OR last_attempt<=?)",
            (cutoff,),
        ).fetchall()
        if not rows:
            rows = db.execute(
                "SELECT query, reward, attempts, successes, last_attempt "
                "FROM search_directions WHERE enabled=1 ORDER BY last_attempt ASC LIMIT 20"
            ).fetchall()
        if not rows:
            return None
        weights = []
        for row in rows:
            freshness = 1.0 if row["attempts"] == 0 else 0.0
            quality = max(0.0, min(5.0, float(row["reward"])))
            reliability = (row["successes"] + 1) / (row["attempts"] + 2)
            weights.append(0.25 + freshness + quality * reliability)
        chosen = random.choices(rows, weights=weights, k=1)[0]
        db.execute(
            "UPDATE search_directions SET last_attempt=? WHERE query=?",
            (now.isoformat(), chosen["query"]),
        )
        return chosen["query"]


def finish_direction(query: str, reward: float = 0.0):
    """Update the direction's rolling quality score after its batch is processed."""
    now = datetime.now(timezone.utc).isoformat()
    reward = max(0.0, min(5.0, float(reward or 0.0)))
    with connect() as db:
        row = db.execute(
            "SELECT reward, attempts, successes FROM search_directions WHERE query=?",
            (query,),
        ).fetchone()
        if not row:
            return
        new_reward = float(row["reward"]) * 0.75 + reward * 0.25
        db.execute(
            "UPDATE search_directions SET reward=?, attempts=attempts+1, "
            "successes=successes+?, last_success=CASE WHEN ?>0 THEN ? ELSE last_success END "
            "WHERE query=?",
            (new_reward, 1 if reward > 0 else 0, reward, now, query),
        )


def cleanup_storage():
    """Apply retention policy without deleting subscriber or search preferences."""
    now = datetime.now(timezone.utc)
    with connect() as db:
        db.execute("DELETE FROM prices WHERE observed_at < ?", ((now - timedelta(days=3)).isoformat(),))
        db.execute("DELETE FROM deliveries WHERE sent_at < ?", ((now - timedelta(days=30)).isoformat(),))
        db.execute("DELETE FROM alerts WHERE sent_at < ?", ((now - timedelta(days=30)).isoformat(),))
        db.execute("DELETE FROM search_directions WHERE enabled=0 AND created_at < ?", ((now - timedelta(days=30)).isoformat(),))
