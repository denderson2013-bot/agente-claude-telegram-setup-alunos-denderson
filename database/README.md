# Database Schema — naia_memory

Schema completo do banco PostgreSQL usado pelo agente Naia (memoria vetorial, SDR, DMs, transcricoes).

- **Engine**: PostgreSQL 14+ (testado em 14.22)
- **Extensao obrigatoria**: `pgvector`
- **Arquivo**: `schema.sql` (DDL apenas, sem dados, sanitizado)
- **Tamanho**: ~41 KB / 1.641 linhas
- **Total**: 24 tabelas, 33 indices, 22 sequences

## Como aplicar o schema

### 1. Pre-requisitos

- PostgreSQL 14+ instalado e rodando
- Extensao `pgvector` instalada no servidor (`apt install postgresql-14-pgvector` em Ubuntu, ou via source)
- Usuario com permissao `CREATE DATABASE` e `CREATE EXTENSION`

### 2. Criar banco e extensao

```bash
# Criar database (ajuste o usuario conforme seu ambiente)
createdb -h 127.0.0.1 -U postgres naia_memory

# Habilitar pgvector dentro do banco recem-criado
psql -h 127.0.0.1 -U postgres -d naia_memory -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### 3. Aplicar o schema

```bash
psql -h 127.0.0.1 -U postgres -d naia_memory -f schema.sql
```

### 4. Validar

```bash
psql -h 127.0.0.1 -U postgres -d naia_memory -c "\dt"
# Deve listar 24 tabelas
```

## Tabelas agrupadas por dominio

### Memoria e contexto (4 tabelas)

Sao o cerebro vetorial do agente. Embeddings de 1536 dimensoes (text-embedding-3-small da OpenAI).

| Tabela | Funcao | Volume prod |
|---|---|---|
| `conversation_history` | Historico completo de mensagens user/agent (com embedding) | ~30k linhas |
| `memory_chunks` | Chunks indexados de arquivos `knowledge/` e `memory/` | ~6k linhas |
| `memory_facts` | Fatos curtos extraidos manualmente (ancoras semanticas) | ~50 linhas |
| `transcript_chunks` | Chunks de transcricoes de calls indexados | 0 linhas |

### Sessoes e transcricoes (4 tabelas)

| Tabela | Funcao | Volume prod |
|---|---|---|
| `session_transcripts` | Transcricoes brutas de sessoes Telegram/CLI | ~1.7k linhas |
| `conversation_transcripts` | Conversas consolidadas (cron 2h) | 0 linhas |
| `session_checkpoints` | Checkpoints de retomada de contexto | 0 linhas |
| `sync_status` | Status de sincronizacao entre processos | ~330 linhas |

### Direct Messages Instagram (2 tabelas)

| Tabela | Funcao | Volume prod |
|---|---|---|
| `dm_conversations` | Mensagens trocadas com leads via DM | ~9.6k linhas |
| `dm_contact_profiles` | Perfis enriquecidos dos contatos | ~1.2k linhas |

### SDR e vendas (4 tabelas)

Sistema de agentes SDR (Davi, Lucas, Felipe, etc).

| Tabela | Funcao | Volume prod |
|---|---|---|
| `sdr_agents` | Configuracao dos agentes SDR | 3 linhas |
| `sdr_agent_files` | Arquivos de conhecimento por agente | 2 linhas |
| `sdr_channels` | Canais conectados (WhatsApp, IG, etc) | 0 linhas |
| `sdr_agent_sales` | Vendas registradas por agente | ~350 linhas |
| `sdr_cart_abandonments` | Carrinhos abandonados rastreados | ~240 linhas |

### Analytics (1 tabela)

| Tabela | Funcao | Volume prod |
|---|---|---|
| `site_analytics` | Eventos de tracking de sites/landing pages | ~770 linhas |

## Top tabelas por volume

1. `conversation_history` - 29.718
2. `dm_conversations` - 9.583
3. `memory_chunks` - 6.081
4. `session_transcripts` - 1.748
5. `dm_contact_profiles` - 1.237
6. `site_analytics` - 766
7. `sdr_agent_sales` - 351
8. `sync_status` - 328

## Notas importantes

- **Schema apenas, sem dados**: o arquivo `schema.sql` contem somente DDL (CREATE TABLE, INDEX, SEQUENCE, FUNCTION). Para popular um ambiente novo voce vai precisar de seeds proprios.
- **Embeddings 1536 dim**: tabelas com coluna `embedding vector(1536)` exigem que voce gere os embeddings via OpenAI `text-embedding-3-small` para indexar conteudo novo.
- **Indice HNSW**: as buscas semanticas (latencia <50ms em 30k+ vetores) usam indices HNSW criados no schema.
- **User**: o dump foi feito com `--no-owner --no-privileges` entao o schema e neutro, voce pode aplicar com qualquer usuario PostgreSQL.

## Referencia para regerar

Caso o schema mude em producao, regere com:

```bash
ssh root@{{VPS_IP}} "PGPASSWORD=*** pg_dump -h 127.0.0.1 -U n8n -s --no-owner --no-privileges --no-comments naia_memory" > schema.sql
# E remova manualmente as linhas \restrict / \unrestrict no inicio e fim
```
