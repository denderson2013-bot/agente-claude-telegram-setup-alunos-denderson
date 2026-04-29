# PLAYBOOK: Quando criar subagente novo vs adaptar um existente

Decisao crucial. Criar subagente custa setup, manutencao, contexto. Adaptar existente e mais leve.

---

## Pergunta 1: O subagente atual cobre 80%+ do trabalho?

Se SIM → ADAPTAR (atualize o system prompt do existente, adicione referencia de nicho).
Se NAO → CRIAR novo.

Exemplo:
- "Preciso de um SDR pra estetica" → Davi (SDR generalista) ja existe. Posso ADAPTAR Davi com knowledge de estetica? Se sim, atualiza. Se a linguagem/tom/estrutura for muito diferente, criar `sdr-estetica` separado.
- "Preciso de um analista de dados" → Nao existe. CRIAR `analista-dados`.

---

## Pergunta 2: O nicho exige tom/linguagem especifica?

Se SIM → tende a CRIAR.
Se NAO → tende a ADAPTAR.

Exemplos:
- Copy financeiro vs copy de info-produto: tom MUITO diferente. CRIAR `copy-financeiro` separado.
- SDR de academia vs SDR de personal trainer: tom proximo. ADAPTAR Davi com referencia de academia.

---

## Pergunta 3: O publico/persona muda completamente?

Se SIM → CRIAR.
Se NAO → ADAPTAR.

Exemplos:
- SDR pra B2B SaaS (CTO, 35-50, tecnico) vs SDR pra estetica (mulher 28-55, autoestima): personas opostas. CRIAR.
- SDR pra info-produto curso A vs curso B (ambos digital, mesmo perfil): mesma persona. ADAPTAR.

---

## Pergunta 4: As tools necessarias sao radicalmente diferentes?

Se SIM → CRIAR (tools diferentes = subagente diferente).
Se NAO → ADAPTAR.

Exemplos:
- Copywriter geral (Read, Write, Edit, WebFetch, WebSearch) vs Copywriter financeiro (mesmas tools): tool igual. ADAPTAR.
- SDR (Read, Write, WebFetch) vs Analista (Read, Bash, Grep, Glob): tool diferente. CRIAR.

---

## Decisao rapida (matriz)

| Pergunta | Se 0-1 SIM | Se 2 SIM | Se 3-4 SIM |
|---|---|---|---|
| Conclusao | ADAPTAR existente | Discutir com Naia | CRIAR novo |

---

## Como ADAPTAR um subagente existente

1. Identificar subagente mais proximo (ex: Davi pra SDR de academia)
2. Editar arquivo `~/.claude/agents/<nome>.md` ou OpenClaw config
3. Adicionar secao "Especialidade do nicho" no system prompt:
   ```
   ## Especialidade adicional: Estetica
   Quando o lead for de clinica de estetica:
   - Tom calorosa e empatico
   - Foco em fluxo de cadeira (paciente/dia, recompra)
   - Linguagem evita termo medico
   - Frase: "Conta como esta o fluxo da sua clinica hoje"
   ```
4. NAO mudar nome, descricao geral, ou tools (afeta outros usos)
5. Validar testando: `Agent(subagent_type="davi", prompt="qualifique uma clinica de estetica em Sao Paulo")`

---

## Como CRIAR um subagente novo

Pipeline completo na SKILL.md. Resumo:

1. Coletar input (nome, especialidade, nicho, tools, modelo)
2. Validar nome
3. Gerar descricao com triggers
4. Escolher tools via CATALOGO-TOOLS.md
5. Gerar system prompt completo
6. Salvar arquivo (script Claude Code OU OpenClaw)
7. Validar e ativar
8. Reportar ao aluno

---

## Anti-patterns na criacao

### Anti-pattern 1: Criar subagente pra tarefa pontual
Se e tarefa unica que nao se repete, NAO criar subagente. Resolva direto.

Exemplo ruim: "Cria um subagente pra escrever 1 email."
Resposta correta: "Escrevo o email direto sem criar subagente."

### Anti-pattern 2: Criar subagente com 80% sobreposicao com existente
Se 80% e igual, ADAPTAR. So criar se a sobreposicao for < 50%.

### Anti-pattern 3: Dar todas as tools "por garantia"
Dar so o necessario. Cada tool extra = risco de seguranca + token gasto.

### Anti-pattern 4: System prompt generico
Subagente sem nicho claro vira ruim no que faz. Especifique mercado, persona, tom, vocabulario.

### Anti-pattern 5: Esquecer de testar
Sempre testar com 1 prompt simples antes de entregar pro aluno.

---

## Quando ARQUIVAR um subagente

- Nao foi acionado em 30+ dias
- Substituido por subagente mais novo/especializado
- Muda escopo do projeto e nao serve mais

Como arquivar:
- Claude Code: mover `~/.claude/agents/<nome>.md` pra `~/.claude/agents/_archive/`
- OpenClaw: marcar `enabled: false` no JSON, manter no historico

NAO deletar fisicamente. Pode precisar voltar.

---

## Rotina de revisao

A cada 30 dias, rodar revisao:

1. Listar todos os subagentes ativos
2. Para cada um, perguntar:
   - Foi usado nos ultimos 30 dias?
   - O system prompt ainda reflete o que ele faz?
   - As tools estao certas (sem excesso)?
   - O modelo esta certo (sonnet/opus/haiku)?
3. Atualizar/arquivar conforme necessario

---

## Quando NAO criar subagente: usar skill direta

Se o trabalho:
- E pontual (1-2 vezes)
- Nao precisa de personalidade fixa
- Pode ser resolvido com instrucao direta

→ NAO criar subagente. Resolver direto OU criar skill `.md` (mais leve).

Skill = receita reutilizavel.
Subagente = membro da equipe com personalidade fixa.

Diferenca pratica:
- Skill `gerar-relatorio-mensal.md` → instrucao reutilizavel
- Subagente `analista-dados` → membro fixo da equipe que faz varias tarefas

---

## Checklist final antes de entregar subagente novo

- [ ] Nome valido (lowercase, hifen, max 30 chars)
- [ ] Description com 3+ triggers reais
- [ ] System prompt em PT-BR
- [ ] Sem travessoes no prompt
- [ ] Hierarquia clara (reporta pra Naia)
- [ ] Tools minimas necessarias (revisado em CATALOGO-TOOLS.md)
- [ ] Modelo apropriado (sonnet default)
- [ ] Testado com 1 prompt simples
- [ ] Arquivo salvo no path correto
- [ ] OpenClaw: backup feito, JSON validado, restart ok
- [ ] Aluno informado com formato padrao do output
