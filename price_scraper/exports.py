"""Transactional export intents and recoverable local artifact publication."""
from __future__ import annotations

from contextlib import closing
import csv
import hashlib
from io import StringIO
import os
from pathlib import Path
import tempfile
import time

from .history import _connect
from .storage import FIELDNAMES


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def validate_targets(database, options, protected=()):
    """Resolve target identities before a scrape and again before enqueueing."""
    kinds = {'csv': options['csv_path']}
    if options.get('report_path'):
        kinds['html'] = options['report_path']
    if options.get('rules') is not None:
        kinds['alerts'] = options.get('alert_path') or str(options['csv_path']) + '.alerts.json'
    database = Path(database).resolve()
    reserved = [database, *[Path(str(database) + suffix) for suffix in ['-wal', '-shm', '-journal']],
                *[Path(path).resolve() for path in protected if path]]
    targets = {}
    for kind, raw in kinds.items():
        original = Path(raw)
        if original.is_symlink():
            raise ValueError('Export targets must be regular file paths: ' + str(original))
        target = original.resolve()
        if target.exists() and not target.is_file():
            raise ValueError('Export target must be a regular file: ' + str(target))
        for other in [*reserved, *targets.values()]:
            if target == other or (target.exists() and other.exists() and target.samefile(other)):
                raise ValueError('Export paths must be distinct from database, inputs and other artifacts.')
        targets[kind] = target
    return targets


def _csv_payload(base: bytes, products, stamp) -> bytes:
    if base:
        rows = csv.reader(StringIO(base.decode('utf-8-sig'), newline=''), strict=True)
        if next(rows, None) != FIELDNAMES:
            raise ValueError('Existing CSV header must match the PriceWatch schema.')
        if any(len(row) != len(FIELDNAMES) for row in rows):
            raise ValueError('Existing CSV record widths must match its header.')
    buffer = StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=FIELDNAMES, lineterminator='\n')
    if not base:
        writer.writeheader()
    for product in products:
        writer.writerow({'scraped_at': stamp, **product.as_row()})
    suffix = buffer.getvalue().encode('utf-8')
    if not base:
        return b'\xef\xbb\xbf' + suffix
    return base + (b'' if base.endswith((b'\n', b'\r')) else b'\n') + suffix


def enqueue_exports(connection, run_id, products, changes, stamp, options, database):
    """Called inside the snapshot's BEGIN IMMEDIATE transaction."""
    from .alerts import detect_alerts, render_alerts
    from .report import render_html
    targets = validate_targets(database, options)
    enqueued_at = _now_ms()
    for kind, target in targets.items():
        previous = connection.execute(
            'SELECT * FROM exports WHERE target = ? ORDER BY id DESC LIMIT 1',
            (str(target),),
        ).fetchone()
        if previous and previous['kind'] != kind:
            raise ValueError('An export path is already assigned to another artifact kind.')
        base = _materialize(connection, previous) if previous else (target.read_bytes() if target.exists() else b'')
        expected = previous['sha256'] if previous else (_digest(base) if target.exists() else None)
        if kind == 'csv':
            payload = _csv_payload(base, products, stamp)
            if previous is None:
                connection.execute('INSERT INTO export_baselines VALUES (?, ?, ?)',
                                   (str(target), base, _digest(base)))
            # Persist each run's rows once; the combined CSV is a projection.
            stored = payload[len(base):]
        elif kind == 'html':
            payload = render_html(changes, options.get('title', 'PriceWatch snapshot changes')).encode('utf-8')
            stored = payload
        else:
            payload = render_alerts(detect_alerts(changes, options['rules']), generated_at=stamp).encode('utf-8')
            stored = payload
        connection.execute('''INSERT INTO exports(run_id, kind, target, payload, payload_sha256,
                              sha256, byte_count, expected_sha256, enqueued_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                           (run_id, kind, str(target), stored, _digest(stored), _digest(payload),
                            len(payload), expected, enqueued_at))


def _materialize(connection, job):
    if job['kind'] == 'csv':
        baseline = connection.execute('SELECT * FROM export_baselines WHERE target = ?', (job['target'],)).fetchone()
        if baseline is None or _digest(bytes(baseline['payload'])) != baseline['sha256']:
            raise ValueError('CSV baseline checksum failed.')
        chunks = [bytes(baseline['payload'])]
        for part in connection.execute('SELECT payload, payload_sha256 FROM exports WHERE target = ? AND id <= ? ORDER BY id',
                                       (job['target'], job['id'])):
            chunk = bytes(part['payload'])
            if _digest(chunk) != part['payload_sha256']:
                raise ValueError('Persisted export checksum failed.')
            chunks.append(chunk)
        payload = b''.join(chunks)
    else:
        payload = bytes(job['payload'])
    if _digest(payload) != job['sha256'] or len(payload) != job['byte_count']:
        raise ValueError('Persisted export checksum failed.')
    return payload


def _publish(job):
    """Publish one complete payload; the caller holds this database's writer lock."""
    target = Path(job['target'])
    if target.is_symlink():
        raise ValueError('Export target became a symbolic link: ' + str(target))
    current = _digest(target.read_bytes()) if target.exists() else None
    payload = bytes(job['payload'])
    if _digest(payload) != job['sha256']:
        raise ValueError('Persisted export checksum failed.')
    if current == job['sha256']:
        return  # Process exited after publication but before committing its receipt.
    if current is not None and current != job['expected_sha256']:
        raise ValueError('Export target changed externally; restore its expected version before recovery: ' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pricewatch-export-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def drain_exports(database, limit=1000):
    """Publish pending intents in commit order, retaining failures for retry."""
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError('Export limit must be an integer from 1 to 1000.')
    if not Path(database).is_file():
        raise ValueError('Recovery requires an existing PriceWatch database.')
    published = 0
    error = None
    with closing(_connect(database)) as connection:
        for _ in range(limit):
            with connection:
                # The lock spans selection, file publication and receipt commit.
                # Another exporter therefore observes the latest completed intent.
                connection.execute('BEGIN IMMEDIATE')
                job = connection.execute("SELECT * FROM exports WHERE state = 'pending' ORDER BY id LIMIT 1").fetchone()
                if job is None:
                    break
                try:
                    prepared = dict(job)
                    prepared['payload'] = _materialize(connection, job)
                    _publish(prepared)
                except (OSError, ValueError) as exc:
                    error = str(exc)
                    connection.execute('''UPDATE exports SET attempts = attempts + 1, last_error = ?,
                                          last_attempt_at = ? WHERE id = ?''',
                                       (error[:1000], _now_ms(), job['id']))
                else:
                    completed_at = _now_ms()
                    connection.execute('''UPDATE exports SET state = 'published', attempts = attempts + 1,
                                          last_error = NULL, last_attempt_at = ?, published_at = ?
                                          WHERE id = ?''',
                                       (completed_at, completed_at, job['id']))
                    published += 1
            if error:
                break
        pending = connection.execute("SELECT count(*) FROM exports WHERE state = 'pending'").fetchone()[0]
    return {'published': published, 'pending': pending, 'error': error}


def export_status(database, limit=100, *, now_ms=None):
    if not Path(database).is_file():
        raise ValueError('Status requires an existing PriceWatch database.')
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError('Status limit must be an integer from 1 to 1000.')
    observed_at = _now_ms() if now_ms is None else now_ms
    if not isinstance(observed_at, int) or isinstance(observed_at, bool) or observed_at < 0:
        raise ValueError('Status time must be a non-negative epoch millisecond integer.')
    with closing(_connect(database)) as connection, connection:
        connection.execute('BEGIN')
        counts = {'pending': 0, 'published': 0}
        counts.update(dict(connection.execute('SELECT state, count(*) FROM exports GROUP BY state')))
        summary = dict(connection.execute('''SELECT
                        min(enqueued_at) FILTER (WHERE state = 'pending') AS oldest_pending_at,
                        max(published_at) AS latest_published_at,
                        count(*) FILTER (WHERE state = 'pending' AND last_error IS NOT NULL) AS failed_pending,
                        count(*) FILTER (WHERE state = 'pending' AND enqueued_at > ?) AS future_pending,
                        coalesce(max(attempts) FILTER (WHERE state = 'pending'), 0) AS max_pending_attempts
                        FROM exports''', (observed_at,)).fetchone())
        oldest = summary['oldest_pending_at']
        summary['oldest_pending_age_ms'] = None if oldest is None else max(0, observed_at - oldest)
        summary['observed_at'] = observed_at
        summary['state'] = ('blocked' if summary['failed_pending'] else
                            'backlogged' if counts['pending'] else 'healthy')
        jobs = [dict(row) for row in connection.execute('''SELECT id, run_id, kind, target, sha256, state,
                        attempts, last_error, enqueued_at, last_attempt_at, published_at,
                        CASE WHEN state = 'pending' AND enqueued_at IS NOT NULL
                             THEN max(0, ? - enqueued_at) END AS pending_age_ms,
                        byte_count AS bytes, length(payload) AS stored_bytes
                        FROM exports ORDER BY state = 'pending' DESC, id LIMIT ?''',
                        (observed_at, limit))]
    return {'counts': counts, 'health': summary, 'jobs': jobs}
