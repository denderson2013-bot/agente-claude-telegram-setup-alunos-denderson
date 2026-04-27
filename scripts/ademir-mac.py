#!/usr/bin/env python3
"""
Ademir - Daemon de Prospeccao Ativa Instagram

Roda no Mac Mini. Usa Tandem Browser (localhost:8765) para navegar Instagram,
descobre leads (seguidores meus, comentadores em posts meus, comentadores em
posts de perfis-alvo), analisa cada perfil, gera DM personalizada via Claude,
envia via Tandem (com jitter), faz handoff para o Clone do {{DONO}}.

Modos:
- DRY_RUN=true (default): descobre, analisa, gera DM, MAS NAO ENVIA. Tudo salvo no banco.
- DRY_RUN=false: envia DMs reais (limite 30/dia, jitter 8-15min, pausa 22h-8h BRT)

Endpoints HTTP locais:
- POST /run-now   -> dispara um run agora (usado pelo dash)
- GET  /health    -> status

Banco Postgres remoto via SSH tunnel local (porta 15432 -> Hetzner 5432).
"""

import os
import sys
import json
import time
import random
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import asyncio
import psycopg2
from psycopg2.extras import RealDictCursor, Json
import httpx
from anthropic import Anthropic
from fastapi import FastAPI, Request, HTTPException
import uvicorn

# Fila local SQLite pra writes que falham quando o tunnel SSH cai.
# Carrega com fallback (se db_queue.py nao existir, daemon roda normal sem fila).
try:
    HERE = os.path.dirname(os.path.abspath(__file__))
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import db_queue  # type: ignore
    DB_QUEUE_AVAILABLE = True
except Exception as _e:
    DB_QUEUE_AVAILABLE = False

# Claude Agent SDK (sem API key, usa OAuth do Chefe via claude CLI)
try:
    from claude_agent_sdk import query as claude_query, ClaudeAgentOptions, AssistantMessage, TextBlock
    CLAUDE_SDK_AVAILABLE = True
except Exception as _e:
    CLAUDE_SDK_AVAILABLE = False

# HikerAPI (FASE 3: discovery + analyze via SaaS, substitui Tandem nesses fluxos)
try:
    import hikerapi
    HIKERAPI_AVAILABLE = True
except Exception as _e:
    HIKERAPI_AVAILABLE = False

# ── Config ──
DRY_RUN = os.getenv('ADEMIR_DRY_RUN', 'true').lower() == 'true'
DAILY_DM_LIMIT = int(os.getenv('ADEMIR_DAILY_DM_LIMIT', '20'))
ACTIVE_START_HOUR = int(os.getenv('ADEMIR_ACTIVE_START', '9'))
ACTIVE_END_HOUR = int(os.getenv('ADEMIR_ACTIVE_END', '18'))
DAEMON_PORT = int(os.getenv('ADEMIR_PORT', '9100'))
DAEMON_TOKEN = os.getenv('ADEMIR_TOKEN', '{{ADEMIR_TOKEN}}')
# Pausa entre leads dentro do mesmo run (com variacao +-2min)
DELAY_BETWEEN_LEADS_SEC = int(os.getenv('ADEMIR_DELAY_BETWEEN_LEADS_SEC', '1200'))
DELAY_JITTER_SEC = int(os.getenv('ADEMIR_DELAY_JITTER_SEC', '120'))

TANDEM_URL = os.getenv('TANDEM_URL', 'http://localhost:8765')
TANDEM_TOKEN = os.getenv('TANDEM_TOKEN', '{{TANDEM_TOKEN}}')

# FASE 3: HikerAPI pra discovery e analyze (read), Tandem so pra envio (write)
HIKERAPI_KEY = os.getenv('HIKERAPI_KEY', '')
HIKERAPI_BASE = 'https://api.hikerapi.com'

# Postgres via SSH tunnel local (autossh -> Hetzner:5432)
PG_HOST = os.getenv('PG_HOST', '127.0.0.1')
PG_PORT = int(os.getenv('PG_PORT', '15432'))
PG_USER = os.getenv('PG_USER', 'n8n')
PG_PASS = os.getenv('PG_PASS', '{{POSTGRES_PASSWORD}}')
PG_DB = os.getenv('PG_DB', 'naia_memory')

# Claude
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')
CLAUDE_OAUTH_TOKEN = os.getenv('CLAUDE_CODE_OAUTH_TOKEN', '')

# Telegram (alertas)
TG_OUTBOX_DIR = os.path.expanduser('~/naia-bot/outbox')
TG_CHAT_ID = int(os.getenv('TG_CHAT_ID', '{{TELEGRAM_CHAT_ID}}'))

# Logging
LOG_DIR = os.path.expanduser('~/naia-agent/logs')
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'ademir.log')),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger('ademir')


# ── Helpers ──
def db_conn():
    return psycopg2.connect(
        host=PG_HOST, port=PG_PORT, user=PG_USER, password=PG_PASS, dbname=PG_DB
    )


def db_query(sql, params=None, fetchone=False, fetchall=False, commit=False):
    """
    Executa query no Postgres remoto. Quando o tunnel SSH cai (OperationalError):
    - SELECTs (fetchone/fetchall): re-levanta a excecao (caller decide).
    - Writes com commit=True: empurra pra fila SQLite local (db_queue) e retorna None.
      O drainer dropa de volta no PG quando o tunnel voltar.
    """
    try:
        conn = db_conn()
    except psycopg2.OperationalError as e:
        if commit and not (fetchone or fetchall) and DB_QUEUE_AVAILABLE:
            db_queue.enqueue_write(sql, params, source='ademir.db_query')
            log.warning(f'[db_query] PG indisponivel ({e}); enfileirado em pending_writes.db')
            return None
        raise

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        try:
            cur.execute(sql, params or ())
        except psycopg2.OperationalError as e:
            if commit and not (fetchone or fetchall) and DB_QUEUE_AVAILABLE:
                try:
                    cur.close()
                except Exception:
                    pass
                db_queue.enqueue_write(sql, params, source='ademir.db_query')
                log.warning(f'[db_query] PG caiu durante execute ({e}); enfileirado')
                return None
            raise
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        if commit:
            try:
                conn.commit()
            except psycopg2.OperationalError as e:
                if DB_QUEUE_AVAILABLE:
                    db_queue.enqueue_write(sql, params, source='ademir.db_query.commit')
                    log.warning(f'[db_query] commit falhou ({e}); enfileirado')
                    cur.close()
                    return None
                raise
        cur.close()
        return result
    finally:
        try:
            conn.close()
        except Exception:
            pass


def telegram_alert(text: str):
    try:
        os.makedirs(TG_OUTBOX_DIR, exist_ok=True)
        msg_id = int(time.time() * 1000)
        path = os.path.join(TG_OUTBOX_DIR, f'{msg_id}.json')
        with open(path, 'w') as f:
            json.dump({'chat_id': TG_CHAT_ID, 'text': f'[Ademir] {text}'}, f)
        log.info(f'Telegram alert sent: {text[:80]}')
    except Exception as e:
        log.error(f'telegram_alert failed: {e}')


def tandem_request(method: str, path: str, body: Optional[dict] = None, timeout: float = 60.0):
    headers = {'Authorization': f'Bearer {TANDEM_TOKEN}', 'Content-Type': 'application/json'}
    url = f'{TANDEM_URL}{path}'
    with httpx.Client(timeout=timeout) as c:
        if method == 'GET':
            r = c.get(url, headers=headers)
        else:
            r = c.post(url, headers=headers, json=body or {})
        return r


def tandem_navigate(url: str):
    log.info(f'Navigate: {url}')
    r = tandem_request('POST', '/navigate', {'url': url}, timeout=90)
    time.sleep(6)  # mais tempo pra IG hydratar JS
    return r.json() if r.status_code == 200 else None


def tandem_status() -> Optional[dict]:
    """Retorna o status atual do Tandem: url, title, loading, ready, etc."""
    try:
        r = tandem_request('GET', '/status', timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f'tandem_status error: {e}')
    return None


def tandem_wait_for_url(target_url: str, timeout_sec: int = 60) -> bool:
    """CORRECAO A: aguarda ate o Tandem confirmar via /status que url=target E loading=false.
    Polling 1s, timeout configuravel (default 60s desde fase 2.10).
    Retorna True se url bate, False se timeout/erro.
    Comparacao tolerante: ignora trailing slash e querystring.
    """
    import re
    def _norm(u: str) -> str:
        if not u:
            return ''
        u = u.split('?')[0].split('#')[0]
        return u.rstrip('/').lower()
    want = _norm(target_url)
    deadline = time.time() + timeout_sec
    last = None
    while time.time() < deadline:
        st = tandem_status()
        if st:
            last = st
            cur = _norm(st.get('url') or '')
            loading = bool(st.get('loading'))
            if not loading and (cur == want or cur.startswith(want)):
                return True
        time.sleep(1)
    log.warning(f'tandem_wait_for_url TIMEOUT: want={want} last={last}')
    return False


def tandem_snapshot():
    r = tandem_request('GET', '/snapshot', timeout=45)
    if r.status_code != 200:
        return None
    return r.json()


def tandem_page_html(retries: int = 3) -> str:
    """Pega o HTML. Tenta varias vezes pq Tandem pode retornar HTML vazio se tab ainda hydratando."""
    for i in range(retries):
        try:
            r = tandem_request('GET', '/page-html', timeout=45)
            if r.status_code == 200:
                t = r.text
                if len(t) > 5000:
                    return t
                # HTML muito pequeno, espera e tenta de novo
                time.sleep(2)
        except Exception as e:
            log.warning(f'tandem_page_html error: {e}')
            time.sleep(2)
    # ultima tentativa, devolve o que tiver
    try:
        r = tandem_request('GET', '/page-html', timeout=45)
        return r.text if r.status_code == 200 else ''
    except Exception:
        return ''


def tandem_scroll(direction: str = 'down', amount: int = 600):
    return tandem_request('POST', '/scroll', {'direction': direction, 'amount': amount}, timeout=20)


def tandem_click(selector: str):
    return tandem_request('POST', '/click', {'selector': selector}, timeout=20)


def tandem_type(selector: str, text: str):
    return tandem_request('POST', '/type', {'selector': selector, 'text': text}, timeout=20)


def tandem_execute_js(code: str):
    """Executa JS na pagina ativa. Use IIFE pra retornar valor.
    Retorna dict {ok: bool, result: any} ou None em caso de erro de transporte.
    """
    try:
        r = tandem_request('POST', '/execute-js', {'code': code}, timeout=30)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f'tandem_execute_js error: {e}')
    return None


def tandem_find_click(by: str, value: str, exact: bool = False):
    """Click via locator amigavel (by/value).
    Estrategias suportadas pelo Tandem: text, role, label, etc.
    Retorna dict de resposta ou None.
    """
    body = {'by': by, 'value': value, 'exact': exact}
    try:
        r = tandem_request('POST', '/find/click', body, timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        log.warning(f'tandem_find_click error: {e}')
    return None


# ── HikerAPI Client (FASE 3) ──
_hiker_client = None

def hiker_client():
    """Cliente singleton do HikerAPI. Usa env HIKERAPI_KEY."""
    global _hiker_client
    if _hiker_client is not None:
        return _hiker_client
    if not HIKERAPI_AVAILABLE:
        raise RuntimeError('hikerapi lib nao instalada. pip install hikerapi')
    if not HIKERAPI_KEY:
        raise RuntimeError('HIKERAPI_KEY nao configurada no env')
    _hiker_client = hikerapi.Client(token=HIKERAPI_KEY)
    return _hiker_client


def hiker_balance() -> Optional[dict]:
    """GET /sys/balance via httpx (lib nao expoe esse endpoint diretamente)."""
    try:
        with httpx.Client(timeout=15) as c:
            r = c.get(f'{HIKERAPI_BASE}/sys/balance', headers={'x-access-key': HIKERAPI_KEY})
            if r.status_code == 200:
                return r.json()
    except Exception as e:
        log.warning(f'hiker_balance error: {e}')
    return None


def hiker_user_by_username(username: str) -> Optional[dict]:
    """user_by_username_v2: retorna {user: {pk, username, biography, follower_count, ...}}.
    Devolve direto o dict 'user' achatado, ou None em erro."""
    try:
        c = hiker_client()
        resp = c.user_by_username_v2(username)
        if isinstance(resp, dict):
            u = resp.get('user') or resp.get('data') or {}
            if u and (u.get('username') or u.get('pk')):
                return u
        log.warning(f'hiker_user_by_username @{username}: resposta vazia/sem user')
    except Exception as e:
        log.error(f'hiker_user_by_username @{username} error: {e}')
    return None


def hiker_user_followers_page(user_id: str, max_id: Optional[str] = None) -> tuple:
    """user_followers_chunk_gql: retorna (lista_de_users, next_cursor_or_None).
    Usa o endpoint GraphQL pq pagina bem (v1 nao retorna cursor confiavel).
    Cada user tem: pk, username, full_name, profile_pic_url, is_private, is_verified."""
    try:
        c = hiker_client()
        resp = c.user_followers_chunk_gql(user_id=str(user_id), end_cursor=max_id)
        if isinstance(resp, list) and len(resp) >= 2:
            users, cursor = resp[0], resp[1]
            return (users or [], cursor)
    except Exception as e:
        log.error(f'hiker_user_followers_page user_id={user_id} error: {e}')
    return ([], None)


def hiker_user_medias(user_id: str, count: int = 12) -> list:
    """user_medias_chunk_v1: retorna lista dos N medias mais recentes do user.
    Cada media tem: pk, code, caption_text, like_count, comment_count, taken_at, media_type."""
    try:
        c = hiker_client()
        resp = c.user_medias_chunk_v1(user_id=str(user_id))
        if isinstance(resp, list) and len(resp) >= 1:
            medias = resp[0] or []
            return medias[:count]
    except Exception as e:
        log.error(f'hiker_user_medias user_id={user_id} error: {e}')
    return []


def hiker_media_commenters(media_pk: str, max_pages: int = 2) -> list:
    """media_comments_chunk_v1 paginado: retorna lista de dicts user (deduplicada).
    Cada user vem do campo .user de cada comment, com is_verified incluido."""
    seen = set()
    out = []
    cursor = None
    try:
        c = hiker_client()
        for _ in range(max_pages):
            kwargs = {'id': str(media_pk)}
            if cursor:
                kwargs['min_id'] = cursor
            resp = c.media_comments_chunk_v1(**kwargs)
            if not isinstance(resp, list) or len(resp) < 1:
                break
            comments = resp[0] or []
            cursor = resp[1] if len(resp) > 1 else None
            for com in comments:
                u = com.get('user') or {}
                uname = (u.get('username') or '').lower()
                if not uname or uname in seen:
                    continue
                seen.add(uname)
                out.append(u)
            if not cursor:
                break
    except Exception as e:
        log.error(f'hiker_media_commenters media_pk={media_pk} error: {e}')
    return out


def now_brt():
    return datetime.now(timezone(timedelta(hours=-3)))


def is_within_active_hours():
    """Janela ativa configuravel via env (default 9-18h BRT)."""
    h = now_brt().hour
    return ACTIVE_START_HOUR <= h < ACTIVE_END_HOUR


def dms_sent_today():
    row = db_query(
        "SELECT COUNT(*) AS c FROM prospect_dms WHERE sent_at::date = (NOW() AT TIME ZONE 'America/Sao_Paulo')::date AND delivered = true",
        fetchone=True,
    )
    return (row or {}).get('c', 0)


# ── Claude Client ──
def claude_client():
    """Usa OAuth token ou ANTHROPIC_API_KEY."""
    if ANTHROPIC_API_KEY:
        return Anthropic(api_key=ANTHROPIC_API_KEY)
    if CLAUDE_OAUTH_TOKEN:
        # OAuth token funciona como auth_token pro SDK
        return Anthropic(auth_token=CLAUDE_OAUTH_TOKEN)
    raise RuntimeError('Nenhum ANTHROPIC_API_KEY ou CLAUDE_CODE_OAUTH_TOKEN configurado')


# ── Discovery ──
def parse_usernames_from_html(html: str) -> list:
    """Tira @usernames do HTML do Instagram. HTML tem href='/username/' real."""
    import re
    found = set()
    for m in re.finditer(r'href="/([a-zA-Z0-9._]{2,30})/"', html):
        u = m.group(1)
        if u.lower() not in (
            'explore', 'reels', 'stories', 'p', 'tv', 'accounts', 'direct',
            'web', 'about', 'press', 'api', 'developer', 'jobs', 'privacy', 'terms',
            'data', 'browsing', 'help', 'fb', 'channel', 'shop', 'create', 'reel',
            'igtv', 'news',
        ) and not u.startswith('_'):
            found.add(u)
    # Tambem captura @user em texto
    for m in re.finditer(r'@([a-zA-Z0-9._]{2,30})\b', html):
        found.add(m.group(1))
    return list(found)


def parse_post_ids_from_html(html: str) -> list:
    """Captura IDs de posts /USERNAME/p/X/ e /USERNAME/reel/X/ do HTML."""
    import re
    found = set()
    # /USERNAME/p/X/
    for m in re.finditer(r'/[a-zA-Z0-9._]+/p/([A-Za-z0-9_-]+)/?', html):
        found.add(('p', m.group(1)))
    # /USERNAME/reel/X/
    for m in re.finditer(r'/[a-zA-Z0-9._]+/reel/([A-Za-z0-9_-]+)/?', html):
        found.add(('reel', m.group(1)))
    # Tambem o formato /p/X/ direto
    for m in re.finditer(r'href="/p/([A-Za-z0-9_-]+)/"', html):
        found.add(('p', m.group(1)))
    return list(found)


def parse_verified_followers_from_html(html: str, owner_username: str) -> list:
    """FASE 2.10: parseia HTML do modal de followers e retorna SO usernames com badge verificado.

    Estrategia: o badge eh um SVG/spam com aria-label="Verificado". Pra cada ocorrencia,
    olhamos para tras no HTML e pegamos o ULTIMO link de perfil (href="/USERNAME/") antes
    do badge, dentro de uma janela razoavel (<=15k chars). Esse padrao bate porque o IG
    renderiza cada item como <a href="/user/">...<span aria-label="Verificado">.

    Filtra:
    - owner_username (perfil base, badge dele aparece no header da pagina)
    - usernames com underscore inicial (geralmente reservados/sistema)
    - paths reservados do IG
    """
    import re
    reserved = {
        'explore', 'reels', 'stories', 'p', 'tv', 'accounts', 'direct',
        'web', 'about', 'press', 'api', 'developer', 'jobs', 'privacy', 'terms',
        'data', 'browsing', 'help', 'fb', 'channel', 'shop', 'create', 'reel',
        'igtv', 'news', 'popular',
    }
    found = []
    for badge_match in re.finditer(r'aria-label="Verificado"', html):
        # janela 15k chars antes do badge
        start = max(0, badge_match.start() - 15000)
        snippet_before = html[start:badge_match.start()]
        # pega TODOS os hrefs e fica com o ultimo
        hrefs = re.findall(r'href="/([a-zA-Z0-9._]{2,30})/"', snippet_before)
        if not hrefs:
            continue
        candidate = hrefs[-1].lower()
        if candidate in reserved:
            continue
        if candidate == owner_username.lower():
            continue
        if candidate.startswith('_'):
            continue
        found.append(candidate)
    # dedupe mantendo ordem
    seen = set()
    out = []
    for u in found:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def discover_seguidor_verificado(target: dict) -> list:
    """[DEPRECATED FASE 3] Use discover_seguidor_verificado_via_hiker.
    Mantido por compatibilidade caso HikerAPI falhe; nao chamar diretamente."""
    log.warning('discover_seguidor_verificado (Tandem) DEPRECATED, use discover_seguidor_verificado_via_hiker')
    return discover_seguidor_verificado_via_hiker(target)


def discover_seguidor_verificado_via_hiker(target: dict) -> list:
    """FASE 3: descobre seguidores VERIFICADOS via HikerAPI (sem Tandem).

    Fluxo:
    1. Resolve user_id do target via user_by_username_v2
    2. Pagina followers via user_followers_chunk_v1 (50 por pagina, max ENV pages)
    3. Filtra is_verified=true
    4. Dedupe contra prospect_leads e retorna usernames novos
    """
    target_user = target['username']
    max_pages = int(os.getenv('ADEMIR_HIKER_FOLLOWERS_PAGES', '4'))  # 4*50 = 200
    log.info(f'[hiker] discover_seguidor_verificado @{target_user} (max {max_pages} paginas)')

    base = hiker_user_by_username(target_user)
    if not base:
        log.warning(f'[hiker] target @{target_user} nao encontrado')
        return []
    user_id = str(base.get('pk') or base.get('id') or '')
    if not user_id:
        log.warning(f'[hiker] target @{target_user} sem pk')
        return []

    seen_verified: list = []
    seen_set = set()
    cursor = None
    for page in range(max_pages):
        users, cursor = hiker_user_followers_page(user_id, max_id=cursor)
        if not users:
            log.info(f'[hiker]  pagina {page+1}: vazia, encerrando')
            break
        page_verifs = 0
        for u in users:
            if not u.get('is_verified'):
                continue
            uname = (u.get('username') or '').lower()
            if not uname or uname in seen_set:
                continue
            seen_set.add(uname)
            seen_verified.append(uname)
            page_verifs += 1
        log.info(f'[hiker]  pagina {page+1}: {len(users)} users, {page_verifs} verificados novos | total={len(seen_verified)}')
        if not cursor:
            log.info('[hiker]  sem cursor, encerrando paginacao')
            break

    # Dedupe contra prospect_leads
    if not seen_verified:
        return []
    rows = db_query(
        'SELECT ig_username FROM prospect_leads WHERE ig_username = ANY(%s)',
        (seen_verified,), fetchall=True,
    ) or []
    existing = set(r['ig_username'] for r in rows)
    novos = [u for u in seen_verified if u not in existing]
    log.info(f'[hiker] discover_seguidor_verificado: verificados={len(seen_verified)} | novos={len(novos)}')
    return novos


def discover_commenters_via_hiker(target: dict, max_posts: int = 3, max_pages_per_post: int = 2) -> list:
    """FASE 3: descobre comentadores nos ultimos N medias do target via HikerAPI.

    Aplica em sources comentou_em_mim e comentou_em_alvo. Devolve usernames novos
    deduplicados contra prospect_leads.
    """
    target_user = target['username']
    log.info(f'[hiker] discover_commenters @{target_user} (max {max_posts} posts, {max_pages_per_post} pgs/post)')

    base = hiker_user_by_username(target_user)
    if not base:
        log.warning(f'[hiker] target @{target_user} nao encontrado')
        return []
    user_id = str(base.get('pk') or base.get('id') or '')
    medias = hiker_user_medias(user_id, count=max_posts)
    if not medias:
        log.warning(f'[hiker] @{target_user} sem medias retornadas')
        return []
    log.info(f'[hiker]  {len(medias)} medias coletadas')

    seen = set()
    out_usernames = []
    for m in medias:
        mid = m.get('pk') or m.get('id')
        if not mid:
            continue
        commenters = hiker_media_commenters(str(mid), max_pages=max_pages_per_post)
        novos_post = 0
        for u in commenters:
            uname = (u.get('username') or '').lower()
            if not uname or uname in seen:
                continue
            seen.add(uname)
            out_usernames.append(uname)
            novos_post += 1
        log.info(f'[hiker]  media pk={mid}: {len(commenters)} commenters, {novos_post} novos')

    if not out_usernames:
        return []
    rows = db_query(
        'SELECT ig_username FROM prospect_leads WHERE ig_username = ANY(%s)',
        (out_usernames,), fetchall=True,
    ) or []
    existing = set(r['ig_username'] for r in rows)
    novos = [u for u in out_usernames if u not in existing]
    log.info(f'[hiker] discover_commenters: total={len(out_usernames)} | novos={len(novos)}')
    return novos


def discover_for_target(target: dict) -> list:
    """FASE 3: descobre usernames novos via HikerAPI (Tandem nao eh mais usado em discovery).

    Sources suportados:
    - seguidor_verificado: pagina followers do target, filtra is_verified=true
    - meu_seguidor: idem, mas sem filtro de verified (descontinuado, mantido pra compat)
    - comentou_em_mim: comentadores dos ultimos 3 medias do target (no nosso caso, @{{DOMINIO_AI}})
    - comentou_em_alvo: comentadores dos ultimos 3 medias de qualquer perfil-alvo
    """
    src = target['source']
    target_user = target['username']
    log.info(f'[hiker] Discover for target id={target["id"]} src={src} user=@{target_user}')

    if not HIKERAPI_AVAILABLE or not HIKERAPI_KEY:
        log.error('[hiker] HikerAPI nao disponivel/configurada. Discovery abortado.')
        telegram_alert('HikerAPI offline. Discovery do Ademir parado.')
        return []

    try:
        if src == 'seguidor_verificado':
            return discover_seguidor_verificado_via_hiker(target)
        elif src == 'meu_seguidor':
            # Mantido por compat: pega followers SEM filtro de verified
            base = hiker_user_by_username(target_user) or {}
            user_id = str(base.get('pk') or base.get('id') or '')
            if not user_id:
                return []
            max_pages = int(os.getenv('ADEMIR_HIKER_FOLLOWERS_PAGES', '4'))
            seen_set = set()
            cursor = None
            for page in range(max_pages):
                users, cursor = hiker_user_followers_page(user_id, max_id=cursor)
                if not users:
                    break
                for u in users:
                    uname = (u.get('username') or '').lower()
                    if uname and uname not in seen_set:
                        seen_set.add(uname)
                if not cursor:
                    break
            usernames = list(seen_set)
            rows = db_query(
                'SELECT ig_username FROM prospect_leads WHERE ig_username = ANY(%s)',
                (usernames,), fetchall=True,
            ) or []
            existing = set(r['ig_username'] for r in rows)
            return [u for u in usernames if u not in existing]
        elif src in ('comentou_em_mim', 'comentou_em_alvo'):
            return discover_commenters_via_hiker(target)
        else:
            log.warning(f'Source desconhecido: {src}')
            return []
    except Exception as e:
        log.error(f'[hiker] Discover error: {e}')
        return []


# ── Profile Analysis ──
# CORRECAO B: bio do proprio {{DONO}} capturada no inicio do run.
# Funciona como firewall: se a bio extraida de um lead for igual ou substring
# grande da MY_OWN_BIO, descartamos pra nunca mais sair lead com bio do Chefe.
MY_OWN_USERNAME = '{{DOMINIO_AI}}'
MY_OWN_BIO: Optional[str] = None


def _extract_bio_from_html(html: str) -> Optional[str]:
    """Helper local pra extrair bio do HTML do IG (mesma logica do analyze_profile)."""
    import re
    bio_text = None
    m = re.search(r'"biography":\s*"([^"]+)"', html)
    if m:
        try:
            bio_text = m.group(1).encode().decode('unicode_escape', errors='ignore')
        except Exception:
            bio_text = m.group(1)
    if not bio_text:
        m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
        if m:
            bio_text = m.group(1)
    if not bio_text:
        m = re.search(r'\\"biography\\":\\?"([^"\\]+)', html)
        if m:
            bio_text = m.group(1)
    if bio_text:
        try:
            bio_text = bio_text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
            bio_text = ''.join(c for c in bio_text if 0xD800 > ord(c) or ord(c) > 0xDFFF)
        except Exception:
            return None
    return bio_text


def capture_my_own_bio() -> Optional[str]:
    """Captura a bio do @{{DOMINIO_AI}} uma vez no inicio do run.
    Usada como firewall pra detectar leads bugados que vieram com bio do Chefe.
    """
    global MY_OWN_BIO
    try:
        url = f'https://www.instagram.com/{MY_OWN_USERNAME}/'
        tandem_navigate(url)
        ok = tandem_wait_for_url(url, timeout_sec=30)
        if not ok:
            log.warning('capture_my_own_bio: nav nao confirmada, continua mesmo assim')
        time.sleep(2)
        html = tandem_page_html()
        bio = _extract_bio_from_html(html)
        if bio and len(bio) > 20:
            MY_OWN_BIO = bio.strip()
            log.info(f'MY_OWN_BIO capturada ({len(MY_OWN_BIO)} chars): "{MY_OWN_BIO[:80]}..."')
            return MY_OWN_BIO
        log.warning('capture_my_own_bio: bio nao encontrada/curta')
    except Exception as e:
        log.error(f'capture_my_own_bio error: {e}')
    return None


def _bio_matches_my_own(bio: Optional[str]) -> tuple:
    """CORRECAO B: compara bio do lead com a do {{DONO}}.
    Retorna (match: bool, reason: str). match=True significa DESCARTAR.
    """
    if not bio:
        return (False, '')
    b = bio.strip().lower()
    # Heuristica forte: nome/dominio do Chefe no meio da bio
    if '{{DONO_SLUG}}' in b or '{{DOMINIO_AI}}' in b:
        return (True, 'bio_contem_{{DONO_SLUG}}')
    if not MY_OWN_BIO:
        return (False, '')
    own = MY_OWN_BIO.strip().lower()
    if b == own:
        return (True, 'bio_igual_a_minha')
    # Substring grande (>=50 chars consecutivos)
    if len(own) >= 50:
        # confere prefixo de 50 caracteres da minha bio na bio do lead
        if own[:50] in b:
            return (True, 'bio_substring_minha_50chars')
    return (False, '')


OFFER_KEYWORDS = (
    'curso', 'cursos', 'mentoria', 'mentorias', 'mentor', 'mentora',
    'compre', 'aula', 'aulas', 'método', 'metodo', 'consultoria',
    'agencia', 'agência', 'pix', 'whatsapp', 'wpp', 'whats',
    'vagas', 'inscreva', 'matricule', 'comprar', 'desconto',
    'promocao', 'promoção', 'lancamento', 'lançamento', 'r$',
    'planos', 'venda', 'vendas', 'serviço', 'servico',
    'formação', 'formacao', 'treinamento', 'palestra', 'palestrante',
    'coach', 'ceo', 'fundador', 'founder',
)

OFFER_LINK_DOMAINS = (
    'contate.me', 'wa.me', 'hotmart', 'kiwify', 'eduzz', 'pay.', 'beacons',
    'linktr.ee', 'lnk.bio', 'bio.link', 'kirvano',
)


def _detect_has_offer(bio: str, external_url: str) -> bool:
    """Heuristica de oferta: bio menciona produto/servico OU link externo aponta pra checkout."""
    bio_low = (bio or '').lower()
    url_low = (external_url or '').lower()
    has_kw = any(kw in bio_low for kw in OFFER_KEYWORDS)
    has_link_offer = bool(url_low) and any(d in url_low for d in OFFER_LINK_DOMAINS)
    return has_kw or has_link_offer


def _guess_niche(bio: str, full_name: str = '', category: str = '') -> Optional[str]:
    """Heuristica leve de nicho a partir de keywords da bio/category.
    Usa word-boundary regex pra evitar falso positivo (ex: 'orto' batendo em 'porto').
    """
    import re as _local_re
    text = f'{bio or ""} {full_name or ""} {category or ""}'.lower()
    rules = [
        ('marketing digital', [r'\bmarketing digital\b', r'\bmkt digital\b', r'\bmedia buyer\b', r'tr[áa]fego pago']),
        ('infoproduto', [r'\binfoprodutor\b', r'\binfoproduto\b', r'\bprodutor digital\b', r'lan[çc]amento']),
        ('agencia', [r'\bag[êe]ncia\b', r'\bagency\b']),
        ('coaching', [r'\bcoach\b', r'\bcoaching\b', r'\bmentor\b', r'\bmentora\b', r'\bmentoria\b']),
        ('arquitetura', [r'\barquiteto\b', r'\barquiteta\b', r'\barquitetura\b']),
        ('estética', [r'\best[ée]tica\b', r'\bharmoniza', r'\bbiomedic']),
        ('odontologia', [r'\bdentist', r'\bodonto', r'\bortodontia\b', r'\bbucomaxilo']),
        ('saúde', [r'\bsa[úu]de\b', r'\bnutri', r'\bpsic[óo]log', r'\bfisioterap']),
        ('imobiliario', [r'\bimobili', r'\bcorretor\b', r'\bim[óo]vel\b']),
        ('educacao', [r'\bprofessor\b', r'\bprofessora\b', r'\beduca', r'\bconcurso\b', r'\benem\b']),
        ('moda', [r'\bmoda\b', r'\bfashion\b', r'\bestilo\b']),
        ('e-commerce', [r'\be-commerce\b', r'\becommerce\b', r'\bloja virtual\b']),
        ('cripto/financas', [r'\bcripto\b', r'\bcrypto\b', r'\binvestimento\b', r'\btrader\b', r'\bforex\b']),
        ('arte/criativo', [r'\bartista\b', r'\bcineasta\b', r'\baudiovisual\b', r'\bdesigner\b', r'\bfotograf']),
        ('consultoria', [r'\bconsultoria\b', r'\bconsultor\b']),
    ]
    for niche, patterns in rules:
        for pat in patterns:
            if _local_re.search(pat, text):
                return niche
    return None


def analyze_profile_via_api(username: str) -> Optional[dict]:
    """FASE 3: analisa perfil via HikerAPI (substitui Tandem).

    Faz 1-2 chamadas:
    - user_by_username_v2 (pega bio, follower_count, full_name, is_verified, external_url, category)
    - user_medias_chunk_v1 (pega 3 posts recentes com caption_text/like_count)

    Retorna dict no MESMO shape de analyze_profile (compatibilidade com run() e DB).
    """
    if not HIKERAPI_AVAILABLE or not HIKERAPI_KEY:
        log.warning(f'[hiker] HikerAPI offline, fallback Tandem para @{username}')
        return analyze_profile(username)
    try:
        log.info(f'[hiker] Analyze @{username}')
        u = hiker_user_by_username(username)
        if not u:
            log.warning(f'[hiker] @{username} nao encontrado, descartando')
            return None
        # Validacao leve: bio retornada nao pode estar vazia E nao pode ser identica a do {{DONO}}
        bio = (u.get('biography') or '').strip()
        if MY_OWN_BIO and bio and bio.strip() == MY_OWN_BIO.strip():
            log.warning(f'[hiker] @{username}: bio igual a do Chefe, descartando (race condition residual)')
            return None
        full_name = u.get('full_name') or None
        followers = u.get('follower_count')
        following = u.get('following_count')
        media_count = u.get('media_count')
        is_verified = bool(u.get('is_verified'))
        external_url = u.get('external_url') or None
        category = u.get('category_name') or u.get('category') or None
        user_id = str(u.get('pk') or u.get('id') or '')

        # Pega 3 posts recentes
        posts_data = []
        if user_id:
            medias = hiker_user_medias(user_id, count=3)
            for m in medias:
                cap = (m.get('caption_text') or '')[:500]
                posts_data.append({
                    'kind': 'reel' if m.get('media_type') == 2 else 'p',
                    'post_id': m.get('code') or m.get('pk'),
                    'caption': cap,
                    'taken_at': m.get('taken_at') or m.get('taken_at_ts'),
                    'like_count': m.get('like_count'),
                    'comment_count': m.get('comment_count'),
                })

        has_offer = _detect_has_offer(bio, external_url)
        if not has_offer and external_url and (followers or 0) > 500:
            has_offer = True
        niche = _guess_niche(bio, full_name or '', category or '')

        briefing = {
            'source_api': 'hikerapi',
            'display_name': full_name,
            'bio': bio,
            'external_link': external_url,
            'followers': followers,
            'following': following,
            'posts_count': media_count,
            'is_verified': is_verified,
            'is_business': bool(u.get('is_business')),
            'category': category,
            'public_email': u.get('public_email') or None,
            'contact_phone_number': u.get('contact_phone_number') or None,
            'oferta_principal': bio[:300] if bio else None,
            'niche_guess': niche,
            'recent_posts': [{'kind': p['kind'], 'id': p['post_id']} for p in posts_data],
            'posts': posts_data,
        }
        # FASE 4: extrai WhatsApp das 3 fontes (sem chamada extra HikerAPI)
        try:
            wa = extract_whatsapp(briefing)
            briefing['whatsapp_number'] = wa
            if wa:
                log.info(f'[hiker] @{username} WhatsApp encontrado: {wa}')
        except Exception as _e:
            log.warning(f'[hiker] @{username} extract_whatsapp falhou: {_e}')
            briefing['whatsapp_number'] = None

        return {
            'ig_username': username,
            'display_name': full_name,
            'bio': bio,
            'external_link': external_url,
            'followers_count': followers,
            'following_count': following,
            'posts_count': media_count,
            'is_verified': is_verified,
            'has_offer': has_offer,
            'niche': niche,
            'briefing': briefing,
        }
    except Exception as e:
        log.error(f'[hiker] analyze_profile_via_api @{username} error: {e}')
        return None


def analyze_profile(username: str) -> Optional[dict]:
    """[DEPRECATED FASE 3] Usa Tandem. Mantido apenas como fallback caso HikerAPI esteja offline.

    Aplica:
    - CORRECAO A: wait robusto via tandem_wait_for_url apos /navigate
    - CORRECAO C: valida via /status que url contem instagram.com/USERNAME/
    - CORRECAO B: rejeita lead se bio bate com a do Chefe
    """
    try:
        log.info(f'Analyze @{username}')
        target_url = f'https://www.instagram.com/{username}/'
        tandem_navigate(target_url)
        # CORRECAO A: aguarda url confirmada antes de ler page-html
        ok = tandem_wait_for_url(target_url, timeout_sec=30)
        if not ok:
            log.warning(f'nav_timeout @{username}: descartando lead')
            return None
        # CORRECAO C: valida URL atual via /status
        st = tandem_status() or {}
        cur = (st.get('url') or '').lower()
        expect_frag = f'instagram.com/{username.lower()}'
        if expect_frag not in cur:
            log.warning(f'url_mismatch @{username}: expected fragment "{expect_frag}", got "{cur}". Descartando.')
            return None
        time.sleep(2)
        html = tandem_page_html()
        if not html or len(html) < 1000:
            log.warning(f'HTML vazio para @{username}')
            return None

        import re
        # og:description trazem followers/following/posts e bio
        og_desc = ''
        m = re.search(r'<meta\s+property="og:description"\s+content="([^"]+)"', html)
        if m:
            og_desc = m.group(1)

        followers = following = posts_count = None
        m = re.search(r'([\d.,]+[KMmkBb]?)\s+(?:Followers|seguidores)', og_desc, re.I)
        if m:
            followers = parse_count(m.group(1))
        m = re.search(r'([\d.,]+[KMmkBb]?)\s+(?:Following|seguindo)', og_desc, re.I)
        if m:
            following = parse_count(m.group(1))
        m = re.search(r'([\d.,]+[KMmkBb]?)\s+(?:Posts|publica)', og_desc, re.I)
        if m:
            posts_count = parse_count(m.group(1))

        # Display name: vem em og:description "...de NAME (@username)"
        display_name = None
        m = re.search(r' de (.+?) \(@', og_desc)
        if m:
            display_name = m.group(1).strip()
        # fallback ao title
        if not display_name:
            m = re.search(r'<title>([^<]+)</title>', html)
            if m:
                t = m.group(1).strip()
                # remove "Instagram"  e "(@user)"
                t = re.sub(r'@\w+', '', t)
                t = re.sub(r'\bInstagram\b', '', t, flags=re.I)
                t = re.sub(r'[\(\)\|]', '', t).strip()
                if t:
                    display_name = t

        # Bio: tenta o JSON inline biography (do shared_data ou meta-style)
        bio_text = None
        m = re.search(r'"biography":\s*"([^"]+)"', html)
        if m:
            bio_text = m.group(1).encode().decode('unicode_escape', errors='ignore')
        if not bio_text:
            # tenta meta name description
            m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', html)
            if m:
                bio_text = m.group(1)
        # tambem pode estar dentro de scripts
        if not bio_text:
            m = re.search(r'\\"biography\\":\\?"([^"\\]+)', html)
            if m:
                bio_text = m.group(1)

        # Sanitiza bio_text removendo surrogates UTF-16 invalidos (emojis quebrados)
        if bio_text:
            try:
                bio_text = bio_text.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')
                # Remove caracteres surrogate isolados
                bio_text = ''.join(c for c in bio_text if 0xD800 > ord(c) or ord(c) > 0xDFFF)
            except Exception:
                bio_text = None

        # CORRECAO B: firewall contra bio mistutada com a do Chefe
        is_dup, reason = _bio_matches_my_own(bio_text)
        if is_dup:
            log.warning(f'bio_mismatch_{{DONO_SLUG}} @{username} ({reason}): descartando')
            return None

        # External link
        external_link = None
        for m in re.finditer(r'href="(https?://l\.instagram\.com/\?u=([^"&]+))', html):
            try:
                from urllib.parse import unquote
                external_link = unquote(m.group(2))
                break
            except Exception:
                pass
        if not external_link:
            m = re.search(r'href="(https?://(?!instagram\.com|cdninstagram|fbcdn|facebook\.com)[^"]+)"', html)
            if m:
                external_link = m.group(1)

        # Verified
        is_verified = 'verified' in html.lower() or 'Verified account' in html or 'Conta verificada' in html

        # Posts
        posts = parse_post_ids_from_html(html)[:3]

        briefing = {
            'display_name': display_name,
            'bio': bio_text,
            'external_link': external_link,
            'followers': followers,
            'following': following,
            'posts_count': posts_count,
            'is_verified': is_verified,
            'recent_posts': [{'kind': k, 'id': pid} for k, pid in posts],
            'og_description': og_desc,
        }

        # Visit a few posts to capture caption
        posts_data = []
        for kind, pid in posts[:2]:
            try:
                purl = f'https://www.instagram.com/{username}/{kind}/{pid}/'
                tandem_navigate(purl)
                # CORRECAO A: confirma navegacao antes de ler HTML
                if not tandem_wait_for_url(purl, timeout_sec=20):
                    log.warning(f'post nav timeout {purl}, skip caption')
                    continue
                ph = tandem_page_html()
                cap = ''
                cm = re.search(r'<meta\s+property="og:title"\s+content="([^"]+)"', ph)
                if cm:
                    cap = cm.group(1)
                if not cap:
                    dm = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', ph)
                    if dm:
                        cap = dm.group(1)
                # tambem captura caption do span aria-label
                if not cap:
                    am = re.search(r'<title>([^<]+)</title>', ph)
                    if am:
                        cap = am.group(1)
                posts_data.append({'kind': kind, 'post_id': pid, 'caption': cap[:500]})
            except Exception as e:
                log.warning(f'post fetch failed: {e}')
        briefing['posts'] = posts_data
        # Detecta oferta principal a partir da bio
        if bio_text:
            briefing['oferta_principal'] = bio_text[:300]

        # has_offer heuristica:
        # 1) Tem link externo + bio menciona CTA/produto, OU
        # 2) Tem link externo na bio + categoria de negocio (heuristica de pgmento/curso)
        offer_kws = ('curso', 'mentoria', 'mentorias', 'compre', 'aula', 'aulas', 'método', 'metodo',
                     'consultoria', 'agencia', 'agência', 'pix', 'whatsapp', 'wpp', 'whats',
                     'vagas', 'inscreva', 'matricule', 'comprar', 'compre ja', 'desconto',
                     'promocao', 'promoção', 'lancamento', 'lançamento', 'r$', 'planos',
                     'venda', 'vendas', 'serviço', 'servico')
        has_offer = bool(external_link) and (
            any(kw in (bio_text or '').lower() for kw in offer_kws)
            or any(kw in (og_desc or '').lower() for kw in offer_kws)
            or 'contate.me' in (external_link or '').lower()
            or 'wa.me' in (external_link or '').lower()
            or 'hotmart' in (external_link or '').lower()
            or 'kiwify' in (external_link or '').lower()
            or 'eduzz' in (external_link or '').lower()
            or 'pay.' in (external_link or '').lower()
        )
        # Se tem link externo e displayname/seguidores >500 marcamos como qualificavel
        if not has_offer and external_link and (followers or 0) > 500:
            has_offer = True

        return {
            'ig_username': username,
            'display_name': display_name,
            'bio': bio_text,
            'external_link': external_link,
            'followers_count': followers,
            'following_count': following,
            'posts_count': posts_count,
            'is_verified': is_verified,
            'has_offer': has_offer,
            'niche': None,  # opcional, Claude pode preencher
            'briefing': briefing,
        }
    except Exception as e:
        log.error(f'analyze_profile error: {e}')
        return None


def sanitize_text(s):
    if not s:
        return s
    try:
        # Remove surrogates invalidos e normaliza
        return ''.join(c for c in s if not (0xD800 <= ord(c) <= 0xDFFF))[:2000]
    except Exception:
        return None


# ── WhatsApp extraction (FASE 4) ──
import re as _re_wa


def _normalize_br_phone(raw: str) -> Optional[str]:
    """Normaliza pra +55XXXXXXXXXXX. Retorna None se invalido.
    Aceita formatos com/sem +55, com/sem DDD, com/sem 9 inicial.
    Valida: DDD 11-99, total 10 ou 11 digitos pos +55."""
    if not raw:
        return None
    digits = _re_wa.sub(r'\D', '', raw)
    # Se ja vem com 55 prefixo (>= 12 digitos), usa como esta
    if len(digits) >= 12 and digits.startswith('55'):
        ddd = digits[2:4]
        rest = digits[4:]
    elif 10 <= len(digits) <= 11:
        ddd = digits[:2]
        rest = digits[2:]
    else:
        return None
    # Valida DDD
    try:
        ddd_int = int(ddd)
        if ddd_int < 11 or ddd_int > 99:
            return None
    except Exception:
        return None
    # Valida tamanho do "rest" (8 ou 9 digitos)
    if len(rest) not in (8, 9):
        return None
    # Se tem 8 digitos e e celular (comecando com 6,7,8,9), adiciona 9
    if len(rest) == 8 and rest[0] in '6789':
        rest = '9' + rest
    return f'+55{ddd}{rest}'


def extract_whatsapp(briefing: dict) -> Optional[str]:
    """Extrai numero WhatsApp em formato +5511XXXXXXXXX a partir do briefing.

    Ordem de prioridade:
    1. briefing.contact_phone_number (campo oficial Instagram)
    2. Regex no briefing.bio (com hints "Whats:", "WhatsApp:", "Wpp:", "Tel:")
    3. Regex em briefing.external_link (wa.me/, whatsapp.com/send?phone=)

    Retorna None se nao achar.
    """
    if not isinstance(briefing, dict):
        return None

    # 1) contact_phone_number direto
    cpn = briefing.get('contact_phone_number')
    if cpn and isinstance(cpn, str):
        n = _normalize_br_phone(cpn)
        if n:
            return n

    # 2) Bio (com hints prioritarios)
    bio = briefing.get('bio') or ''
    if isinstance(bio, str) and bio:
        # Hint patterns: "Whats: 11999999999" tem precedencia
        hint_patterns = [
            r'(?:whats(?:app)?|wpp|tel(?:efone)?|fone|contato)[\s:]*\+?(?:55\s?)?\(?(\d{2})\)?[\s\-]?9?(\d{4})[\s\-]?(\d{4})',
        ]
        for pat in hint_patterns:
            m = _re_wa.search(pat, bio, _re_wa.IGNORECASE)
            if m:
                ddd, p1, p2 = m.group(1), m.group(2), m.group(3)
                # Reconstroi com 9 se for celular sem 9
                base = f'{ddd}{p1}{p2}'
                n = _normalize_br_phone(base)
                if n:
                    return n
        # Padroes BR genericos sem hint
        generic_patterns = [
            r'\+?55[\s\-]?\(?(\d{2})\)?[\s\-]?9?(\d{4})[\s\-]?(\d{4})',
            r'\(?(\d{2})\)?[\s\-]?9(\d{4})[\s\-]?(\d{4})',
        ]
        for pat in generic_patterns:
            m = _re_wa.search(pat, bio)
            if m:
                ddd, p1, p2 = m.group(1), m.group(2), m.group(3)
                base = f'{ddd}{p1}{p2}'
                n = _normalize_br_phone(base)
                if n:
                    return n

    # 3) external_link (wa.me/, whatsapp.com/send?phone=)
    ext = briefing.get('external_link') or ''
    if isinstance(ext, str) and ext:
        wa_patterns = [
            r'wa\.me/(\+?55)?(\d{10,13})',
            r'whatsapp\.com/send\?[^#]*phone=(\+?55)?(\d{10,13})',
            r'api\.whatsapp\.com/send\?[^#]*phone=(\+?55)?(\d{10,13})',
        ]
        for pat in wa_patterns:
            m = _re_wa.search(pat, ext, _re_wa.IGNORECASE)
            if m:
                raw = (m.group(1) or '') + m.group(2)
                n = _normalize_br_phone(raw)
                if n:
                    return n

    return None


def parse_count(s: str) -> Optional[int]:
    """Converte '1.2K' / '6,020' / '2.5M' em int.
    PT-BR usa virgula como decimal e ponto como milhar.
    EN usa virgula como milhar e ponto como decimal.
    Heuristica: se terminar com 'k'/'m' eh sufixo, processa como decimal."""
    s = s.strip().lower()
    try:
        if s.endswith('m'):
            num = s[:-1].replace(',', '.')
            return int(float(num) * 1_000_000)
        if s.endswith('k'):
            num = s[:-1].replace(',', '.')
            return int(float(num) * 1000)
        # numeros inteiros: remove qualquer separador
        clean = s.replace(',', '').replace('.', '')
        return int(clean)
    except Exception:
        return None


# ── DM Generation (Template oficial + Claude Agent SDK) ──
DM_SYSTEM_PROMPT = """Você é o {{DONO_NOME_COMPLETO}} escrevendo a primeira DM no Instagram para um lead que você acabou de descobrir via prospecção ativa. Você NUNCA se identifica como IA, agente ou bot. Você É o {{DONO}}.

OBJETIVO: gerar UMA DM seguindo EXATAMENTE o template oficial cravado pelo Chefe (10 elementos, ordem fixa), QUEBRADA EM 3 PARTES separadas por [BREAK], com parágrafos curtos.

==================================
REGRA INVIOLÁVEL #1: NUNCA USE TRAVESSÃO
==================================
NUNCA use o caractere travessão "—" (em dash) ou "–" (en dash) em NENHUM lugar da mensagem.
Travessão entrega imediatamente que o texto foi gerado por IA. É PROIBIDO.
Substitua sempre por: vírgula, ponto, dois pontos, ponto e vírgula, ou quebra de frase.

ERRADO: "isso é resultado de método bem feito — mermão"
CERTO: "isso é resultado de método bem feito, mermão"

Se sair UM travessão na resposta, ela SERÁ REJEITADA.

==================================
REGRA INVIOLÁVEL #2: ACENTUAÇÃO PORTUGUÊS BRASILEIRO SEMPRE CORRETA
==================================
O texto DEVE sair como um humano escreve no celular com teclado PT-BR. Acentuação completa, cedilha, til, tudo certo. NUNCA escreva sem acento.

PROIBIDO (errado, parece IA mal calibrada):
"To vendo", "metodo", "Nao sei", "operacao", "Inteligencia", "Agentica", "padronizacao", "voce", "esta", "ja", "tambem", "agencia", "mentoria sera", "nao", "cao", "promocao", "lancamento", "servico", "atencao", "informacao".

OBRIGATÓRIO (certo, soa humano):
"Tô vendo", "método", "Não sei", "operação", "Inteligência", "Agêntica", "padronização", "você", "está", "já", "também", "agência", "mentoria será", "não", "cão", "promoção", "lançamento", "serviço", "atenção", "informação".

Se a DM sair sem acentos, SERÁ REJEITADA.

==================================
REGRA INVIOLÁVEL #3: EMOJIS DISCRETOS (MÁXIMO 2 NO TOTAL)
==================================
Emojis devem ser DISCRETOS. A DM total (3 partes juntas) deve ter NO MÁXIMO 2 emojis no TOTAL, somando todas as partes.

REGRAS:
- Não use emoji em toda parte. Use só onde fizer sentido natural.
- Sem DM colorida. Sem emoji em cada frase.
- Local preferido: 🤙 (call me hand) no CTA final, conforme template original.
- 🤝 também é aceito como segundo emoji opcional.
- ZERO emojis no meio do texto.

Se a DM tiver 3 ou mais emojis, SERÁ REJEITADA.

==================================
REGRA INVIOLÁVEL #4: QUEBRAR EM 3 PARTES COM [BREAK]
==================================
A DM PRECISA SAIR DIVIDIDA EM 3 PARTES, separadas pelo marcador literal [BREAK] (com colchetes, em uma linha sozinha).

Cada parte deve ser autocontida, com parágrafos curtos de 2-3 frases e linhas em branco entre parágrafos quando fizer sentido. Nunca uma muralha de texto.

Estrutura obrigatória:
- PARTE 1 (saudação + reconhecimento + prova social + validação posicionamento): elementos 1-4 do template
- PARTE 2 (gancho IA Agêntica + conexão real + pitch prático + encaixe na dor): elementos 5-9 do template
- PARTE 3 (CTA suave fechando): elemento 10 apenas

Entre cada parte, uma linha contendo APENAS [BREAK].

==================================
TEMPLATE OFICIAL (10 elementos, ordem rígida):
==================================
1. Saudação informal: "Fala [Primeiro Nome]!"
2. Reconhecimento do trabalho: "Tô vendo teu trabalho com [tema dele identificado da bio/posts]"
3. Citação de prova social do próprio perfil dele (extraída da bio/posts): número R$ X milhões / [N] pessoas no time / [N] clientes / [N] alunos seguido de "isso é resultado de método bem feito, mermão" (use VÍRGULA antes de "isso", NUNCA travessão)
4. Validação do posicionamento dele: 'Curti demais a proposta da "[posicionamento literal que ele usa na bio]"'
5. Diferenciação positiva: "sai muito do guru que complica pra parecer expert"
6. Gancho IA Agêntica (linha curta): "Não sei se tu já está utilizando Inteligência artificial 'Agêntica' na sua rotina"
7. Conexão real com a operação: "Olhando o que tu faz, achei que poderia ter uma sinergia boa pra escalar a operação da tua [tipo de operação: agência / mentoria / curso / clínica / etc] com [tamanho: N clientes / N alunos / N pessoas no time]"
8-9. Pitch prático + encaixe na dor (gestão/padronização): "ter automações de Instagram e WhatsApp integradas ao CRM num lugar só deve resolver bastante da dor de gestão e padronização de processos"
10. CTA suave fechando: "Se fizer sentido, me avisa aqui que eu te explico melhor (ou a gente marca uma call). Sem compromisso, só quis deixar a porta aberta. \U0001F919"

==================================
TOM:
==================================
- Casual, com gírias regionais ("mermão", "tu")
- Emojis raros (máximo 2 na DM inteira), só onde faz sentido natural
- Sem cara de vendedor, cara de empresário curtindo o trabalho do outro
- Sem mencionar preço, produto, marca específica
- Pergunta-gancho sobre IA Agêntica como ponte natural

==================================
NUNCA FAÇA:
==================================
- "Oi, tudo bem?"
- Apresentação genérica
- Pitch de produto direto
- Frases de venda explícitas ("vou te ajudar a vender mais")
- Marketing institucional ("nossa empresa oferece")
- Promessas exageradas
- TRAVESSÃO ("—" ou "–") em qualquer lugar do texto
- ESCREVER SEM ACENTOS ("To", "metodo", "Nao", "operacao")
- 3+ emojis na DM
- Emojis no meio do texto
- Bloco único sem [BREAK]
- Texto sem parágrafos curtos

==================================
EXEMPLO OURO PADRÃO (use como referência de estilo):
==================================

LEAD: @jaquesocialselling
NOME: Jaqueline Cassiano
BIO: Especialista em Social Selling, agência própria, +R$7MM gerados ativando pelo direct, 19 pessoas no time, 20+ clientes ativos. Posicionamento: "simplicidade que escala".

DM GERADA (formato exato a seguir, com acentos corretos e apenas 1 emoji no final):
Fala Jaqueline! Tô vendo teu trabalho com Social Selling e prospecção via Direct.

+7 milhões gerados "ativando pelo direct" com 19 pessoas no time e mais de 20 clientes ativos. Isso é resultado de método bem feito, mermão.

Curti demais a proposta da "simplicidade que escala", sai muito do guru que complica pra parecer expert.
[BREAK]
Não sei se tu já está utilizando Inteligência artificial "Agêntica" na sua rotina.

Olhando o que tu faz, achei que poderia ter uma sinergia boa pra escalar a operação da tua agência com 20+ clientes ativos.

Ter automações de Instagram e WhatsApp integradas ao CRM num lugar só deve resolver bastante da dor de gestão e padronização de processos.
[BREAK]
Se fizer sentido, me avisa aqui que eu te explico melhor (ou a gente marca uma call). Sem compromisso, só quis deixar a porta aberta. \U0001F919

(Note: 1 único emoji no final inteiro, todas as palavras com acentos, [BREAK] separando 3 partes.)

==================================
OUTPUT:
==================================
Retorne APENAS o texto da DM completo (com os 2 [BREAK] separadores), sem cabeçalho, sem aspas envolvendo, sem comentário, sem markdown, sem numeração. Apenas o texto puro com [BREAK] separando as 3 partes. Adapte os 10 elementos aos dados reais do lead. ZERO TRAVESSÃO. ACENTOS PERFEITOS. MÁXIMO 2 EMOJIS NO TOTAL."""


def _build_dm_user_prompt(lead: dict) -> str:
    bio = lead.get('bio') or 'sem bio capturada'
    name = lead.get('display_name') or lead.get('ig_username')
    briefing = lead.get('briefing') or {}
    posts = briefing.get('posts') or []
    captions = '\n'.join([f'- {(p.get("caption") or "")[:240]}' for p in posts[:3]]) or '- (sem posts capturados)'
    oferta = briefing.get('oferta_principal') or briefing.get('external_link') or '(sem oferta declarada)'
    seguidores = lead.get('followers_count') or briefing.get('followers') or '?'
    return f"""DADOS DO LEAD:
- Username Instagram: @{lead.get('ig_username')}
- Nome: {name}
- Bio (literal do Instagram): {bio}
- Oferta/link principal detectado: {oferta}
- Seguidores: {seguidores}
- Tem oferta declarada: {lead.get('has_offer')}
- Posts recentes (captions):
{captions}

Gera a DM seguindo o template oficial dos 10 elementos. Use girias casuais ("tu", "mermao") quando couber. Adapte os numeros e o posicionamento ao que esta de fato na bio/posts dele. Se nao tiver numero especifico de prova social, use volume de seguidores ou de conteudo. Mantem natural e nao copia o exemplo da Jaqueline — adapta tudo ao contexto deste lead."""


async def _generate_dm_via_claude_sdk(system_prompt: str, user_prompt: str) -> Optional[str]:
    """Roda o claude_agent_sdk localmente, sem API key (usa OAuth do Chefe via CLI claude)."""
    if not CLAUDE_SDK_AVAILABLE:
        return None
    try:
        options = ClaudeAgentOptions(
            max_turns=1,
            system_prompt=system_prompt,
        )
        result_text = ''
        async for msg in claude_query(prompt=user_prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
        return result_text.strip() if result_text else None
    except Exception as e:
        log.error(f'claude_sdk error: {e}')
        return None


# ── Sanitizacao anti-travessao + validacao [BREAK] + acentos + emojis ──
DASH_CHARS = ('—', '–')  # em dash, en dash

# Mapa de correcoes programaticas: token sem acento -> token com acento
# So aplica em casos inequivocos (palavra completa, sem ambiguidade contextual).
ACCENT_FIXES = {
    # Verbos
    r'\bTo vendo\b': 'Tô vendo',
    r'\bto vendo\b': 'tô vendo',
    r'\bNao sei\b': 'Não sei',
    r'\bnao sei\b': 'não sei',
    r'\bnao\b': 'não',
    r'\bja esta\b': 'já está',
    r'\bja está\b': 'já está',
    r'\bvoce\b': 'você',
    r'\btambem\b': 'também',
    # Substantivos chave do template
    r'\bmetodo\b': 'método',
    r'\bMetodo\b': 'Método',
    r'\boperacao\b': 'operação',
    r'\bOperacao\b': 'Operação',
    r'\bInteligencia\b': 'Inteligência',
    r'\binteligencia\b': 'inteligência',
    r'\bAgentica\b': 'Agêntica',
    r'\bagentica\b': 'agêntica',
    r'\bpadronizacao\b': 'padronização',
    r'\bPadronizacao\b': 'Padronização',
    r'\bgestao\b': 'gestão',
    r'\bGestao\b': 'Gestão',
    r'\bautomacoes\b': 'automações',
    r'\bAutomacoes\b': 'Automações',
    r'\bagencia\b': 'agência',
    r'\bAgencia\b': 'Agência',
    r'\bclinica\b': 'clínica',
    r'\bClinica\b': 'Clínica',
    r'\bmilhoes\b': 'milhões',
    r'\bservico\b': 'serviço',
    r'\bservicos\b': 'serviços',
    r'\bso\b': 'só',  # cuidado: pode pegar palavras como "isso" se usado mal, mas \b protege
    r'\bSo\b': 'Só',
    r'\binformacao\b': 'informação',
    r'\binformacoes\b': 'informações',
    r'\batencao\b': 'atenção',
    r'\bAtencao\b': 'Atenção',
    r'\bpromocao\b': 'promoção',
    r'\bPromocao\b': 'Promoção',
    r'\blancamento\b': 'lançamento',
    r'\bLancamento\b': 'Lançamento',
    r'\bsessao\b': 'sessão',
    r'\bsessoes\b': 'sessões',
    r'\bsao\b': 'são',  # plural de "ser"
    r'\bSao\b': 'São',
    r'\bnumero\b': 'número',
    r'\bnumeros\b': 'números',
    r'\bsera\b': 'será',
    r'\bSera\b': 'Será',
    r'\bproprio\b': 'próprio',
    r'\bproxima\b': 'próxima',
    r'\bproximo\b': 'próximo',
    r'\bmermao\b': 'mermão',
    r'\bMermao\b': 'Mermão',
    r'\bja\b': 'já',
    r'\besta\b': 'está',  # em contextos de "esta a usar" (raro confundir com pronome)
    # Palavras unicas tipicas do template
    r'\bsentido\.': 'sentido.',  # ja tem acento
}

# Palavras-chave que sinalizam DM sem acentos e merecem retry
ACCENT_REQUIRED_TOKENS = (
    'to vendo', 'metodo', 'nao sei', 'operacao', 'inteligencia',
    'agentica', 'padronizacao', 'gestao', 'automacoes', 'agencia',
    'voce', 'tambem', 'milhoes', 'mermao',
)

# Regex de emojis (cobre BMP + Supplementary Multilingual Plane)
import re as _re
_EMOJI_RE = _re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F700-\U0001F77F"  # alchemical symbols
    "\U0001F780-\U0001F7FF"  # geometric shapes ext
    "\U0001F800-\U0001F8FF"  # supplemental arrows-c
    "\U0001F900-\U0001F9FF"  # supplemental symbols & pictographs
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols & pictographs ext-a
    "\U00002600-\U000026FF"  # miscellaneous symbols
    "\U00002700-\U000027BF"  # dingbats
    "]+",
    flags=_re.UNICODE,
)


def _strip_dashes(text: str) -> str:
    """Remove travessoes substituindo por virgula+espaco. Tambem normaliza ' — ' -> ', '."""
    if not text:
        return text
    # primeiro normaliza padroes ' — ' / ' – ' (com espacos) pra ', '
    out = text.replace(' — ', ', ').replace(' – ', ', ')
    out = out.replace('— ', ', ').replace('– ', ', ')
    out = out.replace(' —', ',').replace(' –', ',')
    # ai limpa qualquer travessao residual
    out = out.replace('—', ', ').replace('–', ', ')
    # limpa espacos duplos que possam ter sido criados
    while '  ' in out:
        out = out.replace('  ', ' ')
    # normaliza ", ," ou " ," que possa ter sobrado
    out = out.replace(' ,', ',').replace(',,', ',')
    return out


def _has_dash(text: str) -> bool:
    return any(d in (text or '') for d in DASH_CHARS)


def _has_break(text: str) -> bool:
    return '[BREAK]' in (text or '')


def _split_dm_parts(text: str) -> list:
    """Quebra a DM nas marcacoes [BREAK]. Retorna lista de partes nao vazias e trimadas."""
    if not text:
        return []
    parts = [p.strip() for p in text.split('[BREAK]')]
    parts = [p for p in parts if p]
    return parts


def _force_break_split(text: str) -> str:
    """Se a DM nao tiver [BREAK], tenta quebrar heuristicamente em 3 partes na cara da sentenca-gancho.
    Estrategia: quebra em 'Nao sei se tu ja esta' (gancho IA) e em 'Se fizer sentido' (CTA).
    Retorna texto com [BREAK] inserido nesses pontos."""
    if not text:
        return text
    out = text
    # ponto 1: gancho IA Agentica
    for needle in ('Nao sei se tu ja esta', 'Não sei se tu já está', 'Nao sei se voce ja esta', 'Não sei se você já está'):
        if needle in out and '[BREAK]\n' + needle not in out:
            out = out.replace(needle, '[BREAK]\n' + needle, 1)
            break
    # ponto 2: CTA
    for needle in ('Se fizer sentido', 'Se fizer sentido,'):
        if needle in out and '[BREAK]\n' + needle not in out:
            out = out.replace(needle, '[BREAK]\n' + needle, 1)
            break
    return out


def _normalize_acentos(text: str) -> str:
    """Aplica correcoes inequivocas de acentuacao em tokens conhecidos.
    Pos-processamento de seguranca: se o modelo escapou e devolveu sem acento,
    trocamos os tokens-chave do template pra forma acentuada."""
    if not text:
        return text
    out = text
    for pattern, repl in ACCENT_FIXES.items():
        out = _re.sub(pattern, repl, out)
    return out


def _has_missing_accents(text: str) -> bool:
    """Detecta se a DM tem tokens conhecidos sem acento (sinaliza que precisa retry)."""
    if not text:
        return False
    low = text.lower()
    hits = sum(1 for tok in ACCENT_REQUIRED_TOKENS if tok in low)
    return hits >= 2  # 2+ tokens sem acento = DM mal formada


def _count_emojis(text: str) -> int:
    """Conta emojis na DM inteira (todas as partes somadas, ignora [BREAK])."""
    if not text:
        return 0
    cleaned = text.replace('[BREAK]', '')
    matches = _EMOJI_RE.findall(cleaned)
    # cada match e um run de 1+ emojis; conta caractere por caractere
    total = 0
    for run in matches:
        total += len([c for c in run if not c.isspace()])
    return total


def _trim_excess_emojis(text: str, max_count: int = 2) -> str:
    """Se DM tem mais de max_count emojis, remove os primeiros e mantem so os ultimos.
    Estrategia: caca os runs de emoji e remove os mais ao topo, preservando o do CTA final."""
    if not text:
        return text
    current = _count_emojis(text)
    if current <= max_count:
        return text
    # Remove emojis um a um a partir do inicio ate sobrarem max_count
    to_remove = current - max_count
    out = text
    for _ in range(to_remove):
        m = _EMOJI_RE.search(out)
        if not m:
            break
        # remove o primeiro caractere emoji do match (nao o run inteiro)
        first_char_idx = m.start()
        out = out[:first_char_idx] + out[first_char_idx + 1:]
    return out


def _validate_dm(text: str) -> tuple:
    """Retorna (ok, motivo). Validacoes: sem travessao, com [BREAK], 3 partes, acentos OK, emojis <= 2."""
    if not text or len(text) < 80:
        return False, 'texto vazio ou muito curto'
    if _has_dash(text):
        return False, 'contem travessao'
    parts = _split_dm_parts(text)
    if len(parts) < 2:
        return False, f'sem [BREAK] suficiente ({len(parts)} partes)'
    if _has_missing_accents(text):
        return False, 'tokens-chave sem acentuacao (To, metodo, Nao, etc)'
    n_emojis = _count_emojis(text)
    if n_emojis > 2:
        return False, f'emojis em excesso ({n_emojis} > 2)'
    return True, 'ok'


def generate_dm(lead: dict) -> str:
    """Gera DM personalizada usando o template oficial cravado pelo Chefe.
    Estrategia: 1) Claude Agent SDK (OAuth, sem API key) -> 2) Anthropic API key se houver -> 3) OpenAI fallback -> 4) fallback local.

    Pos-geracao:
    - se tiver travessao: sanitiza programaticamente (substitui por virgula)
    - se faltar [BREAK]: tenta quebrar heuristicamente em 3 partes
    - se ainda invalida apos sanitize: retry ate 2 vezes com instrucao reforcada
    """
    user_prompt = _build_dm_user_prompt(lead)
    reinforced_user_prompt = user_prompt + (
        "\n\nLEMBRETE CRÍTICO: ZERO TRAVESSÃO. Use vírgula ou ponto. "
        "OBRIGATÓRIO 3 partes separadas por [BREAK] em linha própria. "
        "ACENTOS PERFEITOS em PT-BR: 'Tô' (não 'To'), 'método' (não 'metodo'), "
        "'Não sei' (não 'Nao sei'), 'operação', 'Inteligência', 'Agêntica', "
        "'padronização', 'gestão', 'automações', 'agência', 'você', 'também', 'mermão', 'só'. "
        "MÁXIMO 2 EMOJIS NO TOTAL na DM inteira. Use 🤙 no final, mais nada se não precisar. "
        "Sem [BREAK] = rejeitado. Travessão = rejeitado. Sem acentos = rejeitado. 3+ emojis = rejeitado."
    )

    def _try_generate(prompt_to_use: str) -> Optional[str]:
        # 1) Claude Agent SDK (preferido)
        if CLAUDE_SDK_AVAILABLE:
            try:
                text = asyncio.run(_generate_dm_via_claude_sdk(DM_SYSTEM_PROMPT, prompt_to_use))
                if text:
                    return text.strip().strip('"').strip("'")
            except Exception as e:
                log.warning(f'Claude SDK fallback: {e}')

        # 2) Fallback Anthropic API key
        try:
            if ANTHROPIC_API_KEY:
                client = Anthropic(api_key=ANTHROPIC_API_KEY)
                msg = client.messages.create(
                    model='claude-sonnet-4-5-20250929',
                    max_tokens=800,
                    system=DM_SYSTEM_PROMPT,
                    messages=[{'role': 'user', 'content': prompt_to_use}],
                )
                return msg.content[0].text.strip().strip('"').strip("'")
        except Exception as e:
            log.warning(f'Anthropic API failed: {e}')

        # 3) Fallback OpenAI (emergencia)
        try:
            openai_key = os.getenv('OPENAI_API_KEY', '')
            if openai_key:
                with httpx.Client(timeout=30) as c:
                    r = c.post(
                        'https://api.openai.com/v1/chat/completions',
                        headers={'Authorization': f'Bearer {openai_key}', 'Content-Type': 'application/json'},
                        json={
                            'model': 'gpt-4o-mini',
                            'messages': [
                                {'role': 'system', 'content': DM_SYSTEM_PROMPT},
                                {'role': 'user', 'content': prompt_to_use},
                            ],
                            'max_tokens': 800,
                            'temperature': 0.7,
                        },
                    )
                    if r.status_code == 200:
                        return r.json()['choices'][0]['message']['content'].strip().strip('"').strip("'")
                    log.warning(f'OpenAI status {r.status_code}: {r.text[:200]}')
        except Exception as e:
            log.error(f'OpenAI fallback failed: {e}')

        return None

    # Tenta ate 3 vezes (1a com prompt normal, 2a e 3a com reforco se invalida)
    text = None
    for attempt in range(3):
        prompt = user_prompt if attempt == 0 else reinforced_user_prompt
        candidate = _try_generate(prompt)
        if not candidate:
            continue

        # Sanitizacao em pipeline: travessoes -> acentos -> emojis -> [BREAK]
        cleaned = _strip_dashes(candidate)
        cleaned = _normalize_acentos(cleaned)
        # se faltar [BREAK], tenta inserir heuristicamente
        if not _has_break(cleaned):
            forced = _force_break_split(cleaned)
            if _has_break(forced):
                cleaned = forced
        # Trim emojis em excesso (corte programatico, sem retry)
        if _count_emojis(cleaned) > 2:
            cleaned = _trim_excess_emojis(cleaned, max_count=2)

        ok, reason = _validate_dm(cleaned)
        if ok:
            log.info(
                f'DM gerada OK na tentativa {attempt+1}: '
                f'{len(_split_dm_parts(cleaned))} partes, '
                f'{_count_emojis(cleaned)} emojis, sem travessao, com acentos'
            )
            return cleaned
        log.warning(f'DM tentativa {attempt+1} falhou validacao: {reason}. Retrying...')
        text = cleaned  # guarda como ultimo recurso

    # Se sobrou algo da geracao, devolve sanitizado (mesmo que sem [BREAK] ideal)
    if text:
        log.warning('Devolvendo DM apos retries sem validacao perfeita; aplicando sanitizacao final')
        text = _strip_dashes(text)
        text = _normalize_acentos(text)
        if not _has_break(text):
            text = _force_break_split(text)
        if _count_emojis(text) > 2:
            text = _trim_excess_emojis(text, max_count=2)
        return text

    # 4) Fallback local (raiz: nem SDK nem APIs disponiveis)
    nome = (lead.get('display_name') or lead.get('ig_username') or 'cara').split()[0]
    fallback = (
        f"Fala {nome}! Tô vendo teu trabalho e curti demais a proposta.\n"
        "[BREAK]\n"
        "Não sei se tu já está utilizando Inteligência artificial 'Agêntica' na sua rotina. "
        "Olhando o que tu faz, achei que poderia ter uma sinergia boa pra escalar a operação, "
        "ter automações de Instagram e WhatsApp integradas ao CRM num lugar só deve resolver "
        "bastante da dor de gestão e padronização de processos.\n"
        "[BREAK]\n"
        "Se fizer sentido, me avisa aqui que eu te explico melhor (ou a gente marca uma call). "
        "Sem compromisso, só quis deixar a porta aberta. \U0001F919"
    )
    return _strip_dashes(_normalize_acentos(fallback))


# ── DM Send ──
def send_dm(lead: dict, message: str) -> bool:
    """Envia DM via Tandem. Suporta split em multiplas mensagens via [BREAK].
    Cada parte e enviada como mensagem separada no Direct, com jitter 4-12s entre elas.
    Retorna True se TODAS as partes foram entregues."""
    parts = _split_dm_parts(message) or [message]
    log.info(f'send_dm @{lead["ig_username"]}: {len(parts)} partes a enviar')

    if DRY_RUN:
        for i, p in enumerate(parts, 1):
            log.info(f'[DRY_RUN] Part {i}/{len(parts)} to @{lead["ig_username"]}: {p}')
        return False  # nao marca como entregue

    try:
        # Abre direct uma unica vez
        tandem_navigate(f'https://www.instagram.com/direct/new/')
        time.sleep(4)
        # Preenche destinatario
        tandem_type('input[placeholder*="esquisar"]', lead['ig_username'])
        time.sleep(2)
        # Clica no primeiro resultado
        tandem_click('div[role="button"]')
        time.sleep(1)
        # Confirma
        tandem_click('button[type="button"]')
        time.sleep(2)

        # Envia cada parte sequencialmente com jitter 4-12s entre elas
        for idx, part in enumerate(parts, 1):
            try:
                # Foca textarea e digita a parte
                tandem_type('textarea', part)
                time.sleep(0.8)
                tandem_request('POST', '/press-key', {'key': 'Enter'})
                log.info(f'Part {idx}/{len(parts)} sent to @{lead["ig_username"]}')
                # jitter 4-12s entre partes (so se nao for a ultima)
                if idx < len(parts):
                    wait = random.uniform(4.0, 12.0)
                    log.info(f'Aguardando {wait:.1f}s antes da proxima parte (jitter inter-msg)')
                    time.sleep(wait)
                else:
                    time.sleep(1.5)
            except Exception as e:
                log.error(f'Falha enviando part {idx}/{len(parts)} para @{lead["ig_username"]}: {e}')
                telegram_alert(f'Falha part {idx}/{len(parts)} para @{lead["ig_username"]}: {e}')
                return False

        log.info(f'DM completa enviada para @{lead["ig_username"]} ({len(parts)} partes)')
        return True
    except Exception as e:
        log.error(f'send_dm failed: {e}')
        telegram_alert(f'Falha ao enviar DM para @{lead["ig_username"]}: {e}')
        return False


# ── Handoff ──
def handoff_to_clone(lead: dict, dm_text: str):
    """Cria row em dm_contact_profiles para Clone do {{DONO}} detectar.
    Usa contact_id sintetico 'ademir:USERNAME' ate o lead responder pela primeira vez
    no GHL (quando o webhook real chegar com o contact_id verdadeiro)."""
    try:
        notes_payload = {
            'source': 'ademir',
            'dm_inicial': dm_text,
            'briefing': lead.get('briefing'),
            'discovered_at': datetime.now(timezone.utc).isoformat(),
        }
        notes_str = json.dumps(notes_payload, ensure_ascii=False)
        contact_id = f'ademir:{lead["ig_username"]}'
        sql = '''
            INSERT INTO dm_contact_profiles
                (contact_id, contact_name, instagram_username, bio, notes,
                 qualification_stage, first_contact_at, last_contact_at)
            VALUES (%s, %s, %s, %s, %s, 'rapport', NOW(), NOW())
            ON CONFLICT (contact_id) DO UPDATE SET
                instagram_username = EXCLUDED.instagram_username,
                bio = EXCLUDED.bio,
                notes = EXCLUDED.notes,
                last_contact_at = NOW()
        '''
        db_query(
            sql,
            (
                contact_id,
                lead.get('display_name') or lead['ig_username'],
                lead['ig_username'],
                lead.get('bio'),
                notes_str,
            ),
            commit=True,
        )
        log.info(f'Handoff to clone OK for @{lead["ig_username"]}')
    except Exception as e:
        log.error(f'handoff error: {e}')


# ── Main run() ──
def run(triggered_by: str = 'manual', ignore_hours: bool = False) -> dict:
    if not ignore_hours and not is_within_active_hours():
        log.info('Fora do horario ativo (22h-8h BRT). Skip.')
        return {'skipped': 'fora_horario'}

    # Cria run row
    run_row = db_query(
        'INSERT INTO prospect_runs (triggered_by, status) VALUES (%s, %s) RETURNING id',
        (triggered_by, 'running'),
        fetchone=True, commit=True,
    )
    run_id = run_row['id']
    log.info(f'=== RUN START id={run_id} dry_run={DRY_RUN} ===')

    discovered = 0
    qualified = 0
    dms_sent = 0
    errors = []

    # FASE 3: HikerAPI dispensa bio firewall (sem race condition de aba do Tandem).
    # Mantemos validacao leve dentro de analyze_profile_via_api comparando contra MY_OWN_BIO
    # caso ainda esteja capturada de runs anteriores.

    try:
        targets = db_query('SELECT * FROM prospect_targets WHERE active = true', fetchall=True) or []
        if not targets:
            log.info('Nenhum perfil-alvo ativo. Run encerrado.')
            db_query(
                'UPDATE prospect_runs SET finished_at = NOW(), status = %s, leads_discovered = 0 WHERE id = %s',
                ('done', run_id), commit=True,
            )
            return {'run_id': run_id, 'status': 'done', 'note': 'sem targets'}

        all_candidates = []
        for t in targets:
            try:
                cands = discover_for_target(t)
                for u in cands:
                    all_candidates.append((u, t))
            except Exception as e:
                errors.append(f'discover target {t["username"]}: {e}')
                log.error(errors[-1])

        # Filtra ja existentes
        existing = db_query(
            'SELECT ig_username FROM prospect_leads WHERE ig_username = ANY(%s)',
            ([c[0] for c in all_candidates],),
            fetchall=True,
        ) or []
        existing_set = set(r['ig_username'] for r in existing)
        new_candidates = [(u, t) for u, t in all_candidates if u not in existing_set]
        log.info(f'Candidates total: {len(all_candidates)} | new: {len(new_candidates)}')

        # Limit por run (5 = bom para teste; em prod, ajusta via env)
        per_run_cap = int(os.getenv('ADEMIR_PER_RUN_CAP', '5'))
        # Em modo SKIP_DM_ON_DISCOVERY, o limite diario nao se aplica (so importa pra envio).
        # Esse modo e usado quando o objetivo e mapear muitos leads sem enviar DM agora.
        if os.getenv('ADEMIR_SKIP_DM_ON_DISCOVERY', 'false').lower() != 'true':
            per_run_cap = min(per_run_cap, max(0, DAILY_DM_LIMIT - dms_sent_today()))
        new_candidates = new_candidates[:per_run_cap]

        for idx, (username, target) in enumerate(new_candidates):
            # Pausa entre leads (NAO antes do primeiro)
            if idx > 0:
                jitter_lo = max(1, DELAY_BETWEEN_LEADS_SEC - DELAY_JITTER_SEC)
                jitter_hi = DELAY_BETWEEN_LEADS_SEC + DELAY_JITTER_SEC
                wait = random.uniform(jitter_lo, jitter_hi)
                log.info(
                    f'Pausa de {int(wait)}s ({wait/60:.1f}min) antes do proximo lead '
                    f'(@{username}) — variacao base={DELAY_BETWEEN_LEADS_SEC}s +-{DELAY_JITTER_SEC}s'
                )
                time.sleep(wait)
                # Apos pausa, valida janela e limite diario
                if not is_within_active_hours():
                    log.warning(
                        f'Lead @{username} pulado: pausa terminou fora da janela '
                        f'{ACTIVE_START_HOUR}-{ACTIVE_END_HOUR}h BRT (hora atual {now_brt().hour}h)'
                    )
                    continue
                if dms_sent_today() >= DAILY_DM_LIMIT:
                    log.info(
                        f'Limite diario {DAILY_DM_LIMIT} atingido apos pausa. '
                        f'Skip restantes ({len(new_candidates) - idx} leads).'
                    )
                    break
            try:
                # FASE 3: usa HikerAPI por padrao (sem Tandem em discovery/analyze)
                profile = analyze_profile_via_api(username)
                if not profile:
                    continue
                discovered += 1
                # Status inicial
                status = 'discovered'
                if profile['has_offer']:
                    status = 'qualified'
                    qualified += 1

                # Insere lead
                source_type = target['source']
                if target['source'] == 'comentou_em_alvo':
                    source_type = f'comentou_em_alvo:{target["username"]}'

                # sanitiza textos pra evitar surrogates invalidos
                briefing_clean = profile['briefing']
                if isinstance(briefing_clean, dict):
                    for k, v in list(briefing_clean.items()):
                        if isinstance(v, str):
                            briefing_clean[k] = sanitize_text(v)
                    if 'posts' in briefing_clean and isinstance(briefing_clean['posts'], list):
                        for p in briefing_clean['posts']:
                            if isinstance(p, dict) and 'caption' in p:
                                p['caption'] = sanitize_text(p['caption'])
                lead_row = db_query(
                    '''INSERT INTO prospect_leads
                       (ig_username, display_name, bio, external_link, followers_count,
                        following_count, posts_count, is_verified, has_offer, briefing,
                        source_target_id, source_type, status)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (ig_username) DO UPDATE SET briefing = EXCLUDED.briefing
                       RETURNING id''',
                    (
                        profile['ig_username'],
                        sanitize_text(profile.get('display_name')),
                        sanitize_text(profile.get('bio')),
                        sanitize_text(profile.get('external_link')),
                        profile['followers_count'],
                        profile['following_count'], profile['posts_count'],
                        profile['is_verified'], profile['has_offer'], Json(briefing_clean),
                        target['id'], source_type, status,
                    ),
                    fetchone=True, commit=True,
                )
                lead = {**profile, 'id': lead_row['id']}

                # FASE 4: run() so descobre + analisa + gera DM. NAO envia.
                # Envio acontece via send_loop_approved() apos aprovacao manual na dash.
                # Pode pular geracao de DM aqui via env ADEMIR_SKIP_DM_ON_DISCOVERY=true
                # (gera DM apenas no momento da aprovacao, mais alinhado com fluxo Chefe)
                if status == 'qualified':
                    if os.getenv('ADEMIR_SKIP_DM_ON_DISCOVERY', 'false').lower() == 'true':
                        log.info(
                            f'@{lead["ig_username"]} qualificado, DM SERA GERADA NA APROVACAO (skip on discovery). '
                            f'Status final: qualified'
                        )
                    else:
                        dm_text = generate_dm(lead)
                        db_query(
                            'INSERT INTO prospect_dms (lead_id, message, delivered) VALUES (%s,%s,%s)',
                            (lead['id'], dm_text, False), commit=True,
                        )
                        log.info(
                            f'@{lead["ig_username"]} qualificado, DM gerada (aguardando aprovacao manual). '
                            f'Status final: qualified'
                        )

            except Exception as e:
                errors.append(f'lead {username}: {e}')
                log.error(errors[-1])

        # Fecha run
        db_query(
            '''UPDATE prospect_runs
               SET finished_at = NOW(), status = %s,
                   leads_discovered = %s, leads_qualified = %s, dms_sent = %s, errors = %s
               WHERE id = %s''',
            ('done', discovered, qualified, dms_sent, Json(errors), run_id),
            commit=True,
        )
        log.info(f'=== RUN END id={run_id} discovered={discovered} qualified={qualified} dms={dms_sent} ===')
        return {'run_id': run_id, 'discovered': discovered, 'qualified': qualified, 'dms_sent': dms_sent, 'dry_run': DRY_RUN}

    except Exception as e:
        log.error(f'RUN FATAL: {e}')
        errors.append(f'fatal: {e}')
        db_query(
            'UPDATE prospect_runs SET finished_at = NOW(), status = %s, errors = %s WHERE id = %s',
            ('failed', Json(errors), run_id), commit=True,
        )
        telegram_alert(f'Run #{run_id} falhou: {e}')
        return {'run_id': run_id, 'status': 'failed', 'error': str(e)}


# ── Send Loop (FASE 4) ──
def send_loop_approved() -> dict:
    """Loop de envio sob demanda. Pega leads aprovados (status=approved_to_send),
    gera DM se nao tiver, envia via Tandem, marca como dm_sent.

    Respeita:
    - Janela ativa (ACTIVE_START_HOUR..ACTIVE_END_HOUR BRT)
    - DAILY_DM_LIMIT
    - Pausa entre leads (DELAY_BETWEEN_LEADS_SEC +- DELAY_JITTER_SEC)
    - DRY_RUN: nao envia, so loga
    """
    if not is_within_active_hours():
        log.warning(
            f'send_loop_approved: fora da janela {ACTIVE_START_HOUR}-{ACTIVE_END_HOUR}h BRT, abortando'
        )
        return {'ok': False, 'reason': 'fora_horario'}

    leads = db_query(
        '''SELECT id, ig_username, display_name, bio, external_link,
                  followers_count, following_count, posts_count,
                  is_verified, has_offer, niche, briefing
           FROM prospect_leads
           WHERE status = 'approved_to_send'
           ORDER BY discovered_at ASC''',
        fetchall=True,
    ) or []
    if not leads:
        log.info('send_loop_approved: nenhum lead aprovado')
        return {'ok': True, 'sent': 0, 'skipped': 0, 'message': 'nenhum lead aprovado'}

    log.info(f'send_loop_approved: {len(leads)} leads aprovados, dry_run={DRY_RUN}')
    sent = 0
    skipped = 0
    errors = []
    sent_today = dms_sent_today()

    for idx, lead in enumerate(leads):
        # Verifica janela e limite diario antes de cada lead
        if not is_within_active_hours():
            log.warning(f'Janela fechada apos {idx} leads. Abortando.')
            skipped += len(leads) - idx
            break
        if sent_today >= DAILY_DM_LIMIT:
            log.info(f'Limite diario {DAILY_DM_LIMIT} atingido. Skip {len(leads) - idx} restantes.')
            skipped += len(leads) - idx
            break

        # Pausa antes do proximo lead (nao antes do primeiro)
        if idx > 0:
            jitter_lo = max(1, DELAY_BETWEEN_LEADS_SEC - DELAY_JITTER_SEC)
            jitter_hi = DELAY_BETWEEN_LEADS_SEC + DELAY_JITTER_SEC
            wait = random.uniform(jitter_lo, jitter_hi)
            log.info(
                f'send_loop_approved: pausa {int(wait)}s antes de @{lead["ig_username"]} '
                f'(base={DELAY_BETWEEN_LEADS_SEC}s +-{DELAY_JITTER_SEC}s)'
            )
            time.sleep(wait)
            # Revalida apos pausa
            if not is_within_active_hours():
                log.warning(f'Janela fechou durante pausa. @{lead["ig_username"]} pulado.')
                skipped += 1
                continue
            if dms_sent_today() >= DAILY_DM_LIMIT:
                log.info(f'Limite diario atingido durante pausa. @{lead["ig_username"]} pulado.')
                skipped += 1
                break

        try:
            # Busca DM ja gerada (mais recente, nao entregue)
            existing_dm = db_query(
                '''SELECT id, message FROM prospect_dms
                   WHERE lead_id = %s AND delivered = false
                   ORDER BY id DESC LIMIT 1''',
                (lead['id'],), fetchone=True,
            )
            if existing_dm and existing_dm.get('message'):
                dm_text = existing_dm['message']
                dm_id = existing_dm['id']
                log.info(f'send_loop_approved: usando DM existente para @{lead["ig_username"]}')
            else:
                dm_text = generate_dm(lead)
                dm_row = db_query(
                    'INSERT INTO prospect_dms (lead_id, message, delivered) VALUES (%s,%s,%s) RETURNING id',
                    (lead['id'], dm_text, False), fetchone=True, commit=True,
                )
                dm_id = dm_row['id']
                log.info(f'send_loop_approved: DM gerada agora para @{lead["ig_username"]}')

            # Envia
            delivered = False
            if DRY_RUN:
                log.info(f'[DRY_RUN] send_loop_approved nao envia @{lead["ig_username"]}: {dm_text[:80]}')
                # Em DRY_RUN, marca como sent_at p/ logica de UI mas mantem delivered=false
                db_query(
                    "UPDATE prospect_leads SET dm_sent_at = NOW() WHERE id = %s",
                    (lead['id'],), commit=True,
                )
                # NAO marca status como dm_sent em DRY_RUN (deixa qualified/approved_to_send)
                # Para simular o flow, mas sem pular a auditoria humana
            else:
                delivered = send_dm(lead, dm_text)
                db_query(
                    'UPDATE prospect_dms SET delivered = %s WHERE id = %s',
                    (delivered, dm_id), commit=True,
                )
                if delivered:
                    db_query(
                        "UPDATE prospect_leads SET status = 'dm_sent', dm_sent_at = NOW() WHERE id = %s",
                        (lead['id'],), commit=True,
                    )
                    handoff_to_clone(lead, dm_text)
                    sent += 1
                    sent_today += 1
                    # Jitter pos-envio (alem da pausa entre leads)
                    inter_wait = random.randint(8 * 60, 15 * 60)
                    log.info(f'Aguardando {inter_wait}s pos-envio (jitter inter-DM)')
                    time.sleep(inter_wait)
                else:
                    errors.append(f'@{lead["ig_username"]}: send_dm retornou false')
                    log.error(errors[-1])

        except Exception as e:
            errors.append(f'@{lead["ig_username"]}: {e}')
            log.error(f'send_loop_approved erro em @{lead["ig_username"]}: {e}')

    log.info(
        f'send_loop_approved fim: sent={sent} skipped={skipped} errors={len(errors)} '
        f'(dry_run={DRY_RUN})'
    )
    return {
        'ok': True,
        'sent': sent,
        'skipped': skipped,
        'errors': errors,
        'dry_run': DRY_RUN,
    }


# ── HTTP API ──
app = FastAPI()

def require_auth(request: Request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token != DAEMON_TOKEN:
        raise HTTPException(status_code=401, detail='Unauthorized')


@app.get('/health')
def health():
    bal = hiker_balance() if HIKERAPI_AVAILABLE and HIKERAPI_KEY else None
    approved_count = 0
    try:
        row = db_query(
            "SELECT COUNT(*) AS c FROM prospect_leads WHERE status = 'approved_to_send'",
            fetchone=True,
        )
        approved_count = (row or {}).get('c', 0)
    except Exception:
        pass
    return {
        'ok': True,
        'service': 'ademir',
        'phase': '4-approval-flow+whatsapp-capture',
        'dry_run': DRY_RUN,
        'approved_pending_send': approved_count,
        'within_active_hours': is_within_active_hours(),
        'dms_sent_today': dms_sent_today(),
        'delay_between_leads_sec': DELAY_BETWEEN_LEADS_SEC,
        'delay_jitter_sec': DELAY_JITTER_SEC,
        'daily_limit': DAILY_DM_LIMIT,
        'hikerapi': {
            'available': HIKERAPI_AVAILABLE,
            'configured': bool(HIKERAPI_KEY),
            'balance': bal,
        },
    }


_run_lock = threading.Lock()


def _run_in_background(triggered_by: str, ignore_hours: bool):
    """Executa run() em thread separada, segurando o lock ate terminar."""
    try:
        run(triggered_by=triggered_by, ignore_hours=ignore_hours)
    except Exception as e:
        log.error(f'background run error: {e}')
    finally:
        _run_lock.release()


@app.post('/run-now')
def run_now(request: Request):
    require_auth(request)
    if not _run_lock.acquire(blocking=False):
        return {'ok': False, 'message': 'run em andamento'}
    # bypass de horario quando triggered manual via dash
    bypass = request.query_params.get('bypass_hours', 'true').lower() == 'true'
    triggered_by = 'manual'
    # Dispara run em thread separada pra nao bloquear o endpoint (pausa de 20min entre leads)
    t = threading.Thread(
        target=_run_in_background,
        args=(triggered_by, bypass),
        daemon=True,
    )
    t.start()
    return {'ok': True, 'message': 'run iniciado em background', 'dry_run': DRY_RUN}


# ── FASE 4: send loop sob demanda ──
_send_lock = threading.Lock()


def _send_in_background():
    try:
        send_loop_approved()
    except Exception as e:
        log.error(f'background send_loop_approved error: {e}')
    finally:
        _send_lock.release()


@app.post('/run-send-now')
def run_send_now(request: Request):
    """FASE 4: dispara send_loop_approved em background, processando leads aprovados."""
    require_auth(request)
    if not _send_lock.acquire(blocking=False):
        return {'ok': False, 'message': 'send loop ja em andamento'}
    t = threading.Thread(target=_send_in_background, daemon=True)
    t.start()
    return {'ok': True, 'message': 'send loop iniciado em background', 'dry_run': DRY_RUN}


@app.post('/enrich-whatsapp')
def enrich_whatsapp_endpoint(request: Request):
    """FASE 4: pass retroativo. Processa todos os leads sem whatsapp_number,
    extrai do briefing existente (sem chamadas HikerAPI). Retorna stats."""
    require_auth(request)
    leads = db_query(
        'SELECT id, ig_username, briefing FROM prospect_leads WHERE briefing IS NOT NULL',
        fetchall=True,
    ) or []
    enriched = 0
    skipped = 0
    already_has = 0
    for lead in leads:
        try:
            briefing = lead['briefing'] or {}
            if briefing.get('whatsapp_number'):
                already_has += 1
                continue
            wa = extract_whatsapp(briefing)
            briefing['whatsapp_number'] = wa
            db_query(
                'UPDATE prospect_leads SET briefing = %s WHERE id = %s',
                (Json(briefing), lead['id']), commit=True,
            )
            if wa:
                enriched += 1
                log.info(f'[enrich-whatsapp] @{lead["ig_username"]} -> {wa}')
            else:
                skipped += 1
        except Exception as e:
            log.error(f'[enrich-whatsapp] @{lead["ig_username"]} erro: {e}')
            skipped += 1
    return {
        'ok': True,
        'total': len(leads),
        'enriched': enriched,
        'skipped': skipped,
        'already_has': already_has,
    }


# ── FASE 5: geracao em massa de DMs ──
_bulk_dm_lock = threading.Lock()
_bulk_dm_state = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'total': 0,
    'generated': 0,
    'failed': 0,
    'workers': 0,
    'last_error': None,
}


def _bulk_dm_worker(worker_id: int, work_queue, state: dict, state_lock: threading.Lock):
    """Worker thread que consome leads da fila, gera DM e grava em prospect_dms.
    Cada chamada generate_dm() abre seu proprio asyncio loop (asyncio.run dentro da thread)."""
    import queue as _queue
    while True:
        try:
            lead = work_queue.get_nowait()
        except _queue.Empty:
            return
        ig = lead.get('ig_username') or '?'
        try:
            dm_text = generate_dm(lead)
            if not dm_text:
                raise RuntimeError('generate_dm devolveu vazio')
            db_query(
                'INSERT INTO prospect_dms (lead_id, message, delivered) VALUES (%s,%s,%s)',
                (lead['id'], dm_text, False), commit=True,
            )
            with state_lock:
                state['generated'] += 1
                done = state['generated'] + state['failed']
                total = state['total']
                if done % 10 == 0 or done == total:
                    log.info(
                        f'[bulk-dm] progresso {done}/{total} '
                        f'(generated={state["generated"]} failed={state["failed"]})'
                    )
            log.info(f'[bulk-dm w{worker_id}] DM OK @{ig} (id={lead["id"]})')
        except Exception as e:
            with state_lock:
                state['failed'] += 1
                state['last_error'] = f'@{ig}: {e}'
            log.error(f'[bulk-dm w{worker_id}] falha @{ig}: {e}')
        finally:
            work_queue.task_done()


def _bulk_dm_run(workers: int, only_missing: bool):
    """Roda a geracao em massa em background. Bloqueia o lock _bulk_dm_lock ate terminar."""
    import queue as _queue
    state_lock = threading.Lock()
    try:
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
        leads = db_query(sql, fetchall=True) or []
        total = len(leads)
        with state_lock:
            _bulk_dm_state['running'] = True
            _bulk_dm_state['started_at'] = datetime.now(timezone.utc).isoformat()
            _bulk_dm_state['finished_at'] = None
            _bulk_dm_state['total'] = total
            _bulk_dm_state['generated'] = 0
            _bulk_dm_state['failed'] = 0
            _bulk_dm_state['workers'] = workers
            _bulk_dm_state['last_error'] = None
        log.info(f'[bulk-dm] start total={total} workers={workers} only_missing={only_missing}')
        if total == 0:
            log.info('[bulk-dm] nenhum lead a processar; encerrando')
            return
        work_queue = _queue.Queue()
        for ld in leads:
            work_queue.put(ld)
        threads = []
        for i in range(max(1, workers)):
            t = threading.Thread(
                target=_bulk_dm_worker,
                args=(i + 1, work_queue, _bulk_dm_state, state_lock),
                daemon=True,
                name=f'bulk-dm-{i+1}',
            )
            t.start()
            threads.append(t)
        for t in threads:
            t.join()
        with state_lock:
            _bulk_dm_state['finished_at'] = datetime.now(timezone.utc).isoformat()
            gen = _bulk_dm_state['generated']
            fail = _bulk_dm_state['failed']
        log.info(f'[bulk-dm] fim total={total} generated={gen} failed={fail}')
        try:
            telegram_alert(f'Geracao em massa de DMs terminou: {gen}/{total} OK, {fail} falhas')
        except Exception:
            pass
    except Exception as e:
        log.error(f'[bulk-dm] erro fatal no run: {e}')
    finally:
        _bulk_dm_state['running'] = False
        _bulk_dm_lock.release()


@app.post('/generate-all-dms')
async def generate_all_dms_endpoint(request: Request):
    """FASE 5: gera DMs em massa para todos os leads qualified sem prospect_dms.
    Body opcional: {"workers": 4, "only_missing": true}.
    - workers (default 4): numero de threads paralelas chamando generate_dm().
    - only_missing (default true): se true, pula leads que ja tem prospect_dms.
    Resposta imediata; processamento roda em background. Use /bulk-dm-status pra acompanhar."""
    require_auth(request)
    if not _bulk_dm_lock.acquire(blocking=False):
        return {'ok': False, 'message': 'geracao em massa ja em andamento'}
    try:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        workers = int(body.get('workers') or 4)
        if workers < 1:
            workers = 1
        if workers > 8:
            workers = 8  # teto de seguranca pra nao sobrecarregar Claude SDK
        only_missing = bool(body.get('only_missing', True))
        # Conta antecipadamente pra resposta
        if only_missing:
            row = db_query(
                "SELECT COUNT(*) AS c FROM prospect_leads l "
                "WHERE l.status = 'qualified' "
                "  AND NOT EXISTS (SELECT 1 FROM prospect_dms d WHERE d.lead_id = l.id)",
                fetchone=True,
            )
        else:
            row = db_query(
                "SELECT COUNT(*) AS c FROM prospect_leads WHERE status = 'qualified'",
                fetchone=True,
            )
        total = (row or {}).get('c', 0)
        t = threading.Thread(
            target=_bulk_dm_run,
            args=(workers, only_missing),
            daemon=True,
            name='bulk-dm-driver',
        )
        t.start()
        return {
            'ok': True,
            'started': total,
            'workers': workers,
            'only_missing': only_missing,
            'message': 'geracao em massa iniciada em background; consulte /bulk-dm-status',
        }
    except Exception as e:
        # libera o lock se algo deu errado antes do thread iniciar
        try:
            _bulk_dm_lock.release()
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=str(e))


@app.get('/bulk-dm-status')
def bulk_dm_status_endpoint(request: Request):
    """Retorna o estado atual da geracao em massa de DMs."""
    require_auth(request)
    snap = dict(_bulk_dm_state)
    # contagem fresca no banco
    try:
        row = db_query('SELECT COUNT(*) AS c FROM prospect_dms', fetchone=True)
        snap['db_dm_count'] = (row or {}).get('c', 0)
    except Exception:
        snap['db_dm_count'] = None
    return snap


# ── Cron loop (sorteio aleatorio dos 20 horarios na janela ativa) ──
_scheduled_today = []  # lista de datetimes (BRT) sorteados pra hoje
_scheduled_day_key = None


def _sample_daily_schedule(day_brt: datetime) -> list:
    """Sorteia DAILY_DM_LIMIT horarios aleatorios entre ACTIVE_START_HOUR e ACTIVE_END_HOUR (segundos do dia).
    Retorna lista de datetimes BRT timezone-aware."""
    start_s = ACTIVE_START_HOUR * 3600
    end_s = ACTIVE_END_HOUR * 3600
    # se faltar muito espaco vs limite, evita travar
    pool_size = max(end_s - start_s, 1)
    n = min(DAILY_DM_LIMIT, pool_size)
    seconds_offsets = sorted(random.sample(range(start_s, end_s), n))
    base = day_brt.replace(hour=0, minute=0, second=0, microsecond=0)
    return [base + timedelta(seconds=s) for s in seconds_offsets]


def cron_loop():
    """Loop continuo. Uma vez por dia (>=9h BRT), sorteia DAILY_DM_LIMIT horarios distribuidos
    aleatoriamente entre 9h-18h. Cada horario dispara 1 lead individualmente.
    Se um horario cair fora da janela ativa, faz skip + log."""
    global _scheduled_today, _scheduled_day_key

    while True:
        try:
            n = now_brt()
            today_key = n.strftime('%Y-%m-%d')

            # Sorteia o dia uma unica vez quando entrar na janela ativa pela primeira vez
            if today_key != _scheduled_day_key and n.hour >= ACTIVE_START_HOUR and n.hour < ACTIVE_END_HOUR:
                _scheduled_today = _sample_daily_schedule(n)
                _scheduled_day_key = today_key
                upcoming = [t.strftime('%H:%M:%S') for t in _scheduled_today[:5]]
                log.info(
                    f'Cron sorteou {len(_scheduled_today)} horarios pra {today_key} '
                    f'janela {ACTIVE_START_HOUR}-{ACTIVE_END_HOUR}h BRT. Primeiros: {upcoming}'
                )

            # Verifica se algum horario agendado ja venceu
            if _scheduled_today:
                due = [t for t in _scheduled_today if t <= n]
                for t in due:
                    _scheduled_today.remove(t)
                    if not is_within_active_hours():
                        log.warning(f'Skip horario {t.strftime("%H:%M")} fora da janela {ACTIVE_START_HOUR}-{ACTIVE_END_HOUR}h')
                        continue
                    if dms_sent_today() >= DAILY_DM_LIMIT:
                        log.info(f'Limite diario {DAILY_DM_LIMIT} atingido. Skip restantes.')
                        _scheduled_today = []
                        break
                    log.info(f'Cron trigger horario {t.strftime("%H:%M:%S")}')
                    if _run_lock.acquire(blocking=False):
                        try:
                            # cada slot processa apenas 1 lead (per_run_cap=1) pra distribuir bem
                            os.environ['ADEMIR_PER_RUN_CAP'] = '1'
                            run(triggered_by='cron-slot')
                        finally:
                            _run_lock.release()

            time.sleep(20)
        except Exception as e:
            log.error(f'cron_loop error: {e}')
            time.sleep(30)


def main():
    log.info(f'Ademir starting on port {DAEMON_PORT} | DRY_RUN={DRY_RUN}')
    # Drainer da fila SQLite (writes pendentes durante quedas do tunnel)
    if DB_QUEUE_AVAILABLE:
        try:
            db_queue.ensure_drainer_running(connect_fn=db_conn)
        except Exception as e:
            log.warning(f'db_queue drainer nao subiu: {e}')
    # Cron em thread separada (pode ser desativado via env DISABLE_CRON=true)
    if os.getenv('ADEMIR_DISABLE_CRON', 'false').lower() == 'true':
        log.info('ADEMIR_DISABLE_CRON=true: cron loop NAO sera iniciado (apenas /run-now manual)')
    else:
        t = threading.Thread(target=cron_loop, daemon=True)
        t.start()
    uvicorn.run(app, host='127.0.0.1', port=DAEMON_PORT, log_level='warning')


if __name__ == '__main__':
    main()
