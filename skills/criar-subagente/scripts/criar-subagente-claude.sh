#!/usr/bin/env bash
# criar-subagente-claude.sh
# Cria um novo subagente Claude Code em ~/.claude/agents/<nome>.md
# Uso:
#   bash criar-subagente-claude.sh \
#     --nome "sdr-estetica" \
#     --descricao "SDR especialista em estetica" \
#     --tools "Read, Write, WebFetch" \
#     --modelo "sonnet" \
#     --prompt-file /tmp/prompt.md

set -euo pipefail

NOME=""
DESCRICAO=""
TOOLS=""
MODELO="sonnet"
PROMPT_FILE=""
DESTINO_BASE="${HOME}/.claude/agents"

# Parse argumentos
while [[ $# -gt 0 ]]; do
  case "$1" in
    --nome)
      NOME="$2"
      shift 2
      ;;
    --descricao)
      DESCRICAO="$2"
      shift 2
      ;;
    --tools)
      TOOLS="$2"
      shift 2
      ;;
    --modelo)
      MODELO="$2"
      shift 2
      ;;
    --prompt-file)
      PROMPT_FILE="$2"
      shift 2
      ;;
    --destino)
      DESTINO_BASE="$2"
      shift 2
      ;;
    *)
      echo "ERRO: argumento desconhecido: $1" >&2
      exit 1
      ;;
  esac
done

# Validacoes
if [[ -z "$NOME" ]]; then
  echo "ERRO: --nome obrigatorio" >&2
  exit 1
fi

if [[ -z "$DESCRICAO" ]]; then
  echo "ERRO: --descricao obrigatorio" >&2
  exit 1
fi

if [[ -z "$TOOLS" ]]; then
  echo "ERRO: --tools obrigatorio (ex: 'Read, Write, WebFetch')" >&2
  exit 1
fi

if [[ -z "$PROMPT_FILE" ]] || [[ ! -f "$PROMPT_FILE" ]]; then
  echo "ERRO: --prompt-file deve apontar pra arquivo existente" >&2
  exit 1
fi

# Validar nome (lowercase, a-z, 0-9, hifen, max 30)
if ! echo "$NOME" | grep -qE '^[a-z0-9-]{1,30}$'; then
  echo "ERRO: nome invalido. Use lowercase, a-z, 0-9 e hifen. Max 30 chars." >&2
  echo "Recebido: '$NOME'" >&2
  exit 1
fi

# Validar modelo
case "$MODELO" in
  sonnet|opus|haiku)
    ;;
  *)
    echo "ERRO: modelo invalido. Use: sonnet, opus, haiku." >&2
    exit 1
    ;;
esac

# Garantir diretorio destino
mkdir -p "$DESTINO_BASE"

DESTINO="${DESTINO_BASE}/${NOME}.md"

# Verificar colisao
if [[ -f "$DESTINO" ]]; then
  echo "ERRO: ja existe subagente com nome '$NOME' em $DESTINO" >&2
  echo "Escolha outro nome ou apague o arquivo existente antes." >&2
  exit 1
fi

# Ler prompt
PROMPT_BODY="$(cat "$PROMPT_FILE")"

# Montar arquivo final
cat > "$DESTINO" <<EOF
---
name: ${NOME}
description: ${DESCRICAO}
tools: [${TOOLS}]
model: ${MODELO}
---

${PROMPT_BODY}
EOF

# Validar arquivo gerado
if [[ ! -s "$DESTINO" ]]; then
  echo "ERRO: arquivo gerado vazio em $DESTINO" >&2
  exit 1
fi

echo "OK"
echo "Subagente criado: $DESTINO"
echo "Modelo: $MODELO"
echo "Tools: $TOOLS"
echo ""
echo "Disponivel imediatamente. Sem restart necessario."
echo "Como usar (em codigo): Agent(subagent_type=\"${NOME}\", ...)"
