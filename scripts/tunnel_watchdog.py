#!/usr/bin/env python3
"""
tunnel_watchdog.py — Health-check do tunnel SSH Mac → Hetzner (porta 15432).

Roda como LaunchAgent a cada 60s (ver com.{{DONO_SLUG}}.ademir-tunnel-watchdog.plist).
Cada execucao:
  1. Faz 2 probes (`nc -z 127.0.0.1 15432`) com 3s de intervalo.
  2. Se ambas falharem, considera o tunnel CAIDO:
     - `launchctl kickstart -k gui/<uid>/com.{{DONO_SLUG}}.ademir-tunnel`
     - Marca estado em /Users/naiarodrigues/naia-agent/state/tunnel_state.json
       como {"status":"down","since":<ts>}
     - Notifica Telegram (uma vez por queda) via outbox.
  3. Se ao menos uma probe passar e o estado anterior era "down":
     - Atualiza pra {"status":"up","since":<ts>,"down_for":<segundos>}
     - Notifica Telegram que recuperou.
  4. Se status atual = up e anterior = up: nao faz nada (sem spam).

Logs em /Users/naiarodrigues/naia-agent/logs/tunnel-watchdog.log.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import sys
import time
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DIR = os.path.join(ROOT, 'logs')
STATE_DIR = os.path.join(ROOT, 'state')
STATE_FILE = os.path.join(STATE_DIR, 'tunnel_state.json')
TG_OUTBOX = '/Users/naiarodrigues/naia-bot/outbox'
TG_CHAT_ID = int(os.getenv('TG_CHAT_ID', '{{TELEGRAM_CHAT_ID}}'))
TUNNEL_LABEL = 'com.{{DONO_SLUG}}.ademir-tunnel'
TUNNEL_PLIST = f'/Users/naiarodrigues/Library/LaunchAgents/{TUNNEL_LABEL}.plist'
PROBE_HOST = '127.0.0.1'
PROBE_PORT = 15432
PROBE_TIMEOUT = 3.0
PROBE_INTERVAL = 3.0  # s entre as 2 probes

os.makedirs(LOG_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'tunnel-watchdog.log')),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger('tunnel-watchdog')


def probe_once() -> bool:
    """TCP probe rapido na porta. True = porta aberta."""
    try:
        with socket.create_connection((PROBE_HOST, PROBE_PORT), timeout=PROBE_TIMEOUT):
            return True
    except (OSError, socket.timeout):
        return False


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {'status': 'unknown', 'since': time.time()}
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {'status': 'unknown', 'since': time.time()}


def save_state(state: dict):
    tmp = STATE_FILE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def telegram_notify(text: str):
    try:
        os.makedirs(TG_OUTBOX, exist_ok=True)
        msg_id = int(time.time() * 1000)
        path = os.path.join(TG_OUTBOX, f'wd-{msg_id}.json')
        with open(path, 'w') as f:
            json.dump({'chat_id': TG_CHAT_ID, 'text': f'[Tunnel Watchdog] {text}'}, f)
        log.info(f'tg notify: {text}')
    except Exception as e:
        log.warning(f'telegram_notify falhou: {e}')


def _run(cmd: list[str], timeout: int = 15) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.returncode, (r.stdout or '').strip(), (r.stderr or '').strip())
    except Exception as e:
        return (-1, '', str(e))


def kickstart_tunnel() -> bool:
    """Religa o tunnel. Tenta kickstart primeiro; se servico nao estiver
    bootstrap'd (rc=113), faz bootstrap a partir do plist."""
    uid = os.getuid()
    domain = f'gui/{uid}'
    rc, _, err = _run(['launchctl', 'kickstart', '-k', f'{domain}/{TUNNEL_LABEL}'])
    if rc == 0:
        log.info('kickstart OK')
        return True
    # rc=113 = "Could not find service in domain": precisa bootstrap
    if rc == 113 or 'Could not find service' in err:
        log.warning(f'kickstart rc={rc}, servico nao carregado; tentando bootstrap')
        rc2, _, err2 = _run(['launchctl', 'bootstrap', domain, TUNNEL_PLIST])
        if rc2 == 0:
            log.info('bootstrap OK')
            return True
        # rc=37 = ja carregado (race com outro bootstrap); ignorar
        if rc2 == 37 or 'already loaded' in err2 or 'already bootstrapped' in err2:
            log.info('bootstrap: servico ja carregado')
            return True
        log.error(f'bootstrap rc={rc2} stderr={err2}')
        return False
    log.error(f'kickstart rc={rc} stderr={err}')
    return False


def main():
    p1 = probe_once()
    if p1:
        is_up = True
    else:
        time.sleep(PROBE_INTERVAL)
        p2 = probe_once()
        is_up = p2

    state = load_state()
    prev_status = state.get('status', 'unknown')
    now = time.time()

    if not is_up:
        # tunnel caiu (2 probes falharam)
        if prev_status != 'down':
            log.warning(f'tunnel CAIDO (prev={prev_status}); chamando kickstart')
            kickstart_tunnel()
            new_state = {'status': 'down', 'since': now, 'kickstarted': True}
            save_state(new_state)
            telegram_notify(
                'Tunnel SSH 15432 CAIU. Kickstart disparado em '
                'com.{{DONO_SLUG}}.ademir-tunnel. Daemon Ademir esta usando fila SQLite '
                'como fallback ate o tunnel voltar.'
            )
        else:
            # ja estava down, tenta kickstart de novo (idempotente)
            log.info('tunnel ainda down; novo kickstart')
            kickstart_tunnel()
        return

    # tunnel UP agora
    if prev_status == 'down':
        down_for = int(now - state.get('since', now))
        log.info(f'tunnel RECUPEROU apos {down_for}s')
        save_state({'status': 'up', 'since': now, 'down_for': down_for})
        telegram_notify(
            f'Tunnel SSH 15432 voltou. Ficou fora por {down_for}s. '
            'Fila SQLite vai drenar automatico nos proximos 30s.'
        )
    elif prev_status != 'up':
        # primeira execucao ou unknown → registra UP sem notificar
        save_state({'status': 'up', 'since': now})
        log.info('estado inicial: UP')
    # status up consistente → silencio


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        log.exception(f'watchdog erro fatal: {e}')
        sys.exit(1)
