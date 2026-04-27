#!/bin/bash
# ============================================
# BOOTSTRAP DO AGENTE CLAUDE + TELEGRAM v3
# ============================================
# Roda UMA VEZ SO numa VPS Ubuntu 22+ ou no macOS.
# Linux: roda como root (sudo bash ou ja como root)
# Mac: roda como usuario normal (sem sudo na chamada)
#
# Instala: Node 22 (via nvm), Python 3, ffmpeg, Claude Code CLI 2.1.118 (pinado),
#          tmux, PostgreSQL 16 + pgvector, Caddy, pm2 globalmente.
#
# v3: Adicionou Caddy + pm2 + nvm (pra gerenciar Node).
#     Suporte macOS via Homebrew.
#
# Uso:
#   curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/bootstrap.sh | bash
# ============================================

set -e

# Detecta SO
if [[ "$OSTYPE" == "darwin"* ]]; then
  OS="macos"
elif [[ -f /etc/os-release ]] && grep -q "Ubuntu" /etc/os-release; then
  OS="ubuntu"
else
  echo "ERRO: SO nao suportado. Precisa Ubuntu 22+ ou macOS 13+."
  exit 1
fi

echo "============================================"
echo "BOOTSTRAP AGENTE CLAUDE + TELEGRAM v3 ($OS)"
echo "============================================"

# ============================================
# UBUNTU
# ============================================
if [[ "$OS" == "ubuntu" ]]; then
  if [[ "$EUID" -ne 0 ]]; then
    echo "ERRO: rode como root no Ubuntu."
    echo "Tenta: sudo bash <(curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/bootstrap.sh)"
    exit 1
  fi

  echo ">> Sistema base + Python + ffmpeg..."
  apt update
  apt install -y curl git tmux build-essential unzip ca-certificates \
                 python3 python3-pip python3-venv \
                 ffmpeg lsof debian-keyring debian-archive-keyring apt-transport-https

  pip3 install --break-system-packages requests psycopg2-binary fastapi uvicorn anthropic 2>/dev/null || \
    pip3 install requests psycopg2-binary fastapi uvicorn anthropic

  # Node 22 via nvm (pra root)
  if ! command -v node &> /dev/null || [[ "$(node -v)" != v2[2-9]* ]]; then
    echo ">> Instalando nvm + Node 22..."
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    nvm install 22
    nvm use 22
    nvm alias default 22

    # symlink global pra acessivel via PATH normal
    ln -sf "$NVM_DIR/versions/node/$(nvm version)/bin/node" /usr/local/bin/node
    ln -sf "$NVM_DIR/versions/node/$(nvm version)/bin/npm" /usr/local/bin/npm
    ln -sf "$NVM_DIR/versions/node/$(nvm version)/bin/npx" /usr/local/bin/npx
  fi

  # PostgreSQL 16 + pgvector
  if ! command -v psql &> /dev/null; then
    echo ">> Instalando PostgreSQL 16 + pgvector..."
    install -d /usr/share/postgresql-common/pgdg
    curl -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
      --fail https://www.postgresql.org/media/keys/ACCC4CF8.asc
    echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt/ $(lsb_release -cs)-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list
    apt update
    apt install -y postgresql-16 postgresql-16-pgvector
    systemctl enable --now postgresql
  fi

  # Caddy (proxy reverso pra agent-manager)
  if ! command -v caddy &> /dev/null; then
    echo ">> Instalando Caddy..."
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
      gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
      tee /etc/apt/sources.list.d/caddy-stable.list
    apt update
    apt install -y caddy
    systemctl enable --now caddy
  fi

  # Claude Code CLI (PINADO 2.1.118)
  echo ">> Instalando Claude Code CLI 2.1.118..."
  npm install -g @anthropic-ai/claude-code@2.1.118

  # PM2 (process manager pro agent-manager)
  echo ">> Instalando pm2..."
  npm install -g pm2

  # Baixa SETUP-AGENTE.md pra /root/
  echo ">> Baixando SETUP-AGENTE.md..."
  curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/SETUP-AGENTE.md \
    -o /root/SETUP-AGENTE.md
  curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/.env.example \
    -o /root/.env.example

  HOME_DIR="/root"
fi

# ============================================
# MACOS
# ============================================
if [[ "$OS" == "macos" ]]; then
  if [[ "$EUID" -eq 0 ]]; then
    echo "ERRO: NAO rode como root no Mac. Roda como seu usuario normal."
    exit 1
  fi

  # Homebrew
  if ! command -v brew &> /dev/null; then
    echo ">> Instalando Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
  fi

  echo ">> Instalando dependencias via Homebrew..."
  brew install python@3.11 ffmpeg tmux postgresql@16 pgvector caddy

  # Node 22 via nvm
  if ! command -v node &> /dev/null || [[ "$(node -v)" != v2[2-9]* ]]; then
    echo ">> Instalando nvm + Node 22..."
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    nvm install 22
    nvm alias default 22
  fi

  # Inicia Postgres
  brew services start postgresql@16

  pip3 install --user requests psycopg2-binary fastapi uvicorn anthropic

  # Claude Code CLI 2.1.118
  echo ">> Instalando Claude Code CLI 2.1.118..."
  npm install -g @anthropic-ai/claude-code@2.1.118
  npm install -g pm2

  # Baixa SETUP-AGENTE.md pra ~
  curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/SETUP-AGENTE.md \
    -o $HOME/SETUP-AGENTE.md
  curl -fsSL https://raw.githubusercontent.com/denderson2013-bot/agente-claude-telegram-setup-alunos-denderson/main/.env.example \
    -o $HOME/.env.example

  HOME_DIR="$HOME"
fi

# ============================================
# RESUMO FINAL
# ============================================
echo ""
echo "============================================"
echo "OK! Pre-requisitos instalados (v3)."
echo "============================================"
echo ""
echo "VERSOES INSTALADAS:"
node --version 2>/dev/null   | xargs echo "  Node:"
python3 --version 2>/dev/null | xargs echo "  Python:"
ffmpeg -version 2>/dev/null | head -1 | xargs echo "  ffmpeg:"
claude --version 2>/dev/null | xargs echo "  Claude:"
psql --version 2>/dev/null | head -1 | xargs echo "  PostgreSQL:"
caddy version 2>/dev/null | head -1 | xargs echo "  Caddy:"
pm2 --version 2>/dev/null | xargs echo "  pm2:"
echo ""
echo "============================================"
echo "PROXIMOS PASSOS:"
echo "============================================"
echo ""
echo "1. Logar no Claude com sua conta Pro ou Max:"
echo "   claude auth login --claudeai"
echo "   (abre link no navegador, loga, autoriza, cola codigo)"
echo ""
echo "2. Iniciar o Claude:"
echo "   cd $HOME_DIR && claude --dangerously-skip-permissions"
echo ""
echo "3. Dentro do Claude, cola essa mensagem:"
echo ""
echo "   Leia o arquivo SETUP-AGENTE.md e execute todos os passos."
echo "   Me faca perguntas quando precisar de informacao minha."
echo ""
echo "============================================"
