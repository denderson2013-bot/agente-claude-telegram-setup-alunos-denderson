#!/usr/bin/env bash
# deploy-landing.sh
# Deploy automatizado de uma landing page:
#   1. valida que o template foi renderizado (index.html sem placeholders)
#   2. git init + commit
#   3. cria repo privado no GitHub do aluno
#   4. push
#   5. deploy na Vercel
#   6. adiciona dominio custom <SLUG>.<DOMINIO_BASE>
#   7. cria registro DNS A no Cloudflare apontando para 76.76.21.21
#
# REQUISITOS:
#  - .env carregado (em /opt/naia-agent/.env OU ./.env)
#  - Vars obrigatorias: GH_TOKEN, GH_USER, VERCEL_TOKEN, VERCEL_SCOPE,
#                       CLOUDFLARE_DNS_TOKEN, CLOUDFLARE_ZONE_ID, DOMINIO_BASE
#  - Tools: git, gh (GitHub CLI), vercel (Vercel CLI), curl, jq
#
# USO:
#   bash deploy-landing.sh --slug "meu-evento" --repo "lp-meu-evento" --dir "/tmp/meu-evento"
#
# Subdominio final: meu-evento.${DOMINIO_BASE}
#   ex: se DOMINIO_BASE=agencia.com -> meu-evento.agencia.com

set -Eeuo pipefail

# ---------------------------------------------------------------
# 1) Carregar .env
# ---------------------------------------------------------------
ENV_LOADED=0
for ENV_FILE in "/opt/naia-agent/.env" "./.env" "$(dirname "$0")/../../.env"; do
  if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    . "$ENV_FILE"
    set +a
    ENV_LOADED=1
    echo "[deploy-landing] .env carregado de $ENV_FILE"
    break
  fi
done

if [[ "$ENV_LOADED" -eq 0 ]]; then
  echo "[deploy-landing] AVISO: nenhum .env encontrado. Continuando com vars do ambiente." >&2
fi

# ---------------------------------------------------------------
# 2) Validacoes
# ---------------------------------------------------------------
fail() { echo "[deploy-landing] ERRO: $1" >&2; exit 1; }

require_var() {
  local var_name="$1"
  if [[ -z "${!var_name:-}" ]]; then
    fail "Variavel \$$var_name esta vazia. Configure no .env do agente."
  fi
}

# DOMINIO_BASE e o mais critico
if [[ -z "${DOMINIO_BASE:-}" ]]; then
  cat >&2 <<'MSG'
[deploy-landing] ERRO CRITICO: $DOMINIO_BASE nao esta configurado.

Esse e o dominio raiz onde os subdominios das landing pages serao publicados.
Exemplo: se DOMINIO_BASE=agencia.com.br, a LP vai pra:
  https://meu-evento.agencia.com.br

PARA CONFIGURAR:

1. Edite /opt/naia-agent/.env (ou o .env do seu projeto) e adicione:
     DOMINIO_BASE=seunegocio.com.br

2. No Cloudflare, abra a zone desse dominio e copie o "Zone ID":
     CLOUDFLARE_ZONE_ID=<zone_id>

3. Crie um API Token Cloudflare com permissao "Zone.DNS: Edit" nessa zone:
     CLOUDFLARE_DNS_TOKEN=<token>

4. Garanta que GitHub e Vercel ja estao configurados:
     GH_TOKEN=<personal_access_token>
     GH_USER=<seu_user_github>
     VERCEL_TOKEN=<token_vercel>
     VERCEL_SCOPE=<slug_time_vercel>

5. Roda o script de novo.
MSG
  exit 1
fi

require_var GH_TOKEN
require_var GH_USER
require_var VERCEL_TOKEN
require_var VERCEL_SCOPE
require_var CLOUDFLARE_DNS_TOKEN
require_var CLOUDFLARE_ZONE_ID

# ---------------------------------------------------------------
# 3) Argumentos
# ---------------------------------------------------------------
SLUG=""
REPO=""
DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --slug)   SLUG="$2"; shift 2 ;;
    --repo)   REPO="$2"; shift 2 ;;
    --dir)    DIR="$2";  shift 2 ;;
    --domain) SLUG="$2"; shift 2 ;; # alias
    --nome)   SLUG="$2"; shift 2 ;; # alias
    *) echo "[deploy-landing] arg desconhecido: $1" >&2; exit 1 ;;
  esac
done

[[ -z "$SLUG" ]] && fail "--slug e obrigatorio (subdominio, ex: meu-evento)"
[[ -z "$REPO" ]] && REPO="lp-${SLUG}"
[[ -z "$DIR"  ]] && DIR="/tmp/${SLUG}"

[[ ! -d "$DIR" ]] && fail "Diretorio $DIR nao existe."
[[ ! -f "$DIR/index.html" ]] && fail "$DIR/index.html nao existe (renderize o template antes do deploy)."

# Sanity check: nao deve existir placeholder nao-resolvido
if grep -qE '\{\{[A-Z_][A-Z0-9_]*\}\}' "$DIR/index.html"; then
  echo "[deploy-landing] AVISO: $DIR/index.html ainda tem placeholders {{...}} nao resolvidos:"
  grep -oE '\{\{[A-Z_][A-Z0-9_]*\}\}' "$DIR/index.html" | sort -u | sed 's/^/    /'
  echo "Continue (Ctrl+C pra abortar e renderizar antes)."
fi

SUBDOMAIN="${SLUG}.${DOMINIO_BASE}"
echo "[deploy-landing] subdomain final: ${SUBDOMAIN}"

# ---------------------------------------------------------------
# 4) Git + GitHub (privado)
# ---------------------------------------------------------------
cd "$DIR"

if [[ ! -d ".git" ]]; then
  git init -b master
  git add .
  git -c user.email="${GH_EMAIL:-deploy@local}" -c user.name="${GH_USER}" commit -m "Landing page ${SLUG}"
fi

if ! gh repo view "${GH_USER}/${REPO}" >/dev/null 2>&1; then
  echo "[deploy-landing] criando repo privado ${GH_USER}/${REPO}"
  GH_TOKEN="$GH_TOKEN" gh repo create "${GH_USER}/${REPO}" --private --source=. --push
else
  echo "[deploy-landing] repo ja existe, push"
  git remote remove origin 2>/dev/null || true
  git remote add origin "https://${GH_TOKEN}@github.com/${GH_USER}/${REPO}.git"
  git push -u origin master
fi

# ---------------------------------------------------------------
# 5) Vercel
# ---------------------------------------------------------------
echo "[deploy-landing] deploy Vercel (prod)"
vercel --token "$VERCEL_TOKEN" --yes --prod --scope "$VERCEL_SCOPE" >/dev/null

echo "[deploy-landing] vinculando dominio ${SUBDOMAIN}"
vercel domains add "${SUBDOMAIN}" "${REPO}" \
  --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" 2>/dev/null \
  || echo "[deploy-landing] dominio ja vinculado ou falhou; segue para DNS"

# ---------------------------------------------------------------
# 6) Cloudflare DNS
# ---------------------------------------------------------------
echo "[deploy-landing] criando registro DNS A ${SUBDOMAIN} -> 76.76.21.21"

DNS_RESP=$(curl -s -X POST \
  "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_DNS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"${SLUG}\",\"content\":\"76.76.21.21\",\"ttl\":1,\"proxied\":false}")

if echo "$DNS_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
  echo "[deploy-landing] DNS criado com sucesso"
else
  ERR=$(echo "$DNS_RESP" | jq -r '.errors[0].message // "erro desconhecido"')
  if echo "$ERR" | grep -qi "already exists"; then
    echo "[deploy-landing] DNS ja existia (ok)"
  else
    fail "Cloudflare DNS falhou: $ERR"
  fi
fi

# ---------------------------------------------------------------
# 7) Resumo
# ---------------------------------------------------------------
cat <<RESUMO

[deploy-landing] CONCLUIDO

  Slug:        ${SLUG}
  Repo:        https://github.com/${GH_USER}/${REPO}
  Producao:    https://${SUBDOMAIN}
  DNS zone:    ${CLOUDFLARE_ZONE_ID}
  Aguarde ate 60s para propagar o DNS antes de abrir.

RESUMO
