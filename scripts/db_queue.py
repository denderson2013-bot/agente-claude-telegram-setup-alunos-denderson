#!/usr/bin/env python3
"""
db_queue.py — Fila SQLite local pra writes que falham no Postgres remoto.

Quando o tunnel SSH cai, todo INSERT/UPDATE/DELETE com commit=True que estourar
psycopg2.OperationalError eh empurrado pra essa fila local. Um thread drainer
roda a cada 30s tentando re-executar os pendentes contra o Postgres.

Apos sucesso → status='done' (mantido pra auditoria).
Apos falha de schema/integridade (nao-recuperavel) → status='failed'.

Uso (no ademir.py / generate_dms_bulk.py):

    from db_queue import enqueue_write, ensure_drainer_running

    # No bootstrap do daemon:
    ensure_drainer_running(connect_fn=db_conn)

    # No db_query, quando psycopg2.OperationalError cair em commit=True:
    enqueue_write(sql, params)

Schema:
    pending_writes(id INTEGER PK, sql TEXT, params TEXT JSON, created_at REAL,
                   status TEXT, last_error TEXT, attempts INTEGER, updated_at REAL)
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import threading
import time
from typing import Any, Callable, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(ROOT, 'state')
DB_PATH = os.path.join(STATE_DIR, 'pending_writes.db')
DRAIN_INTERVAL_SEC = 30
MAX_ATTEMPTS = 50  # depois disso marca como failed
NON_RETRIABLE_KEYWORDS = (
    'duplicate key', 'violates', 'syntax error', 'does not exist',
    'invalid input syntax', 'check constraint',
)

os.makedirs(STATE_DIR, exist_ok=True)

log = logging.getLogger('db_queue')

_lock = threading.Lock()
_drainer_started = False
_drainer_lock = threading.Lock()


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    return c


def _ensure_schema():
    with _lock, _conn() as c:
        c.execute('''
            CREATE TABLE IF NOT EXISTS pending_writes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sql TEXT NOT NULL,
                params TEXT,
                created_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                last_error TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                updated_at REAL
            )
        ''')
        c.execute('CREATE INDEX IF NOT EXISTS idx_pw_status ON pending_writes(status, id)')


_ensure_schema()


def enqueue_write(sql: str, params: Optional[Sequence[Any]] = None, source: str = 'ademir') -> int:
    """Empurra um write pendente. Retorna ID. Thread-safe."""
    serial = json.dumps(list(params) if params else [], default=str)
    with _lock, _conn() as c:
        cur = c.execute(
            'INSERT INTO pending_writes (sql, params, created_at, status) VALUES (?,?,?,?)',
            (sql, serial, time.time(), 'pending'),
        )
        rid = cur.lastrowid
    log.warning(f'[db_queue] enqueued #{rid} (src={source}) sql={sql[:80]!r}')
    return rid


def queue_stats() -> dict:
    with _lock, _conn() as c:
        rows = c.execute(
            'SELECT status, COUNT(*) FROM pending_writes GROUP BY status'
        ).fetchall()
    out = {'pending': 0, 'done': 0, 'failed': 0}
    for status, n in rows:
        out[status] = n
    return out


def _is_non_retriable(err: str) -> bool:
    e = (err or '').lower()
    return any(k in e for k in NON_RETRIABLE_KEYWORDS)


def _drain_once(connect_fn: Callable[[], Any]) -> tuple[int, int]:
    """Tenta drenar todos os pending. Retorna (sucesso, falha)."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, sql, params, attempts FROM pending_writes "
            "WHERE status='pending' ORDER BY id LIMIT 500"
        ).fetchall()
    if not rows:
        return (0, 0)

    # tenta abrir uma conexao com Postgres; se falhar, nem drena
    try:
        pg = connect_fn()
    except Exception as e:
        log.debug(f'[db_queue] drain skip, sem PG ainda: {e}')
        return (0, 0)

    ok = 0
    fail = 0
    try:
        for rid, sql, params_json, attempts in rows:
            try:
                params = tuple(json.loads(params_json) or [])
            except Exception:
                params = ()
            try:
                cur = pg.cursor()
                cur.execute(sql, params)
                pg.commit()
                cur.close()
                with _lock, _conn() as c:
                    c.execute(
                        "UPDATE pending_writes SET status='done', updated_at=? WHERE id=?",
                        (time.time(), rid),
                    )
                ok += 1
            except Exception as e:
                pg.rollback() if hasattr(pg, 'rollback') else None
                msg = str(e)
                attempts_new = (attempts or 0) + 1
                non_retri = _is_non_retriable(msg)
                final = non_retri or attempts_new >= MAX_ATTEMPTS
                with _lock, _conn() as c:
                    c.execute(
                        "UPDATE pending_writes SET attempts=?, last_error=?, "
                        "status=?, updated_at=? WHERE id=?",
                        (
                            attempts_new,
                            msg[:500],
                            'failed' if final else 'pending',
                            time.time(),
                            rid,
                        ),
                    )
                if final:
                    log.error(f'[db_queue] #{rid} FAILED final ({"non-retriable" if non_retri else "max-attempts"}): {msg[:120]}')
                else:
                    log.warning(f'[db_queue] #{rid} retry {attempts_new}: {msg[:120]}')
                fail += 1
                # se for OperationalError (tunnel caiu de novo), parar o drain
                if 'connection' in msg.lower() and 'refused' in msg.lower():
                    log.info('[db_queue] tunnel caiu durante drain, abortando ciclo')
                    break
    finally:
        try:
            pg.close()
        except Exception:
            pass
    if ok or fail:
        log.info(f'[db_queue] drain ciclo: ok={ok} fail={fail}')
    return (ok, fail)


def _drainer_loop(connect_fn: Callable[[], Any]):
    log.info(f'[db_queue] drainer loop start (interval={DRAIN_INTERVAL_SEC}s, db={DB_PATH})')
    while True:
        try:
            _drain_once(connect_fn)
        except Exception as e:
            log.exception(f'[db_queue] drain ciclo erro: {e}')
        time.sleep(DRAIN_INTERVAL_SEC)


def ensure_drainer_running(connect_fn: Callable[[], Any]):
    """Idempotente: starta o drainer thread uma unica vez por processo."""
    global _drainer_started
    with _drainer_lock:
        if _drainer_started:
            return
        t = threading.Thread(
            target=_drainer_loop,
            args=(connect_fn,),
            daemon=True,
            name='db-queue-drainer',
        )
        t.start()
        _drainer_started = True
        log.info('[db_queue] drainer thread iniciado')


# CLI util pra inspecao manual
if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['stats', 'drain-once', 'list'])
    args = ap.parse_args()
    if args.action == 'stats':
        print(json.dumps(queue_stats(), indent=2))
    elif args.action == 'list':
        with _conn() as c:
            for row in c.execute(
                'SELECT id, status, attempts, created_at, last_error, substr(sql,1,80) '
                'FROM pending_writes ORDER BY id DESC LIMIT 50'
            ):
                print(row)
    elif args.action == 'drain-once':
        # importa do ademir e usa db_conn
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import ademir
        ok, fail = _drain_once(ademir.db_conn)
        print(f'ok={ok} fail={fail}')
