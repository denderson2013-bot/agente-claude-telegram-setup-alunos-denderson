#!/usr/bin/env python3
"""
Pipeline BMAD de analise de perfil Instagram.

Uso:
  python3 analyze.py @username
  python3 analyze.py username
  python3 analyze.py @username --modo vendas
  python3 analyze.py @username --no-deploy

Variaveis de ambiente esperadas (lidas de /opt/naia-agent/.env automaticamente):
  HIKERAPI_KEY         - chave da HikerAPI
  GEMINI_API_KEY       - chave Google Gemini
  DOMINIO_BASE         - dominio raiz do ALUNO (ex: meunegocio.com.br)
  CLOUDFLARE_DNS_TOKEN - token Cloudflare DNS Edit do aluno
  CLOUDFLARE_ZONE_ID   - zone_id da DOMINIO_BASE no Cloudflare do aluno
  VERCEL_TOKEN         - token Vercel do aluno
  VERCEL_SCOPE         - scope/team Vercel do aluno
  GH_TOKEN             - PAT GitHub do aluno
  GH_OWNER             - user/org GitHub do aluno

Saida:
  /tmp/dossie-USERNAME/index.html
  /tmp/dossie-USERNAME/data.json
  Stdout: JSON com url final + caminho html
"""

import os
import sys
import json
import time
import argparse
import re
import subprocess
from datetime import datetime
from pathlib import Path

try:
    import httpx
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "httpx"])
    import httpx

try:
    import requests
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "requests"])
    import requests


def load_env_file(path):
    """Carrega .env simples (KEY=VALUE) e injeta em os.environ se ainda nao setado."""
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


# Carrega .env do agente do aluno (se existir) antes de qualquer leitura de env
ENV_FILE = os.environ.get("NAIA_ENV_FILE", "/opt/naia-agent/.env")
load_env_file(ENV_FILE)


# ----------------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------------

SKILL_DIR = Path(__file__).resolve().parent.parent
PROMPTS_DIR = SKILL_DIR / "prompts"
TEMPLATE_PATH = SKILL_DIR / "template-dossie.html"

HIKERAPI_BASE = "https://hikerapi.com/v2"
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-exp:generateContent"

OUTPUT_BASE = Path("/tmp")

MAX_RETRIES = 3
RETRY_DELAY = 2

# circuit breaker simples
CB_FAILS = 0
CB_THRESHOLD = 5


# ----------------------------------------------------------------------------
# UTILS
# ----------------------------------------------------------------------------

def log(msg, level="info"):
    icons = {"info": "[i]", "ok": "[OK]", "warn": "[!]", "err": "[X]"}
    print(f"{icons.get(level, '[i]')} {msg}", flush=True)


def clean_username(raw):
    return raw.strip().lstrip("@").lower()


def fmt_number(n):
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def retry(func, *args, **kwargs):
    """Retry com backoff simples."""
    global CB_FAILS
    for attempt in range(MAX_RETRIES):
        try:
            res = func(*args, **kwargs)
            CB_FAILS = 0
            return res
        except Exception as e:
            CB_FAILS += 1
            log(f"Tentativa {attempt + 1}/{MAX_RETRIES} falhou: {e}", "warn")
            if CB_FAILS >= CB_THRESHOLD:
                log("Circuit breaker aberto. Abortando.", "err")
                raise
            time.sleep(RETRY_DELAY * (attempt + 1))
    raise Exception("Max retries excedido")


# ----------------------------------------------------------------------------
# HIKERAPI
# ----------------------------------------------------------------------------

def hikerapi_user_by_username(username, key):
    """Busca dados base do perfil."""
    url = f"{HIKERAPI_BASE}/user/by/username"
    headers = {"x-access-key": key, "accept": "application/json"}
    params = {"username": username}
    r = httpx.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def hikerapi_user_medias(user_id, key, count=12):
    """Busca os 12 ultimos posts."""
    url = f"{HIKERAPI_BASE}/user/medias/chunk"
    headers = {"x-access-key": key, "accept": "application/json"}
    params = {"user_id": user_id, "end_cursor": ""}
    r = httpx.get(url, headers=headers, params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list) and len(data) > 0:
        return data[0][:count] if isinstance(data[0], list) else data[:count]
    return []


def hikerapi_user_clips(user_id, key, count=3):
    """Busca os 3 ultimos reels (opcional)."""
    try:
        url = f"{HIKERAPI_BASE}/user/clips/chunk"
        headers = {"x-access-key": key, "accept": "application/json"}
        params = {"user_id": user_id, "end_cursor": ""}
        r = httpx.get(url, headers=headers, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            return data[0][:count] if isinstance(data[0], list) else data[:count]
        return []
    except Exception as e:
        log(f"Reels nao coletados (opcional): {e}", "warn")
        return []


# ----------------------------------------------------------------------------
# TANDEM (FALLBACK)
# ----------------------------------------------------------------------------

def tandem_fallback(username):
    """Fallback minimo quando HikerAPI esta down ou bloqueada.
    Retorna estrutura compativel com dados mockados, marcando is_fallback=True.
    Em producao real, integraria com PinchTab Browser.
    """
    log("Usando fallback Tandem (dados limitados)", "warn")
    return {
        "user": {
            "username": username,
            "full_name": username,
            "biography": "",
            "follower_count": 0,
            "following_count": 0,
            "media_count": 0,
            "is_verified": False,
            "is_business": False,
            "is_private": False,
            "external_url": "",
            "category": "",
            "public_email": "",
            "profile_pic_url": "",
            "pk": ""
        },
        "posts": [],
        "is_fallback": True
    }


# ----------------------------------------------------------------------------
# GEMINI
# ----------------------------------------------------------------------------

def call_gemini(prompt_text, key, max_tokens=8192):
    url = f"{GEMINI_BASE}?key={key}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": max_tokens,
            "responseMimeType": "application/json"
        }
    }
    r = httpx.post(url, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def load_prompt(name):
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def render_prompt(template, **vars):
    out = template
    for k, v in vars.items():
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False, indent=2)
        out = out.replace("{{" + k + "}}", v)
    return out


# ----------------------------------------------------------------------------
# RENDER HTML
# ----------------------------------------------------------------------------

def render_visao_geral(macro):
    if not macro:
        return "<p class='text-gray-400'>Sem dados.</p>"

    score = macro.get("score_bmad", {})
    forcas = macro.get("forcas", [])
    fraquezas = macro.get("fraquezas", [])
    icp = macro.get("icp", {})
    bm = macro.get("business_model", {})
    pos = macro.get("posicionamento", {})

    forcas_html = "".join([
        f"<div class='glass rounded-2xl p-5'><h4 class='gold font-semibold mb-2'>{f.get('titulo', '')}</h4><p class='text-sm text-gray-300'>{f.get('evidencia', '')}</p></div>"
        for f in forcas
    ])
    fraquezas_html = "".join([
        f"<div class='glass rounded-2xl p-5 border-red-300/20'><h4 class='text-red-300 font-semibold mb-2'>{f.get('titulo', '')}</h4><p class='text-sm text-gray-300'>{f.get('evidencia', '')}</p></div>"
        for f in fraquezas
    ])

    dores = "".join([f"<li class='text-gray-300'>{d}</li>" for d in icp.get("dores_principais", [])])

    score_html = f"""
    <div class='grid grid-cols-2 md:grid-cols-4 gap-4 mb-8'>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-4xl gold font-bold'>{score.get('business_model', 0)}</div>
        <div class='text-xs text-gray-400 uppercase tracking-wider mt-2'>Business Model</div>
        <div class='progress-bar mt-3'><div class='progress-fill' style='width: {score.get('business_model', 0)}%'></div></div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-4xl gold font-bold'>{score.get('marketing', 0)}</div>
        <div class='text-xs text-gray-400 uppercase tracking-wider mt-2'>Marketing</div>
        <div class='progress-bar mt-3'><div class='progress-fill' style='width: {score.get('marketing', 0)}%'></div></div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-4xl gold font-bold'>{score.get('audience', 0)}</div>
        <div class='text-xs text-gray-400 uppercase tracking-wider mt-2'>Audience</div>
        <div class='progress-bar mt-3'><div class='progress-fill' style='width: {score.get('audience', 0)}%'></div></div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-4xl gold font-bold'>{score.get('differentiation', 0)}</div>
        <div class='text-xs text-gray-400 uppercase tracking-wider mt-2'>Differentiation</div>
        <div class='progress-bar mt-3'><div class='progress-fill' style='width: {score.get('differentiation', 0)}%'></div></div>
      </div>
    </div>
    """

    return f"""
    {score_html}
    <div class='glass rounded-2xl p-6 mb-6'>
      <p class='text-base text-gray-200 leading-relaxed'>{macro.get('diagnostico_executivo', '')}</p>
    </div>
    <div class='grid md:grid-cols-2 gap-6 mb-8'>
      <div class='glass rounded-2xl p-6'>
        <h3 class='display text-2xl gold mb-3'>ICP</h3>
        <p class='text-sm text-gray-300 mb-3'>{icp.get('perfil', '')}</p>
        <p class='text-xs text-gray-400 uppercase tracking-wider mb-2'>Dores principais</p>
        <ul class='list-disc list-inside text-sm space-y-1'>{dores}</ul>
        <p class='text-xs text-gray-400 mt-4'>Poder de compra: <span class='gold'>{icp.get('poder_compra_estimado', 'medio')}</span></p>
      </div>
      <div class='glass rounded-2xl p-6'>
        <h3 class='display text-2xl gold mb-3'>Posicionamento</h3>
        <p class='text-sm text-gray-300 mb-3'>{pos.get('promessa_central', '')}</p>
        <p class='text-xs text-gray-400 uppercase tracking-wider mb-2'>Angulo unico</p>
        <p class='text-sm text-gray-300'>{pos.get('angulo_unico', '')}</p>
        <p class='text-xs text-gray-400 mt-4'>Clareza: <span class='gold'>{pos.get('clareza_score', 0)}/100</span></p>
      </div>
    </div>
    <div class='glass rounded-2xl p-6 mb-8'>
      <h3 class='display text-2xl gold mb-4'>Business Model</h3>
      <p class='text-sm text-gray-300 mb-3'>{bm.get('como_monetiza', '')}</p>
      <div class='grid grid-cols-2 md:grid-cols-4 gap-4 text-sm'>
        <div><p class='text-xs text-gray-400 uppercase'>Ticket</p><p class='gold'>{bm.get('ticket_estimado', 'N/D')}</p></div>
        <div><p class='text-xs text-gray-400 uppercase'>Escada de Valor</p><p class='gold'>{'Sim' if bm.get('tem_escada_valor') else 'Nao'}</p></div>
        <div><p class='text-xs text-gray-400 uppercase'>Funil</p><p class='gold'>{'Sim' if bm.get('tem_funil_captura') else 'Nao'}</p></div>
        <div><p class='text-xs text-gray-400 uppercase'>Bio Score</p><p class='gold'>{bm.get('link_bio_qualidade', 0)}/100</p></div>
      </div>
    </div>
    <div class='grid md:grid-cols-2 gap-6'>
      <div>
        <h3 class='display text-2xl gold mb-4'>3 Forcas</h3>
        <div class='space-y-3'>{forcas_html}</div>
      </div>
      <div>
        <h3 class='display text-2xl text-red-300 mb-4'>3 Fraquezas</h3>
        <div class='space-y-3'>{fraquezas_html}</div>
      </div>
    </div>
    """


def render_conteudo(conteudo):
    if not conteudo:
        return "<p class='text-gray-400'>Sem dados.</p>"

    pilares = conteudo.get("pilares_de_conteudo", [])
    padroes = conteudo.get("padroes_virais", [])
    gaps = conteudo.get("gaps_de_conteudo", [])
    fmt = conteudo.get("formato_dominante", {})
    freq = conteudo.get("frequencia_media", {})
    top = conteudo.get("post_top_performance", {})
    pior = conteudo.get("post_pior_performance", {})

    pilares_html = "".join([
        f"<div class='glass rounded-2xl p-5'><h4 class='gold font-semibold mb-2'>{p.get('pilar', '')}</h4><p class='text-xs text-gray-400 mb-2'>Frequencia: {p.get('frequencia', '')}</p></div>"
        for p in pilares
    ])

    padroes_html = "".join([
        f"<div class='glass rounded-2xl p-5'><div class='flex items-start justify-between mb-2'><h4 class='gold font-semibold'>{p.get('padrao', '')}</h4><span class='text-xs px-2 py-1 rounded bg-yellow-300/10 text-yellow-300'>{p.get('replicabilidade', '')}</span></div><p class='text-xs text-gray-400'>{p.get('evidencia', '')}</p></div>"
        for p in padroes
    ])

    gaps_html = "".join([
        f"<div class='glass rounded-2xl p-5 border-red-300/20'><div class='flex items-start justify-between mb-2'><h4 class='text-red-300 font-semibold'>{g.get('gap', '')}</h4><span class='text-xs px-2 py-1 rounded bg-red-300/10 text-red-300'>{g.get('impacto', '')}</span></div></div>"
        for g in gaps
    ])

    return f"""
    <div class='glass rounded-2xl p-6 mb-6'>
      <p class='text-base text-gray-200 leading-relaxed'>{conteudo.get('diagnostico_conteudo', '')}</p>
    </div>
    <div class='grid grid-cols-2 md:grid-cols-4 gap-4 mb-8'>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{fmt.get('porcentagem', 0)}%</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>{fmt.get('tipo', 'reel')}s</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{freq.get('posts_por_semana', 0)}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Posts/Semana</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{freq.get('consistencia_score', 0)}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Consistencia</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{fmt.get('engagement_medio', 0)}%</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Engagement</div>
      </div>
    </div>
    <div class='mb-8'>
      <h3 class='display text-2xl gold mb-4'>Pilares de Conteudo</h3>
      <div class='grid md:grid-cols-2 lg:grid-cols-3 gap-4'>{pilares_html}</div>
    </div>
    <div class='grid md:grid-cols-2 gap-6 mb-8'>
      <div class='glass rounded-2xl p-6 border-yellow-300/30'>
        <p class='text-xs text-gray-400 uppercase mb-2'>Post de Maior Performance</p>
        <p class='text-sm text-gray-200 mb-3'>{top.get('caption_resumo', '')}</p>
        <p class='text-xs text-yellow-300 mb-2'>Engagement: {top.get('engagement_rate', 0)}%</p>
        <p class='text-xs text-gray-400'>{top.get('por_que_funcionou', '')}</p>
      </div>
      <div class='glass rounded-2xl p-6 border-red-300/30'>
        <p class='text-xs text-gray-400 uppercase mb-2'>Post de Menor Performance</p>
        <p class='text-sm text-gray-200 mb-3'>{pior.get('caption_resumo', '')}</p>
        <p class='text-xs text-red-300 mb-2'>Engagement: {pior.get('engagement_rate', 0)}%</p>
        <p class='text-xs text-gray-400'>{pior.get('por_que_falhou', '')}</p>
      </div>
    </div>
    <div class='mb-8'>
      <h3 class='display text-2xl gold mb-4'>Padroes Virais Identificados</h3>
      <div class='space-y-3'>{padroes_html}</div>
    </div>
    <div>
      <h3 class='display text-2xl text-red-300 mb-4'>Gaps de Conteudo</h3>
      <div class='grid md:grid-cols-2 gap-3'>{gaps_html}</div>
    </div>
    """


def render_estrategia(plano):
    if not plano:
        return "<p class='text-gray-400'>Sem dados.</p>"

    meta = plano.get("meta_principal", {})
    cal = plano.get("calendario_30_dias", [])
    hacks = plano.get("growth_hacks_semanais", [])
    oportunidades = plano.get("oportunidades_nao_exploradas", [])
    previsao = plano.get("previsao_resultado", {})
    stories = plano.get("estrategia_stories", {})
    trafego = plano.get("estrategia_trafego", {})

    cal_html = "".join([
        f"""<div class='day-card glass rounded-2xl p-5 border border-blue-300/10'>
          <div class='flex items-start justify-between mb-3'>
            <div>
              <p class='text-xs text-gray-400 uppercase'>{c.get('data_relativa', f"Dia {c.get('dia', '?')}")}</p>
              <h4 class='gold font-semibold mt-1'>{c.get('formato', '')} | {c.get('pilar', '')}</h4>
            </div>
            <span class='text-xs text-gray-400'>{c.get('horario_recomendado', '')}</span>
          </div>
          <p class='text-sm text-gray-200 font-medium mb-2'>{c.get('hook', '')}</p>
          <p class='text-xs text-gray-400 mb-3 leading-relaxed'>{c.get('descricao', '')}</p>
          <p class='text-xs text-yellow-300 mb-2'>CTA: {c.get('cta', '')}</p>
          <p class='text-xs text-gray-500'>{' '.join(c.get('hashtags_sugeridas', []))}</p>
        </div>"""
        for c in cal
    ])

    hacks_html = "".join([
        f"<div class='glass rounded-2xl p-5'><p class='text-xs text-gray-400 uppercase mb-2'>Semana {h.get('semana', '')}</p><h4 class='gold font-semibold mb-2'>{h.get('hack', '')}</h4><p class='text-xs text-gray-400'>{h.get('como', '')}</p></div>"
        for h in hacks
    ])

    op_html = "".join([
        f"<div class='glass rounded-2xl p-5'><div class='flex items-start justify-between mb-2'><h4 class='gold font-semibold'>{o.get('oportunidade', '')}</h4><span class='text-xs px-2 py-1 rounded bg-yellow-300/10 text-yellow-300'>{o.get('impacto_estimado', '')}/{o.get('esforco', '')}</span></div><p class='text-xs text-gray-400'>{o.get('como_executar', '')}</p></div>"
        for o in oportunidades
    ])

    return f"""
    <div class='glass rounded-2xl p-6 mb-6 border-yellow-300/30'>
      <p class='text-xs text-gray-400 uppercase mb-2'>Meta Principal ({meta.get('tipo', 'crescimento')})</p>
      <h3 class='display text-3xl gold mb-2'>{meta.get('valor_numerico', '')}</h3>
      <p class='text-sm text-gray-300'>{plano.get('resumo_executivo', '')}</p>
    </div>
    <div class='grid grid-cols-2 md:grid-cols-4 gap-4 mb-8'>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{fmt_number(previsao.get('follower_count_final_estimado', 0))}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Seguidores Meta</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>+{fmt_number(previsao.get('novos_seguidores', 0))}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Novos Seg.</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>{fmt_number(previsao.get('conversao_estimada_lead_magnets', 0))}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Leads Estim.</div>
      </div>
      <div class='stat-card glass rounded-2xl p-5 text-center'>
        <div class='display text-3xl gold font-bold'>R$ {fmt_number(previsao.get('vendas_estimadas_brl', 0))}</div>
        <div class='text-xs text-gray-400 uppercase mt-2'>Vendas Estim.</div>
      </div>
    </div>
    <div class='mb-8'>
      <h3 class='display text-2xl gold mb-4'>Oportunidades Nao Exploradas</h3>
      <div class='grid md:grid-cols-2 gap-4'>{op_html}</div>
    </div>
    <div class='mb-8'>
      <h3 class='display text-2xl gold mb-4'>Growth Hacks Semanais</h3>
      <div class='grid md:grid-cols-2 lg:grid-cols-4 gap-4'>{hacks_html}</div>
    </div>
    <div class='mb-8'>
      <h3 class='display text-2xl gold mb-4'>Calendario de 30 Dias</h3>
      <div class='grid md:grid-cols-2 lg:grid-cols-3 gap-4'>{cal_html}</div>
    </div>
    <div class='grid md:grid-cols-2 gap-6'>
      <div class='glass rounded-2xl p-6'>
        <h3 class='display text-2xl gold mb-3'>Estrategia de Stories</h3>
        <p class='text-sm text-gray-300 mb-3'>{stories.get('narrativa_30_dias', '')}</p>
        <p class='text-xs text-gray-400'>Frequencia: {stories.get('frequencia_diaria', 0)}/dia</p>
      </div>
      <div class='glass rounded-2xl p-6'>
        <h3 class='display text-2xl gold mb-3'>Trafego Pago</h3>
        <p class='text-sm text-gray-300 mb-2'>Tipo: {trafego.get('tipo', 'organico')}</p>
        <p class='text-xs text-gray-400'>Investimento sugerido: R$ {trafego.get('investimento_sugerido_brl', 0)}/mes</p>
      </div>
    </div>
    """


def render_dados_brutos(posts):
    if not posts:
        return "<p class='text-gray-400'>Sem posts coletados.</p>"

    cards = "".join([
        f"""<div class='glass rounded-2xl p-5'>
          <p class='text-xs text-gray-400 uppercase mb-2'>{p.get('media_type', 'post')} | {p.get('taken_at', '')[:10] if p.get('taken_at') else ''}</p>
          <p class='text-sm text-gray-200 leading-relaxed mb-3'>{(p.get('caption', '') or '')[:240]}{'...' if len(p.get('caption', '') or '') > 240 else ''}</p>
          <div class='flex items-center gap-4 text-xs'>
            <span class='gold'>{fmt_number(p.get('like_count', 0))} likes</span>
            <span class='text-blue-200/70'>{fmt_number(p.get('comment_count', 0))} comments</span>
          </div>
        </div>"""
        for p in posts
    ])

    return f"""
    <p class='text-sm text-gray-400 mb-6'>Dados coletados via HikerAPI. {len(posts)} posts analisados.</p>
    <div class='grid md:grid-cols-2 gap-4'>{cards}</div>
    """


def render_html(template_html, ctx):
    out = template_html
    for k, v in ctx.items():
        out = out.replace("{{" + k + "}}", str(v) if v is not None else "")
    return out


# ----------------------------------------------------------------------------
# PIPELINE
# ----------------------------------------------------------------------------

def run_pipeline(username, modo="crescimento", do_deploy=True):
    log(f"Iniciando pipeline BMAD para @{username} (modo={modo})")

    hikerapi_key = os.getenv("HIKERAPI_KEY", "{{HIKERAPI_KEY}}")
    gemini_key = os.getenv("GEMINI_API_KEY", "{{GEMINI_API_KEY}}")

    if hikerapi_key.startswith("{{") or gemini_key.startswith("{{"):
        log("HIKERAPI_KEY ou GEMINI_API_KEY nao configuradas. Use env vars.", "err")
        sys.exit(1)

    # 1. CRAWL
    log("Etapa 1: Coletando dados via HikerAPI")
    try:
        user = retry(hikerapi_user_by_username, username, hikerapi_key)
        if isinstance(user, dict) and "user" in user:
            user_obj = user.get("user", user)
        else:
            user_obj = user
    except Exception as e:
        log(f"HikerAPI falhou, ativando fallback: {e}", "warn")
        fb = tandem_fallback(username)
        user_obj = fb["user"]

    if user_obj.get("is_private"):
        log("Perfil privado. Abortando.", "err")
        sys.exit(2)

    user_id = user_obj.get("pk") or user_obj.get("id")
    posts = []
    if user_id:
        try:
            log("Etapa 2: Coletando 12 ultimos posts")
            posts = retry(hikerapi_user_medias, user_id, hikerapi_key, 12)
        except Exception as e:
            log(f"Erro ao coletar posts: {e}", "warn")

    follower_count = user_obj.get("follower_count", 0)

    # Calcular engagement_rate por post
    for p in posts:
        if follower_count > 0:
            er = (p.get("like_count", 0) + p.get("comment_count", 0)) / follower_count * 100
            p["engagement_rate"] = round(er, 2)
        else:
            p["engagement_rate"] = 0

    avg_engagement = round(sum(p.get("engagement_rate", 0) for p in posts) / max(len(posts), 1), 2)

    # 2. GEMINI 1: VISAO MACRO
    log("Etapa 3: Analise Gemini - Visao Macro (BMAD)")
    perfil_data = {
        "username": user_obj.get("username", username),
        "full_name": user_obj.get("full_name", ""),
        "bio": user_obj.get("biography", ""),
        "external_url": user_obj.get("external_url", ""),
        "category": user_obj.get("category", ""),
        "public_email": user_obj.get("public_email", ""),
        "follower_count": follower_count,
        "following_count": user_obj.get("following_count", 0),
        "media_count": user_obj.get("media_count", 0),
        "is_verified": user_obj.get("is_verified", False),
        "is_business": user_obj.get("is_business", False),
        "is_private": user_obj.get("is_private", False),
        "posts": posts[:12]
    }

    prompt1_tmpl = load_prompt("01-visao-macro.md")
    prompt1 = render_prompt(prompt1_tmpl, PERFIL_JSON=perfil_data)
    try:
        macro = retry(call_gemini, prompt1, gemini_key)
    except Exception as e:
        log(f"Gemini visao macro falhou: {e}", "err")
        macro = {}

    # 3. GEMINI 2: CONTEUDO
    log("Etapa 4: Analise Gemini - Conteudo")
    prompt2_tmpl = load_prompt("02-conteudo.md")
    prompt2 = render_prompt(prompt2_tmpl, POSTS_JSON=posts, FOLLOWER_COUNT=str(follower_count))
    try:
        conteudo = retry(call_gemini, prompt2, gemini_key)
    except Exception as e:
        log(f"Gemini conteudo falhou: {e}", "err")
        conteudo = {}

    # 4. GEMINI 3: ESTRATEGIA 30 DIAS
    log("Etapa 5: Analise Gemini - Estrategia 30 Dias")
    prompt3_tmpl = load_prompt("03-estrategia-30-dias.md")
    prompt3 = render_prompt(prompt3_tmpl,
                            PERFIL_JSON=perfil_data,
                            ANALISE_MACRO_JSON=macro,
                            ANALISE_CONTEUDO_JSON=conteudo,
                            FOLLOWER_COUNT=str(follower_count),
                            MODO=modo)
    try:
        plano = retry(call_gemini, prompt3, gemini_key)
    except Exception as e:
        log(f"Gemini estrategia falhou: {e}", "err")
        plano = {}

    # 5. RENDER HTML
    log("Etapa 6: Renderizando HTML")
    template_html = TEMPLATE_PATH.read_text(encoding="utf-8")

    verified_badge = '<span class="text-blue-400 text-2xl" title="Verificado">checkmark</span>' if user_obj.get("is_verified") else ""

    ctx = {
        "USERNAME": username,
        "FULL_NAME": user_obj.get("full_name", username) or username,
        "BIO": (user_obj.get("biography", "") or "").replace("\n", "<br>"),
        "AVATAR_URL": user_obj.get("profile_pic_url_hd") or user_obj.get("profile_pic_url", "") or "",
        "FOLLOWERS": fmt_number(follower_count),
        "FOLLOWING": fmt_number(user_obj.get("following_count", 0)),
        "POSTS_COUNT": fmt_number(user_obj.get("media_count", 0)),
        "ENGAGEMENT_RATE": str(avg_engagement),
        "VERIFIED_BADGE": verified_badge,
        "GERADO_EM": datetime.now().strftime("%d/%m/%Y as %H:%M"),
        "ANALISE_VISAO_GERAL": render_visao_geral(macro),
        "ANALISE_CONTEUDO": render_conteudo(conteudo),
        "PLANO_30_DIAS": render_estrategia(plano),
        "POSTS_DATA": render_dados_brutos(posts)
    }

    html_final = render_html(template_html, ctx)

    out_dir = OUTPUT_BASE / f"dossie-{username}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "index.html"
    out_file.write_text(html_final, encoding="utf-8")

    # Salva tambem o JSON cru pra debug / reuso
    debug = {
        "perfil": perfil_data,
        "analise_macro": macro,
        "analise_conteudo": conteudo,
        "plano_30_dias": plano,
        "gerado_em": datetime.now().isoformat()
    }
    (out_dir / "data.json").write_text(json.dumps(debug, ensure_ascii=False, indent=2), encoding="utf-8")

    log(f"HTML gerado em {out_file}", "ok")

    # 6. DEPLOY
    final_url = f"file://{out_file}"
    dominio_base = os.getenv("DOMINIO_BASE", "")

    if do_deploy:
        if not dominio_base:
            log("Falta configurar DOMINIO_BASE no .env do agente. Te explico como.", "warn")
            log("1) Edita /opt/naia-agent/.env", "warn")
            log("2) Adiciona: DOMINIO_BASE=seudominio.com.br", "warn")
            log("3) Tambem cadastra: CLOUDFLARE_DNS_TOKEN, CLOUDFLARE_ZONE_ID, VERCEL_TOKEN, VERCEL_SCOPE, GH_TOKEN, GH_OWNER", "warn")
            log("Ver passo a passo em PLAYBOOK-BMAD.md secao 'Como o aluno configura o dominio dele'", "warn")
            log("HTML local segue disponivel: " + str(out_file), "warn")
        else:
            log("Etapa 7: Deploy via deploy-dossie.sh")
            deploy_script = SKILL_DIR / "scripts" / "deploy-dossie.sh"
            if deploy_script.exists():
                try:
                    res = subprocess.run(
                        ["bash", str(deploy_script), username, str(out_dir)],
                        capture_output=False, text=True, timeout=600
                    )
                    if res.returncode == 0:
                        final_url = f"https://{username}.{dominio_base}"
                        log(f"Deploy ok: {final_url}", "ok")
                    else:
                        log(f"Deploy retornou codigo {res.returncode}", "warn")
                except Exception as e:
                    log(f"Deploy nao executou: {e}", "warn")

    log("=" * 60)
    log(f"DOSSIE PRONTO: {final_url}")
    log("=" * 60)

    return {
        "username": username,
        "html_path": str(out_file),
        "url": final_url,
        "macro": macro,
        "conteudo": conteudo,
        "plano": plano
    }


# ----------------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Pipeline BMAD de analise Instagram")
    parser.add_argument("username", help="@username (com ou sem @)")
    parser.add_argument("--modo", choices=["crescimento", "vendas"], default="crescimento")
    parser.add_argument("--no-deploy", action="store_true", help="So gera HTML, nao faz deploy")
    args = parser.parse_args()

    username = clean_username(args.username)
    if not re.match(r"^[a-z0-9._]{1,30}$", username):
        log(f"Username invalido: {username}", "err")
        sys.exit(1)

    res = run_pipeline(username, modo=args.modo, do_deploy=not args.no_deploy)
    print(json.dumps({"url": res["url"], "html_path": res["html_path"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
