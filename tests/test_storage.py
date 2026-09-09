import csv

from price_scraper.scraper import Product
from price_scraper.storage import FIELDNAMES, write_rows


def _products():
    return [
        Product(name="A", price=51.77, currency="£", availability="In stock", url="https://x/a"),
        Product(name="B", price=None, currency="£", availability=None, url=None),
    ]


def _read(path):
    with path.open(encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def test_writes_header_and_rows(tmp_path):
    out = tmp_path / "prices.csv"
    written = write_rows(_products(), out, scraped_at="2026-05-24 06:00:00")

    assert written == 2
    rows = _read(out)
    assert list(rows[0].keys()) == FIELDNAMES
    assert rows[0]["name"] == "A"
    assert rows[0]["price"] == "51.77"
    assert rows[0]["scraped_at"] == "2026-05-24 06:00:00"
    assert rows[1]["price"] == ""  # None serialises to empty


def test_appends_without_duplicate_header(tmp_path):
    out = tmp_path / "prices.csv"
    write_rows(_products(), out, scraped_at="2026-05-24 06:00:00")
    write_rows(_products(), out, scraped_at="2026-05-25 06:00:00")

    raw = out.read_text(encoding="utf-8")
    assert raw.count("scraped_at") == 1  # header written exactly once

    rows = _read(out)
    assert len(rows) == 4
    assert {r["scraped_at"] for r in rows} == {"2026-05-24 06:00:00", "2026-05-25 06:00:00"}


def test_creates_missing_parent_dir(tmp_path):
    out = tmp_path / "nested" / "dir" / "prices.csv"
    write_rows(_products(), out, scraped_at="2026-05-24 06:00:00")
    assert out.exists()
    assert len(_read(out)) == 2
