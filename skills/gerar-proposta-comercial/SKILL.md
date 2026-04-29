---
name: gerar-proposta-comercial
description: Gera proposta comercial profissional completa em HTML single-page, cria repo GitHub privado, faz deploy na Vercel e aponta DNS no Cloudflare no DOMINIO DO ALUNO. Acionar quando o dono pedir "fazer proposta", "gerar proposta comercial", "monta proposta pra cliente X", "criar proposta de mentoria", "criar proposta de consultoria", "proposta pra fulano", "criar proposta a partir da call", "transcricao em proposta", "proposta light navy", "proposta layout maicon", "proposta com tabs", "proposta dark navy".
type: skill
---

# Skill: gerar-proposta-comercial

Pipeline completo de 7 etapas para transformar uma transcricao de call de vendas em proposta profissional online no DOMINIO DO ALUNO.

> **Importante:** o subdominio final e `<NOME>.<DOMINIO_BASE>`, onde `DOMINIO_BASE` e definido pelo proprio aluno no `.env` do agente (ex: `meunegocio.com.br`, `agencia.com`). NAO e mais hardcoded para `denderson.com`. Se o aluno nao configurar `DOMINIO_BASE`, o agente avisa e mostra como configurar.

## Quando acionar

Sempre que o Chefe pedir alguma destas variacoes:
- "fazer proposta"
- "gerar proposta comercial"
- "monta proposta pra cliente X"
- "criar proposta de mentoria"
- "criar proposta de consultoria"
- "transformar essa call em proposta"
- "transformar essa transcricao em proposta"
- "proposta layout maicon"
- "proposta light navy"

## Pipeline obrigatorio (executar nessa ordem)

### Etapa 1 — Analisar a transcricao
Ler a transcricao da call inteira. Extrair:
- Nome do cliente
- Nicho/segmento
- Produtos/servicos atuais
- Faturamento mensal/anual
- Dor principal
- O que o cliente quer (vs o que foi sugerido pelo Chefe)
- Reacao do cliente (positiva, neutra, negativa)
- Modalidade pedida (mentoria solo, mentoria estabelecido, consultoria)

REGRA CRITICA: nunca atribuir falas do dono (closer) ao cliente.

### Etapa 2 — Definir pricing
Consultar `PLAYBOOK-PRECOS.md` desta skill e escolher a faixa correta. Default:
- Mentoria solo: R$20.000
- Mentoria estabelecido: R$30.000
- Consultoria media: R$50-60k
- Consultoria grande: R$95-150k
- Consultoria enterprise: R$250k

### Etapa 3 — Montar HTML
Usar `template-proposta.html` como base. Substituir TODOS os placeholders:
- `{{NOME_CLIENTE}}` — nome completo
- `{{PRIMEIRO_NOME}}` — primeiro nome para titulo
- `{{NICHO}}` — segmento de atuacao
- `{{ANO}}` — 2026
- `{{CONTEXTO_CLIENTE}}` — paragrafo de contexto
- `{{METRICAS_HTML}}` — blocos `<div class="metric">` com numeros do cliente
- `{{DIAGNOSTICO_FUNCIONA}}` — lista do que ja funciona
- `{{DIAGNOSTICO_PRECISA_MUDAR}}` — lista do que precisa evoluir
- `{{SOLUCAO_HTML}}` — paragrafo da solucao proposta
- `{{ESCADA_VALOR_HTML}}` — degraus da escada de valor
- `{{TAB_ESPECIFICA_TITULO}}` — titulo da tab customizada do nicho
- `{{TAB_ESPECIFICA_HTML}}` — conteudo dessa tab
- `{{ENTREGAVEIS_HTML}}` — lista de entregaveis
- `{{FASES_HTML}}` — fases do plano de acao
- `{{GANTT_HTML}}` — barras do Gantt
- `{{INVESTIMENTO_MENTORIA}}` — valor mentoria (R$X.000)
- `{{INVESTIMENTO_CONSULTORIA}}` — valor consultoria
- `{{ROI_ESTIMADO}}` — projecao de retorno
- `{{REFERENCIAS_HTML}}` — clientes anteriores parecidos
- `{{VALIDADE_DATA}}` — data atual + 7 dias

### Etapa 4 — Criar repo GitHub privado
Padrao de nome: `proposta-NOME-CLIENTE` (slug, lowercase, hifens).
Repo SEMPRE privado, na conta GitHub do aluno (variavel `GH_USER` no `.env`).

### Etapa 5 — Deploy na Vercel
Token em variavel `VERCEL_TOKEN` (env do aluno).
Scope em `VERCEL_SCOPE` (slug do time/usuario do aluno na Vercel).

### Etapa 6 — Apontar DNS no Cloudflare
Zone ID em `{{CLOUDFLARE_ZONE_ID}}` (do aluno, da zone do dominio dele).
Token em `{{CLOUDFLARE_DNS_TOKEN}}` (do aluno).
Padrao: `<NOME>.${DOMINIO_BASE}` (lido do `.env` do agente do aluno).
Tipo A, content `76.76.21.21`, TTL 1, proxied false.

Antes de rodar a etapa 6, o script valida que `DOMINIO_BASE` existe e nao esta vazio. Se vazio, agente para a execucao e avisa o dono: "Falta configurar DOMINIO_BASE no .env do agente. Te explico como" + passo a passo.

### Etapa 7 — Salvar na dashboard (opcional)
Se o aluno tiver dashboard de propostas, fazer POST em `${DASHBOARD_URL}/api/proposals`:
```json
{
  "client_name": "NOME",
  "proposal_type": "mentoria|consultoria",
  "value_display": "R$30.000",
  "value_cents": 3000000,
  "description": "resumo curto",
  "proposal_url": "https://nome.<DOMINIO_BASE>",
  "closer": "Nome do closer",
  "status": "enviada"
}
```
Se `DASHBOARD_URL` nao estiver definido no `.env`, pular essa etapa.

## Como executar

Para fazer deploy completo, rode:
```bash
bash skills/gerar-proposta-comercial/scripts/deploy-proposta.sh \
  --nome "joao-silva" \
  --repo "proposta-joao-silva" \
  --domain "joaosilva"
```

O script faz git init + gh repo create + vercel deploy + cloudflare DNS automaticamente.

## Variaveis de ambiente obrigatorias

O aluno precisa configurar em `/opt/naia-agent/.env` (ou no `.env` do projeto) antes de rodar:
- `GH_TOKEN` — Personal Access Token do GitHub com escopo `repo`
- `GH_USER` — username do GitHub do aluno (ex: `meu-bot`)
- `VERCEL_TOKEN` — Token da Vercel
- `VERCEL_SCOPE` — slug do time/usuario na Vercel
- `CLOUDFLARE_DNS_TOKEN` — Token de API do Cloudflare com permissao DNS:Edit (na zone do dominio do aluno)
- `CLOUDFLARE_ZONE_ID` — ID da zone do dominio do aluno
- `DOMINIO_BASE` — dominio raiz do aluno (ex: `meunegocio.com.br`, `agencia.com`). OBRIGATORIO. O subdominio final sera `<NOME_PROJETO>.${DOMINIO_BASE}`.
- `DASHBOARD_URL` — URL da dashboard de propostas (opcional)

> O agente VALIDA `DOMINIO_BASE` no inicio da execucao. Se estiver vazio ou ausente, ele para e mostra ao dono como configurar.

## Regras criticas (nao quebrar)

1. NUNCA atribuir falas do dono ao cliente
2. SEMPRE acentuacao PT-BR completa
3. NUNCA travessoes (usar virgula, ponto ou quebra de linha)
4. SEMPRE tangibilizar tudo que foi discutido na call
5. SEMPRE validade de 7 dias
6. SEMPRE repo privado
7. Tab "Investimento" SEMPRE por ultimo
8. Fonts: Cormorant Garamond (display) + DM Sans (body)
9. Paleta: light navy + glassmorphism
10. Checkboxes interativos com localStorage

## Variacoes da proposta

- Sem preco fechado: trocar tab "Investimento" por "Parceria"
- So mentoria: remover card de consultoria
- Plano de mentorado (ja comprou): sem investimento, so checkboxes
- Briefing pre-call: uso interno, sem investimento
- Projeto de socio: sem investimento, foco em execucao

## Erros que nao podem repetir

1. Atribuir falas do dono ao cliente
2. Nao tangibilizar o que foi discutido
3. Texto sem acentuacao
4. Incluir modalidade nao pedida
5. Condicionar parcelas a fases
6. Vazar info pessoal sensivel
7. Tabs desalinhadas

## Arquivos auxiliares

- `template-proposta.html` — template HTML completo com placeholders
- `scripts/deploy-proposta.sh` — script de deploy automatizado
- `PLAYBOOK-PRECOS.md` — tabela de pricing com criterios
