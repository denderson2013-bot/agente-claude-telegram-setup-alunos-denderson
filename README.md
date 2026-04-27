# Agente Claude Code + Telegram - Setup Automatizado (v3 - VERSAO PUBLICA PRA ALUNOS)

> **VERSAO PUBLICA - ALUNOS DO {{DONO}}**
> Esse repo e a versao publica/sanitizada do setup interno do {{DONO}}. Todos os dados pessoais do dono original viraram placeholders no formato `{{NOME}}`. Voce, aluno, vai preencher com os SEUS proprios dados (nome, dominios, IPs, tokens, bot Telegram, etc).
>
> Nao tente rodar nada sem antes substituir os placeholders. Veja a tabela em `SETUP-AGENTE.md` (ETAPA 0) com cada `{{NOME}}` e onde pegar/o que e.

Instala um agente Claude Code conectado ao Telegram em qualquer VPS Ubuntu 22+ ou macOS. Roda 24/7 via tmux + systemd (Linux) ou tmux + launchd (Mac), sobrevive reboots e auto-reinicia se cair.

**v3 (abril 2026)** - upgrade massivo da v2:
- **Prospect Instagram** via HikerAPI + Tandem (DM real, sem bot risk)
- **Prospect Google Maps** via Places API + Geocoding (lead local)
- **Bulk DM Gen** (gera N copies personalizadas em paralelo)
- **Tunnel Watchdog** (Cloudflare tunnel sempre vivo)
- **db_queue** (resiliencia: jobs em fila quando rede cai)
- **Padrao Naia + Naia Rita** (orquestradora + executora dedicada pra prospect)
- 5 subagentes especializados (paulo-dev, juliana-ops, jonathan-copy, rafael-projetos, davi-sdr)

## Uso

### 1. Na VPS limpa (Ubuntu 22.04+) ou no Mac:

```bash
curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/bootstrap.sh | bash
```

Linux: roda como `root`. Mac: roda como usuario normal (sem sudo na chamada). O script detecta o SO e instala o que precisa.

Instala: Node 22 (via nvm), Python 3, ffmpeg, Claude Code CLI 2.1.118 (pinado), tmux, PostgreSQL 16 + pgvector, Caddy, pm2 globalmente. Baixa o `SETUP-AGENTE.md` pra `/root/` (Linux) ou `~/` (Mac).

### 2. Logar na conta Claude:

```bash
claude auth login --claudeai
```

Abre link no navegador, loga com Pro/Max, autoriza.

### 3. Iniciar o Claude e deixar ele fazer o resto:

```bash
cd /root        # ou cd ~ no Mac
claude --dangerously-skip-permissions
```

### 4. Dentro do Claude, cola:

```
Leia o arquivo SETUP-AGENTE.md e execute todos os passos.
Me faca perguntas quando precisar de informacao minha.
```

O Claude pergunta nome do agente, dono, tokens, configura tudo e entrega o agente rodando.

## Arquitetura v3

```
[Telegram]  <==>  [Bot Python systemd 24/7]  <==>  tmux send-keys + outbox JSON
                            |                              |
                            v                              v
                   [audio Whisper / TTS]          [Claude Code (tmux)]
                                                          |
                                                          v
                                               [agent-manager.py PM2]
                                                          |
                                  +---------+---------+---+---+----------+
                                  v         v         v       v          v
                              paulo-dev juliana-ops jonathan rafael    davi-sdr
                                                                          |
                                                                          v
                                                              [Naia Rita - prospect]
                                                              HikerAPI + Tandem + GMaps
```

5 camadas de resiliencia:
- Bot Python systemd `Restart=always`
- Claude Code tmux + start.sh com backoff
- agent-manager via PM2 (auto-restart)
- Tunnel watchdog (re-cria tunnel Cloudflare se cair)
- db_queue (jobs persistentes em PostgreSQL, retry automatico)
- Healthcheck a cada 2 min com auto-alerta no Telegram

## Recursos v3

- Bot externo Python sempre vivo (independente do Claude Code)
- Audio entrada (Whisper PT-BR) e saida (ElevenLabs TTS) bidirecional
- "Digitando..." automatico durante todo processamento
- 5 subagentes especializados (paulo-dev, juliana-ops, jonathan-copy, rafael-projetos, davi-sdr)
- Memoria vetorial PostgreSQL + pgvector (HNSW index)
- agent-manager.py via PM2 (porta 3600 + Caddy proxy HTTPS)
- **Prospect Instagram**: HikerAPI (busca followers/hashtags/locations) + Tandem (envia DM real do navegador, evita ban)
- **Prospect Google Maps**: Places API + Geocoding (acha negocios locais por nicho/cidade)
- **Bulk DM gen**: gera 50-200 copies personalizadas em paralelo (1 LLM call por lead)
- **Tunnel watchdog**: re-cria Cloudflare tunnel se cair, garantindo `https://AGENTE.dominio.com` sempre online
- **db_queue**: fila de jobs em PostgreSQL com retry exponencial (sobrevive net flap)
- Naia + Naia Rita pattern (Naia orquestra conversa, Rita roda prospect em paralelo)
- Healthcheck a cada 2 min com auto-alerta
- Backup conversas a cada 2h, relatorio diario as 9h

## Requisitos

- VPS Ubuntu 22.04+ (8 GB RAM, 50 GB disco recomendado pra subagentes paralelos) **ou** macOS 13+
- Conta Claude Pro ou Max
- Conta Telegram (pra @BotFather)
- (Opcional) OpenAI API key (audio entrada via Whisper)
- (Opcional) ElevenLabs API key (audio saida via TTS)
- (Opcional, prospect Insta) HikerAPI key + Tandem instalado no Mac do dono
- (Opcional, prospect Maps) Google Maps API key (Places + Geocoding habilitados)
- (Opcional, deploy publico) GitHub PAT + Vercel token + Cloudflare DNS token

## Custo mensal por agente (estimado)

- VPS 4-8 GB: R$40-120 (Hostinger, Hetzner, DigitalOcean)
- Claude Max: US$100 (Pro tambem serve)
- OpenAI Whisper: ~$0.006/min de audio (irrelevante)
- ElevenLabs: free tier 10k chars/mes serve, basic $5/mes
- HikerAPI (prospect Insta): $30-50/mes
- Tandem (prospect Insta): $20/mes
- Google Maps API (prospect): ~$5-15/mes (depende volume)
- Telegram: gratis
- PostgreSQL: gratis (local)

Total por agente: ~R$600-1000/mes (sem prospect: ~R$600).

## Migracao da v2 para v3

Se ja tem agente v2 rodando, pra adicionar features v3:
1. Atualiza Claude Code: `npm i -g @anthropic-ai/claude-code@2.1.118`
2. Instala PM2: `npm i -g pm2`
3. Cria `agent-manager.py` em `/opt/AGENTE/agent-manager/`
4. Configura Caddy proxy + Cloudflare tunnel
5. (Opcional) Configura HikerAPI + Tandem + GMaps API no `.env`
6. Cria subagente `naia-rita` em `/opt/AGENTE/.claude/agents/`
7. Reinicia tudo: `systemctl restart AGENTE AGENTE-bot && pm2 restart agent-manager`

## Issues / Suporte

https://github.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/issues

## Como personalizar (placeholders)

Antes de rodar o setup, voce precisa preencher os placeholders no formato `{{NOME}}` que estao espalhados pelos arquivos. Tabela completa em `SETUP-AGENTE.md` (ETAPA 0).

Resumo dos principais:

| Placeholder | O que e | Onde voce acha |
|---|---|---|
| `{{DONO}}` | Seu primeiro nome (ou da empresa) | Voce decide |
| `{{DONO_NOME_COMPLETO}}` | Nome completo do dono do agente | Voce decide |
| `{{DONO_SLUG}}` | Versao "slug" do nome (lowercase, sem espacos) | Ex: `joao` se voce e Joao |
| `{{EMAIL_DONO}}` | Seu email | Seu email pessoal/profissional |
| `{{NICHO_DONO}}` | Nome da empresa/produto/marca | Voce decide |
| `{{NICHO_DONO_SLUG}}` | Slug da empresa | Ex: `meunicho` |
| `{{TELEGRAM_USER_ID_DONO}}` | Seu ID numerico no Telegram | Mande `/id` pra @userinfobot |
| `{{TELEGRAM_BOT_USERNAME}}` | Username do bot que voce criou no @BotFather | Ex: `meuagente_bot` |
| `{{INSTAGRAM_HANDLE_DONO}}` | Seu @ no Instagram (sem o @) | Ex: `joao.silva` |
| `{{VPS_IP}}` | IP da VPS principal onde roda o agente | Hostinger/Hetzner/DigitalOcean |
| `{{VPS_IP_ALT}}` / `{{VPS_IP_ALT_2}}` / `{{VPS_IP_ALT_3}}` | IPs de VPS adicionais (se tiver) | Painel da sua provedora |
| `{{DOMINIO_PRINCIPAL}}` | Seu dominio raiz | Ex: `meusite.com` |
| `{{DOMINIO_AI}}` | Dominio secundario (opcional) | Ex: `meusite.ai` |
| `{{DOMINIO_CRM}}` | Dominio do CRM (se tiver) | Ex: `crm.meusite.com` |
| `{{DOMINIO_CLIENTE_EXEMPLO}}` | Subdominio exemplo de cliente | Ex: `cliente1.meusite.com` |
| `{{TANDEM_TOKEN}}` | Token Tandem (so se for usar prospect Insta) | Painel Tandem |
| `{{PRODUTO_DONO}}` | Nome do seu produto/SaaS principal | Voce decide |
| `{{PRODUTO_DONO_SLUG}}` | Slug do produto | Ex: `meu-produto` |
| `{{MENTORIA_DONO}}` / `{{FORMACAO_DONO}}` / `{{COMUNIDADE_DONO}}` | Nomes dos seus produtos educacionais | Voce decide |
| `{{SENHA_PADRAO}}` | Senha admin que voce vai usar (TROCA depois!) | Voce define |
| `{{GITHUB_USERNAME}}` | Seu username no GitHub | Conta sua |
| `{{DONO_UPPER}}` / `{{NICHO_DONO_UPPER}}` | Versoes em CAIXA ALTA | Use os mesmos valores ja definidos, em uppercase |
