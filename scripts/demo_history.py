"""Offline two-run demo using synthetic fixture HTML."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from price_scraper.config import load_config
from price_scraper.history import save_snapshot
from price_scraper.report import write_html
from price_scraper.scraper import extract_products

root = Path(__file__).resolve().parents[1]
out = root / "outputs"
out.mkdir(exist_ok=True)
html = (root / "tests/fixtures/books_listing.html").read_text(encoding="utf-8")
config = load_config(root / "config.yaml")
first = extract_products(html, config)
second_html = html.replace("£51.77", "£49.99").replace("In stock", "Sold out", 1)
second = extract_products(second_html, config)
database = out / "demo-history.db"
if database.exists():
    database.unlink()
save_snapshot(first, database, "offline-demo", "2026-09-08T09:00:00Z")
_, changes = save_snapshot(second, database, "offline-demo", "2026-09-09T09:00:00Z")
report = write_html(changes, out / "demo-report.html", "PriceWatch · Offline demo")
print(f"{len(first)} products, {len(changes)} changes, report={report}")
