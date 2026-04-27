# SETUP-AGENTE.md v3 - Instalacao automatica do agente Claude + Telegram (Bot Externo + Prospect + agent-manager)

> Claude, esse arquivo e pra voce executar. O aluno ja rodou o bootstrap e esta logado na conta Claude dele.
>
> Sua missao: seguir esse manual do inicio ao fim, fazendo perguntas claras quando precisar, e entregar:
> - Agente principal (Naia) rodando 24/7 no tmux
> - 5 subagentes especializados
> - Bot externo Python (audio bidirecional)
> - agent-manager.py via PM2 (porta 3600 + Caddy + Cloudflare tunnel)
> - (Opcional) Clone do {{DONO}} SDR personalizado
> - (Opcional) Naia Rita pra prospect Insta + Maps

---

## v3 - O QUE MUDA VS v2

A v2 ja tinha bot Python externo + Whisper + ElevenLabs. A v3 adiciona:

1. **agent-manager.py** (Python via PM2): gerencia jobs longos sem travar a Naia. Roda na porta 3600 com proxy reverso via Caddy. Acessivel em `https://AGENTE.dominio.com`.
2. **Prospect Instagram** via HikerAPI + Tandem: pesquisa leads no Insta (followers de concorrentes, hashtags, locations) e envia DM real pelo navegador (anti-ban).
3. **Prospect Google Maps**: Places API + Geocoding pra leads locais por nicho + cidade.
4. **Bulk DM Gen**: gera 50-200 copies personalizadas em paralelo (Claude API ou subagente jonathan-copy).
5. **Tunnel watchdog**: garante `https://AGENTE.dominio.com` sempre online (recria Cloudflare tunnel se cair).
6. **db_queue resiliencia**: tabela `job_queue` no PostgreSQL pra fila de jobs com retry exponencial.
7. **Padrao Naia + Naia Rita**: Naia orquestra conversa com o Chefe, Rita roda prospect em paralelo (subagente dedicado).

---

## Regras de execucao

1. Leia esse arquivo INTEIRO antes de comecar.
2. Execute na ordem exata.
3. Quando precisar de info do aluno, **pergunte claramente** e **espere a resposta**.
4. Apos cada bloco grande, valide com check.
5. Se falhar, pare e explique. Nao chute solucao.
6. Fala PT-BR direto. Sem travessoes.

---

## ETAPA 0 - PLACEHOLDERS (PERSONALIZACAO PRO ALUNO)

Esse repo e a versao publica/sanitizada. Antes de qualquer ETAPA tecnica, voce, Claude, deve fazer ao aluno UMA PERGUNTA POR VEZ pra coletar os valores reais que substituirao os placeholders no formato `{{NOME}}` espalhados por todos os arquivos do projeto. Depois faz um find+replace global no `/opt/AGENTE/` (ou onde for) trocando placeholder por valor real.

**Tabela completa de placeholders** (na ordem que voce deve perguntar):

| # | Placeholder | Pergunta pro aluno | Exemplo |
|---|---|---|---|
| 1 | `{{DONO}}` | "Qual seu primeiro nome (ou apelido) que vai aparecer no agente?" | `Joao` |
| 2 | `{{DONO_NOME_COMPLETO}}` | "E seu nome completo?" | `Joao Silva` |
| 3 | `{{DONO_SLUG}}` | "Versao 'slug' do seu nome (lowercase, sem espacos, sem acentos). Default: lowercase do anterior." | `joao` |
| 4 | `{{DONO_UPPER}}` | "Nome em CAIXA ALTA (default: uppercase do {{DONO}})" | `JOAO` |
| 5 | `{{EMAIL_DONO}}` | "Seu email (vai virar email do agente nos commits e logs)" | `joao@meusite.com` |
| 6 | `{{NICHO_DONO}}` | "Nome da sua empresa/marca/produto principal" | `Empresa X` |
| 7 | `{{NICHO_DONO_SLUG}}` | "Slug da empresa (lowercase, sem espacos)" | `empresax` |
| 8 | `{{NICHO_DONO_UPPER}}` | "Empresa em CAIXA ALTA" | `EMPRESAX` |
| 9 | `{{TELEGRAM_USER_ID_DONO}}` | "Seu ID numerico no Telegram. Mande `/start` pra @userinfobot e cola o numero aqui." | `123456789` |
| 10 | `{{TELEGRAM_BOT_USERNAME}}` | "Username do bot que voce criou no @BotFather (com `_bot` no final, sem o @)" | `meuagente_bot` |
| 11 | `{{INSTAGRAM_HANDLE_DONO}}` | "Seu @ no Instagram (sem o @)" | `joao.silva` |
| 12 | `{{VPS_IP}}` | "IP da VPS principal onde o agente vai rodar" | `123.45.67.89` |
| 13 | `{{VPS_IP_ALT}}` | "(Opcional) IP de VPS secundaria. Pula se nao tiver." | `123.45.67.90` |
| 14 | `{{VPS_IP_ALT_2}}` | "(Opcional) IP de VPS terciaria. Pula se nao tiver." | `123.45.67.91` |
| 15 | `{{VPS_IP_ALT_3}}` | "(Opcional) IP de VPS quaternaria. Pula se nao tiver." | `123.45.67.92` |
| 16 | `{{DOMINIO_PRINCIPAL}}` | "Seu dominio raiz (sem https, sem www)" | `meusite.com` |
| 17 | `{{DOMINIO_AI}}` | "(Opcional) Dominio secundario .ai ou outro. Pula se nao tiver." | `meusite.ai` |
| 18 | `{{DOMINIO_CRM}}` | "(Opcional) Dominio do seu CRM" | `crm.meusite.com` |
| 19 | `{{DOMINIO_CLIENTE_EXEMPLO}}` | "(Opcional) Subdominio exemplo de cliente" | `cliente1.meusite.com` |
| 20 | `{{DOMINIO_CLIENTE}}` | "(Opcional) Dominio de um cliente real (so pra exemplo)" | `cliente1.com.br` |
| 21 | `{{TANDEM_TOKEN}}` | "(Opcional, so se for usar prospect Insta) Token Tandem do painel" | `abcd1234...` |
| 22 | `{{PRODUTO_DONO}}` | "Nome do seu produto/SaaS principal" | `Meu CRM` |
| 23 | `{{PRODUTO_DONO_SLUG}}` | "Slug do produto" | `meu-crm` |
| 24 | `{{MENTORIA_DONO}}` | "(Opcional) Nome da sua mentoria" | `Mentoria X` |
| 25 | `{{FORMACAO_DONO}}` | "(Opcional) Nome da sua formacao/curso" | `Formacao X em IA` |
| 26 | `{{COMUNIDADE_DONO}}` | "(Opcional) Nome da sua comunidade paga" | `Comunidade X` |
| 27 | `{{SENHA_PADRAO}}` | "Senha admin pro agent-manager (TROCA depois pelo painel!). Default: ano+empresa." | `meusite2026` |
| 28 | `{{GITHUB_USERNAME}}` | "Seu username no GitHub" | `joaodev` |

**Como executar a substituicao depois de coletar tudo:**

```bash
cd /opt/AGENTE  # ou onde for o diretorio raiz do agente
# Cria arquivo de replacements
cat > /tmp/replace.txt <<EOF
{{DONO}}|VALOR_REAL_1
{{DONO_NOME_COMPLETO}}|VALOR_REAL_2
{{DONO_SLUG}}|VALOR_REAL_3
{{DONO_UPPER}}|VALOR_REAL_4
{{EMAIL_DONO}}|VALOR_REAL_5
{{NICHO_DONO}}|VALOR_REAL_6
{{NICHO_DONO_SLUG}}|VALOR_REAL_7
{{NICHO_DONO_UPPER}}|VALOR_REAL_8
{{TELEGRAM_USER_ID_DONO}}|VALOR_REAL_9
{{TELEGRAM_BOT_USERNAME}}|VALOR_REAL_10
{{INSTAGRAM_HANDLE_DONO}}|VALOR_REAL_11
{{VPS_IP}}|VALOR_REAL_12
{{DOMINIO_PRINCIPAL}}|VALOR_REAL_13
{{PRODUTO_DONO}}|VALOR_REAL_14
{{PRODUTO_DONO_SLUG}}|VALOR_REAL_15
{{SENHA_PADRAO}}|VALOR_REAL_16
{{GITHUB_USERNAME}}|VALOR_REAL_17
EOF

# Aplica em todos os arquivos texto do projeto
while IFS='|' read -r placeholder valor; do
  find . -type f \( -name "*.md" -o -name "*.txt" -o -name "*.sh" -o -name "*.py" -o -name "*.sql" -o -name "*.json" -o -name "*.example" -o -name "*.plist.example" -o -name ".env*" \) \
    -print0 | xargs -0 sed -i "s|$placeholder|$valor|g"
done < /tmp/replace.txt
```

Apos rodar, valida com:
```bash
grep -r "{{[A-Z_]*}}" . | head -10  # deve ser ZERO matches
```

So depois disso, segue pra ETAPA 1.

---

## ETAPA 1 - BOOTSTRAP

> Pre-requisito ja feito pelo aluno via `bootstrap.sh`. Confirma:

```bash
node --version       # v22.x
python3 --version    # 3.10+
psql --version       # PostgreSQL 16
claude --version     # 2.1.118
tmux -V              # 3.x
pm2 --version        # 5.x
caddy version        # 2.x
ffmpeg -version | head -1
```

Se algo faltar, manda o aluno rodar de novo:
```bash
curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/bootstrap.sh | bash
```

---

## ETAPA 2 - CLAUDE AUTH LOGIN

Ja foi feito pelo aluno. Valida:
```bash
claude auth status
```

Deve mostrar `"loggedIn": true`.

Se nao logou:
```bash
claude auth login --claudeai
```
Pega o link, manda pro aluno, ele autoriza, copia o codigo, cola.

---

## ETAPA 3 - CONFIGURAR .ENV (variaveis de ambiente)

Pergunta ao aluno e guarda:

| Variavel | Onde pegar | Obrigatorio? |
|---|---|---|
| `AGENTE_NAME` | minusculas, sem espaco. ex `jonas`, `ana` | sim |
| `OWNER_NAME` | nome do dono pro CLAUDE.md. ex `Jonas` | sim |
| `TELEGRAM_BOT_TOKEN` | @BotFather no Telegram | sim |
| `ALLOWED_USERS` | @userinfobot no Telegram (ID numerico) | sim |
| `OPENAI_API_KEY` | platform.openai.com/api-keys | opcional (audio) |
| `ELEVENLABS_API_KEY` | elevenlabs.io/profile | opcional (audio) |
| `ELEVENLABS_VOICE_ID` | elevenlabs.io/voice-library | opcional |
| `HIKERAPI_KEY` | hikerapi.com (assinatura) | opcional (prospect Insta) |
| `TANDEM_TOKEN` | gerado pelo Tandem app no Mac do dono | opcional (prospect Insta) |
| `GOOGLE_MAPS_API_KEY` | console.cloud.google.com (Places + Geocoding) | opcional (prospect Maps) |
| `GITHUB_TOKEN` | github.com/settings/tokens (PAT classic) | opcional (deploy) |
| `VERCEL_TOKEN` | vercel.com/account/tokens | opcional (deploy) |
| `CLOUDFLARE_API_TOKEN` | dash.cloudflare.com/profile/api-tokens (DNS edit) | opcional (tunnel) |
| `ANTHROPIC_API_KEY` | console.anthropic.com (so se usar API direta) | opcional |

**ATENCAO**: ele NAO precisa fornecer tudo de uma vez. So as obrigatorias. As outras pode adicionar depois.

Cria estrutura base:
```bash
useradd -m -s /bin/bash AGENTE 2>/dev/null || echo "ja existe"
mkdir -p /opt/AGENTE/{logs,knowledge,workspace,hooks,cron-scripts,memory-service,agent-manager,.claude/agents}
mkdir -p /opt/AGENTE-bot/{inbox,outbox,sent,processed,state,logs,audio/incoming,audio/outgoing}
chown -R AGENTE:AGENTE /opt/AGENTE /opt/AGENTE-bot
```

Cria `.env` em `/opt/AGENTE/.env` baseado no `.env.example` (copia o template do repo, substitui placeholders).
```bash
chmod 600 /opt/AGENTE/.env
chown AGENTE:AGENTE /opt/AGENTE/.env
```

---

## ETAPA 4 - INICIALIZAR BANCO POSTGRESQL

```bash
PGPASS=$(openssl rand -hex 24)
echo "PG_PASSWORD_AGENTE=$PGPASS" >> /root/.agente-secrets.env
chmod 600 /root/.agente-secrets.env

sudo -u postgres psql -c "CREATE USER AGENTE WITH PASSWORD '$PGPASS';"
sudo -u postgres psql -c "CREATE DATABASE AGENTE_memory OWNER AGENTE;"
sudo -u postgres psql -d AGENTE_memory -c "CREATE EXTENSION IF NOT EXISTS vector;"
sudo -u postgres psql -d AGENTE_memory -c "GRANT ALL PRIVILEGES ON DATABASE AGENTE_memory TO AGENTE;"
```

Aplica `schema.sql` (criar arquivo `/opt/AGENTE/schema.sql` com as tabelas):

- `conversation_history` (id, role, content, embedding vector(1536), created_at)
- `memory_chunks` (id, source, content, embedding, metadata jsonb)
- `memory_facts` (id, fact, embedding, created_at)
- `transcript_chunks` (id, source_call, content, embedding)
- **`job_queue`** (id, type, payload jsonb, status, attempts, scheduled_for, created_at) - **db_queue v3**
- **`prospect_leads`** (id, source, username, profile_data jsonb, status, dm_sent_at) - **prospect v3**
- **`prospect_dm_log`** (id, lead_id, copy, sent_at, response_received) - **prospect v3**

Cria index HNSW em todas as colunas `embedding`:
```sql
CREATE INDEX ON conversation_history USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memory_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON memory_facts USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON transcript_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON job_queue (status, scheduled_for);
CREATE INDEX ON prospect_leads (source, status);
```

Aplica:
```bash
sudo -u postgres psql -d AGENTE_memory -f /opt/AGENTE/schema.sql
```

Adiciona ao `.env`:
```
DATABASE_URL=postgres://AGENTE:PGPASS@127.0.0.1:5432/AGENTE_memory
```

---

## ETAPA 5 - CONFIGURAR BOT TELEGRAM

Pergunta ao aluno o `TELEGRAM_BOT_TOKEN` (do @BotFather) e o `ALLOWED_USERS` (do @userinfobot).

Como criar o bot (passo pro aluno):
1. Telegram, busca `@BotFather`, manda `/newbot`
2. Escolhe nome (ex "Assistente do Jonas")
3. Escolhe username terminando em `bot` (ex `jonas_assistente_bot`)
4. Copia o token retornado
5. Busca `@userinfobot`, manda qualquer msg, copia o ID numerico

Salva no `/opt/AGENTE-bot/.env`:
```
TELEGRAM_BOT_TOKEN=<TOKEN>
ALLOWED_USERS=<ID>
TMUX_SESSION=AGENTE
TMUX_USER=AGENTE
OPENAI_API_KEY=<OPENAI_KEY_OU_VAZIO>
ELEVENLABS_API_KEY=<ELEVENLABS_KEY_OU_VAZIO>
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM
DEBOUNCE_SECONDS=8
```

Cria `bot.py` em `/opt/AGENTE-bot/bot.py` (codigo Python completo: long polling, audio Whisper entrada, ElevenLabs saida, watch outbox, tmux send-keys).

Cria systemd service `/etc/systemd/system/AGENTE-bot.service`:
```ini
[Unit]
Description=AGENTE Telegram Bot (external daemon)
After=network.target

[Service]
Type=simple
User=AGENTE
WorkingDirectory=/opt/AGENTE-bot
ExecStart=/usr/bin/python3 /opt/AGENTE-bot/bot.py
Restart=always
RestartSec=5
EnvironmentFile=/opt/AGENTE-bot/.env

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now AGENTE-bot
systemctl status AGENTE-bot
```

---

## ETAPA 6 - PERSONALIZAR CLAUDE.MD

Pergunta:
- Nome do dono (ex "Jonas")
- Ramo/personalidade ("sou mentor de musica, quero atender duvidas dos alunos")
- Tom desejado (formal, casual, brincalhao)

Cria `/opt/AGENTE/CLAUDE.md` com:

1. PROTOCOLO DE BOOT (recuperar contexto do banco, ler arquivos persistentes)
2. Quem e o agente (nome, papel, missao customizado pra esse aluno)
3. Quem e o dono (info coletada acima)
4. Hierarquia (Dono manda, Naia orquestra, Juliana coordena, subagentes executam)
5. REGRA SUPREMA - PROTOCOLO DE CONVERSA 3 FASES (igual ao da Naia)
6. ARQUITETURA DE ORQUESTRADORA (Naia delega, nao executa)
7. Lista dos 5 subagentes
8. Como responder no Telegram (outbox JSON)
9. Voice ON/OFF (quando usar audio)
10. Anti-patterns (sem travessoes, sem voz de IA)

Cria os 5 subagentes em `/opt/AGENTE/.claude/agents/`:
- `paulo-dev.md` (dev full-stack)
- `juliana-ops.md` (sub-gerente, design, processos)
- `jonathan-copy.md` (copywriter, roteiros)
- `rafael-projetos.md` (gestao de projetos)
- `davi-sdr.md` (SDR vendas SPIN)

Cada um com personalidade dedicada e missao clara.

---

## ETAPA 7 - SUBIR AGENT-MANAGER.PY VIA PM2

`agent-manager.py` e um servico HTTP Python (FastAPI ou Flask) que expoe endpoints internos pra:
- Criar jobs no `job_queue`
- Consultar status
- Disparar prospect (Insta/Maps)
- Bulk DM gen
- Listar leads
- Trigger subagentes em background

Roda na porta 3600.

```bash
mkdir -p /opt/AGENTE/agent-manager
cd /opt/AGENTE/agent-manager

# Copia agent-manager.py do repo (template Python FastAPI)
# Endpoints principais:
#   POST /jobs         - criar job no job_queue
#   GET /jobs/:id      - status
#   POST /prospect/insta - dispara prospect Insta
#   POST /prospect/maps  - dispara prospect Maps
#   POST /dm/bulk-gen  - gera N copies
#   GET /leads         - lista leads do prospect_leads

pip3 install fastapi uvicorn psycopg2-binary requests anthropic

pm2 start agent-manager.py --name agent-manager --interpreter python3
pm2 save
pm2 startup    # gera comando systemctl, executa o que retornar
```

Configura Caddy pra proxy HTTPS:
```bash
cat > /etc/caddy/Caddyfile << EOF
AGENTE.dominio.com {
    reverse_proxy 127.0.0.1:3600
}
EOF
systemctl reload caddy
```

DNS no Cloudflare (via API):
```bash
curl -X POST "https://api.cloudflare.com/client/v4/zones/ZONE_ID/dns_records" \
  -H "Authorization: Bearer $CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  --data '{"type":"A","name":"AGENTE","content":"VPS_IP","proxied":true}'
```

(Tunnel watchdog opcional: cron a cada 5 min checa `curl -I https://AGENTE.dominio.com` e recria tunnel se 502.)

---

## ETAPA 8 - SUBIR CLONE DO {{DONO_UPPER}} SDR (CONFIG PERSONALIZADO)

Pergunta ao aluno se ele quer ativar o Clone SDR (responder DMs Insta como SDR).

Se sim, coleta:
- Nome do produto/oferta principal
- Pitch curto (1-2 frases)
- Preco e termos
- Link de checkout
- Tom (consultivo, agressivo, casual)
- Limites (quantas DMs por dia, horario de funcionamento)

Cria `/opt/AGENTE/.claude/agents/clone-sdr.md` com a personalidade configurada (rapport + SPIN + agendamento).

Configura webhook (se aluno tem GHL/CRM):
```bash
# Endpoint no agent-manager: POST /webhook/insta-dm
# Recebe DM, salva no prospect_dm_log, dispara subagente clone-sdr
```

Atualiza `.env`:
```
SDR_OFFER_NAME=<NOME>
SDR_PITCH=<PITCH>
SDR_PRICE=<PRECO>
SDR_CHECKOUT_URL=<URL>
SDR_DAILY_LIMIT=50
SDR_HOURS=09-22
```

---

## ETAPA 9 - (OPCIONAL) SUBIR NAIA RITA PROSPECCAO

Pre-requisitos:
- `HIKERAPI_KEY` no `.env`
- Tandem instalado e configurado no Mac do dono (gera `TANDEM_TOKEN`)
- (Opcional) `GOOGLE_MAPS_API_KEY` pra prospect local

Cria `/opt/AGENTE/.claude/agents/naia-rita.md`:
- Missao: rodar prospect Insta + Maps em paralelo
- Tools: HikerAPI client, Tandem client, GMaps client, jonathan-copy delegate
- Workflow: busca leads -> filtra ICP -> gera DM personalizada -> envia via Tandem -> log no prospect_dm_log
- Limites: 50-100 DMs/dia (anti-ban), random delay 30-180s entre envios

Cria scripts auxiliares em `/opt/AGENTE/cron-scripts/`:
- `prospect-insta.py` (busca via HikerAPI, salva em `prospect_leads`)
- `prospect-maps.py` (Places + Geocoding, salva em `prospect_leads`)
- `bulk-dm-gen.py` (gera copies em batch via Claude API)
- `tandem-send.py` (envia DM via Tandem token)
- `tunnel-watchdog.sh` (cron 5 min, recria tunnel se cair)

Crons:
```cron
*/30 * * * * /opt/AGENTE/cron-scripts/prospect-insta.py
0 */2 * * * /opt/AGENTE/cron-scripts/prospect-maps.py
*/5 * * * * /opt/AGENTE/cron-scripts/tunnel-watchdog.sh
*/15 * * * * /opt/AGENTE/cron-scripts/process-job-queue.py
```

---

## ETAPA 10 - RESTART E VALIDAR

Reinicia tudo:
```bash
systemctl restart AGENTE-bot
systemctl restart AGENTE
pm2 restart agent-manager
systemctl reload caddy
```

Valida em paralelo:

```bash
# Bot externo vivo
systemctl is-active AGENTE-bot

# Naia Claude vivo
systemctl is-active AGENTE
su - AGENTE -c "tmux ls" | grep AGENTE

# Banco respondendo
sudo -u AGENTE psql -d AGENTE_memory -c "SELECT COUNT(*) FROM conversation_history"

# agent-manager respondendo
curl -s http://127.0.0.1:3600/health
curl -s https://AGENTE.dominio.com/health

# Healthcheck rodando
crontab -u AGENTE -l | grep healthcheck

# Bot recebe mensagem
# (manda "oi" do Telegram, deve aparecer em /opt/AGENTE-bot/inbox/)
```

Se tudo OK, manda mensagem final pro aluno:
- URL agent-manager: `https://AGENTE.dominio.com`
- Bot Telegram: `@bot_username`
- Comandos uteis (logs, restart, ver tela)
- Custos mensais
- Como customizar subagentes
- (Se ativou prospect) primeiros 10 leads ja na fila

---

## COMANDOS UTEIS DO DIA A DIA

**Logs ao vivo:**
```bash
tail -f /opt/AGENTE/logs/agent.log         # Naia Claude
tail -f /opt/AGENTE-bot/logs/bot.log       # Bot Python
pm2 logs agent-manager                      # agent-manager
journalctl -u AGENTE -f                     # systemd Naia
```

**Restart:**
```bash
systemctl restart AGENTE          # restart Naia
systemctl restart AGENTE-bot      # restart bot
pm2 restart agent-manager         # restart manager
```

**Tela do Claude ao vivo:**
```bash
su - AGENTE -c "tmux attach -t AGENTE"
# pra sair sem fechar: Ctrl+B, D
```

**Editar personalidade:**
```bash
nano /opt/AGENTE/CLAUDE.md
systemctl restart AGENTE
```

**Editar subagente:**
```bash
nano /opt/AGENTE/.claude/agents/paulo-dev.md
# nao precisa restart
```

**Ver leads do prospect:**
```bash
sudo -u AGENTE psql -d AGENTE_memory -c "SELECT username, source, status FROM prospect_leads ORDER BY created_at DESC LIMIT 20;"
```

**Disparar prospect manual:**
```bash
curl -X POST https://AGENTE.dominio.com/prospect/insta \
  -H 'Content-Type: application/json' \
  -d '{"keyword":"mentoria de musica","limit":50}'
```

---

## TROUBLESHOOTING

| Problema | Solucao |
|---|---|
| Bot reage mas nao responde | `systemctl is-active AGENTE`. Se inactive, restart. |
| Mensagens duplicadas | Confere se `enabledPlugins.telegram` NAO esta no `~/.claude/settings.json` (foi removido na v3) |
| Audio nao transcreve | Confere `OPENAI_API_KEY` no `/opt/AGENTE-bot/.env` |
| Audio nao sai | Confere `ELEVENLABS_API_KEY` |
| `https://AGENTE.dominio.com` 502 | Tunnel caiu. Cron `tunnel-watchdog.sh` deveria reiniciar em 5 min. Se nao, manual: `pm2 restart agent-manager && systemctl reload caddy` |
| Prospect Insta nao envia DM | Confere TANDEM_TOKEN valido. Tandem precisa estar aberto no Mac do dono. |
| Job queue parado | Cron `process-job-queue.py` rodando? `crontab -u AGENTE -l` |
| Agente nao lembra conversa antiga | Cron `consolidate-conversations.py` ativo? Banco crescendo? |
| VPS reboot e nao volta | `systemctl is-enabled AGENTE AGENTE-bot` deve dar `enabled` |

---

## FIM DO SETUP v3

Em caso de duvida, abrir issue:
https://github.com/{{GITHUB_USERNAME}}/agente-claude-telegram-setup-alunos-denderson/issues
