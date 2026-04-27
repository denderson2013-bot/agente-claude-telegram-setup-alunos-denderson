#!/usr/bin/env python3
"""Agent Manager - Painel unificado de gerenciamento de agentes SDR
Porta: 3600 | Frontend + API
"""
import json, os, shutil, subprocess, time, io, secrets, hashlib, hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn
import httpx

# Carrega .env do naia-agent (GOOGLE_MAPS_API_KEY etc.)
try:
    _env_path = Path('/opt/naia-agent/.env')
    if _env_path.exists():
        for _line in _env_path.read_text().splitlines():
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _k, _v = _line.split('=', 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except Exception:
    pass

BRT = timezone(timedelta(hours=-3))
# Per-agent script directory (Naia Rita owns 3, 4 / Naia {{DONO}} owns the rest)
NAIA_AGENT_DIR = Path('/opt/naia-agent/scripts/agents')
NAIA_RITA_DIR  = Path('/opt/naia-rita/scripts/agents')
AGENT_DIR_BY_ID = {3: NAIA_RITA_DIR, 4: NAIA_RITA_DIR}

def agents_dir_for(agent_id):
    return AGENT_DIR_BY_ID.get(int(agent_id), NAIA_AGENT_DIR)

# Default for legacy code that has no agent context (agent creation, etc.)
AGENTS_DIR    = NAIA_AGENT_DIR
TEMPLATE_PATH = NAIA_AGENT_DIR / 'template.py'
BASE_PORT = 3501

# ── Auth ──
AUTH_EMAIL = '{{EMAIL_DONO}}'
AUTH_PASSWORD = '{{SENHA_PADRAO}}'
AUTH_SECRET = 'agents-dashboard-secret-2026'
active_tokens = set()

def generate_token():
    return hashlib.sha256(f'{AUTH_SECRET}{time.time()}{secrets.token_hex(16)}'.encode()).hexdigest()

def get_date_filter(request: Request, column='created_at'):
    """Build SQL date filter clause from request query params (period or from/to)."""
    period = request.query_params.get('period', '7d')
    date_from = request.query_params.get('from')
    date_to = request.query_params.get('to')
    tz = "AT TIME ZONE 'America/Sao_Paulo'"
    if date_from and date_to:
        return f"AND ({column} {tz})::date >= '{date_from}' AND ({column} {tz})::date <= '{date_to}'"
    elif period == 'today':
        return f"AND ({column} {tz})::date = (NOW() {tz})::date"
    elif period == 'yesterday':
        return f"AND ({column} {tz})::date = ((NOW() {tz}) - INTERVAL '1 day')::date"
    elif period == '7d':
        return f"AND {column} >= NOW() - INTERVAL '7 days'"
    elif period == '30d':
        return f"AND {column} >= NOW() - INTERVAL '30 days'"
    elif period == 'all':
        return ""
    return f"AND {column} >= NOW() - INTERVAL '7 days'"

def log(msg):
    ts = datetime.now(BRT).strftime('%H:%M:%S')
    print(f'[{ts}] [AgentManager] {msg}', flush=True)

# ── Database ──
def get_db():
    return psycopg2.connect(host='127.0.0.1', user='n8n', password=os.getenv('PG_PASS', '{{POSTGRES_PASSWORD}}'), dbname='naia_memory')

def db_query(sql, params=None, fetchone=False, fetchall=False, commit=False):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params or ())
    result = None
    if fetchone:
        result = cur.fetchone()
    elif fetchall:
        result = cur.fetchall()
    if commit:
        conn.commit()
    cur.close()
    conn.close()
    return result

def init_db():
    """Run migrations on startup"""
    conn = get_db()
    cur = conn.cursor()
    migrations = [
        "ALTER TABLE sdr_agents ADD COLUMN IF NOT EXISTS receive_method VARCHAR(20) DEFAULT 'webhook'",
        "ALTER TABLE sdr_agents ADD COLUMN IF NOT EXISTS send_method VARCHAR(20) DEFAULT 'api'",
        "ALTER TABLE sdr_agents ADD COLUMN IF NOT EXISTS send_webhook_url TEXT",
        "ALTER TABLE sdr_agents ADD COLUMN IF NOT EXISTS receive_api_endpoint TEXT",
        """CREATE TABLE IF NOT EXISTS sdr_agent_files (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER REFERENCES sdr_agents(id) ON DELETE CASCADE,
            filename VARCHAR(255) NOT NULL,
            file_type VARCHAR(10) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        """CREATE TABLE IF NOT EXISTS sdr_agent_sales (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER REFERENCES sdr_agents(id) ON DELETE CASCADE,
            platform VARCHAR(50),
            product VARCHAR(255),
            amount DECIMAL(10,2) DEFAULT 0,
            buyer_name VARCHAR(255),
            buyer_email VARCHAR(255),
            transaction_id VARCHAR(255),
            status VARCHAR(20) DEFAULT 'approved',
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
        "ALTER TABLE sdr_agents ADD COLUMN IF NOT EXISTS sales_webhook_secret VARCHAR(100)",
        "ALTER TABLE sdr_agent_sales ADD COLUMN IF NOT EXISTS buyer_phone VARCHAR(50)",
        """CREATE TABLE IF NOT EXISTS sdr_cart_abandonments (
            id SERIAL PRIMARY KEY,
            agent_id INTEGER REFERENCES sdr_agents(id) ON DELETE CASCADE,
            platform VARCHAR(50),
            product VARCHAR(255),
            buyer_name VARCHAR(255),
            buyer_email VARCHAR(255),
            buyer_phone VARCHAR(50),
            event_type VARCHAR(50),
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""",
    ]
    for sql in migrations:
        try:
            cur.execute(sql)
        except Exception as e:
            log(f'Migration warning: {e}')
            conn.rollback()
            continue
    conn.commit()
    cur.close()
    conn.close()
    log('DB migrations applied')

def next_available_port():
    row = db_query('SELECT MAX(port) as max_port FROM sdr_agents', fetchone=True)
    max_port = row['max_port'] if row and row['max_port'] else BASE_PORT - 1
    return max(max_port + 1, BASE_PORT)

# ── PM2 helpers ──
def pm2_start(agent_id):
    base = agents_dir_for(agent_id)
    # Prefer agent-N.py if it exists, otherwise fall back to template.py
    candidate = base / f'agent-{agent_id}.py'
    script = candidate if candidate.exists() else (base / 'template.py')
    name = f'sdr-agent-{agent_id}'
    result = subprocess.run(
        ['pm2', 'start', str(script), '--name', name, '--interpreter', 'python3', '--', str(agent_id)],
        capture_output=True, text=True
    )
    log(f'PM2 start {name}: {result.returncode}')
    try:
        info = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True)
        procs = json.loads(info.stdout)
        for p in procs:
            if p.get('name') == name:
                return p.get('pid', 0)
    except:
        pass
    return 0

def pm2_stop(agent_id):
    name = f'sdr-agent-{agent_id}'
    subprocess.run(['pm2', 'stop', name], capture_output=True, text=True)
    subprocess.run(['pm2', 'delete', name], capture_output=True, text=True)
    log(f'PM2 stopped {name}')

def pm2_status(agent_id, port=None):
    # First check if port is actually listening (most reliable)
    if port:
        try:
            check = subprocess.run(['ss', '-tlnp', f'sport = :{port}'], capture_output=True, text=True, timeout=3)
            if str(port) in check.stdout and 'LISTEN' in check.stdout:
                return 'online'
        except:
            pass
    # Then check PM2 by name
    name = f'sdr-agent-{agent_id}'
    try:
        info = subprocess.run(['pm2', 'jlist'], capture_output=True, text=True)
        procs = json.loads(info.stdout)
        for p in procs:
            if p.get('name') == name:
                return p.get('pm2_env', {}).get('status', 'unknown')
    except:
        pass
    return 'stopped'

# ── Agent file generation ──
def generate_agent_files(agent_id, data):
    config_path = agents_dir_for(agent_id) / f'config-{agent_id}.json'
    with open(config_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    script_path = agents_dir_for(agent_id) / f'agent-{agent_id}.py'
    shutil.copy2(TEMPLATE_PATH, script_path)
    os.chmod(script_path, 0o755)
    log(f'Generated files for agent {agent_id}')

# ── PDF text extraction ──
def extract_pdf_text(file_bytes):
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        text = ''
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text += t + '\n'
        return text.strip()
    except Exception as e:
        log(f'PyPDF2 failed: {e}, trying pdftotext')
        try:
            import tempfile
            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(file_bytes)
                tmp_path = tmp.name
            result = subprocess.run(['pdftotext', tmp_path, '-'], capture_output=True, text=True, timeout=30)
            os.unlink(tmp_path)
            return result.stdout.strip()
        except Exception as e2:
            log(f'pdftotext also failed: {e2}')
            return ''

# ── FastAPI ──
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

# ── Prospeccao B2B (Google Maps Places) ──
try:
    import b2b_routes
    b2b_routes.init(db_query, log)
    app.include_router(b2b_routes.router)
    log('B2B routes registradas')
except Exception as _b2b_e:
    log(f'B2B routes ERRO ao registrar: {_b2b_e}')

@app.on_event("startup")
async def startup_event():
    init_db()

# ── Auth Routes ──
@app.post('/api/login')
async def login(request: Request):
    body = await request.json()
    email = body.get('email', '')
    password = body.get('password', '')
    if email == AUTH_EMAIL and password == AUTH_PASSWORD:
        token = generate_token()
        active_tokens.add(token)
        return {'ok': True, 'token': token}
    return JSONResponse(status_code=401, content={'error': 'Email ou senha incorretos'})

@app.get('/api/check-auth')
async def check_auth(request: Request):
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token in active_tokens:
        return {'ok': True}
    return JSONResponse(status_code=401, content={'error': 'Não autorizado'})

# ── Auth Middleware ──
from starlette.middleware.base import BaseHTTPMiddleware

class AuthMiddleware(BaseHTTPMiddleware):
    OPEN_PATHS = {'/', '/health', '/api/login', '/api/check-auth'}

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Public paths
        if path in self.OPEN_PATHS:
            return await call_next(request)
        # Webhooks are public
        if path.startswith('/webhook/'):
            return await call_next(request)
        # Sales webhooks are public
        if '/sales/webhook' in path:
            return await call_next(request)
        # Protect all /api/* routes
        if path.startswith('/api/'):
            token = request.headers.get('Authorization', '').replace('Bearer ', '')
            if token not in active_tokens:
                return JSONResponse(status_code=401, content={'error': 'Não autorizado'})
        return await call_next(request)

app.add_middleware(AuthMiddleware)

# ── API Routes: Agents ──
@app.get('/api/agents')
async def list_agents():
    agents = db_query('SELECT * FROM sdr_agents ORDER BY created_at DESC', fetchall=True)
    for a in agents:
        a['created_at'] = str(a['created_at'])
        a['updated_at'] = str(a['updated_at'])
        if a['status'] == 'active':
            real = pm2_status(a['id'], a.get('port'))
            if real not in ('online', 'launching'):
                db_query('UPDATE sdr_agents SET status = %s WHERE id = %s', ('error', a['id']), commit=True)
                a['status'] = 'error'
    return agents

@app.get('/api/agents/{agent_id}')
async def get_agent(agent_id: int):
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    agent['created_at'] = str(agent['created_at'])
    agent['updated_at'] = str(agent['updated_at'])
    agent['pm2_status'] = pm2_status(agent_id, agent.get('port'))
    return agent

@app.post('/api/agents')
async def create_agent(request: Request):
    data = await request.json()
    port = next_available_port()
    row = db_query(
        '''INSERT INTO sdr_agents (name, company, webhook_url, personality, products, links,
           blocked_names, spin_flow, calendar_id, ghl_api_key, ghl_location_id, port, status,
           receive_method, send_method, send_webhook_url, receive_api_endpoint)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'inactive',%s,%s,%s,%s)
           RETURNING id''',
        (data.get('name'), data.get('company'), data.get('webhook_url'),
         data.get('personality'), data.get('products'), data.get('links'),
         data.get('blocked_names'), data.get('spin_flow'), data.get('calendar_id'),
         data.get('ghl_api_key'), data.get('ghl_location_id'), port,
         data.get('receive_method', 'webhook'), data.get('send_method', 'api'),
         data.get('send_webhook_url'), data.get('receive_api_endpoint')),
        fetchone=True, commit=True
    )
    agent_id = row['id']
    config = {**data, 'port': port, 'id': agent_id}
    generate_agent_files(agent_id, config)
    log(f'Agent created: {data.get("name")} (id={agent_id}, port={port})')
    return {'id': agent_id, 'port': port, 'status': 'inactive'}

@app.put('/api/agents/{agent_id}')
async def update_agent(agent_id: int, request: Request):
    data = await request.json()
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    db_query(
        '''UPDATE sdr_agents SET name=%s, company=%s, webhook_url=%s, personality=%s,
           products=%s, links=%s, blocked_names=%s, spin_flow=%s, calendar_id=%s,
           ghl_api_key=%s, ghl_location_id=%s, receive_method=%s, send_method=%s,
           send_webhook_url=%s, receive_api_endpoint=%s, updated_at=NOW()
           WHERE id=%s''',
        (data.get('name', agent['name']), data.get('company', agent['company']),
         data.get('webhook_url', agent['webhook_url']), data.get('personality', agent['personality']),
         data.get('products', agent['products']), data.get('links', agent['links']),
         data.get('blocked_names', agent['blocked_names']), data.get('spin_flow', agent['spin_flow']),
         data.get('calendar_id', agent['calendar_id']), data.get('ghl_api_key', agent['ghl_api_key']),
         data.get('ghl_location_id', agent['ghl_location_id']),
         data.get('receive_method', agent.get('receive_method', 'webhook')),
         data.get('send_method', agent.get('send_method', 'api')),
         data.get('send_webhook_url', agent.get('send_webhook_url')),
         data.get('receive_api_endpoint', agent.get('receive_api_endpoint')),
         agent_id),
        commit=True
    )
    config = {**data, 'port': agent['port'], 'id': agent_id}
    generate_agent_files(agent_id, config)
    if agent['status'] == 'active':
        pm2_stop(agent_id)
        pid = pm2_start(agent_id)
        db_query('UPDATE sdr_agents SET pid=%s WHERE id=%s', (pid, agent_id), commit=True)
    return {'id': agent_id, 'updated': True}

@app.delete('/api/agents/{agent_id}')
async def delete_agent(agent_id: int):
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    if agent['status'] == 'active':
        pm2_stop(agent_id)
    db_query('DELETE FROM sdr_channels WHERE agent_id = %s', (agent_id,), commit=True)
    db_query('DELETE FROM sdr_agents WHERE id = %s', (agent_id,), commit=True)
    for f in [agents_dir_for(agent_id) / f'agent-{agent_id}.py', agents_dir_for(agent_id) / f'config-{agent_id}.json']:
        if f.exists():
            f.unlink()
    return {'deleted': True}

@app.post('/api/agents/{agent_id}/start')
async def start_agent(agent_id: int):
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    config_path = agents_dir_for(agent_id) / f'config-{agent_id}.json'
    if not config_path.exists():
        config = {
            'name': agent['name'], 'company': agent['company'], 'port': agent['port'],
            'webhook_url': agent['webhook_url'], 'personality': agent['personality'],
            'products': agent['products'], 'links': agent['links'],
            'blocked_names': agent['blocked_names'], 'spin_flow': agent['spin_flow'],
            'calendar_id': agent['calendar_id'], 'ghl_api_key': agent['ghl_api_key'],
            'ghl_location_id': agent['ghl_location_id'], 'id': agent_id,
            'receive_method': agent.get('receive_method', 'webhook'),
            'send_method': agent.get('send_method', 'api'),
            'send_webhook_url': agent.get('send_webhook_url', ''),
            'receive_api_endpoint': agent.get('receive_api_endpoint', ''),
        }
        generate_agent_files(agent_id, config)
    pid = pm2_start(agent_id)
    db_query('UPDATE sdr_agents SET status=%s, pid=%s, updated_at=NOW() WHERE id=%s',
             ('active', pid, agent_id), commit=True)
    return {'status': 'active', 'pid': pid}

@app.post('/api/agents/{agent_id}/stop')
async def stop_agent(agent_id: int):
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    pm2_stop(agent_id)
    db_query('UPDATE sdr_agents SET status=%s, pid=NULL, updated_at=NOW() WHERE id=%s',
             ('inactive', agent_id), commit=True)
    return {'status': 'inactive'}

@app.get('/api/agents/{agent_id}/stats')
async def agent_stats(agent_id: int):
    agent = db_query('SELECT messages_in, messages_out, contacts, status FROM sdr_agents WHERE id = %s',
                     (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    agent['pm2_status'] = pm2_status(agent_id, agent.get('port'))
    return agent

# ── API Routes: Training Files ──
@app.post('/api/agents/{agent_id}/files')
async def upload_file(agent_id: int, file: UploadFile = File(...)):
    agent = db_query('SELECT id FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})

    filename = file.filename or 'unknown'
    ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if ext not in ('pdf', 'md', 'txt'):
        return JSONResponse(status_code=400, content={'error': 'Tipo de arquivo nao suportado. Use .pdf, .md ou .txt'})

    file_bytes = await file.read()

    if ext in ('txt', 'md'):
        content = file_bytes.decode('utf-8', errors='replace')
    elif ext == 'pdf':
        content = extract_pdf_text(file_bytes)
        if not content:
            return JSONResponse(status_code=400, content={'error': 'Nao foi possivel extrair texto do PDF'})
    else:
        content = ''

    row = db_query(
        'INSERT INTO sdr_agent_files (agent_id, filename, file_type, content) VALUES (%s, %s, %s, %s) RETURNING id, created_at',
        (agent_id, filename, ext, content), fetchone=True, commit=True
    )
    log(f'File uploaded for agent {agent_id}: {filename} ({ext}, {len(content)} chars)')
    return {'id': row['id'], 'filename': filename, 'file_type': ext, 'created_at': str(row['created_at'])}

@app.get('/api/agents/{agent_id}/files')
async def list_files(agent_id: int):
    files = db_query(
        'SELECT id, filename, file_type, LENGTH(content) as size, created_at FROM sdr_agent_files WHERE agent_id = %s ORDER BY created_at DESC',
        (agent_id,), fetchall=True
    )
    for f in files:
        f['created_at'] = str(f['created_at'])
    return files or []

@app.delete('/api/agents/{agent_id}/files/{file_id}')
async def delete_file(agent_id: int, file_id: int):
    db_query('DELETE FROM sdr_agent_files WHERE id = %s AND agent_id = %s', (file_id, agent_id), commit=True)
    return {'deleted': True}

# ── API Routes: Dashboard ──
@app.get('/api/dashboard')
async def dashboard():
    stats = db_query('''SELECT
        COUNT(*) as total,
        COUNT(*) FILTER (WHERE status = 'active') as active,
        SUM(messages_in) as total_in,
        SUM(messages_out) as total_out,
        SUM(contacts) as total_contacts
        FROM sdr_agents''', fetchone=True)
    return {
        'total_agents': stats['total'] or 0,
        'active_agents': stats['active'] or 0,
        'total_messages_in': stats['total_in'] or 0,
        'total_messages_out': stats['total_out'] or 0,
        'total_contacts': stats['total_contacts'] or 0
    }

@app.get('/api/dashboard/stats')
async def dashboard_stats(request: Request):
    date_filter = get_date_filter(request, 'created_at')
    # Agent counts from sdr_agents
    agent_stats = db_query('''SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE status = 'active') as active FROM sdr_agents''', fetchone=True)
    # Real message stats from dm_conversations with period filter
    msg_stats = db_query(f'''SELECT
        COUNT(*) FILTER (WHERE direction='inbound') as total_in,
        COUNT(*) FILTER (WHERE direction='outbound') as total_out,
        COUNT(DISTINCT contact_id) as total_contacts
        FROM dm_conversations WHERE 1=1 {date_filter}''', fetchone=True)
    total_in = int(msg_stats['total_in'] or 0)
    total_out = int(msg_stats['total_out'] or 0)
    stats = {**agent_stats, **msg_stats}
    response_rate = round((total_out / total_in * 100), 1) if total_in > 0 else 0

    # Sales stats filtered by period
    sales_row = db_query(f'''SELECT
        COUNT(*) as total_sales,
        COALESCE(SUM(amount), 0) as total_amount
        FROM sdr_agent_sales WHERE status = 'approved' {date_filter}''', fetchone=True)

    # By hour distribution (from dm_conversations if exists, fallback to empty)
    by_hour = {}
    try:
        hours = db_query('''SELECT EXTRACT(HOUR FROM created_at)::int as hour, COUNT(*) as cnt
            FROM dm_conversations GROUP BY hour ORDER BY hour''', fetchall=True) or []
        for h in hours:
            by_hour[str(h['hour'])] = h['cnt']
    except:
        pass

    # Weekly stats (last 4 weeks)
    weekly = []
    try:
        weeks = db_query('''SELECT
            date_trunc('week', created_at) as week_start,
            COUNT(*) FILTER (WHERE direction = 'inbound') as msgs_in,
            COUNT(*) FILTER (WHERE direction = 'outbound') as msgs_out
            FROM dm_conversations
            WHERE created_at >= NOW() - INTERVAL '28 days'
            GROUP BY week_start ORDER BY week_start''', fetchall=True) or []
        for w in weeks:
            weekly.append({'week': str(w['week_start'])[:10], 'msgs_in': w['msgs_in'] or 0, 'msgs_out': w['msgs_out'] or 0})
    except:
        pass
    # Pad to 4 weeks if needed
    while len(weekly) < 4:
        weekly.insert(0, {'week': '', 'msgs_in': 0, 'msgs_out': 0})

    return {
        'total_agents': stats['total'] or 0,
        'total_active': stats['active'] or 0,
        'total_messages_in': total_in,
        'total_messages_out': total_out,
        'total_contacts': int(stats['total_contacts'] or 0),
        'response_rate': response_rate,
        'sales_today': sales_row['total_sales'] or 0,
        'amount_today': float(sales_row['total_amount'] or 0),
        'sales_total': sales_row['total_sales'] or 0,
        'amount_total': float(sales_row['total_amount'] or 0),
        'by_hour': by_hour,
        'weekly': weekly,
    }

@app.get('/api/dashboard/recent')
async def dashboard_recent(request: Request):
    agents = db_query('''SELECT id, name, company, messages_in, messages_out, contacts, status, updated_at
        FROM sdr_agents ORDER BY updated_at DESC LIMIT 15''', fetchall=True)
    results = []
    for a in agents:
        results.append({
            'id': a['id'],
            'agent_name': a['name'],
            'company': a['company'] or '',
            'messages_in': a['messages_in'] or 0,
            'messages_out': a['messages_out'] or 0,
            'contacts': a['contacts'] or 0,
            'status': a['status'],
            'updated_at': str(a['updated_at'])
        })
    return results

# ── API Routes: Agent Individual Dashboard ──
@app.get('/api/agents/{agent_id}/dashboard')
async def agent_dashboard(agent_id: int, request: Request):
    agent = db_query('SELECT * FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})

    date_filter = get_date_filter(request, 'created_at')

    # All metrics from dm_conversations with period filter
    # Filter by agent_id (default 2 for Clone, specific for others)
    agent_filter = f"AND agent_id = {agent_id}" if agent_id != 2 else "AND (agent_id = 2 OR agent_id IS NULL)"

    stats_row = db_query(f'''SELECT
        COUNT(*) FILTER (WHERE direction='inbound') as msgs_in,
        COUNT(*) FILTER (WHERE direction='outbound') as msgs_out,
        COUNT(DISTINCT contact_id) as contacts,
        COUNT(DISTINCT contact_id) FILTER (WHERE direction='outbound' AND message LIKE '%%http%%') as links_sent
        FROM dm_conversations WHERE 1=1 {agent_filter} {date_filter}''', fetchone=True)

    total_in = stats_row['msgs_in'] or 0
    total_out = stats_row['msgs_out'] or 0
    total_contacts = stats_row['contacts'] or 0
    links_sent = stats_row['links_sent'] or 0
    response_rate = round((total_out / total_in * 100), 1) if total_in > 0 else 0

    # Warm conversations: contacts with 2+ inbound messages in the period
    warm = db_query(f'''SELECT COUNT(*) as c FROM (
        SELECT contact_id FROM dm_conversations WHERE direction='inbound' {agent_filter} {date_filter}
        GROUP BY contact_id HAVING COUNT(*) >= 2
    ) sub''', fetchone=True)['c'] or 0

    # Recent conversations with period filter
    recent_convos = []
    try:
        recent_convos = db_query(f'''
            SELECT contact_id, contact_name, direction, message, created_at
            FROM dm_conversations WHERE 1=1 {agent_filter} {date_filter}
            ORDER BY created_at DESC LIMIT 50
        ''', fetchall=True) or []
        for c in recent_convos:
            c['created_at'] = str(c['created_at'])
    except:
        pass

    # Contact profiles with period filter
    contact_profiles = []
    try:
        contact_profiles = db_query(f'''
            SELECT contact_id, contact_name, COUNT(*) as messages_count, MAX(created_at) as last_contact_at
            FROM dm_conversations WHERE 1=1 {agent_filter} {date_filter}
            GROUP BY contact_id, contact_name
            ORDER BY last_contact_at DESC LIMIT 30
        ''', fetchall=True) or []
        for cp in contact_profiles:
            cp['last_contact_at'] = str(cp['last_contact_at']) if cp.get('last_contact_at') else ''
            cp['stage'] = 'active'
    except:
        pass

    return {
        'agent': {
            'id': agent['id'],
            'name': agent['name'],
            'company': agent['company'] or '',
            'status': agent['status'],
            'port': agent['port'],
            'avatar_url': agent.get('avatar_url', ''),
        },
        'stats': {
            'messages_in': total_in,
            'messages_out': total_out,
            'contacts': total_contacts,
            'response_rate': response_rate,
            'conversations': total_contacts,
            'avg_messages': round((total_in + total_out) / max(total_contacts, 1), 1),
            'links_sent': links_sent,
            'warm_conversations': warm,
            'appointments': 0,
        },
        'recent_conversations': recent_convos[:20],
        'contact_profiles': contact_profiles,
    }

# ── API Routes: Channels ──
@app.get('/api/channels')
async def list_channels():
    channels = db_query('''SELECT c.*, a.name as agent_name
        FROM sdr_channels c
        LEFT JOIN sdr_agents a ON a.id = c.agent_id
        ORDER BY c.created_at DESC''', fetchall=True)
    for c in channels:
        c['created_at'] = str(c['created_at'])
    return channels

@app.post('/api/channels')
async def create_channel(request: Request):
    data = await request.json()
    agent_id = data.get('agent_id')
    channel_type = data.get('channel_type', 'instagram')
    webhook_url = f'https://agents.{{DOMINIO_AI}}/webhook/{agent_id}'
    row = db_query(
        '''INSERT INTO sdr_channels (agent_id, channel_type, webhook_url, status)
           VALUES (%s, %s, %s, 'active') RETURNING id''',
        (agent_id, channel_type, webhook_url),
        fetchone=True, commit=True
    )
    return {'id': row['id'], 'webhook_url': webhook_url}

@app.delete('/api/channels/{channel_id}')
async def delete_channel(channel_id: int):
    db_query('DELETE FROM sdr_channels WHERE id = %s', (channel_id,), commit=True)
    return {'deleted': True}

# ── Webhook Router ──
@app.post('/webhook/{agent_id}')
async def webhook_router(agent_id: int, request: Request):
    agent = db_query('SELECT port, status FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    if not agent['port']:
        return JSONResponse(status_code=400, content={'error': 'Agent has no port assigned'})
    body = await request.json()
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"http://localhost:{agent['port']}/webhook", json=body)
            return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as e:
        log(f'Webhook forward error for agent {agent_id}: {e}')
        return JSONResponse(status_code=502, content={'error': str(e)})

# ── API Routes: Sales ──
@app.post('/api/agents/{agent_id}/sales/webhook')
async def sales_webhook(agent_id: int, request: Request):
    agent = db_query('SELECT id, sales_webhook_secret FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    body = await request.json()
    log(f'Sales webhook raw: {json.dumps(body)[:2000]}')

    # Detect platform and extract fields
    platform = 'manual'
    buyer_name = ''
    buyer_email = ''
    amount = 0
    product = ''
    transaction_id = ''
    status = 'approved'

    buyer_phone = ''

    # Hotmart format
    if 'data' in body and isinstance(body.get('data'), dict):
        d = body['data']
        purchase = d.get('purchase', {})
        buyer = d.get('buyer', {})
        prod = d.get('product', {})
        platform = 'hotmart'
        buyer_name = buyer.get('name', '')
        buyer_email = buyer.get('email', '')
        buyer_phone = buyer.get('phone', '') or buyer.get('checkout_phone', '')
        # Route events to correct table
        event = body.get('event', '')
        abandonment_events = ('PURCHASE_OUT_OF_SHOPPING_CART', 'PURCHASE_CANCELED', 'PURCHASE_DELAYED', 'PURCHASE_PROTEST')
        approved_events = ('PURCHASE_APPROVED', 'PURCHASE_COMPLETE', 'PURCHASE_BILLET_PRINTED', '')

        if event and event in abandonment_events:
            product = prod.get('name', '')
            db_query(
                '''INSERT INTO sdr_cart_abandonments (agent_id, platform, product, buyer_name, buyer_email, buyer_phone, event_type)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (agent_id, 'hotmart', product, buyer_name, buyer_email, buyer_phone, event),
                commit=True
            )
            log(f'Cart abandonment recorded for agent {agent_id}: {event} {buyer_name} {buyer_phone}')
            return {'ok': True, 'event': event, 'type': 'abandonment'}

        if event and event not in approved_events:
            log(f'Hotmart event ignored: {event}')
            return {'ok': True, 'ignored': event}

        price = purchase.get('price', {})
        amount = price.get('value', 0) if isinstance(price, dict) else (price if isinstance(price, (int, float)) else 0)
        # Fallback: original_offer_price
        if not amount:
            ofp = purchase.get('original_offer_price', {})
            amount = ofp.get('value', 0) if isinstance(ofp, dict) else 0
        # Fallback: full_price
        if not amount:
            fp = purchase.get('full_price', {})
            amount = fp.get('value', 0) if isinstance(fp, dict) else 0
        # Fallback: sum commissions (most reliable)
        if not amount:
            comms = d.get('commissions', [])
            if isinstance(comms, list):
                amount = sum(c.get('value', 0) for c in comms if isinstance(c, dict))
        product = prod.get('name', '')
        transaction_id = purchase.get('transaction', '')
        raw_status = purchase.get('status', 'APPROVED')
        if raw_status == 'APPROVED': status = 'approved'
        elif raw_status == 'REFUNDED': status = 'refunded'
        elif raw_status == 'CHARGEBACK': status = 'chargeback'
        else: status = raw_status.lower()

    # Kiwify format
    elif 'order_status' in body:
        platform = 'kiwify'
        customer = body.get('Customer', {})
        buyer_name = customer.get('full_name', '')
        buyer_email = customer.get('email', '')
        commissions = body.get('Commissions', {})
        amount = commissions.get('charge_amount', 0)
        product = body.get('Product', {}).get('product_name', body.get('product_name', ''))
        transaction_id = body.get('order_id', '')
        raw_status = body.get('order_status', 'paid')
        if raw_status in ('paid', 'approved'): status = 'approved'
        elif raw_status == 'refunded': status = 'refunded'
        elif raw_status == 'chargedback': status = 'chargeback'
        else: status = raw_status

    # Generic format
    else:
        platform = body.get('platform', 'manual')
        buyer_name = body.get('buyer_name', '')
        buyer_email = body.get('buyer_email', '')
        amount = body.get('amount', 0)
        product = body.get('product', '')
        transaction_id = body.get('transaction_id', '')
        status = body.get('status', 'approved')

    try:
        amount = float(amount or 0)
    except (ValueError, TypeError):
        amount = 0

    # Auto-route sales by product code to correct agent
    PRODUCT_TO_AGENT = {
        # {{DONO}} products → agent 2 (Clone)
        '1891263': 2, '5735333': 2, '5929153': 2, '5997535': 2, '5997927': 2,
        # Rita Machado products → agent 3 (Cristal IG)
        '5248082': 3, '6263770': 3, '2229233': 3, '2972278': 3, '6778617': 3,
    }
    ALL_KNOWN_PRODUCTS = set(PRODUCT_TO_AGENT.keys())
    hotmart_product_id = str(d.get('product', {}).get('id', '')) if isinstance(body.get('data'), dict) else ''

    # Auto-route: if product belongs to a different agent, redirect
    if hotmart_product_id and hotmart_product_id in PRODUCT_TO_AGENT:
        correct_agent = PRODUCT_TO_AGENT[hotmart_product_id]
        if correct_agent != agent_id:
            log(f'Sale auto-routed: product {hotmart_product_id} → agent {correct_agent} (was {agent_id})')
            agent_id = correct_agent
    elif hotmart_product_id and hotmart_product_id not in ALL_KNOWN_PRODUCTS:
        # Unknown product: ignore
        log(f'Sale ignored (unknown product {hotmart_product_id}): {product} | {buyer_name}')
        return {'ok': True, 'ignored': True, 'reason': f'unknown product {hotmart_product_id}'}

    # Fallback for non-Hotmart: filter by name only for agent 2
    if not hotmart_product_id and product and agent_id == 2:
        pl = (product or '').lower()
        allowed_names = ['imersão', 'imersao', 'openclaw', 'gravação', 'gravacao', 'mentoria {{NICHO_DONO_SLUG}}', 'inteligência artificial', 'inteligencia artificial', 'formação {{NICHO_DONO_SLUG}}', 'formacao {{NICHO_DONO_SLUG}}']
        if not any(k in pl for k in allowed_names):
            log(f'Sale ignored (name filter): {product} | {buyer_name}')
            return {'ok': True, 'ignored': True}

    # Skip duplicate transactions
    if transaction_id:
        existing = db_query('SELECT id FROM sdr_agent_sales WHERE transaction_id = %s AND agent_id = %s', (transaction_id, agent_id), fetchone=True)
        if existing:
            log(f'Sale duplicate skipped: {transaction_id} {buyer_name}')
            return {'ok': True, 'duplicate': True}

    db_query(
        '''INSERT INTO sdr_agent_sales (agent_id, platform, product, amount, buyer_name, buyer_email, buyer_phone, transaction_id, status)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (agent_id, platform, product, amount, buyer_name, buyer_email, buyer_phone, transaction_id, status),
        commit=True
    )
    log(f'Sale recorded for agent {agent_id}: {platform} R${amount} {buyer_name} {buyer_phone}')
    return {'ok': True, 'platform': platform, 'amount': amount}

@app.get('/api/agents/{agent_id}/sales')
async def list_sales(agent_id: int, request: Request):
    agent = db_query('SELECT id FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    date_filter = get_date_filter(request, 'created_at')
    sales = db_query(
        f'SELECT * FROM sdr_agent_sales WHERE agent_id = %s {date_filter} ORDER BY created_at DESC LIMIT 100',
        (agent_id,), fetchall=True
    )
    for s in (sales or []):
        s['created_at'] = str(s['created_at'])
        s['amount'] = float(s['amount'] or 0)
    return sales or []

@app.get('/api/agents/{agent_id}/sales/stats')
async def agent_sales_stats(agent_id: int, request: Request):
    agent = db_query('SELECT id FROM sdr_agents WHERE id = %s', (agent_id,), fetchone=True)
    if not agent:
        return JSONResponse(status_code=404, content={'error': 'Agent not found'})
    date_filter = get_date_filter(request, 'created_at')
    row = db_query(f'''SELECT
        COUNT(*) as total_sales,
        COALESCE(SUM(amount), 0) as total_amount
        FROM sdr_agent_sales WHERE agent_id = %s AND status = 'approved' {date_filter}''',
        (agent_id,), fetchone=True
    )
    return {
        'total_sales': row['total_sales'] or 0,
        'total_amount': float(row['total_amount'] or 0),
        'sales_today': row['total_sales'] or 0,
        'amount_today': float(row['total_amount'] or 0),
        'sales_7d': row['total_sales'] or 0,
        'amount_7d': float(row['total_amount'] or 0),
    }

@app.get('/api/dashboard/sales')
async def dashboard_sales():
    row = db_query('''SELECT
        COUNT(*) as total_sales,
        COALESCE(SUM(amount), 0) as total_amount,
        COUNT(*) FILTER (WHERE (created_at AT TIME ZONE 'America/Sao_Paulo')::date = (NOW() AT TIME ZONE 'America/Sao_Paulo')::date) as sales_today,
        COALESCE(SUM(amount) FILTER (WHERE (created_at AT TIME ZONE 'America/Sao_Paulo')::date = (NOW() AT TIME ZONE 'America/Sao_Paulo')::date), 0) as amount_today,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') as sales_7d,
        COALESCE(SUM(amount) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'), 0) as amount_7d
        FROM sdr_agent_sales WHERE status = 'approved' ''',
        fetchone=True
    )
    return {
        'total_sales': row['total_sales'] or 0,
        'total_amount': float(row['total_amount'] or 0),
        'sales_today': row['sales_today'] or 0,
        'amount_today': float(row['amount_today'] or 0),
        'sales_7d': row['sales_7d'] or 0,
        'amount_7d': float(row['amount_7d'] or 0),
    }

# ── API Routes: Global Sales & Cart Abandonments ──
@app.get('/api/sales')
async def global_sales(request: Request):
    date_filter = get_date_filter(request, 's.created_at')
    sales = db_query(
        f'''SELECT s.id, a.name as agent_name, s.product, s.amount, s.buyer_name, s.buyer_email,
                  s.buyer_phone, s.platform, s.status, s.created_at
           FROM sdr_agent_sales s
           LEFT JOIN sdr_agents a ON a.id = s.agent_id
           WHERE 1=1 {date_filter}
           ORDER BY s.created_at DESC LIMIT 100''',
        fetchall=True
    )
    for s in (sales or []):
        s['created_at'] = str(s['created_at'])
        s['amount'] = float(s['amount'] or 0)
    return sales or []

@app.get('/api/sales/stats')
async def global_sales_stats(request: Request):
    date_filter = get_date_filter(request, 'created_at')
    row = db_query(f'''SELECT
        COUNT(*) as total_sales,
        COALESCE(SUM(amount), 0) as total_amount,
        CASE WHEN COUNT(*) > 0 THEN COALESCE(SUM(amount), 0) / COUNT(*) ELSE 0 END as avg_ticket
        FROM sdr_agent_sales WHERE status = 'approved' {date_filter}''',
        fetchone=True
    )
    total_s = row['total_sales'] or 0
    total_a = float(row['total_amount'] or 0)
    avg_t = float(row['avg_ticket'] or 0)
    return {
        'total_sales': total_s, 'total_amount': total_a, 'avg_ticket': avg_t,
        'sales_today': total_s, 'amount_today': total_a,
        'sales_7d': total_s, 'amount_7d': total_a,
        'sales_30d': total_s, 'amount_30d': total_a,
    }

@app.get('/api/cart-abandonments')
async def global_cart_abandonments(request: Request):
    date_filter = get_date_filter(request, 'c.created_at')
    rows = db_query(
        f'''SELECT c.id, a.name as agent_name, c.product, c.buyer_name, c.buyer_email,
                  c.buyer_phone, c.event_type, c.created_at
           FROM sdr_cart_abandonments c
           LEFT JOIN sdr_agents a ON a.id = c.agent_id
           WHERE 1=1 {date_filter}
           ORDER BY c.created_at DESC LIMIT 100''',
        fetchall=True
    )
    for r in (rows or []):
        r['created_at'] = str(r['created_at'])
    return rows or []

@app.get('/api/cart-abandonments/stats')
async def global_cart_abandonment_stats(request: Request):
    date_filter = get_date_filter(request, 'created_at')
    row = db_query(f'''SELECT
        COUNT(*) as total_abandonments,
        COUNT(*) FILTER (WHERE (created_at AT TIME ZONE 'America/Sao_Paulo')::date = (NOW() AT TIME ZONE 'America/Sao_Paulo')::date) as today,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days') as last_7d,
        COUNT(*) FILTER (WHERE created_at >= NOW() - INTERVAL '30 days') as last_30d
        FROM sdr_cart_abandonments WHERE 1=1 {date_filter}''',
        fetchone=True
    )
    return {
        'total_abandonments': row['total_abandonments'] or 0,
        'today': row['today'] or 0,
        'last_7d': row['last_7d'] or 0,
        'last_30d': row['last_30d'] or 0,
    }

# ── Prospecção Ativa (Ademir) ──
ADEMIR_DAEMON_URL = os.getenv('ADEMIR_DAEMON_URL', 'http://127.0.0.1:9100')
ADEMIR_DAEMON_TOKEN = os.getenv('ADEMIR_DAEMON_TOKEN', '{{ADEMIR_TOKEN}}')

@app.get('/api/prospect/targets')
async def prospect_list_targets():
    rows = db_query('SELECT * FROM prospect_targets ORDER BY created_at DESC', fetchall=True) or []
    for r in rows:
        r['created_at'] = str(r['created_at'])
    return rows

@app.post('/api/prospect/targets')
async def prospect_add_target(request: Request):
    body = await request.json()
    username = (body.get('username') or '').strip().lstrip('@')
    source = (body.get('source') or '').strip()
    if not username or not source:
        return JSONResponse(status_code=400, content={'error': 'username e source obrigatorios'})
    if source not in ('meu_seguidor', 'comentou_em_mim', 'comentou_em_alvo'):
        return JSONResponse(status_code=400, content={'error': 'source invalido'})
    try:
        row = db_query('INSERT INTO prospect_targets (username, source) VALUES (%s, %s) RETURNING id', (username, source), fetchone=True, commit=True)
        return {'ok': True, 'id': row['id']}
    except Exception as e:
        return JSONResponse(status_code=400, content={'error': str(e)})

@app.delete('/api/prospect/targets/{target_id}')
async def prospect_delete_target(target_id: int):
    db_query('DELETE FROM prospect_targets WHERE id = %s', (target_id,), commit=True)
    return {'ok': True}

@app.get('/api/prospect/leads')
async def prospect_list_leads(request: Request, status: str = '', limit: int = 200, offset: int = 0):
    where = ''
    params = []
    if status:
        where = 'WHERE status = %s'
        params.append(status)
    params.extend([limit, offset])
    rows = db_query(
        f'''SELECT id, ig_username, display_name, bio, followers_count, has_offer, niche, status, source_type,
                   discovered_at, dm_sent_at, replied_at,
                   briefing->>'whatsapp_number' AS whatsapp_number
           FROM prospect_leads {where} ORDER BY discovered_at DESC LIMIT %s OFFSET %s''',
        tuple(params), fetchall=True,
    ) or []
    for r in rows:
        for k in ('discovered_at', 'dm_sent_at', 'replied_at'):
            if r.get(k):
                r[k] = str(r[k])
    return rows

@app.get('/api/prospect/leads/{lead_id}')
async def prospect_get_lead(lead_id: int):
    row = db_query('SELECT * FROM prospect_leads WHERE id = %s', (lead_id,), fetchone=True)
    if not row:
        return JSONResponse(status_code=404, content={'error': 'lead nao encontrado'})
    for k in ('discovered_at', 'dm_sent_at', 'replied_at', 'handed_off_at'):
        if row.get(k):
            row[k] = str(row[k])
    dms = db_query('SELECT * FROM prospect_dms WHERE lead_id = %s ORDER BY sent_at ASC', (lead_id,), fetchall=True) or []
    for d in dms:
        d['sent_at'] = str(d['sent_at'])
    row['dms'] = dms
    return row

@app.patch('/api/prospect/leads/{lead_id}')
async def prospect_update_lead(lead_id: int, request: Request):
    body = await request.json()
    status = body.get('status')
    if status not in ('discovered', 'qualified', 'approved_to_send', 'dm_sent', 'replied', 'handed_off', 'disqualified'):
        return JSONResponse(status_code=400, content={'error': 'status invalido'})
    db_query('UPDATE prospect_leads SET status = %s WHERE id = %s', (status, lead_id), commit=True)
    return {'ok': True}


# FASE 4: aprovacao manual de leads
@app.post('/api/prospect/leads/{lead_id}/approve')
async def prospect_approve_lead(lead_id: int):
    row = db_query('SELECT id, status FROM prospect_leads WHERE id = %s', (lead_id,), fetchone=True)
    if not row:
        return JSONResponse(status_code=404, content={'error': 'lead nao encontrado'})
    if row['status'] in ('dm_sent', 'replied', 'handed_off'):
        return JSONResponse(status_code=400, content={'error': f'lead ja tem status {row["status"]}'})
    db_query("UPDATE prospect_leads SET status = 'approved_to_send' WHERE id = %s", (lead_id,), commit=True)
    return {'ok': True, 'lead_id': lead_id, 'status': 'approved_to_send'}


@app.post('/api/prospect/leads/{lead_id}/unapprove')
async def prospect_unapprove_lead(lead_id: int):
    row = db_query('SELECT id, status FROM prospect_leads WHERE id = %s', (lead_id,), fetchone=True)
    if not row:
        return JSONResponse(status_code=404, content={'error': 'lead nao encontrado'})
    if row['status'] != 'approved_to_send':
        return JSONResponse(status_code=400, content={'error': f'lead nao esta aprovado (status={row["status"]})'})
    db_query("UPDATE prospect_leads SET status = 'qualified' WHERE id = %s", (lead_id,), commit=True)
    return {'ok': True, 'lead_id': lead_id, 'status': 'qualified'}


@app.post('/api/prospect/leads/bulk-approve')
async def prospect_bulk_approve(request: Request):
    body = await request.json()
    ids = body.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return JSONResponse(status_code=400, content={'error': 'ids deve ser lista nao-vazia'})
    int_ids = []
    for x in ids:
        try:
            int_ids.append(int(x))
        except Exception:
            pass
    if not int_ids:
        return JSONResponse(status_code=400, content={'error': 'nenhum id valido'})
    db_query(
        '''UPDATE prospect_leads SET status = 'approved_to_send'
           WHERE id = ANY(%s) AND status IN ('qualified', 'discovered')''',
        (int_ids,), commit=True,
    )
    approved = db_query(
        "SELECT COUNT(*) AS c FROM prospect_leads WHERE id = ANY(%s) AND status = 'approved_to_send'",
        (int_ids,), fetchone=True,
    )
    return {'ok': True, 'requested': len(int_ids), 'approved': approved['c'] if approved else 0}


@app.post('/api/prospect/run-send-now')
async def prospect_run_send_now():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f'{ADEMIR_DAEMON_URL}/run-send-now',
                headers={'Authorization': f'Bearer {ADEMIR_DAEMON_TOKEN}'},
            )
            if resp.status_code == 200:
                return {'ok': True, 'message': 'send loop disparado', 'response': resp.json()}
            return JSONResponse(status_code=502, content={'error': f'daemon retornou {resp.status_code}', 'body': resp.text})
    except Exception as e:
        return JSONResponse(status_code=502, content={'error': f'daemon nao acessivel: {str(e)}'})


@app.post('/api/prospect/enrich-whatsapp')
async def prospect_enrich_whatsapp():
    """Pass retroativo via daemon (sem chamadas HikerAPI)."""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f'{ADEMIR_DAEMON_URL}/enrich-whatsapp',
                headers={'Authorization': f'Bearer {ADEMIR_DAEMON_TOKEN}'},
            )
            if resp.status_code == 200:
                return resp.json()
            return JSONResponse(status_code=502, content={'error': f'daemon retornou {resp.status_code}', 'body': resp.text})
    except Exception as e:
        return JSONResponse(status_code=502, content={'error': f'daemon nao acessivel: {str(e)}'})


@app.get('/api/prospect/dms')
async def prospect_list_dms(lead_id: int = 0, limit: int = 200):
    if lead_id:
        rows = db_query('SELECT d.*, l.ig_username FROM prospect_dms d JOIN prospect_leads l ON l.id = d.lead_id WHERE d.lead_id = %s ORDER BY d.sent_at DESC LIMIT %s', (lead_id, limit), fetchall=True) or []
    else:
        rows = db_query('SELECT d.*, l.ig_username FROM prospect_dms d JOIN prospect_leads l ON l.id = d.lead_id ORDER BY d.sent_at DESC LIMIT %s', (limit,), fetchall=True) or []
    for r in rows:
        r['sent_at'] = str(r['sent_at'])
    return rows

@app.get('/api/prospect/runs')
async def prospect_list_runs(limit: int = 50):
    rows = db_query('SELECT * FROM prospect_runs ORDER BY started_at DESC LIMIT %s', (limit,), fetchall=True) or []
    for r in rows:
        r['started_at'] = str(r['started_at'])
        if r.get('finished_at'):
            r['finished_at'] = str(r['finished_at'])
    return rows

@app.post('/api/prospect/run-now')
async def prospect_run_now():
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f'{ADEMIR_DAEMON_URL}/run-now', headers={'Authorization': f'Bearer {ADEMIR_DAEMON_TOKEN}'})
            if resp.status_code == 200:
                return {'ok': True, 'message': 'Ademir disparado', 'response': resp.json()}
            return JSONResponse(status_code=502, content={'error': f'daemon retornou {resp.status_code}', 'body': resp.text})
    except Exception as e:
        return JSONResponse(status_code=502, content={'error': f'daemon nao acessivel: {str(e)}'})

@app.get('/api/prospect/stats')
async def prospect_stats():
    leads = db_query('''SELECT
        COUNT(*) AS total,
        COUNT(*) FILTER (WHERE status = 'discovered') AS discovered,
        COUNT(*) FILTER (WHERE status = 'qualified') AS qualified,
        COUNT(*) FILTER (WHERE status = 'approved_to_send') AS approved_to_send,
        COUNT(*) FILTER (WHERE status = 'dm_sent') AS dm_sent,
        COUNT(*) FILTER (WHERE status = 'replied') AS replied,
        COUNT(*) FILTER (WHERE status = 'handed_off') AS handed_off,
        COUNT(*) FILTER (WHERE status = 'disqualified') AS disqualified,
        COUNT(*) FILTER (WHERE briefing->>'whatsapp_number' IS NOT NULL) AS with_whatsapp,
        COUNT(*) FILTER (WHERE discovered_at >= NOW() - INTERVAL '24 hours') AS last_24h,
        COUNT(*) FILTER (WHERE discovered_at >= NOW() - INTERVAL '7 days') AS last_7d
        FROM prospect_leads''', fetchone=True) or {}
    dms = db_query('SELECT COUNT(*) AS total FROM prospect_dms', fetchone=True) or {'total': 0}
    targets = db_query('SELECT COUNT(*) AS total FROM prospect_targets WHERE active = true', fetchone=True) or {'total': 0}
    reply_rate = 0
    if leads.get('dm_sent') and leads['dm_sent'] > 0:
        reply_rate = round((leads.get('replied', 0) / leads['dm_sent']) * 100, 1)
    return {
        'leads': leads,
        'dms_total': dms['total'],
        'targets_active': targets['total'],
        'reply_rate': reply_rate,
    }

# ── Frontend ──
@app.get('/')
async def index():
    return HTMLResponse(content=FRONTEND_HTML, headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache", "Expires": "0"})

# ── Site Analytics (tracking pixel) ──
@app.get('/t.gif')
async def track_pixel(request: Request, s: str = '', p: str = '/'):
    """Tracking pixel - add <img src="https://agents.{{DOMINIO_AI}}/t.gif?s=SITE&p=PATH"> to any site"""
    try:
        ip = request.headers.get('x-forwarded-for', request.client.host if request.client else '')
        ua = request.headers.get('user-agent', '')[:300]
        ref = request.headers.get('referer', '')[:300]
        db_query('INSERT INTO site_analytics (site, path, ip, user_agent, referrer) VALUES (%s,%s,%s,%s,%s)',
                 (s or 'unknown', p, ip, ua, ref), commit=True)
    except:
        pass
    return JSONResponse(content={}, headers={"Content-Type": "image/gif", "Cache-Control": "no-cache"})

@app.get('/api/analytics')
async def get_analytics(request: Request, site: str = '', period: str = '24h'):
    """Get site analytics"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if token not in active_tokens:
        return JSONResponse(status_code=401, content={'error': 'Não autorizado'})
    if period == '24h':
        time_filter = "AND created_at >= NOW() - INTERVAL '24 hours'"
    elif period == '7d':
        time_filter = "AND created_at >= NOW() - INTERVAL '7 days'"
    elif period == '30d':
        time_filter = "AND created_at >= NOW() - INTERVAL '30 days'"
    else:
        time_filter = ""
    site_filter = f"AND site = '{site}'" if site else ""
    stats = db_query(f'''SELECT
        COUNT(*) as total_hits,
        COUNT(DISTINCT ip) as unique_visitors,
        site, path
        FROM site_analytics WHERE 1=1 {site_filter} {time_filter}
        GROUP BY site, path ORDER BY total_hits DESC LIMIT 20''', fetchall=True)
    totals = db_query(f'''SELECT
        COUNT(*) as total_hits,
        COUNT(DISTINCT ip) as unique_visitors
        FROM site_analytics WHERE 1=1 {site_filter} {time_filter}''', fetchone=True)
    return {'totals': totals, 'by_page': stats or []}

@app.get('/health')
async def health():
    return {'status': 'ok', 'service': 'agent-manager', 'port': 3600}

# ── Frontend HTML ──
FRONTEND_HTML = r'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agents | {{DONO}} AI</title>
<script src="https://cdn.tailwindcss.com"></script>
<link href="https://fonts.googleapis.com/css2?family=Google+Sans:wght@300;400;500;600;700;800;900&family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<script>
tailwind.config = {
  theme: {
    extend: {
      fontFamily: { inter: ['Google Sans', 'Inter', 'sans-serif'] },
      colors: {
        dark: { 900: '#000000', 800: '#0a0a0a', 700: '#111111', 600: '#1a1a1a' },
        accent: { blue: '#ffffff', purple: '#cccccc', cyan: '#e0e0e0', pink: '#999999', green: '#ffffff', orange: '#bbbbbb' }
      }
    }
  }
}
</script>
<style>
:root {
  --bg-primary: #0a0a0a;
  --bg-secondary: #111111;
  --bg-card: rgba(255,255,255,0.04);
  --bg-card-solid: #111111;
  --text-primary: #ffffff;
  --text-secondary: rgba(255,255,255,0.65);
  --text-dim: rgba(255,255,255,0.45);
  --text-muted: rgba(255,255,255,0.3);
  --border-color: rgba(255,255,255,0.06);
  --border-hover: rgba(255,255,255,0.15);
  --sidebar-bg: rgba(8,8,8,0.85);
  --sidebar-border: rgba(255,255,255,0.05);
  --input-bg: rgba(0,0,0,0.3);
  --modal-bg: #111111;
  --table-hover: rgba(255,255,255,0.025);
  --scrollbar-bg: #0a0a0a;
  --scrollbar-thumb: rgba(255,255,255,0.12);
  --card-radius: 20px;
  --card-radius-lg: 24px;
  --transition-default: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}
[data-theme="light"] {
  --bg-primary: #f8f9fa;
  --bg-secondary: #ffffff;
  --bg-card: #ffffff;
  --bg-card-solid: #ffffff;
  --text-primary: #1a1a1a;
  --text-secondary: rgba(0,0,0,0.65);
  --text-dim: rgba(0,0,0,0.5);
  --text-muted: rgba(0,0,0,0.35);
  --border-color: rgba(0,0,0,0.06);
  --border-hover: rgba(0,0,0,0.15);
  --sidebar-bg: rgba(15,15,25,0.97);
  --sidebar-border: rgba(255,255,255,0.06);
  --input-bg: rgba(0,0,0,0.04);
  --modal-bg: #ffffff;
  --table-hover: rgba(0,0,0,0.02);
  --scrollbar-bg: #f0f0f0;
  --scrollbar-thumb: rgba(0,0,0,0.18);
}
[data-theme="light"] .stat-number { background: linear-gradient(135deg, #0e7490, #0891b2); -webkit-background-clip: text; -webkit-text-fill-color: transparent; background-clip: text; }
[data-theme="light"] .badge-active { background: rgba(34,197,94,0.1); color: #15803d; }
[data-theme="light"] .badge-inactive, [data-theme="light"] .badge-error { background: rgba(239,68,68,0.08); color: #dc2626; }
[data-theme="light"] .funnel-bar { opacity: 0.8; }
[data-theme="light"] table th { color: rgba(0,0,0,0.5) !important; border-color: rgba(0,0,0,0.1) !important; }
[data-theme="light"] table td { color: rgba(0,0,0,0.7) !important; border-color: rgba(0,0,0,0.06) !important; }
[data-theme="light"] .nav-item { color: rgba(255,255,255,0.7); }
[data-theme="light"] .nav-item.active { color: #22d3ee; background: rgba(34,211,238,0.1); }
[data-theme="light"] header { background: rgba(255,255,255,0.92) !important; backdrop-filter: blur(24px); border-color: rgba(0,0,0,0.05) !important; }
[data-theme="light"] header span, [data-theme="light"] header select { color: #333 !important; }
[data-theme="light"] select { background: rgba(0,0,0,0.05) !important; border-color: rgba(0,0,0,0.15) !important; color: #333 !important; }
[data-theme="light"] .wa-link { color: #0e7490 !important; }
* { font-family: 'Google Sans', 'Inter', sans-serif; margin: 0; padding: 0; box-sizing: border-box; }
body { background: var(--bg-primary); color: var(--text-primary); min-height: 100vh; display: flex; -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }
::selection { background: rgba(6,182,212,0.25); }
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: var(--scrollbar-bg); }
::-webkit-scrollbar-thumb { background: var(--scrollbar-thumb); border-radius: 3px; }

/* LED border animation */
@property --angle { syntax: "<angle>"; initial-value: 0deg; inherits: false; }
@keyframes led-rotate { to { --angle: 360deg; } }

.glass {
  background: var(--bg-card);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  border: 1px solid var(--border-color);
  border-radius: var(--card-radius);
  transition: all var(--transition-default);
}
.glass-strong {
  background: var(--bg-card);
  backdrop-filter: blur(32px);
  -webkit-backdrop-filter: blur(32px);
  border: 1px solid var(--border-color);
  border-radius: var(--card-radius);
  transition: all var(--transition-default);
}
[data-theme="light"] .glass,
[data-theme="light"] .glass-strong {
  background: var(--bg-card-solid);
  box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.04);
  backdrop-filter: none;
  border-color: rgba(0,0,0,0.06);
}
.gradient-border {
  position: relative;
}
.gradient-border::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  background: conic-gradient(from var(--angle), transparent 40%, rgba(6,182,212,0.3), rgba(34,211,238,0.2), transparent 60%);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  pointer-events: none;
}
.card-hover {
  transition: transform var(--transition-default), box-shadow var(--transition-default);
}
.card-hover:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 40px rgba(6,182,212,0.08), 0 2px 12px rgba(0,0,0,0.12);
}
.glow-blue { box-shadow: 0 0 48px rgba(59,130,246,0.06); }
.glow-purple { box-shadow: 0 0 48px rgba(139,92,246,0.06); }
.glow-cyan { box-shadow: 0 0 48px rgba(6,182,212,0.06); }
.stat-number {
  background: linear-gradient(135deg, #ffffff, #a0aec0);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}
@keyframes pulse-slow {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.7; }
}
.pulse-slow { animation: pulse-slow 3s ease-in-out infinite; }
.funnel-bar { transition: width 0.8s ease; }
.heatmap-cell { transition: background 0.3s ease; }

/* Sidebar - always dark */
.sidebar {
  width: 260px;
  min-height: 100vh;
  background: var(--sidebar-bg);
  backdrop-filter: blur(32px);
  -webkit-backdrop-filter: blur(32px);
  border-right: 1px solid var(--sidebar-border);
  display: flex;
  flex-direction: column;
  position: fixed;
  left: 0; top: 0; bottom: 0;
  z-index: 50;
  transition: transform .3s cubic-bezier(0.4, 0, 0.2, 1);
}
.sidebar-logo {
  padding: 28px 24px;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: #fff;
  display: flex;
  align-items: center;
  gap: 12px;
}
.sidebar-logo .icon {
  width: 36px; height: 36px;
  background: linear-gradient(135deg, #06b6d4, #3b82f6);
  border-radius: 10px;
  display: flex; align-items: center; justify-content: center;
  font-size: 16px; color: #000; font-weight: 800;
}
.sidebar-nav { flex: 1; padding: 12px 14px; }
.nav-item {
  display: flex; align-items: center; gap: 14px;
  padding: 12px 16px; border-radius: 12px; cursor: pointer;
  color: rgba(255,255,255,0.4); font-size: 14px; font-weight: 500;
  transition: all var(--transition-default); margin-bottom: 4px; user-select: none;
  position: relative;
}
.nav-item:hover { background: rgba(255,255,255,0.04); color: rgba(255,255,255,0.7); }
.nav-item.active { background: rgba(6,182,212,0.08); color: #22d3ee; }
.nav-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 20px;
  background: #22d3ee;
  border-radius: 0 3px 3px 0;
}
.nav-item .nav-icon { width: 20px; height: 20px; }
.sidebar-footer {
  padding: 20px 24px;
  border-top: 1px solid rgba(255,255,255,0.05);
  font-size: 11px; color: rgba(255,255,255,0.2);
}
.logout-btn {
  display: flex; align-items: center; gap: 8px;
  width: 100%; padding: 11px 16px; border-radius: 12px; border: none;
  background: rgba(239,68,68,0.06); color: rgba(239,68,68,0.6);
  font-family: inherit; font-size: 13px; font-weight: 500;
  cursor: pointer; transition: all var(--transition-default); margin-bottom: 10px;
}
.logout-btn:hover { background: rgba(239,68,68,0.12); color: #ef4444; }

/* Main area */
.main-area { margin-left: 260px; flex: 1; min-height: 100vh; transition: margin-left .3s cubic-bezier(0.4, 0, 0.2, 1); }
.page { display: none; }
.page.active { display: block; }

/* Buttons */
.btn {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 22px; border-radius: 12px; border: none;
  font-family: inherit; font-size: 13px; font-weight: 600;
  cursor: pointer; transition: all var(--transition-default); letter-spacing: 0.1px;
  position: relative; height: 44px;
}
.btn-primary { background: linear-gradient(135deg, #06b6d4, #3b82f6); color: #000; font-weight: 600; }
.btn-primary:hover { transform: translateY(-1px); box-shadow: 0 6px 24px rgba(6,182,212,0.25); }
.btn-led {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.06);
  color: #fff; border-radius: 14px;
}
.btn-led::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: conic-gradient(from var(--angle), #000, #06b6d4, #22d3ee, #06b6d4, #000);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
.btn-secondary {
  background: rgba(255,255,255,0.04); color: #e5e5e5;
  border: 1px solid rgba(255,255,255,0.06);
}
.btn-secondary:hover { border-color: rgba(255,255,255,0.15); background: rgba(255,255,255,0.07); }
.btn-danger { background: transparent; color: #ef4444; border: 1px solid rgba(239,68,68,0.2); border-radius: 12px; }
.btn-danger:hover { background: #ef4444; color: #fff; }
.btn-success { background: #22c55e; color: #000; border-radius: 12px; }
.btn-success:hover { opacity: .9; transform: translateY(-1px); }
.btn-sm { padding: 7px 16px; font-size: 12px; border-radius: 10px; height: 36px; }

/* Badge */
.badge {
  display: inline-block; padding: 4px 12px; border-radius: 100px;
  font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.3px;
}
.badge-active, .badge-online { background: rgba(34,197,94,.10); color: #22c55e; }
.badge-inactive, .badge-stopped { background: rgba(136,136,136,.10); color: #888; }
.badge-error { background: rgba(239,68,68,.10); color: #ef4444; }
.wa-link { color: #22d3ee !important; text-decoration: underline !important; cursor: pointer !important; }

/* Agent cards */
.agents-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
.agents-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr)); gap: 16px; }
.agent-card {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: var(--card-radius); padding: 24px; backdrop-filter: blur(24px);
  transition: all var(--transition-default); cursor: pointer; position: relative;
}
[data-theme="light"] .agent-card { background: var(--bg-card-solid); box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.03); backdrop-filter: none; }
.agent-card::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: conic-gradient(from var(--angle), transparent 40%, rgba(6,182,212,0.3), rgba(34,211,238,0.2), transparent 60%);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
.agent-card:hover { transform: translateY(-2px); box-shadow: 0 12px 40px rgba(6,182,212,0.08), 0 2px 8px rgba(0,0,0,0.08); }
.agent-card-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 14px; }
.agent-name { font-size: 15px; font-weight: 600; }
.agent-company { color: rgba(255,255,255,0.4); font-size: 12px; margin-top: 4px; }
.agent-meta {
  display: flex; gap: 18px; color: rgba(255,255,255,0.3); font-size: 12px;
  margin-top: 14px; padding-top: 14px; border-top: 1px solid rgba(255,255,255,0.05);
}
.agent-meta span { display: flex; align-items: center; gap: 5px; }

/* Detail view */
.detail-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; flex-wrap: wrap; gap: 14px; }
.detail-actions { display: flex; gap: 10px; flex-wrap: wrap; }
.detail-section {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: var(--card-radius); padding: 28px; margin-bottom: 16px; backdrop-filter: blur(24px);
  transition: all var(--transition-default);
}
[data-theme="light"] .detail-section { background: var(--bg-card-solid); box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.03); backdrop-filter: none; }
.detail-section h3 { font-size: 12px; font-weight: 600; color: #22d3ee; margin-bottom: 16px; text-transform: uppercase; letter-spacing: 0.6px; }
.detail-field { margin-bottom: 14px; }
.detail-field .label { font-size: 11px; color: rgba(255,255,255,0.4); margin-bottom: 4px; text-transform: uppercase; letter-spacing: 0.4px; }
.detail-field .value { font-size: 14px; white-space: pre-wrap; line-height: 1.6; }
.detail-stats { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-bottom: 20px; }

/* Channels */
.channel-card {
  background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: var(--card-radius); padding: 22px; margin-bottom: 14px; backdrop-filter: blur(24px);
  display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 14px;
  position: relative; transition: all var(--transition-default);
}
[data-theme="light"] .channel-card { background: var(--bg-card-solid); box-shadow: 0 1px 3px rgba(0,0,0,0.04), 0 4px 16px rgba(0,0,0,0.03); backdrop-filter: none; }
.channel-card::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: conic-gradient(from var(--angle), transparent 40%, rgba(6,182,212,0.3), rgba(34,211,238,0.2), transparent 60%);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
.channel-info { flex: 1; min-width: 200px; position: relative; z-index: 1; }
.channel-type { font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.channel-agent { font-size: 12px; color: rgba(255,255,255,0.4); margin-top: 3px; }
.channel-url {
  font-size: 12px; color: rgba(255,255,255,0.3); margin-top: 6px;
  padding: 8px 12px; background: rgba(0,0,0,0.4); border-radius: 8px;
  font-family: monospace; word-break: break-all; cursor: pointer; transition: background .2s;
}
.channel-url:hover { background: rgba(6,182,212,0.08); }
.channel-actions { display: flex; gap: 8px; align-items: center; position: relative; z-index: 1; }

/* Modal */
.modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(0,0,0,.65);
  z-index: 100; justify-content: center; align-items: flex-start;
  padding: 48px 20px; overflow-y: auto; backdrop-filter: blur(8px);
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--modal-bg); border: 1px solid var(--border-color);
  border-radius: var(--card-radius-lg); width: 100%; max-width: 660px; padding: 40px;
  animation: modalIn .3s cubic-bezier(0.4, 0, 0.2, 1); position: relative;
  box-shadow: 0 24px 80px rgba(0,0,0,0.3);
}
.modal::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: conic-gradient(from var(--angle), transparent 40%, rgba(6,182,212,0.25), rgba(34,211,238,0.15), transparent 60%);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
@keyframes modalIn { from { opacity: 0; transform: translateY(20px) scale(0.97); } to { opacity: 1; transform: translateY(0) scale(1); } }
.modal-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 28px; position: relative; z-index: 1; }
.modal-header h2 { font-size: 20px; font-weight: 600; }
.modal-close {
  background: none; border: none; color: rgba(255,255,255,0.4);
  font-size: 22px; cursor: pointer; padding: 6px 10px; border-radius: 8px; transition: all var(--transition-default);
}
.modal-close:hover { color: #fff; background: rgba(255,255,255,0.06); }

/* Form */
.form-group { margin-bottom: 18px; position: relative; z-index: 1; }
.form-group label {
  display: block; font-size: 12px; font-weight: 500;
  color: rgba(255,255,255,0.45); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.3px;
}
.form-group label .req { color: #ef4444; }
.form-input {
  width: 100%; padding: 12px 16px; background: var(--input-bg);
  border: 1px solid var(--border-color); border-radius: 12px;
  color: var(--text-primary); font-family: inherit; font-size: 14px; transition: all var(--transition-default);
  height: 44px;
}
.form-input:focus { outline: none; border-color: #06b6d4; box-shadow: 0 0 0 3px rgba(6,182,212,0.1); }
textarea.form-input { resize: vertical; min-height: 88px; height: auto; }
select.form-input {
  cursor: pointer; appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 8L1 3h10z'/%3E%3C/svg%3E");
  background-repeat: no-repeat; background-position: right 16px center;
}
.form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; position: relative; z-index: 1; }
.form-submit-row { display: flex; gap: 12px; justify-content: flex-end; margin-top: 28px; position: relative; z-index: 1; }

/* Radio group */
.radio-group { display: flex; gap: 12px; margin-top: 6px; position: relative; z-index: 1; }
.radio-option {
  display: flex; align-items: center; gap: 6px; cursor: pointer;
  font-size: 13px; color: rgba(255,255,255,0.6);
}
.radio-option input[type="radio"] { accent-color: #06b6d4; }

/* File upload zone */
.upload-zone {
  border: 2px dashed rgba(255,255,255,0.08); border-radius: 16px; padding: 32px;
  text-align: center; cursor: pointer; transition: all var(--transition-default); position: relative; z-index: 1;
}
.upload-zone:hover, .upload-zone.dragover { border-color: #06b6d4; background: rgba(6,182,212,0.03); }
.upload-zone p { font-size: 13px; color: rgba(255,255,255,0.4); margin-top: 8px; }
.file-list { margin-top: 12px; position: relative; z-index: 1; }
.file-item {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding: 8px 12px; background: rgba(0,0,0,0.3); border-radius: 8px; margin-bottom: 6px;
  font-size: 13px; color: rgba(255,255,255,0.6);
}
.file-item .file-remove { color: #ef4444; cursor: pointer; font-size: 11px; padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(239,68,68,0.3); background: none; }
.file-item .file-remove:hover { background: #ef4444; color: #fff; }

/* Section divider in form */
.form-section-title {
  font-size: 12px; font-weight: 600; color: #22d3ee; text-transform: uppercase;
  letter-spacing: 0.6px; margin: 24px 0 14px; padding-top: 20px;
  border-top: 1px solid rgba(255,255,255,0.05); position: relative; z-index: 1;
}

/* Empty */
.empty { text-align: center; padding: 100px 24px; color: rgba(255,255,255,0.35); }
.empty-icon { font-size: 48px; margin-bottom: 20px; opacity: .25; }
.empty p { margin-bottom: 24px; font-size: 14px; }

/* Toast */
.toast {
  position: fixed; bottom: 28px; right: 28px; padding: 14px 24px;
  border-radius: 14px; font-size: 14px; font-weight: 500; z-index: 200;
  animation: toastIn .3s cubic-bezier(0.4, 0, 0.2, 1); backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px rgba(0,0,0,0.2);
}
.toast-success { background: rgba(34,197,94,0.9); color: #000; }
.toast-error { background: rgba(239,68,68,0.9); color: #fff; }
@keyframes toastIn { from { opacity: 0; transform: translateY(16px); } to { opacity: 1; transform: translateY(0); } }

/* Mobile toggle */
.mobile-toggle {
  display: none; position: fixed; top: 16px; left: 16px; z-index: 60;
  width: 44px; height: 44px; background: rgba(0,0,0,0.8);
  border: 1px solid rgba(255,255,255,0.06); border-radius: 12px;
  color: #fff; font-size: 20px; cursor: pointer; align-items: center; justify-content: center;
  transition: all var(--transition-default);
}

/* Copy button */
.copy-btn {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.5); font-size: 11px; padding: 6px 14px; border-radius: 8px;
  cursor: pointer; transition: all var(--transition-default);
}
.copy-btn:hover { background: rgba(6,182,212,0.1); color: #22d3ee; border-color: rgba(6,182,212,0.3); }

/* Responsive */
@media(max-width:768px){
  .sidebar { transform: translateX(-100%); width: 260px; }
  .sidebar.open { transform: translateX(0); }
  .main-area { margin-left: 0; padding-top: 60px; }
  .mobile-toggle { display: flex; }
  .form-row { grid-template-columns: 1fr; }
  .detail-stats { grid-template-columns: 1fr 1fr 1fr; }
  .agents-grid { grid-template-columns: 1fr; }
}

/* ── Login Screen ── */
.login-overlay {
  position: fixed; inset: 0; z-index: 999;
  background: linear-gradient(135deg, #050510 0%, #0a0a1a 40%, #0d1117 100%);
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.3s ease;
}
.login-overlay.hidden { display: none; }
.login-card {
  width: 460px; max-width: 92vw; padding: 56px 48px;
  background: rgba(255,255,255,0.03);
  backdrop-filter: blur(32px); -webkit-backdrop-filter: blur(32px);
  border: 1px solid rgba(255,255,255,0.06);
  border-radius: 28px;
  position: relative;
  box-shadow: 0 0 100px rgba(6,182,212,0.03), 0 32px 64px rgba(0,0,0,0.4);
  animation: modalIn .4s cubic-bezier(0.4, 0, 0.2, 1);
}
.login-card::before {
  content: ''; position: absolute; inset: 0; border-radius: inherit; padding: 1px;
  background: conic-gradient(from var(--angle), transparent 40%, rgba(6,182,212,0.25), rgba(34,211,238,0.15), transparent 60%);
  animation: led-rotate 4s linear infinite;
  -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
  -webkit-mask-composite: xor; mask-composite: exclude;
  pointer-events: none;
}
.login-logo {
  text-align: center; margin-bottom: 40px;
}
.login-logo .icon-wrap {
  width: 60px; height: 60px; margin: 0 auto 18px;
  background: linear-gradient(135deg, #06b6d4, #3b82f6);
  border-radius: 16px; display: flex; align-items: center; justify-content: center;
  font-size: 26px; color: #000; font-weight: 800;
}
.login-logo h1 {
  font-size: 24px; font-weight: 700; color: #fff; letter-spacing: -0.5px;
}
.login-logo p {
  font-size: 14px; color: rgba(255,255,255,0.3); margin-top: 8px;
}
.login-field {
  margin-bottom: 22px; position: relative; z-index: 1;
}
.login-field label {
  display: block; font-size: 12px; font-weight: 500;
  color: rgba(255,255,255,0.4); margin-bottom: 8px;
  text-transform: uppercase; letter-spacing: 0.4px;
}
.login-field input {
  width: 100%; padding: 14px 18px; height: 48px;
  background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.06);
  border-radius: 12px; color: #e5e5e5; font-family: inherit; font-size: 15px;
  transition: all var(--transition-default); outline: none;
}
.login-field input:focus {
  border-color: #06b6d4; box-shadow: 0 0 0 3px rgba(6,182,212,0.1);
}
.login-submit {
  width: 100%; padding: 14px 24px; height: 48px; border: none; border-radius: 12px;
  background: linear-gradient(135deg, #06b6d4, #3b82f6);
  color: #000; font-family: inherit; font-size: 15px; font-weight: 600;
  cursor: pointer; transition: all var(--transition-default); margin-top: 12px;
  position: relative; z-index: 1;
}
.login-submit:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 28px rgba(6,182,212,0.25);
}
.login-error {
  color: #ef4444; font-size: 13px; text-align: center;
  margin-top: 16px; min-height: 20px;
  position: relative; z-index: 1;
}

/* ── Theme Toggle ── */
.theme-toggle {
  background: rgba(255,255,255,0.04); border: 1px solid rgba(255,255,255,0.06);
  color: rgba(255,255,255,0.5); width: 40px; height: 40px; border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  cursor: pointer; transition: all var(--transition-default); font-size: 16px;
}
.theme-toggle:hover { background: rgba(255,255,255,0.08); color: #fff; }
[data-theme="light"] .theme-toggle {
  background: rgba(0,0,0,0.03); border-color: rgba(0,0,0,0.06);
  color: rgba(0,0,0,0.5);
}
[data-theme="light"] .theme-toggle:hover { background: rgba(0,0,0,0.08); color: #111; }

/* ── Light mode text overrides ── */
[data-theme="light"] .text-white\\/80, [data-theme="light"] .text-white\\/70,
[data-theme="light"] .text-white\\/60, [data-theme="light"] .text-white\\/50 { color: var(--text-secondary) !important; }
[data-theme="light"] .text-white\\/40, [data-theme="light"] .text-white\\/30 { color: var(--text-dim) !important; }
[data-theme="light"] .text-white\\/20, [data-theme="light"] .text-white\\/15 { color: var(--text-muted) !important; }
[data-theme="light"] .text-white { color: var(--text-primary) !important; }
[data-theme="light"] .border-white\\/5, [data-theme="light"] .border-white\\/\\[0\\.04\\] { border-color: var(--border-color) !important; }
[data-theme="light"] .stat-number { background: linear-gradient(135deg, #111, #475569); -webkit-background-clip: text; }
[data-theme="light"] .btn-secondary { background: rgba(0,0,0,0.04); color: #333; border-color: rgba(0,0,0,0.1); }
[data-theme="light"] .btn-secondary:hover { background: rgba(0,0,0,0.08); border-color: rgba(0,0,0,0.2); }
[data-theme="light"] .btn-led { background: rgba(0,0,0,0.03); border-color: rgba(0,0,0,0.08); color: #111; }
[data-theme="light"] .modal-overlay { background: rgba(0,0,0,0.35); backdrop-filter: blur(4px); }
[data-theme="light"] .modal { background: var(--modal-bg); border-color: var(--border-color); box-shadow: 0 24px 80px rgba(0,0,0,0.1); }
[data-theme="light"] .form-group label { color: var(--text-dim); }
[data-theme="light"] .mobile-toggle { background: rgba(255,255,255,0.95); border-color: rgba(0,0,0,0.06); color: #111; }
[data-theme="light"] .main-area { background: var(--bg-primary); }
[data-theme="light"] select { background-color: var(--bg-card-solid) !important; color: var(--text-primary) !important; border-color: var(--border-color) !important; }
[data-theme="light"] .agent-card { border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .agent-card:hover { box-shadow: 0 12px 40px rgba(0,0,0,0.06), 0 2px 8px rgba(0,0,0,0.04); }
[data-theme="light"] .channel-card { border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .detail-section { border-color: rgba(0,0,0,0.06); }
[data-theme="light"] .upload-zone { border-color: rgba(0,0,0,0.1); }
[data-theme="light"] .upload-zone:hover { border-color: #0891b2; background: rgba(8,145,178,0.03); }
[data-theme="light"] .toast { box-shadow: 0 8px 32px rgba(0,0,0,0.12); }
[data-theme="light"] .login-card { background: rgba(255,255,255,0.95); border-color: rgba(0,0,0,0.06); box-shadow: 0 24px 80px rgba(0,0,0,0.08); }
[data-theme="light"] .card-hover:hover { box-shadow: 0 8px 40px rgba(0,0,0,0.06), 0 2px 12px rgba(0,0,0,0.04); }
</style>
</head>
<body>
<script>
// Restore theme BEFORE paint
(function(){
  var t = localStorage.getItem('agents_theme') || 'dark';
  if (t === 'light') document.documentElement.setAttribute('data-theme', 'light');
})();
</script>

<!-- Login Overlay -->
<div class="login-overlay" id="login-overlay">
  <div class="login-card">
    <div class="login-logo">
      <div class="icon-wrap">A</div>
      <h1>Agents</h1>
      <p>Painel de Gerenciamento</p>
    </div>
    <form id="login-form" onsubmit="doLogin(event)">
      <div class="login-field">
        <label>Email</label>
        <input type="email" id="login-email" placeholder="seu@email.com" required autocomplete="email">
      </div>
      <div class="login-field">
        <label>Senha</label>
        <input type="password" id="login-password" placeholder="********" required autocomplete="current-password">
      </div>
      <button type="submit" class="login-submit" id="login-btn">Entrar</button>
      <div class="login-error" id="login-error"></div>
    </form>
  </div>
</div>

<!-- App Wrapper (hidden until auth) -->
<div id="app-wrapper" style="display:none;width:100%;min-height:100vh">

<!-- Mobile toggle -->
<button class="mobile-toggle" onclick="document.querySelector('.sidebar').classList.toggle('open')">&#9776;</button>

<!-- Sidebar -->
<aside class="sidebar" id="sidebar">
  <div class="sidebar-logo">
    <div class="icon">A</div>
    <span>Agents</span>
  </div>
  <nav class="sidebar-nav">
    <div class="nav-item active" data-page="dashboard" onclick="navigate('dashboard')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zm10 0a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z"/></svg></span>
      Dashboard
    </div>
    <div class="nav-item" data-page="agents" onclick="navigate('agents')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg></span>
      Agentes
    </div>
    <div class="nav-item" data-page="vendas" onclick="navigate('vendas')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg></span>
      Vendas
    </div>
    <div class="nav-item" data-page="prospeccao" onclick="navigate('prospeccao')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg></span>
      Prospect Insta
    </div>
    <div class="nav-item" data-page="prospect-b2b" onclick="navigate('prospect-b2b')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/></svg></span>
      Prospect Google
    </div>
    <div class="nav-item" data-page="channels" onclick="navigate('channels')">
      <span class="nav-icon"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg></span>
      Canais
    </div>
  </nav>
  <div class="sidebar-footer">
    <button class="logout-btn" onclick="logout()">
      <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
      Sair
    </button>
    agents.{{DOMINIO_AI}}
  </div>
</aside>

<!-- Main Content -->
<div class="main-area">

  <!-- DASHBOARD PAGE -->
  <div class="page active" id="page-dashboard">
    <!-- Header -->
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80" id="dash-header-title">Agents Dashboard</span>
      </div>
      <div class="flex items-center gap-4">
        <select id="dashAgentSelect" onchange="onDashAgentSelect(this.value)" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:6px 12px;border-radius:8px;font-size:12px;font-family:Inter,sans-serif;cursor:pointer;outline:none;min-width:180px">
          <option value="all">Todos os Agentes</option>
        </select>
        <select id="periodFilter" onchange="onPeriodChange(this.value)" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:6px 12px;border-radius:8px;font-size:12px;font-family:Inter,sans-serif;cursor:pointer;outline:none">
          <option value="today">Hoje</option>
          <option value="yesterday">Ontem</option>
          <option value="7d" selected>7 dias</option>
          <option value="30d">30 dias</option>
          <option value="all">Todo periodo</option>
          <option value="custom">Personalizado</option>
        </select>
        <div id="customDateRange" style="display:none;gap:8px;align-items:center">
          <input type="date" id="dateFrom" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 8px;border-radius:6px;font-size:11px">
          <span style="color:rgba(255,255,255,0.3)">ate</span>
          <input type="date" id="dateTo" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 8px;border-radius:6px;font-size:11px">
          <button onclick="applyCustomDate()" style="background:rgba(34,211,238,0.2);border:1px solid rgba(34,211,238,0.3);color:#22d3ee;padding:4px 12px;border-radius:6px;font-size:11px;cursor:pointer">Aplicar</button>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()" title="Alternar tema">
          <span id="theme-icon-dash">&#9790;</span>
        </button>
        <button class="theme-toggle" onclick="toggleFullscreen()" title="Tela cheia" id="btnFullscreen">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>
        </button>
        <span class="text-xs text-white/40" id="currentDate"></span>
        <div class="w-2 h-2 rounded-full bg-green-400 pulse-slow"></div>
        <span class="text-xs text-white/80">Live</span>
      </div>
    </header>

    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8 space-y-6">
      <!-- General dashboard container -->
      <div id="dash-general">
        <!-- Top: Profile + Metric Cards -->
        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
          <!-- Profile Card -->
          <div class="lg:col-span-4 glass-strong rounded-3xl p-8 gradient-border card-hover">
            <div class="flex items-start gap-4">
              <div class="w-20 h-20 rounded-full bg-gradient-to-br from-blue-500 via-purple-500 to-cyan-400 p-[3px] flex-shrink-0">
                <div class="w-full h-full rounded-full bg-black flex items-center justify-center">
                  <svg class="w-10 h-10 text-white/60" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714a2.25 2.25 0 00.659 1.591L19 14.5M14.25 3.104c.251.023.501.05.75.082M19 14.5l-2.47 2.47a2.25 2.25 0 01-1.591.659H9.061a2.25 2.25 0 01-1.591-.659L5 14.5m14 0V5a2 2 0 00-2-2H7a2 2 0 00-2 2v9.5"/></svg>
                </div>
              </div>
              <div class="min-w-0">
                <h2 class="text-lg font-bold truncate">Agent Manager</h2>
                <p class="text-sm text-white/80 font-medium">agents.{{DOMINIO_AI}}</p>
              </div>
            </div>
            <p class="mt-4 text-sm text-white/50 leading-relaxed">Painel unificado de gerenciamento de agentes SDR com IA. Controle, monitore e escale seus agentes de vendas automatizados.</p>
            <div class="mt-5 flex items-center justify-between text-center">
              <div>
                <p class="text-base font-bold stat-number" id="ds-total">0</p>
                <p class="text-[11px] text-white/30 mt-0.5">agentes</p>
              </div>
              <div class="w-px h-8 bg-white/5"></div>
              <div>
                <p class="text-base font-bold stat-number" id="ds-active">0</p>
                <p class="text-[11px] text-white/30 mt-0.5">ativos</p>
              </div>
              <div class="w-px h-8 bg-white/5"></div>
              <div>
                <p class="text-base font-bold stat-number" id="ds-contacts">0</p>
                <p class="text-[11px] text-white/30 mt-0.5">contatos</p>
              </div>
            </div>
          </div>

          <!-- Metric Cards Grid -->
          <div class="lg:col-span-8 grid grid-cols-2 sm:grid-cols-3 gap-4">
            <!-- Card: Agentes Ativos -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover glow-blue">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Ativos</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-active2">0</p>
              <p class="text-xs text-white/30 mt-1">agentes online</p>
            </div>
            <!-- Card: Msgs Recebidas -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover glow-purple">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-gray-300/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Msgs IN</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-in">0</p>
              <p class="text-xs text-white/30 mt-1">recebidas</p>
            </div>
            <!-- Card: Msgs Enviadas -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover glow-cyan">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-cyan-500/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Msgs OUT</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-out">0</p>
              <p class="text-xs text-white/30 mt-1">enviadas</p>
            </div>
            <!-- Card: Contatos -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-white/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Contatos</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-contacts2">0</p>
              <p class="text-xs text-white/30 mt-1">total</p>
            </div>
            <!-- Card: Taxa Resposta -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-pink-500/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Tx Resposta</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-rate">0%</p>
              <p class="text-xs text-white/30 mt-1">taxa de resposta</p>
            </div>
            <!-- Card: Total Agentes -->
            <div class="glass rounded-3xl p-7 gradient-border card-hover">
              <div class="flex items-center gap-2 mb-3">
                <div class="w-8 h-8 rounded-lg bg-orange-500/10 flex items-center justify-center">
                  <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
                </div>
                <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Total</span>
              </div>
              <p class="text-3xl font-bold stat-number" id="ds-total2">0</p>
              <p class="text-xs text-white/30 mt-1">agentes</p>
            </div>
          </div>
        </div>

        <!-- Sales Cards Row -->
        <div class="grid grid-cols-2 gap-4 mt-4">
          <div class="glass rounded-3xl p-7 gradient-border card-hover glow-cyan">
            <div class="flex items-center gap-2 mb-3">
              <div class="w-8 h-8 rounded-lg bg-green-500/10 flex items-center justify-center">
                <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              </div>
              <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider" id="ds-sales-label">Vendas Hoje</span>
            </div>
            <p class="text-3xl font-bold stat-number" id="ds-sales-today">0</p>
            <p class="text-xs text-white/30 mt-1">vendas</p>
          </div>
          <div class="glass rounded-3xl p-7 gradient-border card-hover glow-blue">
            <div class="flex items-center gap-2 mb-3">
              <div class="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center">
                <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"/></svg>
              </div>
              <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Faturamento</span>
            </div>
            <p class="text-3xl font-bold stat-number" id="ds-amount-today">R$ 0</p>
            <p class="text-xs text-white/30 mt-1">total hoje</p>
          </div>
        </div>

        <!-- Funnel: Agent Activity -->
        <div class="glass-strong rounded-3xl p-8 gradient-border mt-6">
          <div class="flex items-center gap-3 mb-6">
            <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center">
              <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
            </div>
            <h3 class="text-base font-semibold">Funil de Atividade</h3>
          </div>
          <div class="space-y-3" id="dash-funnel">
            <div class="flex items-center gap-4">
              <span class="text-xs text-white/40 w-28 text-right">Total Agentes</span>
              <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
                <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-blue-500/30 to-blue-500/10" style="width:100%" id="funnel-total-bar"></div>
                <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50" id="funnel-total-val">0</span>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <span class="text-xs text-white/40 w-28 text-right">Ativos</span>
              <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
                <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-green-500/30 to-green-500/10" style="width:0%" id="funnel-active-bar"></div>
                <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50" id="funnel-active-val">0</span>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <span class="text-xs text-white/40 w-28 text-right">Msgs Recebidas</span>
              <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
                <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-cyan-500/30 to-cyan-500/10" style="width:0%" id="funnel-in-bar"></div>
                <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50" id="funnel-in-val">0</span>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <span class="text-xs text-white/40 w-28 text-right">Msgs Enviadas</span>
              <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
                <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-purple-500/30 to-purple-500/10" style="width:0%" id="funnel-out-bar"></div>
                <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50" id="funnel-out-val">0</span>
              </div>
            </div>
            <div class="flex items-center gap-4">
              <span class="text-xs text-white/40 w-28 text-right">Contatos</span>
              <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
                <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-pink-500/30 to-pink-500/10" style="width:0%" id="funnel-contacts-bar"></div>
                <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50" id="funnel-contacts-val">0</span>
              </div>
            </div>
          </div>
        </div>

        <!-- Table: Atividade Recente -->
        <div class="glass-strong rounded-3xl p-8 gradient-border mt-6">
          <div class="flex items-center gap-3 mb-6">
            <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500/20 to-blue-500/20 flex items-center justify-center">
              <svg class="w-4 h-4 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"/></svg>
            </div>
            <h3 class="text-base font-semibold">Atividade Recente</h3>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead>
                <tr class="border-b border-white/5">
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Agente</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Empresa</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Status</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">IN</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">OUT</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Contatos</th>
                  <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider"></th>
                </tr>
              </thead>
              <tbody id="dash-recent">
                <tr>
                  <td colspan="7" class="text-center py-12 text-white/20 text-sm">
                    <div class="flex flex-col items-center gap-3">
                      <svg class="w-10 h-10 text-white/10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/></svg>
                      <span>Nenhuma atividade ainda</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>

        <!-- Bottom Charts Grid -->
        <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 mt-6">
          <!-- Card 1: Distribuicao por Agente (Donut) -->
          <div class="glass-strong rounded-3xl p-8 gradient-border">
            <div class="flex items-center gap-3 mb-6">
              <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500/20 to-purple-500/20 flex items-center justify-center">
                <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20.488 9H15V3.512A9.025 9.025 0 0120.488 9z"/></svg>
              </div>
              <h3 class="text-sm font-semibold">Perfil que Mais Respondeu</h3>
            </div>
            <div class="flex justify-center mb-5">
              <div class="relative">
                <svg width="160" height="160" viewBox="0 0 160 160" id="donut-chart">
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(255,255,255,0.03)" stroke-width="20"/>
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(59,130,246,0.3)" stroke-width="20" stroke-dasharray="0 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)" id="donut-seg-0"/>
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(139,92,246,0.3)" stroke-width="20" stroke-dasharray="0 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)" id="donut-seg-1"/>
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(236,72,153,0.3)" stroke-width="20" stroke-dasharray="0 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)" id="donut-seg-2"/>
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(6,182,212,0.3)" stroke-width="20" stroke-dasharray="0 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)" id="donut-seg-3"/>
                  <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(245,158,11,0.3)" stroke-width="20" stroke-dasharray="0 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)" id="donut-seg-4"/>
                </svg>
                <div class="absolute inset-0 flex flex-col items-center justify-center">
                  <span class="text-xl font-bold stat-number" id="donut-total">0</span>
                  <span class="text-[10px] text-white/30">contatos</span>
                </div>
              </div>
            </div>
            <div class="space-y-2" id="donut-legend">
              <div class="flex items-center justify-between text-xs">
                <div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full bg-blue-500/60"></div><span class="text-white/40">Agente 1</span></div>
                <span class="text-white/30">0%</span>
              </div>
            </div>
          </div>

          <!-- Card 2: Melhores Horarios (Heatmap) -->
          <div class="glass-strong rounded-3xl p-8 gradient-border">
            <div class="flex items-center gap-3 mb-6">
              <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500/20 to-cyan-500/20 flex items-center justify-center">
                <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              </div>
              <h3 class="text-sm font-semibold">Melhores Horarios</h3>
            </div>
            <div id="heatmap-container">
              <div class="grid grid-cols-8 gap-1.5">
                <div class="text-[10px] text-white/20 text-center"></div>
                <div class="text-[10px] text-white/20 text-center">Seg</div>
                <div class="text-[10px] text-white/20 text-center">Ter</div>
                <div class="text-[10px] text-white/20 text-center">Qua</div>
                <div class="text-[10px] text-white/20 text-center">Qui</div>
                <div class="text-[10px] text-white/20 text-center">Sex</div>
                <div class="text-[10px] text-white/20 text-center">Sab</div>
                <div class="text-[10px] text-white/20 text-center">Dom</div>
              </div>
              <div class="grid grid-cols-8 gap-1.5 mt-1" id="heatmap-grid"></div>
            </div>
            <div class="flex items-center justify-between mt-4">
              <span class="text-[10px] text-white/20">Menos</span>
              <div class="flex gap-1">
                <div class="w-4 h-4 rounded bg-white/[0.02]"></div>
                <div class="w-4 h-4 rounded bg-cyan-500/15"></div>
                <div class="w-4 h-4 rounded bg-cyan-500/30"></div>
                <div class="w-4 h-4 rounded bg-cyan-500/50"></div>
                <div class="w-4 h-4 rounded bg-cyan-500/70"></div>
              </div>
              <span class="text-[10px] text-white/20">Mais</span>
            </div>
          </div>

          <!-- Card 3: Historico Semanal (Bars) -->
          <div class="glass-strong rounded-3xl p-8 gradient-border">
            <div class="flex items-center gap-3 mb-6">
              <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500/20 to-pink-500/20 flex items-center justify-center">
                <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
              </div>
              <h3 class="text-sm font-semibold">Historico Semanal</h3>
            </div>
            <div class="flex items-center gap-2 mb-4">
              <div class="flex items-center gap-1.5"><div class="w-2.5 h-2.5 rounded-full bg-cyan-500/50"></div><span class="text-[10px] text-white/30">Recebidas</span></div>
              <div class="flex items-center gap-1.5"><div class="w-2.5 h-2.5 rounded-full bg-red-500/50"></div><span class="text-[10px] text-white/30">Enviadas</span></div>
            </div>
            <div class="flex items-end gap-3 h-48" id="weekly-bars">
              <div class="flex-1 flex flex-col items-center gap-1">
                <div class="w-full flex gap-1 items-end justify-center h-40">
                  <div class="w-5 rounded-t bg-cyan-500/15 min-h-[4px]" style="height:4px" id="wb-in-0"></div>
                  <div class="w-5 rounded-t bg-red-500/15 min-h-[4px]" style="height:4px" id="wb-out-0"></div>
                </div>
                <span class="text-[10px] text-white/20" id="wb-label-0">Sem 1</span>
                <span class="text-[10px] text-white/30 font-medium" id="wb-val-0">0 / 0</span>
              </div>
              <div class="flex-1 flex flex-col items-center gap-1">
                <div class="w-full flex gap-1 items-end justify-center h-40">
                  <div class="w-5 rounded-t bg-cyan-500/15 min-h-[4px]" style="height:4px" id="wb-in-1"></div>
                  <div class="w-5 rounded-t bg-red-500/15 min-h-[4px]" style="height:4px" id="wb-out-1"></div>
                </div>
                <span class="text-[10px] text-white/20" id="wb-label-1">Sem 2</span>
                <span class="text-[10px] text-white/30 font-medium" id="wb-val-1">0 / 0</span>
              </div>
              <div class="flex-1 flex flex-col items-center gap-1">
                <div class="w-full flex gap-1 items-end justify-center h-40">
                  <div class="w-5 rounded-t bg-cyan-500/15 min-h-[4px]" style="height:4px" id="wb-in-2"></div>
                  <div class="w-5 rounded-t bg-red-500/15 min-h-[4px]" style="height:4px" id="wb-out-2"></div>
                </div>
                <span class="text-[10px] text-white/20" id="wb-label-2">Sem 3</span>
                <span class="text-[10px] text-white/30 font-medium" id="wb-val-2">0 / 0</span>
              </div>
              <div class="flex-1 flex flex-col items-center gap-1">
                <div class="w-full flex gap-1 items-end justify-center h-40">
                  <div class="w-5 rounded-t bg-cyan-500/15 min-h-[4px]" style="height:4px" id="wb-in-3"></div>
                  <div class="w-5 rounded-t bg-red-500/15 min-h-[4px]" style="height:4px" id="wb-out-3"></div>
                </div>
                <span class="text-[10px] text-white/20" id="wb-label-3">Sem 4</span>
                <span class="text-[10px] text-white/30 font-medium" id="wb-val-3">0 / 0</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- Individual agent dashboard container (hidden by default) -->
      <div id="dash-individual" style="display:none"></div>

      <!-- Footer -->
      <div class="text-center py-4">
        <p class="text-[11px] text-white/15">Agent Manager Dashboard &middot; Powered by Naia AI &middot; {{DOMINIO_AI}}</p>
      </div>
    </div>
  </div>

  <!-- AGENTS PAGE -->
  <div class="page" id="page-agents">
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80">Agentes SDR</span>
      </div>
      <button class="btn btn-primary" onclick="openCreate()">+ Novo Agente</button>
    </header>
    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8">
      <div id="agents-list-view">
        <div class="agents-grid" id="agents-grid"></div>
      </div>
      <div id="agents-detail-view" style="display:none"></div>
    </div>
  </div>

  <!-- VENDAS PAGE -->
  <div class="page" id="page-vendas">
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500 to-cyan-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80">Vendas</span>
      </div>
      <div class="flex items-center gap-4">
        <select id="periodFilterVendas" onchange="onPeriodChange(this.value)" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:6px 12px;border-radius:8px;font-size:12px;font-family:Inter,sans-serif;cursor:pointer;outline:none">
          <option value="today">Hoje</option>
          <option value="yesterday">Ontem</option>
          <option value="7d" selected>7 dias</option>
          <option value="30d">30 dias</option>
          <option value="all">Todo periodo</option>
          <option value="custom">Personalizado</option>
        </select>
        <div id="customDateRangeVendas" style="display:none;gap:8px;align-items:center">
          <input type="date" id="dateFromVendas" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 8px;border-radius:6px;font-size:11px">
          <span style="color:rgba(255,255,255,0.3)">ate</span>
          <input type="date" id="dateToVendas" style="background:rgba(255,255,255,0.06);border:1px solid rgba(255,255,255,0.1);color:#fff;padding:4px 8px;border-radius:6px;font-size:11px">
          <button onclick="applyCustomDate()" style="background:rgba(34,211,238,0.2);border:1px solid rgba(34,211,238,0.3);color:#22d3ee;padding:4px 12px;border-radius:6px;font-size:11px;cursor:pointer">Aplicar</button>
        </div>
        <div class="w-2 h-2 rounded-full bg-green-400 pulse-slow"></div>
        <span class="text-xs text-white/80">Live</span>
      </div>
    </header>
    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8 space-y-6">
      <!-- Summary Cards Row 1 -->
      <div class="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <div class="glass rounded-3xl p-7 gradient-border card-hover glow-cyan">
          <div class="flex items-center gap-2 mb-3">
            <div class="w-8 h-8 rounded-lg bg-green-500/10 flex items-center justify-center">
              <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
            </div>
            <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Vendas Hoje</span>
          </div>
          <p class="text-3xl font-bold stat-number" id="v-sales-today">0</p>
          <p class="text-xs text-white/30 mt-1">aprovadas</p>
        </div>
        <div class="glass rounded-3xl p-7 gradient-border card-hover glow-blue">
          <div class="flex items-center gap-2 mb-3">
            <div class="w-8 h-8 rounded-lg bg-emerald-500/10 flex items-center justify-center">
              <svg class="w-4 h-4 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z"/></svg>
            </div>
            <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Faturamento Hoje</span>
          </div>
          <p class="text-3xl font-bold stat-number" id="v-amount-today">R$ 0</p>
          <p class="text-xs text-white/30 mt-1">total</p>
        </div>
        <div class="glass rounded-3xl p-7 gradient-border card-hover">
          <div class="flex items-center gap-2 mb-3">
            <div class="w-8 h-8 rounded-lg bg-cyan-500/10 flex items-center justify-center">
              <svg class="w-4 h-4 text-cyan-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
            </div>
            <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Ticket Medio</span>
          </div>
          <p class="text-3xl font-bold stat-number" id="v-avg-ticket">R$ 0</p>
          <p class="text-xs text-white/30 mt-1">media</p>
        </div>
        <div class="glass rounded-3xl p-7 gradient-border card-hover" style="border-color:rgba(239,68,68,0.15)">
          <div class="flex items-center gap-2 mb-3">
            <div class="w-8 h-8 rounded-lg bg-red-500/10 flex items-center justify-center">
              <svg class="w-4 h-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 100 4 2 2 0 000-4z"/></svg>
            </div>
            <span class="text-[11px] text-white/40 font-medium uppercase tracking-wider">Abandonos Hoje</span>
          </div>
          <p class="text-3xl font-bold" style="color:#ef4444" id="v-abandonments-today">0</p>
          <p class="text-xs text-white/30 mt-1">carrinhos</p>
        </div>
      </div>

      <!-- Summary Cards Row 2 -->
      <div class="grid grid-cols-3 gap-4">
        <div class="glass rounded-3xl p-7 gradient-border card-hover">
          <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Vendas 7 dias</div>
          <p class="text-2xl font-bold stat-number" id="v-sales-7d">0</p>
          <p class="text-xs text-white/30 mt-1" id="v-amount-7d">R$ 0</p>
        </div>
        <div class="glass rounded-3xl p-7 gradient-border card-hover">
          <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Vendas 30 dias</div>
          <p class="text-2xl font-bold stat-number" id="v-sales-30d">0</p>
          <p class="text-xs text-white/30 mt-1" id="v-amount-30d">R$ 0</p>
        </div>
        <div class="glass rounded-3xl p-7 gradient-border card-hover">
          <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Taxa Recuperacao</div>
          <p class="text-2xl font-bold stat-number" id="v-recovery-rate">0%</p>
          <p class="text-xs text-white/30 mt-1">abandonos vs vendas</p>
        </div>
      </div>

      <!-- Vendas Aprovadas Table -->
      <div class="glass-strong rounded-3xl p-8 gradient-border">
        <div class="flex items-center gap-3 mb-6">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500/20 to-cyan-500/20 flex items-center justify-center">
            <svg class="w-4 h-4 text-green-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          </div>
          <h3 class="text-base font-semibold">Vendas Aprovadas</h3>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-white/5">
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Data</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Produto</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Comprador</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Email</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Telefone</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Valor</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Plataforma</th>
              </tr>
            </thead>
            <tbody id="v-sales-table">
              <tr><td colspan="7" class="text-center py-12 text-white/20 text-sm">Nenhuma venda registrada</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Carrinho Abandonado Table -->
      <div class="glass-strong rounded-3xl p-8 gradient-border" style="border-color:rgba(239,68,68,0.08)">
        <div class="flex items-center gap-3 mb-6">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-red-500/20 to-orange-500/20 flex items-center justify-center">
            <svg class="w-4 h-4 text-red-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m0 0a2 2 0 100 4 2 2 0 000-4zm-8 2a2 2 0 100 4 2 2 0 000-4z"/></svg>
          </div>
          <h3 class="text-base font-semibold">Carrinho Abandonado</h3>
        </div>
        <div class="overflow-x-auto">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-white/5">
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Data</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Produto</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Nome</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Email</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Telefone</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Evento</th>
                <th class="text-left py-4 px-5 text-[11px] text-white/30 font-medium uppercase tracking-wider">Acao</th>
              </tr>
            </thead>
            <tbody id="v-abandonments-table">
              <tr><td colspan="7" class="text-center py-12 text-white/20 text-sm">Nenhum abandono registrado</td></tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="text-center py-4">
        <p class="text-[11px] text-white/15">Vendas Dashboard &middot; Powered by Naia AI &middot; {{DOMINIO_AI}}</p>
      </div>
    </div>
  </div>

  <!-- PROSPECCAO PAGE -->
  <div class="page" id="page-prospeccao">
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-fuchsia-500 to-pink-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80">Prospeccao Ativa &middot; Ademir</span>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-secondary" onclick="loadProspeccao()">Atualizar</button>
        <button class="btn btn-primary" onclick="ademirRunNow()" id="btn-run-ademir">Rodar Ademir agora</button>
      </div>
    </header>
    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8">
      <!-- Stats cards -->
      <div class="grid grid-cols-2 md:grid-cols-7 gap-3 mb-6" id="prospect-stats">
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Perfis-alvo</div><div class="text-xl font-bold mt-1" id="ps-targets">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Total leads</div><div class="text-xl font-bold mt-1" id="ps-total">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Aguardando</div><div class="text-xl font-bold mt-1 text-blue-300" id="ps-qualified">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Aprovados</div><div class="text-xl font-bold mt-1 text-emerald-300" id="ps-approved">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">DMs enviadas</div><div class="text-xl font-bold mt-1" id="ps-dms">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Com WhatsApp</div><div class="text-xl font-bold mt-1 text-cyan-300" id="ps-whatsapp">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Respostas / Taxa</div><div class="text-xl font-bold mt-1" id="ps-replied-rate">-</div></div>
      </div>

      <!-- Tabs -->
      <div class="flex gap-2 mb-4 border-b border-white/10">
        <button class="px-4 py-2 text-sm border-b-2 border-fuchsia-500 text-fuchsia-400" id="ptab-icp" onclick="switchProspectTab('icp')">ICP</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="ptab-leads" onclick="switchProspectTab('leads')">Leads</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="ptab-dms" onclick="switchProspectTab('dms')">DMs</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="ptab-runs" onclick="switchProspectTab('runs')">Runs</button>
      </div>

      <!-- ICP TAB -->
      <div class="prospect-tab" id="ptab-content-icp">
        <div class="glass-card p-4 mb-4">
          <h3 class="text-sm font-semibold mb-3">Adicionar perfil-alvo</h3>
          <div class="flex flex-col md:flex-row gap-2">
            <input id="target-username" class="form-input flex-1" placeholder="@username (sem o @)" />
            <select id="target-source" class="form-input md:w-64">
              <option value="meu_seguidor">Meus seguidores</option>
              <option value="comentou_em_mim">Comentou nos meus posts</option>
              <option value="comentou_em_alvo">Comentadores deste perfil</option>
            </select>
            <button class="btn btn-primary" onclick="addTarget()">Adicionar</button>
          </div>
        </div>
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-4 py-2">Username</th>
              <th class="text-left px-4 py-2">Tipo</th>
              <th class="text-left px-4 py-2">Adicionado</th>
              <th class="text-left px-4 py-2">Status</th>
              <th class="text-right px-4 py-2">Acoes</th>
            </tr></thead>
            <tbody id="targets-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- LEADS TAB -->
      <div class="prospect-tab hidden" id="ptab-content-leads">
        <!-- Filtros + bulk actions -->
        <div class="glass-card p-3 mb-3 flex flex-wrap items-center gap-2">
          <div class="flex items-center gap-2">
            <span class="text-xs opacity-50">Filtro:</span>
            <button class="lead-filter-btn px-3 py-1.5 text-xs rounded bg-fuchsia-500/20 text-fuchsia-300 border border-fuchsia-500/30" data-filter="" onclick="setLeadFilter('')">Todos</button>
            <button class="lead-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="qualified" onclick="setLeadFilter('qualified')">Aguardando aprovacao</button>
            <button class="lead-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="approved_to_send" onclick="setLeadFilter('approved_to_send')">Aprovados</button>
            <button class="lead-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="dm_sent" onclick="setLeadFilter('dm_sent')">Enviados</button>
            <button class="lead-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="replied" onclick="setLeadFilter('replied')">Responderam</button>
          </div>
          <div class="ml-auto flex items-center gap-2">
            <span class="text-xs opacity-50" id="bulk-selected-count">0 selecionados</span>
            <button class="btn btn-secondary text-xs" id="btn-bulk-approve" onclick="bulkApproveSelected()" disabled>Aprovar selecionados</button>
            <button class="btn btn-primary text-xs" onclick="runSendNow()">Enviar aprovados agora</button>
            <button class="btn btn-secondary text-xs" onclick="enrichWhatsapp()" title="Extrai numeros WhatsApp do briefing existente (sem chamadas HikerAPI)">Reprocessar WhatsApp</button>
          </div>
        </div>

        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-3 py-2 w-8"><input type="checkbox" id="leads-select-all" onchange="toggleSelectAll(this.checked)" /></th>
              <th class="text-left px-4 py-2">@Username</th>
              <th class="text-left px-4 py-2">Bio / Resumo</th>
              <th class="text-left px-4 py-2">Origem</th>
              <th class="text-left px-4 py-2">Status</th>
              <th class="text-left px-4 py-2">Descoberto</th>
              <th class="text-right px-4 py-2">Acoes</th>
            </tr></thead>
            <tbody id="leads-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- DMS TAB -->
      <div class="prospect-tab hidden" id="ptab-content-dms">
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-4 py-2">Lead</th>
              <th class="text-left px-4 py-2">Mensagem</th>
              <th class="text-left px-4 py-2">Enviado</th>
              <th class="text-left px-4 py-2">Status</th>
            </tr></thead>
            <tbody id="dms-tbody"></tbody>
          </table>
        </div>
      </div>

      <!-- RUNS TAB -->
      <div class="prospect-tab hidden" id="ptab-content-runs">
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-4 py-2">Inicio</th>
              <th class="text-left px-4 py-2">Fim</th>
              <th class="text-left px-4 py-2">Trigger</th>
              <th class="text-left px-4 py-2">Descobertos</th>
              <th class="text-left px-4 py-2">Qualificados</th>
              <th class="text-left px-4 py-2">DMs</th>
              <th class="text-left px-4 py-2">Status</th>
            </tr></thead>
            <tbody id="runs-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- PROSPECCAO B2B PAGE -->
  <div class="page" id="page-prospect-b2b">
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z"/><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 11a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80">Prospeccao B2B &middot; Google Maps</span>
      </div>
      <div class="flex gap-2">
        <button class="btn btn-secondary" onclick="loadProspectB2B()">Atualizar</button>
        <button class="btn btn-primary" onclick="b2bRunAll()" id="btn-b2b-run-all">Rodar todos os ICPs</button>
      </div>
    </header>
    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8">
      <div class="grid grid-cols-2 md:grid-cols-7 gap-3 mb-6" id="b2b-stats">
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">ICPs ativos</div><div class="text-xl font-bold mt-1" id="b2b-targets">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Empresas</div><div class="text-xl font-bold mt-1" id="b2b-total">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Aguardando</div><div class="text-xl font-bold mt-1 text-blue-300" id="b2b-discovered">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Aprovadas</div><div class="text-xl font-bold mt-1 text-emerald-300" id="b2b-approved">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">WhatsApp</div><div class="text-xl font-bold mt-1 text-cyan-300" id="b2b-whatsapp">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Instagram</div><div class="text-xl font-bold mt-1 text-fuchsia-300" id="b2b-instagram">-</div></div>
        <div class="glass-card p-3"><div class="text-[10px] uppercase opacity-50">Mensagens</div><div class="text-xl font-bold mt-1" id="b2b-sent">-</div></div>
      </div>
      <div class="flex gap-2 mb-4 border-b border-white/10">
        <button class="px-4 py-2 text-sm border-b-2 border-cyan-500 text-cyan-400" id="b2btab-icp" onclick="switchB2BTab('icp')">ICP B2B</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="b2btab-companies" onclick="switchB2BTab('companies')">Empresas</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="b2btab-messages" onclick="switchB2BTab('messages')">Mensagens</button>
        <button class="px-4 py-2 text-sm text-white/50 hover:text-white" id="b2btab-runs" onclick="switchB2BTab('runs')">Runs</button>
      </div>
      <div class="b2b-tab" id="b2btab-content-icp">
        <div class="glass-card p-4 mb-4">
          <h3 class="text-sm font-semibold mb-3">Adicionar nova busca de empresas</h3>
          <div class="grid grid-cols-1 md:grid-cols-2 gap-3 mb-3">
            <div>
              <label class="text-xs opacity-60 mb-1 block">Categoria</label>
              <select id="b2b-segment" class="form-input w-full" onchange="onB2BSegmentChange()">
                <option value="">Selecione um segmento...</option>
              </select>
            </div>
            <div>
              <label class="text-xs opacity-60 mb-1 block">Tipo de negocio</label>
              <select id="b2b-business-type" class="form-input w-full" disabled>
                <option value="">Selecione um segmento primeiro</option>
              </select>
            </div>
            <div>
              <label class="text-xs opacity-60 mb-1 block">Estado</label>
              <select id="b2b-state" class="form-input w-full" onchange="onB2BStateChange()">
                <option value="">UF...</option>
              </select>
            </div>
            <div>
              <label class="text-xs opacity-60 mb-1 block">Cidade / Bairro</label>
              <input id="b2b-city" class="form-input w-full" placeholder="Ex: Sao Paulo, Vila Madalena" />
            </div>
            <div>
              <label class="text-xs opacity-60 mb-1 block">Raio (km)</label>
              <input type="number" id="b2b-radius-km" class="form-input w-full" value="5" min="1" max="50" />
            </div>
            <div>
              <label class="text-xs opacity-60 mb-1 block">Max resultados</label>
              <input type="number" id="b2b-max" class="form-input w-full" value="60" min="1" max="200" />
            </div>
          </div>
          <div class="flex gap-2 justify-end">
            <button class="btn btn-secondary" onclick="addB2BTarget()">Salvar ICP</button>
            <button class="btn btn-primary" onclick="addAndRunB2BTarget()">Salvar e rodar agora</button>
          </div>
        </div>
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-4 py-2">Categoria</th>
              <th class="text-left px-4 py-2">Localizacao</th>
              <th class="text-left px-4 py-2">Raio</th>
              <th class="text-left px-4 py-2">Ultima execucao</th>
              <th class="text-right px-4 py-2">Acoes</th>
            </tr></thead>
            <tbody id="b2b-targets-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="b2b-tab hidden" id="b2btab-content-companies">
        <div class="glass-card p-3 mb-3 flex flex-wrap items-center gap-2">
          <div class="flex items-center gap-2">
            <span class="text-xs opacity-50">Filtro:</span>
            <button class="b2b-filter-btn px-3 py-1.5 text-xs rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30" data-filter="" onclick="setB2BFilter('')">Todas</button>
            <button class="b2b-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="discovered" onclick="setB2BFilter('discovered')">Descobertas</button>
            <button class="b2b-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="approved_to_send" onclick="setB2BFilter('approved_to_send')">Aprovadas</button>
            <button class="b2b-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5" data-filter="dm_sent" onclick="setB2BFilter('dm_sent')">Enviadas</button>
            <input type="text" id="b2b-search-q" class="form-input ml-2" placeholder="buscar nome/endereco" oninput="b2bSearchDebounced()" />
          </div>
          <div class="ml-auto flex items-center gap-2">
            <span class="text-xs opacity-50" id="b2b-bulk-count">0 selecionadas</span>
            <button class="btn btn-secondary text-xs" id="btn-b2b-bulk-approve" onclick="b2bBulkApprove()" disabled>Aprovar selecionadas</button>
          </div>
        </div>
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-3 py-2 w-8"><input type="checkbox" id="b2b-select-all" onchange="b2bToggleSelectAll(this.checked)" /></th>
              <th class="text-left px-4 py-2">Nome</th>
              <th class="text-left px-4 py-2">Endereco</th>
              <th class="text-left px-4 py-2">Telefone</th>
              <th class="text-left px-4 py-2">Site / IG</th>
              <th class="text-left px-4 py-2">Categoria</th>
              <th class="text-left px-4 py-2">Status</th>
              <th class="text-right px-4 py-2">Acoes</th>
            </tr></thead>
            <tbody id="b2b-companies-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="b2b-tab hidden" id="b2btab-content-messages">
        <div class="glass-card p-6 text-center text-white/60">
          Mensagens via WhatsApp serao habilitadas na proxima fase (B2B-2). Por enquanto, voce pode aprovar empresas para envio.
        </div>
      </div>
      <div class="b2b-tab hidden" id="b2btab-content-runs">
        <div class="glass-card overflow-hidden">
          <table class="w-full text-sm">
            <thead class="bg-white/5"><tr>
              <th class="text-left px-4 py-2">Inicio</th>
              <th class="text-left px-4 py-2">ICP</th>
              <th class="text-left px-4 py-2">Trigger</th>
              <th class="text-left px-4 py-2">Descobertas</th>
              <th class="text-left px-4 py-2">Novas</th>
              <th class="text-left px-4 py-2">Duplicadas</th>
              <th class="text-left px-4 py-2">IG cross</th>
              <th class="text-left px-4 py-2">API$</th>
              <th class="text-left px-4 py-2">Status</th>
            </tr></thead>
            <tbody id="b2b-runs-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- COMPANY DETAIL MODAL B2B -->
  <div class="modal-overlay" id="modal-b2b-company">
    <div class="modal" style="max-width:760px">
      <div class="modal-header flex items-center justify-between">
        <h2 id="b2b-company-title">Empresa</h2>
        <button onclick="closeModal('modal-b2b-company')" class="text-white/50 hover:text-white">X</button>
      </div>
      <div class="modal-body" id="b2b-company-body"></div>
    </div>
  </div>

  <!-- LEAD DETAIL MODAL -->
  <div class="modal-overlay" id="modal-lead-detail">
    <div class="modal" style="max-width:760px">
      <div class="modal-header flex items-center justify-between">
        <h2 id="lead-detail-title">Lead</h2>
        <button onclick="closeModal('modal-lead-detail')" class="text-white/50 hover:text-white">X</button>
      </div>
      <div class="modal-body" id="lead-detail-body"></div>
    </div>
  </div>

  <!-- CHANNELS PAGE -->
  <div class="page" id="page-channels">
    <header class="glass sticky top-0 z-40 px-8 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-cyan-500 to-blue-600 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 10V3L4 14h7v7l9-11h-7z"/></svg>
        </div>
        <span class="text-sm font-semibold text-white/80">Canais</span>
      </div>
      <button class="btn btn-primary" onclick="openChannelModal()">+ Novo Canal</button>
    </header>
    <div class="max-w-[1440px] mx-auto px-6 sm:px-8 py-8">
      <div id="channels-list"></div>
    </div>
  </div>

</div>

<!-- Agent Create/Edit Modal -->
<div class="modal-overlay" id="modal-agent">
  <div class="modal">
    <div class="modal-header">
      <h2 id="modal-agent-title">Novo Agente</h2>
      <button class="modal-close" onclick="closeModal('modal-agent')">&times;</button>
    </div>
    <form id="agent-form" onsubmit="submitAgentForm(event)">
      <input type="hidden" id="f-id">
      <div class="form-row">
        <div class="form-group">
          <label>Nome do Agente <span class="req">*</span></label>
          <input class="form-input" id="f-name" required placeholder="Ex: Clone {{DONO}}">
        </div>
        <div class="form-group">
          <label>Empresa/Cliente <span class="req">*</span></label>
          <input class="form-input" id="f-company" required placeholder="Ex: {{NICHO_DONO}} Digital">
        </div>
      </div>
      <div class="form-group">
        <label>Personalidade</label>
        <textarea class="form-input" id="f-personality" placeholder="Tom de voz, como falar..."></textarea>
      </div>
      <div class="form-group">
        <label>Produtos/Servicos</label>
        <textarea class="form-input" id="f-products" placeholder="Descreva os produtos ou servicos..."></textarea>
      </div>
      <div class="form-group">
        <label>Links Permitidos (um por linha)</label>
        <textarea class="form-input" id="f-links" placeholder="https://exemplo.com/produto"></textarea>
      </div>
      <div class="form-group">
        <label>Nomes Bloqueados (virgula)</label>
        <input class="form-input" id="f-blocked" placeholder="fulano, ciclano">
      </div>

      <!-- RECEIVE METHOD SECTION -->
      <div class="form-section-title">Recebimento de Mensagens</div>
      <div class="form-group">
        <div class="radio-group">
          <label class="radio-option"><input type="radio" name="receive_method" value="webhook" checked onchange="toggleReceiveMethod()"> Webhook (recomendado)</label>
          <label class="radio-option"><input type="radio" name="receive_method" value="api" onchange="toggleReceiveMethod()"> API</label>
        </div>
      </div>
      <div id="receive-webhook-fields">
        <div class="form-group">
          <label>Webhook URL (gerado automaticamente)</label>
          <div style="display:flex;gap:8px;align-items:center">
            <input class="form-input" id="f-receive-webhook-url" readonly style="color:rgba(255,255,255,0.3);flex:1" placeholder="Salve o agente para gerar a URL">
            <button type="button" class="copy-btn" onclick="copyField('f-receive-webhook-url')">Copiar</button>
          </div>
        </div>
      </div>
      <div id="receive-api-fields" style="display:none">
        <div class="form-group">
          <label>Endpoint de origem</label>
          <input class="form-input" id="f-receive-api-endpoint" placeholder="https://api.exemplo.com/messages">
        </div>
      </div>

      <!-- SEND METHOD SECTION -->
      <div class="form-section-title">Envio de Mensagens</div>
      <div class="form-group">
        <div class="radio-group">
          <label class="radio-option"><input type="radio" name="send_method" value="api" checked onchange="toggleSendMethod()"> API do {{PRODUTO_DONO}} (recomendado)</label>
          <label class="radio-option"><input type="radio" name="send_method" value="webhook" onchange="toggleSendMethod()"> Webhook</label>
        </div>
      </div>
      <div id="send-api-fields">
        <div class="form-row">
          <div class="form-group">
            <label>API Key do CRM <span class="req">*</span></label>
            <input class="form-input" id="f-ghl-key" placeholder="pit-xxxxxx">
          </div>
          <div class="form-group">
            <label>Location ID do CRM <span class="req">*</span></label>
            <input class="form-input" id="f-ghl-loc" placeholder="xxxxx">
          </div>
        </div>
      </div>
      <div id="send-webhook-fields" style="display:none">
        <div class="form-group">
          <label>URL de envio (webhook da plataforma de destino)</label>
          <input class="form-input" id="f-send-webhook-url" placeholder="https://api.plataforma.com/send">
        </div>
      </div>

      <div class="form-group" style="position:relative;z-index:1">
        <label>Calendar ID (opcional)</label>
        <input class="form-input" id="f-calendar" placeholder="ID do calendario no {{PRODUTO_DONO}}">
      </div>
      <div class="form-group" style="position:relative;z-index:1">
        <label>Fluxo de conversa customizado</label>
        <textarea class="form-input" id="f-spin" placeholder="Etapas do fluxo de conversa..."></textarea>
      </div>

      <!-- SALES WEBHOOK SECTION -->
      <div class="form-section-title">Webhook de Vendas</div>
      <div class="form-group" style="position:relative;z-index:1">
        <label>URL do Webhook de Vendas (gerado automaticamente)</label>
        <div style="display:flex;gap:8px;align-items:center">
          <input class="form-input" id="f-sales-webhook-url" readonly style="color:rgba(255,255,255,0.3);flex:1" placeholder="Salve o agente para gerar a URL">
          <button type="button" class="copy-btn" onclick="copyField('f-sales-webhook-url')">Copiar</button>
        </div>
        <p style="font-size:11px;color:rgba(255,255,255,0.25);margin-top:6px">Cole esta URL no postback da sua plataforma de vendas (Hotmart, Kiwify, Eduzz, Hubla)</p>
      </div>

      <!-- TRAINING FILES SECTION -->
      <div class="form-section-title">Treinamento</div>
      <div id="training-section" style="position:relative;z-index:1">
        <div class="upload-zone" id="upload-zone" onclick="document.getElementById('file-input').click()">
          <svg class="w-8 h-8 mx-auto text-white/20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"/></svg>
          <p>Arraste arquivos aqui ou clique para selecionar</p>
          <p style="font-size:11px;color:rgba(255,255,255,0.25);margin-top:4px">Aceita: .pdf, .md, .txt</p>
        </div>
        <input type="file" id="file-input" accept=".pdf,.md,.txt" multiple style="display:none" onchange="handleFileUpload(this.files)">
        <div class="file-list" id="file-list"></div>
        <p style="font-size:11px;color:rgba(255,255,255,0.25);margin-top:8px">Arquivos sao processados e incluidos instantaneamente na memoria do agente</p>
      </div>

      <div class="form-submit-row">
        <button type="button" class="btn btn-secondary" onclick="closeModal('modal-agent')">Cancelar</button>
        <button type="submit" class="btn btn-primary" id="btn-submit">Criar Agente</button>
      </div>
    </form>
  </div>
</div>

<!-- Channel Create Modal -->
<div class="modal-overlay" id="modal-channel">
  <div class="modal">
    <div class="modal-header">
      <h2>Novo Canal</h2>
      <button class="modal-close" onclick="closeModal('modal-channel')">&times;</button>
    </div>
    <form id="channel-form" onsubmit="submitChannelForm(event)">
      <div class="form-group">
        <label>Agente <span class="req">*</span></label>
        <select class="form-input" id="fc-agent" required></select>
      </div>
      <div class="form-group">
        <label>Tipo de Canal <span class="req">*</span></label>
        <select class="form-input" id="fc-type" required>
          <option value="instagram">Instagram DM</option>
          <option value="whatsapp">WhatsApp</option>
          <option value="telegram">Telegram</option>
          <option value="sms">SMS</option>
        </select>
      </div>
      <div class="form-group">
        <label>Webhook URL (gerado automaticamente)</label>
        <input class="form-input" id="fc-webhook" readonly placeholder="Sera gerado ao selecionar o agente" style="color:rgba(255,255,255,0.3)">
      </div>
      <div class="form-submit-row">
        <button type="button" class="btn btn-secondary" onclick="closeModal('modal-channel')">Cancelar</button>
        <button type="submit" class="btn btn-primary">Criar Canal</button>
      </div>
    </form>
  </div>
</div>

<script>
// ── Auth ──
function showLogin() {
  document.getElementById('login-overlay').classList.remove('hidden');
  document.getElementById('app-wrapper').style.display = 'none';
}
function hideLogin() {
  document.getElementById('login-overlay').classList.add('hidden');
  document.getElementById('app-wrapper').style.display = '';
}
async function doLogin(e) {
  e.preventDefault();
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  const errEl = document.getElementById('login-error');
  const btn = document.getElementById('login-btn');
  errEl.textContent = '';
  btn.textContent = 'Entrando...';
  btn.disabled = true;
  try {
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({email, password})
    });
    const data = await r.json();
    if (r.ok && data.token) {
      localStorage.setItem('agents_token', data.token);
      hideLogin();
      initApp();
    } else {
      errEl.textContent = data.error || 'Email ou senha incorretos';
    }
  } catch(err) {
    errEl.textContent = 'Erro de conexao. Tente novamente.';
  }
  btn.textContent = 'Entrar';
  btn.disabled = false;
}
async function verifyToken(t) {
  try {
    const r = await fetch('/api/check-auth', {headers:{'Authorization':'Bearer '+t}});
    if (r.ok) { hideLogin(); initApp(); }
    else { localStorage.removeItem('agents_token'); showLogin(); }
  } catch { showLogin(); }
}
function logout() {
  localStorage.removeItem('agents_token');
  showLogin();
}

// ── Theme Toggle ──
function toggleTheme() {
  const html = document.documentElement;
  const current = html.getAttribute('data-theme') || 'dark';
  const next = current === 'dark' ? 'light' : 'dark';
  html.setAttribute('data-theme', next);
  localStorage.setItem('agents_theme', next);
  updateThemeIcons();
}
function updateThemeIcons() {
  const isDark = (document.documentElement.getAttribute('data-theme') || 'dark') === 'dark';
  const icon = isDark ? '&#9790;' : '&#9728;';
  document.querySelectorAll('[id^="theme-icon"]').forEach(el => el.innerHTML = icon);
}

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen().catch(()=>{});
    document.getElementById('btnFullscreen').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 14h6v6m10-10h-6V4M4 10h6V4m10 10h-6v6"/></svg>';
  } else {
    document.exitFullscreen();
    document.getElementById('btnFullscreen').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>';
  }
}
document.addEventListener('fullscreenchange', () => {
  if (!document.fullscreenElement) {
    document.getElementById('btnFullscreen').innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 00-2 2v3m18 0V5a2 2 0 00-2-2h-3m0 18h3a2 2 0 002-2v-3M3 16v3a2 2 0 002 2h3"/></svg>';
  }
});

// ── Check auth on load ──
(function() {
  const token = localStorage.getItem('agents_token');
  if (token) verifyToken(token);
  else showLogin();
})();

const API = '/api';
let agents = [];
let channels = [];
let currentPage = 'dashboard';
let dashMode = 'general'; // 'general' or 'individual'
let dashAgentId = null;

// ── Period Filter (global) ──
let currentPeriod = '7d';
let customFrom = null;
let customTo = null;

function onPeriodChange(val) {
  if (val === 'custom') {
    document.getElementById('customDateRange').style.display = 'flex';
    const cdrv = document.getElementById('customDateRangeVendas');
    if (cdrv) cdrv.style.display = 'flex';
    return;
  }
  document.getElementById('customDateRange').style.display = 'none';
  const cdrv = document.getElementById('customDateRangeVendas');
  if (cdrv) cdrv.style.display = 'none';
  currentPeriod = val;
  // Sync both selects
  const pf = document.getElementById('periodFilter');
  const pfv = document.getElementById('periodFilterVendas');
  if (pf) pf.value = val;
  if (pfv) pfv.value = val;
  refreshCurrentPage();
}

function applyCustomDate() {
  const df = document.getElementById('dateFrom').value || document.getElementById('dateFromVendas').value;
  const dt = document.getElementById('dateTo').value || document.getElementById('dateToVendas').value;
  if (!df || !dt) return;
  customFrom = df;
  customTo = dt;
  // Sync date inputs
  document.getElementById('dateFrom').value = df;
  document.getElementById('dateTo').value = dt;
  const dfv = document.getElementById('dateFromVendas');
  const dtv = document.getElementById('dateToVendas');
  if (dfv) dfv.value = df;
  if (dtv) dtv.value = dt;
  currentPeriod = 'custom';
  const pf = document.getElementById('periodFilter');
  const pfv = document.getElementById('periodFilterVendas');
  if (pf) pf.value = 'custom';
  if (pfv) pfv.value = 'custom';
  refreshCurrentPage();
}


function getPeriodLabel() {
  const labels = {today:'Hoje',yesterday:'Ontem','7d':'7 Dias','30d':'30 Dias',all:'Total',custom:'Periodo'};
  return labels[currentPeriod] || '7 Dias';
}

function getPeriodParams() {
  if (currentPeriod === 'today') return '?period=today';
  if (currentPeriod === 'yesterday') return '?period=yesterday';
  if (currentPeriod === '7d') return '?period=7d';
  if (currentPeriod === '30d') return '?period=30d';
  if (currentPeriod === 'all') return '?period=all';
  if (currentPeriod === 'custom' && customFrom && customTo) return '?from=' + customFrom + '&to=' + customTo;
  return '?period=7d';
}

function refreshCurrentPage() {
  if (currentPage === 'dashboard') loadDashboard();
  else if (currentPage === 'vendas') loadSales();
}

function syncPeriodSelectors() {
  const pf = document.getElementById('periodFilter');
  const pfv = document.getElementById('periodFilterVendas');
  if (pf) pf.value = currentPeriod;
  if (pfv) pfv.value = currentPeriod;
  if (currentPeriod === 'custom') {
    document.getElementById('customDateRange').style.display = 'flex';
    const cdrv = document.getElementById('customDateRangeVendas');
    if (cdrv) cdrv.style.display = 'flex';
  }
}

// Set current date
const d = new Date();
const dateOpts = { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' };
const dateEl = document.getElementById('currentDate');
if (dateEl) dateEl.textContent = d.toLocaleDateString('pt-BR', dateOpts);

// ── API ──
async function api(path, method='GET', body=null) {
  const t = localStorage.getItem('agents_token');
  const opts = {method, headers: {'Content-Type':'application/json'}};
  if (t) opts.headers['Authorization'] = 'Bearer ' + t;
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(API + path, opts);
  if (r.status === 401) { showLogin(); return null; }
  return r.json();
}

// ── Navigation ──
function navigate(page) {
  currentPage = page;
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.querySelector(`.nav-item[data-page="${page}"]`).classList.add('active');
  document.getElementById('sidebar').classList.remove('open');
  if (page === 'dashboard') { dashMode = 'general'; dashAgentId = null; loadDashboard(); }
  else if (page === 'agents') loadAgents();
  else if (page === 'vendas') { syncPeriodSelectors(); loadSales(); }
  else if (page === 'prospeccao') loadProspeccao();
  else if (page === 'prospect-b2b') loadProspectB2B();
  else if (page === 'channels') loadChannels();
}

// ── Prospeccao Ativa (Ademir) ──
let prospectCurrentTab = 'icp';

async function loadProspeccao() {
  await loadProspectStats();
  await loadProspectTab(prospectCurrentTab);
}

async function loadProspectStats() {
  try {
    const s = await api('/prospect/stats');
    document.getElementById('ps-targets').textContent = s.targets_active || 0;
    document.getElementById('ps-total').textContent = s.leads.total || 0;
    const qel = document.getElementById('ps-qualified');
    if (qel) qel.textContent = s.leads.qualified || 0;
    const ael = document.getElementById('ps-approved');
    if (ael) ael.textContent = s.leads.approved_to_send || 0;
    document.getElementById('ps-dms').textContent = s.dms_total || 0;
    const wel = document.getElementById('ps-whatsapp');
    if (wel) wel.textContent = s.leads.with_whatsapp || 0;
    const rrel = document.getElementById('ps-replied-rate');
    if (rrel) rrel.textContent = (s.leads.replied || 0) + ' / ' + (s.reply_rate || 0) + '%';
    // Compat com layout antigo (caso o user tenha cache)
    const oldR = document.getElementById('ps-replied');
    if (oldR) oldR.textContent = s.leads.replied || 0;
    const oldRate = document.getElementById('ps-rate');
    if (oldRate) oldRate.textContent = (s.reply_rate || 0) + '%';
  } catch(e) { console.error(e); }
}

function switchProspectTab(tab) {
  prospectCurrentTab = tab;
  ['icp','leads','dms','runs'].forEach(t => {
    const btn = document.getElementById('ptab-' + t);
    const content = document.getElementById('ptab-content-' + t);
    if (t === tab) {
      btn.classList.add('border-b-2','border-fuchsia-500','text-fuchsia-400');
      btn.classList.remove('text-white/50');
      content.classList.remove('hidden');
    } else {
      btn.classList.remove('border-b-2','border-fuchsia-500','text-fuchsia-400');
      btn.classList.add('text-white/50');
      content.classList.add('hidden');
    }
  });
  loadProspectTab(tab);
}

async function loadProspectTab(tab) {
  if (tab === 'icp') return loadTargets();
  if (tab === 'leads') return loadLeads();
  if (tab === 'dms') return loadDms();
  if (tab === 'runs') return loadRuns();
}

async function loadTargets() {
  try {
    const rows = await api('/prospect/targets');
    const tbody = document.getElementById('targets-tbody');
    tbody.innerHTML = '';
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center opacity-50">Nenhum perfil-alvo. Adicione acima.</td></tr>'; return; }
    rows.forEach(r => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5';
      tr.innerHTML = `
        <td class="px-4 py-2 font-mono">@${r.username}</td>
        <td class="px-4 py-2">${labelSource(r.source)}</td>
        <td class="px-4 py-2 opacity-70">${r.created_at ? r.created_at.slice(0,16) : ''}</td>
        <td class="px-4 py-2">${r.active ? '<span class="text-emerald-400">ativo</span>' : '<span class="text-white/40">inativo</span>'}</td>
        <td class="px-4 py-2 text-right"><button class="text-rose-400 hover:text-rose-300 text-xs" onclick="deleteTarget(${r.id})">Remover</button></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

function labelSource(s) {
  return ({meu_seguidor: 'Meu seguidor', comentou_em_mim: 'Comentou em mim', comentou_em_alvo: 'Comentou em alvo'})[s] || s;
}

async function addTarget() {
  const username = document.getElementById('target-username').value.trim();
  const source = document.getElementById('target-source').value;
  if (!username) return alert('username obrigatorio');
  try {
    await api('/prospect/targets', 'POST', { username, source });
    document.getElementById('target-username').value = '';
    loadTargets(); loadProspectStats();
  } catch(e) { alert('Erro: ' + (e.message || e)); }
}

async function deleteTarget(id) {
  if (!confirm('Remover este perfil-alvo?')) return;
  await api('/prospect/targets/' + id, 'DELETE');
  loadTargets(); loadProspectStats();
}

// FASE 4: filtro + selecao bulk
let currentLeadFilter = '';
let selectedLeadIds = new Set();

function setLeadFilter(filter) {
  currentLeadFilter = filter;
  document.querySelectorAll('.lead-filter-btn').forEach(btn => {
    if (btn.dataset.filter === filter) {
      btn.classList.remove('text-white/50','border-white/10');
      btn.classList.add('bg-fuchsia-500/20','text-fuchsia-300','border-fuchsia-500/30');
    } else {
      btn.classList.remove('bg-fuchsia-500/20','text-fuchsia-300','border-fuchsia-500/30');
      btn.classList.add('text-white/50','border-white/10');
    }
  });
  selectedLeadIds.clear();
  updateBulkUI();
  loadLeads();
}

function updateBulkUI() {
  const c = selectedLeadIds.size;
  document.getElementById('bulk-selected-count').textContent = c + ' selecionados';
  document.getElementById('btn-bulk-approve').disabled = c === 0;
  const all = document.getElementById('leads-select-all');
  if (all) all.checked = false;
}

function toggleSelectAll(checked) {
  selectedLeadIds.clear();
  if (checked) {
    document.querySelectorAll('.lead-row-checkbox').forEach(cb => {
      cb.checked = true;
      selectedLeadIds.add(parseInt(cb.dataset.leadId, 10));
    });
  } else {
    document.querySelectorAll('.lead-row-checkbox').forEach(cb => { cb.checked = false; });
  }
  updateBulkUI();
}

function toggleLeadSelected(id, checked) {
  if (checked) selectedLeadIds.add(id);
  else selectedLeadIds.delete(id);
  updateBulkUI();
}

async function loadLeads() {
  try {
    const qs = currentLeadFilter ? '?status=' + encodeURIComponent(currentLeadFilter) + '&limit=500' : '?limit=500';
    const rows = await api('/prospect/leads' + qs);
    const tbody = document.getElementById('leads-tbody');
    tbody.innerHTML = '';
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center opacity-50">Nenhum lead nesse filtro. Rode o Ademir ou troque o filtro.</td></tr>'; return; }
    rows.forEach(r => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5 hover:bg-white/5';
      const bio = (r.bio || '').slice(0, 80);
      const wa = r.whatsapp_number ? `<span class="ml-2 px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300 text-[10px]" title="${escapeHtml(r.whatsapp_number)}">WhatsApp</span>` : '';
      const offerBadge = r.has_offer ? '<span class="ml-2 px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 text-[10px]">com oferta</span>' : '';
      const checked = selectedLeadIds.has(r.id) ? 'checked' : '';
      let actionsHtml = '';
      if (r.status === 'qualified' || r.status === 'discovered') {
        actionsHtml = `<button class="text-emerald-400 hover:text-emerald-300 text-xs px-2 py-1 rounded border border-emerald-500/30 bg-emerald-500/5" onclick="approveLead(event, ${r.id})">Aprovar pra envio</button>`;
      } else if (r.status === 'approved_to_send') {
        actionsHtml = `<button class="text-amber-400 hover:text-amber-300 text-xs px-2 py-1 rounded border border-amber-500/30 bg-amber-500/5" onclick="unapproveLead(event, ${r.id})">Cancelar aprovacao</button>`;
      } else {
        actionsHtml = `<span class="opacity-30 text-xs">--</span>`;
      }
      const checkboxCell = (r.status === 'qualified' || r.status === 'discovered')
        ? `<input type="checkbox" class="lead-row-checkbox" data-lead-id="${r.id}" ${checked} onchange="toggleLeadSelected(${r.id}, this.checked)" onclick="event.stopPropagation()" />`
        : '';
      tr.innerHTML = `
        <td class="px-3 py-2">${checkboxCell}</td>
        <td class="px-4 py-2 font-mono cursor-pointer" onclick="openLeadDetail(${r.id})">@${r.ig_username}</td>
        <td class="px-4 py-2 opacity-80 cursor-pointer" onclick="openLeadDetail(${r.id})">${escapeHtml(bio)}${offerBadge}${wa}</td>
        <td class="px-4 py-2 opacity-60 text-xs">${r.source_type || '-'}</td>
        <td class="px-4 py-2"><span class="px-2 py-0.5 rounded text-[11px] ${statusClass(r.status)}">${statusLabel(r.status)}</span></td>
        <td class="px-4 py-2 opacity-70 text-xs">${r.discovered_at ? r.discovered_at.slice(0,16) : ''}</td>
        <td class="px-4 py-2 text-right">${actionsHtml}</td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

function statusLabel(s) {
  return ({
    discovered: 'descoberto',
    qualified: 'aguardando',
    approved_to_send: 'aprovado',
    dm_sent: 'enviado',
    replied: 'respondeu',
    handed_off: 'handoff',
    disqualified: 'desqualif.',
  })[s] || s;
}

function statusClass(s) {
  return ({
    discovered: 'bg-slate-500/20 text-slate-300',
    qualified: 'bg-slate-400/20 text-slate-200',
    approved_to_send: 'bg-emerald-500/20 text-emerald-300',
    dm_sent: 'bg-blue-500/20 text-blue-300',
    replied: 'bg-amber-500/20 text-amber-300',
    handed_off: 'bg-cyan-500/20 text-cyan-300',
    disqualified: 'bg-rose-500/20 text-rose-300',
  })[s] || 'bg-white/10';
}

async function approveLead(ev, id) {
  if (ev) ev.stopPropagation();
  try {
    await api('/prospect/leads/' + id + '/approve', 'POST');
    selectedLeadIds.delete(id);
    loadLeads(); loadProspectStats();
  } catch(e) { alert('Erro: ' + (e.message || e)); }
}

async function unapproveLead(ev, id) {
  if (ev) ev.stopPropagation();
  try {
    await api('/prospect/leads/' + id + '/unapprove', 'POST');
    loadLeads(); loadProspectStats();
  } catch(e) { alert('Erro: ' + (e.message || e)); }
}

async function bulkApproveSelected() {
  if (selectedLeadIds.size === 0) return;
  if (!confirm('Aprovar ' + selectedLeadIds.size + ' leads pra envio?')) return;
  try {
    const r = await api('/prospect/leads/bulk-approve', 'POST', { ids: Array.from(selectedLeadIds) });
    alert(`OK: ${r.approved}/${r.requested} leads aprovados`);
    selectedLeadIds.clear();
    loadLeads(); loadProspectStats();
  } catch(e) { alert('Erro: ' + (e.message || e)); }
}

async function runSendNow() {
  if (!confirm('Disparar envio dos leads aprovados? (respeita janela 9-18h, limite diario, jitter)')) return;
  try {
    const r = await api('/prospect/run-send-now', 'POST');
    alert('Send loop disparado. ' + (r.response && r.response.dry_run ? 'DRY_RUN: nao envia, apenas loga.' : 'Envio em andamento.'));
    setTimeout(() => loadProspectTab('runs'), 2000);
  } catch(e) { alert('Erro: ' + (e.message || 'daemon nao acessivel')); }
}

async function enrichWhatsapp() {
  if (!confirm('Reprocessar WhatsApp em todos os leads (sem chamadas HikerAPI)?')) return;
  try {
    const r = await api('/prospect/enrich-whatsapp', 'POST');
    alert(`OK. Total: ${r.total}, Enriquecidos: ${r.enriched}, Ja tinham: ${r.already_has}, Sem numero: ${r.skipped}`);
    loadLeads(); loadProspectStats();
  } catch(e) { alert('Erro: ' + (e.message || 'daemon nao acessivel')); }
}

function escapeHtml(s) {
  return (s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function openLeadDetail(id) {
  try {
    const lead = await api('/prospect/leads/' + id);
    const b = lead.briefing || {};
    const dms = lead.dms || [];
    document.getElementById('lead-detail-title').textContent = '@' + lead.ig_username + ' - ' + (lead.display_name || '');
    const dmsHtml = dms.map(d => `<div class="border border-white/10 rounded p-3 mb-2"><div class="text-xs opacity-50 mb-1">${d.sent_at ? d.sent_at.slice(0,16) : ''} ${d.delivered ? '(enviada)' : '(pendente)'}</div><div class="text-sm whitespace-pre-wrap">${escapeHtml(d.message)}</div></div>`).join('') || '<div class="opacity-50 text-sm">Nenhuma DM gerada ainda.</div>';
    const wa = b.whatsapp_number || '';
    const waBlock = wa
      ? `<div class="bg-cyan-500/10 border border-cyan-500/30 rounded p-3 flex items-center gap-3">
           <div class="text-2xl">📱</div>
           <div class="flex-1">
             <div class="text-xs opacity-50">WhatsApp encontrado</div>
             <div class="font-mono text-cyan-300">${escapeHtml(wa)}</div>
           </div>
           <button class="btn btn-secondary text-xs" onclick="copyToClipboard('${wa}')">Copiar</button>
         </div>`
      : `<div class="bg-white/5 border border-white/10 rounded p-3 text-xs opacity-60">Sem numero WhatsApp identificado no briefing.</div>`;
    let approveBtn = '';
    if (lead.status === 'qualified' || lead.status === 'discovered') {
      approveBtn = `<button class="btn btn-primary w-full py-3 text-sm" onclick="approveLeadAndClose(${lead.id})">Aprovar pra envio agora</button>`;
    } else if (lead.status === 'approved_to_send') {
      approveBtn = `<button class="btn btn-secondary w-full py-3 text-sm" onclick="unapproveLeadAndClose(${lead.id})">Cancelar aprovacao</button>`;
    } else {
      approveBtn = `<div class="text-center text-xs opacity-50 py-3">Status: ${statusLabel(lead.status)}</div>`;
    }
    document.getElementById('lead-detail-body').innerHTML = `
      <div class="space-y-4">
        <div class="grid grid-cols-2 gap-3 text-sm">
          <div><span class="opacity-50">Seguidores:</span> ${lead.followers_count || '-'}</div>
          <div><span class="opacity-50">Seguindo:</span> ${lead.following_count || '-'}</div>
          <div><span class="opacity-50">Posts:</span> ${lead.posts_count || '-'}</div>
          <div><span class="opacity-50">Verificado:</span> ${lead.is_verified ? 'sim' : 'nao'}</div>
          <div><span class="opacity-50">Tem oferta:</span> ${lead.has_offer ? 'sim' : 'nao'}</div>
          <div><span class="opacity-50">Nicho:</span> ${escapeHtml(lead.niche || '-')}</div>
          <div><span class="opacity-50">Status:</span> <span class="px-2 py-0.5 rounded text-[11px] ${statusClass(lead.status)}">${statusLabel(lead.status)}</span></div>
        </div>
        ${waBlock}
        <div><div class="text-xs opacity-50 mb-1">Bio</div><div class="text-sm whitespace-pre-wrap">${escapeHtml(lead.bio || '-')}</div></div>
        ${lead.external_link ? `<div><div class="text-xs opacity-50 mb-1">Link</div><a href="${lead.external_link}" target="_blank" class="text-cyan-400 text-sm">${escapeHtml(lead.external_link)}</a></div>` : ''}
        ${b.posts ? `<div><div class="text-xs opacity-50 mb-1">Posts recentes</div><div class="space-y-2">${(b.posts || []).map(p => `<div class="border border-white/10 rounded p-2 text-xs"><div class="opacity-50">${p.taken_at || p.date || ''}</div><div>${escapeHtml((p.caption || '').slice(0, 200))}</div></div>`).join('')}</div></div>` : ''}
        ${b.oferta_principal ? `<div><div class="text-xs opacity-50 mb-1">Oferta principal</div><div class="text-sm">${escapeHtml(b.oferta_principal)}</div></div>` : ''}
        ${b.gancho_dm ? `<div><div class="text-xs opacity-50 mb-1">Gancho DM sugerido</div><div class="text-sm bg-fuchsia-500/10 border border-fuchsia-500/30 rounded p-2">${escapeHtml(b.gancho_dm)}</div></div>` : ''}
        <div><div class="text-xs opacity-50 mb-1">DMs (${dms.length})</div>${dmsHtml}</div>
        <div class="pt-2 border-t border-white/10">
          ${approveBtn}
        </div>
        <div class="flex gap-2">
          <button class="btn btn-secondary text-xs flex-1" onclick="updateLeadStatus(${lead.id}, 'disqualified')">Desqualificar</button>
        </div>
      </div>`;
    document.getElementById('modal-lead-detail').classList.add('open');
  } catch(e) { alert('Erro: ' + e.message); }
}

async function approveLeadAndClose(id) {
  await approveLead(null, id);
  closeModal('modal-lead-detail');
}

async function unapproveLeadAndClose(id) {
  await unapproveLead(null, id);
  closeModal('modal-lead-detail');
}

function copyToClipboard(text) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(() => {
      // No-op feedback ja basta
    });
  } else {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch(e) {}
    document.body.removeChild(ta);
  }
}

async function updateLeadStatus(id, status) {
  await api('/prospect/leads/' + id, 'PATCH', { status });
  closeModal('modal-lead-detail');
  loadLeads(); loadProspectStats();
}

async function loadDms() {
  try {
    const rows = await api('/prospect/dms?limit=200');
    const tbody = document.getElementById('dms-tbody');
    tbody.innerHTML = '';
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="4" class="px-4 py-6 text-center opacity-50">Nenhuma DM enviada ainda.</td></tr>'; return; }
    rows.forEach(r => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5';
      tr.innerHTML = `
        <td class="px-4 py-2 font-mono">@${r.ig_username}</td>
        <td class="px-4 py-2 text-xs">${escapeHtml((r.message || '').slice(0, 200))}</td>
        <td class="px-4 py-2 opacity-70 text-xs">${r.sent_at ? r.sent_at.slice(0,16) : ''}</td>
        <td class="px-4 py-2">${r.delivered ? '<span class="text-emerald-400">entregue</span>' : '<span class="text-amber-400">pendente</span>'}</td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

async function loadRuns() {
  try {
    const rows = await api('/prospect/runs');
    const tbody = document.getElementById('runs-tbody');
    tbody.innerHTML = '';
    if (!rows.length) { tbody.innerHTML = '<tr><td colspan="7" class="px-4 py-6 text-center opacity-50">Nenhuma execucao ainda.</td></tr>'; return; }
    rows.forEach(r => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5';
      tr.innerHTML = `
        <td class="px-4 py-2 text-xs opacity-80">${r.started_at ? r.started_at.slice(0,16) : ''}</td>
        <td class="px-4 py-2 text-xs opacity-80">${r.finished_at ? r.finished_at.slice(0,16) : '-'}</td>
        <td class="px-4 py-2 text-xs">${r.triggered_by || '-'}</td>
        <td class="px-4 py-2">${r.leads_discovered || 0}</td>
        <td class="px-4 py-2">${r.leads_qualified || 0}</td>
        <td class="px-4 py-2">${r.dms_sent || 0}</td>
        <td class="px-4 py-2"><span class="px-2 py-0.5 rounded text-[11px] ${r.status === 'done' ? 'bg-emerald-500/20 text-emerald-300' : r.status === 'failed' ? 'bg-rose-500/20 text-rose-300' : 'bg-amber-500/20 text-amber-300'}">${r.status}</span></td>`;
      tbody.appendChild(tr);
    });
  } catch(e) { console.error(e); }
}

async function ademirRunNow() {
  const btn = document.getElementById('btn-run-ademir');
  const original = btn.textContent;
  btn.textContent = 'Disparando...';
  btn.disabled = true;
  try {
    const r = await api('/prospect/run-now', 'POST');
    alert('Ademir disparado. Acompanhe na aba Runs.');
    setTimeout(() => loadProspectTab('runs'), 2000);
  } catch(e) {
    alert('Erro: ' + (e.message || 'daemon nao acessivel'));
  } finally {
    btn.textContent = original;
    btn.disabled = false;
  }
}

// ── Dashboard ──
// Populate agent selector dropdown
async function populateAgentSelect() {
  const agents = await api('/agents');
  const sel = document.getElementById('dashAgentSelect');
  // Keep "Todos" option, remove old agent options
  while (sel.options.length > 1) sel.remove(1);
  agents.forEach(a => {
    const opt = document.createElement('option');
    opt.value = a.id;
    opt.textContent = a.name + (a.company ? ' (' + a.company + ')' : '');
    sel.appendChild(opt);
  });
}
function onDashAgentSelect(val) {
  if (val === 'all') {
    dashMode = 'general'; dashAgentId = null;
  } else {
    dashMode = 'individual'; dashAgentId = parseInt(val);
  }
  loadDashboard();
}

async function loadDashboard() {
  await populateAgentSelect();
  // Sync select with current state
  const sel = document.getElementById('dashAgentSelect');
  if (sel) sel.value = dashAgentId ? String(dashAgentId) : 'all';

  // Always reset both containers first
  const genEl = document.getElementById('dash-general');
  const indEl = document.getElementById('dash-individual');
  if (genEl) genEl.style.display = 'none';
  if (indEl) { indEl.style.display = 'none'; indEl.innerHTML = ''; }

  if (dashMode === 'individual' && dashAgentId) {
    loadIndividualDashboard(dashAgentId);
    return;
  }
  if (genEl) genEl.style.display = 'block';
  document.getElementById('dash-header-title').textContent = 'Agents Dashboard';

  const [stats, recent] = await Promise.all([api('/dashboard/stats' + getPeriodParams()), api('/dashboard/recent' + getPeriodParams())]);
  const active = stats.total_active || 0;
  const total = stats.total_agents || 0;
  const msgIn = stats.total_messages_in || 0;
  const msgOut = stats.total_messages_out || 0;
  const contacts = stats.total_contacts || 0;
  const rate = stats.response_rate || 0;

  // Profile card
  document.getElementById('ds-total').textContent = total;
  document.getElementById('ds-active').textContent = active;
  document.getElementById('ds-contacts').textContent = contacts.toLocaleString();

  // Metric cards
  document.getElementById('ds-active2').textContent = active;
  document.getElementById('ds-in').textContent = msgIn.toLocaleString();
  document.getElementById('ds-out').textContent = msgOut.toLocaleString();
  document.getElementById('ds-contacts2').textContent = contacts.toLocaleString();
  document.getElementById('ds-rate').textContent = rate + '%';
  document.getElementById('ds-total2').textContent = total;

  // Funnel
  const maxVal = Math.max(total, msgIn, msgOut, contacts, 1);
  document.getElementById('funnel-total-val').textContent = total;
  document.getElementById('funnel-total-bar').style.width = '100%';
  document.getElementById('funnel-active-val').textContent = active;
  document.getElementById('funnel-active-bar').style.width = (total > 0 ? (active/total*100) : 0) + '%';
  document.getElementById('funnel-in-val').textContent = msgIn;
  document.getElementById('funnel-in-bar').style.width = (maxVal > 0 ? Math.min(msgIn/maxVal*100, 100) : 0) + '%';
  document.getElementById('funnel-out-val').textContent = msgOut;
  document.getElementById('funnel-out-bar').style.width = (maxVal > 0 ? Math.min(msgOut/maxVal*100, 100) : 0) + '%';
  document.getElementById('funnel-contacts-val').textContent = contacts;
  document.getElementById('funnel-contacts-bar').style.width = (maxVal > 0 ? Math.min(contacts/maxVal*100, 100) : 0) + '%';

  // Sales cards
  const salesToday = stats.total_sales || stats.sales_today || 0;
  const amountToday = stats.total_amount || stats.amount_today || 0;
  document.getElementById('ds-sales-today').textContent = salesToday;
  document.getElementById('ds-amount-today').textContent = 'R$ ' + amountToday.toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  const dsLabel = document.getElementById('ds-sales-label');
  if (dsLabel) dsLabel.textContent = 'Vendas ' + getPeriodLabel();

  // Donut chart (by agent)
  updateDonut(recent);

  // Heatmap (by_hour)
  updateHeatmap(stats.by_hour || {});

  // Weekly bars
  updateWeeklyBars(stats.weekly || []);

  // Recent table
  const tbody = document.getElementById('dash-recent');
  if (!recent.length) {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-12 text-white/20 text-sm"><div class="flex flex-col items-center gap-3"><svg class="w-10 h-10 text-white/10" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4"/></svg><span>Nenhuma atividade ainda</span></div></td></tr>';
  } else {
    tbody.innerHTML = recent.map(r => `<tr class="border-b border-white/[0.04]">
      <td class="py-3 px-4 font-medium text-white/80">${esc(r.agent_name)}</td>
      <td class="py-3 px-4 text-white/50 text-xs">${esc(r.company)}</td>
      <td class="py-3 px-4"><span class="badge badge-${r.status}">${r.status}</span></td>
      <td class="py-3 px-4 text-white/60">${r.messages_in}</td>
      <td class="py-3 px-4 text-white/60">${r.messages_out}</td>
      <td class="py-3 px-4 text-white/60">${r.contacts}</td>
      <td class="py-3 px-4"><button class="btn btn-secondary btn-sm" onclick="openAgentDash(${r.id})">Ver Dashboard</button></td>
    </tr>`).join('');
  }
}

function updateDonut(recent) {
  const colors = ['rgba(59,130,246,0.4)','rgba(139,92,246,0.4)','rgba(236,72,153,0.4)','rgba(6,182,212,0.4)','rgba(245,158,11,0.4)'];
  const dotColors = ['bg-blue-500/60','bg-purple-500/60','bg-pink-500/60','bg-cyan-500/60','bg-orange-500/60'];
  const circumference = 2 * Math.PI * 60;

  // Build per-agent contacts
  const agentData = [];
  let totalContacts = 0;
  recent.forEach(r => {
    agentData.push({name: r.agent_name || ('Agent '+r.id), contacts: r.contacts || 0});
    totalContacts += (r.contacts || 0);
  });
  // Fallback if no contacts: use message counts
  if (totalContacts === 0) {
    agentData.forEach(a => { a.contacts = 1; totalContacts++; });
  }

  document.getElementById('donut-total').textContent = totalContacts;

  // Update SVG segments (up to 5)
  let offset = 0;
  for (let i = 0; i < 5; i++) {
    const seg = document.getElementById('donut-seg-' + i);
    if (!seg) continue;
    if (i < agentData.length && totalContacts > 0) {
      const len = (agentData[i].contacts / totalContacts) * circumference;
      seg.setAttribute('stroke', colors[i % colors.length]);
      seg.setAttribute('stroke-dasharray', len + ' ' + (circumference - len));
      seg.setAttribute('stroke-dashoffset', -offset);
      offset += len;
    } else {
      seg.setAttribute('stroke-dasharray', '0 376.8');
    }
  }

  // Legend
  const legend = document.getElementById('donut-legend');
  legend.innerHTML = agentData.slice(0, 5).map((a, i) => {
    const pct = totalContacts > 0 ? Math.round(a.contacts / totalContacts * 100) : 0;
    return '<div class="flex items-center justify-between text-xs"><div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full ' + dotColors[i % dotColors.length] + '"></div><span class="text-white/40">' + esc(a.name) + '</span></div><span class="text-white/30">' + pct + '%</span></div>';
  }).join('');
}

function updateHeatmap(byHour) {
  const grid = document.getElementById('heatmap-grid');
  if (!grid) return;
  const hours = [8,9,10,11,12,13,14,15,16,17,18,19,20,21];
  const maxVal = Math.max(...Object.values(byHour).map(Number), 1);
  let html = '';
  hours.forEach(h => {
    html += '<div class="text-[10px] text-white/20 text-right pr-1">' + h + 'h</div>';
    for (let d = 0; d < 7; d++) {
      const key = String(h);
      const val = Number(byHour[key] || 0);
      const intensity = val > 0 ? Math.max(0.08, Math.min(0.7, val / maxVal * 0.7)) : 0.02;
      const bg = val > 0 ? 'rgba(6,182,212,' + intensity + ')' : 'rgba(255,255,255,0.02)';
      html += '<div class="heatmap-cell h-6 rounded" style="background:' + bg + '" title="' + h + 'h: ' + val + '"></div>';
    }
  });
  grid.innerHTML = html;
  grid.style.gridTemplateColumns = 'auto repeat(7, 1fr)';
}

function updateWeeklyBars(weekly) {
  if (!weekly || !weekly.length) return;
  const maxVal = Math.max(...weekly.map(w => Math.max(w.msgs_in || 0, w.msgs_out || 0)), 1);
  const maxH = 140; // max px height
  for (let i = 0; i < 4 && i < weekly.length; i++) {
    const w = weekly[i];
    const inH = Math.max(4, (w.msgs_in / maxVal) * maxH);
    const outH = Math.max(4, (w.msgs_out / maxVal) * maxH);
    const inBar = document.getElementById('wb-in-' + i);
    const outBar = document.getElementById('wb-out-' + i);
    const label = document.getElementById('wb-label-' + i);
    const val = document.getElementById('wb-val-' + i);
    if (inBar) { inBar.style.height = inH + 'px'; inBar.style.background = 'rgba(6,182,212,0.3)'; }
    if (outBar) { outBar.style.height = outH + 'px'; outBar.style.background = 'rgba(239,68,68,0.3)'; }
    if (label) label.textContent = 'Sem ' + (i + 1);
    if (val) val.textContent = (w.msgs_in || 0) + ' / ' + (w.msgs_out || 0);
  }
}

// ── Individual Agent Dashboard ──
function openAgentDash(agentId) {
  dashMode = 'individual';
  dashAgentId = agentId;
  loadIndividualDashboard(agentId);
}

async function loadIndividualDashboard(agentId) {
  document.getElementById('dash-general').style.display = 'none';
  const container = document.getElementById('dash-individual');
  container.style.display = 'block';
  // Only show loading on first load, not on refresh
  if (!container.innerHTML || container.innerHTML.trim() === '') {
    container.innerHTML = '<div class="text-center py-20 text-white/30">Carregando...</div>';
  }

  const pp = getPeriodParams();
  const [data, salesStats, salesProducts] = await Promise.all([
    api(`/agents/${agentId}/dashboard` + pp),
    api(`/agents/${agentId}/sales/stats` + pp),
    api(`/agents/${agentId}/sales` + pp)
  ]);
  const a = data.agent;
  const s = data.stats;
  document.getElementById('dash-header-title').textContent = `Dashboard: ${a.name}`;

  // Conversations - only last 10
  let conversationsHtml = '';
  const convs = (data.recent_conversations || []).slice(0, 10);
  if (convs.length) {
    conversationsHtml = convs.map(c => `<tr class="border-b border-white/[0.04]">
      <td class="py-1.5 px-3 text-white/60 text-xs">${esc(c.contact_name || c.contact_id)}</td>
      <td class="py-1.5 px-3"><span class="badge badge-${c.direction === 'inbound' ? 'active' : 'inactive'}" style="font-size:9px;padding:1px 6px">${c.direction === 'inbound' ? 'IN' : 'OUT'}</span></td>
      <td class="py-1.5 px-3 text-white/50 text-xs" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc((c.message||'').substring(0,60))}</td>
      <td class="py-1.5 px-3 text-white/30 text-[10px]">${c.created_at ? c.created_at.substring(5,16) : ''}</td>
    </tr>`).join('');
  } else {
    conversationsHtml = '<tr><td colspan="4" class="text-center py-4 text-white/20 text-xs">Nenhuma conversa</td></tr>';
  }

  // Products sold - aggregate by product name
  let productsHtml = '';
  const productMap = {};
  (salesProducts || []).forEach(sale => {
    const name = sale.product || 'Sem nome';
    if (!productMap[name]) productMap[name] = {count: 0, total: 0};
    productMap[name].count++;
    productMap[name].total += parseFloat(sale.amount || 0);
  });
  const productEntries = Object.entries(productMap).sort((a,b) => b[1].count - a[1].count);
  if (productEntries.length) {
    productsHtml = productEntries.map(([name, d]) => `<tr class="border-b border-white/[0.04]">
      <td class="py-2 px-3 text-white/70 text-xs">${esc(name.substring(0,40))}</td>
      <td class="py-2 px-3 text-center"><span class="badge badge-active" style="font-size:10px">${d.count}</span></td>
      <td class="py-2 px-3 text-right text-xs font-medium stat-number">R$ ${d.total.toLocaleString('pt-BR',{minimumFractionDigits:2})}</td>
    </tr>`).join('');
  } else {
    productsHtml = '<tr><td colspan="3" class="text-center py-4 text-white/20 text-xs">Nenhuma venda registrada</td></tr>';
  }

  // Contacts table - show @ leads
  let contactsHtml = '';
  if (data.contact_profiles && data.contact_profiles.length) {
    contactsHtml = data.contact_profiles.slice(0, 20).map(cp => {
      const name = cp.contact_name || cp.contact_id || '?';
      const handle = '@' + name.toLowerCase().replace(/[^a-z0-9._]/g, '').substring(0, 20);
      return `<tr class="border-b border-white/[0.04]">
        <td class="py-1.5 px-3 text-cyan-400/80 text-xs font-medium">${esc(handle)}</td>
        <td class="py-1.5 px-3 text-white/60 text-xs">${esc(name)}</td>
        <td class="py-1.5 px-3 text-white/40 text-xs">${cp.messages_count || 0} msgs</td>
        <td class="py-1.5 px-3 text-white/30 text-[10px]">${cp.last_contact_at ? cp.last_contact_at.substring(5,16) : ''}</td>
      </tr>`;
    }).join('');
  } else {
    contactsHtml = '<tr><td colspan="4" class="text-center py-4 text-white/20 text-xs">Nenhum contato</td></tr>';
  }

  container.innerHTML = `
    <div style="margin-bottom:20px">
      <button class="btn btn-secondary btn-sm" onclick="backToGeneralDash()">&#8592; Voltar ao Dashboard Geral</button>
    </div>

    <!-- Profile -->
    <div class="glass-strong rounded-3xl p-8 gradient-border card-hover" style="margin-bottom:20px">
      <div class="flex items-start gap-4">
        <div class="w-20 h-20 rounded-full bg-gradient-to-br from-blue-500 via-purple-500 to-cyan-400 p-[3px] flex-shrink-0">
          <div class="w-full h-full rounded-full bg-black flex items-center justify-center overflow-hidden">
            ${a.avatar_url ? '<img src="' + a.avatar_url + '" style="width:100%;height:100%;object-fit:cover;border-radius:50%">' : '<span style="font-size:28px;font-weight:800;color:rgba(255,255,255,0.6)">' + esc(a.name.charAt(0)) + '</span>'}
          </div>
        </div>
        <div class="min-w-0">
          <h2 class="text-lg font-bold truncate">${esc(a.name)} <span class="badge badge-${a.status}" style="vertical-align:middle">${a.status}</span></h2>
          <p class="text-sm text-white/80 font-medium">${esc(a.company)} | Porta ${a.port}</p>
          <p class="text-xs text-white/40" style="margin-top:2px">Modelo: Claude Opus 4.6</p>
        </div>
      </div>
    </div>

    <!-- 5 Metric Cards -->
    <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4" style="margin-bottom:20px">
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Conversas</div>
        <div class="text-2xl font-bold stat-number">${s.contacts}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Msgs por Conversa</div>
        <div class="text-2xl font-bold stat-number">${s.avg_messages || (s.contacts > 0 ? ((s.messages_in + s.messages_out) / s.contacts).toFixed(1) : '0')}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Conversas Aquecidas</div>
        <div class="text-2xl font-bold stat-number">${s.warm_conversations || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Links Enviados</div>
        <div class="text-2xl font-bold stat-number">${s.links_sent || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Agendamentos</div>
        <div class="text-2xl font-bold stat-number">${s.appointments || 0}</div>
      </div>
    </div>

    <!-- Sales Cards -->
    <div class="grid grid-cols-2 sm:grid-cols-4 gap-4" style="margin-bottom:20px">
      <div class="glass rounded-3xl p-7 gradient-border card-hover glow-cyan">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Vendas ${getPeriodLabel()}</div>
        <div class="text-2xl font-bold stat-number">${salesStats.total_sales || salesStats.sales_today || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover glow-blue">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Faturamento ${getPeriodLabel()}</div>
        <div class="text-2xl font-bold stat-number">R$ ${(salesStats.total_amount || salesStats.amount_today || 0).toLocaleString('pt-BR', {minimumFractionDigits:2})}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Vendas 7d</div>
        <div class="text-2xl font-bold stat-number">${salesStats.sales_7d || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Total Vendas</div>
        <div class="text-2xl font-bold stat-number">R$ ${(salesStats.total_amount || 0).toLocaleString('pt-BR', {minimumFractionDigits:2})}</div>
      </div>
    </div>

    <!-- Funnel -->
    <div class="glass-strong rounded-3xl p-8 gradient-border" style="margin-bottom:20px">
      <div class="flex items-center gap-3 mb-6">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"/></svg>
        </div>
        <h3 class="text-base font-semibold">Funil do Agente</h3>
      </div>
      <div class="space-y-3">
        <div class="flex items-center gap-4">
          <span class="text-xs text-white/40 w-28 text-right">Msgs Recebidas</span>
          <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
            <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-cyan-500/30 to-cyan-500/10" style="width:100%"></div>
            <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50">${s.messages_in}</span>
          </div>
        </div>
        <div class="flex items-center gap-4">
          <span class="text-xs text-white/40 w-28 text-right">Msgs Enviadas</span>
          <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
            <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-purple-500/30 to-purple-500/10" style="width:${s.messages_in > 0 ? Math.min(s.messages_out/s.messages_in*100,100) : 0}%"></div>
            <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50">${s.messages_out}</span>
          </div>
        </div>
        <div class="flex items-center gap-4">
          <span class="text-xs text-white/40 w-28 text-right">Contatos</span>
          <div class="flex-1 h-9 bg-white/[0.02] rounded-lg overflow-hidden relative">
            <div class="funnel-bar h-full rounded-lg bg-gradient-to-r from-pink-500/30 to-pink-500/10" style="width:${s.messages_in > 0 ? Math.min(s.contacts/s.messages_in*100,100) : 0}%"></div>
            <span class="absolute inset-0 flex items-center justify-center text-xs font-medium text-white/50">${s.contacts}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 3 Tables side by side -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6" style="margin-bottom:20px">
      <!-- Produtos Vendidos -->
      <div class="glass-strong rounded-3xl p-6 gradient-border">
        <div class="flex items-center gap-2 mb-4">
          <div class="w-6 h-6 rounded-lg bg-gradient-to-br from-green-500/20 to-cyan-500/20 flex items-center justify-center">
            <svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 11V7a4 4 0 00-8 0v4M5 9h14l1 12H4L5 9z"/></svg>
          </div>
          <h3 class="text-sm font-semibold">Produtos Vendidos</h3>
        </div>
        <div class="overflow-x-auto" style="max-height:320px;overflow-y:auto">
          <table class="w-full text-sm">
            <thead><tr class="border-b border-white/5">
              <th class="text-left py-1 px-3 text-[10px] text-white/30 uppercase">Produto</th>
              <th class="text-center py-1 px-3 text-[10px] text-white/30 uppercase">Qtd</th>
              <th class="text-right py-1 px-3 text-[10px] text-white/30 uppercase">Total</th>
            </tr></thead>
            <tbody>${productsHtml}</tbody>
          </table>
        </div>
      </div>

      <!-- Agendamentos -->
      <div class="glass-strong rounded-3xl p-6 gradient-border">
        <div class="flex items-center gap-2 mb-4">
          <div class="w-6 h-6 rounded-lg bg-gradient-to-br from-purple-500/20 to-pink-500/20 flex items-center justify-center">
            <svg class="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/></svg>
          </div>
          <h3 class="text-sm font-semibold">Agendamentos</h3>
          <span class="text-[10px] text-white/30">({{PRODUTO_DONO}})</span>
        </div>
        <div class="text-center py-8 text-white/20 text-xs">Conecte o calendario do {{PRODUTO_DONO}} para ver agendamentos em tempo real</div>
      </div>
    </div>

    <!-- Contacts Table (compact) -->
    <div class="glass-strong rounded-3xl p-8 gradient-border" style="margin-bottom:20px">
      <div class="flex items-center gap-3 mb-6">
        <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500/20 to-cyan-500/20 flex items-center justify-center">
          <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"/></svg>
        </div>
        <h3 class="text-base font-semibold">Contatos</h3>
      </div>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-white/5">
              <th class="text-left py-2 px-4 text-[11px] text-white/30 font-medium uppercase tracking-wider">@</th>
              <th class="text-left py-2 px-4 text-[11px] text-white/30 font-medium uppercase tracking-wider">Nome</th>
              <th class="text-left py-2 px-4 text-[11px] text-white/30 font-medium uppercase tracking-wider">Msgs</th>
              <th class="text-left py-2 px-4 text-[11px] text-white/30 font-medium uppercase tracking-wider">Ultimo Contato</th>
            </tr>
          </thead>
          <tbody>${contactsHtml}</tbody>
        </table>
      </div>
    </div>

    <!-- 3 Bottom Cards -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <!-- Donut: Distribuicao -->
      <div class="glass-strong rounded-3xl p-8 gradient-border">
        <div class="flex items-center gap-3 mb-6">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500/20 to-purple-500/20 flex items-center justify-center">
            <svg class="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 3.055A9.001 9.001 0 1020.945 13H11V3.055z"/></svg>
          </div>
          <h3 class="text-sm font-semibold">Distribuicao</h3>
        </div>
        <div class="flex justify-center mb-5">
          <div class="relative">
            <svg width="160" height="160" viewBox="0 0 160 160">
              <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(255,255,255,0.03)" stroke-width="20"/>
              <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(34,211,238,0.4)" stroke-width="20" stroke-dasharray="${s.messages_in > 0 ? (s.messages_in/(s.messages_in+s.messages_out)*376.8).toFixed(1) : 0} 376.8" stroke-dashoffset="0" transform="rotate(-90 80 80)"/>
              <circle cx="80" cy="80" r="60" fill="none" stroke="rgba(220,38,38,0.4)" stroke-width="20" stroke-dasharray="${s.messages_out > 0 ? (s.messages_out/(s.messages_in+s.messages_out)*376.8).toFixed(1) : 0} 376.8" stroke-dashoffset="-${s.messages_in > 0 ? (s.messages_in/(s.messages_in+s.messages_out)*376.8).toFixed(1) : 0}" transform="rotate(-90 80 80)"/>
            </svg>
            <div class="absolute inset-0 flex flex-col items-center justify-center">
              <span class="text-xl font-bold stat-number">${s.contacts}</span>
              <span class="text-[10px] text-white/30">contatos</span>
            </div>
          </div>
        </div>
        <div class="space-y-2">
          <div class="flex items-center justify-between text-xs"><div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full" style="background:rgba(34,211,238,0.5)"></div><span class="text-white/40">Recebidas</span></div><span class="text-white/30">${s.messages_in}</span></div>
          <div class="flex items-center justify-between text-xs"><div class="flex items-center gap-2"><div class="w-2.5 h-2.5 rounded-full" style="background:rgba(220,38,38,0.5)"></div><span class="text-white/40">Enviadas</span></div><span class="text-white/30">${s.messages_out}</span></div>
        </div>
      </div>

      <!-- Heatmap: Melhores Horarios -->
      <div class="glass-strong rounded-3xl p-8 gradient-border">
        <div class="flex items-center gap-3 mb-6">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-green-500/20 to-cyan-500/20 flex items-center justify-center">
            <svg class="w-4 h-4 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
          </div>
          <h3 class="text-sm font-semibold">Melhores Horarios</h3>
        </div>
        <div id="ind-heatmap" class="text-center text-white/20 text-xs py-8">Dados em breve</div>
      </div>

      <!-- Bars: Historico Semanal -->
      <div class="glass-strong rounded-3xl p-8 gradient-border">
        <div class="flex items-center gap-3 mb-6">
          <div class="w-8 h-8 rounded-lg bg-gradient-to-br from-orange-500/20 to-pink-500/20 flex items-center justify-center">
            <svg class="w-4 h-4 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"/></svg>
          </div>
          <h3 class="text-sm font-semibold">Historico Semanal</h3>
        </div>
        <div class="flex items-center gap-2 mb-4">
          <div class="flex items-center gap-1.5"><div class="w-2.5 h-2.5 rounded-full bg-cyan-500/50"></div><span class="text-[10px] text-white/30">Recebidas</span></div>
          <div class="flex items-center gap-1.5"><div class="w-2.5 h-2.5 rounded-full bg-red-500/50"></div><span class="text-[10px] text-white/30">Enviadas</span></div>
        </div>
        <div class="flex items-end gap-3 h-32">
          <div class="flex-1 flex flex-col items-center gap-1"><div class="w-full flex gap-1 items-end justify-center h-24"><div class="w-5 rounded-t min-h-[4px]" style="height:${Math.max(4,s.messages_in/3)}px;background:rgba(34,211,238,0.3)"></div><div class="w-5 rounded-t min-h-[4px]" style="height:${Math.max(4,s.messages_out/3)}px;background:rgba(220,38,38,0.3)"></div></div><span class="text-[10px] text-white/20">Total</span><span class="text-[10px] text-white/30">${s.messages_in} / ${s.messages_out}</span></div>
        </div>
      </div>
    </div>
  `;
}

function backToGeneralDash() {
  dashMode = 'general';
  dashAgentId = null;
  const sel = document.getElementById('dashAgentSelect');
  if (sel) sel.value = 'all';
  // Force hide individual, show general
  const indEl = document.getElementById('dash-individual');
  if (indEl) { indEl.style.display = 'none'; indEl.innerHTML = ''; }
  const genEl = document.getElementById('dash-general');
  if (genEl) genEl.style.display = 'block';
  loadDashboard();
}

// ── Sales Page ──
async function loadSales() {
  const pp = getPeriodParams();
  const [sales, stats, abandonments, abStats] = await Promise.all([
    api('/sales' + pp), api('/sales/stats' + pp), api('/cart-abandonments' + pp), api('/cart-abandonments/stats' + pp)
  ]);

  // Stats cards
  const el = (id) => document.getElementById(id);
  el('v-sales-today').textContent = stats.total_sales || stats.sales_today || 0;
  el('v-amount-today').textContent = 'R$ ' + (stats.total_amount || stats.amount_today || 0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  const vlabel1 = document.querySelector('#v-sales-today')?.closest('.glass')?.querySelector('.tracking-wider');
  if (vlabel1) vlabel1.textContent = 'Vendas ' + getPeriodLabel();
  const vlabel2 = document.querySelector('#v-amount-today')?.closest('.glass')?.querySelector('.tracking-wider');
  if (vlabel2) vlabel2.textContent = 'Faturamento ' + getPeriodLabel();
  el('v-avg-ticket').textContent = 'R$ ' + (stats.avg_ticket || 0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  el('v-abandonments-today').textContent = abStats.today || 0;
  el('v-sales-7d').textContent = stats.sales_7d || 0;
  el('v-amount-7d').textContent = 'R$ ' + (stats.amount_7d || 0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});
  el('v-sales-30d').textContent = stats.sales_30d || 0;
  el('v-amount-30d').textContent = 'R$ ' + (stats.amount_30d || 0).toLocaleString('pt-BR', {minimumFractionDigits:2, maximumFractionDigits:2});

  // Recovery rate
  const totalAb30 = abStats.last_30d || 0;
  const totalSales30 = stats.sales_30d || 0;
  const recoveryRate = (totalAb30 + totalSales30) > 0 ? Math.round(totalSales30 / (totalAb30 + totalSales30) * 100) : 0;
  el('v-recovery-rate').textContent = recoveryRate + '%';

  // Sales table
  const tbody = el('v-sales-table');
  if (sales && sales.length) {
    tbody.innerHTML = sales.slice(0, 30).map(s => {
      const dt = s.created_at ? s.created_at.substring(0, 16).replace('T', ' ') : '';
      return `<tr class="border-b border-white/[0.04]" style="transition:background .2s" onmouseover="this.style.background='rgba(255,255,255,0.02)'" onmouseout="this.style.background='transparent'">
        <td class="py-3 px-4 text-white/50 text-xs">${esc(dt)}</td>
        <td class="py-3 px-4 text-white/80 text-xs font-medium">${esc((s.product||'').substring(0,40))}</td>
        <td class="py-3 px-4 text-white/70 text-xs">${esc(s.buyer_name||'')}</td>
        <td class="py-3 px-4 text-white/50 text-xs">${esc(s.buyer_email||'')}</td>
        <td class="py-3 px-4 text-xs font-medium">${s.buyer_phone ? '<a href="https://wa.me/55' + (s.buyer_phone||'').replace(/\\D/g,'') + '" target="_blank" style="color:#22d3ee;text-decoration:underline;cursor:pointer">📱 ' + esc(s.buyer_phone) + '</a>' : '-'}</td>
        <td class="py-3 px-4 text-xs font-medium stat-number">R$ ${(s.amount||0).toLocaleString('pt-BR',{minimumFractionDigits:2})}</td>
        <td class="py-3 px-4"><span class="badge badge-active" style="font-size:10px">${esc(s.platform||'')}</span></td>
      </tr>`;
    }).join('');
  } else {
    tbody.innerHTML = '<tr><td colspan="7" class="text-center py-12 text-white/20 text-sm">Nenhuma venda registrada</td></tr>';
  }

  // Abandonments table
  const abBody = el('v-abandonments-table');
  if (abandonments && abandonments.length) {
    abBody.innerHTML = abandonments.slice(0, 30).map(a => {
      const dt = a.created_at ? a.created_at.substring(0, 16).replace('T', ' ') : '';
      const evtLabel = (a.event_type||'').replace('PURCHASE_','').replace(/_/g,' ');
      return `<tr class="border-b border-white/[0.04]" style="transition:background .2s" onmouseover="this.style.background='rgba(239,68,68,0.02)'" onmouseout="this.style.background='transparent'">
        <td class="py-3 px-4 text-white/50 text-xs">${esc(dt)}</td>
        <td class="py-3 px-4 text-white/80 text-xs font-medium">${esc((a.product||'').substring(0,40))}</td>
        <td class="py-3 px-4 text-white/70 text-xs">${esc(a.buyer_name||'')}</td>
        <td class="py-3 px-4 text-white/50 text-xs">${esc(a.buyer_email||'')}</td>
        <td class="py-3 px-4 text-xs font-bold">${a.buyer_phone ? '<a href="https://wa.me/55' + (a.buyer_phone||'').replace(/\\D/g,'') + '" target="_blank" style="color:#22d3ee;text-decoration:underline;cursor:pointer">📱 ' + esc(a.buyer_phone) + '</a>' : '-'}</td>
        <td class="py-3 px-4"><span class="badge badge-error" style="font-size:10px">${esc(evtLabel)}</span></td>
        <td class="py-3 px-4"><span class="badge" style="background:rgba(245,158,11,0.12);color:#f59e0b;font-size:10px;cursor:pointer">Recuperar</span></td>
      </tr>`;
    }).join('');
  } else {
    abBody.innerHTML = '<tr><td colspan="7" class="text-center py-12 text-white/20 text-sm">Nenhum abandono registrado</td></tr>';
  }
}

// ── Agents ──
async function loadAgents() {
  agents = await api('/agents');
  showAgentsList();
}

function showAgentsList() {
  document.getElementById('agents-list-view').style.display = 'block';
  document.getElementById('agents-detail-view').style.display = 'none';
  const grid = document.getElementById('agents-grid');
  if (!agents.length) {
    grid.innerHTML = `<div class="empty" style="grid-column:1/-1">
      <div class="empty-icon">&#128126;</div>
      <p>Nenhum agente criado ainda</p>
      <button class="btn btn-primary" onclick="openCreate()">+ Criar Primeiro Agente</button>
    </div>`;
    return;
  }
  grid.innerHTML = agents.map(a => `
    <div class="agent-card" onclick="showAgentDetail(${a.id})">
      <div class="agent-card-header" style="position:relative;z-index:1">
        <div>
          <div class="agent-name">${esc(a.name)}</div>
          <div class="agent-company">${esc(a.company || '')}</div>
        </div>
        <span class="badge badge-${a.status}">${a.status}</span>
      </div>
      <div class="agent-meta" style="position:relative;z-index:1">
        <span>&#9654; Porta ${a.port || '-'}</span>
        <span>&#8595; ${a.messages_in || 0}</span>
        <span>&#8593; ${a.messages_out || 0}</span>
        <span>&#9679; ${a.contacts || 0}</span>
      </div>
      <div style="position:relative;z-index:1;margin-top:10px">
        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();openAgentDash(${a.id})">Ver Dashboard</button>
      </div>
    </div>
  `).join('');
}

async function showAgentDetail(id) {
  const a = await api(`/agents/${id}`);
  const files = await api(`/agents/${id}/files`);
  document.getElementById('agents-list-view').style.display = 'none';
  const detail = document.getElementById('agents-detail-view');
  detail.style.display = 'block';

  let filesHtml = '';
  if (files && files.length) {
    filesHtml = files.map(f => `<div class="file-item">
      <span>${esc(f.filename)} <span style="color:rgba(255,255,255,0.25)">(${f.file_type}, ${f.size || 0} chars)</span></span>
      <button class="file-remove" onclick="removeFile(${id}, ${f.id})">Remover</button>
    </div>`).join('');
  } else {
    filesHtml = '<p style="font-size:12px;color:rgba(255,255,255,0.25)">Nenhum arquivo de treinamento</p>';
  }

  detail.innerHTML = `
    <div class="detail-header">
      <div>
        <div style="font-size:20px;font-weight:600">${esc(a.name)} <span class="badge badge-${a.status}" style="vertical-align:middle">${a.status}</span></div>
        <div style="color:rgba(255,255,255,0.4);font-size:13px;margin-top:4px">${esc(a.company || '')} | Porta ${a.port || '-'} | PM2: ${a.pm2_status || 'unknown'}</div>
      </div>
      <div class="detail-actions">
        <button class="btn btn-secondary btn-sm" onclick="showAgentsList()">Voltar</button>
        <button class="btn btn-secondary btn-sm" onclick="openAgentDash(${a.id})">Ver Dashboard</button>
        ${a.status !== 'active' ? `<button class="btn btn-success btn-sm" onclick="startAgent(${a.id})">Iniciar</button>` : ''}
        ${a.status === 'active' ? `<button class="btn btn-secondary btn-sm" onclick="stopAgent(${a.id})">Parar</button>` : ''}
        <button class="btn btn-secondary btn-sm" onclick="openEdit(${a.id})">Editar</button>
        <button class="btn btn-danger btn-sm" onclick="deleteAgent(${a.id})">Deletar</button>
      </div>
    </div>
    <div class="detail-stats">
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Mensagens IN</div>
        <div class="text-3xl font-bold stat-number">${a.messages_in || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Mensagens OUT</div>
        <div class="text-3xl font-bold stat-number">${a.messages_out || 0}</div>
      </div>
      <div class="glass rounded-3xl p-7 gradient-border card-hover">
        <div class="text-[11px] text-white/40 font-medium uppercase tracking-wider mb-2">Contatos</div>
        <div class="text-3xl font-bold stat-number">${a.contacts || 0}</div>
      </div>
    </div>
    <div class="detail-section">
      <h3>Configuracao</h3>
      <div class="detail-field"><div class="label">Webhook URL</div><div class="value">${esc(a.webhook_url || '-')}</div></div>
      <div class="detail-field"><div class="label">Recebimento</div><div class="value">${esc(a.receive_method || 'webhook')}</div></div>
      <div class="detail-field"><div class="label">Envio</div><div class="value">${esc(a.send_method || 'api')}</div></div>
      <div class="detail-field"><div class="label">Location ID do CRM</div><div class="value">${esc(a.ghl_location_id || '-')}</div></div>
      <div class="detail-field"><div class="label">Calendar</div><div class="value">${esc(a.calendar_id || '-')}</div></div>
    </div>
    <div class="detail-section">
      <h3>Personalidade</h3>
      <div class="detail-field"><div class="value">${esc(a.personality || 'Nao configurada')}</div></div>
    </div>
    <div class="detail-section">
      <h3>Produtos</h3>
      <div class="detail-field"><div class="value">${esc(a.products || 'Nao configurado')}</div></div>
    </div>
    ${a.links ? `<div class="detail-section"><h3>Links</h3><div class="detail-field"><div class="value">${esc(a.links)}</div></div></div>` : ''}
    ${a.spin_flow ? `<div class="detail-section"><h3>Fluxo</h3><div class="detail-field"><div class="value">${esc(a.spin_flow)}</div></div></div>` : ''}
    <div class="detail-section">
      <h3>Arquivos de Treinamento</h3>
      ${filesHtml}
    </div>
  `;
}

async function removeFile(agentId, fileId) {
  if (!confirm('Remover arquivo de treinamento?')) return;
  await api(`/agents/${agentId}/files/${fileId}`, 'DELETE');
  toast('Arquivo removido', 'success');
  showAgentDetail(agentId);
}

async function startAgent(id) {
  toast('Iniciando agente...');
  const r = await api(`/agents/${id}/start`, 'POST');
  if (r.status === 'active') toast('Agente iniciado!', 'success');
  else toast('Erro ao iniciar', 'error');
  showAgentDetail(id);
}

async function stopAgent(id) {
  await api(`/agents/${id}/stop`, 'POST');
  toast('Agente parado', 'success');
  showAgentDetail(id);
}

async function deleteAgent(id) {
  if (!confirm('Deletar esse agente?')) return;
  await api(`/agents/${id}`, 'DELETE');
  toast('Agente deletado', 'success');
  loadAgents();
}

// ── Channels ──
async function loadChannels() {
  const [ch, ag] = await Promise.all([api('/channels'), api('/agents')]);
  channels = ch;
  agents = ag;
  renderChannels();
}

function renderChannels() {
  const list = document.getElementById('channels-list');
  if (!channels.length) {
    list.innerHTML = `<div class="empty">
      <div class="empty-icon">&#128225;</div>
      <p>Nenhum canal configurado</p>
      <button class="btn btn-primary" onclick="openChannelModal()">+ Criar Primeiro Canal</button>
    </div>`;
    return;
  }
  const icons = {instagram:'&#128247;', whatsapp:'&#128172;', telegram:'&#9992;', sms:'&#128233;'};
  list.innerHTML = channels.map(c => `
    <div class="channel-card">
      <div class="channel-info">
        <div class="channel-type">${icons[c.channel_type] || '&#9679;'} ${esc(c.channel_type)}</div>
        <div class="channel-agent">Agente: ${esc(c.agent_name || 'ID ' + c.agent_id)}</div>
        <div class="channel-url" onclick="copyUrl(this)" title="Clique para copiar">${esc(c.webhook_url || '')}</div>
      </div>
      <div class="channel-actions">
        <span class="badge badge-${c.status}">${c.status}</span>
        <button class="btn btn-danger btn-sm" onclick="deleteChannel(${c.id})" style="position:relative;z-index:1">Remover</button>
      </div>
    </div>
  `).join('');
}

function copyUrl(el) {
  navigator.clipboard.writeText(el.textContent.trim());
  toast('URL copiada!', 'success');
}

function copyField(fieldId) {
  const v = document.getElementById(fieldId).value;
  if (v) { navigator.clipboard.writeText(v); toast('Copiado!', 'success'); }
}

async function deleteChannel(id) {
  if (!confirm('Remover canal?')) return;
  await api(`/channels/${id}`, 'DELETE');
  toast('Canal removido', 'success');
  loadChannels();
}

// ── Form: Toggle receive/send methods ──
function toggleReceiveMethod() {
  const val = document.querySelector('input[name="receive_method"]:checked').value;
  document.getElementById('receive-webhook-fields').style.display = val === 'webhook' ? 'block' : 'none';
  document.getElementById('receive-api-fields').style.display = val === 'api' ? 'block' : 'none';
}

function toggleSendMethod() {
  const val = document.querySelector('input[name="send_method"]:checked').value;
  document.getElementById('send-api-fields').style.display = val === 'api' ? 'block' : 'none';
  document.getElementById('send-webhook-fields').style.display = val === 'webhook' ? 'block' : 'none';
}

// ── File Upload ──
const uploadZone = document.getElementById('upload-zone');
if (uploadZone) {
  uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.classList.add('dragover'); });
  uploadZone.addEventListener('dragleave', () => uploadZone.classList.remove('dragover'));
  uploadZone.addEventListener('drop', e => {
    e.preventDefault(); uploadZone.classList.remove('dragover');
    if (e.dataTransfer.files.length) handleFileUpload(e.dataTransfer.files);
  });
}

async function handleFileUpload(files) {
  const agentId = document.getElementById('f-id').value;
  if (!agentId) {
    toast('Salve o agente primeiro para enviar arquivos', 'error');
    return;
  }
  for (const file of files) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['pdf','md','txt'].includes(ext)) {
      toast(`Formato nao suportado: ${file.name}`, 'error');
      continue;
    }
    const formData = new FormData();
    formData.append('file', file);
    try {
      const tk = localStorage.getItem('agents_token');
      const r = await fetch(`${API}/agents/${agentId}/files`, { method: 'POST', body: formData, headers: tk ? {'Authorization': 'Bearer ' + tk} : {} });
      const data = await r.json();
      if (r.ok) {
        toast(`${file.name} enviado!`, 'success');
        loadAgentFiles(agentId);
      } else {
        toast(data.error || 'Erro ao enviar', 'error');
      }
    } catch(e) {
      toast('Erro: ' + e.message, 'error');
    }
  }
}

async function loadAgentFiles(agentId) {
  const files = await api(`/agents/${agentId}/files`);
  const list = document.getElementById('file-list');
  if (!files || !files.length) {
    list.innerHTML = '';
    return;
  }
  list.innerHTML = files.map(f => `<div class="file-item">
    <span>${esc(f.filename)} <span style="color:rgba(255,255,255,0.25)">(${f.file_type})</span></span>
    <button class="file-remove" onclick="deleteTrainingFile(${agentId}, ${f.id})">Remover</button>
  </div>`).join('');
}

async function deleteTrainingFile(agentId, fileId) {
  await api(`/agents/${agentId}/files/${fileId}`, 'DELETE');
  toast('Arquivo removido', 'success');
  loadAgentFiles(agentId);
}

// ── Modals ──
function closeModal(id) {
  document.getElementById(id).classList.remove('open');
}

function openCreate() {
  document.getElementById('modal-agent-title').textContent = 'Novo Agente';
  document.getElementById('btn-submit').textContent = 'Criar Agente';
  document.getElementById('f-id').value = '';
  document.getElementById('agent-form').reset();
  document.getElementById('f-receive-webhook-url').value = '';
  document.getElementById('f-sales-webhook-url').value = '';
  document.getElementById('file-list').innerHTML = '';
  toggleReceiveMethod();
  toggleSendMethod();
  document.getElementById('modal-agent').classList.add('open');
}

async function openEdit(id) {
  const a = await api(`/agents/${id}`);
  document.getElementById('modal-agent-title').textContent = 'Editar Agente';
  document.getElementById('btn-submit').textContent = 'Salvar';
  document.getElementById('f-id').value = a.id;
  document.getElementById('f-name').value = a.name || '';
  document.getElementById('f-company').value = a.company || '';
  document.getElementById('f-personality').value = a.personality || '';
  document.getElementById('f-products').value = a.products || '';
  document.getElementById('f-links').value = a.links || '';
  document.getElementById('f-blocked').value = a.blocked_names || '';
  document.getElementById('f-ghl-key').value = a.ghl_api_key || '';
  document.getElementById('f-ghl-loc').value = a.ghl_location_id || '';
  document.getElementById('f-calendar').value = a.calendar_id || '';
  document.getElementById('f-spin').value = a.spin_flow || '';
  document.getElementById('f-receive-webhook-url').value = `https://agents.{{DOMINIO_AI}}/webhook/${a.id}`;
  document.getElementById('f-receive-api-endpoint').value = a.receive_api_endpoint || '';
  document.getElementById('f-send-webhook-url').value = a.send_webhook_url || '';
  document.getElementById('f-sales-webhook-url').value = `https://agents.{{DOMINIO_AI}}/api/agents/${a.id}/sales/webhook`;

  // Set radio buttons
  const rm = a.receive_method || 'webhook';
  const sm = a.send_method || 'api';
  document.querySelector(`input[name="receive_method"][value="${rm}"]`).checked = true;
  document.querySelector(`input[name="send_method"][value="${sm}"]`).checked = true;
  toggleReceiveMethod();
  toggleSendMethod();

  // Load files
  loadAgentFiles(a.id);

  document.getElementById('modal-agent').classList.add('open');
}

async function submitAgentForm(e) {
  e.preventDefault();
  const id = document.getElementById('f-id').value;
  const receiveMethod = document.querySelector('input[name="receive_method"]:checked').value;
  const sendMethod = document.querySelector('input[name="send_method"]:checked').value;
  const data = {
    name: document.getElementById('f-name').value,
    company: document.getElementById('f-company').value,
    personality: document.getElementById('f-personality').value,
    products: document.getElementById('f-products').value,
    links: document.getElementById('f-links').value,
    blocked_names: document.getElementById('f-blocked').value,
    webhook_url: 'https://agents.{{DOMINIO_AI}}/webhook/' + (id || 'new'),
    ghl_api_key: document.getElementById('f-ghl-key').value,
    ghl_location_id: document.getElementById('f-ghl-loc').value,
    calendar_id: document.getElementById('f-calendar').value,
    spin_flow: document.getElementById('f-spin').value,
    receive_method: receiveMethod,
    send_method: sendMethod,
    send_webhook_url: document.getElementById('f-send-webhook-url').value,
    receive_api_endpoint: document.getElementById('f-receive-api-endpoint').value,
  };
  if (id) {
    await api(`/agents/${id}`, 'PUT', data);
    toast('Agente atualizado!', 'success');
    closeModal('modal-agent');
    showAgentDetail(parseInt(id));
  } else {
    const r = await api('/agents', 'POST', data);
    toast(`Agente criado! Porta: ${r.port}`, 'success');
    // Update webhook URLs with real ID
    document.getElementById('f-receive-webhook-url').value = `https://agents.{{DOMINIO_AI}}/webhook/${r.id}`;
    document.getElementById('f-sales-webhook-url').value = `https://agents.{{DOMINIO_AI}}/api/agents/${r.id}/sales/webhook`;
    closeModal('modal-agent');
    loadAgents();
  }
}

function openChannelModal() {
  const sel = document.getElementById('fc-agent');
  sel.innerHTML = agents.map(a => `<option value="${a.id}">${esc(a.name)}</option>`).join('');
  if (!agents.length) sel.innerHTML = '<option value="">Crie um agente primeiro</option>';
  updateChannelWebhook();
  sel.onchange = updateChannelWebhook;
  document.getElementById('modal-channel').classList.add('open');
}

function updateChannelWebhook() {
  const agentId = document.getElementById('fc-agent').value;
  document.getElementById('fc-webhook').value = agentId ? `https://agents.{{DOMINIO_AI}}/webhook/${agentId}` : '';
}

async function submitChannelForm(e) {
  e.preventDefault();
  const agentId = document.getElementById('fc-agent').value;
  const channelType = document.getElementById('fc-type').value;
  if (!agentId) { toast('Selecione um agente', 'error'); return; }
  await api('/channels', 'POST', {agent_id: parseInt(agentId), channel_type: channelType});
  toast('Canal criado!', 'success');
  closeModal('modal-channel');
  loadChannels();
}

// ── Helpers ──
function esc(s) { if (!s) return ''; const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
// WhatsApp links via event delegation - no DOM manipulation needed
document.addEventListener('click', function(e) {
  var td = e.target.closest('[data-wa]');
  if (!td) return;
  var num = td.getAttribute('data-wa');
  if (!num) return;
  if (num.length >= 10 && num.length <= 11) num = '55' + num;
  e.preventDefault();
  e.stopPropagation();
  location.href = 'https://wa.me/' + num;
});
function waLink(phone) {
  if(!phone) return "-";
  var n = String(phone).replace(/\D/g,"");
  if (n.length >= 10 && n.length <= 11 && !n.startsWith("55")) n = "55" + n;
  var a = document.createElement("a");
  a.href = "https://wa.me/" + n;
  a.target = "_blank";
  a.style.cssText = "color:#22d3ee;text-decoration:underline;cursor:pointer";
  a.textContent = "\ud83d\udcf1 " + phone;
  var tmp = document.createElement("div");
  tmp.appendChild(a);
  return tmp.innerHTML;
}

function toast(msg, type='success') {
  const t = document.createElement('div');
  t.className = `toast toast-${type}`;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// Close modals on overlay click
document.querySelectorAll('.modal-overlay').forEach(m => {
  m.addEventListener('click', e => { if (e.target === e.currentTarget) m.classList.remove('open'); });
});

// Keyboard
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
});

// Init
function initApp() {
  updateThemeIcons();
  loadDashboard();
}
// Auto refresh every 30s
// Silent refresh: only update numbers, never reconstruct DOM
setInterval(async () => {
  if (currentPage === 'dashboard' && dashMode === 'general') {
    try {
      const stats = await api('/dashboard/stats' + getPeriodParams());
      const el = (id) => document.getElementById(id);
      if (el('ds-active2')) el('ds-active2').textContent = stats.total_active || 0;
      if (el('ds-in')) el('ds-in').textContent = (stats.total_messages_in || 0).toLocaleString();
      if (el('ds-out')) el('ds-out').textContent = (stats.total_messages_out || 0).toLocaleString();
      if (el('ds-contacts')) el('ds-contacts').textContent = (stats.total_contacts || 0).toLocaleString();
      if (el('ds-rate')) el('ds-rate').textContent = Math.min(stats.response_rate || 0, 100) + '%';
      if (el('ds-total2')) el('ds-total2').textContent = ((stats.total_messages_in||0) + (stats.total_messages_out||0)).toLocaleString();
      if (el('ds-sales')) el('ds-sales').textContent = stats.total_sales || stats.sales_today || 0;
      if (el('ds-amount')) el('ds-amount').textContent = 'R$ ' + (stats.total_amount || stats.amount_today || 0).toLocaleString('pt-BR', {minimumFractionDigits:2});
    } catch(e) {}
  }
  if (currentPage === 'vendas') {
    try { loadSales(); } catch(e) {}
  }
}, 60000);

// ============ B2B PROSPECTING ============
// ============ PROSPECCAO B2B ============

let b2bCategories = null;
let b2bCurrentTab = 'icp';
let b2bCurrentFilter = '';
let b2bCompanies = [];
let b2bSelected = new Set();
let b2bSearchQ = '';

async function loadProspectB2B() {
  await Promise.all([
    loadB2BCategories(),
    loadB2BStats(),
    loadB2BTab(b2bCurrentTab)
  ]);
}

async function loadB2BCategories() {
  if (b2bCategories) return b2bCategories;
  try {
    const res = await fetch('/api/prospect-b2b/categories');
    b2bCategories = await res.json();
    const segSelect = document.getElementById('b2b-segment');
    if (segSelect && segSelect.options.length <= 1) {
      b2bCategories.segments.forEach(s => {
        const o = document.createElement('option');
        o.value = s.key;
        o.textContent = `${s.label} (${s.types.length})`;
        segSelect.appendChild(o);
      });
    }
    const stSelect = document.getElementById('b2b-state');
    if (stSelect && stSelect.options.length <= 1) {
      b2bCategories.states.forEach(s => {
        const o = document.createElement('option');
        o.value = s.uf;
        o.textContent = `${s.uf} - ${s.name}`;
        stSelect.appendChild(o);
      });
    }
  } catch(e) { console.warn('loadB2BCategories', e); }
}

function onB2BSegmentChange() {
  const segKey = document.getElementById('b2b-segment').value;
  const btSelect = document.getElementById('b2b-business-type');
  btSelect.innerHTML = '';
  if (!segKey || !b2bCategories) {
    btSelect.disabled = true;
    btSelect.innerHTML = '<option value="">Selecione um segmento primeiro</option>';
    return;
  }
  const seg = b2bCategories.segments.find(s => s.key === segKey);
  if (!seg) return;
  btSelect.disabled = false;
  btSelect.appendChild(new Option('Selecione...', ''));
  seg.types.forEach(t => {
    btSelect.appendChild(new Option(t.label, t.value));
  });
}

function onB2BStateChange() {} // placeholder se quiser preencher cidades

async function loadB2BStats() {
  try {
    const res = await fetch('/api/prospect-b2b/stats');
    const s = await res.json();
    setText('b2b-targets', s.targets_active);
    setText('b2b-total', s.total_companies);
    setText('b2b-discovered', s.discovered);
    setText('b2b-approved', s.approved);
    setText('b2b-whatsapp', s.with_whatsapp);
    setText('b2b-instagram', s.with_instagram);
    setText('b2b-sent', s.sent);
  } catch(e) { console.warn('loadB2BStats', e); }
}

function setText(id, v) {
  const el = document.getElementById(id);
  if (el) el.textContent = (v ?? '-');
}

function switchB2BTab(tab) {
  b2bCurrentTab = tab;
  ['icp','companies','messages','runs'].forEach(t => {
    const btn = document.getElementById(`b2btab-${t}`);
    const pane = document.getElementById(`b2btab-content-${t}`);
    if (!btn || !pane) return;
    if (t === tab) {
      btn.className = 'px-4 py-2 text-sm border-b-2 border-cyan-500 text-cyan-400';
      pane.classList.remove('hidden');
    } else {
      btn.className = 'px-4 py-2 text-sm text-white/50 hover:text-white';
      pane.classList.add('hidden');
    }
  });
  loadB2BTab(tab);
}

async function loadB2BTab(tab) {
  if (tab === 'icp') return loadB2BTargets();
  if (tab === 'companies') return loadB2BCompanies();
  if (tab === 'runs') return loadB2BRuns();
}

async function loadB2BTargets() {
  try {
    const res = await fetch('/api/prospect-b2b/targets');
    const data = await res.json();
    const tbody = document.getElementById('b2b-targets-tbody');
    tbody.innerHTML = '';
    (data.targets || []).forEach(t => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5';
      tr.innerHTML = `
        <td class="px-4 py-2">${escapeHtml(t.business_label || t.business_type)}</td>
        <td class="px-4 py-2">${escapeHtml(t.location_text)}</td>
        <td class="px-4 py-2">${(t.radius_meters/1000).toFixed(1)} km</td>
        <td class="px-4 py-2 opacity-70">${t.last_run_at ? _b2bFormatDate(t.last_run_at) : '-'}</td>
        <td class="px-4 py-2 text-right space-x-1">
          <button class="btn btn-secondary text-xs" onclick="b2bRunTarget(${t.id})">Rodar</button>
          <button class="btn btn-secondary text-xs" onclick="deleteB2BTarget(${t.id})" style="background:rgba(239,68,68,0.15);color:#fca5a5">Excluir</button>
        </td>`;
      tbody.appendChild(tr);
    });
    if (!(data.targets || []).length) {
      tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-6 text-center text-white/40">Nenhum ICP configurado</td></tr>';
    }
  } catch(e) { console.warn('loadB2BTargets', e); }
}

async function addB2BTarget(_runAfter) {
  const segKey = document.getElementById('b2b-segment').value;
  const business_type = document.getElementById('b2b-business-type').value;
  const state = document.getElementById('b2b-state').value;
  const city = document.getElementById('b2b-city').value.trim();
  const radius_km = parseFloat(document.getElementById('b2b-radius-km').value) || 5;
  const max_results = parseInt(document.getElementById('b2b-max').value) || 60;
  if (!business_type) { alert('Selecione o tipo de negocio'); return null; }
  if (!city) { alert('Informe cidade ou bairro'); return null; }
  const location_text = state ? `${city}, ${state}, Brasil` : `${city}, Brasil`;
  const body = {
    business_type, location_text,
    radius_meters: Math.max(1000, Math.round(radius_km * 1000)),
    max_results,
    segment: segKey,
  };
  try {
    const res = await fetch('/api/prospect-b2b/targets', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) { alert('Erro: ' + (data.error || 'desconhecido')); return null; }
    document.getElementById('b2b-city').value = '';
    await loadB2BTargets();
    await loadB2BStats();
    return data.id;
  } catch(e) { alert('erro: '+e.message); return null; }
}

async function addAndRunB2BTarget() {
  const id = await addB2BTarget(true);
  if (id) {
    await b2bRunTarget(id);
  }
}

async function deleteB2BTarget(id) {
  if (!confirm('Excluir este ICP?')) return;
  try {
    await fetch(`/api/prospect-b2b/targets/${id}`, { method: 'DELETE' });
    await loadB2BTargets();
    await loadB2BStats();
  } catch(e) { alert('erro: '+e.message); }
}

async function b2bRunTarget(target_id) {
  const btn = event && event.target;
  if (btn) { btn.disabled = true; btn.textContent = 'Rodando...'; }
  try {
    const res = await fetch('/api/prospect-b2b/run-now', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ target_id, trigger: 'manual' })
    });
    const data = await res.json();
    if (data.ok === false) {
      alert('Erro: ' + (data.error || 'desconhecido'));
    } else {
      alert(`Rodada concluida.\nDescobertas: ${data.discovered}\nNovas: ${data.new}\nDuplicadas: ${data.duplicates}\nCusto estimado: $${(data.estimated_cost_usd||0).toFixed(3)}`);
      switchB2BTab('companies');
    }
  } catch(e) { alert('erro: '+e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = 'Rodar'; } loadB2BStats(); }
}

async function b2bRunAll() {
  const btn = document.getElementById('btn-b2b-run-all');
  if (btn) { btn.disabled = true; btn.textContent = 'Rodando...'; }
  try {
    const res = await fetch('/api/prospect-b2b/run-now', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ trigger: 'bulk' })
    });
    const data = await res.json();
    alert('Rodadas executadas: ' + ((data.runs||[]).length));
    loadB2BStats();
  } catch(e) { alert('erro: '+e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = 'Rodar todos os ICPs'; } }
}

let b2bSearchTimer = null;
function b2bSearchDebounced() {
  if (b2bSearchTimer) clearTimeout(b2bSearchTimer);
  b2bSearchTimer = setTimeout(() => {
    b2bSearchQ = document.getElementById('b2b-search-q').value.trim();
    loadB2BCompanies();
  }, 350);
}

function setB2BFilter(f) {
  b2bCurrentFilter = f;
  document.querySelectorAll('.b2b-filter-btn').forEach(btn => {
    if (btn.dataset.filter === f) {
      btn.className = 'b2b-filter-btn px-3 py-1.5 text-xs rounded bg-cyan-500/20 text-cyan-300 border border-cyan-500/30';
    } else {
      btn.className = 'b2b-filter-btn px-3 py-1.5 text-xs rounded text-white/50 border border-white/10 hover:bg-white/5';
    }
  });
  loadB2BCompanies();
}

async function loadB2BCompanies() {
  try {
    const params = new URLSearchParams();
    if (b2bCurrentFilter) params.set('status', b2bCurrentFilter);
    if (b2bSearchQ) params.set('q', b2bSearchQ);
    params.set('limit', 200);
    const res = await fetch('/api/prospect-b2b/companies?' + params.toString());
    const data = await res.json();
    b2bCompanies = data.companies || [];
    const tbody = document.getElementById('b2b-companies-tbody');
    tbody.innerHTML = '';
    b2bCompanies.forEach(c => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5 hover:bg-white/5';
      const igLink = c.instagram ? `<a href="https://instagram.com/${escapeHtml(c.instagram)}" target="_blank" class="text-fuchsia-300 hover:underline">@${escapeHtml(c.instagram)}</a>` : '';
      const siteLink = c.website ? `<a href="${escapeHtml(c.website)}" target="_blank" class="text-cyan-300 hover:underline">site</a>` : '';
      const phoneStr = c.phone ? escapeHtml(c.phone) : '<span class="opacity-30">-</span>';
      tr.innerHTML = `
        <td class="px-3 py-2"><input type="checkbox" data-id="${c.id}" onchange="b2bToggleRow(${c.id}, this.checked)" ${b2bSelected.has(c.id)?'checked':''} /></td>
        <td class="px-4 py-2">
          <div class="font-semibold cursor-pointer hover:text-cyan-300" onclick="openB2BCompany(${c.id})">${escapeHtml(c.name||'')}</div>
          ${c.is_person_profile ? '<div class="text-[10px] text-amber-300">parece pessoa fisica</div>' : ''}
        </td>
        <td class="px-4 py-2 opacity-80 text-xs">${escapeHtml(c.formatted_address||'')}</td>
        <td class="px-4 py-2">${phoneStr}</td>
        <td class="px-4 py-2 space-x-2 text-xs">${siteLink} ${igLink}</td>
        <td class="px-4 py-2 text-xs opacity-70">${escapeHtml(c.business_label||c.segment||'')}</td>
        <td class="px-4 py-2"><span class="text-xs px-2 py-0.5 rounded ${b2bStatusClass(c.status)}">${escapeHtml(c.status)}</span></td>
        <td class="px-4 py-2 text-right space-x-1">
          ${c.status === 'discovered' || c.status === 'qualified' ? `<button class="btn btn-primary text-xs" onclick="b2bApprove(${c.id})">Aprovar</button>` : ''}
          <button class="btn btn-secondary text-xs" onclick="openB2BCompany(${c.id})">Ver</button>
        </td>`;
      tbody.appendChild(tr);
    });
    if (!b2bCompanies.length) {
      tbody.innerHTML = '<tr><td colspan="8" class="px-4 py-6 text-center text-white/40">Nenhuma empresa</td></tr>';
    }
    updateB2BBulkButton();
  } catch(e) { console.warn('loadB2BCompanies', e); }
}

function b2bStatusClass(s) {
  return ({
    discovered: 'bg-blue-500/20 text-blue-300',
    qualified: 'bg-yellow-500/20 text-yellow-300',
    approved_to_send: 'bg-emerald-500/20 text-emerald-300',
    dm_sent: 'bg-cyan-500/20 text-cyan-300',
    replied: 'bg-fuchsia-500/20 text-fuchsia-300',
    skipped: 'bg-white/10 text-white/50',
  })[s] || 'bg-white/10 text-white/60';
}

function b2bToggleRow(id, checked) {
  if (checked) b2bSelected.add(id); else b2bSelected.delete(id);
  updateB2BBulkButton();
}

function b2bToggleSelectAll(checked) {
  if (checked) b2bCompanies.forEach(c => b2bSelected.add(c.id));
  else b2bSelected.clear();
  document.querySelectorAll('#b2b-companies-tbody input[type="checkbox"]').forEach(cb => cb.checked = checked);
  updateB2BBulkButton();
}

function updateB2BBulkButton() {
  const n = b2bSelected.size;
  setText('b2b-bulk-count', n + ' selecionadas');
  const btn = document.getElementById('btn-b2b-bulk-approve');
  if (btn) btn.disabled = n === 0;
}

async function b2bApprove(id) {
  try {
    await fetch(`/api/prospect-b2b/companies/${id}/approve`, { method: 'POST' });
    loadB2BCompanies();
    loadB2BStats();
  } catch(e) { alert('erro: '+e.message); }
}

async function b2bBulkApprove() {
  if (!b2bSelected.size) return;
  if (!confirm(`Aprovar ${b2bSelected.size} empresas para envio?`)) return;
  try {
    await fetch('/api/prospect-b2b/companies/bulk-approve', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ ids: Array.from(b2bSelected) })
    });
    b2bSelected.clear();
    loadB2BCompanies();
    loadB2BStats();
  } catch(e) { alert('erro: '+e.message); }
}

async function openB2BCompany(id) {
  try {
    const res = await fetch(`/api/prospect-b2b/companies/${id}`);
    const data = await res.json();
    if (!data.ok) return;
    const c = data.company;
    document.getElementById('b2b-company-title').textContent = c.name;
    const igLink = c.instagram ? `<a href="https://instagram.com/${escapeHtml(c.instagram)}" target="_blank" class="text-fuchsia-300 hover:underline">@${escapeHtml(c.instagram)}</a>` : '<span class="opacity-30">-</span>';
    const wppLink = c.whatsapp ? `<a href="https://wa.me/${escapeHtml(c.whatsapp)}" target="_blank" class="text-emerald-300 hover:underline">${escapeHtml(c.whatsapp)}</a>` : '<span class="opacity-30">-</span>';
    const siteLink = c.website ? `<a href="${escapeHtml(c.website)}" target="_blank" class="text-cyan-300 hover:underline">${escapeHtml(c.website)}</a>` : '<span class="opacity-30">-</span>';
    const mapLink = c.lat && c.lng ? `<a href="https://maps.google.com/?q=${c.lat},${c.lng}" target="_blank" class="text-cyan-300 hover:underline">abrir no Maps</a>` : '';
    const body = document.getElementById('b2b-company-body');
    body.innerHTML = `
      <div class="space-y-3 text-sm">
        <div><span class="opacity-60">Endereco:</span> ${escapeHtml(c.formatted_address||'-')}</div>
        <div class="grid grid-cols-2 gap-3">
          <div><span class="opacity-60">Telefone:</span> ${escapeHtml(c.phone||'-')}</div>
          <div><span class="opacity-60">WhatsApp:</span> ${wppLink}</div>
          <div><span class="opacity-60">Site:</span> ${siteLink}</div>
          <div><span class="opacity-60">Instagram:</span> ${igLink}</div>
          <div><span class="opacity-60">Rating:</span> ${c.rating ? c.rating + ' ('+c.user_ratings_total+')' : '-'}</div>
          <div><span class="opacity-60">Categoria:</span> ${escapeHtml(c.business_label||c.segment||'-')}</div>
        </div>
        <div><span class="opacity-60">Mapa:</span> ${mapLink}</div>
        <div><span class="opacity-60">Status:</span>
          <select id="b2b-detail-status" class="form-input ml-2" onchange="b2bUpdateStatus(${c.id}, this.value)">
            <option ${c.status==='discovered'?'selected':''}>discovered</option>
            <option ${c.status==='qualified'?'selected':''}>qualified</option>
            <option ${c.status==='approved_to_send'?'selected':''}>approved_to_send</option>
            <option ${c.status==='skipped'?'selected':''}>skipped</option>
          </select>
        </div>
        <div class="opacity-50 text-xs">Descoberta em ${_b2bFormatDate(c.discovered_at)}</div>
      </div>`;
    document.getElementById('modal-b2b-company').classList.add('open');
  } catch(e) { console.warn('openB2BCompany', e); }
}

async function b2bUpdateStatus(id, status) {
  try {
    await fetch(`/api/prospect-b2b/companies/${id}`, {
      method: 'PATCH',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ status })
    });
    loadB2BCompanies();
    loadB2BStats();
  } catch(e) { alert('erro: '+e.message); }
}

async function loadB2BRuns() {
  try {
    const res = await fetch('/api/prospect-b2b/runs');
    const data = await res.json();
    const tbody = document.getElementById('b2b-runs-tbody');
    tbody.innerHTML = '';
    (data.runs || []).forEach(r => {
      const tr = document.createElement('tr');
      tr.className = 'border-t border-white/5';
      tr.innerHTML = `
        <td class="px-4 py-2 text-xs">${_b2bFormatDate(r.started_at)}</td>
        <td class="px-4 py-2 text-xs">${escapeHtml(r.business_label||r.target_id||'-')} &middot; ${escapeHtml(r.location_text||'')}</td>
        <td class="px-4 py-2 text-xs">${escapeHtml(r.trigger)}</td>
        <td class="px-4 py-2">${r.discovered_count}</td>
        <td class="px-4 py-2 text-emerald-300">${r.new_count}</td>
        <td class="px-4 py-2 opacity-60">${r.duplicates_count}</td>
        <td class="px-4 py-2 text-fuchsia-300">${r.cross_channel_count}</td>
        <td class="px-4 py-2">$${(r.estimated_cost_usd||0).toFixed(3)}</td>
        <td class="px-4 py-2"><span class="text-xs px-2 py-0.5 rounded ${r.status==='completed'?'bg-emerald-500/20 text-emerald-300':r.status==='failed'?'bg-red-500/20 text-red-300':'bg-yellow-500/20 text-yellow-300'}">${escapeHtml(r.status)}</span></td>`;
      tbody.appendChild(tr);
    });
    if (!(data.runs || []).length) {
      tbody.innerHTML = '<tr><td colspan="9" class="px-4 py-6 text-center text-white/40">Nenhuma rodada</td></tr>';
    }
  } catch(e) { console.warn('loadB2BRuns', e); }
}

// escapeHtml ja existe no agent-manager.py base; formatDate replicado abaixo
function _b2bFormatDate(d) {
  if (!d) return '';
  try { return new Date(d).toLocaleString('pt-BR', {day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}); }
  catch(e) { return String(d); }
}

</script>
</div><!-- /app-wrapper -->
</body>
</html>
'''

if __name__ == '__main__':
    log('Agent Manager ATIVO na porta 3600')
    uvicorn.run(app, host='0.0.0.0', port=3600, log_level='warning')
