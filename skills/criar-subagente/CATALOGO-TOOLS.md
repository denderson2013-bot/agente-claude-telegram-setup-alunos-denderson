# CATALOGO de Tools por Especialidade

Matriz oficial de quais tools dar pra cada tipo de subagente. Regra de ouro: dar a MENOR quantidade de tools que resolva o trabalho.

---

## Matriz Resumo

| Especialidade | Tools Claude Code | Tools OpenClaw |
|---|---|---|
| Dev / Engenheiro | Read, Write, Edit, Bash, WebFetch, Grep, Glob | fs.read, fs.write, fs.edit, shell.exec, web.fetch, grep, glob |
| Copywriter | Read, Write, Edit, WebFetch, WebSearch, Grep | fs.read, fs.write, fs.edit, web.fetch, web.search, grep |
| SDR / Vendas | Read, Write, WebFetch | fs.read, fs.write, web.fetch |
| Designer | Read, Write, Edit, Bash, WebFetch, Grep, Glob | fs.read, fs.write, fs.edit, shell.exec, web.fetch, grep, glob |
| Analista de dados | Read, Bash, WebFetch, WebSearch, Grep, Glob | fs.read, shell.exec, web.fetch, web.search, grep, glob |
| Pesquisador | Read, WebFetch, WebSearch, Grep, Write | fs.read, web.fetch, web.search, grep, fs.write |
| Gestor / PM | Read, Write, Edit, Grep, Glob, Agent | fs.read, fs.write, fs.edit, grep, glob, agent.invoke |
| Triagem / Roteador | Read, Grep | fs.read, grep |
| Suporte / Atendimento | Read, Write, WebFetch | fs.read, fs.write, web.fetch |
| Editor de Video / Roteirista | Read, Write, Edit, WebFetch, WebSearch | fs.read, fs.write, fs.edit, web.fetch, web.search |
| Coach / Mentor | Read, Write, WebSearch | fs.read, fs.write, web.search |
| Tradutor / Localizador | Read, Write, Edit, WebFetch | fs.read, fs.write, fs.edit, web.fetch |

---

## Detalhamento por Tool

### Read
**Quando dar**: praticamente todo subagente. Le arquivo, briefing, swipe file, dossie, log.
**Quando NAO dar**: praticamente nunca. Pode tirar so de subagente que so devolve resposta texto direto.

### Write
**Quando dar**: produz entregavel salvo em arquivo (copy, relatorio, deck, codigo).
**Quando NAO dar**: subagente que so qualifica/responde sem produzir artefato.

### Edit
**Quando dar**: itera entregavel existente (refactor de codigo, revisao de copy).
**Quando NAO dar**: SDR, pesquisador inicial, triagem.

### Bash
**Quando dar**: precisa executar comando (rodar script Python, git, npm, psql, conversor).
**Quando NAO dar**: copywriter, SDR, pesquisador, suporte. Risco de seguranca alto.

### WebFetch
**Quando dar**: precisa ler URL especifica (site do concorrente, blog, landing page).
**Quando NAO dar**: subagente totalmente offline (raro).

### WebSearch
**Quando dar**: precisa BUSCAR algo na internet (benchmark, noticia recente, swipe file).
**Quando NAO dar**: subagente que recebe URL ja pronta. Reduz token.

### Grep
**Quando dar**: precisa filtrar arquivo grande (log, swipe file, dossie historico).
**Quando NAO dar**: subagente sem acesso a base de arquivo grande.

### Glob
**Quando dar**: precisa listar arquivos por padrao (ex: todos os .csv de um dir).
**Quando NAO dar**: subagente que sempre recebe path exato.

### Agent (Claude Code) / agent.invoke (OpenClaw)
**Quando dar**: SO pra coordenador (Juliana, gestor de projeto, PM).
**Quando NAO dar**: especialista. Risco de loop infinito de delegacao.

---

## Regras de Seguranca por Tool

### Bash / shell.exec — CRITICO
- Nunca dar pra SDR, copywriter, pesquisador puro
- Subagente com Bash pode rodar comando destrutivo. Garantir prompt forte com regras (no rm -rf, no force push, etc)
- Em OpenClaw, considerar `isolation: true` no config

### Write / Edit — MEDIO
- Dar so pra quem produz artefato real
- Subagente com Write pode sobrescrever arquivo. Garantir prompt que pede confirmacao em sobrescrita

### WebFetch / WebSearch — BAIXO
- Liberar a maioria. Risco baixo.
- So nao liberar pra subagente offline (raro)

### Agent — MEDIO
- Liberar so pra coordenador
- Risco: loop infinito. Subagente A invoca B, B invoca A.
- Em OpenClaw, trabalhar com `max_depth: 2` no config

---

## Decisao rapida: qual perfil tem essa tool?

Pergunta a ser feita ao criar o subagente:

| Pergunta | Se SIM, dar tool |
|---|---|
| Vai produzir arquivo (md, json, html, py, etc)? | Write |
| Vai ITERAR arquivo existente? | Edit |
| Vai rodar script/comando shell? | Bash |
| Vai abrir URL especifica? | WebFetch |
| Vai BUSCAR informacao na web? | WebSearch |
| Vai ler arquivo de input? | Read (sempre) |
| Vai filtrar arquivo grande por padrao texto? | Grep |
| Vai listar arquivos de um diretorio? | Glob |
| Vai DELEGAR pra outro subagente? | Agent (so coordenador) |

---

## Tools que NAO existem no ecossistema (nao tentar liberar)

- ~~`Database`~~ — usar Bash + psql/sqlite via shell
- ~~`HTTP`~~ — usar WebFetch
- ~~`Python`~~ — usar Bash com python3
- ~~`Git`~~ — usar Bash com git

---

## Override pra subagente especial

Se o subagente precisa de combinacao incomum, justificar no system prompt. Exemplo:

```
## Tools permitidas (nao convencional)
- Read, Write, Edit, Bash, Agent
- Por que Agent: esse e o sub-gerente operacional, ele coordena outros subagentes
- Por que Bash: precisa rodar deploy via Vercel CLI
```

Documentar no proprio prompt evita confusao depois.
