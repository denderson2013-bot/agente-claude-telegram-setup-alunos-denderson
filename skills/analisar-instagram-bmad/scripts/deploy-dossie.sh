#!/usr/bin/env bash
#
# deploy-dossie.sh
#
# Faz deploy do dossie BMAD para <USERNAME>.<DOMINIO_BASE> usando o dominio do ALUNO.
#
# Pre-requisitos: variaveis no .env do agente (/opt/naia-agent/.env), carregadas no ambiente.
#   DOMINIO_BASE             dominio raiz do aluno (ex: meunegocio.com.br)
#   CLOUDFLARE_DNS_TOKEN     token Cloudflare DNS Edit do aluno
#   CLOUDFLARE_ZONE_ID       zone_id da DOMINIO_BASE no Cloudflare do aluno
#   VERCEL_TOKEN             token Vercel do aluno
#   VERCEL_SCOPE             scope/team Vercel do aluno
#   GH_TOKEN                 PAT GitHub do aluno
#   GH_OWNER                 user/org GitHub do aluno (dono dos repos dossie-*)
#
# Uso:
#   ./deploy-dossie.sh <username_instagram> <pasta_com_index_html>
#
# Exemplo:
#   ./deploy-dossie.sh amandagomes /tmp/dossie-amandagomes
#   -> publica em https://amandagomes.<DOMINIO_BASE>

set -euo pipefail

USERNAME_INSTAGRAM="${1:-}"
PASTA_DOSSIE="${2:-}"

if [[ -z "$USERNAME_INSTAGRAM" || -z "$PASTA_DOSSIE" ]]; then
  echo "Uso: $0 <username_instagram> <pasta_com_index_html>" >&2
  exit 2
fi

# ----------------------------------------------------------------------------
# 1) Carregar .env do agente (se existir) e validar variaveis obrigatorias
# ----------------------------------------------------------------------------

ENV_FILE="${NAIA_ENV_FILE:-/opt/naia-agent/.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi

abort() {
  echo "" >&2
  echo "ERRO: $1" >&2
  echo "" >&2
  echo "$2" >&2
  exit 1
}

if [[ -z "${DOMINIO_BASE:-}" ]]; then
  abort \
    "Falta configurar DOMINIO_BASE no $ENV_FILE." \
    "Te explico como:
  1) Edita $ENV_FILE
  2) Adiciona a linha: DOMINIO_BASE=seudominio.com.br (sem http, sem subdominio)
  3) Tambem cadastra: CLOUDFLARE_DNS_TOKEN, CLOUDFLARE_ZONE_ID, VERCEL_TOKEN, VERCEL_SCOPE, GH_TOKEN, GH_OWNER
  4) Salva e roda novamente.
Ver passo a passo completo em PLAYBOOK-BMAD.md secao 'Como o aluno configura o dominio dele'."
fi

for var in CLOUDFLARE_DNS_TOKEN CLOUDFLARE_ZONE_ID VERCEL_TOKEN VERCEL_SCOPE GH_TOKEN GH_OWNER; do
  if [[ -z "${!var:-}" ]]; then
    abort \
      "Falta configurar $var no $ENV_FILE." \
      "Adiciona $var=... no $ENV_FILE e roda novamente.
Ver PLAYBOOK-BMAD.md secao 'Como o aluno configura o dominio dele'."
  fi
done

if [[ ! -d "$PASTA_DOSSIE" ]]; then
  abort \
    "Pasta do dossie nao existe: $PASTA_DOSSIE" \
    "Rode o analyze.py primeiro pra gerar o /tmp/dossie-${USERNAME_INSTAGRAM}/index.html."
fi

if [[ ! -f "$PASTA_DOSSIE/index.html" ]]; then
  abort \
    "Nao achei index.html em $PASTA_DOSSIE." \
    "Verifica o output do analyze.py."
fi

# ----------------------------------------------------------------------------
# 2) Montar variaveis derivadas
# ----------------------------------------------------------------------------

FQDN="${USERNAME_INSTAGRAM}.${DOMINIO_BASE}"
REPO_NAME="dossie-${USERNAME_INSTAGRAM}"
PROJECT_NAME="$REPO_NAME"

echo "==> Deploy do dossie de @${USERNAME_INSTAGRAM}"
echo "    DOMINIO_BASE   : $DOMINIO_BASE"
echo "    FQDN final     : $FQDN"
echo "    GH owner       : $GH_OWNER"
echo "    Vercel scope   : $VERCEL_SCOPE"
echo "    Pasta dossie   : $PASTA_DOSSIE"

cd "$PASTA_DOSSIE"

# ----------------------------------------------------------------------------
# 3) Git init + commit + push pro GitHub do aluno (privado)
# ----------------------------------------------------------------------------

if [[ ! -d ".git" ]]; then
  echo "==> Inicializando repo git"
  git init -q
  git checkout -q -b master 2>/dev/null || git checkout -q -b main
fi

git add -A
git -c user.email="bot@${DOMINIO_BASE}" -c user.name="${GH_OWNER}" commit -q -m "Dossie BMAD @${USERNAME_INSTAGRAM}" || true

echo "==> Criando repo privado ${GH_OWNER}/${REPO_NAME} no GitHub do aluno"
GH_TOKEN_ENV="$GH_TOKEN" gh auth status >/dev/null 2>&1 || {
  echo "$GH_TOKEN" | gh auth login --with-token >/dev/null 2>&1
}

if ! gh repo view "${GH_OWNER}/${REPO_NAME}" >/dev/null 2>&1; then
  gh repo create "${GH_OWNER}/${REPO_NAME}" --private --source=. --push -d "Dossie BMAD do Instagram @${USERNAME_INSTAGRAM}"
else
  echo "    Repo ja existe, fazendo push"
  git remote add origin "https://${GH_TOKEN}@github.com/${GH_OWNER}/${REPO_NAME}.git" 2>/dev/null || true
  git push -u origin HEAD --force
fi

# ----------------------------------------------------------------------------
# 4) Deploy Vercel no scope do aluno
# ----------------------------------------------------------------------------

echo "==> Deploy Vercel (scope=${VERCEL_SCOPE})"
vercel --prod --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" --yes --name "$PROJECT_NAME" --confirm >/tmp/vercel-${USERNAME_INSTAGRAM}.log 2>&1 || {
  echo "Erro no deploy Vercel. Log:" >&2
  cat /tmp/vercel-${USERNAME_INSTAGRAM}.log >&2
  exit 1
}

# ----------------------------------------------------------------------------
# 5) Adicionar dominio FQDN no projeto Vercel
# ----------------------------------------------------------------------------

echo "==> Adicionando dominio ${FQDN} no projeto Vercel ${PROJECT_NAME}"
vercel domains add "$FQDN" "$PROJECT_NAME" --token "$VERCEL_TOKEN" --scope "$VERCEL_SCOPE" >/dev/null 2>&1 || {
  echo "    (dominio ja existia ou foi adicionado em outro projeto, seguindo)"
}

# ----------------------------------------------------------------------------
# 6) Criar registro DNS A no Cloudflare do aluno
# ----------------------------------------------------------------------------

echo "==> Criando DNS A ${USERNAME_INSTAGRAM} -> 76.76.21.21 na zone do aluno"
DNS_RESP=$(curl -sS -X POST "https://api.cloudflare.com/client/v4/zones/${CLOUDFLARE_ZONE_ID}/dns_records" \
  -H "Authorization: Bearer ${CLOUDFLARE_DNS_TOKEN}" \
  -H "Content-Type: application/json" \
  --data "{\"type\":\"A\",\"name\":\"${USERNAME_INSTAGRAM}\",\"content\":\"76.76.21.21\",\"ttl\":1,\"proxied\":false}" \
)

if ! echo "$DNS_RESP" | grep -q '"success":true'; then
  if echo "$DNS_RESP" | grep -q "already exists"; then
    echo "    Registro DNS ja existia, ok."
  else
    echo "Falha ao criar DNS no Cloudflare:" >&2
    echo "$DNS_RESP" >&2
    exit 1
  fi
fi

# ----------------------------------------------------------------------------
# 7) Resultado
# ----------------------------------------------------------------------------

echo ""
echo "==> Pronto."
echo "    URL final: https://${FQDN}"
echo "    Pode levar de 30s a 5min pro DNS propagar e o Vercel emitir TLS."
