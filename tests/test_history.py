import sqlite3
from price_scraper.history import list_runs, product_key, save_snapshot
from price_scraper.report import write_html
from price_scraper.scraper import Product


def p(name, price, stock="In stock", url=None):
    return Product(name, price, "GBP", stock, url)


def test_snapshot_diff_and_source_isolation(tmp_path):
    db = tmp_path / "history.db"
    _, first = save_snapshot([p("A", 10, url="https://x/a"), p("B", 20)], db, "shop", "t1")
    assert [c.kind for c in first] == ["added", "added"]
    _, changes = save_snapshot([p("A", 12, "Sold out", "https://x/a"), p("C", 5)], db, "shop", "t2")
    assert sorted(c.kind for c in changes) == [
        "added", "availability_changed", "price_changed", "removed"
    ]
    assert [r["scraped_at"] for r in list_runs(db, "shop")] == ["t2", "t1"]
    _, other = save_snapshot([p("A", 99, url="https://x/a")], db, "other", "t3")
    assert [c.kind for c in other] == ["added"]


def test_atomic_duplicate_rejected(tmp_path):
    db = tmp_path / "history.db"
    item = p("A", 10)
    try:
        save_snapshot([item, item], db, "shop")
        assert False
    except ValueError:
        pass
    assert list_runs(db) == []


def test_product_key_and_html_escaping(tmp_path):
    assert product_key(p("  Same  Name ", 1)) == "name:same name"
    db = tmp_path / "history.db"
    _, changes = save_snapshot([p("<script>", 1, url='https://x/?q="bad"')], db, "shop")
    report = write_html(changes, tmp_path / "report.html")
    text = report.read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "&quot;bad&quot;" in text


def test_database_schema(tmp_path):
    db = tmp_path / "history.db"
    save_snapshot([p("A", None)], db, "shop")
    with sqlite3.connect(db) as con:
        assert con.execute("select product_count from runs").fetchone()[0] == 1
