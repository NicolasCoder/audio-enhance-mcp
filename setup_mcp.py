#!/usr/bin/env python3
"""
Instalador Universal do Adobe Podcast Studio MCP + Skill + Auto-Healing.
100% compativel em paralelo com o Flow Studio MCP (nao sobrescreve nem conflita com o flow-studio).
Configura automaticamente:
  - Google Antigravity (~/.gemini/config/mcp_config.json + Skill global adobe-podcast)
  - Claude Code (.mcp.json + ~/.claude.json + CLAUDE.md)
  - Cursor (~/.cursor/mcp.json + .cursor/mcp.json)
  - OpenAI Codex / GPT Agents (AGENTS.md)
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
MCP_SERVER_SCRIPT = str(BASE_DIR / "podcast_mcp_server.py")
SKILL_SRC = BASE_DIR / ".agents" / "skills" / "adobe-podcast" / "SKILL.md"
PYTHON_EXE = sys.executable


def ensure_dependencies():
    print("[1/4] Verificando dependencias Python (playwright, aiohttp, mcp, pystray, pillow, pywebview, psutil)...")
    missing = []
    for mod in ("playwright", "aiohttp", "mcp", "pystray", "PIL", "webview", "psutil"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print(f"      Instalando pacotes ausentes: {', '.join(missing)}...")
        subprocess.check_call([PYTHON_EXE, "-m", "pip", "install", "-r", str(BASE_DIR / "requirements.txt")])
    else:
        print("      [OK] Todas as bibliotecas ja estao instaladas.")


def merge_mcp_json(file_path: Path):
    """Adiciona ou atualiza a entrada 'adobe-podcast' em mcp_config.json / .mcp.json preservando o 'flow-studio' e demais MCPs."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if file_path.exists():
        try:
            content = file_path.read_text(encoding="utf-8").strip()
            if content:
                data = json.loads(content)
        except Exception:
            data = {}

    if not isinstance(data, dict):
        data = {}
    mcp_servers = data.setdefault("mcpServers", {})
    mcp_servers["adobe-podcast"] = {
        "command": PYTHON_EXE,
        "args": [MCP_SERVER_SCRIPT],
    }
    file_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"      [OK] MCP 'adobe-podcast' registrado em: {file_path}")


def configure_all_agents():
    print("[2/4] Registrando servidor MCP 'adobe-podcast' nos agentes de IA (em paralelo ao 'flow-studio')...")
    home = Path.home()

    # 1. Antigravity (Global)
    merge_mcp_json(home / ".gemini" / "config" / "mcp_config.json")
    merge_mcp_json(home / ".gemini" / "antigravity" / "mcp_config.json")

    # 2. Claude Code / Repo-level .mcp.json
    merge_mcp_json(BASE_DIR / ".mcp.json")
    claude_global = home / ".claude.json"
    if claude_global.exists():
        merge_mcp_json(claude_global)

    # 3. Cursor (Global & Local)
    merge_mcp_json(home / ".cursor" / "mcp.json")
    merge_mcp_json(BASE_DIR / ".cursor" / "mcp.json")

    print("[3/4] Instalando Skill Global 'adobe-podcast' (com protocolo Auto-Healing)...")
    if SKILL_SRC.exists():
        for dest_dir in (
            home / ".gemini" / "config" / "skills" / "adobe-podcast",
            home / ".gemini" / "antigravity" / "skills" / "adobe-podcast",
            home / ".claude" / "skills" / "adobe-podcast",
        ):
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SKILL_SRC, dest_dir / "SKILL.md")
            print(f"      [OK] Skill instalada em: {dest_dir / 'SKILL.md'}")


def check_chrome_and_login():
    print("[4/4] Verificando Google Chrome e isolamento de portas/perfil...")
    sys.path.insert(0, str(BASE_DIR))
    import podcastapi

    try:
        chrome_path = podcastapi.detectar_executavel_chrome()
        print(f"      [OK] Google Chrome detectado: {chrome_path}")
    except Exception as e:
        print(f"      [!] Aviso: {e}")
        return

    print(f"      [OK] Porta CDP exclusiva Adobe Podcast: {podcastapi.CHROME_PORT} (Flow usa 9333)")
    print(f"      [OK] Porta Daemon Fila Central: 9344 (Flow usa 9334)")
    print(f"      [OK] Diretorio de perfil isolado: {podcastapi.CHROME_PROFILE_DIR}")
    if podcastapi.ja_fez_login_inicial():
        print("      [OK] Login inicial ja consta como concluido (.login_completed). O navegador operara 100% invisivel!")
    else:
        print("      [i] Primeiro uso detectado: Na primeira execucao (ou chamando podcast_login_browser),")
        print("          o Chrome da porta 9343 abrira visivel para voce logar na sua conta Adobe uma unica vez.")


if __name__ == "__main__":
    print("=" * 70)
    print("  INSTALADOR UNIVERSAL - ADOBE PODCAST STUDIO MCP + AUTO-HEALING")
    print("=" * 70)
    ensure_dependencies()
    configure_all_agents()
    check_chrome_and_login()
    print("\n[SUCESSO] Configuracao concluida! O Adobe Podcast MCP esta pronto para rodar em paralelo ao Flow Studio.")
