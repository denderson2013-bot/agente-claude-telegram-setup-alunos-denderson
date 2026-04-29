#!/bin/bash
#
# deploy-proposta.sh
# Faz deploy completo de uma proposta: GitHub privado + Vercel + Cloudflare DNS.
#
# Uso:
#   bash deploy-proposta.sh --nome "joao-silva" --repo "proposta-joao-silva" --domain "joaosilva"
#
# Variaveis de ambiente obrigatorias (setar antes de rodar):
#   GH_TOKEN              - Personal Access Token do GitHub (escopo repo)
#   GH_USER               - Usuario/org do GitHub do aluno
#   VERCEL_TOKEN          - Token da Vercel
#   VERCEL_SCOPE          - Scope/team da Vercel
#   CLOUDFLARE_DNS_TOKEN  - Token do Cloudflare com permissao DNS:Edit
#   CLOUDFLARE_ZONE_ID    - ID da zone no Cloudflare (zone do dominio do aluno)
#   DOMINIO_BASE          - dominio base do aluno (ex: meunegocio.com.br)
#

set -e

# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------
log() { printf "\n[deploy-proposta] %s\n" "$1"; }
die() { printf "\n[ERRO] %s\n" "$1" >&2; exit 1; }

require_env() {
  local var="$1"
  if [ -z "${!var}" ]; then
    die "Variavel de ambiente $var nao definida. Configure antes de rodar."
  fi
}

# ------------------------------------------------------------
# Parse args
# ------------------------------------------------------------
NOME=""
REPO=""
DOMAIN=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --nome)   NOME="$2";   shift 2 ;;
    --repo)   REPO="$2";   shift 2 ;;
    --domain) DOMAIN="$2"; shift 2 ;;
    *) die "Argumento desconhecido: $1" ;;
  esac
done

[ -z "$NOME" ]   && die "Faltou --nome (slug do cliente, ex: joao-silva)"
[ -z "$REPO" ]   && die "Faltou --repo (ex: proposta-joao-silva)"
[ -z "$DOMAIN" ] && die "Faltou --domain (subdominio, ex: joaosilva)"

# ------------------------------------------------------------
# Valida envs
# ------------------------------------------------------------
require_env GH_TOKEN
require_env GH_USER
require_env VERCEL_TOKEN
require_env VERCEL_SCOPE
require_env CLOUDFLARE_DNS_TOKEN
require_env CLOUDFLARE_ZONE_ID
require_env DOMINIO_BASE

# ------------------------------------------------------------
# Etapa 1 — Verifica que existe index.html
# ------------------------------------------------------------
if [ ! -f "index.html" ]; then
  die "index.html nao encontrado no diretorio atual. Gere a proposta antes de rodar este script."
fi

log "Iniciando deploy da proposta $NOME"

# ------------------------------------------------------------
# Etapa 2 — Git init e push pro GitHub privado
# ------------------------------------------------------------
log "Etapa 1/3: Criando repo GitHub privado $GH_USER/$REPO"

if [ ! -d ".git" ]; then
  git init -b main
fi

git add .
git commit -m "Proposta $NOME" || log "Nada a commitar (provavelmente ja commitado)"

# Configura remote com token
REMOTE_URL="https://${GH_USER}:${GH_TOKEN}@github.com/${GH_USER}/${REPO}.git"

# Cria repo via API se nao existir
HTTP_STATUS=$(curl -s -o /tmp/gh_create.json -w "%{http_code}" \
  -X POST "https://api.github.com/user/repos" \
  -H "Authorization: token ${GH_TOKEN}" \
  -H "Accept: application/vnd.github.v3+json" \
  -d "{\"name\":\"${REPO}\",\"private\":true,\"description\":\"Proposta comercial para ${NOME}\"}")

if [ "$HTTP_STATUS" = "201" ]; then
  log "Repo criado com sucesso"
elif [ "$HTTP_STATUS" = "422" ]; then
  log "Repo ja existia, seguindo"
else
  cat /tmp/gh_create.json
  die "Falha ao criar repo no GitHub (HTTP $HTTP_STATUS)"
fi

# Adiciona remote e da push
git remote remove origin 2>/dev/null || true
git remote add origin "$REMOTE_URL"
git push -u origin main --force

log "GitHub OK"

# ------------------------------------------------------------
# Etapa 3 — Deploy na Vercel
# ------------------------------------------------------------
log "Etapa 2/3: Deploy na Vercel"

if ! command -v vercel >/dev/null 2>&1; then
  die "CLI 'vercel' nao encontrada. Instale com: npm i -g vercel"
fi

vercel --token "$VERCEL_TOKEN" --yes --prod --scope "$VERCEL_SCOPE" --name "$REPO"

log "Adicionando dominio customizado"

vercel domains add "${DOMAIN}.${DOMINIO_BASE}" "$REPO" --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" || log "Dominio talvez ja apontado, seguindo"

log "Vercel OK"

# ------------------------------------------------------------
# Etapa 4 — DNS no Cloudflare
# ------------------------------------------------------------
log "Etapa 3/3: Apontando DNS no Cloudflare"

DNS_RESPONSE=$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_DNS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"${DOMAIN}\",\"content\":\"76.76.21.21\",\"ttl\":1,\"proxied\":false}")

SUCCESS=$(echo "$DNS_RESPONSE" | grep -o '"success":[^,]*' | head -1 | cut -d: -f2 | tr -d ' ')

if [ "$SUCCESS" = "true" ]; then
  log "DNS criado com sucesso"
elif echo "$DNS_RESPONSE" | grep -q "already exists"; then
  log "DNS ja existia, seguindo"
else
  echo "$DNS_RESPONSE"
  log "Aviso: criacao de DNS pode ter falhado. Verifique manualmente no Cloudflare."
fi

# ------------------------------------------------------------
# Final
# ------------------------------------------------------------
log "DEPLOY COMPLETO"
log "URL final: https://${DOMAIN}.${DOMINIO_BASE}"
log "Repo: https://github.com/${GH_USER}/${REPO}"
log "Aguarde 1 a 2 minutos para o DNS propagar."

exit 0
