#!/usr/bin/env python3
"""
criar-subagente-openclaw.py
Cria um novo subagente OpenClaw editando /root/.openclaw/openclaw.json
e criando workspace dedicado em /root/.openclaw/workspace-<nome>/

Uso:
  python3 criar-subagente-openclaw.py \
    --nome "sdr-estetica" \
    --descricao "SDR especialista em estetica" \
    --tools "fs.read,fs.write,web.fetch" \
    --modelo "claude-sonnet-4-5" \
    --prompt-file /tmp/prompt.md

Faz:
  1. Backup do openclaw.json antes de editar (com timestamp)
  2. Valida JSON antes de salvar
  3. Cria workspace dedicado com SOUL.md, MEMORY.md, BOOTSTRAP.md
  4. Restart openclaw-gateway via systemctl
  5. Roda openclaw doctor no final
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

OPENCLAW_BASE = Path("/root/.openclaw")
OPENCLAW_JSON = OPENCLAW_BASE / "openclaw.json"
WORKSPACE_BASE = OPENCLAW_BASE
NOME_PATTERN = re.compile(r"^[a-z0-9-]{1,30}$")
MODELOS_VALIDOS = {
    "claude-sonnet-4-5",
    "claude-opus-4-6",
    "claude-haiku-4-5",
    "sonnet",
    "opus",
    "haiku",
}


def parse_args():
    p = argparse.ArgumentParser(description="Criar subagente OpenClaw")
    p.add_argument("--nome", required=True, help="Nome do subagente (lowercase, sem espacos)")
    p.add_argument("--descricao", required=True, help="Descricao curta com triggers")
    p.add_argument("--tools", required=True, help="Lista CSV de tools (ex: fs.read,fs.write,web.fetch)")
    p.add_argument("--modelo", default="claude-sonnet-4-5", help="Modelo (default claude-sonnet-4-5)")
    p.add_argument("--prompt-file", required=True, help="Arquivo com system prompt em PT-BR")
    p.add_argument("--max-tokens", type=int, default=8000, help="Max tokens default 8000")
    p.add_argument("--no-restart", action="store_true", help="Nao reiniciar openclaw-gateway")
    p.add_argument("--openclaw-base", default=str(OPENCLAW_BASE), help="Base do OpenClaw (default /root/.openclaw)")
    return p.parse_args()


def validar_nome(nome: str) -> None:
    if not NOME_PATTERN.match(nome):
        print(f"ERRO: nome invalido '{nome}'. Use a-z, 0-9 e hifen. Max 30 chars.", file=sys.stderr)
        sys.exit(1)


def validar_modelo(modelo: str) -> None:
    if modelo not in MODELOS_VALIDOS:
        print(f"ERRO: modelo invalido '{modelo}'. Validos: {sorted(MODELOS_VALIDOS)}", file=sys.stderr)
        sys.exit(1)


def fazer_backup(json_path: Path) -> Path:
    if not json_path.exists():
        print(f"ERRO: openclaw.json nao encontrado em {json_path}", file=sys.stderr)
        sys.exit(1)
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = json_path.with_suffix(f".json.bak.{ts}")
    shutil.copy2(json_path, backup)
    print(f"BACKUP: {backup}")
    return backup


def carregar_config(json_path: Path) -> dict:
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        print(f"ERRO: openclaw.json invalido: {e}", file=sys.stderr)
        sys.exit(1)


def garantir_estrutura_agents(config: dict) -> dict:
    if "agents" not in config or not isinstance(config["agents"], dict):
        config["agents"] = {}
    if "list" not in config["agents"] or not isinstance(config["agents"]["list"], list):
        config["agents"]["list"] = []
    return config


def checar_colisao(config: dict, nome: str) -> None:
    for ag in config["agents"]["list"]:
        if ag.get("id") == nome or ag.get("name") == nome:
            print(f"ERRO: ja existe agente com id/name '{nome}' em openclaw.json", file=sys.stderr)
            sys.exit(1)


def criar_workspace(workspace_dir: Path, nome: str, descricao: str, prompt_body: str) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # SOUL.md
    soul = workspace_dir / "SOUL.md"
    soul.write_text(
        f"""# SOUL.md — {nome}

## Identidade
{prompt_body}

## Origem
Criado via skill criar-subagente em {time.strftime('%Y-%m-%d %H:%M:%S')}.

## Hierarquia
Subagente da equipe Naia. Reporta pra Naia. Nunca fala direto com o Chefe.

## Descricao operacional
{descricao}
""",
        encoding="utf-8",
    )

    # MEMORY.md
    memory = workspace_dir / "MEMORY.md"
    memory.write_text(
        f"""# MEMORY.md — {nome}

Indice de memorias persistentes. Comeca vazio.

Adicionar entradas conforme aprendizado:
- decisions/ — decisoes permanentes
- lessons/ — licoes aprendidas
- references/ — material de referencia
- pending/ — aguardando input
""",
        encoding="utf-8",
    )

    # BOOTSTRAP.md
    bootstrap = workspace_dir / "BOOTSTRAP.md"
    bootstrap.write_text(
        f"""# BOOTSTRAP.md — {nome}

## Inicializacao da sessao

1. Ler SOUL.md (identidade)
2. Ler MEMORY.md (memorias)
3. Recuperar contexto da sessao anterior (se houver)
4. Aguardar comando

## Regras gerais

- PT-BR sempre
- Sem travessoes
- Sem voz robotica de IA
- Reportar pra Naia, nao falar com Chefe direto
- Tools restritas conforme openclaw.json
""",
        encoding="utf-8",
    )

    print(f"WORKSPACE: {workspace_dir}")


def adicionar_agente(config: dict, nome: str, descricao: str, modelo: str, tools_csv: str,
                     workspace_dir: Path, max_tokens: int, prompt_file: Path) -> None:
    tools_list = [t.strip() for t in tools_csv.split(",") if t.strip()]
    novo = {
        "id": nome,
        "name": nome,
        "description": descricao,
        "model": modelo,
        "system_prompt_file": str(workspace_dir / "SOUL.md"),
        "tools": tools_list,
        "channels": [],
        "max_tokens": max_tokens,
        "isolation": True,
        "workspace": str(workspace_dir),
    }
    config["agents"]["list"].append(novo)


def salvar_config(config: dict, json_path: Path) -> None:
    # Validar serializacao
    try:
        serialized = json.dumps(config, indent=2, ensure_ascii=False)
        # Re-parse pra garantir
        json.loads(serialized)
    except (json.JSONDecodeError, TypeError) as e:
        print(f"ERRO: config gerado invalido: {e}", file=sys.stderr)
        sys.exit(1)

    json_path.write_text(serialized, encoding="utf-8")
    print(f"SAVED: {json_path}")


def restart_gateway() -> bool:
    try:
        subprocess.run(["systemctl", "restart", "openclaw-gateway"], check=True, timeout=30)
        print("RESTART: openclaw-gateway reiniciado")
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"AVISO: falha ao reiniciar openclaw-gateway: {e}", file=sys.stderr)
        return False


def doctor() -> None:
    try:
        result = subprocess.run(["openclaw", "doctor"], check=False, capture_output=True, text=True, timeout=30)
        print("DOCTOR stdout:")
        print(result.stdout)
        if result.stderr:
            print("DOCTOR stderr:", result.stderr, file=sys.stderr)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"AVISO: nao foi possivel rodar openclaw doctor: {e}", file=sys.stderr)


def main():
    args = parse_args()

    # Validacoes
    validar_nome(args.nome)
    validar_modelo(args.modelo)

    prompt_file = Path(args.prompt_file)
    if not prompt_file.is_file():
        print(f"ERRO: prompt-file nao existe: {prompt_file}", file=sys.stderr)
        sys.exit(1)
    prompt_body = prompt_file.read_text(encoding="utf-8")

    base = Path(args.openclaw_base)
    json_path = base / "openclaw.json"

    # 1. Backup
    fazer_backup(json_path)

    # 2. Carregar e validar config
    config = carregar_config(json_path)
    config = garantir_estrutura_agents(config)
    checar_colisao(config, args.nome)

    # 3. Criar workspace
    workspace_dir = base / f"workspace-{args.nome}"
    if workspace_dir.exists():
        print(f"ERRO: workspace ja existe em {workspace_dir}", file=sys.stderr)
        sys.exit(1)
    criar_workspace(workspace_dir, args.nome, args.descricao, prompt_body)

    # 4. Adicionar agente no config
    adicionar_agente(
        config,
        nome=args.nome,
        descricao=args.descricao,
        modelo=args.modelo,
        tools_csv=args.tools,
        workspace_dir=workspace_dir,
        max_tokens=args.max_tokens,
        prompt_file=prompt_file,
    )

    # 5. Salvar config
    salvar_config(config, json_path)

    # 6. Restart + doctor
    if not args.no_restart:
        if restart_gateway():
            time.sleep(2)
            doctor()
    else:
        print("RESTART: pulado por flag --no-restart")

    # Output final
    print()
    print(f"OK")
    print(f"Subagente OpenClaw criado: {args.nome}")
    print(f"Modelo: {args.modelo}")
    print(f"Tools: {args.tools}")
    print(f"Workspace: {workspace_dir}")
    print(f"Como usar: chamada via gateway com agent_id=\"{args.nome}\"")


if __name__ == "__main__":
    main()
