---
name: analisar-instagram-bmad
description: Faz analise BMAD profunda de qualquer perfil de Instagram, gera dossie cinematografico com 4 abas (Visao Geral, Conteudo, Estrategia 30 Dias, Dados Brutos) e faz deploy automatico em USERNAME.DOMINIO_BASE (subdominio do dominio do aluno, configurado no .env do agente). Ative essa skill quando o usuario disser coisas como "analisa esse perfil do instagram", "analisa @username", "faz dossie de @username", "analise BMAD do @perfil", "analisa o instagram dessa pessoa", "monta dossie completo do instagram", "quero entender o perfil @x", "analise estrategica do instagram @y", "gera plano de 30 dias pro @z", "auditoria instagram @w", "diagnostico instagram", "raio-x do instagram @username".
license: Comercial - Avalanche / Denderson Rodrigues
---

# Skill: Analisar Instagram com BMAD

## O que essa skill faz

Voce e um agente que recebe um @username do Instagram e em ate 5 minutos entrega um dossie cinematografico completo, deployado em uma URL publica (USERNAME.DOMINIO_BASE, onde DOMINIO_BASE e o dominio do aluno configurado no .env do agente, ex: meunegocio.com.br), com:

1. Analise BMAD (Business Model, Audience, Differentiation) profunda
2. Diagnostico de pilares de conteudo e padroes virais
3. Plano estrategico de 30 dias com meta de +10% seguidores
4. Site HTML cinematografico com 4 abas para o cliente navegar
5. Deploy via Vercel + DNS Cloudflare em subdominio profissional do dominio do aluno

## Quando ativar

Sempre que o usuario pedir qualquer coisa relacionada a entender, analisar, auditar ou planejar crescimento de um perfil do Instagram. Triggers comuns:

- "analisa esse perfil do instagram @username"
- "faz dossie de @username"
- "analise BMAD do @username"
- "monta dossie completo do @username"
- "auditoria do instagram @username"
- "diagnostico do instagram @username"
- "plano de 30 dias pro @username"
- "quero entender o perfil @username"
- "raio-x do instagram do @username"

## Fluxo de execucao (10 etapas)

### Etapa 1: Receber input
Extrair @username da mensagem do usuario. Limpar @ e espacos. Validar formato.

### Etapa 2: Crawl HikerAPI (3 chamadas)
Coletar dados base do perfil:
- `user_by_username_v2`: bio, follower_count, full_name, is_verified, external_url, category, public_email
- `user_medias_chunk_v1`: 12 posts mais recentes com captions, likes, comments, taken_at
- `user_clips`: 3 reels mais recentes (opcional)

### Etapa 3: Crawl Tandem (fallback)
Se HikerAPI falhar (rate limit / bloqueio), acionar fallback Tandem Browser via PinchTab. Capturar dados visiveis (bio, seguidores, ultimos posts).

### Etapa 4: Analise Gemini (3 chamadas em sequencia)
1. Visao Macro: nicho, ICP, posicionamento, top 3 forcas, top 3 fraquezas
2. Conteudo: pilares, formato dominante, frequencia, padroes virais, gaps
3. Estrategia 30 dias: plano dia a dia, metas, oportunidades nao exploradas

Prompts completos em `prompts/01-visao-macro.md`, `prompts/02-conteudo.md`, `prompts/03-estrategia-30-dias.md`.

### Etapa 5: Montar dossie HTML
Usar `template-dossie.html` e substituir placeholders:
- `{{USERNAME}}`, `{{AVATAR_URL}}`, `{{BIO}}`, `{{FOLLOWERS}}`, `{{FULL_NAME}}`, `{{VERIFIED}}`
- `{{ANALISE_VISAO_GERAL}}`, `{{ANALISE_CONTEUDO}}`, `{{PLANO_30_DIAS}}`, `{{POSTS_DATA}}`
- `{{GERADO_EM}}` (timestamp BRT)

### Etapa 6: Deploy
Rodar `scripts/deploy-dossie.sh USERNAME` que faz:
1. Le `DOMINIO_BASE` do .env do agente (`/opt/naia-agent/.env` ou env exportada). Se nao existir, aborta e instrui o aluno: "Falta configurar DOMINIO_BASE no /opt/naia-agent/.env. Te explico como." e mostra o passo a passo.
2. Define `FQDN="${USERNAME_INSTAGRAM}.${DOMINIO_BASE}"` (ex: `joaodasilva.meunegocio.com.br`).
3. `git init` na pasta de output
4. `gh repo create $GH_OWNER/dossie-USERNAME --private --source=. --push` (GH_OWNER tambem vem do .env, default `denderson2013-bot` so se nao houver alternativa)
5. `vercel --prod --token $VERCEL_TOKEN --scope $VERCEL_SCOPE --yes`
6. `vercel domains add $FQDN <project>` no escopo do aluno
7. Cria registro DNS A no Cloudflare zone do aluno (`$CLOUDFLARE_ZONE_ID` do .env) apontando pra 76.76.21.21 (proxy OFF), usando token `$CLOUDFLARE_DNS_TOKEN` do aluno.

### Etapa 7: Entregar URL
Retornar `https://$FQDN` ao usuario (ex: `https://joaodasilva.meunegocio.com.br`), com resumo executivo de 5 bullets.

### Etapa 8: Meta de seguidores
Por padrao, plano de 30 dias mira em `+10%` no follower_count atual.
Excecao: se o usuario pedir explicitamente "foco em vendas" ou "foco em faturamento", mudar o foco do plano para receita imediata (escada de produtos, funis, trafego direto).

### Etapa 9: Tom do dossie
Educativo + estrategico + acionavel. Nunca academico. Nunca generico. Sempre com numeros, datas e CTAs concretos.

### Etapa 10: Custo e precificacao
- Custo de producao: ~US$0,025 por dossie (3 chamadas HikerAPI a US$0,001 + Gemini ~US$0,02)
- Pricing pro cliente: R$ 497 (perfis ate 50k seguidores), R$ 997 (50k a 500k), R$ 2.000 (500k+)
- Margem operacional: > 99%

## Como usar

### Modo direto (Claude Code / OpenClaw)

```bash
python3 ~/.claude/skills/analisar-instagram-bmad/scripts/analyze.py @username
```

Output:
- HTML pronto em `/tmp/dossie-USERNAME/index.html`
- Deploy automatico (se variaveis de ambiente estiverem setadas)
- URL final ecoada no stdout

### Variaveis de ambiente necessarias

O aluno cadastra no `/opt/naia-agent/.env` do agente dele (NAO no codigo da skill). A skill apenas le essas variaveis em runtime:

```
# Crawl + IA
HIKERAPI_KEY=...
GEMINI_API_KEY=...

# Deploy (dominio do ALUNO, nao do Denderson)
DOMINIO_BASE=meunegocio.com.br        # dominio raiz do aluno
CLOUDFLARE_DNS_TOKEN=...              # token Cloudflare DNS Edit do aluno
CLOUDFLARE_ZONE_ID=...                # zone_id da DOMINIO_BASE no Cloudflare do aluno
VERCEL_TOKEN=...                      # token Vercel do aluno
VERCEL_SCOPE=...                      # scope/team Vercel do aluno (ex: meunegocio-team)
GH_TOKEN=...                          # PAT GitHub do aluno
GH_OWNER=meunegocio-bot               # owner/org no GitHub do aluno (user ou org)
```

Se `DOMINIO_BASE` nao estiver setado, o agente ABORTA o deploy e responde:

> "Falta configurar `DOMINIO_BASE` no /opt/naia-agent/.env. Te explico como."

E mostra o passo a passo (ver secao "Como o aluno configura o dominio dele" no PLAYBOOK-BMAD.md).

## Arquivos da skill

```
analisar-instagram-bmad/
  SKILL.md                    (este arquivo)
  PLAYBOOK-BMAD.md            (metodologia BMAD em PT-BR)
  template-dossie.html        (template cinematografico)
  prompts/
    01-visao-macro.md
    02-conteudo.md
    03-estrategia-30-dias.md
  scripts/
    analyze.py                (pipeline end-to-end)
    deploy-dossie.sh          (deploy GitHub + Vercel + DNS)
```

## Regras importantes

1. Nunca invente dados. Se HikerAPI nao retornar campo X, deixar `null` no HTML.
2. Sempre confirmar @username antes de gastar API. Se ambiguo, perguntar.
3. Se perfil for privado, avisar e parar. Nao tentar engenharia social.
4. Plano de 30 dias sempre tem meta numerica realista (+10% padrao).
5. Tom de voz: portugues brasileiro, fluido, sem travessoes, sem linguagem robotica.
6. Apos deploy, sempre entregar a URL final (`https://USERNAME.DOMINIO_BASE`) com 5 insights principais resumidos.
7. Custo de cada dossie e baixo (~US$0,025), mas evite rodar 2x pro mesmo perfil sem necessidade.
