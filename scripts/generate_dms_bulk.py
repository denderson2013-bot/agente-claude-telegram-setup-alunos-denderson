#!/usr/bin/env python3
"""
generate_dms_bulk.py — Geracao em massa de DMs (FASE 5)

Uso (standalone, NAO precisa restart do daemon Ademir):
    python3 scripts/generate_dms_bulk.py --workers 4 --only-missing
    python3 scripts/generate_dms_bulk.py --workers 6
    python3 scripts/generate_dms_bulk.py --dry-run    (so lista, nao gera)
    python3 scripts/generate_dms_bulk.py --limit 5    (testa com 5 leads)

Conecta no banco direto via tunnel SSH local (127.0.0.1:15432) e roda N workers
em paralelo chamando ademir.generate_dm() pra cada lead 'qualified' que ainda
nao tem prospect_dms. Cada worker abre seu proprio asyncio loop pro Claude SDK.

Equivalente ao endpoint POST /generate-all-dms do daemon Ademir, mas roda
sem precisar restart do daemon (que esta no meio de um run).
"""
from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time
from typing import Optional

# Importa do ademir.py do mesmo diretorio (db_query, generate_dm, helpers)
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ademir  # type: ignore  # noqa: E402

log = ademir.log


_state = {
    'total': 0,
    'generated': 0,
    'failed': 0,
    'last_error': None,
}
_state_lock = threading.Lock()


def _worker(worker_id: int, work_queue: 'queue.Queue', dry_run: bool):
    while True:
        try:
            lead = work_queue.get_nowait()
        except queue.Empty:
            return
        ig = lead.get('ig_username') or '?'
        try:
            if dry_run:
                log.info(f'[dry-run w{worker_id}] @{ig} (id={lead["id"]}) - SKIP geracao')
                with _state_lock:
                    _state['generated'] += 1
            else:
                dm_text = ademir.generate_dm(lead)
                if not dm_text:
                    raise RuntimeError('generate_dm vazio')
                ademir.db_query(
                    'INSERT INTO prospect_dms (lead_id, message, delivered) VALUES (%s,%s,%s)',
                    (lead['id'], dm_text, False), commit=True,
                )
                with _state_lock:
                    _state['generated'] += 1
                log.info(f'[bulk-dm w{worker_id}] DM OK @{ig} (id={lead["id"]})')
        except Exception as e:
            with _state_lock:
                _state['failed'] += 1
                _state['last_error'] = f'@{ig}: {e}'
            log.error(f'[bulk-dm w{worker_id}] falha @{ig}: {e}')
        finally:
            with _state_lock:
                done = _state['generated'] + _state['failed']
                total = _state['total']
            if done % 10 == 0 or done == total:
                log.info(
                    f'[bulk-dm] progresso {done}/{total} '
                    f'(generated={_state["generated"]} failed={_state["failed"]})'
                )
            work_queue.task_done()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=4, help='threads paralelas (default 4, max 8)')
    ap.add_argument('--only-missing', action='store_true', default=True,
                    help='pula leads que ja tem prospect_dms (default ON)')
    ap.add_argument('--all', action='store_true',
                    help='inclui leads que ja tem DMs (forca regenerar tudo)')
    ap.add_argument('--limit', type=int, default=0, help='limita N leads (0 = todos)')
    ap.add_argument('--dry-run', action='store_true', help='lista o que faria, nao gera')
    args = ap.parse_args()

    workers = max(1, min(8, args.workers))
    only_missing = not args.all  # --all desliga only_missing

    if only_missing:
        sql = (
            "SELECT l.* FROM prospect_leads l "
            "WHERE l.status = 'qualified' "
            "  AND NOT EXISTS (SELECT 1 FROM prospect_dms d WHERE d.lead_id = l.id) "
            "ORDER BY l.id"
        )
    else:
        sql = (
            "SELECT l.* FROM prospect_leads l "
            "WHERE l.status = 'qualified' "
            "ORDER BY l.id"
        )
    leads = ademir.db_query(sql, fetchall=True) or []
    if args.limit and args.limit > 0:
        leads = leads[: args.limit]
    total = len(leads)
    _state['total'] = total
    log.info(
        f'[bulk-dm] start total={total} workers={workers} only_missing={only_missing} '
        f'dry_run={args.dry_run}'
    )
    if total == 0:
        log.info('nenhum lead a processar; encerrando')
        return

    work_queue: 'queue.Queue' = queue.Queue()
    for ld in leads:
        work_queue.put(ld)

    started = time.time()
    threads = []
    for i in range(workers):
        t = threading.Thread(
            target=_worker,
            args=(i + 1, work_queue, args.dry_run),
            daemon=True,
            name=f'bulk-dm-{i+1}',
        )
        t.start()
        threads.append(t)
    for t in threads:
        t.join()

    elapsed = time.time() - started
    log.info(
        f'[bulk-dm] FIM total={total} generated={_state["generated"]} '
        f'failed={_state["failed"]} elapsed={elapsed:.1f}s'
    )
    if _state['last_error']:
        log.warning(f'ultimo erro: {_state["last_error"]}')


if __name__ == '__main__':
    main()
