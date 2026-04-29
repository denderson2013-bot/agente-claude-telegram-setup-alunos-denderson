---
name: gerar-landing-page
description: Gera uma landing page profissional escolhendo entre 10 templates pre-prontos, cria repo GitHub privado, deploy na Vercel e DNS no Cloudflare apontando para o DOMINIO DO ALUNO. Acionar quando o dono pedir "cria uma landing page", "faz uma LP", "monta uma pagina de vendas", "preciso de uma LP", "landing page pra X", "pagina pro produto Y", "criar LP", "fazer landing", "gerar landing", "criar pagina de captura", "criar pagina de evento", "criar pagina de lancamento", "landing pra capturar email", "landing terminal", "landing matrix", "landing editorial", "landing de casamento", "landing pra meu SaaS".
type: skill
---

# Skill: gerar-landing-page

Pipeline para gerar uma landing page completa com base em 1 dos 10 templates disponiveis. Deploy automatico em `<NOME_PROJETO>.${DOMINIO_BASE}` (dominio do proprio aluno, NAO denderson.com).

---

## Fluxo obrigatorio (NUNCA pular a pergunta inicial)

1. Quando o dono pedir "cria uma landing page", "faz uma LP", "pagina de vendas", "monta uma landing", "preciso de uma pagina pra X", **PERGUNTE ANTES de gerar:**

   ```
   Qual modelo voce quer? Tenho 10 templates:

   1.  NYOS Editorial Tech       — cinza+dourado, vibe revista
   2.  Matrix Terminal           — neon green, terminal scrolling
   3.  Lead Capture Minimal      — formulario centro, captura
   4.  Infoproduto VSL           — video hero + estrutura PASO
   5.  Agencia Premium           — luxo, preto+dourado, cases
   6.  Evento Presencial         — urgencia, contagem regressiva
   7.  Lancamento Perpetuo       — neon, escassez + bonus
   8.  SaaS Clean                — azul tech, mockup produto
   9.  Consultor Autoridade      — editorial, autoridade
   10. Wedding/Evento Social     — rose/champagne, RSVP

   Qual prefere? Ou descreve o que voce quer e eu sugiro?
   ```

2. **So depois da resposta, gere a LP com o template escolhido.**

3. Se o dono nao souber qual usar, **sugira 1 ou 2** com base no que ele descrever (nicho, objetivo, publico, ticket). Use o guia abaixo:

   | Se o produto for...                                 | Sugira                                |
   |-----------------------------------------------------|---------------------------------------|
   | Curso, ebook, mentoria com VSL                      | 4 (Infoproduto VSL) ou 7 (Lancamento) |
   | Servico de agencia (B2B premium)                    | 5 (Agencia Premium)                   |
   | App, software, SaaS, ferramenta                     | 8 (SaaS Clean)                        |
   | Evento ao vivo, workshop, imersao com data          | 6 (Evento Presencial)                 |
   | Captura de lead simples (PDF gratis, webinar)       | 3 (Lead Capture Minimal)              |
   | Consultor, autor, palestrante (autoridade pessoal)  | 9 (Consultor Autoridade)              |
   | Landing tech moderna sem ser obvia                  | 1 (NYOS) ou 2 (Matrix)                |
   | Casamento, formatura, evento social, RSVP           | 10 (Wedding/Evento Social)            |

4. Se o dono pedir "manda os 10 modelos pra eu ver", liste com mini descricao + paleta + fonts (sem gerar nada ainda).

5. NUNCA gerar uma LP sem confirmar o template. Atalho de OK: o dono manda "vai 4" ou "usa o 8" e ai sim gera.

---

## Os 10 templates disponiveis

Localizacao: `skills/gerar-landing-page/templates/<nome>.html`

| # | Arquivo                         | Vibe                              | Paleta                          | Fonts                                  |
|---|---------------------------------|-----------------------------------|---------------------------------|----------------------------------------|
| 1 | `nyos-editorial.html`           | Editorial tech, revista digital   | Cinza+preto+dourado             | Tobias (display) + Sohne (body)        |
| 2 | `matrix-terminal.html`          | Cyberpunk terminal, hacker chic   | Neon green sobre preto          | JetBrains Mono                         |
| 3 | `lead-capture-minimal.html`     | Captura clean, foco no formulario | Branco+preto+1 cor de destaque  | Inter                                  |
| 4 | `infoproduto-vsl.html`          | VSL longa, PASO completo          | Laranja+preto OU roxo+preto     | Bebas Neue (display) + Inter (body)    |
| 5 | `agencia-premium.html`          | Luxo B2B, cases destacados        | Preto+dourado+champagne         | Playfair Display + Inter               |
| 6 | `evento-presencial.html`        | Urgencia, contagem regressiva     | Vermelho+preto OU verde+preto   | Bebas Neue + Inter                     |
| 7 | `lancamento-perpetuo.html`      | Neon escassez + bonus em pilha    | Rosa+ciano+preto                | Orbitron + Inter                       |
| 8 | `saas-clean.html`               | Tech profissional, mockup hero    | Azul+branco+grafite             | Inter (tudo)                           |
| 9 | `consultor-autoridade.html`     | Editorial autoridade pessoal      | Branco+preto+vermelho           | Cormorant Garamond + Inter             |
| 10| `wedding-event.html`            | Soft romantico, RSVP form         | Rose+champagne+sage             | Cormorant Garamond + Lato              |

Cada template e auto-contido (Tailwind CDN ou CSS inline). Os placeholders variam por template:

### Placeholders dos 7 templates novos (4, 5, 6, 7, 8, 9, 10)
- `{{TITULO}}` — titulo da pagina (`<title>` + meta og)
- `{{HEADLINE}}` — headline principal do hero
- `{{SUBHEADLINE}}` — sub-titulo do hero
- `{{HERO_IMG}}` — URL da imagem do hero
- `{{CTA_TEXT}}` — texto do botao principal
- `{{CTA_URL}}` — link do CTA (checkout, formulario, calendario)
- `{{COR_PRIMARIA}}` — cor primaria (override)
- `{{COR_SECUNDARIA}}` — cor secundaria (override)
- `{{ANO}}` — ano (footer)
- `{{NOME_MARCA}}` — nome da marca/empresa
- `{{TELEFONE}}` — WhatsApp/telefone (opcional)
- `{{EMAIL}}` — email de contato
- `{{PIXEL_HEAD}}` — script de pixel/GA no `<head>`
- `{{UTM_SCRIPT}}` — tracker UTM antes de `</body>`

### Placeholders dos 3 templates legados (1, 2, 3)

Estes 3 (NYOS Editorial, Matrix Terminal, Lead Capture Minimal) usam um SET DIFERENTE de placeholders, herdado do projeto original. NAO confundir com os novos.

- `{{TITULO_PRINCIPAL}}` — equivalente a `{{HEADLINE}}`
- `{{TITULO_DESTAQUE}}` — destaque dentro do titulo
- `{{SUBTITULO}}` — equivalente a `{{SUBHEADLINE}}`
- `{{CTA_TEXTO}}` — equivalente a `{{CTA_TEXT}}`
- `{{CTA_LINK}}` — equivalente a `{{CTA_URL}}`
- `{{NOME_PRODUTO}}` — equivalente a `{{NOME_MARCA}}`
- `{{ANO}}`, `{{PIXEL_HEAD}}`, `{{UTM_SCRIPT}}` — iguais

Cada template tem placeholders adicionais especificos (lotes, FAQ, modulos, beneficios, etc). Eles estao documentados dentro do proprio HTML como comentarios `<!-- placeholder: {{NOME}} - descricao -->`. Antes de gerar, abra o template e leia.

> **Recomendacao:** quando o agente renderizar o HTML, leia todos os `{{...}}` do template escolhido com `grep -oE '\{\{[A-Z_][A-Z0-9_]*\}\}' <template>.html | sort -u` e preencha cada um. Se sobrar `{{...}}` no HTML final, abortar deploy (o script `deploy-landing.sh` ja avisa).

---

## Pipeline de execucao

### Etapa 1 — Confirmar template
Pergunta + resposta do dono (descrita acima). NAO pular.

### Etapa 2 — Coletar dados do projeto
Perguntar (o que faltar):
- Nome do projeto/produto
- Slug do subdominio (ex: `lancamento-x`)
- Headline + sub-headline
- CTA (texto + URL)
- 3-5 bullets/beneficios
- Tem VSL? Link do video?
- Cor de destaque preferida (se nao quiser o padrao do template)

### Etapa 3 — Montar HTML
Copiar `templates/<escolhido>.html` para `/tmp/<slug>/index.html` e substituir todos os placeholders.

### Etapa 4 — Validar `DOMINIO_BASE`
Antes de qualquer deploy, validar que `DOMINIO_BASE` existe no `.env` do agente. Se vazio, parar e mostrar como configurar.

### Etapa 5 — Deploy completo
Rodar `scripts/deploy-landing.sh`:

```bash
bash skills/gerar-landing-page/scripts/deploy-landing.sh \
  --slug "lancamento-x" \
  --repo "lp-lancamento-x" \
  --dir  "/tmp/lancamento-x"
```

URL final: `https://lancamento-x.${DOMINIO_BASE}`.

### Etapa 6 — Entregar
Devolver pro dono:
- URL publica
- Repo privado no GitHub
- Lembrar que pode editar o repo pra ajustar copy/imagens

---

## Variaveis de ambiente

Mesmas da skill de proposta-comercial (compartilhadas no `.env` do agente):
- `GH_TOKEN`, `GH_USER`
- `VERCEL_TOKEN`, `VERCEL_SCOPE`
- `CLOUDFLARE_DNS_TOKEN`, `CLOUDFLARE_ZONE_ID`
- `DOMINIO_BASE` (OBRIGATORIO — dominio raiz do aluno)

Se `DOMINIO_BASE` estiver vazio, o agente para e responde:

> Falta configurar `DOMINIO_BASE` no .env do agente. Te explico como:
> 1. Edite `/opt/naia-agent/.env`
> 2. Adicione `DOMINIO_BASE=seunegocio.com.br`
> 3. Configure tambem `CLOUDFLARE_ZONE_ID` (zone do Cloudflare desse dominio) e `CLOUDFLARE_DNS_TOKEN` (token DNS:Edit)
> 4. Reinicie o agente e tente de novo

---

## Regras criticas

1. NUNCA gerar a LP sem confirmar qual dos 10 templates usar
2. NUNCA usar `denderson.com` hardcoded — sempre `${DOMINIO_BASE}`
3. NUNCA usar travessoes na copy
4. SEMPRE PT-BR com acentuacao completa
5. SEMPRE repo privado
6. Imagens/videos do hero: sempre URL HTTPS valida
7. Mobile first: todos os templates ja sao responsivos
8. Performance: Tailwind via CDN OK no MVP, otimizar quando virar producao
9. Lighthouse alvo: 85+ em Performance e 95+ em Accessibility
10. Sem Google Analytics/Meta Pixel hardcoded no template; o aluno coloca depois

---

## Erros que nao podem repetir

1. Gerar LP sem perguntar o template
2. Usar dominio que nao e do aluno
3. Misturar paletas de templates diferentes na mesma LP
4. Esquecer placeholder e deixar `{{HEADLINE}}` no HTML final
5. Subir repo publico (deve ser sempre privado)
6. CTA quebrado (URL vazia ou apontando pra `#`)
7. Imagem do hero faltando (deixar placeholder)

---

## Arquivos auxiliares

- `templates/nyos-editorial.html`
- `templates/matrix-terminal.html`
- `templates/lead-capture-minimal.html`
- `templates/infoproduto-vsl.html`
- `templates/agencia-premium.html`
- `templates/evento-presencial.html`
- `templates/lancamento-perpetuo.html`
- `templates/saas-clean.html`
- `templates/consultor-autoridade.html`
- `templates/wedding-event.html`
- `scripts/deploy-landing.sh`
- `GUIA-ESCOLHA-TEMPLATE.md` (guia rapido para escolher)
