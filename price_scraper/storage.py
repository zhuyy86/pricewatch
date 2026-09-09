"""Append scraped products to a CSV, building a dated price history."""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .scraper import Product

FIELDNAMES = ["scraped_at", "name", "price", "currency", "availability", "url"]


def write_rows(
    products: Iterable[Product],
    csv_path: str | Path,
    scraped_at: str | None = None,
) -> int:
    """Append product rows to ``csv_path`` (writing a header if the file is new).

    Every run stamps each row with ``scraped_at``, so repeated daily runs turn
    the CSV into a price history. Returns the number of rows written.
    """
    path = Path(csv_path)
    if path.parent != Path(""):
        path.parent.mkdir(parents=True, exist_ok=True)
    stamp = scraped_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    is_new = not path.exists() or path.stat().st_size == 0
    # Write a UTF-8 BOM only when creating the file, so Excel opens currency
    # symbols (£, €, …) correctly. Appends stay plain UTF-8 (no extra BOM).
    encoding = "utf-8-sig" if is_new else "utf-8"

    count = 0
    with path.open("a", newline="", encoding=encoding) as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        if is_new:
            writer.writeheader()
        for product in products:
            writer.writerow({"scraped_at": stamp, **product.as_row()})
            count += 1
    return count
