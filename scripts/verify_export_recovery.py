"""Reproduce a bounded local export backlog and verify recovery invariants."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import sys
from tempfile import TemporaryDirectory
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from price_scraper import exports
from price_scraper.history import save_snapshot
from price_scraper.scraper import Product


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=100)
    parser.add_argument('--products', type=int, default=20)
    args = parser.parse_args(argv)
    if not 2 <= args.runs <= 1000 or not 1 <= args.products <= 100:
        parser.error('Use 2–1000 runs and 1–100 products per run.')
    started = perf_counter()
    with TemporaryDirectory(prefix='pricewatch-workload-') as directory:
        root = Path(directory)
        database = root / 'history.db'
        options = {'csv_path': root / 'prices.csv', 'report_path': root / 'changes.html',
                   'alert_path': root / 'alerts.json', 'rules': {'default': {'max_price': 100000}}}
        observed = datetime.now(timezone.utc)
        for run in range(args.runs):
            products = [Product(f'Product {index}', run + index + 1, 'USD', 'In stock',
                                f'https://fixture.invalid/{index}') for index in range(args.products)]
            save_snapshot(products, database, 'local-workload',
                          (observed + timedelta(seconds=run)).isoformat(), export_options=options)
        queued_at = perf_counter()
        expected_jobs = args.runs * 3
        assert exports.export_status(database)['counts']['pending'] == expected_jobs
        original = exports._publish
        injected = False

        def lose_receipt(job):
            nonlocal injected
            original(job)
            if not injected:
                injected = True
                raise OSError('workload: publication succeeded, receipt interrupted')

        exports._publish = lose_receipt
        try:
            interrupted = exports.drain_exports(database)
        finally:
            exports._publish = original
        assert injected and interrupted['pending'] == expected_jobs and interrupted['published'] == 0
        assert options['csv_path'].exists()
        published = 0
        recovery_passes = 0
        while True:
            result = exports.drain_exports(database)
            recovery_passes += 1
            assert result['error'] is None
            published += result['published']
            if result['pending'] == 0:
                break
        assert published == expected_jobs
        with options['csv_path'].open(encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == args.runs * args.products
        assert len({(row['scraped_at'], row['url']) for row in rows}) == len(rows)
        alerts = json.loads(options['alert_path'].read_text(encoding='utf-8'))
        assert alerts['alert_count'] == args.products
        assert options['report_path'].read_text(encoding='utf-8').count('Price changed') == args.products
        with sqlite3.connect(database) as connection:
            assert connection.execute('SELECT count(*) FROM runs').fetchone()[0] == args.runs
            assert connection.execute("SELECT count(*) FROM exports WHERE state = 'published'").fetchone()[0] == expected_jobs
            assert connection.execute('SELECT max(attempts) FROM exports').fetchone()[0] == 2
            stored_csv = connection.execute("SELECT sum(length(payload)) FROM exports WHERE kind = 'csv'").fetchone()[0]
            checksums = dict(connection.execute('SELECT kind, sha256 FROM exports WHERE run_id = ?', (args.runs,)))
        assert stored_csv == options['csv_path'].stat().st_size
        for kind, key in [('csv', 'csv_path'), ('html', 'report_path'), ('alerts', 'alert_path')]:
            assert hashlib.sha256(options[key].read_bytes()).hexdigest() == checksums[kind]
        finished = perf_counter()
        report = {'observed_at': observed.isoformat(), 'python': platform.python_version(), 'platform': sys.platform,
                  'runs': args.runs, 'products_per_run': args.products, 'export_intents': expected_jobs,
                  'published_receipts': published, 'csv_rows': len(rows), 'duplicate_csv_contributions': 0,
                  'csv_bytes': options['csv_path'].stat().st_size, 'stored_csv_payload_bytes': stored_csv,
                  'injected_failure': 'after first publication, before receipt', 'recovery_passes': recovery_passes,
                  'seed_ms': round((queued_at - started) * 1000, 2),
                  'recovery_and_verification_ms': round((finished - queued_at) * 1000, 2),
                  'scope': 'Local temporary SQLite database and files; correctness and storage exercise. Timings describe this run.'}
        print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
