#!/usr/bin/env python3
"""
Instagram Profile Analyzer — Metodologia BMAD (MIT)
B: Brand  |  M: Market  |  A: Audit  |  D: Direction
Gemini 3.1 Pro Preview + Whisper + Deploy Automático
Uso: python3 analyze.py USERNAME
"""

import sys
import os
import json
import subprocess
import requests
import base64
import re
import time
import glob
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

# ============================================================
# CREDENCIAIS
# ============================================================
OPENAI_API_KEY   = os.getenv("OPENAI_API_KEY", "{{OPENAI_API_KEY}}")
GOOGLE_AI_KEY    = os.getenv("GOOGLE_AI_KEY", "{{GOOGLE_AI_KEY}}")
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN", "{{GITHUB_TOKEN}}")
VERCEL_TOKEN     = os.getenv("VERCEL_TOKEN", "{{VERCEL_TOKEN}}")
VERCEL_ORG_ID    = os.getenv("VERCEL_ORG_ID", "{{VERCEL_ORG_ID}}")
CF_TOKEN         = os.getenv("CF_TOKEN", "{{CLOUDFLARE_TOKEN}}")
CF_ZONE_ID       = os.getenv("CF_ZONE_ID", "{{CLOUDFLARE_ZONE_ID}}")
INSTA_SESSION    = os.getenv("INSTA_SESSION", "{{INSTAGRAM_SESSION_USER}}")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-pro")

# ============================================================

def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

def run(cmd, capture=False, cwd=None):
    log(f"$ {cmd}")
    if capture:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)
        return r.stdout.strip()
    else:
        subprocess.run(cmd, shell=True, check=False, cwd=cwd)

def coletar_perfil(username, tmpdir):
    """Coleta dados do perfil via instaloader Python API (rapido)"""
    log(f"Coletando perfil @{username}...")
    profile_data = {
        "username": username, "followers": 0, "following": 0, "posts_count": 0,
        "bio": "", "is_verified": False, "is_business": False, "recent_posts": []
    }
    try:
        import instaloader
        L = instaloader.Instaloader(download_pictures=False, download_videos=False,
                                    download_video_thumbnails=False, download_geotags=False,
                                    download_comments=False, save_metadata=False, quiet=True)
        session_file = f"/root/.config/instaloader/session-{INSTA_SESSION}"
        if os.path.exists(session_file):
            L.load_session_from_file(INSTA_SESSION, session_file)
        profile = instaloader.Profile.from_username(L.context, username)
        profile_data["followers"] = profile.followers
        profile_data["following"] = profile.followees
        profile_data["posts_count"] = profile.mediacount
        profile_data["bio"] = profile.biography or ""
        profile_data["is_verified"] = profile.is_verified
        profile_data["is_business"] = profile.is_business_account
        count = 0
        for post in profile.get_posts():
            if count >= 12:
                break
            profile_data["recent_posts"].append({
                "shortcode": post.shortcode,
                "likes": post.likes,
                "comments": post.comments,
                "is_video": post.is_video,
                "views": post.video_view_count if post.is_video else 0,
                "timestamp": int(post.date_utc.timestamp()) if post.date_utc else 0
            })
            count += 1
    except Exception as ex:
        log(f"instaloader Python API: {ex}")
    log(f"Perfil coletado: {profile_data['followers']:,} seguidores, {profile_data['posts_count']} posts")
    return profile_data

def baixar_reels(username, tmpdir, max_reels=5):
    log(f"Baixando reels de @{username} via instaloader Python API...")
    reels_dir = f"{tmpdir}/reels"
    os.makedirs(reels_dir, exist_ok=True)

    try:
        import instaloader as IL
        L = IL.Instaloader(
            dirname_pattern=reels_dir,
            filename_pattern="{shortcode}",
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
        )
        L.load_session_from_file(INSTA_SESSION)
        profile = IL.Profile.from_username(L.context, username)

        count = 0
        for post in profile.get_posts():
            if count >= max_reels:
                break
            if post.is_video and post.typename == "GraphVideo":
                try:
                    L.download_post(post, target=reels_dir)
                    count += 1
                    log(f"  Reel {count}/{max_reels}: {post.shortcode}")
                except Exception as ex:
                    log(f"  Erro ao baixar reel {post.shortcode}: {ex}")
    except Exception as ex:
        log(f"Erro baixando reels: {ex}")

    videos = glob.glob(f"{reels_dir}/*.mp4") + glob.glob(f"{reels_dir}/**/*.mp4")
    log(f"{len(videos)} reels baixados")
    return videos[:max_reels]

def extrair_frames_audio(video_path, tmpdir):
    vid_id = Path(video_path).stem
    frames_dir = f"{tmpdir}/frames/{vid_id}"
    os.makedirs(frames_dir, exist_ok=True)
    audio_path = f"{tmpdir}/audio/{vid_id}.mp3"
    os.makedirs(f"{tmpdir}/audio", exist_ok=True)

    run(f'ffprobe -v quiet -print_format json -show_streams "{video_path}" > {tmpdir}/probe_{vid_id}.json')
    try:
        with open(f"{tmpdir}/probe_{vid_id}.json") as f:
            probe = json.load(f)
        duration = float(next((s.get("duration", "30") for s in probe.get("streams", []) if s.get("codec_type") == "video"), "30"))
    except:
        duration = 30.0

    for i, pct in enumerate([0.1, 0.5, 0.8]):
        t = duration * pct
        run(f'ffmpeg -ss {t:.1f} -i "{video_path}" -vframes 1 -q:v 2 "{frames_dir}/frame_{i}.jpg" -y -loglevel quiet')

    run(f'ffmpeg -i "{video_path}" -vn -ar 16000 -ac 1 -b:a 64k "{audio_path}" -y -loglevel quiet')
    frames = sorted(glob.glob(f"{frames_dir}/*.jpg"))
    return frames, audio_path if os.path.exists(audio_path) else None

def transcrever_audio(audio_path):
    if not audio_path or not os.path.exists(audio_path):
        return ""
    if os.path.getsize(audio_path) < 1000:
        return ""
    log(f"Transcrevendo {Path(audio_path).name}...")
    try:
        with open(audio_path, "rb") as f:
            r = requests.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={"file": (Path(audio_path).name, f, "audio/mpeg")},
                data={"model": "whisper-1", "language": "pt", "response_format": "text"},
                timeout=60
            )
        return r.text.strip() if r.ok else ""
    except Exception as ex:
        log(f"Whisper falhou: {ex}")
        return ""

def calcular_score_compra(profile_data):
    """Score de autenticidade de seguidores (0=orgânico, 100=comprado)"""
    score = 0
    motivos = []
    followers = profile_data.get("followers", 0)
    following = profile_data.get("following", 0)
    posts = profile_data.get("recent_posts", [])

    if not posts or followers == 0:
        return {"score": 0, "veredicto": "INCONCLUSIVO", "confianca": 0, "motivos": ["Dados insuficientes"]}

    likes_list = [p["likes"] for p in posts if p["likes"] > 0]
    if likes_list:
        avg_likes = sum(likes_list) / len(likes_list)
        eng_rate = (avg_likes / followers) * 100

        if followers < 10000:
            expected_min, expected_max = 4.0, 8.0
        elif followers < 100000:
            expected_min, expected_max = 2.0, 4.0
        elif followers < 1000000:
            expected_min, expected_max = 1.0, 2.0
        else:
            expected_min, expected_max = 0.5, 1.0

        if eng_rate < expected_min * 0.3:
            score += min(40, int((expected_min * 0.3 - eng_rate) / expected_min * 100))
            motivos.append(f"Engajamento muito baixo: {eng_rate:.2f}% (esperado {expected_min:.1f}%-{expected_max:.1f}%)")
        elif eng_rate < expected_min * 0.6:
            score += min(20, int((expected_min * 0.6 - eng_rate) / expected_min * 60))
            motivos.append(f"Engajamento abaixo do esperado: {eng_rate:.2f}%")

    if len(likes_list) >= 4:
        import statistics
        mean_likes = statistics.mean(likes_list)
        if mean_likes > 0:
            cv = statistics.stdev(likes_list) / mean_likes
            if cv > 2.5:
                score += 25
                motivos.append(f"Curtidas muito inconsistentes (CV={cv:.1f}) — possíveis picos artificiais")
            elif cv > 1.5:
                score += 10
                motivos.append(f"Curtidas inconsistentes (CV={cv:.1f})")

    if following > 0 and followers > 0:
        ratio = followers / following
        if ratio > 50 and followers > 50000:
            score += 10
            motivos.append(f"Proporção seguidores/seguindo alta: {ratio:.0f}x")

    if len(likes_list) > 0:
        avg_likes_per_post = sum(likes_list) / len(likes_list)
        expected_per_post = followers * 0.01
        if avg_likes_per_post < expected_per_post * 0.2 and followers > 10000:
            score += 15
            motivos.append(f"Média de curtidas muito baixa vs seguidores ({avg_likes_per_post:.0f} vs {expected_per_post:.0f} esperado)")

    score = min(100, score)

    if score <= 30:
        veredicto, confianca = "LIMPO", 100 - score
    elif score <= 60:
        veredicto, confianca = "SUSPEITO", score
    else:
        veredicto, confianca = "ALTA PROBABILIDADE", score

    if not motivos:
        motivos = ["Perfil dentro dos padrões orgânicos normais"]

    return {"score": score, "veredicto": veredicto, "confianca": confianca, "motivos": motivos}

def analisar_com_gemini_bmad(profile_data, transcricoes, frames_b64, compra_score):
    """Análise completa com Gemini 3.1 Pro Preview usando metodologia BMAD do MIT"""
    log(f"Analisando com {GEMINI_MODEL} — Metodologia BMAD...")

    username = profile_data["username"]
    followers = profile_data["followers"]
    following = profile_data["following"]
    posts_count = profile_data["posts_count"]
    bio = profile_data["bio"]
    is_verified = profile_data["is_verified"]
    posts = profile_data["recent_posts"]

    likes_list = [p["likes"] for p in posts if p["likes"] > 0]
    avg_likes = sum(likes_list) / len(likes_list) if likes_list else 0
    eng_rate = (avg_likes / followers * 100) if followers > 0 else 0

    posts_info = "\n".join([
        f"Post {i+1}: {p['likes']} likes, {p['comments']} comentários" +
        (f", {p['views']} views (vídeo)" if p.get('is_video') else "")
        for i, p in enumerate(posts[:8])
    ])

    transcricoes_str = ""
    for i, t in enumerate(transcricoes[:5]):
        if t.get("texto"):
            transcricoes_str += f"\nReel {i+1}: {t['texto'][:500]}\n"

    compra_info = f"Score: {compra_score['score']}/100 | Veredicto: {compra_score['veredicto']} | Indicadores: {'; '.join(compra_score['motivos'])}"

    prompt = f"""Você é um estrategista sênior especialista em crescimento digital, usando a METODOLOGIA BMAD do MIT para análise de perfis do Instagram.

BMAD = Brand (Marca) + Market (Mercado) + Audit (Auditoria) + Direction (Direção)

Esta é a abordagem mais robusta e científica para diagnóstico e estratégia de crescimento digital.

## DADOS DO PERFIL @{username}
- Seguidores: {followers:,} | Seguindo: {following:,} | Posts totais: {posts_count}
- Bio: {bio or 'Não disponível'} | Verificado: {is_verified}
- Média de curtidas: {avg_likes:.0f} | Taxa de engajamento: {eng_rate:.2f}%

## POSTS RECENTES
{posts_info}

## TRANSCRIÇÕES DOS REELS
{transcricoes_str or 'Transcrições não disponíveis.'}

## AUTENTICIDADE DOS SEGUIDORES
{compra_info}

---

Aplique a metodologia BMAD e retorne um JSON válido (sem markdown, só JSON puro) com EXATAMENTE esta estrutura:

{{
  "bmad_brand": {{
    "identidade_marca": "Como a marca se posiciona, qual é sua proposta de valor única",
    "arquetipo": "Qual arquétipo de marca representa este perfil (Herói/Sábio/Criador/etc)",
    "pilares_comunicacao": ["pilar 1", "pilar 2", "pilar 3"],
    "tom_de_voz": "Como o criador se comunica — tom, linguagem, estilo",
    "consistencia_visual": "Alta/Média/Baixa — e por quê",
    "diferencial_competitivo": "O que torna este perfil único vs concorrência",
    "gaps_de_marca": ["gap 1", "gap 2", "gap 3"],
    "score_marca": 0
  }},

  "bmad_market": {{
    "nicho_principal": "Nicho de mercado identificado",
    "sub_nicho": "Micro-nicho específico",
    "tamanho_mercado": "Estimativa do tamanho do mercado deste nicho no Brasil",
    "concorrencia": "Alta/Média/Baixa — análise do nível de concorrência",
    "posicionamento_mercado": "Como o perfil se posiciona vs mercado — líder/seguidor/nicho",
    "tendencias_relevantes": ["tendência 1", "tendência 2", "tendência 3"],
    "oportunidades_mercado": ["oportunidade 1", "oportunidade 2", "oportunidade 3"],
    "benchmarks_nicho": {{
      "taxa_engajamento_ideal": "X% para este nicho e tamanho",
      "frequencia_posts_ideal": "X posts por semana",
      "formatos_dominantes": ["formato 1", "formato 2"],
      "horarios_pico": "Melhores horários para este nicho"
    }},
    "score_mercado": 0
  }},

  "bmad_audit": {{
    "performance_geral": "Excelente/Bom/Regular/Fraco",
    "taxa_engajamento_atual": "{eng_rate:.2f}%",
    "avaliacao_engajamento": "Como este engajamento se compara ao benchmark do nicho",
    "autenticidade": {{
      "veredicto": "{compra_score['veredicto']}",
      "score": {compra_score['score']},
      "explicacao": "Interpretação detalhada dos dados de autenticidade",
      "indicadores": {json.dumps(compra_score['motivos'], ensure_ascii=False)}
    }},
    "auditoria_conteudo": {{
      "tipos_conteudo": ["tipo 1", "tipo 2"],
      "qualidade_producao": "Alta/Média/Baixa",
      "frequencia_atual": "Estimativa de frequência de postagem",
      "consistencia_temas": "Alta/Média/Baixa"
    }},
    "gargalos_crescimento": ["gargalo 1", "gargalo 2", "gargalo 3"],
    "pontos_fortes": ["força 1", "força 2", "força 3"],
    "pontos_fracos": ["fraqueza 1", "fraqueza 2", "fraqueza 3"],
    "score_auditoria": 0
  }},

  "bmad_direction": {{
    "estrategia_central": "A grande estratégia recomendada em 2-3 frases",
    "quick_wins": [
      {{
        "acao": "Ação imediata que pode ser feita hoje",
        "impacto": "Impacto esperado",
        "como": "Passo a passo de como fazer"
      }}
    ],
    "plano_90_dias": {{
      "fase_1_30_dias": {{
        "objetivo": "Objetivo desta fase",
        "acoes": ["ação 1", "ação 2", "ação 3", "ação 4", "ação 5"],
        "kpis": ["KPI 1", "KPI 2"],
        "meta_seguidores": "Meta de seguidores ao final dos 30 dias"
      }},
      "fase_2_60_dias": {{
        "objetivo": "Objetivo desta fase",
        "acoes": ["ação 1", "ação 2", "ação 3", "ação 4", "ação 5"],
        "kpis": ["KPI 1", "KPI 2"],
        "meta_seguidores": "Meta de seguidores ao final dos 60 dias"
      }},
      "fase_3_90_dias": {{
        "objetivo": "Objetivo desta fase",
        "acoes": ["ação 1", "ação 2", "ação 3", "ação 4", "ação 5"],
        "kpis": ["KPI 1", "KPI 2"],
        "meta_seguidores": "Meta de seguidores ao final dos 90 dias"
      }}
    }},
    "estrategia_conteudo": {{
      "pilares_conteudo": ["pilar 1", "pilar 2", "pilar 3"],
      "mix_formatos": {{"Reels": "X%", "Carrossel": "X%", "Stories": "X%", "Feed": "X%"}},
      "temas_semana": ["seg: tema", "ter: tema", "qua: tema", "qui: tema", "sex: tema"],
      "hooks_recomendados": ["hook 1", "hook 2", "hook 3"]
    }},
    "monetizacao": {{
      "potencial_atual": "Alto/Médio/Baixo",
      "modelo_primario": "Principal forma de monetização recomendada",
      "modelo_secundario": "Monetização complementar",
      "produto_ideal": "Qual produto/serviço funciona melhor para este perfil",
      "faixa_preco": "Faixa de preço ideal para o nicho",
      "ticket_medio_estimado": "R$ X",
      "canais_venda": ["canal 1", "canal 2", "canal 3"],
      "tempo_para_monetizar": "Estimativa realista para começar a monetizar"
    }},
    "score_direcao": 0
  }},

  "bmad_score_total": 0,
  "resumo_executivo": "Parágrafo de 3-5 frases com diagnóstico geral usando a visão BMAD",
  "conclusao_estrategica": "Parágrafo final com os 3 movimentos mais urgentes que este perfil precisa fazer agora"
}}

IMPORTANTE: Os scores (score_marca, score_mercado, score_auditoria, score_direcao) devem ser de 0 a 100 cada. O bmad_score_total é a média dos 4."""

    parts = [{"text": prompt}]
    for i, frame_b64 in enumerate(frames_b64[:6]):
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": frame_b64}})
        parts.append({"text": f"(Frame {i+1} do Reel {i//3 + 1})"})

    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_AI_KEY}",
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0.7,
                    "maxOutputTokens": 8192,
                    "responseMimeType": "application/json"
                }
            },
            timeout=120
        )
        if r.ok:
            result = r.json()
            text = result["candidates"][0]["content"]["parts"][0]["text"]
            text = re.sub(r'^```json\s*', '', text.strip())
            text = re.sub(r'\s*```$', '', text.strip())
            return json.loads(text)
        else:
            log(f"Gemini erro: {r.status_code} {r.text[:300]}")
            return None
    except Exception as ex:
        log(f"Gemini falhou: {ex}")
        return None

def gerar_html_bmad(username, profile_data, analise, compra_score, transcricoes):
    """Gera site HTML com layout BMAD — 4 pilares visuais"""

    veredicto_color = {"LIMPO": "#10b981", "SUSPEITO": "#f59e0b", "ALTA PROBABILIDADE": "#ef4444", "INCONCLUSIVO": "#6b7280"}.get(compra_score["veredicto"], "#6b7280")
    veredicto_icon = {"LIMPO": "✅", "SUSPEITO": "⚠️", "ALTA PROBABILIDADE": "🚨", "INCONCLUSIVO": "❓"}.get(compra_score["veredicto"], "❓")

    followers = profile_data.get("followers", 0)
    following = profile_data.get("following", 0)
    posts_count = profile_data.get("posts_count", 0)

    def fmt(n):
        if n >= 1000000: return f"{n/1000000:.1f}M"
        if n >= 1000: return f"{n/1000:.1f}K"
        return str(n)

    def tags(items, bg="rgba(99,102,241,0.15)", color="var(--accent)"):
        if not items: return "<span style='color:var(--text2)'>N/A</span>"
        return "".join(f'<span class="tag" style="background:{bg};color:{color}">{i}</span>' for i in items)

    brand  = analise.get("bmad_brand", {})  if analise else {}
    market = analise.get("bmad_market", {}) if analise else {}
    audit  = analise.get("bmad_audit", {})  if analise else {}
    direction = analise.get("bmad_direction", {}) if analise else {}
    resumo = analise.get("resumo_executivo", "") if analise else ""
    conclusao = analise.get("conclusao_estrategica", "") if analise else ""
    bmad_total = analise.get("bmad_score_total", 0) if analise else 0

    # Scores dos 4 pilares
    sb = brand.get("score_marca", 0)
    sm = market.get("score_mercado", 0)
    sa = audit.get("score_auditoria", 0)
    sd = direction.get("score_direcao", 0)
    auth_explicacao = audit.get("autenticidade", {}).get("explicacao", "") if analise else ""
    audit_conteudo = audit.get("auditoria_conteudo", {}) if analise else {}
    qualidade_producao = audit_conteudo.get("qualidade_producao", "N/A")
    consistencia_temas = audit_conteudo.get("consistencia_temas", "N/A")

    likes_list = [p["likes"] for p in profile_data.get("recent_posts", []) if p["likes"] > 0]
    avg_likes = sum(likes_list) / len(likes_list) if likes_list else 0
    eng_rate = (avg_likes / followers * 100) if followers > 0 else 0

    # Plano 90 dias HTML
    plano = direction.get("plano_90_dias", {})
    plano_html = ""
    for fase_key, fase_label, cor in [
        ("fase_1_30_dias", "30 dias", "#6366f1"),
        ("fase_2_60_dias", "60 dias", "#a855f7"),
        ("fase_3_90_dias", "90 dias", "#ec4899")
    ]:
        fase = plano.get(fase_key, {})
        if fase:
            acoes_html = "".join(f"<li>{a}</li>" for a in fase.get("acoes", []))
            kpis_html = "".join(f'<span class="kpi-tag">{k}</span>' for k in fase.get("kpis", []))
            plano_html += f"""
            <div class="fase-card" style="border-left: 3px solid {cor}">
              <div class="fase-header">
                <span class="fase-label" style="color:{cor}">{fase_label}</span>
                <span class="fase-meta">{fase.get('meta_seguidores', '')}</span>
              </div>
              <p class="fase-obj">{fase.get('objetivo', '')}</p>
              <ul class="fase-acoes">{acoes_html}</ul>
              <div class="kpis-row">{kpis_html}</div>
            </div>"""

    # Quick wins HTML
    quick_wins = direction.get("quick_wins", [])
    qw_html = ""
    for qw in quick_wins[:4]:
        qw_html += f"""
        <div class="qw-card">
          <div class="qw-acao">{qw.get('acao', '')}</div>
          <p class="qw-impacto">Impacto: {qw.get('impacto', '')}</p>
          <p class="qw-como">Como: {qw.get('como', '')}</p>
        </div>"""

    # Estratégia de conteúdo
    ec = direction.get("estrategia_conteudo", {})
    temas_html = "".join(f"<div class='tema-item'>{t}</div>" for t in ec.get("temas_semana", []))
    hooks_html = "".join(f'<div class="hook-item">"{h}"</div>' for h in ec.get("hooks_recomendados", []))
    mix = ec.get("mix_formatos", {})
    mix_html = "".join(f'<div class="mix-item"><span class="mix-pct">{v}</span><span class="mix-fmt">{k}</span></div>' for k, v in mix.items())

    # Transcrições
    trans_html = "".join(
        f'<div class="trans-card"><h5>Reel {i+1}</h5><p>{t["texto"]}</p></div>'
        for i, t in enumerate(transcricoes[:5]) if t.get("texto")
    ) or "<p class='empty'>Nenhuma transcrição disponível</p>"

    monet = direction.get("monetizacao", {})

    data_analise = datetime.now().strftime("%d/%m/%Y às %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>@{username} — Análise BMAD | {{DONO}}.com</title>
<style>
:root {{
  --bg: #07070f; --bg2: #0e0e1a; --bg3: #161622; --bg4: #1e1e2e;
  --border: rgba(255,255,255,0.07);
  --text: #e2e8f0; --text2: #94a3b8; --text3: #64748b;
  --b: #6366f1; --m: #a855f7; --a: #3b82f6; --d: #10b981;
  --red: #ef4444; --yellow: #f59e0b;
  --r: 10px;
}}
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Inter','Segoe UI',sans-serif; }}

/* HEADER */
.hdr {{ background:linear-gradient(135deg,#07071a,#100d2a); border-bottom:1px solid var(--border); padding:28px 0; }}
.hdr-in {{ max-width:1200px; margin:0 auto; padding:0 28px; display:flex; align-items:center; gap:20px; }}
.av {{ width:72px; height:72px; border-radius:50%; background:linear-gradient(135deg,var(--b),var(--m)); display:flex; align-items:center; justify-content:center; font-size:30px; font-weight:700; color:#fff; flex-shrink:0; }}
.hdr-info h1 {{ font-size:22px; font-weight:700; }}
.hdr-info h1 em {{ color:var(--b); font-style:normal; }}
.hdr-info p {{ color:var(--text2); font-size:13px; margin-top:4px; }}
.hdr-badges {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }}
.badge {{ padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }}

/* BMAD SCORES HEADER */
.bmad-bar {{ background:var(--bg2); border-bottom:1px solid var(--border); padding:20px 0; }}
.bmad-in {{ max-width:1200px; margin:0 auto; padding:0 28px; display:grid; grid-template-columns:repeat(5,1fr); gap:16px; }}
.bmad-score {{ text-align:center; }}
.bmad-score .lbl {{ font-size:11px; text-transform:uppercase; letter-spacing:1px; font-weight:700; margin-bottom:6px; }}
.bmad-score .val {{ font-size:28px; font-weight:800; }}
.bmad-score .sub {{ font-size:11px; color:var(--text3); margin-top:2px; }}
.total-score .val {{ color:#fff; background:linear-gradient(135deg,var(--b),var(--m)); -webkit-background-clip:text; -webkit-text-fill-color:transparent; font-size:36px; }}

/* STATS */
.stats-bar {{ background:var(--bg2); border-bottom:1px solid var(--border); padding:14px 0; }}
.stats-in {{ max-width:1200px; margin:0 auto; padding:0 28px; display:flex; gap:32px; flex-wrap:wrap; }}
.stat .v {{ font-size:20px; font-weight:700; color:var(--b); }}
.stat .l {{ font-size:11px; color:var(--text3); margin-top:2px; }}

/* TABS */
.tabs {{ background:var(--bg2); border-bottom:1px solid var(--border); position:sticky; top:0; z-index:20; }}
.tabs-in {{ max-width:1200px; margin:0 auto; padding:0 28px; display:flex; overflow-x:auto; gap:0; }}
.tab {{ padding:14px 20px; cursor:pointer; border-bottom:2px solid transparent; color:var(--text2); font-size:13px; font-weight:500; white-space:nowrap; transition:all .2s; display:flex; align-items:center; gap:6px; }}
.tab:hover {{ color:var(--text); }}
.tab.active {{ color:var(--accent-tab); border-bottom-color:var(--accent-tab); }}
.tab[data-idx="0"] {{ --accent-tab:var(--b); }}
.tab[data-idx="1"] {{ --accent-tab:var(--m); }}
.tab[data-idx="2"] {{ --accent-tab:var(--a); }}
.tab[data-idx="3"] {{ --accent-tab:var(--d); }}
.tab[data-idx="4"] {{ --accent-tab:var(--yellow); }}
.tab[data-idx="5"] {{ --accent-tab:#ec4899; }}
.tab[data-idx="6"] {{ --accent-tab:var(--text2); }}

/* CONTENT */
.content {{ max-width:1200px; margin:0 auto; padding:32px 28px; }}
.panel {{ display:none; }}
.panel.active {{ display:block; }}
.section-title {{ font-size:20px; font-weight:700; margin-bottom:20px; display:flex; align-items:center; gap:10px; }}
.section-title .pill {{ font-size:11px; font-weight:700; padding:3px 10px; border-radius:20px; letter-spacing:1px; }}

/* CARDS */
.card {{ background:var(--bg3); border:1px solid var(--border); border-radius:var(--r); padding:22px; margin-bottom:18px; }}
.card h3 {{ font-size:14px; font-weight:600; color:var(--text2); text-transform:uppercase; letter-spacing:.5px; margin-bottom:14px; }}
.card p {{ color:var(--text2); line-height:1.7; font-size:14px; }}
.g2 {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.g3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:14px; }}
@media(max-width:768px) {{ .g2,.g3 {{ grid-template-columns:1fr; }} .bmad-in {{ grid-template-columns:repeat(3,1fr); }} }}

/* TAGS */
.tag {{ display:inline-flex; align-items:center; padding:4px 12px; border-radius:20px; font-size:12px; margin:2px; }}

/* SCORE BAR */
.score-bar {{ margin-top:8px; }}
.score-bar-track {{ height:6px; background:var(--bg4); border-radius:3px; overflow:hidden; }}
.score-bar-fill {{ height:100%; border-radius:3px; }}

/* AUTH */
.auth-row {{ display:flex; align-items:center; gap:20px; }}
.auth-circle {{ width:76px; height:76px; border-radius:50%; border:3px solid {veredicto_color}; display:flex; flex-direction:column; align-items:center; justify-content:center; flex-shrink:0; }}
.auth-num {{ font-size:22px; font-weight:800; color:{veredicto_color}; }}
.auth-sub {{ font-size:10px; color:var(--text3); }}
.veredicto {{ font-size:16px; font-weight:700; color:{veredicto_color}; margin-bottom:4px; }}

/* PLANO 90 DIAS */
.fase-card {{ background:var(--bg4); border:1px solid var(--border); border-radius:var(--r); padding:20px; margin-bottom:14px; }}
.fase-header {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }}
.fase-label {{ font-size:13px; font-weight:800; text-transform:uppercase; letter-spacing:1px; }}
.fase-meta {{ font-size:12px; color:var(--text3); background:var(--bg3); padding:3px 10px; border-radius:20px; }}
.fase-obj {{ font-size:13px; color:var(--text2); margin-bottom:12px; }}
.fase-acoes {{ list-style:none; }}
.fase-acoes li {{ font-size:13px; color:var(--text2); padding:5px 0; border-bottom:1px solid var(--border); }}
.fase-acoes li:last-child {{ border-bottom:none; }}
.fase-acoes li::before {{ content:"→ "; color:var(--b); }}
.kpis-row {{ display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }}
.kpi-tag {{ background:rgba(99,102,241,.12); color:var(--b); padding:3px 10px; border-radius:20px; font-size:11px; font-weight:600; }}

/* QUICK WINS */
.qw-card {{ background:rgba(16,185,129,.06); border:1px solid rgba(16,185,129,.2); border-radius:var(--r); padding:18px; margin-bottom:12px; }}
.qw-acao {{ font-size:14px; font-weight:600; margin-bottom:8px; color:var(--d); }}
.qw-impacto, .qw-como {{ font-size:13px; color:var(--text2); margin-bottom:4px; }}

/* MIX CONTEÚDO */
.mix-grid {{ display:flex; gap:12px; flex-wrap:wrap; margin-top:10px; }}
.mix-item {{ background:var(--bg4); border:1px solid var(--border); border-radius:var(--r); padding:14px; text-align:center; min-width:80px; }}
.mix-pct {{ display:block; font-size:20px; font-weight:700; color:var(--b); }}
.mix-fmt {{ display:block; font-size:11px; color:var(--text3); margin-top:3px; }}

/* TEMAS / HOOKS */
.tema-item {{ background:var(--bg4); border:1px solid var(--border); border-radius:var(--r); padding:10px 14px; margin-bottom:6px; font-size:13px; color:var(--text2); }}
.hook-item {{ background:rgba(168,85,247,.08); border:1px solid rgba(168,85,247,.2); border-radius:var(--r); padding:12px 16px; margin-bottom:8px; font-size:14px; color:var(--text); font-style:italic; }}

/* MONETIZAÇÃO */
.mono-grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:14px; margin-top:10px; }}
.mono-card {{ background:var(--bg4); border:1px solid var(--border); border-radius:var(--r); padding:16px; }}
.mono-card .mk {{ font-size:10px; text-transform:uppercase; letter-spacing:1px; color:var(--d); font-weight:700; }}
.mono-card .mv {{ font-size:15px; font-weight:600; margin-top:6px; }}

/* TRANSCRIÇÕES */
.trans-card {{ background:var(--bg3); border:1px solid var(--border); border-radius:var(--r); padding:18px; margin-bottom:12px; }}
.trans-card h5 {{ font-size:12px; font-weight:700; color:var(--b); text-transform:uppercase; margin-bottom:8px; }}
.trans-card p {{ font-size:13px; color:var(--text2); line-height:1.7; }}

/* CONCLUSÃO */
.conclusao {{ background:linear-gradient(135deg,rgba(99,102,241,.08),rgba(168,85,247,.08)); border:1px solid rgba(99,102,241,.25); border-radius:var(--r); padding:28px; }}
.conclusao p {{ font-size:15px; line-height:1.8; }}

/* FOOTER */
.ftr {{ border-top:1px solid var(--border); padding:24px; text-align:center; color:var(--text3); font-size:12px; margin-top:60px; }}
.ftr a {{ color:var(--b); text-decoration:none; }}
.empty {{ color:var(--text3); font-style:italic; text-align:center; padding:40px 0; }}
</style>
</head>
<body>

<!-- HEADER -->
<div class="hdr">
  <div class="hdr-in">
    <div class="av">{username[0].upper()}</div>
    <div class="hdr-info">
      <h1>@<em>{username}</em> — Análise BMAD</h1>
      <p>Metodologia BMAD do MIT &nbsp;·&nbsp; {data_analise} &nbsp;·&nbsp; Gemini 3.1 Pro Preview</p>
      <div class="hdr-badges">
        {'<span class="badge" style="background:rgba(99,102,241,.2);color:#818cf8">✓ Verificado</span>' if profile_data.get("is_verified") else ""}
        {'<span class="badge" style="background:rgba(16,185,129,.2);color:#10b981">Business</span>' if profile_data.get("is_business") else ""}
        <span class="badge" style="background:{veredicto_color}20;color:{veredicto_color}">{veredicto_icon} {compra_score["veredicto"]}</span>
      </div>
    </div>
  </div>
</div>

<!-- BMAD SCORES -->
<div class="bmad-bar">
  <div class="bmad-in">
    <div class="bmad-score">
      <div class="lbl" style="color:var(--b)">B — Brand</div>
      <div class="val" style="color:var(--b)">{sb}</div>
      <div class="sub">Identidade</div>
    </div>
    <div class="bmad-score">
      <div class="lbl" style="color:var(--m)">M — Market</div>
      <div class="val" style="color:var(--m)">{sm}</div>
      <div class="sub">Mercado</div>
    </div>
    <div class="bmad-score">
      <div class="lbl" style="color:var(--a)">A — Audit</div>
      <div class="val" style="color:var(--a)">{sa}</div>
      <div class="sub">Performance</div>
    </div>
    <div class="bmad-score">
      <div class="lbl" style="color:var(--d)">D — Direction</div>
      <div class="val" style="color:var(--d)">{sd}</div>
      <div class="sub">Estratégia</div>
    </div>
    <div class="bmad-score total-score">
      <div class="lbl" style="color:var(--text2)">BMAD Total</div>
      <div class="val">{bmad_total}</div>
      <div class="sub">/ 100</div>
    </div>
  </div>
</div>

<!-- STATS -->
<div class="stats-bar">
  <div class="stats-in">
    <div class="stat"><div class="v">{fmt(followers)}</div><div class="l">Seguidores</div></div>
    <div class="stat"><div class="v">{fmt(following)}</div><div class="l">Seguindo</div></div>
    <div class="stat"><div class="v">{fmt(posts_count)}</div><div class="l">Posts</div></div>
    <div class="stat"><div class="v">{eng_rate:.2f}%</div><div class="l">Engajamento</div></div>
    <div class="stat"><div class="v">{fmt(int(avg_likes))}</div><div class="l">Média Likes</div></div>
    <div class="stat"><div class="v">{audit.get("performance_geral", "N/A")}</div><div class="l">Performance</div></div>
  </div>
</div>

<!-- TABS -->
<div class="tabs">
  <div class="tabs-in">
    <div class="tab active" data-idx="0" onclick="st(0)">🏷️ B — Brand</div>
    <div class="tab" data-idx="1" onclick="st(1)">📊 M — Market</div>
    <div class="tab" data-idx="2" onclick="st(2)">🔍 A — Audit</div>
    <div class="tab" data-idx="3" onclick="st(3)">🚀 D — Direction</div>
    <div class="tab" data-idx="4" onclick="st(4)">📅 Plano 90 dias</div>
    <div class="tab" data-idx="5" onclick="st(5)">💰 Monetização</div>
    <div class="tab" data-idx="6" onclick="st(6)">🎙️ Transcrições</div>
  </div>
</div>

<div class="content">

<!-- PAINEL 0: BRAND -->
<div class="panel active" id="p0">
  <div class="section-title">
    <span style="color:var(--b)">B</span> Brand — Identidade e Posicionamento
    <span class="pill" style="background:rgba(99,102,241,.15);color:var(--b)">SCORE {sb}/100</span>
  </div>
  <div class="card">
    <h3>Resumo Executivo BMAD</h3>
    <p>{resumo}</p>
  </div>
  <div class="g2">
    <div class="card">
      <h3>Identidade da Marca</h3>
      <p>{brand.get("identidade_marca", "N/A")}</p>
      <div style="margin-top:12px">
        <span style="font-size:12px;color:var(--text3)">Arquétipo: </span>
        <span style="font-size:13px;font-weight:600;color:var(--b)">{brand.get("arquetipo", "N/A")}</span>
      </div>
    </div>
    <div class="card">
      <h3>Tom de Voz</h3>
      <p>{brand.get("tom_de_voz", "N/A")}</p>
      <div style="margin-top:12px">
        <span style="font-size:12px;color:var(--text3)">Consistência visual: </span>
        <span style="font-size:13px;font-weight:600">{brand.get("consistencia_visual", "N/A")}</span>
      </div>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <h3>Pilares de Comunicação</h3>
      {tags(brand.get("pilares_comunicacao", []))}
    </div>
    <div class="card">
      <h3>Diferencial Competitivo</h3>
      <p>{brand.get("diferencial_competitivo", "N/A")}</p>
    </div>
  </div>
  <div class="card">
    <h3>Gaps de Marca — O que falta desenvolver</h3>
    {tags(brand.get("gaps_de_marca", []), "rgba(239,68,68,.1)", "#ef4444")}
  </div>
</div>

<!-- PAINEL 1: MARKET -->
<div class="panel" id="p1">
  <div class="section-title">
    <span style="color:var(--m)">M</span> Market — Análise de Mercado
    <span class="pill" style="background:rgba(168,85,247,.15);color:var(--m)">SCORE {sm}/100</span>
  </div>
  <div class="g2">
    <div class="card">
      <h3>Posicionamento no Mercado</h3>
      <p style="margin-bottom:10px"><strong>{market.get("nicho_principal", "N/A")}</strong></p>
      <p style="font-size:13px;color:var(--text3)">Sub-nicho: {market.get("sub_nicho", "N/A")}</p>
      <p style="font-size:13px;color:var(--text3);margin-top:6px">Posicionamento: {market.get("posicionamento_mercado", "N/A")}</p>
    </div>
    <div class="card">
      <h3>Competição</h3>
      <p>Concorrência: <strong>{market.get("concorrencia", "N/A")}</strong></p>
      <p style="margin-top:8px;font-size:13px;color:var(--text3)">Tamanho do mercado: {market.get("tamanho_mercado", "N/A")}</p>
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <h3>Tendências Relevantes</h3>
      {tags(market.get("tendencias_relevantes", []), "rgba(168,85,247,.12)", "var(--m)")}
    </div>
    <div class="card">
      <h3>Oportunidades de Mercado</h3>
      {tags(market.get("oportunidades_mercado", []), "rgba(16,185,129,.1)", "var(--d)")}
    </div>
  </div>
  <div class="card">
    <h3>Benchmarks do Nicho</h3>
    <div class="g2" style="margin-top:0">
      <div>
        <p style="font-size:12px;color:var(--text3)">Engajamento ideal</p>
        <p style="font-size:16px;font-weight:700;color:var(--b);margin-top:4px">{market.get("benchmarks_nicho", {}).get("taxa_engajamento_ideal", "N/A")}</p>
      </div>
      <div>
        <p style="font-size:12px;color:var(--text3)">Frequência ideal</p>
        <p style="font-size:16px;font-weight:700;color:var(--b);margin-top:4px">{market.get("benchmarks_nicho", {}).get("frequencia_posts_ideal", "N/A")}</p>
      </div>
      <div>
        <p style="font-size:12px;color:var(--text3)">Melhores horários</p>
        <p style="font-size:15px;font-weight:600;margin-top:4px">{market.get("benchmarks_nicho", {}).get("horarios_pico", "N/A")}</p>
      </div>
      <div>
        <p style="font-size:12px;color:var(--text3)">Formatos dominantes</p>
        {tags(market.get("benchmarks_nicho", {}).get("formatos_dominantes", []))}
      </div>
    </div>
  </div>
</div>

<!-- PAINEL 2: AUDIT -->
<div class="panel" id="p2">
  <div class="section-title">
    <span style="color:var(--a)">A</span> Audit — Auditoria de Performance
    <span class="pill" style="background:rgba(59,130,246,.15);color:var(--a)">SCORE {sa}/100</span>
  </div>
  <div class="card">
    <h3>Autenticidade dos Seguidores</h3>
    <div class="auth-row">
      <div class="auth-circle">
        <span class="auth-num">{compra_score["score"]}</span>
        <span class="auth-sub">/ 100</span>
      </div>
      <div>
        <div class="veredicto">{veredicto_icon} {compra_score["veredicto"]}</div>
        <p style="font-size:13px;color:var(--text2);margin-top:4px">{auth_explicacao}</p>
      </div>
    </div>
    <div style="margin-top:16px">
      <div style="height:6px;background:var(--bg4);border-radius:3px;overflow:hidden">
        <div style="height:100%;width:{compra_score["score"]}%;background:linear-gradient(to right,#10b981,#f59e0b,#ef4444);border-radius:3px"></div>
      </div>
      <div style="display:flex;justify-content:space-between;font-size:10px;color:var(--text3);margin-top:4px">
        <span>0 — Orgânico</span><span>50 — Suspeito</span><span>100 — Comprado</span>
      </div>
    </div>
    <div style="margin-top:16px">
      {tags(compra_score.get("motivos", []), f"{veredicto_color}15", veredicto_color)}
    </div>
  </div>
  <div class="g2">
    <div class="card">
      <h3>Pontos Fortes</h3>
      {tags(audit.get("pontos_fortes", []), "rgba(16,185,129,.1)", "var(--d)")}
    </div>
    <div class="card">
      <h3>Pontos Fracos</h3>
      {tags(audit.get("pontos_fracos", []), "rgba(239,68,68,.1)", "#ef4444")}
    </div>
  </div>
  <div class="card">
    <h3>Gargalos de Crescimento</h3>
    {tags(audit.get("gargalos_crescimento", []), "rgba(245,158,11,.1)", "var(--yellow)")}
  </div>
  <div class="card">
    <h3>Auditoria de Conteúdo</h3>
    <div class="g2" style="margin-top:0">
      <div>
        <p style="font-size:12px;color:var(--text3)">Qualidade de produção</p>
        <p style="font-size:15px;font-weight:600;margin-top:4px">{qualidade_producao}</p>
      </div>
      <div>
        <p style="font-size:12px;color:var(--text3)">Consistência de temas</p>
        <p style="font-size:15px;font-weight:600;margin-top:4px">{consistencia_temas}</p>
      </div>
    </div>
  </div>
</div>

<!-- PAINEL 3: DIRECTION -->
<div class="panel" id="p3">
  <div class="section-title">
    <span style="color:var(--d)">D</span> Direction — Estratégia e Direção
    <span class="pill" style="background:rgba(16,185,129,.15);color:var(--d)">SCORE {sd}/100</span>
  </div>
  <div class="card">
    <h3>Estratégia Central</h3>
    <p>{direction.get("estrategia_central", "N/A")}</p>
  </div>
  <div class="card">
    <h3>Quick Wins — Ações Imediatas</h3>
    {qw_html or "<p class='empty'>N/A</p>"}
  </div>
  <div class="card">
    <h3>Estratégia de Conteúdo</h3>
    <div class="g2">
      <div>
        <p style="font-size:12px;color:var(--text3);margin-bottom:8px">Mix de Formatos</p>
        <div class="mix-grid">{mix_html}</div>
      </div>
      <div>
        <p style="font-size:12px;color:var(--text3);margin-bottom:8px">Pilares de Conteúdo</p>
        {tags(ec.get("pilares_conteudo", []))}
      </div>
    </div>
    <div style="margin-top:16px">
      <p style="font-size:12px;color:var(--text3);margin-bottom:8px">Temas por dia da semana</p>
      {temas_html}
    </div>
    <div style="margin-top:16px">
      <p style="font-size:12px;color:var(--text3);margin-bottom:8px">Hooks recomendados para os reels</p>
      {hooks_html}
    </div>
  </div>
  <div class="conclusao" style="margin-top:20px">
    <h3 style="margin-bottom:12px;font-size:14px;color:var(--text2);text-transform:uppercase;letter-spacing:.5px">Conclusão Estratégica</h3>
    <p>{conclusao}</p>
  </div>
</div>

<!-- PAINEL 4: PLANO 90 DIAS -->
<div class="panel" id="p4">
  <div class="section-title">📅 Plano de Ação — 90 dias</div>
  {plano_html or "<div class='card'><p class='empty'>Plano não disponível</p></div>"}
</div>

<!-- PAINEL 5: MONETIZAÇÃO -->
<div class="panel" id="p5">
  <div class="section-title">💰 Estratégia de Monetização</div>
  <div class="card">
    <div class="mono-grid">
      <div class="mono-card"><div class="mk">Potencial</div><div class="mv">{monet.get("potencial_atual", "N/A")}</div></div>
      <div class="mono-card"><div class="mk">Modelo Primário</div><div class="mv">{monet.get("modelo_primario", "N/A")}</div></div>
      <div class="mono-card"><div class="mk">Produto Ideal</div><div class="mv">{monet.get("produto_ideal", "N/A")}</div></div>
      <div class="mono-card"><div class="mk">Ticket Médio</div><div class="mv">{monet.get("ticket_medio_estimado", "N/A")}</div></div>
      <div class="mono-card"><div class="mk">Faixa de Preço</div><div class="mv">{monet.get("faixa_preco", "N/A")}</div></div>
      <div class="mono-card"><div class="mk">Prazo p/ Monetizar</div><div class="mv">{monet.get("tempo_para_monetizar", "N/A")}</div></div>
    </div>
  </div>
  <div class="card">
    <h3>Modelo Secundário</h3>
    <p>{monet.get("modelo_secundario", "N/A")}</p>
  </div>
  <div class="card">
    <h3>Canais de Venda Recomendados</h3>
    {tags(monet.get("canais_venda", []), "rgba(16,185,129,.1)", "var(--d)")}
  </div>
</div>

<!-- PAINEL 6: TRANSCRIÇÕES -->
<div class="panel" id="p6">
  <div class="section-title">🎙️ Transcrições dos Reels</div>
  {trans_html}
</div>

</div><!-- /content -->

<div class="ftr">
  Análise gerada por <a href="https://{{DOMINIO_PRINCIPAL}}">{{DONO}}.com</a> &nbsp;·&nbsp; Metodologia BMAD do MIT &nbsp;·&nbsp; Gemini 3.1 Pro Preview + OpenAI Whisper
</div>

<script>
function st(n){{
  document.querySelectorAll('.tab').forEach((t,i)=>t.classList.toggle('active',i===n));
  document.querySelectorAll('.panel').forEach((p,i)=>p.classList.toggle('active',i===n));
}}
</script>
</body>
</html>"""

    return html

def fazer_deploy_vercel(html_path, username):
    """Deploy file-based direto para Vercel sem GitHub"""
    import hashlib
    log(f"Deploy Vercel para @{username}...")
    headers = {"Authorization": f"Bearer {VERCEL_TOKEN}", "Content-Type": "application/json"}
    project_name = f"ig-{username.lower().replace('.', '-').replace('_', '-')}"

    # Criar projeto se nao existe
    requests.post("https://api.vercel.com/v10/projects", headers=headers,
        params={"teamId": VERCEL_ORG_ID},
        json={"name": project_name, "framework": None})

    # Ler HTML e calcular sha1
    with open(html_path, "rb") as fh:
        html_bytes = fh.read()
    sha1 = hashlib.sha1(html_bytes).hexdigest()

    # Upload do arquivo
    requests.post("https://api.vercel.com/v2/files", params={"teamId": VERCEL_ORG_ID},
        headers={"Authorization": f"Bearer {VERCEL_TOKEN}", "Content-Type": "text/html", "x-vercel-digest": sha1},
        data=html_bytes)

    # Deploy com arquivo
    deploy_r = requests.post("https://api.vercel.com/v13/deployments",
        headers=headers, params={"teamId": VERCEL_ORG_ID},
        json={"name": project_name,
              "files": [{"file": "index.html", "sha": sha1, "size": len(html_bytes)}],
              "projectSettings": {"framework": None, "outputDirectory": ".", "buildCommand": None, "installCommand": None},
              "target": "production"})

    if deploy_r.ok:
        data = deploy_r.json()
        deploy_id = data.get("id", "")
        url = data.get("url", f"{project_name}.vercel.app")
        log(f"Deploy iniciado: {deploy_id}")
        for _ in range(18):
            time.sleep(5)
            sr = requests.get(f"https://api.vercel.com/v13/deployments/{deploy_id}",
                headers=headers, params={"teamId": VERCEL_ORG_ID})
            if sr.ok:
                state = sr.json().get("readyState", "")
                if state == "READY":
                    log(f"Deploy pronto: https://{url}")
                    return f"https://{url}"
                elif state == "ERROR":
                    log("Deploy falhou")
                    return None
        return f"https://{url}"
    log(f"Deploy erro: {deploy_r.status_code} {deploy_r.text[:300]}")
    return None

def configurar_cloudflare_dns(username):
    subdomain = username.lower().replace(".", "-").replace("_", "-")
    log(f"DNS: {subdomain}.{{DOMINIO_PRINCIPAL}} → Vercel...")
    headers = {"Authorization": f"Bearer {CF_TOKEN}", "Content-Type": "application/json"}
    payload = {"type": "CNAME", "name": subdomain, "content": "cname.vercel-dns.com", "proxied": True, "ttl": 1}
    r = requests.get(f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records", headers=headers, params={"name": f"{subdomain}.{{DOMINIO_PRINCIPAL}}", "type": "CNAME"})
    if r.ok:
        records = r.json().get("result", [])
        if records:
            requests.put(f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records/{records[0]['id']}", headers=headers, json={**payload, "name": f"{subdomain}.{{DOMINIO_PRINCIPAL}}"})
        else:
            requests.post(f"https://api.cloudflare.com/client/v4/zones/{CF_ZONE_ID}/dns_records", headers=headers, json=payload)
    return f"https://{subdomain}.{{DOMINIO_PRINCIPAL}}"

def salvar_no_postgres(username, profile_data, analise, compra_score, url_final):
    try:
        resumo = (analise.get("resumo_executivo", "") if analise else "")[:400]
        taxa_eng = analise.get('bmad_audit', {}).get('taxa_engajamento_atual', 'N/A') if analise else 'N/A'
        conteudo = f"""ANÁLISE INSTAGRAM BMAD @{username}
Seguidores: {profile_data.get('followers', 0):,} | Engajamento: {taxa_eng}
Autenticidade: {compra_score.get('veredicto', '')} (score {compra_score.get('score', 0)}/100)
BMAD Total: {analise.get('bmad_score_total', 0) if analise else 0}/100
Resumo: {resumo}
Site: {url_final or 'N/A'} | Data: {datetime.now().strftime('%d/%m/%Y')}"""
        sql = f"INSERT INTO memory_chunks (content, source, created_at, metadata) VALUES ('{conteudo.replace(chr(39), chr(39)+chr(39))}', 'instagram-analyzer-bmad', NOW(), '{{\"username\": \"{username}\"}}') ON CONFLICT DO NOTHING;"
        subprocess.run(["psql", "-U", "n8n", "-d", "naia_memory", "-h", "127.0.0.1", "-c", sql],
            capture_output=True, text=True, env={**os.environ, "PGPASSWORD": os.getenv("PG_PASS", "{{POSTGRES_PASSWORD}}")})
        log(f"Salvo no PostgreSQL para @{username}")
    except Exception as ex:
        log(f"PostgreSQL (não crítico): {ex}")

def gerar_plano_30dias(username, profile_data, analise, compra_score, transcricoes):
    """Gera plano de ação 30 dias usando Gemini"""
    followers = profile_data.get("followers", 0)
    meta = int(followers * 1.10)
    ganho = meta - followers

    likes_list = [p["likes"] for p in profile_data.get("recent_posts", []) if p["likes"] > 0]
    avg_likes = sum(likes_list) / len(likes_list) if likes_list else 0
    eng_rate = (avg_likes / followers * 100) if followers > 0 else 0

    brand = analise.get("bmad_brand", {}) if analise else {}
    market = analise.get("bmad_market", {}) if analise else {}
    audit = analise.get("bmad_audit", {}) if analise else {}
    direction = analise.get("bmad_direction", {}) if analise else {}

    context = {
        "username": username,
        "followers": followers,
        "following": profile_data.get("following", 0),
        "posts_count": profile_data.get("posts_count", 0),
        "engagement_rate": round(eng_rate, 2),
        "avg_likes": int(avg_likes),
        "verified": profile_data.get("is_verified", False),
        "business": profile_data.get("is_business", False),
        "bmad_total": analise.get("bmad_score_total", 0) if analise else 0,
        "brand_score": brand.get("score_marca", 0),
        "market_score": market.get("score_mercado", 0),
        "audit_score": audit.get("score_auditoria", 0),
        "direction_score": direction.get("score_direcao", 0),
        "autenticidade_score": compra_score.get("score", 0),
        "resumo": analise.get("resumo_executivo", "") if analise else "",
        "pilares": brand.get("pilares_comunicacao", []),
        "gaps": brand.get("gaps_marca", []),
        "oportunidades": market.get("oportunidades", []),
        "tendencias": market.get("tendencias", []),
        "top_posts": [{"caption": p.get("shortcode", ""), "likes": p["likes"]} for p in sorted(profile_data.get("recent_posts", []), key=lambda x: x["likes"], reverse=True)[:8]],
        "transcricoes": (list(transcricoes.values())[:5] if isinstance(transcricoes, dict) else transcricoes[:5]) if transcricoes else [],
    }

    prompt = f"""Você é um estrategista de crescimento de Instagram de elite no Brasil.

## DADOS DO PERFIL @{username}
{json.dumps(context, ensure_ascii=False, indent=2)}

## OBJETIVO
Plano de ação EXTREMAMENTE DETALHADO para crescimento de 10% nos próximos 30 dias.
- Meta: {followers:,} → {meta:,} seguidores (+{ganho:,})
- Prazo: 30 dias
- Foco: crescimento orgânico + tráfego pago

## ESTRUTURA DO PLANO

### 1. ESTRATÉGIA GERAL
- Diagnóstico rápido
- 3 alavancas principais
- Orçamento sugerido de tráfego pago
- KPIs semanais

### 2. CALENDÁRIO SEMANAL (Semana 1 a 4)
Cada semana com: tema central, posts dia a dia (seg-dom) com formato, hook, conteúdo, CTA, hashtags, horário, inspiração. Stories e growth hack.

### 3. ESTRATÉGIA DE REELS
3 formatos virais, roteiros resumidos dos melhores reels de cada semana, dicas de edição, duração ideal.

### 4. TRÁFEGO PAGO
Investimento semanal, tipo campanha, públicos, criativos.

### 5. COLLABS
5 perfis ideais, formato, abordagem.

### 6. MÉTRICAS
KPIs diários/semanais, checkpoints, plano B.

## FORMATO: JSON
{{
  "estrategia_geral": {{"diagnostico": "str", "alavancas": ["str"], "orcamento_trafego": "str", "kpis_semanais": ["str"]}},
  "semanas": [{{"numero": 1, "tema_central": "str", "meta_seguidores": "str", "dias": [{{"dia": "Segunda", "formato": "Reel|Carrossel|Story|Feed", "hook": "str", "conteudo": "str", "cta": "str", "hashtags": ["str"], "horario": "str", "inspiracao": "str"}}], "stories_diarios": "str", "growth_hack": "str"}}],
  "estrategia_reels": {{"formatos_virais": ["str"], "roteiros": [{{"titulo": "str", "semana": 1, "duracao": "str", "script_resumido": "str", "dicas_edicao": "str"}}]}},
  "trafego_pago": {{"investimento_semanal": "str", "tipo_campanha": "str", "publicos": ["str"], "posts_impulsionar": ["str"]}},
  "collabs": [{{"perfil": "str", "seguidores": "str", "formato_collab": "str", "como_abordar": "str"}}],
  "metricas": {{"kpis_diarios": ["str"], "checkpoints": ["str"], "plano_b": "str"}}
}}
Responda APENAS JSON válido."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_AI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16000, "responseMimeType": "application/json"}
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    log(f"Plano 30 dias recebido ({len(text)} chars)")
    return json.loads(text)


def gerar_html_plano_30dias(plano):
    """Gera HTML do painel Plano 30 dias"""
    eg = plano.get("estrategia_geral", {})
    semanas = plano.get("semanas", [])
    reels = plano.get("estrategia_reels", {})
    trafego = plano.get("trafego_pago", {})
    collabs = plano.get("collabs", [])
    metricas = plano.get("metricas", {})

    alavancas_html = "".join(f'<div class="qw-card"><div class="qw-acao">{a}</div></div>' for a in eg.get("alavancas", []))
    kpis_html = "".join(f'<span class="kpi-tag">{k}</span>' for k in eg.get("kpis_semanais", []))

    semanas_html = ""
    cores = ["#6366f1", "#a855f7", "#ec4899", "#f59e0b"]
    for i, sem in enumerate(semanas):
        cor = cores[i % len(cores)]
        dias_html = ""
        for dia in sem.get("dias", []):
            fmt = dia.get("formato", "")
            fc = {"Reel": "#ef4444", "Carrossel": "#6366f1", "Story": "#a855f7", "Feed": "#10b981"}.get(fmt, "#64748b")
            th = "".join(f'<span style="background:rgba(99,102,241,.1);color:#818cf8;padding:2px 8px;border-radius:12px;font-size:10px;margin-right:4px">#{h}</span>' for h in dia.get("hashtags", [])[:6])
            dias_html += f"""
            <div style="background:var(--bg4);border:1px solid var(--border);border-radius:var(--r);padding:16px;margin-bottom:10px;border-left:3px solid {fc}">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
                <span style="font-weight:700;font-size:14px">{dia.get('dia','')}</span>
                <div style="display:flex;gap:8px;align-items:center">
                  <span style="background:{fc}20;color:{fc};padding:3px 10px;border-radius:12px;font-size:11px;font-weight:700">{fmt}</span>
                  <span style="color:var(--text3);font-size:11px">⏰ {dia.get('horario','')}</span>
                </div>
              </div>
              <p style="font-size:15px;font-weight:600;color:var(--text);margin-bottom:6px">🎣 {dia.get('hook','')}</p>
              <p style="font-size:13px;color:var(--text2);line-height:1.6;margin-bottom:8px">{dia.get('conteudo','')}</p>
              <p style="font-size:12px;color:var(--d);margin-bottom:6px">👉 CTA: {dia.get('cta','')}</p>
              <p style="font-size:11px;color:var(--text3);margin-bottom:6px">💡 Inspiração: {dia.get('inspiracao','')}</p>
              <div style="margin-top:6px">{th}</div>
            </div>"""
        semanas_html += f"""
        <div style="margin-bottom:28px">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
            <div style="width:40px;height:40px;border-radius:50%;background:{cor}20;display:flex;align-items:center;justify-content:center;font-weight:800;color:{cor};font-size:16px">{sem.get('numero',i+1)}</div>
            <div>
              <h3 style="font-size:16px;font-weight:700;color:{cor};margin:0">Semana {sem.get('numero',i+1)}: {sem.get('tema_central','')}</h3>
              <p style="font-size:12px;color:var(--text3);margin-top:2px">Meta: {sem.get('meta_seguidores','')}</p>
            </div>
          </div>
          {dias_html}
          <div style="background:rgba(168,85,247,.06);border:1px solid rgba(168,85,247,.2);border-radius:var(--r);padding:14px;margin-bottom:8px">
            <p style="font-size:12px;font-weight:700;color:var(--m);text-transform:uppercase;margin-bottom:6px">📱 Stories da Semana</p>
            <p style="font-size:13px;color:var(--text2)">{sem.get('stories_diarios','')}</p>
          </div>
          <div style="background:rgba(16,185,129,.06);border:1px solid rgba(16,185,129,.2);border-radius:var(--r);padding:14px">
            <p style="font-size:12px;font-weight:700;color:var(--d);text-transform:uppercase;margin-bottom:6px">🚀 Growth Hack da Semana</p>
            <p style="font-size:13px;color:var(--text2)">{sem.get('growth_hack','')}</p>
          </div>
        </div>"""

    formatos_html = "".join(f'<span class="tag" style="background:rgba(239,68,68,.1);color:#ef4444">{f}</span>' for f in reels.get("formatos_virais", []))
    roteiros_html = ""
    for rot in reels.get("roteiros", []):
        sl = rot.get("script_resumido", "").replace("\n", "<br>")
        roteiros_html += f"""
        <div class="card" style="border-left:3px solid #ef4444">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
            <h3 style="margin:0">{rot.get('titulo','')}</h3>
            <span style="font-size:11px;color:var(--text3)">Semana {rot.get('semana','')} · {rot.get('duracao','')}</span>
          </div>
          <p style="font-size:13px;color:var(--text2);line-height:1.7">{sl}</p>
          <p style="font-size:12px;color:var(--yellow);margin-top:10px">🎬 {rot.get('dicas_edicao','')}</p>
        </div>"""

    publicos_html = "".join(f'<span class="tag" style="background:rgba(59,130,246,.1);color:var(--a)">{p}</span>' for p in trafego.get("publicos", []))
    posts_html = "".join(f'<li style="font-size:13px;color:var(--text2);padding:4px 0">{p}</li>' for p in trafego.get("posts_impulsionar", []))

    collabs_html = ""
    for c in collabs:
        collabs_html += f"""
        <div style="background:var(--bg4);border:1px solid var(--border);border-radius:var(--r);padding:16px;margin-bottom:10px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <span style="font-weight:700;font-size:14px;color:var(--b)">{c.get('perfil','')}</span>
            <span style="font-size:11px;color:var(--text3)">{c.get('seguidores','')}</span>
          </div>
          <p style="font-size:13px;color:var(--text2);margin-bottom:4px">📌 {c.get('formato_collab','')}</p>
          <p style="font-size:12px;color:var(--text3)">💬 {c.get('como_abordar','')}</p>
        </div>"""

    kd = "".join(f'<li style="font-size:13px;color:var(--text2);padding:4px 0">{k}</li>' for k in metricas.get("kpis_diarios", []))
    ch = "".join(f'<div class="fase-card" style="border-left:3px solid var(--b);padding:14px;margin-bottom:8px"><p style="font-size:13px;color:var(--text2)">{c}</p></div>' for c in metricas.get("checkpoints", []))

    return f"""
<div class="panel" id="p7">
  <div class="section-title">📈 Plano de Ação 30 Dias — Crescimento 10%<span class="pill" style="background:rgba(245,158,11,.15);color:var(--yellow)">META +10%</span></div>
  <div class="card"><h3>Diagnóstico</h3><p>{eg.get('diagnostico','')}</p></div>
  <div class="card"><h3>3 Alavancas de Crescimento</h3>{alavancas_html}</div>
  <div class="g2">
    <div class="card"><h3>Orçamento de Tráfego Pago</h3><p style="font-size:20px;font-weight:700;color:var(--b)">{eg.get('orcamento_trafego','')}</p></div>
    <div class="card"><h3>KPIs Semanais</h3><div class="kpis-row">{kpis_html}</div></div>
  </div>
  <div style="margin-top:32px"><h2 style="font-size:18px;font-weight:700;margin-bottom:20px">📅 Calendário Semanal</h2>{semanas_html}</div>
  <div style="margin-top:32px"><h2 style="font-size:18px;font-weight:700;margin-bottom:16px">🎬 Estratégia de Reels</h2>
    <div class="card"><h3>Formatos Virais</h3>{formatos_html}</div>{roteiros_html}</div>
  <div style="margin-top:32px"><h2 style="font-size:18px;font-weight:700;margin-bottom:16px">💰 Tráfego Pago</h2>
    <div class="g2">
      <div class="card"><h3>Investimento Semanal</h3><p style="font-size:18px;font-weight:700;color:var(--d)">{trafego.get('investimento_semanal','')}</p><p style="font-size:13px;color:var(--text2);margin-top:8px">Campanha: {trafego.get('tipo_campanha','')}</p></div>
      <div class="card"><h3>Públicos-Alvo</h3>{publicos_html}</div>
    </div>
    <div class="card"><h3>Posts para Impulsionar</h3><ul style="list-style:none;padding:0">{posts_html}</ul></div>
  </div>
  <div style="margin-top:32px"><h2 style="font-size:18px;font-weight:700;margin-bottom:16px">🤝 Collabs</h2>{collabs_html}</div>
  <div style="margin-top:32px"><h2 style="font-size:18px;font-weight:700;margin-bottom:16px">📊 Métricas</h2>
    <div class="card"><h3>KPIs Diários</h3><ul style="list-style:none;padding:0">{kd}</ul></div>
    <div class="card"><h3>Checkpoints Semanais</h3>{ch}</div>
    <div class="card" style="border-left:3px solid #ef4444"><h3>Plano B</h3><p>{metricas.get('plano_b','')}</p></div>
  </div>
</div>"""


def navegar_link_bio(profile_data):
    """Navega o link da bio e mapeia produtos"""
    import instaloader as IL
    bio_url = None
    bio_text = profile_data.get("bio", "")
    username = profile_data.get("username", "")

    try:
        L = IL.Instaloader()
        L.load_session_from_file(INSTA_SESSION)
        prof = IL.Profile.from_username(L.context, username)
        bio_url = prof.external_url
        bio_text = prof.biography or bio_text
    except:
        pass

    produtos = []
    if bio_url:
        log(f"Navegando link da bio: {bio_url}")
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            r = requests.get(bio_url, headers=headers, timeout=15, allow_redirects=True)
            if r.ok:
                from html.parser import HTMLParser
                class LinkExtractor(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.links = []
                        self.texts = []
                        self._current = ""
                    def handle_starttag(self, tag, attrs):
                        if tag == "a":
                            for k, v in attrs:
                                if k == "href" and v and v.startswith("http"):
                                    self.links.append(v)
                    def handle_data(self, data):
                        self.texts.append(data.strip())
                parser = LinkExtractor()
                parser.feed(r.text)
                produtos = {"url": bio_url, "final_url": str(r.url), "links": parser.links[:20], "texts": [t for t in parser.texts if len(t) > 3][:30]}
        except Exception as ex:
            log(f"Erro ao navegar link bio: {ex}")

    return {"bio_url": bio_url, "bio_text": bio_text, "produtos_raw": produtos}


def gerar_monetizacao_gemini(username, profile_data, analise, bio_data):
    """Gera análise de monetização profunda via Gemini"""
    followers = profile_data.get("followers", 0)
    brand = analise.get("bmad_brand", {}) if analise else {}

    prompt = f"""Você é um estrategista de monetização e arquiteto de funis de vendas de elite no Brasil.

## PERFIL: @{username}
- Seguidores: {followers:,}
- Bio: {bio_data.get('bio_text', '')}
- Link da bio: {bio_data.get('bio_url', 'Nenhum')}
- Dados da página: {json.dumps(bio_data.get('produtos_raw', {}), ensure_ascii=False)[:3000]}
- Pilares: {json.dumps(brand.get('pilares_comunicacao', []), ensure_ascii=False)}
- Resumo: {analise.get('resumo_executivo', '') if analise else ''}

## RETORNE EM JSON:
{{
  "escada_de_valor": [
    {{
      "nivel": "Front-end|Mid-ticket|Back-end|High-ticket",
      "produto": "nome",
      "status": "existente|sugerido",
      "faixa_preco": "R$ X - R$ Y",
      "publico_alvo": "desc",
      "proposta_valor": "o que ganha",
      "funis": [
        {{
          "tipo": "Tráfego Direto|Lançamento Semente|Lançamento Interno|Webinário Gravado|VSL|Desafio|Perpétuo|Isca Digital",
          "descricao": "como funciona",
          "etapas": ["passo 1", "passo 2", "passo 3", "passo 4"],
          "metricas_esperadas": "conversão, CAC, ROAS",
          "investimento_trafego": "R$ X/mês"
        }}
      ],
      "stack_tech": ["ferramenta 1", "ferramenta 2"],
      "precisa_sdr_ia": true,
      "precisa_closer": true
    }}
  ],
  "estrategia_recorrencia": {{
    "modelo": "como criar MRR",
    "produtos_recorrentes": ["prod1"],
    "ticket_medio_mrr": "R$ X/mês"
  }},
  "projecao_faturamento": {{
    "conservador": {{"mensal": "R$ X", "anual": "R$ X", "premissas": "texto"}},
    "moderado": {{"mensal": "R$ X", "anual": "R$ X", "premissas": "texto"}},
    "agressivo": {{"mensal": "R$ X", "anual": "R$ X", "premissas": "texto"}}
  }},
  "stack_recomendado": [
    {{"categoria": "CRM", "ferramenta": "nome", "porque": "motivo"}}
  ],
  "insight_estrategico": "frase matadora"
}}

REGRAS: High-ticket (>R$2000) SEMPRE precisa SDR IA + closer humano. Cada produto 2-3 funis. Projeções realistas. JSON válido apenas."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GOOGLE_AI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.7, "maxOutputTokens": 16000, "responseMimeType": "application/json"}
    }
    r = requests.post(url, json=payload, timeout=120)
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
    log(f"Monetização recebida ({len(text)} chars)")
    return json.loads(text)


def gerar_html_monetizacao_nova(monetizacao):
    """Gera HTML da nova aba Monetização turbinada"""
    escada = monetizacao.get("escada_de_valor", [])
    recorrencia = monetizacao.get("estrategia_recorrencia", {})
    projecao = monetizacao.get("projecao_faturamento", {})
    stack = monetizacao.get("stack_recomendado", [])
    insight = monetizacao.get("insight_estrategico", "")
    nivel_cores = {"Front-end": "#10b981", "Mid-ticket": "#6366f1", "Back-end": "#a855f7", "High-ticket": "#ef4444"}
    funil_cores = {"Tráfego Direto": "#3b82f6", "Lançamento Semente": "#f59e0b", "Lançamento Interno": "#ec4899", "Webinário Gravado": "#8b5cf6", "VSL": "#ef4444", "Desafio": "#10b981", "Perpétuo": "#6366f1", "Isca Digital": "#14b8a6"}

    escada_html = ""
    for prod in escada:
        nivel = prod.get("nivel", "")
        cor = nivel_cores.get(nivel, "#64748b")
        status = prod.get("status", "existente")
        sb = '<span style="background:rgba(16,185,129,.15);color:#10b981;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:700">✅ EXISTENTE</span>' if status == "existente" else '<span style="background:rgba(245,158,11,.15);color:#f59e0b;padding:2px 8px;border-radius:12px;font-size:10px;font-weight:700">💡 SUGERIDO</span>'
        sdr = '<span style="background:rgba(239,68,68,.1);color:#ef4444;padding:2px 8px;border-radius:12px;font-size:10px;margin-left:4px">🤖 SDR IA</span>' if prod.get("precisa_sdr_ia") else ""
        closer = '<span style="background:rgba(168,85,247,.1);color:#a855f7;padding:2px 8px;border-radius:12px;font-size:10px;margin-left:4px">🤝 CLOSER</span>' if prod.get("precisa_closer") else ""
        st_tags = "".join(f'<span style="background:var(--bg4);color:var(--text2);padding:2px 8px;border-radius:12px;font-size:10px;margin:2px">{s}</span>' for s in prod.get("stack_tech", []))
        funis_html = ""
        for funil in prod.get("funis", []):
            fc = funil_cores.get(funil.get("tipo", ""), "#64748b")
            etapas = "".join(f'<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)"><span style="width:20px;height:20px;border-radius:50%;background:{fc}20;color:{fc};display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:800;flex-shrink:0">{i+1}</span><span style="font-size:12px;color:var(--text2)">{e}</span></div>' for i, e in enumerate(funil.get("etapas", [])))
            funis_html += f'<div style="background:var(--bg4);border:1px solid var(--border);border-left:3px solid {fc};border-radius:var(--r);padding:16px;margin-bottom:10px"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px"><span style="background:{fc}20;color:{fc};padding:4px 12px;border-radius:12px;font-size:12px;font-weight:700">{funil.get("tipo","")}</span><span style="font-size:11px;color:var(--text3)">{funil.get("investimento_trafego","")}</span></div><p style="font-size:13px;color:var(--text2);margin-bottom:10px">{funil.get("descricao","")}</p><div style="margin-bottom:8px">{etapas}</div><p style="font-size:11px;color:var(--text3)">📊 {funil.get("metricas_esperadas","")}</p></div>'
        escada_html += f'<div class="card" style="border-left:4px solid {cor}"><div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px"><div><div style="display:flex;align-items:center;gap:8px;margin-bottom:6px"><span style="background:{cor}20;color:{cor};padding:4px 12px;border-radius:12px;font-size:11px;font-weight:800;text-transform:uppercase">{nivel}</span>{sb}{sdr}{closer}</div><h3 style="font-size:16px;font-weight:700;margin:0;color:var(--text)">{prod.get("produto","")}</h3></div><span style="font-size:18px;font-weight:800;color:{cor}">{prod.get("faixa_preco","")}</span></div><p style="font-size:13px;color:var(--text2);margin-bottom:6px">👥 {prod.get("publico_alvo","")}</p><p style="font-size:13px;color:var(--d);margin-bottom:14px">💎 {prod.get("proposta_valor","")}</p><h4 style="font-size:12px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Funis de Venda</h4>{funis_html}<div style="margin-top:10px"><span style="font-size:11px;color:var(--text3)">🔧 Stack: </span>{st_tags}</div></div>'

    proj_html = ""
    for cenario, dados in projecao.items():
        cc = {"conservador": "#10b981", "moderado": "#6366f1", "agressivo": "#ef4444"}.get(cenario, "#64748b")
        proj_html += f'<div style="background:var(--bg4);border:1px solid var(--border);border-top:3px solid {cc};border-radius:var(--r);padding:18px;text-align:center"><p style="font-size:11px;color:{cc};font-weight:800;text-transform:uppercase;letter-spacing:1px">{cenario.capitalize()}</p><p style="font-size:24px;font-weight:800;color:var(--text);margin:8px 0">{dados.get("mensal","")}</p><p style="font-size:12px;color:var(--text3)">mensal</p><p style="font-size:16px;font-weight:700;color:{cc};margin-top:8px">{dados.get("anual","")}/ano</p><p style="font-size:11px;color:var(--text3);margin-top:8px">{dados.get("premissas","")}</p></div>'

    stack_html = "".join(f'<div style="background:var(--bg4);border:1px solid var(--border);border-radius:var(--r);padding:14px"><p style="font-size:10px;color:var(--text3);text-transform:uppercase;letter-spacing:1px;font-weight:700">{s.get("categoria","")}</p><p style="font-size:14px;font-weight:700;color:var(--b);margin-top:4px">{s.get("ferramenta","")}</p><p style="font-size:11px;color:var(--text2);margin-top:4px">{s.get("porque","")}</p></div>' for s in stack)
    rec_prods = "".join(f'<span class="tag" style="background:rgba(99,102,241,.1);color:#818cf8">{p}</span>' for p in recorrencia.get("produtos_recorrentes", []))

    return f"""
<div class="panel" id="p5">
  <div class="section-title">💰 Estratégia de Monetização<span class="pill" style="background:rgba(16,185,129,.15);color:var(--d)">ECOSSISTEMA COMPLETO</span></div>
  <div class="conclusao" style="margin-bottom:24px"><p style="font-size:15px;line-height:1.8">💡 {insight}</p></div>
  <h2 style="font-size:18px;font-weight:700;margin-bottom:20px">🪜 Escada de Valor + Funis de Venda</h2>
  {escada_html}
  <h2 style="font-size:18px;font-weight:700;margin:32px 0 16px">🔄 Estratégia de Recorrência (MRR)</h2>
  <div class="card"><p style="font-size:14px;color:var(--text2);line-height:1.7;margin-bottom:12px">{recorrencia.get('modelo','')}</p><div style="margin-bottom:8px">{rec_prods}</div><p style="font-size:16px;font-weight:700;color:var(--d)">Ticket médio MRR: {recorrencia.get('ticket_medio_mrr','')}</p></div>
  <h2 style="font-size:18px;font-weight:700;margin:32px 0 16px">📈 Projeção de Faturamento</h2>
  <div class="g3">{proj_html}</div>
  <h2 style="font-size:18px;font-weight:700;margin:32px 0 16px">🔧 Stack Tecnológico</h2>
  <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:14px">{stack_html}</div>
</div>"""


def main():
    if len(sys.argv) < 2:
        print("Uso: python3 analyze.py USERNAME")
        sys.exit(1)
    username = sys.argv[1].lstrip("@").strip()
    log(f"=== ANÁLISE BMAD — @{username} ===")
    tmpdir = tempfile.mkdtemp(prefix=f"ig_bmad_{username}_")
    try:
        profile_data = coletar_perfil(username, tmpdir)
        compra_score = calcular_score_compra(profile_data)
        log(f"Autenticidade: {compra_score['veredicto']} ({compra_score['score']}/100)")

        # Análise BMAD (sem transcrições/frames)
        analise = analisar_com_gemini_bmad(profile_data, [], [], compra_score)
        html = gerar_html_bmad(username, profile_data, analise, compra_score, [])

        # Remover aba Transcrições do HTML gerado
        import re as regex
        html = regex.sub(r'<div class="tab"[^>]*>🎙️ Transcrições</div>\n?', '', html)
        html = regex.sub(r'<div class="panel" id="p6">.*?</div>\n</div>', '', html, flags=regex.DOTALL)

        # ===== MONETIZAÇÃO TURBINADA =====
        log("Navegando link da bio e gerando monetização...")
        try:
            bio_data = navegar_link_bio(profile_data)
            monetizacao = gerar_monetizacao_gemini(username, profile_data, analise, bio_data)
            monetizacao_html = gerar_html_monetizacao_nova(monetizacao)
            # Substituir aba Monetização antiga
            old = regex.search(r'<div class="panel" id="p5">.*?</div>\n</div>', html, regex.DOTALL)
            if old:
                html = html.replace(old.group(0), monetizacao_html)
            log("Monetização turbinada integrada")
            local_dir_m = "/root/.openclaw/workspace/analises-instagram"
            os.makedirs(local_dir_m, exist_ok=True)
            with open(f"{local_dir_m}/{username}-monetizacao.json", "w") as f:
                json.dump(monetizacao, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            log(f"Aviso: monetização falhou: {ex}")

        # ===== PLANO DE AÇÃO 30 DIAS =====
        log("Gerando plano de ação 30 dias via Gemini...")
        try:
            plano_data = gerar_plano_30dias(username, profile_data, analise, compra_score, [])
            if plano_data:
                plano_html = gerar_html_plano_30dias(plano_data)
                new_tab = '    <div class="tab" data-idx="6" onclick="st(6)">📈 Plano 30 dias</div>'
                html = html.replace(
                    '  </div>\n</div>\n\n<div class="content">',
                    f'    {new_tab}\n  </div>\n</div>\n\n<div class="content">'
                )
                html = html.replace(
                    '</div><!-- /content -->',
                    f'{plano_html}\n</div><!-- /content -->'
                )
                log("Plano de 30 dias integrado")
                local_dir_p = "/root/.openclaw/workspace/analises-instagram"
                os.makedirs(local_dir_p, exist_ok=True)
                with open(f"{local_dir_p}/{username}-plano30.json", "w") as f:
                    json.dump(plano_data, f, ensure_ascii=False, indent=2)
        except Exception as ex:
            log(f"Aviso: plano 30 dias falhou: {ex}")

        # ===== SALVAR E DEPLOY =====
        local_dir = "/root/.openclaw/workspace/analises-instagram"
        os.makedirs(local_dir, exist_ok=True)
        local_html = f"{local_dir}/{username}.html"
        with open(local_html, "w") as f:
            f.write(html)
        log(f"HTML salvo: {local_html}")

        # Deploy individual (projeto separado por perfil)
        subdomain = username.lower().replace(".", "-").replace("_", "-")
        deploy_dir = f"/tmp/deploy-{subdomain}"
        os.makedirs(deploy_dir, exist_ok=True)
        shutil.copy(local_html, f"{deploy_dir}/index.html")
        with open(f"{deploy_dir}/vercel.json", "w") as f:
            json.dump({"version": 2}, f)

        log(f"Deploy Vercel para @{username}...")
        result = subprocess.run(
            f'cd {deploy_dir} && vercel --prod --yes --name ig-{subdomain} --token {VERCEL_TOKEN}',
            shell=True, capture_output=True, text=True, timeout=60
        )
        deploy_url = result.stdout.strip().split("\\n")[-1] if result.stdout.strip() else ""
        if deploy_url and "vercel.app" in deploy_url:
            log(f"Deploy pronto: {deploy_url}")
            subprocess.run(
                f'vercel alias {deploy_url} {subdomain}.{{DOMINIO_PRINCIPAL}} --token {VERCEL_TOKEN}',
                shell=True, capture_output=True, text=True, timeout=30
            )
        configurar_cloudflare_dns(username)
        url_final = f"https://{subdomain}.{{DOMINIO_PRINCIPAL}}"
        salvar_no_postgres(username, profile_data, analise, compra_score, url_final)

        likes_list = [p["likes"] for p in profile_data.get("recent_posts", []) if p["likes"] > 0]
        avg_likes = sum(likes_list) / len(likes_list) if likes_list else 0
        eng_rate = (avg_likes / profile_data['followers'] * 100) if profile_data['followers'] > 0 else 0
        print("\n" + "="*60)
        print(f"ANÁLISE BMAD COMPLETA — @{username}")
        print("="*60)
        print(f"Seguidores:   {profile_data['followers']:,}")
        print(f"Engajamento:  {eng_rate:.2f}%")
        print(f"Autenticidade:{compra_score['veredicto']} ({compra_score['score']}/100)")
        print(f"BMAD Total:   {analise.get('bmad_score_total', 0) if analise else 0}/100")
        print(f"Site:         {url_final}")
        print(f"HTML local:   {local_html}")
        print("="*60)
    finally:
        try:
            shutil.rmtree(tmpdir)
        except:
            pass

if __name__ == "__main__":
    main()