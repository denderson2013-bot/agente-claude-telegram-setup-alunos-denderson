#!/usr/bin/env python3
"""SDR Agent Template - Auto-generated, do not edit manually"""
import asyncio, json, re, os, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# ── Load config from JSON ──
AGENT_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 0
CONFIG_PATH = Path(__file__).parent / f'config-{AGENT_ID}.json'
if not CONFIG_PATH.exists():
    print(f'Config not found: {CONFIG_PATH}')
    sys.exit(1)

with open(CONFIG_PATH) as f:
    CFG = json.load(f)

PORT = CFG['port']
AGENT_NAME = CFG['name']
COMPANY = CFG.get('company', '')
PERSONALITY = CFG.get('personality', 'Voce e um agente de vendas profissional e amigavel.')
PRODUCTS = CFG.get('products', '')
ALLOWED_LINKS = [l.strip() for l in CFG.get('links', '').split('\n') if l.strip()]
BLOCKED_NAMES = [n.strip().lower() for n in CFG.get('blocked_names', '').split(',') if n.strip()]
WEBHOOK_URL = CFG.get('webhook_url', '')
GHL_API_KEY = CFG.get('ghl_api_key', '')
GHL_LOCATION = CFG.get('ghl_location_id', '')
CALENDAR_ID = CFG.get('calendar_id', '')
SPIN_FLOW = CFG.get('spin_flow', '')
SEND_METHOD = CFG.get('send_method', 'api')
SEND_WEBHOOK_URL = CFG.get('send_webhook_url', '')
RECEIVE_METHOD = CFG.get('receive_method', 'webhook')

GHL_BASE = 'https://services.leadconnectorhq.com'
BRT = timezone(timedelta(hours=-3))

def log(msg):
    ts = datetime.now(BRT).strftime('%H:%M:%S')
    print(f'[{ts}] [{AGENT_NAME}] {msg}', flush=True)

def is_business_hours():
    now = datetime.now(BRT)
    return now.weekday() < 5 and 9 <= now.hour < 19

# ── PostgreSQL ──
def get_db():
    return psycopg2.connect(host='127.0.0.1', user='n8n', password=os.getenv('PG_PASS', '{{POSTGRES_PASSWORD}}'), dbname='naia_memory')

def save_message(contact_id, contact_name, direction, message):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            'INSERT INTO dm_conversations (contact_id, contact_name, direction, message) VALUES (%s, %s, %s, %s)',
            (contact_id, contact_name, direction, message))
        cur.execute("""INSERT INTO dm_contact_profiles (contact_id, contact_name, last_contact_at, messages_count)
            VALUES (%s, %s, NOW(), 1)
            ON CONFLICT (contact_id) DO UPDATE SET
                contact_name = COALESCE(EXCLUDED.contact_name, dm_contact_profiles.contact_name),
                last_contact_at = NOW(),
                messages_count = dm_contact_profiles.messages_count + 1""",
            (contact_id, contact_name))
        conn.commit()
        cur.close()
        conn.close()
        # Update agent stats
        update_agent_stats(direction)
    except Exception as e:
        log(f'DB Error: {e}')

def update_agent_stats(direction):
    try:
        conn = get_db()
        cur = conn.cursor()
        col = 'messages_in' if direction == 'inbound' else 'messages_out'
        cur.execute(f'UPDATE sdr_agents SET {col} = {col} + 1, updated_at = NOW() WHERE id = %s', (AGENT_ID,))
        conn.commit()
        cur.close()
        conn.close()
    except:
        pass

# ── Training files loader ──
def load_training_files(agent_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT filename, content FROM sdr_agent_files WHERE agent_id = %s', (agent_id,))
        files = cur.fetchall()
        cur.close()
        conn.close()
        if not files:
            return ''
        parts = []
        for filename, content in files:
            parts.append(f'--- Arquivo: {filename} ---\n{content}')
        return '\n\n'.join(parts)
    except Exception as e:
        log(f'Training files load error: {e}')
        return ''

# Load training context at startup and cache it
TRAINING_CONTEXT = load_training_files(AGENT_ID)
if TRAINING_CONTEXT:
    log(f'Loaded training files ({len(TRAINING_CONTEXT)} chars)')

# ── API helpers ──
http = httpx.AsyncClient(timeout=15)
GHL_HEADERS = {'Authorization': f'Bearer {GHL_API_KEY}', 'Version': '2021-07-28', 'Content-Type': 'application/json'}

async def send_message(contact_id, message):
    """Send message via configured method (API or webhook)"""
    if SEND_METHOD == 'webhook' and SEND_WEBHOOK_URL:
        try:
            r = await http.post(SEND_WEBHOOK_URL,
                json={'contactId': contact_id, 'message': message})
            return r.json()
        except Exception as e:
            log(f'Webhook send error: {e}')
            return {}
    else:
        # Default: send via {{PRODUTO_DONO}} API
        try:
            r = await http.post(f'{GHL_BASE}/conversations/messages', headers=GHL_HEADERS,
                               json={'type': 'IG', 'contactId': contact_id, 'message': message})
            return r.json()
        except Exception as e:
            log(f'API send error: {e}')
            return {}

async def get_history(contact_id):
    try:
        r = await http.get(f'{GHL_BASE}/conversations/search', headers=GHL_HEADERS,
                          params={'locationId': GHL_LOCATION, 'contactId': contact_id, 'limit': '1'})
        conv_id = r.json().get('conversations', [{}])[0].get('id')
        if not conv_id:
            return ''
        r2 = await http.get(f'{GHL_BASE}/conversations/{conv_id}/messages', headers=GHL_HEADERS,
                           params={'limit': '20'})
        data = r2.json()
        msgs = data.get('messages', {})
        if isinstance(msgs, dict):
            msgs = msgs.get('messages', [])
        lines = []
        for m in reversed(msgs):
            if not m.get('body') or m.get('messageType') == 'TYPE_ACTIVITY_OPPORTUNITY':
                continue
            who = 'LEAD' if m.get('direction') == 'inbound' else 'EU'
            lines.append(f"{who}: {(m.get('body',''))[:200]}")
        return '\n'.join(lines)
    except Exception as e:
        log(f'CRM History Error: {e}')
        try:
            conn = get_db()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute('SELECT direction, message FROM dm_conversations WHERE contact_id = %s ORDER BY created_at ASC LIMIT 20', (contact_id,))
            rows = cur.fetchall()
            cur.close(); conn.close()
            return '\n'.join(f"{'LEAD' if r['direction']=='inbound' else 'EU'}: {r['message']}" for r in rows)
        except:
            return ''

async def get_available_slots():
    if not CALENDAR_ID:
        return None
    try:
        now = datetime.now(BRT)
        min_time = now + timedelta(hours=2)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = today + timedelta(days=3)
        r = await http.get(f'{GHL_BASE}/calendars/{CALENDAR_ID}/free-slots', headers=GHL_HEADERS,
                          params={'startDate': today.strftime('%Y-%m-%d'), 'endDate': end_day.strftime('%Y-%m-%d')})
        data = r.json()
        all_slots = []
        day_names = ['domingo', 'segunda', 'terca', 'quarta', 'quinta', 'sexta', 'sabado']
        for date_key, slots in (data or {}).items():
            if not isinstance(slots, list):
                continue
            for slot in slots:
                st = slot.get('startTime', slot) if isinstance(slot, dict) else slot
                dt = datetime.fromisoformat(str(st).replace('Z', '+00:00')).astimezone(BRT)
                if dt < min_time or dt.weekday() == 6:
                    continue
                label = f"{day_names[dt.weekday()]}-feira as {dt.strftime('%H:%M')}"
                all_slots.append({'iso': dt.isoformat(), 'label': label})
        if len(all_slots) >= 2:
            first = all_slots[0]
            second = next((s for s in all_slots if s['iso'][:10] != first['iso'][:10]), all_slots[1])
            return {'text': f"{first['label']} ou {second['label']}", 'slots': [first, second]}
        elif len(all_slots) == 1:
            return {'text': all_slots[0]['label'], 'slots': [all_slots[0]]}
        return None
    except Exception as e:
        log(f'Calendar error: {e}')
        return None

async def update_contact_ghl(contact_id, phone, email):
    try:
        body = {}
        if phone: body['phone'] = phone
        if email: body['email'] = email
        await http.put(f'{GHL_BASE}/contacts/{contact_id}', headers=GHL_HEADERS, json=body)
    except Exception as e:
        log(f'Update contact error: {e}')

# ── Generate response using Claude Agent SDK ──
async def generate_with_claude(name, message, history, available_slots=''):
    exchange_count = history.count('LEAD:')
    history_clean = history.replace('\n', ' | ')[:2000]
    message_clean = message[:300]

    links_text = '\n'.join(f'- {l}' for l in ALLOWED_LINKS) if ALLOWED_LINKS else 'Nenhum link configurado'

    etapa = ''
    if SPIN_FLOW:
        etapa = SPIN_FLOW
    else:
        if exchange_count <= 1:
            etapa = 'ETAPA 1 - RAPPORT: Cumprimente, comente algo do contexto. Pergunte sobre o negocio da pessoa.'
        elif exchange_count == 2:
            etapa = 'ETAPA 2 - PROBLEMA: Entenda a dor. Conecte com o que voce oferece.'
        elif exchange_count == 3:
            etapa = 'ETAPA 3 - IMPLICACAO: Mostre o impacto do problema. Apresente a solucao.'
        else:
            etapa = 'ETAPA 4 - FECHAMENTO: Direcione para acao (link, agendamento, etc).'

    training_section = ''
    if TRAINING_CONTEXT:
        training_section = f'\nMATERIAL DE TREINAMENTO:\n{TRAINING_CONTEXT}\n'

    prompt = f"""{PERSONALITY}

VOCE E: {AGENT_NAME}, representando {COMPANY}.

PRODUTOS/SERVICOS:
{PRODUCTS}

LINKS PERMITIDOS (so envie esses):
{links_text}

REGRAS:
- LEIA O HISTORICO antes de responder
- NUNCA repita o que ja foi dito
- NUNCA se apresente de novo se ja conversou
- NUNCA invente links alem dos permitidos
- Responda com 1-2 mensagens curtas separadas por |||
- Tom casual e profissional

{etapa}

{f'HORARIOS DISPONIVEIS: {available_slots}' if available_slots else ''}
{training_section}
HISTORICO ({exchange_count} trocas): {history_clean or 'Primeira interacao'}

LEAD: {name}
MENSAGEM: {message_clean}

Responda (separe com |||):"""

    try:
        options = ClaudeAgentOptions(max_turns=1)
        result_text = ''
        async for msg in query(prompt=prompt, options=options):
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        result_text += block.text
        return result_text.strip() if result_text else None
    except Exception as e:
        log(f'Claude SDK error: {e}')
        return None

# ── Debounce system ──
DEBOUNCE_SECONDS = 10
pending_messages = {}

async def debounce_fire(contact_id):
    await asyncio.sleep(DEBOUNCE_SECONDS)
    if contact_id not in pending_messages:
        return
    data = pending_messages.pop(contact_id)
    combined = ' '.join(data['messages'])
    log(f'Debounce: {data["name"]} ({len(data["messages"])} msgs)')
    await handle_message(contact_id, data['name'], combined)

def debounce_add(contact_id, name, message):
    if contact_id in pending_messages:
        pending_messages[contact_id]['timer'].cancel()
        pending_messages[contact_id]['messages'].append(message)
    else:
        pending_messages[contact_id] = {'name': name, 'messages': [message]}
    pending_messages[contact_id]['timer'] = asyncio.create_task(debounce_fire(contact_id))

# ── FastAPI app ──
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])

@app.post('/webhook')
async def webhook(request: Request):
    data = await request.json()
    contact_id = data.get('contact_id', '')
    name = data.get('full_name') or data.get('first_name') or '?'
    message = (data.get('message') or {}).get('body', '')
    if not contact_id or not message:
        return {'success': True}
    if any(b in name.lower() for b in BLOCKED_NAMES):
        log(f'Bloqueado: {name}')
        return {'success': True}
    log(f'IN {name}: "{message[:80]}"')
    save_message(contact_id, name, 'inbound', message)
    debounce_add(contact_id, name, message)
    return {'success': True}

async def handle_message(contact_id, name, message):
    history = await get_history(contact_id)
    phone_match = re.search(r'(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}[\s-]?\d{4}', message)
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', message, re.I)
    if phone_match or email_match:
        await update_contact_ghl(contact_id,
            re.sub(r'\D', '', phone_match.group()) if phone_match else None,
            email_match.group() if email_match else None)

    await asyncio.sleep(8)

    try:
        exchange_count = history.count('LEAD:')
        slots_text = ''
        if CALENDAR_ID and exchange_count >= 3:
            slot_data = await get_available_slots()
            if slot_data:
                slots_text = slot_data['text']

        reply = await generate_with_claude(name, message, history, slots_text)
        if not reply:
            log(f'Sem resposta pra {name}')
            return

        msgs = [m.strip() for m in reply.split('|||') if m.strip()]
        for i, msg in enumerate(msgs):
            result = await send_message(contact_id, msg)
            ok = bool(result.get('messageId') or result.get('id') or result.get('success'))
            log(f"{'OK' if ok else 'FAIL'} Msg {i+1}/{len(msgs)} to {name}")
            if ok:
                save_message(contact_id, name, 'outbound', msg)
            if not ok:
                break
            if i < len(msgs) - 1:
                await asyncio.sleep(4)
    except Exception as e:
        log(f'Error: {e}')

@app.get('/health')
async def health():
    return {'status': 'ok', 'agent': AGENT_NAME, 'company': COMPANY, 'port': PORT}

# ── Reload training files endpoint ──
@app.post('/reload-training')
async def reload_training():
    global TRAINING_CONTEXT
    TRAINING_CONTEXT = load_training_files(AGENT_ID)
    log(f'Training files reloaded ({len(TRAINING_CONTEXT)} chars)')
    return {'status': 'ok', 'chars': len(TRAINING_CONTEXT)}

START_TIME = time.time()

if __name__ == '__main__':
    log(f'Agent {AGENT_NAME} ({COMPANY}) ATIVO na porta {PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='warning')
