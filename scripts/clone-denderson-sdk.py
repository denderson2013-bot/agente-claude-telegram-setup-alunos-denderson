#!/usr/bin/env python3
"""Clone do {{DONO}} - Agent SDK version"""
import asyncio, json, re, os, time, subprocess, tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anyio
import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

# ── Config ──
GHL_API_KEY = os.getenv('GHL_API_KEY', '{{GHL_API_KEY}}')
GHL_LOCATION = os.getenv('GHL_LOCATION_ID', '{{GHL_LOCATION_ID}}')
GHL_BASE = 'https://services.leadconnectorhq.com'
EVENTS_FILE = '/tmp/ghl-webhook-events.jsonl'
BRT = timezone(timedelta(hours=-3))

BLOCKED_NAMES = ['jaine', 'jaineamorimsz', 'jaine amorim', 'amanda elia', 'amandaeliaadv', 'amanda elia adv', 'murilo cruz', 'murilo', 'lucas andré', 'lucas andre', 'lucas_andre_souza', 'won academy', 'wonacademy']

MAX_OUTBOUND_PER_CONTACT = 15
COOLDOWN_MINUTES = 60

# ── Knowledge Base: Imersão Claude + OpenClaw ──
IMERSAO_KNOWLEDGE_PATH = '/opt/naia-agent/knowledge/imersao/IMERSAO-KNOWLEDGE-BASE.md'
IMERSAO_KNOWLEDGE = ''
try:
    with open(IMERSAO_KNOWLEDGE_PATH, 'r') as _f:
        IMERSAO_KNOWLEDGE = _f.read()
    print(f'[BOOT] Imersao knowledge base loaded: {len(IMERSAO_KNOWLEDGE)} chars')
except Exception as _e:
    print(f'[BOOT] Warning: could not load imersao knowledge: {_e}')

# ── Audio transcription ──
async def transcribe_audio(url):
    """Download audio from URL and transcribe with Whisper"""
    try:
        log(f'🎤 Transcrevendo audio: {url[:80]}')
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url)
            if r.status_code != 200:
                log(f'🎤 Download falhou: {r.status_code}')
                return None
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
            f.write(r.content)
            tmp_path = f.name
        result = subprocess.run(
            ['whisper', tmp_path, '--language', 'pt', '--model', 'small', '--output_format', 'txt', '--output_dir', '/tmp'],
            capture_output=True, text=True, timeout=60
        )
        txt_path = tmp_path.replace('.ogg', '.txt')
        txt_path2 = f'/tmp/{os.path.basename(tmp_path).replace(".ogg", ".txt")}'
        text = ''
        for p in [txt_path, txt_path2]:
            if os.path.exists(p):
                with open(p) as tf:
                    text = tf.read().strip()
                os.unlink(p)
                break
        os.unlink(tmp_path)
        if text:
            log(f'🎤 Transcricao: "{text[:100]}"')
        else:
            log(f'🎤 Transcricao vazia. stderr: {result.stderr[:200]}')
        return text or None
    except Exception as e:
        log(f'🎤 Erro transcricao: {e}')
        return None

HEADS = [
    {'id': 'WeQ7I4P9U9wj5nTxNb8e', 'name': 'Davi Galvao'},
    {'id': 'fIwdiDBQHSfFhkhHgNBV', 'name': 'Fernando Rolim'},
    {'id': 'TYvpeAsCABGWhONCYUzJ', 'name': 'Jonathan Pires'},
    {'id': 'BPHong51UAw3URn1L7Be', 'name': 'Rafael Ondei'},
]
head_index = 0

def get_next_head():
    global head_index
    h = HEADS[head_index % len(HEADS)]
    head_index += 1
    return h

def log(msg):
    ts = datetime.now(BRT).strftime('%H:%M:%S')
    print(f'[{ts}] {msg}', flush=True)

def is_business_hours():
    """Horario comercial BRT: 08:00 ate 18:59, todos os dias.
    Define se o agente se apresenta como {{DONO}} (1a pessoa) ou Clone do {{DONO}}."""
    now = datetime.now(BRT)
    return 8 <= now.hour < 19

def current_brt_label():
    now = datetime.now(BRT)
    return now.strftime('%H:%M BRT')

# ── PostgreSQL ──
def get_db():
    return psycopg2.connect(host='127.0.0.1', user='n8n', password=os.getenv('PG_PASS', '{{POSTGRES_PASSWORD}}'), dbname='naia_memory')

def save_message(contact_id, contact_name, direction, message):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('INSERT INTO dm_conversations (contact_id, contact_name, direction, message) VALUES (%s, %s, %s, %s)',
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
    except Exception as e:
        log(f'DB Error: {e}')

# ── GHL API helpers ──
http = httpx.AsyncClient(timeout=15)
GHL_HEADERS = {'Authorization': f'Bearer {GHL_API_KEY}', 'Version': '2021-07-28', 'Content-Type': 'application/json'}

async def send_ig(contact_id, message):
    try:
        r = await http.post(f'{GHL_BASE}/conversations/messages', headers=GHL_HEADERS,
                           json={'type': 'IG', 'contactId': contact_id, 'message': message})
        return r.json()
    except Exception as e:
        log(f'SendIG error: {e}')
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
            who = 'LEAD' if m.get('direction') == 'inbound' else 'EU (automacao ou agente)'
            lines.append(f"{who}: {(m.get('body',''))[:200]}")
        return '\n'.join(lines)
    except Exception as e:
        log(f'GHL History Error: {e}')
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
    try:
        cal_id = '3smzlsCyESzM86N3Yh5S'
        now = datetime.now(BRT)
        min_time = now + timedelta(hours=2)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_day = today + timedelta(days=3)
        r = await http.get(f'{GHL_BASE}/calendars/{cal_id}/free-slots', headers=GHL_HEADERS,
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
        log(f'📇 Contato atualizado: {phone or ""} {email or ""}')
    except Exception as e:
        log(f'Update contact error: {e}')

async def create_appointment_ghl(contact_id, slot_iso, contact_name, phone, email):
    try:
        cal_id = '3smzlsCyESzM86N3Yh5S'
        start = datetime.fromisoformat(slot_iso)
        end = start + timedelta(minutes=30)
        head = get_next_head()
        body = {
            'calendarId': cal_id, 'locationId': GHL_LOCATION, 'contactId': contact_id,
            'startTime': start.isoformat(), 'endTime': end.isoformat(),
            'title': f'Reuniao IA - {contact_name}', 'appointmentStatus': 'confirmed',
            'assignedUserId': head['id'], 'phone': phone or '', 'email': email or ''
        }
        r = await http.post(f'{GHL_BASE}/calendars/events/appointments', headers=GHL_HEADERS, json=body)
        data = r.json()
        if data.get('id') or data.get('appointment'):
            log(f"📅 Appointment criado: {contact_name} em {start.strftime('%d/%m %H:%M')} com {head['name']}")
        else:
            log(f"⚠️ Appointment response: {str(data)[:200]}")
        try:
            await http.put(f'{GHL_BASE}/contacts/{contact_id}', headers=GHL_HEADERS, json={'assignedTo': head['id']})
            log(f"👤 Contato atribuido a {head['name']}")
        except: pass
        return {**data, 'head': head}
    except Exception as e:
        log(f'Create appointment error: {e}')
        return None

# ── Ademir handoff lookup ──
def get_ademir_context(contact_id, name):
    """Procura briefing do Ademir em dm_contact_profiles.
    Match por contact_id direto OU por nome/instagram_username (caso o GHL ainda nao
    tenha vinculado ao contact_id sintetico 'ademir:USERNAME')."""
    try:
        conn = get_db()
        cur = conn.cursor()
        # 1) Try direct contact_id match
        cur.execute(
            "SELECT instagram_username, bio, notes FROM dm_contact_profiles WHERE contact_id = %s LIMIT 1",
            (contact_id,),
        )
        row = cur.fetchone()
        if not row:
            # 2) Try by name as instagram_username (GHL contact name often = @handle)
            handle = (name or '').lstrip('@').strip()
            if handle:
                cur.execute(
                    "SELECT instagram_username, bio, notes FROM dm_contact_profiles "
                    "WHERE LOWER(instagram_username) = LOWER(%s) AND notes LIKE %s LIMIT 1",
                    (handle, '%"source": "ademir"%'),
                )
                row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        ig, bio, notes = row
        try:
            data = json.loads(notes or '{}')
            if data.get('source') != 'ademir':
                return None
            return {
                'ig_username': ig,
                'bio': bio,
                'dm_inicial': data.get('dm_inicial'),
                'briefing': data.get('briefing') or {},
            }
        except Exception:
            return None
    except Exception as e:
        log(f'get_ademir_context error: {e}')
        return None


# ── Generate response using Claude Agent SDK ──
async def generate_with_claude(name, message, history, available_slots='', booking_context='', contact_id=''):
    bh = is_business_hours()
    now_label = current_brt_label()
    if bh:
        identity = (
            f'AGORA SAO {now_label} (horario comercial: 08:00-18:59 BRT) -> MODO PRIMEIRA_PESSOA. '
            'Voce E o {{DONO_NOME_COMPLETO}} falando diretamente. Fale em primeira pessoa como ele, '
            'use o nome dele se for se apresentar (ex: "Fala fulano! Sou o {{DONO}}..."), '
            'NUNCA mencione que voce e clone, IA, agente ou bot. '
            'A apresentacao acontece SOMENTE na PRIMEIRA mensagem da conversa.'
        )
    else:
        identity = (
            f'AGORA SAO {now_label} (fora do horario comercial: 19:00-07:59 BRT) -> MODO CLONE. '
            'Voce e o Clone do {{DONO}}. Se apresente assim na PRIMEIRA mensagem apenas '
            '(ex: "Fala fulano! Sou o Clone do {{DONO}}..."). '
            'A apresentacao acontece SOMENTE na PRIMEIRA mensagem da conversa.'
        )

    exchange_count = history.count('LEAD:')
    history_clean = history.replace('\n', ' | ')[:2000]
    message_clean = message[:300]

    # Detect if any link was already sent in history
    link_already_sent = 'http' in history.lower() and ('formacao2' in history.lower() or 'mentoring' in history.lower() or '{{DOMINIO_AI}}' in history.lower() or 'crm{{NICHO_DONO_SLUG}}' in history.lower() or 'imersao' in history.lower() or 'hotmart' in history.lower())
    link_warning = 'ATENCAO: JA FOI ENVIADO UM LINK NESTA CONVERSA. NAO ENVIE NENHUM LINK NOVAMENTE. RESPONDA APENAS COM TEXTO, SEM NENHUMA URL.' if link_already_sent else ''

    etapa = ''
    if exchange_count <= 1:
        etapa = 'ETAPA 1 - RAPPORT + QUALIFICACAO INICIAL: Cumprimente, comente algo do contexto. Pergunte: voce ja usa IA de alguma forma no seu negocio? Ja tem resultado real com estrategias digitais?'
    elif exchange_count == 2:
        etapa = 'ETAPA 2 - ENTENDER NIVEL: Baseado na resposta, identifique se a pessoa JA TEM RESULTADO (empresa rodando, faturamento, equipe) ou se e INICIANTE (quer comecar, nao tem estrutura). Se ja tem resultado, siga pra ETAPA 3A. Se e iniciante, siga pra ETAPA 3B.'
    elif exchange_count == 3:
        etapa = 'ETAPA 3A (SE JA TEM RESULTADO): Fale sobre a consultoria estrategica de implementacao de IA. Explique que voces ajudam empresarios que ja faturam a escalar ou multiplicar resultado com ZERO crescimento de time. Agentes de IA substituem equipe operacional. Pergunte se tem interesse em conversar com um especialista do time. ETAPA 3B (SE E INICIANTE): Fale sobre a Formacao {{NICHO_DONO}} em Inteligencia Artificial. E o treinamento completo pra quem quer comecar do zero com IA aplicada a negocios. Mande o link UMA VEZ: https://formacao2.{{DOMINIO_CRM}}'
    else:
        etapa = 'ETAPA 4 - CONVERTER: Se o lead e qualificado (ja tem resultado), ofereca agendar uma reuniao no Zoom com um especialista do time. REGRA CRITICA DE AGENDAMENTO: NUNCA sugira horario com menos de 2 HORAS de antecedencia. NUNCA marque reuniao pra hoje se falta menos de 2h. Sempre sugira para AMANHA ou DEPOIS DE AMANHA em horario comercial (9h-18h). Pergunte: "Qual dia fica melhor pra voce, amanha ou depois de amanha? Manha ou tarde?" NAO confirme horario exato, diga que o time vai confirmar o horario disponivel. Se o lead e iniciante e ainda nao recebeu o link da formacao, mande https://formacao2.{{DOMINIO_CRM}} UMA VEZ. Se ja mandou, responda duvidas normalmente sem repetir o link. NUNCA mencione precos.'

    # Ademir handoff (lead veio de prospecção ativa)
    ademir_block = ''
    try:
        ademir_ctx = get_ademir_context(contact_id, name)
        if ademir_ctx:
            briefing = ademir_ctx.get('briefing') or {}
            posts_summary = ' | '.join(
                [(p.get('caption') or '')[:120] for p in (briefing.get('posts') or [])]
            ) or 'sem posts capturados'
            ademir_block = f"""

ATENCAO: ESSE LEAD VEIO DA PROSPECCAO ATIVA DO ADEMIR.
- Instagram: @{ademir_ctx.get('ig_username')}
- Bio: {ademir_ctx.get('bio') or '?'}
- Oferta principal detectada: {briefing.get('oferta_principal') or briefing.get('external_link') or '?'}
- Posts recentes: {posts_summary}
- DM inicial enviada por mim: "{ademir_ctx.get('dm_inicial')}"

USE ESSE CONTEXTO PARA DAR CONTINUIDADE NATURAL. NAO repita a DM inicial. Reaja a resposta dele com base no conteudo real do perfil.
"""
    except Exception as _e:
        log(f'ademir_block error: {_e}')

    prompt = f"""Voce e um agente de social selling no Instagram DM. {identity}{ademir_block}

FOCO PRINCIPAL AGORA: COMUNIDADE {{NICHO_DONO_UPPER}} (Claude + OpenClaw)
{{DONO_NOME_COMPLETO}}: especialista em IA Generativa, +10.000 mentorados, 45.000 alunos, +500 lancamentos.

CONTEXTO: A COMUNIDADE {{NICHO_DONO_UPPER}} ja esta ABERTA e VENDENDO. E uma comunidade anual com 6 semanas ao vivo toda segunda, aulas semanais continuas, grupo ativo 24h, 3 cursos bonus (Formacao em IA, Propulsor de Vendas, Funil de Ascensao) + call de onboarding individual. Turma de Fundadores: 12x de R$97,94 ou R$947 a vista. Garantia de 7 dias.

PRIORIDADE NUMERO 1: VENDER A COMUNIDADE {{NICHO_DONO_UPPER}}. Quando o lead demonstrar interesse ou disser "eu quero", mandar o pitch completo + link de pagamento Hotmart.
- Link de venda (UNICO valido): https://pay.hotmart.com/D100404088I?off=03ci13t1&utm_source=clone_{{DONO_SLUG}}&utm_medium=instagram_dm&utm_campaign=comunidade_{{NICHO_DONO_SLUG}}
- NUNCA mande link de grupo WhatsApp. NAO existe grupo gratis. A entrada e PAGA via Hotmart.
- NUNCA invente links. O unico link de compra e o Hotmart acima.

Alem da comunidade, mantenha os dois caminhos conforme o perfil do lead:

CAMINHO 1 - LEAD QUALIFICADO (ja tem empresa, ja fatura, ja tem equipe):
- Oferecer: Consultoria estrategica de implementacao de IA
- Proposta: escalar ou multiplicar resultado com ZERO crescimento de time
- Agentes de IA que substituem equipe operacional (SDR, atendimento, conteudo, vendas)
- Se qualificado: oferecer agenda pra reuniao no Zoom com especialista do time
- NUNCA mencione precos da consultoria
- TAMBEM convide pro grupo da comunidade

CAMINHO 2 - LEAD INICIANTE (quer comecar, nao tem estrutura ainda):
- Apresente a {{COMUNIDADE_DONO}}: 6 semanas ao vivo toda segunda + aulas semanais continuas + grupo ativo 24h + 3 cursos bonus + call de onboarding
- Turma de Fundadores: 12x de R$97,94 ou R$947 a vista. Garantia de 7 dias.
- Mande o link de compra: https://pay.hotmart.com/D100404088I?off=03ci13t1&utm_source=clone_{{DONO_SLUG}}&utm_medium=instagram_dm&utm_campaign=comunidade_{{NICHO_DONO_SLUG}}
- Se quiser ir alem: Formacao {{NICHO_DONO}} em IA (link abaixo)

LINKS PERMITIDOS (NUNCA invente outros):
- {{COMUNIDADE_DONO}} (COMPRAR): https://pay.hotmart.com/D100404088I?off=03ci13t1&utm_source=clone_{{DONO_SLUG}}&utm_medium=instagram_dm&utm_campaign=comunidade_{{NICHO_DONO_SLUG}}
- Formacao: https://formacao2.{{DOMINIO_CRM}}
- Mentoria: https://mentoring.{{DOMINIO_AI}}
- Bio: https://{{DOMINIO_AI}}
- CRM: https://{{DOMINIO_CRM}}

REGRAS ABSOLUTAS:
- LEIA O HISTORICO antes de responder
- NUNCA repita o que ja foi dito
- NUNCA se apresente de novo se ja conversou
- NUNCA mencione precos
- NUNCA invente links
- Responda com 1-2 mensagens curtas separadas por |||
- Tom casual direto como empresario
- Cada link so pode ser enviado UMA VEZ na conversa. Se ja aparece no historico, NAO mande de novo.
{link_warning}

{etapa}
{booking_context}

BASE DE CONHECIMENTO - IMERSAO CLAUDE + OPENCLAW (use para responder perguntas sobre Claude, OpenClaw, agentes de IA, o que foi ensinado na imersao, produtos, ecossistema, metodologia):
{IMERSAO_KNOWLEDGE if IMERSAO_KNOWLEDGE else 'Nao disponivel'}

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

# ── Debounce system: accumulate messages per contact, respond once ──
DEBOUNCE_SECONDS = 10
pending_messages = {}  # contact_id -> {'name': str, 'messages': [str], 'timer': asyncio.Task}

async def debounce_fire(contact_id):
    """Called after DEBOUNCE_SECONDS of silence from a contact. Joins all messages and processes."""
    await asyncio.sleep(DEBOUNCE_SECONDS)
    if contact_id not in pending_messages:
        return
    data = pending_messages.pop(contact_id)
    combined = ' '.join(data['messages'])
    log(f'📦 Debounce: {data["name"]} ({len(data["messages"])} msgs) → "{combined[:80]}"')
    await handle_message(contact_id, data['name'], combined)

def debounce_add(contact_id, name, message):
    """Add message to debounce queue. Resets the timer."""
    if contact_id in pending_messages:
        # Cancel existing timer and add message
        pending_messages[contact_id]['timer'].cancel()
        pending_messages[contact_id]['messages'].append(message)
    else:
        pending_messages[contact_id] = {'name': name, 'messages': [message]}
    # Start new timer
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

    # Log raw payload for audio debugging
    msg_data = data.get('message') or {}
    attachments = msg_data.get('attachments', [])
    media_url = msg_data.get('mediaUrl', '') or msg_data.get('media_url', '')

    # If no text but has audio attachment or media URL, transcribe
    if not message and (attachments or media_url):
        audio_url = media_url
        if not audio_url and attachments:
            for att in attachments:
                url = att if isinstance(att, str) else att.get('url', '')
                if any(ext in url.lower() for ext in ['.ogg', '.mp3', '.m4a', '.wav', '.opus', 'audio']):
                    audio_url = url
                    break
            if not audio_url and attachments:
                audio_url = attachments[0] if isinstance(attachments[0], str) else attachments[0].get('url', '')
        if audio_url:
            message = await transcribe_audio(audio_url)
            if message:
                message = f'[audio transcrito] {message}'
        if not message:
            log(f'📎 {name}: attachment sem texto e sem audio transcritivel. Raw: {json.dumps(msg_data)[:300]}')
            return {'success': True}

    if not contact_id or not message:
        return {'success': True}

    # Blocklist check before debounce
    if any(b in name.lower() for b in BLOCKED_NAMES):
        log(f'🚫 Bloqueado: {name} (familia/pessoal)')
        return {'success': True}

    log(f'📩 {name}: "{message[:80]}"')
    save_message(contact_id, name, 'inbound', message)

    # Add to debounce queue (waits 10s for more messages before processing)
    debounce_add(contact_id, name, message)
    return {'success': True}

async def handle_message(contact_id, name, message):
    # message here is already combined from debounce (all messages joined)
    # save_message and blocklist already handled in webhook handler

    # Check outbound message limit (max 15) + cooldown (1h since last outbound)
    try:
        conn = get_db()
        cur = conn.cursor()
        # Count outbound messages to this contact
        cur.execute("SELECT COUNT(*) FROM dm_conversations WHERE contact_id = %s AND direction = 'outbound'", (contact_id,))
        outbound_count = cur.fetchone()[0]
        # Check last outbound time
        cur.execute("SELECT MAX(created_at) FROM dm_conversations WHERE contact_id = %s AND direction = 'outbound'", (contact_id,))
        last_outbound = cur.fetchone()[0]
        cur.close()
        conn.close()

        if outbound_count >= MAX_OUTBOUND_PER_CONTACT:
            # Check if lead has been quiet for 1h+ and is re-initiating (reset allowed)
            if last_outbound:
                from datetime import timezone as tz2
                now_utc = datetime.now(tz2.utc)
                last_utc = last_outbound.astimezone(tz2.utc) if last_outbound.tzinfo else last_outbound.replace(tzinfo=tz2.utc)
                minutes_since = (now_utc - last_utc).total_seconds() / 60
                if minutes_since < COOLDOWN_MINUTES:
                    log(f'⛔ Limite de {MAX_OUTBOUND_PER_CONTACT} msgs atingido pra {name} ({outbound_count} enviadas, última há {minutes_since:.0f}min). Aguardando cooldown de {COOLDOWN_MINUTES}min.')
                    return
                else:
                    log(f'🔄 {name} voltou após {minutes_since:.0f}min de cooldown. Resetando conversa.')
    except Exception as e:
        log(f'Erro ao checar limite: {e}')

    # Detect "Eu quero" from story replies → send Hotmart sale link
    if 'eu quero' in message.lower():
        log(f'🔥 Story reply "Eu quero" detectado de {name}')
        msg1 = f'Fala {name}! Bora! A {{COMUNIDADE_DONO}} e o seguinte: 6 semanas ao vivo comigo toda segunda, onde a gente constroi uma agencia inteira com agentes de IA. Depois das 6 semanas, voce continua com 1 aula ao vivo por semana enquanto for membro + grupo ativo 24h. Ainda leva 3 cursos bonus (Formacao em IA, Propulsor de Vendas e Funil de Ascensao) + uma call individual de onboarding. Turma de Fundadores: 12x de R$97,94 ou R$947 a vista. Garantia de 7 dias. Segue o link: https://pay.hotmart.com/D100404088I?off=03ci13t1&utm_source=clone_{{DONO_SLUG}}&utm_medium=instagram_dm&utm_campaign=comunidade_{{NICHO_DONO_SLUG}}'
        result1 = await send_ig(contact_id, msg1)
        ok1 = bool(result1.get('messageId') or result1.get('id'))
        if ok1:
            save_message(contact_id, name, 'outbound', msg1)
        log(f"{'✅' if ok1 else '❌'} Hotmart sale link sent to {name}")
        with open(EVENTS_FILE, 'a') as f:
            f.write(json.dumps({'ts': datetime.now(BRT).isoformat(), 'type': 'outbound', 'name': name, 'contactId': contact_id, 'message': msg1, 'sent': ok1}) + '\n')
        return

    # Detect "Bombe vídeo ao vivo" trigger from Reels comment automation
    if 'bombe' in message.lower() and ('vivo' in message.lower() or 'video' in message.lower()):
        log(f'🎬 Trigger Live Ads detectado de {name}')
        msg1 = f'Fala {name}! Show, preparei esse passo a passo completo pra voce criar sua campanha de video ao vivo no Meta Ads. Ta tudo explicado aqui, so seguir os passos: https://live-ads.{{DOMINIO_AI}}'
        msg2 = 'Me conta, voce ja trabalha com lives no seu negocio? O que voce vende hoje?'
        result1 = await send_ig(contact_id, msg1)
        ok1 = bool(result1.get('messageId') or result1.get('id'))
        if ok1:
            save_message(contact_id, name, 'outbound', msg1)
        await asyncio.sleep(4)
        result2 = await send_ig(contact_id, msg2)
        ok2 = bool(result2.get('messageId') or result2.get('id'))
        if ok2:
            save_message(contact_id, name, 'outbound', msg2)
        log(f"{'✅' if ok1 and ok2 else '❌'} Live Ads response to {name}")
        with open(EVENTS_FILE, 'a') as f:
            f.write(json.dumps({'ts': datetime.now(BRT).isoformat(), 'type': 'outbound', 'name': name, 'contactId': contact_id, 'message': msg1, 'sent': ok1}) + '\n')
        return

    history = await get_history(contact_id)
    log(f'📚 Historico: {history[:100]}...' if history else '📚 Primeiro contato')

    # Log event
    with open(EVENTS_FILE, 'a') as f:
        f.write(json.dumps({'ts': datetime.now(BRT).isoformat(), 'type': 'inbound', 'name': name, 'contactId': contact_id, 'message': message}) + '\n')

    # Detect phone/email
    phone_match = re.search(r'(?:\+?55\s?)?(?:\(?\d{2}\)?\s?)?\d{4,5}[\s-]?\d{4}', message)
    email_match = re.search(r'[\w.+-]+@[\w-]+\.[\w.]+', message, re.I)
    detected_phone = re.sub(r'\D', '', phone_match.group()) if phone_match else None
    detected_email = email_match.group() if email_match else None

    booking_done = None
    if detected_phone or detected_email:
        log(f'📞 Detectado: phone={detected_phone or "n/a"} email={detected_email or "n/a"}')
        await update_contact_ghl(contact_id, detected_phone, detected_email)

        if detected_phone and detected_email:
            day_words = ['quinta', 'sexta', 'segunda', 'terca', 'quarta', 'hoje', 'amanha']
            if any(w in history.lower() for w in day_words):
                slot_data = await get_available_slots()
                if slot_data and slot_data['slots']:
                    chosen = slot_data['slots'][0]
                    for s in slot_data['slots']:
                        day = s['label'].split(' as ')[0].split('-')[0]
                        if day in history.lower():
                            chosen = s
                            break
                    result = await create_appointment_ghl(contact_id, chosen['iso'], name, detected_phone, detected_email)
                    if result and result.get('head'):
                        booking_done = {'slot': chosen, 'head': result['head']}
                        log(f"✅ Booking completo: {name} → {chosen['label']} com {result['head']['name']}")

    # 8 second human delay
    await asyncio.sleep(8)

    try:
        log('🧠 Claude Opus (SDK) processando...')

        slots_text = ''
        exchange_count = history.count('LEAD:')
        if exchange_count >= 3:
            slot_data = await get_available_slots()
            if slot_data:
                slots_text = slot_data['text']
                log(f'📅 Slots: {slots_text}')

        booking_context = ''
        if booking_done:
            s = booking_done['slot']
            h = booking_done['head']
            booking_context = f"AGENDAMENTO CONFIRMADO: reuniao criada. Dia: {s['label']}. Vendedor: {h['name']}. Confirme: Agendamento confirmado! Sua reuniao sera {s['label']} com {h['name']}. Voce vai receber o convite por email."

        reply = await generate_with_claude(name, message, history, slots_text, booking_context, contact_id=contact_id)

        if not reply:
            log(f'⚠️ Sem resposta pra {name}')
            return

        log(f'🤖 Claude: "{reply[:120]}"')

        msgs = [m.strip() for m in reply.split('|||') if m.strip()]
        # Remove links duplicados: se um link aparece em mais de uma msg, manter só na primeira
        seen_links = set()
        for idx, m in enumerate(msgs):
            links_in_msg = re.findall(r'https?://\S+', m)
            for link in links_in_msg:
                if link in seen_links:
                    msgs[idx] = msgs[idx].replace(link, '').strip()
                else:
                    seen_links.add(link)
        msgs = [m for m in msgs if m]
        for i, msg in enumerate(msgs):
            result = await send_ig(contact_id, msg)
            ok = bool(result.get('messageId') or result.get('id'))
            log(f"{'✅' if ok else '❌'} Msg {i+1}/{len(msgs)} to {name}{'' if ok else ': ' + str(result.get('message',''))[:80]}")
            if ok:
                save_message(contact_id, name, 'outbound', msg)
            with open(EVENTS_FILE, 'a') as f:
                f.write(json.dumps({'ts': datetime.now(BRT).isoformat(), 'type': 'outbound', 'name': name, 'contactId': contact_id, 'message': msg, 'sent': ok}) + '\n')
            if not ok:
                break
            if i < len(msgs) - 1:
                await asyncio.sleep(4)
    except Exception as e:
        log(f'❌ Error: {e}')

@app.post('/send')
async def send(request: Request):
    data = await request.json()
    contact_id = data.get('contactId', '')
    message = data.get('message', '')
    if not contact_id or not message:
        return JSONResponse(status_code=400, content={'error': 'missing params'})
    result = await send_ig(contact_id, message)
    return result

@app.get('/health')
async def health():
    return {'status': 'ok', 'service': 'clone-{{DONO_SLUG}}-sdk', 'ai': 'claude-opus-sdk', 'memory': 'postgresql',
            'mode': '{{DONO_SLUG}}' if is_business_hours() else 'clone', 'now_brt': current_brt_label(),
            'business_hours_window': '08:00-18:59 BRT', 'uptime': int(time.time() - START_TIME)}

@app.get('/stats')
async def stats():
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # Total counts
        cur.execute("SELECT COUNT(*) as c FROM dm_conversations WHERE direction='inbound'")
        inb = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM dm_conversations WHERE direction='outbound'")
        outb = cur.fetchone()['c']
        cur.execute("SELECT COUNT(*) as c FROM dm_contact_profiles")
        contacts = cur.fetchone()['c']

        # Today
        cur.execute("SELECT COUNT(*) as c FROM dm_conversations WHERE direction='inbound' AND created_at >= CURRENT_DATE")
        hoje = cur.fetchone()['c']

        # 7 days
        cur.execute("SELECT COUNT(*) as c FROM dm_conversations WHERE direction='inbound' AND created_at >= CURRENT_DATE - INTERVAL '7 days'")
        d7 = cur.fetchone()['c']

        # 30 days
        cur.execute("SELECT COUNT(*) as c FROM dm_conversations WHERE direction='inbound' AND created_at >= CURRENT_DATE - INTERVAL '30 days'")
        d30 = cur.fetchone()['c']

        # Last 10 conversations (latest message per contact)
        cur.execute("""
            SELECT DISTINCT ON (contact_name) contact_name, direction, message, created_at
            FROM dm_conversations ORDER BY contact_name, created_at DESC LIMIT 10
        """)
        recent = [{'name': r['contact_name'], 'direction': r['direction'], 'message': r['message'][:80], 'ts': str(r['created_at'])} for r in cur.fetchall()]

        # Contacts who responded (had both inbound and outbound)
        cur.execute("""
            SELECT COUNT(DISTINCT contact_id) as c FROM dm_conversations WHERE direction='inbound'
            AND contact_id IN (SELECT DISTINCT contact_id FROM dm_conversations WHERE direction='outbound')
        """)
        responderam = cur.fetchone()['c']

        # Messages by hour (for heatmap)
        cur.execute("""
            SELECT EXTRACT(HOUR FROM created_at) as h, COUNT(*) as c
            FROM dm_conversations WHERE direction='inbound' GROUP BY h ORDER BY h
        """)
        by_hour = {int(r['h']): int(r['c']) for r in cur.fetchall()}

        # Weekly history (last 4 weeks)
        cur.execute("""
            SELECT EXTRACT(WEEK FROM created_at) as w, COUNT(*) as c
            FROM dm_conversations WHERE direction='inbound' AND created_at >= CURRENT_DATE - INTERVAL '28 days'
            GROUP BY w ORDER BY w
        """)
        weekly = [{'week': int(r['w']), 'count': int(r['c'])} for r in cur.fetchall()]

        cur.close(); conn.close()
        return {
            'inbound': inb, 'outbound': outb, 'contacts': contacts,
            'hoje': hoje, 'd7': d7, 'd30': d30,
            'responderam': responderam,
            'recent': recent,
            'by_hour': by_hour,
            'weekly': weekly
        }
    except Exception as e:
        return {'inbound': 0, 'outbound': 0, 'contacts': 0, 'error': str(e)}

@app.get('/history/{contact_id}')
async def history_endpoint(contact_id: str):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute('SELECT direction, message, created_at FROM dm_conversations WHERE contact_id = %s ORDER BY created_at ASC', (contact_id,))
        rows = cur.fetchall()
        cur.close(); conn.close()
        return [{'direction': r['direction'], 'message': r['message'], 'created_at': str(r['created_at'])} for r in rows]
    except Exception as e:
        return JSONResponse(status_code=500, content={'error': str(e)})

START_TIME = time.time()

if __name__ == '__main__':
    log('Clone do {{DONO}} ATIVO (SDK)')
    log(f'IA: Claude Opus via Agent SDK | Memoria: PostgreSQL')
    log(f'Modo: {"{{DONO_UPPER}} 1a PESSOA (horario comercial 08:00-18:59 BRT)" if is_business_hours() else "CLONE DO {{DONO_UPPER}} (fora do horario 19:00-07:59 BRT)"} | Agora: {current_brt_label()}')
    log('Webhook: https://webhook.{{DOMINIO_AI}}/webhook')
    uvicorn.run(app, host='0.0.0.0', port=3500, log_level='warning')
