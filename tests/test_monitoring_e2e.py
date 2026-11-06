"""Two real CLI processes scrape a local HTTP shop and publish all artifacts."""
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
import sys
from threading import Thread

from price_scraper.history import list_runs


def test_two_http_scrapes_publish_consistent_history_csv_report_and_alerts(tmp_path):
    class Shop(BaseHTTPRequestHandler):
        price = 10
        requests = 0

        def do_GET(self):
            type(self).requests += 1
            data = f'<article class="p"><a class="name" href="/a">Book A</a><b class="price">${self.price}.00</b></article>'.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Shop)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}'
        cfg = tmp_path / 'shop.json'
        # JSON is a valid YAML subset and keeps path quoting deterministic.
        cfg.write_text(json.dumps({'site': {'name': 'local-shop', 'start_url': url, 'base_url': url},
            'selectors': {'product': '.p', 'name': '.name', 'price': '.price', 'url': '.name'},
            'request': {'delay_seconds': 0, 'max_pages': 1},
            'output': {'csv_path': str(tmp_path / 'prices.csv'), 'currency_symbol': '$'}}))
        rules = tmp_path / 'rules.json'
        rules.write_text('{"default":{"min_drop_percent":15}}')
        database, report, alerts = [tmp_path / name for name in ['history.db', 'report.html', 'alerts.json']]
        command = [sys.executable, '-m', 'price_scraper', '-c', str(cfg), '--database', str(database),
                   '--report', str(report), '--alert-rules', str(rules), '--alert-output', str(alerts)]
        first = subprocess.run(command, capture_output=True, text=True)
        assert first.returncode == 0, first.stderr
        assert json.loads(alerts.read_text())['alert_count'] == 0
        Shop.price = 8
        second = subprocess.run(command, capture_output=True, text=True)
        assert second.returncode == 0, second.stderr
        assert Shop.requests == 2
        assert len(list_runs(database)) == 2
        with (tmp_path / 'prices.csv').open(encoding='utf-8-sig', newline='') as handle:
            assert [row['price'] for row in csv.DictReader(handle)] == ['10.0', '8.0']
        event = json.loads(alerts.read_text())['alerts'][0]
        assert event['old_price'] == 10 and event['new_price'] == 8 and event['drop_percent'] == 20
        assert 'Price changed' in report.read_text()
        # Recovery and status still work after the live shop and configuration disappear.
        cfg.unlink()
        rules.unlink()
        status = subprocess.run([sys.executable, '-m', 'price_scraper', '--database', str(database),
                                 '--exports-status'], capture_output=True, text=True)
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)['counts'] == {'pending': 0, 'published': 6}
        recovery = subprocess.run([sys.executable, '-m', 'price_scraper', '--database', str(database),
                                   '--recover-exports'], capture_output=True, text=True)
        assert recovery.returncode == 0 and json.loads(recovery.stdout)['published'] == 0
        assert Shop.requests == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
