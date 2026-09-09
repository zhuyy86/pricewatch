"""Command-line entry point for the price scraper."""
from __future__ import annotations

import argparse
import sys

from .config import ConfigError, load_config
from .scraper import scrape
from .storage import write_rows
from .history import save_snapshot
from .report import write_html


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="price-scraper",
        description="Configurable daily competitor price scraper (BeautifulSoup -> CSV).",
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml",
        help="Path to the YAML config (default: config.yaml)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Override the CSV output path from the config",
    )
    parser.add_argument(
        "--max-pages", type=int, default=None,
        help="Override the maximum number of pages to scrape",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Only print errors",
    )
    parser.add_argument("--database", default=None,
                        help="Store snapshots in SQLite and compare with the previous successful run")
    parser.add_argument("--report", default=None,
                        help="Write an HTML change report (requires --database)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.report and not args.database:
        print("--report requires --database", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.output:
        config.csv_path = args.output
    if args.max_pages is not None:
        config.request.max_pages = args.max_pages

    if not args.quiet:
        print(f"Scraping '{config.name}' from {config.start_url} ...")

    try:
        products = scrape(config)
    except Exception as exc:  # network / HTTP / parsing failures
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return 1

    written = write_rows(products, config.csv_path)
    changes = []
    if args.database:
        try:
            run_id, changes = save_snapshot(products, args.database, config.name)
            if args.report:
                write_html(changes, args.report, f"{config.name} · PriceWatch changes")
        except (OSError, ValueError) as exc:
            print(f"History failed: {exc}", file=sys.stderr)
            return 1
    if not args.quiet:
        priced = sum(1 for p in products if p.price is not None)
        print(f"Done: {written} products ({priced} with a price) appended to {config.csv_path}")
        if args.database:
            print(f"Snapshot {run_id}: {len(changes)} change event(s)")
        if args.report:
            print(f"Report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
