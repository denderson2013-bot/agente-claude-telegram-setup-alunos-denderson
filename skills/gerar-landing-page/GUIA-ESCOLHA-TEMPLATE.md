# Guia de Escolha de Template

10 templates-base para landing pages. Use esta arvore de decisao pra escolher o ideal.

> **NUNCA escolha sozinho.** Pergunte primeiro ao dono qual ele prefere (ver fluxo na SKILL.md). Este guia serve para SUGERIR opcoes quando ele nao souber decidir.

---

## Arvore de decisao rapida

**O dono quer o que?**

### 1. Vender curso/mentoria/ebook com video VSL
=> `infoproduto-vsl.html` (template 4)
- Video hero com headline grande
- Estrutura PASO completa (Problema, Agitacao, Solucao, Oferta)
- 6-8 secoes (problema, agitacao, solucao, prova social, oferta, garantia, FAQ, CTA final)
- Paleta vibrante (laranja+preto OU roxo+preto)
- CTA persistente (sticky)

### 2. Vender servico de agencia (B2B premium)
=> `agencia-premium.html` (template 5)
- Hero com video bg
- Cases grid em destaque
- Depoimentos de clientes
- Paleta luxo (preto+dourado+champagne)
- Fonts serif display + sans body
- Form de contato

### 3. App, software, SaaS, ferramenta
=> `saas-clean.html` (template 8)
- Hero com mockup do produto
- Features grid
- Pricing 3 colunas (Free / Pro / Enterprise)
- Logos de integracoes
- FAQ
- Paleta tech (azul+branco+grafite)

### 4. Evento ao vivo, workshop, imersao com data
=> `evento-presencial.html` (template 6)
- Contagem regressiva
- Localizacao com mapa
- Programacao em timeline
- Palestrantes em grid
- Ingressos com 3 tiers (Lote 1/2/3)
- Paleta urgencia (vermelho+preto OU verde-neon+preto)

### 5. Captura simples (PDF gratis, webinar, waitlist)
=> `lead-capture-minimal.html` (template 3)
- Formulario centro de tela
- Hero curto e direto
- Validacao + OTP opcional
- Footer minimalista
- Paleta clean (branco+preto+1 cor de destaque)

### 6. Lancamento perpetuo com escassez
=> `lancamento-perpetuo.html` (template 7)
- Contadores
- Lista de 10+ bonus
- Garantia 7 dias
- Depoimentos em video
- CTA pulsante
- Paleta neon (rosa+ciano+preto)

### 7. Consultor, autor, palestrante, autoridade pessoal
=> `consultor-autoridade.html` (template 9)
- Hero com foto vertical do consultor
- Sessao de autoridade (livros, podcasts, midia, eventos)
- Metodologia em 5 passos
- CTA "marcar consulta"
- Paleta minimalista (branco+preto+vermelho)
- Tipografia editorial (Cormorant + Inter)

### 8. Landing premium tech "diferentona" (sem ser obvia)
=> `nyos-editorial.html` (template 1)
- Tom editorial/Bloomberg
- Paleta cinza+preto+dourado
- Foco em autoridade e posicionamento
- Sem urgencia agressiva

### 9. Vibe hacker/cyberpunk (eventos tech, lote 0/1/2)
=> `matrix-terminal.html` (template 2)
- Matrix Rain de fundo
- LED Border rotativa nos cards
- Vibe "vai esgotar"
- Neon green sobre preto
- JetBrains Mono

### 10. Casamento, formatura, evento social, RSVP
=> `wedding-event.html` (template 10)
- Hero com foto romantica
- Contagem regressiva
- Programacao
- RSVP form
- Hospedagem/transporte info
- Paleta soft (rose+champagne+sage)

---

## Tabela comparativa

| # | Template | Nicho | Paleta | Fonts | Conversao alvo |
|---|---|---|---|---|---|
| 1 | nyos-editorial | Premium tech | Cinza+preto+dourado | Tobias + Sohne | 1-3% (autoridade) |
| 2 | matrix-terminal | Tech/IA event | Neon green+preto | JetBrains Mono | 5-10% (urgencia) |
| 3 | lead-capture-minimal | Lead magnet | Branco+accent | Inter | 20-40% (captura) |
| 4 | infoproduto-vsl | Curso/ebook | Laranja+preto | Bebas + Inter | 2-5% (VSL) |
| 5 | agencia-premium | B2B servico | Preto+dourado+champ | Playfair + Inter | 1-2% (high ticket) |
| 6 | evento-presencial | Workshop ao vivo | Vermelho+preto | Bebas + Inter | 3-8% (ingresso) |
| 7 | lancamento-perpetuo | Curso evergreen | Rosa+ciano+preto | Orbitron + Inter | 2-6% (escassez) |
| 8 | saas-clean | App/software | Azul+branco | Inter | 5-15% (trial) |
| 9 | consultor-autoridade | Consultor pessoa | Branco+preto+vermelho | Cormorant + Inter | 1-3% (consulta) |
| 10 | wedding-event | Evento social | Rose+champagne+sage | Cormorant + Lato | 60-80% (RSVP) |

---

## Customizacoes mais pedidas

### Mudar paleta de cores
Em todos os templates, as cores estao em variaveis CSS no `:root`. Trocar so essas variaveis ja muda toda a pagina.

### Adicionar Pixel + UTM
Todos os templates tem placeholder `{{PIXEL_HEAD}}` no `<head>` e `{{UTM_SCRIPT}}` antes do `</body>`. Substituir pelos snippets reais.

### Adicionar checkout
- Hotmart, Stripe, Asaas: `{{CTA_URL}}` aponta pra URL do checkout
- Outros: trocar pelo link direto de pagamento

### Adicionar video VSL
- Tella, YouTube, Vimeo, Loom: cole o iframe no placeholder `{{VIDEO_EMBED}}`

### Adicionar countdown
Templates 6 (evento-presencial) e 7 (lancamento-perpetuo) ja tem JS pronto pra contagem regressiva. Editar a data alvo no `<script>` no fim do HTML.

### Adicionar formulario de contato
Templates 5 (agencia-premium) e 6 (evento-presencial) tem forms prontos. Configurar `action="..."` pra Formspree, Notion API, GHL webhook ou backend proprio.

---

## Regra de ouro

Quando o dono nao soube decidir, faca 1 unica pergunta:

> "E captura de lead, venda de produto, evento com data ou apresentacao de servico/autoridade?"

A resposta filtra os templates em 2 segundos:
- Captura de lead -> 3 ou 7
- Venda de produto -> 4 ou 7
- Evento com data -> 6 ou 10
- Servico / autoridade -> 5 ou 9
- Tech / SaaS -> 8 ou 1
- "So quero algo bonito e moderno" -> 1 ou 2
