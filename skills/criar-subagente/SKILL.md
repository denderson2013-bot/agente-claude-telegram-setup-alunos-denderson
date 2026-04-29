---
name: criar-subagente
description: Cria um subagente novo do zero (Claude Code ou OpenClaw), com personalidade, system prompt, tools restritas e nicho especifico. Acionar quando o aluno disser "cria um subagente pra X", "preciso de um agente especialista em Y", "monta um subagente novo de Z", "quero um SDR de estetica", "cria um copywriter de mercado financeiro", "monta um analista de dados", "adiciona um agente novo no time", "expande a equipe com mais um agente", "preciso de um especialista em <nicho>", ou qualquer pedido pra adicionar um novo membro a equipe Naia (alem dos 13 padrao).
allowed-tools: Read, Write, Edit, Bash, Grep, Glob
---

# Skill: Criar Subagente

## Quando usar essa skill

Acione essa skill SEMPRE que o usuario quiser adicionar um novo subagente ao time. Frases gatilho:

- "cria um subagente pra X"
- "preciso de um agente especialista em Y"
- "monta um subagente novo de Z"
- "adiciona um agente novo no time"
- "expande a equipe com mais um agente"
- "quero um SDR especializado em <nicho>"
- "preciso de um copywriter de <mercado>"
- "monta um analista de dados / pesquisador / designer / gerente / etc"

## O que essa skill faz

Cria um subagente novo, do zero, com:

1. Nome validado (lowercase, sem espacos, sem caracteres especiais)
2. Descricao com triggers (palavras-chave de quando ativar)
3. System prompt completo em PT-BR (personalidade, hierarquia, tom, regras, anti-patterns)
4. Tools restritas conforme especialidade (matriz em CATALOGO-TOOLS.md)
5. Modelo apropriado (sonnet 4.6 default, opus pra coordenacao critica, haiku pra triagem)
6. Arquivo salvo no destino correto (Claude Code OU OpenClaw)
7. Validacao e ativacao automatica

## Decisao: Claude Code ou OpenClaw?

Antes de tudo, identificar a plataforma do aluno. Pergunta direta se nao estiver claro:

- **Claude Code CLI**: arquivo em `~/.claude/agents/<nome>.md`
- **OpenClaw**: edita `/root/.openclaw/openclaw.json` + cria workspace dedicado

Se o aluno tiver os dois, perguntar onde quer adicionar. Default sugerido: Claude Code (mais simples).

## Pipeline (8 etapas obrigatorias)

### 1. Coletar inputs

Perguntar ao aluno (ou inferir da conversa):

- **Nome do subagente**: lowercase, sem espacos. Ex: `sdr-estetica`, `copy-financeiro`, `analista-dados`
- **Especialidade**: o que ele faz de fato. Ex: "vende para clinicas de estetica", "escreve copy pra mercado financeiro"
- **Nicho de atuacao**: contexto/mercado. Ex: "estetica feminina premium", "renda fixa publico 40+"
- **Tools que precisa**: derivar da especialidade via `CATALOGO-TOOLS.md`. Se duvida, perguntar.
- **Modelo**: default `sonnet`. Subir pra `opus` so se for tarefa critica/coordenacao. `haiku` pra triagem rapida.

### 2. Validar nome

Regras:

- Lowercase obrigatorio
- Apenas `a-z`, `0-9` e hifen `-`
- Sem espacos, sem acentos, sem underscore, sem maiuscula
- Maximo 30 caracteres
- Nao colidir com nome existente

Se invalido, devolver opcoes corrigidas (ex: "Carolina SDR" → `carolina-sdr`).

### 3. Gerar descricao com triggers

Formato fixo:

```
<Papel curto>. <O que faz>. Acionar quando: "<trigger 1>", "<trigger 2>", "<trigger 3>".
```

Exemplo: `SDR especialista em estetica. Qualifica leads de clinicas via SPIN selling. Acionar quando: "lead de estetica chegou", "qualifica essa clinica", "agenda call com a esteticista".`

Triggers devem ser frases reais que o usuario diria, nao termos tecnicos.

### 4. Escolher tools (matriz em CATALOGO-TOOLS.md)

Resumo (ler `CATALOGO-TOOLS.md` para detalhe):

| Tipo | Tools |
|---|---|
| Dev / Engenheiro | Read, Write, Edit, Bash, WebFetch, Grep, Glob |
| Copywriter | Read, Write, Edit, WebFetch, WebSearch, Grep |
| SDR / Vendas | Read, Write, WebFetch |
| Designer | Read, Write, Edit, Bash, WebFetch, Grep, Glob |
| Analista de dados | Read, Bash, WebFetch, WebSearch, Grep, Glob |
| Pesquisador | Read, WebFetch, WebSearch, Grep, Write |
| Gestor / PM | Read, Write, Edit, Grep, Glob, Agent |
| Triagem / Roteador | Read, Grep |

Regra: dar a MENOR quantidade de tools que resolva o trabalho. Sem `Bash` pra quem nao precisa rodar comando. Sem `Agent` pra quem nao coordena.

### 5. Gerar system prompt

Estrutura fixa (template em `templates/subagent-template.md`):

1. **Quem e**: nome + papel + emoji opcional
2. **Hierarquia**: filial da Naia, reporta pra Naia. Nunca fala direto com Chefe sem passar pela Naia.
3. **Tom**: definir conforme nicho (tecnico, criativo, formal, casual)
4. **O que faz**: 4-6 bullets
5. **O que NAO faz**: 3-4 bullets (limites claros)
6. **Tools permitidas**: lista + motivo de cada
7. **Anti-patterns universais**: sem travessoes, PT-BR sempre, sem voz robotica de IA, sem elogio vazio
8. **Referencias**: arquivos relevantes pro nicho (knowledge/, memory/)

### 6. Salvar arquivo

#### Claude Code

Caminho: `~/.claude/agents/<nome>.md`

Usar script: `scripts/criar-subagente-claude.sh`

```bash
bash skills/criar-subagente/scripts/criar-subagente-claude.sh \
  --nome "sdr-estetica" \
  --descricao "SDR especialista em estetica..." \
  --tools "Read, Write, WebFetch" \
  --modelo "sonnet" \
  --prompt-file /tmp/sdr-estetica-prompt.md
```

#### OpenClaw

Caminho do JSON: `/root/.openclaw/openclaw.json`
Caminho do workspace: `/root/.openclaw/workspace-<nome>/`

Usar script: `scripts/criar-subagente-openclaw.py`

```bash
python3 skills/criar-subagente/scripts/criar-subagente-openclaw.py \
  --nome "sdr-estetica" \
  --descricao "SDR especialista em estetica..." \
  --tools "fs.read,fs.write,web.fetch" \
  --modelo "claude-sonnet-4-5" \
  --prompt-file /tmp/sdr-estetica-prompt.md
```

O script:
1. Faz backup `openclaw.json.bak.<timestamp>` antes de editar
2. Valida JSON antes de salvar (`json.loads`)
3. Cria workspace `/root/.openclaw/workspace-<nome>/` com SOUL.md, MEMORY.md, BOOTSTRAP.md
4. Restart `systemctl restart openclaw-gateway`
5. Roda `openclaw doctor` no fim

### 7. Validar e ativar

#### Claude Code

- Disponibilidade imediata (sem restart). O subagente fica disponivel no proximo turno.
- Validar com: `Agent(subagent_type="<nome>", description="teste curto", prompt="responde so 'ok'")`

#### OpenClaw

- Restart obrigatorio: `systemctl restart openclaw-gateway`
- Validar com: `openclaw doctor`
- Esperar gateway voltar (porta 18789)

### 8. Output final ao aluno

Formato fixo:

```
Subagente <nome> criado.

Onde: <caminho do arquivo>
Modelo: <modelo>
Tools: <tools>
Triggers: "<trigger 1>", "<trigger 2>", "<trigger 3>"

Como usar:
- Em conversa: "<exemplo de pedido que ativa o subagente>"
- Em codigo (Claude Code): Agent(subagent_type="<nome>", ...)
- Em codigo (OpenClaw): chamada via gateway com agent_id="<nome>"

Status: ativo
```

## Arquivos da skill

- `SKILL.md` (esse) — entrada principal
- `scripts/criar-subagente-claude.sh` — script Claude Code
- `scripts/criar-subagente-openclaw.py` — script OpenClaw
- `templates/subagent-template.md` — skeleton Claude Code
- `templates/subagent-soul-template.md` — skeleton SOUL.md OpenClaw
- `EXEMPLOS.md` — 5 exemplos prontos
- `CATALOGO-TOOLS.md` — matriz tools x especialidade
- `PLAYBOOK.md` — quando criar vs adaptar existente

## Regras de ouro

1. **PT-BR sempre** no system prompt do subagente
2. **Sem travessoes** no prompt do subagente (anti-pattern de voz de IA)
3. **Hierarquia clara**: subagente reporta pra Naia, nao fala com Chefe direto
4. **Menos tools = mais seguro**: dar so o necessario
5. **Backup antes de editar JSON** (OpenClaw) — sempre, com timestamp
6. **Validar JSON** antes de salvar — `json.loads` ou `jq`
7. **Nao quebrar agentes existentes** — verificar colisao de nome antes de salvar
