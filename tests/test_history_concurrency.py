"""Exercise competing SQLite writers and failures after a run row is inserted."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
from threading import Event, current_thread

import pytest

from price_scraper import history
from price_scraper.scraper import Product


def product(price):
    return Product('A', price, 'GBP', 'In stock', 'https://example.test/a')


def test_competing_writers_compare_against_the_latest_committed_snapshot(tmp_path, monkeypatch):
    database = tmp_path / 'history.db'
    history.save_snapshot([product(10)], database, 'shop', 'baseline')
    original_connect = history._connect
    first_at_insert, release_first = Event(), Event()
    second_started, second_at_select = Event(), Event()

    def connect(path):
        connection = original_connect(path)

        def trace(sql):
            if 'INSERT INTO runs' in sql and current_thread().name.startswith('first'):
                first_at_insert.set()
                release_first.wait(5)
            if 'SELECT id FROM runs' in sql and current_thread().name.startswith('second'):
                second_at_select.set()
        connection.set_trace_callback(trace)
        return connection

    monkeypatch.setattr(history, '_connect', connect)

    def second_save():
        second_started.set()
        return history.save_snapshot([product(30)], database, 'shop', 'second')

    with ThreadPoolExecutor(1, thread_name_prefix='first') as first_pool, \
            ThreadPoolExecutor(1, thread_name_prefix='second') as second_pool:
        first = first_pool.submit(history.save_snapshot, [product(20)], database, 'shop', 'first')
        try:
            assert first_at_insert.wait(3)
            second = second_pool.submit(second_save)
            assert second_started.wait(3)
            # Give the competing writer the opportunity to read while writer one
            # is paused. BEGIN IMMEDIATE must prevent this stale predecessor read.
            second_at_select.wait(.3)
        finally:
            release_first.set()
        first_id, first_changes = first.result(timeout=5)
        second_id, second_changes = second.result(timeout=5)

    assert first_id < second_id
    assert [(c.old_price, c.new_price) for c in first_changes] == [(10, 20)]
    assert [(c.old_price, c.new_price) for c in second_changes] == [(20, 30)]
    assert [r['scraped_at'] for r in history.list_runs(database, 'shop')] == ['second', 'first', 'baseline']


def test_mid_snapshot_binding_failure_rolls_back_run_and_rows(tmp_path):
    database = tmp_path / 'history.db'
    history.save_snapshot([product(10)], database, 'shop', 'baseline')
    invalid = Product('B', [], 'GBP', None, 'https://example.test/b')
    with pytest.raises(sqlite3.Error):
        history.save_snapshot([product(20), invalid], database, 'shop', 'failed')

    assert [r['scraped_at'] for r in history.list_runs(database)] == ['baseline']
    with sqlite3.connect(database) as connection:
        assert connection.execute('SELECT count(*) FROM snapshots').fetchone()[0] == 1
    _, changes = history.save_snapshot([product(30)], database, 'shop', 'recovered')
    assert [(c.old_price, c.new_price) for c in changes] == [(10, 30)]
