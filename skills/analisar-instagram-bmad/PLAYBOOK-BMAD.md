# Playbook BMAD aplicado a Instagram

Este playbook explica a metodologia BMAD usada na analise de perfis de Instagram. BMAD e um framework de auditoria estrategica que combina tres pilares de diagnostico:

- **B** = Business Model (Modelo de negocio)
- **M** = Marketing/Mensagem (Posicionamento e narrativa)
- **A** = Audience (Audiencia e ICP)
- **D** = Differentiation (Diferenciacao competitiva)

## Por que BMAD

A maioria dos perfis cria conteudo aleatorio sem clareza de modelo de negocio, mensagem central ou diferenciacao. O resultado e crescimento lento, baixa conversao em vendas e churn alto de seguidores.

Aplicar BMAD obriga o analista (no caso, voce, agente IA) a olhar o perfil sob 4 lentes complementares e produzir um diagnostico que o dono do perfil consiga executar em 30 dias.

## Pilar 1: Business Model (B)

Perguntas que voce responde:

1. Como esse perfil ganha dinheiro hoje? (curso, mentoria, infoproduto, servico, e-commerce, parcerias)
2. Existe oferta clara no link da bio?
3. Qual o ticket medio aparente?
4. Existe escada de valor (produto barato => caro)?
5. Existe funil de captura (lead magnet => nutricao => venda)?

Sinais de saude:
- Link da bio leva pra pagina de venda especifica (nao homepage)
- Bio menciona oferta principal e CTA
- Stories destacados explicam produtos
- Posts recentes sao colados em ofertas (lancamento ou perpetuo)

Sinais de doenca:
- Link "linktree" generico com 10 destinos sem priorizacao
- Bio so com missao/valores, sem CTA
- Zero menciao a produto nos ultimos 12 posts
- Nenhum lead magnet visivel

## Pilar 2: Marketing/Mensagem (M)

Perguntas:

1. Qual a promessa central do perfil?
2. Em uma frase, "esse perfil ajuda quem a fazer o que"?
3. A bio comunica isso em ate 150 caracteres?
4. Os ultimos 12 posts reforcam a mesma mensagem ou pulam de tema?

Sinais de saude:
- Mensagem central repetida em angulos diferentes
- Linguagem consistente (tom, jargao, gatilhos)
- Bio responde "pra quem e isso" e "qual o resultado prometido"

Sinais de doenca:
- Mistura nichos (ex: "fitness + financas pessoais + reels engracados")
- Bio com palavras genericas ("apaixonado por ajudar pessoas")
- Posts sem tese clara

## Pilar 3: Audience (A) — ICP

Perguntas:

1. Quem e o ICP (perfil de cliente ideal)?
2. Esse ICP pode pagar pelo produto?
3. Os comentarios mostram que esse ICP esta engajando?
4. Existe diversidade de origem (tudo veio de trafego pago? ou tem organico?)

Sinais de saude:
- Comentarios sao do nicho-alvo (linguagem coerente, perguntas de compra)
- ICP claro e segmentado (ex: "dentistas que faturam 30k/mes e querem escalar pra 100k")
- Trafego pago fortalece organico, nao mascara

Sinais de doenca:
- Comentarios vazios ("amei", "lindo")
- ICP indefinido ("todo mundo que quer melhorar de vida")
- Engajamento alto em conteudo viral mas zero em conteudo de venda

## Pilar 4: Differentiation (D)

Perguntas:

1. Por que esse perfil em vez do concorrente?
2. Qual o angulo unico?
3. Existe prova social proprietaria (cases, depoimentos, dados)?
4. Existe metodo proprio nomeado?

Sinais de saude:
- Metodo proprio com nome (ex: "Metodo SDR Avalanche")
- Cases recorrentes nos posts
- Posicionamento contraintuitivo

Sinais de doenca:
- Conteudo identico ao de 50 outros perfis do nicho
- Sem cases proprios
- Linguagem padrao do nicho ("dicas pra empreender", "mindset")

## Pricing pro cliente

Apos rodar o BMAD e gerar o dossie, esses sao os tickets sugeridos:

| Faixa de seguidores | Ticket sugerido | Margem operacional |
|---|---|---|
| Ate 50k | R$ 497 | > 99% |
| 50k a 500k | R$ 997 | > 99% |
| 500k a 1M | R$ 1.497 | > 99% |
| 1M+ | R$ 2.000+ | > 99% |

Custo de producao: ~US$0,025 por dossie (HikerAPI + Gemini).

## Diferenciais do dossie BMAD vs analise comum

1. **Dados reais**: HikerAPI retorna numeros oficiais (seguidores, engajamento, lista de posts).
2. **Plano executavel**: 30 dias com posts dia a dia, nao apenas conselhos vagos.
3. **Site cinematografico**: cliente recebe URL profissional no SUBDOMINIO DO ALUNO (USERNAME.DOMINIO_BASE, ex: joaodasilva.meunegocio.com.br), nao PDF.
4. **Meta numerica**: +10% seguidores em 30 dias por padrao, ou foco em vendas se pedido.
5. **Velocidade**: 3 a 5 minutos do @username ao link entregue.

## Como o aluno configura o dominio dele

A skill NAO usa o dominio do Denderson. Cada aluno usa o proprio dominio. O subdominio gerado e sempre `<USERNAME_DO_INSTAGRAM>.<DOMINIO_BASE>`, onde `DOMINIO_BASE` vem do `.env` do agente do aluno.

### Pre-requisitos do aluno

1. Ter um dominio registrado (ex: `meunegocio.com.br`, `clinicadrjoao.com`, qualquer um).
2. Ter o dominio gerenciado pelo Cloudflare (DNS apontado para nameservers do Cloudflare).
3. Ter conta no Vercel com um team/scope ativo.
4. Ter conta no GitHub (user ou organization).

### Variaveis que o aluno cadastra no /opt/naia-agent/.env

```bash
# Dominio raiz do aluno (sem http, sem subdominio)
DOMINIO_BASE=meunegocio.com.br

# Cloudflare (do aluno)
CLOUDFLARE_DNS_TOKEN=cfut_...    # token com permissao DNS Edit na zone
CLOUDFLARE_ZONE_ID=abc123...     # zone_id da DOMINIO_BASE no Cloudflare

# Vercel (do aluno)
VERCEL_TOKEN=vcp_...             # token de API
VERCEL_SCOPE=meunegocio-team     # nome do team/scope

# GitHub (do aluno)
GH_TOKEN=ghp_...                 # PAT com permissao repo
GH_OWNER=meunegocio-bot          # username ou org do GitHub
```

### Como o aluno consegue cada token

**Cloudflare DNS Token + Zone ID:**
1. Login em dash.cloudflare.com.
2. Selecionar o dominio `DOMINIO_BASE`.
3. No painel direito (Overview), copiar o `Zone ID` -> cola em `CLOUDFLARE_ZONE_ID`.
4. Em "My Profile" -> "API Tokens" -> "Create Token" -> template "Edit zone DNS" -> escopo apenas para a zone do `DOMINIO_BASE` -> copiar token -> cola em `CLOUDFLARE_DNS_TOKEN`.

**Vercel Token + Scope:**
1. Login em vercel.com -> Settings -> Tokens -> Create Token (escopo: full access).
2. `VERCEL_SCOPE` e o slug do team (visivel na URL `vercel.com/<scope>/...`).

**GitHub Token + Owner:**
1. Login em github.com -> Settings -> Developer settings -> Personal access tokens -> Fine-grained ou Classic.
2. Permissoes: `repo` (criar repos privados, push).
3. `GH_OWNER` e o username (ou org) que vai dono dos repos `dossie-<username>`.

### Validacao automatica no deploy-dossie.sh

O script valida na ordem:

1. Existe `DOMINIO_BASE`? Se nao -> aborta com instrucao.
2. Existe `CLOUDFLARE_DNS_TOKEN` e `CLOUDFLARE_ZONE_ID`? Se nao -> aborta.
3. Existe `VERCEL_TOKEN` e `VERCEL_SCOPE`? Se nao -> aborta.
4. Existe `GH_TOKEN` e `GH_OWNER`? Se nao -> aborta.

Se tudo OK, monta o FQDN como `${USERNAME_INSTAGRAM}.${DOMINIO_BASE}` e segue o pipeline.

### Exemplo concreto

Aluno: Joao da Silva. Dominio dele: `joaosilva.com.br`.

```bash
# .env do agente do Joao
DOMINIO_BASE=joaosilva.com.br
CLOUDFLARE_ZONE_ID=cf_zone_do_joaosilva_com_br
GH_OWNER=joaosilva-bot
VERCEL_SCOPE=joaosilva-team
```

Joao roda `analisa @amandagomes`. Resultado:
- Repo GitHub: `joaosilva-bot/dossie-amandagomes` (privado)
- Vercel project: `dossie-amandagomes` no scope `joaosilva-team`
- Dominio Vercel: `amandagomes.joaosilva.com.br`
- DNS Cloudflare: A `amandagomes` -> `76.76.21.21`, proxy OFF, na zone do `joaosilva.com.br`
- URL final entregue ao cliente: `https://amandagomes.joaosilva.com.br`

## Checklist mental antes de entregar

- [ ] Os 4 pilares B M A D foram cobertos no dossie?
- [ ] O plano de 30 dias tem 30 cards (um por dia)?
- [ ] A meta numerica esta explicita (+10% ou X reais)?
- [ ] O HTML carrega sem erro no Vercel?
- [ ] O `DOMINIO_BASE` do aluno foi lido com sucesso do .env?
- [ ] O dominio `USERNAME.DOMINIO_BASE` responde 200?
- [ ] O resumo executivo tem 5 bullets concretos?

Se algum item falhou, corrigir antes de entregar.

## Tom de voz

Sempre PT-BR, fluido, estrategico. Sem travessoes. Sem linguagem academica. Direto ao ponto, com numeros e CTAs concretos.

Exemplo de frase boa:
"Seu engajamento medio cai 47% nos posts de venda. A causa e que o angulo muda: voce vende como guru e seu publico esta acostumado com voce educando."

Exemplo de frase ruim:
"Observamos uma certa queda no engajamento dos posts comerciais que pode estar relacionada a fatores diversos."
