"""Command-line entry point for the price scraper."""
from __future__ import annotations

import argparse
import csv
import json
import sys
import sqlite3

from .config import ConfigError, load_config
from .scraper import scrape
from .storage import write_rows
from .history import save_snapshot
from .alerts import load_rules
from .exports import drain_exports, export_status, validate_targets


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
    parser.add_argument("--alert-rules", default=None,
                        help="JSON alert policy (requires --database)")
    parser.add_argument("--alert-output", default=None,
                        help="JSON alert output path (defaults beside the CSV)")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument("--recover-exports", action="store_true",
                             help="Publish saved export intents from --database")
    maintenance.add_argument("--exports-status", action="store_true",
                             help="Print export counts, checksums and errors from --database")
    parser.add_argument("--require-healthy-exports", action="store_true",
                        help="With --exports-status, exit nonzero while exports are pending or failed")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.recover_exports or args.exports_status:
        if (not args.database or
                any([args.output, args.report, args.alert_rules, args.alert_output]) or
                args.max_pages is not None or
                (args.require_healthy_exports and not args.exports_status)):
            print("Export maintenance requires --database with its saved destinations.", file=sys.stderr)
            return 2
        try:
            result = drain_exports(args.database) if args.recover_exports else export_status(args.database)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            unhealthy = args.exports_status and args.require_healthy_exports and result['health']['state'] != 'healthy'
            return 1 if (args.recover_exports and result['pending']) or unhealthy else 0
        except (OSError, ValueError, sqlite3.Error, csv.Error) as exc:
            print(f"Export maintenance failed: {exc}", file=sys.stderr)
            return 1
    if args.require_healthy_exports:
        print("--require-healthy-exports requires --exports-status", file=sys.stderr)
        return 2
    if args.report and not args.database:
        print("--report requires --database", file=sys.stderr)
        return 2
    if args.alert_rules and not args.database:
        print("--alert-rules requires --database", file=sys.stderr)
        return 2
    if args.alert_output and not args.alert_rules:
        print("--alert-output requires --alert-rules", file=sys.stderr)
        return 2
    if args.max_pages is not None and args.max_pages < 1:
        print("--max-pages must be positive", file=sys.stderr)
        return 2

    try:
        config = load_config(args.config)
        rules = load_rules(args.alert_rules) if args.alert_rules else None
    except (ConfigError, OSError, UnicodeError, ValueError) as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2

    if args.output:
        config.csv_path = args.output
    if args.max_pages is not None:
        config.request.max_pages = args.max_pages

    export_options = {'csv_path': config.csv_path, 'report_path': args.report,
                      'rules': rules, 'alert_path': args.alert_output,
                      'title': f'{config.name} · PriceWatch changes'}
    if args.database:
        try:
            validate_targets(args.database, export_options, protected=[args.config, args.alert_rules])
        except (OSError, ValueError) as exc:
            print(f"Export configuration failed: {exc}", file=sys.stderr)
            return 2

    if not args.quiet:
        print(f"Scraping '{config.name}' from {config.start_url} ...")

    try:
        products = scrape(config)
    except Exception as exc:  # network / HTTP / parsing failures
        print(f"Scrape failed: {exc}", file=sys.stderr)
        return 1

    changes = []
    if args.database:
        try:
            run_id, changes = save_snapshot(products, args.database, config.name, export_options=export_options)
        except (OSError, ValueError, sqlite3.Error, csv.Error) as exc:
            print(f"Snapshot failed: {exc}", file=sys.stderr)
            return 1
        try:
            delivery = drain_exports(args.database)
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"Snapshot {run_id} committed; export recovery required: {exc}", file=sys.stderr)
            return 1
        if delivery['pending']:
            print(f"Snapshot {run_id} committed; {delivery['pending']} export(s) pending. "
                  f"Recovery: --database {args.database} --recover-exports. {delivery['error'] or ''}", file=sys.stderr)
            return 1
        written = len(products)
    else:
        try:
            written = write_rows(products, config.csv_path)
        except (OSError, ValueError) as exc:
            print(f"CSV output failed: {exc}", file=sys.stderr)
            return 1
    if not args.quiet:
        priced = sum(1 for p in products if p.price is not None)
        print(f"Done: {written} products ({priced} with a price) appended to {config.csv_path}")
        if args.database:
            print(f"Snapshot {run_id}: {len(changes)} change event(s)")
        if args.report:
            print(f"Report: {args.report}")
        if args.alert_rules:
            print(f"Alerts: {args.alert_output or str(config.csv_path) + '.alerts.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
