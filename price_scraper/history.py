"""SQLite snapshots and a deterministic product change engine."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from .scraper import Product


@dataclass(frozen=True)
class Change:
    kind: str
    product_key: str
    name: str
    old_price: float | None = None
    new_price: float | None = None
    old_availability: str | None = None
    new_availability: str | None = None
    url: str | None = None


def product_key(product: Product) -> str:
    """Prefer the stable URL; fall back to a normalized name."""
    url = (product.url or "").strip()
    name = " ".join(product.name.casefold().split())
    if not url and not name:
        raise ValueError("A product needs a URL or a non-empty name.")
    return f"url:{url}" if url else f"name:{name}"


def _connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY,
            scraped_at TEXT NOT NULL,
            source TEXT NOT NULL,
            product_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS snapshots (
            run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            product_key TEXT NOT NULL,
            name TEXT NOT NULL,
            price REAL,
            currency TEXT NOT NULL,
            availability TEXT,
            url TEXT,
            PRIMARY KEY (run_id, product_key)
        );
        CREATE INDEX IF NOT EXISTS idx_snapshots_key ON snapshots(product_key);
        CREATE TABLE IF NOT EXISTS exports (
            id INTEGER PRIMARY KEY,
            run_id INTEGER NOT NULL REFERENCES runs(id),
            kind TEXT NOT NULL CHECK (kind IN ('csv', 'html', 'alerts')),
            target TEXT NOT NULL,
            payload BLOB NOT NULL,
            payload_sha256 TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            byte_count INTEGER NOT NULL,
            expected_sha256 TEXT,
            state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending', 'published')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            enqueued_at INTEGER,
            last_attempt_at INTEGER,
            published_at INTEGER,
            UNIQUE(run_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_exports_pending ON exports(id) WHERE state = 'pending';
        CREATE INDEX IF NOT EXISTS idx_exports_target ON exports(target, id DESC);
        CREATE TABLE IF NOT EXISTS export_baselines (
            target TEXT PRIMARY KEY,
            payload BLOB NOT NULL,
            sha256 TEXT NOT NULL
        );
    """)
    columns = {row['name'] for row in connection.execute('PRAGMA table_info(exports)')}
    for name in ('enqueued_at', 'last_attempt_at', 'published_at'):
        if name not in columns:
            connection.execute(f'ALTER TABLE exports ADD COLUMN {name} INTEGER')
    return connection


def save_snapshot(products, database, source, scraped_at=None, *, export_options=None):
    """Store one atomic snapshot and return (run_id, changes from previous run)."""
    stamp = scraped_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    current = {}
    for product in products:
        key = product_key(product)
        if key in current:
            raise ValueError(f"Duplicate product key in one scrape: {key}")
        current[key] = product

    with closing(_connect(database)) as connection, connection:
        # Reserve the writer before reading the predecessor. A deferred
        # transaction would let competing scrapes compare against the same old
        # run even though their inserts commit one after the other.
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT id FROM runs WHERE source = ? ORDER BY id DESC LIMIT 1", (source,)
        ).fetchone()
        old = {}
        if previous:
            rows = connection.execute(
                "SELECT * FROM snapshots WHERE run_id = ?", (previous["id"],)
            ).fetchall()
            old = {row["product_key"]: dict(row) for row in rows}
        cursor = connection.execute(
            "INSERT INTO runs(scraped_at, source, product_count) VALUES (?, ?, ?)",
            (stamp, source, len(current)),
        )
        run_id = cursor.lastrowid
        connection.executemany(
            """INSERT INTO snapshots
               (run_id, product_key, name, price, currency, availability, url)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(run_id, key, p.name, p.price, p.currency, p.availability, p.url)
             for key, p in current.items()],
        )

        changes = compare_products(current, old)
        if export_options is not None:
            from .exports import enqueue_exports
            enqueue_exports(connection, run_id, list(current.values()), changes, stamp, export_options, database)
    return run_id, changes


def compare_products(current, old):
    """Compare one source's normalized current products with its predecessor."""
    changes = []
    for key in sorted(current.keys() | old.keys()):
        new, before = current.get(key), old.get(key)
        if before is None:
            changes.append(Change("added", key, new.name, new_price=new.price,
                                  new_availability=new.availability, url=new.url))
        elif new is None:
            changes.append(Change("removed", key, before["name"], old_price=before["price"],
                                  old_availability=before["availability"], url=before["url"]))
        else:
            if before["price"] != new.price:
                changes.append(Change("price_changed", key, new.name,
                                      before["price"], new.price, url=new.url))
            if before["availability"] != new.availability:
                changes.append(Change("availability_changed", key, new.name,
                                      old_availability=before["availability"],
                                      new_availability=new.availability, url=new.url))
    return changes


def list_runs(database, source=None):
    with closing(_connect(database)) as connection:
        if source is None:
            rows = connection.execute("SELECT * FROM runs ORDER BY id DESC").fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM runs WHERE source = ? ORDER BY id DESC", (source,)
            ).fetchall()
        return [dict(row) for row in rows]
