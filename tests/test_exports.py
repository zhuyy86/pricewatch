import csv
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from price_scraper import cli, exports
from price_scraper.config import Config, Selectors
from price_scraper.history import list_runs, save_snapshot
from price_scraper.scraper import Product
from price_scraper.storage import write_rows


def product(price=10, name='A'):
    return Product(name, price, 'USD', 'In stock', 'https://x/' + name)


def options(root, alerts=False):
    value = {'csv_path': root / 'prices.csv', 'report_path': root / 'changes.html', 'title': 'Saved report'}
    if alerts:
        value.update(rules={'default': {'max_price': 20}}, alert_path=root / 'alerts.json')
    return value


def rows(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def test_snapshot_and_export_intents_commit_together_then_publish(tmp_path):
    database = tmp_path / 'history.db'
    settings = options(tmp_path, alerts=True)
    run, _ = save_snapshot([product()], database, 'shop', '2026-11-06T00:00:00Z', export_options=settings)
    assert run == 1
    assert not Path(settings['csv_path']).exists()
    state = exports.export_status(database)
    assert state['counts'] == {'pending': 3, 'published': 0}
    assert all(job['run_id'] == run and job['attempts'] == 0 for job in state['jobs'])
    assert exports.drain_exports(database) == {'published': 3, 'pending': 0, 'error': None}
    assert rows(settings['csv_path'])[0]['price'] == '10'
    alerts = json.loads(settings['alert_path'].read_text())
    assert alerts['generated_at'] == '2026-11-06T00:00:00Z' and alerts['alert_count'] == 1
    for job in exports.export_status(database)['jobs']:
        assert hashlib.sha256(Path(job['target']).read_bytes()).hexdigest() == job['sha256']


def test_render_failure_rolls_back_snapshot_and_intents(tmp_path, monkeypatch):
    from price_scraper import report
    database = tmp_path / 'history.db'

    def fail_render(*args):
        raise ValueError('injected rendering failure')

    monkeypatch.setattr(report, 'render_html', fail_render)
    with pytest.raises(ValueError, match='rendering'):
        save_snapshot([product()], database, 'shop', export_options=options(tmp_path))
    assert list_runs(database) == []
    assert exports.export_status(database)['counts'] == {'pending': 0, 'published': 0}
    assert not (tmp_path / 'prices.csv').exists()


@pytest.mark.parametrize('stage', ['fsync', 'replace'])
def test_disk_failure_keeps_old_file_and_pending_intent_for_retry(stage, tmp_path, monkeypatch):
    database = tmp_path / 'history.db'
    settings = {'csv_path': tmp_path / 'prices.csv'}
    write_rows([product(5)], settings['csv_path'], scraped_at='baseline')
    old = settings['csv_path'].read_bytes()
    save_snapshot([product(10)], database, 'shop', export_options=settings)
    original = getattr(exports.os, stage)

    def fail(*args):
        raise OSError('injected disk failure')

    monkeypatch.setattr(exports.os, stage, fail)
    failed = exports.drain_exports(database)
    assert failed['pending'] == 1 and 'disk failure' in failed['error']
    assert settings['csv_path'].read_bytes() == old
    assert not list(tmp_path.glob('.pricewatch-export-*'))
    state = exports.export_status(database)['jobs'][0]
    assert state['attempts'] == 1 and 'disk failure' in state['last_error']
    monkeypatch.setattr(exports.os, stage, original)
    assert exports.drain_exports(database)['pending'] == 0
    assert [row['price'] for row in rows(settings['csv_path'])] == ['5', '10']
    assert exports.drain_exports(database)['published'] == 0


def test_real_process_exit_after_publish_recovers_without_duplicate_csv(tmp_path):
    database = tmp_path / 'history.db'
    settings = options(tmp_path)
    save_snapshot([product(10)], database, 'shop', export_options=settings)
    script = '''import os,sys
from price_scraper import exports
original = exports._publish
def crash(job):
    original(job)
    os._exit(23)
exports._publish = crash
exports.drain_exports(sys.argv[1])
'''
    result = subprocess.run([sys.executable, '-c', script, str(database)], capture_output=True)
    assert result.returncode == 23, result.stderr
    assert len(rows(settings['csv_path'])) == 1
    assert exports.export_status(database)['counts']['pending'] == 2
    assert exports.drain_exports(database)['pending'] == 0
    assert len(rows(settings['csv_path'])) == 1
    assert settings['report_path'].exists()
    assert len(list_runs(database)) == 1


def test_later_snapshots_build_on_pending_csv_and_export_in_order(tmp_path):
    database = tmp_path / 'history.db'
    settings = options(tmp_path)
    save_snapshot([product(10)], database, 'shop', 'first', export_options=settings)
    save_snapshot([product(8)], database, 'shop', 'second', export_options=settings)
    assert exports.drain_exports(database, limit=1)['pending'] == 3
    assert [r['price'] for r in rows(settings['csv_path'])] == ['10']
    assert exports.drain_exports(database)['pending'] == 0
    assert [r['price'] for r in rows(settings['csv_path'])] == ['10', '8']
    assert 'Price changed' in settings['report_path'].read_text()


def test_two_concurrent_producers_and_exporters_preserve_all_committed_rows(tmp_path):
    database = tmp_path / 'history.db'
    settings = options(tmp_path)
    save_snapshot([product(10)], database, 'shop', 'baseline', export_options=settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda price: save_snapshot([product(price)], database, 'shop',
                                                            str(price), export_options=settings), [20, 30]))
    committed = [10] + [price for _, price in sorted((r[0], price) for r, price in zip(results, [20, 30]))]
    with ThreadPoolExecutor(max_workers=2) as pool:
        delivered = list(pool.map(lambda _: exports.drain_exports(database), range(2)))
    assert sum(x['published'] for x in delivered) == 6
    assert [int(r['price']) for r in rows(settings['csv_path'])] == committed
    assert exports.export_status(database)['counts'] == {'pending': 0, 'published': 6}


def test_external_edits_are_preserved_until_expected_version_is_restored(tmp_path):
    database = tmp_path / 'history.db'
    settings = {'csv_path': tmp_path / 'prices.csv'}
    save_snapshot([product()], database, 'shop', export_options=settings)
    exports.drain_exports(database)
    expected = settings['csv_path'].read_bytes()
    save_snapshot([product(8)], database, 'shop', export_options=settings)
    settings['csv_path'].write_bytes(b'operator edited this file')
    result = exports.drain_exports(database)
    assert result['pending'] == 1 and 'changed externally' in result['error']
    assert settings['csv_path'].read_bytes() == b'operator edited this file'
    settings['csv_path'].write_bytes(expected)
    assert exports.drain_exports(database)['pending'] == 0
    assert len(rows(settings['csv_path'])) == 2


def test_existing_csv_imports_once_and_malformed_csv_rolls_back(tmp_path):
    database = tmp_path / 'history.db'
    settings = {'csv_path': tmp_path / 'prices.csv'}
    write_rows([product(5)], settings['csv_path'], scraped_at='legacy')
    save_snapshot([product(10)], database, 'shop', export_options=settings)
    exports.drain_exports(database)
    save_snapshot([product(8)], database, 'shop', export_options=settings)
    exports.drain_exports(database)
    assert [r['price'] for r in rows(settings['csv_path'])] == ['5', '10', '8']
    assert settings['csv_path'].read_bytes().count(b'\xef\xbb\xbf') == 1
    broken = tmp_path / 'broken.csv'
    broken.write_text('wrong,header\n1,2\n')
    with pytest.raises(ValueError, match='header'):
        save_snapshot([product()], database, 'other', export_options={'csv_path': broken})
    assert len(list_runs(database)) == 2 and broken.read_text() == 'wrong,header\n1,2\n'


def test_paths_protect_database_inputs_and_other_artifacts(tmp_path):
    database = tmp_path / 'history.db'
    database.write_text('reserved')
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('reserved')
    alias = tmp_path / 'alias.csv'
    os.link(database, alias)
    for target in [database, Path(str(database) + '-wal'), cfg, alias]:
        with pytest.raises(ValueError, match='distinct'):
            exports.validate_targets(database, {'csv_path': target}, protected=[cfg])
    with pytest.raises(ValueError, match='distinct'):
        exports.validate_targets(database, {'csv_path': tmp_path / 'x', 'report_path': tmp_path / 'x'})


def test_checksum_corruption_stays_pending_and_preserves_target(tmp_path):
    database = tmp_path / 'history.db'
    settings = {'csv_path': tmp_path / 'prices.csv'}
    save_snapshot([product()], database, 'shop', export_options=settings)
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE exports SET payload = ?', (b'corrupted',))
    result = exports.drain_exports(database)
    assert 'checksum' in result['error'] and result['pending'] == 1
    assert not settings['csv_path'].exists()


def test_legacy_database_migrates_without_losing_snapshots(tmp_path):
    database = tmp_path / 'history.db'
    save_snapshot([product()], database, 'shop', 'legacy')
    with sqlite3.connect(database) as connection:
        connection.execute('DROP TABLE exports')
    assert exports.export_status(database)['counts'] == {'pending': 0, 'published': 0}
    run, changes = save_snapshot([product(8)], database, 'shop', export_options=options(tmp_path))
    assert run == 2 and changes[0].old_price == 10
    assert exports.drain_exports(database)['pending'] == 0


def test_cli_commits_before_publication_and_recovers_saved_payload_offline(tmp_path, monkeypatch, capsys):
    cfg = Config(name='offline', start_url='https://example.test', base_url='https://example.test',
                 selectors=Selectors(product='.p', name='.name', price='.price'),
                 csv_path=str(tmp_path / 'prices.csv'))
    monkeypatch.setattr(cli, 'load_config', lambda _: cfg)
    monkeypatch.setattr(cli, 'scrape', lambda _: [product()])
    database = tmp_path / 'history.db'
    original = exports._publish

    def fail(job):
        raise OSError('disk unavailable')

    monkeypatch.setattr(exports, '_publish', fail)
    assert cli.main(['--quiet', '--database', str(database)]) == 1
    assert 'Snapshot 1 committed' in capsys.readouterr().err
    assert len(list_runs(database)) == 1 and not Path(cfg.csv_path).exists()
    monkeypatch.setattr(exports, '_publish', original)

    def unexpected(*args):
        pytest.fail('Recovery must use its persisted payload')

    monkeypatch.setattr(cli, 'scrape', unexpected)
    monkeypatch.setattr(cli, 'load_config', unexpected)
    assert cli.main(['--database', str(database), '--recover-exports']) == 0
    assert json.loads(capsys.readouterr().out)['pending'] == 0
    assert cli.main(['--database', str(database), '--exports-status']) == 0
    assert json.loads(capsys.readouterr().out)['counts']['published'] == 1
    assert len(rows(Path(cfg.csv_path))) == 1


def test_cli_database_failure_preserves_csv_and_snapshot_failure_has_clean_error(tmp_path, monkeypatch, capsys):
    cfg = Config(name='offline', start_url='https://example.test', base_url='https://example.test',
                 selectors=Selectors(product='.p', name='.name', price='.price'),
                 csv_path=str(tmp_path / 'prices.csv'))
    write_rows([product(5)], cfg.csv_path)
    old = Path(cfg.csv_path).read_bytes()
    monkeypatch.setattr(cli, 'load_config', lambda _: cfg)
    monkeypatch.setattr(cli, 'scrape', lambda _: [product()])

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('injected snapshot failure')

    monkeypatch.setattr(cli, 'save_snapshot', fail)
    assert cli.main(['--database', str(tmp_path / 'history.db')]) == 1
    assert Path(cfg.csv_path).read_bytes() == old
    assert 'Snapshot failed:' in capsys.readouterr().err


def test_invalid_maintenance_and_path_alias_fail_before_scraping(tmp_path, monkeypatch):
    def unexpected(*args):
        pytest.fail('Argument validation must precede configuration and network')

    monkeypatch.setattr(cli, 'load_config', unexpected)
    assert cli.main(['--recover-exports']) == 2
    assert cli.main(['--database', 'x', '--exports-status', '--report', 'y']) == 2
    with pytest.raises(ValueError):
        exports.drain_exports(tmp_path / 'missing.db')
    assert not (tmp_path / 'missing.db').exists()


def test_csv_intents_store_incremental_rows_instead_of_repeated_history(tmp_path):
    database = tmp_path / 'history.db'
    settings = {'csv_path': tmp_path / 'prices.csv'}
    for index in range(20):
        save_snapshot([product(index)], database, 'shop', str(index), export_options=settings)
    with sqlite3.connect(database) as connection:
        stored, final = connection.execute('SELECT sum(length(payload)), max(byte_count) FROM exports').fetchone()
        assert stored == final
        assert connection.execute('SELECT length(payload) FROM export_baselines').fetchone()[0] == 0
    assert exports.drain_exports(database)['pending'] == 0
    assert len(rows(settings['csv_path'])) == 20


def test_recovery_uses_original_alert_policy_and_timestamp_after_inputs_change(tmp_path):
    database = tmp_path / 'history.db'
    settings = options(tmp_path, alerts=True)
    save_snapshot([product(10)], database, 'shop', 'saved-observation', export_options=settings)
    settings['rules']['default']['max_price'] = 0
    assert exports.drain_exports(database)['pending'] == 0
    payload = json.loads(settings['alert_path'].read_text())
    assert payload['generated_at'] == 'saved-observation' and payload['alert_count'] == 1


def test_csv_record_error_is_reported_without_publishing_a_snapshot(tmp_path, monkeypatch, capsys):
    cfg = Config(name='offline', start_url='https://example.test', base_url='https://example.test',
                 selectors=Selectors(product='.p', name='.name', price='.price'),
                 csv_path=str(tmp_path / 'prices.csv'))
    Path(cfg.csv_path).write_text('scraped_at,name,price,currency,availability,url\n"unclosed\n')
    monkeypatch.setattr(cli, 'load_config', lambda _: cfg)
    monkeypatch.setattr(cli, 'scrape', lambda _: [product()])
    database = tmp_path / 'history.db'
    assert cli.main(['--database', str(database)]) == 1
    assert 'Snapshot failed:' in capsys.readouterr().err
    assert list_runs(database) == []
