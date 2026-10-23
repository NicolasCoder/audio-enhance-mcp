#!/usr/bin/env python3
"""
Adobe Podcast Studio — System Tray & Apple Control Center Flyout Popup
- 100% compativel em paralelo com o Flow Studio (usa portas 9343/9344 e PID proprio)
- Zero icone de Python na barra de tarefas (WS_EX_TOOLWINDOW + ITaskbarList::DeleteTab)
- Cantos arredondados nativos via Windows DWM (DWMWA_WINDOW_CORNER_PREFERENCE)
- Icone exclusivo de Ondas de Audio (Waveform) na bandeja do sistema
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path
from PIL import Image, ImageDraw
import pystray
import webview

BASE_DIR = Path(__file__).resolve().parent
DAEMON_SCRIPT = BASE_DIR / "podcast_queue_daemon.py"
DAEMON_PORT = int(os.environ.get("ADOBE_DAEMON_PORT", "9344"))
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"

POPUP_WIDTH = 430
POPUP_HEIGHT = 600


def ensure_daemon():
    """Garante que o Daemon Central do Adobe Podcast esteja rodando na porta 9344."""
    try:
        req = urllib.request.Request(f"{DAEMON_URL}/health")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            if resp.status == 200:
                return True
    except Exception:
        pass

    pythonw_exe = sys.executable.replace("python.exe", "pythonw.exe")
    if not os.path.exists(pythonw_exe):
        pythonw_exe = sys.executable

    if os.name == "nt":
        cmd_str = f'"{pythonw_exe}" "{DAEMON_SCRIPT}"'
        ps_cmd = (
            f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine='{cmd_str}'; CurrentDirectory='{BASE_DIR}'}} | Out-Null"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            [sys.executable, str(DAEMON_SCRIPT)],
            cwd=str(BASE_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    for _ in range(25):
        time.sleep(0.4)
        try:
            with urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=1.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
    return False


def get_flyout_position(width=POPUP_WIDTH, height=POPUP_HEIGHT, margin=12):
    """Calcula X e Y para o popup flutuar no canto inferior direito logo acima da barra de tarefas."""
    if os.name == "nt":
        try:
            rect = wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0)
            pos_x = rect.right - width - margin
            pos_y = rect.bottom - height - margin
            return max(10, pos_x), max(10, pos_y)
        except Exception:
            pass
    return 120, 100


def create_tray_icon_image(status="ready"):
    """
    Desenha um icone de Ondas Sonoras (Audio Waveform) na bandeja do sistema (64x64)
    para diferenciar visualmente do icone de Play do Flow Studio:
      - ready: Roxo/Indigo Vibrante com anel Ciano/Esmeralda
      - processing: Ambar Dourado Brilhante
      - offline / login: Vermelho Coral
    """
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if status == "processing":
        primary = (245, 158, 11, 255)
        glow = (245, 158, 11, 85)
        ring = (253, 224, 71, 255)
    elif status in ("offline", "login"):
        primary = (239, 68, 68, 255)
        glow = (239, 68, 68, 85)
        ring = (252, 165, 165, 255)
    else:
        primary = (104, 109, 244, 255)
        glow = (104, 109, 244, 85)
        ring = (165, 180, 252, 255)

    draw.ellipse((2, 2, 62, 62), fill=glow)
    draw.ellipse((6, 6, 58, 58), fill=primary, outline=ring, width=3)

    # Desenha 5 barras de onda de audio (Waveform) brancas no centro
    bars = [
        (17, 26, 21, 38),
        (24, 19, 28, 45),
        (31, 14, 35, 50),
        (38, 20, 42, 44),
        (45, 26, 49, 38),
    ]
    for x1, y1, x2, y2 in bars:
        draw.rounded_rectangle((x1, y1, x2, y2), radius=2, fill=(255, 255, 255, 255))
    return img


HTML_DASHBOARD = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <title>Adobe Podcast Flyout</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    :root {
      --bg-base: #0b0e18;
      --bg-card: rgba(255, 255, 255, 0.045);
      --bg-card-hover: rgba(255, 255, 255, 0.075);
      --bg-input: rgba(0, 0, 0, 0.35);
      --border-subtle: rgba(255, 255, 255, 0.09);
      --border-highlight: rgba(255, 255, 255, 0.18);
      --accent: #686df4;
      --emerald: #10b981;
      --amber: #f59e0b;
      --rose: #f43f5e;
      --text-primary: #f8fafc;
      --text-secondary: #94a3b8;
      --text-dim: #64748b;
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      user-select: none;
      -webkit-font-smoothing: antialiased;
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Segoe UI", sans-serif;
      background:
        radial-gradient(120% 80% at 15% 0%, rgba(104, 109, 244, 0.22) 0%, transparent 55%),
        radial-gradient(100% 70% at 90% 100%, rgba(236, 72, 153, 0.12) 0%, transparent 50%),
        linear-gradient(180deg, #121628 0%, #090c15 100%);
      color: var(--text-primary);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 16px;
    }

    .flyout-wrapper {
      display: flex;
      flex-direction: column;
      height: 100%;
      animation: popIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
    }

    @keyframes popIn {
      from { opacity: 0; transform: translateY(10px) scale(0.985); }
      to { opacity: 1; transform: translateY(0) scale(1); }
    }

    .header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 16px 10px;
      -webkit-app-region: drag;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-icon {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: linear-gradient(135deg, #686df4 0%, #a855f7 100%);
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px rgba(104, 109, 244, 0.45);
    }
    .brand-icon svg {
      width: 16px;
      height: 16px;
      stroke: #fff;
      stroke-width: 2.2;
      fill: none;
    }
    .brand-text {
      display: flex;
      flex-direction: column;
    }
    .brand-title {
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.3px;
      color: #fff;
    }
    .brand-sub {
      font-size: 10px;
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      gap: 5px;
    }
    .status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: var(--emerald);
      box-shadow: 0 0 6px var(--emerald);
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 6px;
      -webkit-app-region: no-drag;
    }
    .icon-btn {
      width: 26px;
      height: 26px;
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border-subtle);
      color: var(--text-secondary);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      transition: all 0.15s;
    }
    .icon-btn:hover {
      background: rgba(255, 255, 255, 0.12);
      color: #fff;
    }

    .nav-container {
      padding: 0 16px 10px;
    }
    .nav-pills {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      background: rgba(0, 0, 0, 0.4);
      border: 1px solid var(--border-subtle);
      border-radius: 12px;
      padding: 3px;
      gap: 3px;
    }
    .nav-pill {
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 7px 4px;
      border-radius: 9px;
      color: var(--text-secondary);
      font-size: 11px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.18s ease;
    }
    .nav-pill svg {
      width: 15px;
      height: 15px;
      stroke-width: 2.1;
    }
    .nav-pill:hover {
      color: #fff;
      background: rgba(255, 255, 255, 0.04);
    }
    .nav-pill.active {
      background: rgba(255, 255, 255, 0.11);
      color: #fff;
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3), inset 0 1px 0 rgba(255, 255, 255, 0.15);
    }
    .nav-pill.active svg {
      stroke: #a5b4fc;
    }
    .badge-counter {
      background: var(--accent);
      color: #fff;
      font-size: 9px;
      font-weight: 700;
      padding: 1px 5px;
      border-radius: 999px;
      min-width: 16px;
      text-align: center;
    }

    .main-scroll {
      flex: 1;
      overflow-y: auto;
      padding: 4px 16px 16px;
    }
    .main-scroll::-webkit-scrollbar { width: 4px; }
    .main-scroll::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.12);
      border-radius: 99px;
    }
    .tab-view { display: none; }
    .tab-view.active {
      display: block;
      animation: tabFade 0.16s ease-out;
    }
    @keyframes tabFade {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: translateY(0); }
    }

    .card {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 13px;
      padding: 12px;
      margin-bottom: 10px;
      backdrop-filter: blur(16px);
    }
    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 8px;
      font-size: 10.5px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-secondary);
    }

    .card-active-job {
      background: linear-gradient(135deg, rgba(245, 158, 11, 0.14) 0%, rgba(104, 109, 244, 0.12) 100%);
      border-color: rgba(245, 158, 11, 0.4);
    }
    .progress-bar-indeterminate {
      height: 3px;
      width: 100%;
      background: rgba(255, 255, 255, 0.08);
      border-radius: 99px;
      overflow: hidden;
      margin-top: 8px;
    }
    .progress-bar-indeterminate::after {
      content: '';
      display: block;
      height: 100%;
      width: 40%;
      background: linear-gradient(90deg, #f59e0b, #686df4);
      border-radius: 99px;
      animation: progressSlide 1.4s infinite ease-in-out;
    }
    @keyframes progressSlide {
      0% { transform: translateX(-100%); }
      100% { transform: translateX(260%); }
    }

    .q-item {
      background: rgba(0, 0, 0, 0.28);
      border: 1px solid var(--border-subtle);
      border-radius: 10px;
      padding: 9px 11px;
      margin-bottom: 7px;
      transition: border-color 0.15s;
    }
    .q-item:hover { border-color: var(--border-highlight); }
    .q-top {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 5px;
    }
    .tags { display: flex; gap: 5px; align-items: center; flex-wrap: wrap; }
    .tag {
      font-size: 9.5px;
      font-weight: 700;
      padding: 2px 6px;
      border-radius: 5px;
      text-transform: uppercase;
      letter-spacing: 0.3px;
    }
    .tag-aud { background: rgba(104, 109, 244, 0.22); color: #c7d2fe; border: 1px solid rgba(104, 109, 244, 0.35); }
    .tag-ok { background: rgba(16, 185, 129, 0.22); color: #a7f3d0; border: 1px solid rgba(16, 185, 129, 0.35); }
    .tag-proj { background: rgba(244, 63, 94, 0.18); color: #fecdd3; border: 1px solid rgba(244, 63, 94, 0.3); }
    .tag-prio { background: rgba(245, 158, 11, 0.22); color: #fde68a; border: 1px solid rgba(245, 158, 11, 0.4); }
    .q-prompt {
      font-size: 11.5px;
      color: #e2e8f0;
      line-height: 1.35;
      word-break: break-all;
    }
    .q-actions {
      display: flex;
      justify-content: flex-end;
      gap: 5px;
      margin-top: 6px;
    }

    .btn {
      padding: 6px 11px;
      border-radius: 8px;
      font-size: 11px;
      font-weight: 600;
      border: 1px solid transparent;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      transition: all 0.15s;
      color: #fff;
    }
    .btn-xs { padding: 3px 8px; font-size: 10px; border-radius: 6px; }
    .btn-primary {
      background: linear-gradient(135deg, #686df4 0%, #9333ea 100%);
      box-shadow: 0 4px 12px rgba(104, 109, 244, 0.35);
    }
    .btn-primary:hover { filter: brightness(1.08); transform: translateY(-1px); }
    .btn-glass {
      background: rgba(255, 255, 255, 0.07);
      border-color: var(--border-subtle);
      color: #e2e8f0;
    }
    .btn-glass:hover { background: rgba(255, 255, 255, 0.13); }
    .btn-amber {
      background: rgba(245, 158, 11, 0.18);
      border-color: rgba(245, 158, 11, 0.38);
      color: #fde68a;
    }
    .btn-rose {
      background: rgba(244, 63, 94, 0.18);
      border-color: rgba(244, 63, 94, 0.38);
      color: #fecdd3;
    }

    .field { margin-bottom: 9px; }
    .label {
      display: flex;
      justify-content: space-between;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }
    input[type="text"], textarea, select {
      width: 100%;
      background: var(--bg-input);
      border: 1px solid var(--border-subtle);
      border-radius: 8px;
      padding: 7px 10px;
      font-size: 11.5px;
      color: #fff;
      outline: none;
      font-family: inherit;
    }
    input[type="range"] {
      width: 100%;
      accent-color: #686df4;
      cursor: pointer;
    }
    select option { background: #111726; color: #fff; }
    textarea { resize: none; height: 90px; line-height: 1.4; }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; }

    .cc-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 10px;
    }
    .cc-tile {
      background: var(--bg-card);
      border: 1px solid var(--border-subtle);
      border-radius: 13px;
      padding: 12px;
      display: flex;
      align-items: center;
      gap: 10px;
      cursor: pointer;
      transition: all 0.15s;
    }
    .cc-tile:hover {
      background: var(--bg-card-hover);
      border-color: var(--border-highlight);
      transform: translateY(-1px);
    }
    .cc-icon {
      width: 34px;
      height: 34px;
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 16px;
      flex-shrink: 0;
    }
    .cc-info { display: flex; flex-direction: column; }
    .cc-title { font-size: 11.5px; font-weight: 700; color: #fff; }
    .cc-sub { font-size: 10px; color: var(--text-secondary); }

    .sys-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 7px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.05);
      font-size: 11.5px;
    }
    .sys-row:last-child { border-bottom: none; }

    #toast {
      position: fixed;
      bottom: 12px;
      left: 50%;
      transform: translateX(-50%) translateY(40px);
      background: rgba(15, 23, 42, 0.96);
      border: 1px solid rgba(104, 109, 244, 0.55);
      color: #fff;
      padding: 6px 14px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 600;
      box-shadow: 0 8px 20px rgba(0, 0, 0, 0.5);
      opacity: 0;
      pointer-events: none;
      transition: all 0.2s ease;
      z-index: 9999;
    }
    #toast.show { transform: translateX(-50%) translateY(0); opacity: 1; }
  </style>
</head>
<body>
  <div class="flyout-wrapper">

    <!-- HEADER -->
    <div class="header">
      <div class="brand">
        <div class="brand-icon">
          <svg viewBox="0 0 24 24"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="22"></line></svg>
        </div>
        <div class="brand-text">
          <span class="brand-title">Adobe Podcast Studio</span>
          <span class="brand-sub">
            <span class="status-dot" id="topDot"></span>
            <span id="topStatusText">Fila Central Ativa (Porta 9344)</span>
          </span>
        </div>
      </div>
      <div class="header-actions">
        <div class="icon-btn" onclick="refreshAll()" title="Atualizar">↻</div>
        <div class="icon-btn" onclick="if(window.pywebview && window.pywebview.api) window.pywebview.api.close_flyout()" title="Recolher Painel">▾</div>
      </div>
    </div>

    <!-- TOP NAVIGATION PILLS -->
    <div class="nav-container">
      <div class="nav-pills">
        <div class="nav-pill active" id="nav-queue" onclick="switchTab('queue')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><line x1="8" y1="6" x2="21" y2="6"></line><line x1="8" y1="12" x2="21" y2="12"></line><line x1="8" y1="18" x2="21" y2="18"></line><line x1="3" y1="6" x2="3.01" y2="6"></line><line x1="3" y1="12" x2="3.01" y2="12"></line><line x1="3" y1="18" x2="3.01" y2="18"></line></svg>
          <span>Fila</span>
          <span class="badge-counter" id="badgeQueue">0</span>
        </div>
        <div class="nav-pill" id="nav-create" onclick="switchTab('create')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path></svg>
          <span>Limpar</span>
        </div>
        <div class="nav-pill" id="nav-batch" onclick="switchTab('batch')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline></svg>
          <span>Lote</span>
        </div>
        <div class="nav-pill" id="nav-status" onclick="switchTab('status')">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor"><rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect><line x1="8" y1="21" x2="16" y2="21"></line><line x1="12" y1="17" x2="12" y2="21"></line></svg>
          <span>Sistema</span>
        </div>
      </div>
    </div>

    <!-- SCROLLABLE CONTENT -->
    <div class="main-scroll">

      <!-- TAB 1: FILA CENTRAL -->
      <div class="tab-view active" id="tab-queue">
        <div class="card card-active-job" id="runningCard" style="display:none;">
          <div class="card-header">
            <span style="color:#fcd34d;">🎙️ Aprimorando no Adobe Podcast</span>
            <span class="tag tag-proj" id="runningProj">Projeto</span>
          </div>
          <div id="runningBody" style="font-size:11.5px; color:#fff;"></div>
          <div class="progress-bar-indeterminate"></div>
        </div>

        <div class="card" id="projectsCard" style="display:none;">
          <div class="card-header">
            <span>Projetos na Fila (Definir Prioridade)</span>
          </div>
          <div id="projectsList" style="display:flex; flex-direction:column; gap:6px;"></div>
        </div>

        <div class="card">
          <div class="card-header">
            <span>Fila de Espera (MCPs & Painel)</span>
            <span id="queuedCountText">0 na fila</span>
          </div>
          <div id="queuedList">
            <div style="text-align:center; padding:18px 8px; color:var(--text-dim); font-size:11.5px;">
              Nenhum áudio aguardando na fila.<br>
              <span style="font-size:10.5px;">Pedidos enviados por qualquer MCP aparecem aqui automaticamente!</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <span>Áudios Aprimorados Recentemente</span>
          </div>
          <div id="completedList"></div>
        </div>
      </div>

      <!-- TAB 2: APRIMORAR ARQUIVO INDIVIDUAL -->
      <div class="tab-view" id="tab-create">
        <div class="card">
          <div class="field">
            <label class="label">Arquivo de Áudio ou Vídeo (Entrada)</label>
            <input type="text" id="singleInput" placeholder="Ex: C:\Projeto\narracao_bruta.wav ou .mp4">
          </div>

          <div class="field">
            <label class="label">
              <span>Intensidade da Voz (Strength)</span>
              <span id="lblStrength" style="color:#a5b4fc;">90%</span>
            </label>
            <input type="range" id="singleStrength" min="0" max="100" value="90" oninput="document.getElementById('lblStrength').innerText=this.value+'%'">
          </div>

          <div class="field">
            <label class="label">
              <span>Preservar Ambiente (Background V2)</span>
              <span id="lblBg" style="color:#a5b4fc;">10%</span>
            </label>
            <input type="range" id="singleBg" min="0" max="100" value="10" oninput="document.getElementById('lblBg').innerText=this.value+'%'">
          </div>

          <div class="grid-3">
            <div class="field">
              <label class="label">Modelo IA</label>
              <select id="singleModel">
                <option value="v2">V2 (SoundLift)</option>
                <option value="v1">V1 (Clássico)</option>
              </select>
            </div>
            <div class="field">
              <label class="label">Faixa</label>
              <select id="singleComp">
                <option value="full">Mix Completo</option>
                <option value="speech">Só Voz</option>
                <option value="background">Só Ambiente</option>
              </select>
            </div>
            <div class="field">
              <label class="label">Prioridade</label>
              <select id="singlePrio">
                <option value="100">Normal</option>
                <option value="10">⚡ Alta</option>
              </select>
            </div>
          </div>

          <div class="field">
            <label class="label">Salvar em (Arquivo .wav/.mp3 ou Pasta)</label>
            <input type="text" id="singleOutput" placeholder="Deixe vazio para salvar em audios_aprimorados...">
          </div>

          <button class="btn btn-primary" style="width:100%; padding:9px;" onclick="submitSingle()">
            🎙️ Enviar para o Adobe Podcast
          </button>
        </div>
      </div>

      <!-- TAB 3: LOTE DE ARQUIVOS -->
      <div class="tab-view" id="tab-batch">
        <div class="card">
          <div class="card-header">
            <span>Lote de Caminhos de Áudio (.TXT)</span>
            <span class="tag tag-prio" id="batchCounter">0 arquivos</span>
          </div>

          <div class="field">
            <label class="btn btn-glass" style="width:100%; cursor:pointer; justify-content:center;">
              📂 Carregar Lista .TXT de Caminhos
              <input type="file" accept=".txt" style="display:none;" onchange="handleTxtUpload(event)">
            </label>
          </div>

          <div class="field">
            <label class="label">Caminhos dos arquivos de áudio/vídeo (1 por linha)</label>
            <textarea id="batchText" placeholder="C:\Projeto\cena1.wav&#10;C:\Projeto\cena2.wav&#10;C:\Projeto\cena3.mp3" oninput="updateBatchCounter()"></textarea>
          </div>

          <div class="grid-2">
            <div class="field">
              <label class="label">Strength do Lote (%)</label>
              <input type="text" id="batchStrength" value="90">
            </div>
            <div class="field">
              <label class="label">Prioridade</label>
              <select id="batchPrio">
                <option value="100">Normal</option>
                <option value="10">⚡ Alta Prioridade</option>
                <option value="200">Baixa (Background)</option>
              </select>
            </div>
          </div>

          <div class="field">
            <label class="label">Pasta de Destino para Todos</label>
            <input type="text" id="batchOutput" placeholder="Ex: C:\Projeto\AudiosLimpos">
          </div>

          <button class="btn btn-primary" style="width:100%; padding:9px;" onclick="submitBatch()">
            ⚡ Enfileirar Lote Inteiro
          </button>
        </div>
      </div>

      <!-- TAB 4: SISTEMA & NAVEGADOR -->
      <div class="tab-view" id="tab-status">
        <div class="cc-grid">
          <div class="cc-tile" onclick="runBrowserCmd('show')">
            <div class="cc-icon" style="background:rgba(104,109,244,0.2); color:#a5b4fc;">👁️</div>
            <div class="cc-info">
              <span class="cc-title">Mostrar Chrome</span>
              <span class="cc-sub">Trazer porta 9343</span>
            </div>
          </div>
          <div class="cc-tile" onclick="runBrowserCmd('hide')">
            <div class="cc-icon" style="background:rgba(16,185,129,0.2); color:#10b981;">👻</div>
            <div class="cc-info">
              <span class="cc-title">Modo Invisível</span>
              <span class="cc-sub">Ocultar janela e ícone</span>
            </div>
          </div>
          <div class="cc-tile" onclick="runBrowserCmd('login')">
            <div class="cc-icon" style="background:rgba(245,158,11,0.2); color:#f59e0b;">🔑</div>
            <div class="cc-info">
              <span class="cc-title">Login Adobe</span>
              <span class="cc-sub">Autenticar conta</span>
            </div>
          </div>
          <div class="cc-tile" onclick="runBrowserCmd('clear_tracks')">
            <div class="cc-icon" style="background:rgba(244,63,94,0.2); color:#f43f5e;">🧹</div>
            <div class="cc-info">
              <span class="cc-title">Limpar Faixas</span>
              <span class="cc-sub">Esvaziar histórico web</span>
            </div>
          </div>
        </div>

        <div class="card">
          <div class="card-header">
            <span>Quota Diária & Pool Multi-Contas</span>
            <button class="btn btn-xs btn-primary" onclick="addNewAccount()">+ Nova Conta</button>
          </div>
          <div style="margin-bottom:8px;">
            <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;">
              <span id="quotaAccLabel" style="font-weight:700; color:#fff;">Conta 1</span>
              <span id="quotaTierBadge" class="tag tag-aud">Gratuito (1h/dia)</span>
            </div>
            <div style="width:100%; height:7px; background:rgba(255,255,255,0.08); border-radius:99px; overflow:hidden; margin-bottom:5px;">
              <div id="quotaProgressBar" style="width:100%; height:100%; background:linear-gradient(90deg, #10b981, #686df4); border-radius:99px; transition:width 0.3s;"></div>
            </div>
            <div style="display:flex; justify-content:space-between; font-size:10.5px; color:var(--text-secondary);">
              <span id="quotaRemText">Restam: 60m 00s</span>
              <span id="quotaResetText">Usado: 0m 00s</span>
            </div>
          </div>
          <div id="accountsPoolList" style="display:flex; flex-direction:column; gap:5px; margin-top:8px; border-top:1px solid rgba(255,255,255,0.06); padding-top:8px;"></div>
        </div>

        <div class="card">
          <div class="card-header">
            <span>Isolamento Paralelo (Zero Conflito com Flow)</span>
          </div>
          <div class="sys-row">
            <span style="color:var(--text-secondary);">Daemon Adobe Podcast</span>
            <span id="sysDaemon" style="font-weight:700; color:#10b981;">Online (Porta 9344)</span>
          </div>
          <div class="sys-row">
            <span style="color:var(--text-secondary);">Chrome CDP Exclusivo</span>
            <span id="sysChrome" style="font-weight:700;">Porta 9343</span>
          </div>
          <div class="sys-row">
            <span style="color:var(--text-secondary);">Sessão Adobe IMS</span>
            <span id="sysLogin" style="font-weight:700;">Verificando...</span>
          </div>
          <div class="sys-row">
            <span style="color:var(--text-secondary);">Visibilidade Atual</span>
            <span id="sysVis" style="font-weight:700; color:#10b981;">Off-Screen Invisível</span>
          </div>
        </div>

        <button class="btn btn-glass" style="width:100%; padding:9px;" onclick="runDiagnose()">
          🩺 Executar Auto-Healing Snapshot (Shadow-DOM + Rede)
        </button>
      </div>

    </div>
  </div>

  <div id="toast">Pronto</div>

  <script>
    const API = "__DAEMON_URL__";

    function toast(msg) {
      const el = document.getElementById('toast');
      el.innerText = msg;
      el.classList.add('show');
      setTimeout(() => el.classList.remove('show'), 2200);
    }

    async function safeFetch(path, options = {}, timeoutMs = 8000) {
      const ctrl = new AbortController();
      const id = setTimeout(() => ctrl.abort(), timeoutMs);
      try {
        const res = await fetch(API + path, { ...options, signal: ctrl.signal });
        clearTimeout(id);
        return await res.json();
      } catch (e) {
        clearTimeout(id);
        return null;
      }
    }

    function switchTab(tab) {
      ['queue', 'create', 'batch', 'status'].forEach(t => {
        document.getElementById('nav-' + t).classList.toggle('active', t === tab);
        document.getElementById('tab-' + t).classList.toggle('active', t === tab);
      });
      if (tab === 'queue') refreshQueue();
      if (tab === 'status') refreshHealth();
    }

    function updateBatchCounter() {
      const raw = document.getElementById('batchText').value || '';
      const lines = raw.split('\n').map(x => x.trim()).filter(x => x && !x.startsWith('#'));
      document.getElementById('batchCounter').innerText = `${lines.length} arquivos`;
    }

    function handleTxtUpload(ev) {
      const f = ev.target.files[0];
      if (!f) return;
      const reader = new FileReader();
      reader.onload = e => {
        document.getElementById('batchText').value = e.target.result;
        updateBatchCounter();
        toast(`Lista ${f.name} carregada!`);
      };
      reader.readAsText(f);
    }

    async function refreshQueue() {
      const data = await safeFetch('/queue');
      if (!data || !data.ok) return;

      const totalActive = (data.queued ? data.queued.length : 0) + (data.running ? data.running.length : 0);
      document.getElementById('badgeQueue').innerText = totalActive;
      document.getElementById('queuedCountText').innerText = `${data.queued.length} na fila`;

      const rCard = document.getElementById('runningCard');
      if (data.running && data.running.length > 0) {
        const r = data.running[0];
        rCard.style.display = 'block';
        const proj = r.workspace ? r.workspace.split(/[\\/]/).pop() : 'Geral';
        const fileName = r.input_path ? r.input_path.split(/[\\/]/).pop() : r.job_id;
        document.getElementById('runningProj').innerText = proj;
        document.getElementById('runningBody').innerHTML = `
          <div style="font-weight:600; margin-bottom:4px;">${fileName}</div>
          <div class="tags">
            <span class="tag tag-aud">Strength ${r.strength}%</span>
            <span class="tag tag-prio">Modelo ${(r.model_version || 'v2').toUpperCase()}</span>
            <span class="tag tag-proj">${r.status}</span>
          </div>
        `;
      } else {
        rCard.style.display = 'none';
      }

      const pCard = document.getElementById('projectsCard');
      const pList = document.getElementById('projectsList');
      if (data.projects && data.projects.length > 0) {
        pCard.style.display = 'block';
        pList.innerHTML = data.projects.map(p => {
          const isHigh = p.highest_priority < 50;
          const safeWs = (p.workspace || '').replace(/\\/g, '\\\\').replace(/'/g, "\\'");
          return `
            <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.3); padding:6px 10px; border-radius:8px;">
              <div style="display:flex; align-items:center; gap:6px;">
                <span class="tag tag-proj">${p.name}</span>
                <span style="font-size:11px; color:#cbd5e1;">${p.count} na fila</span>
                ${isHigh ? '<span class="tag tag-prio">⚡ PRIORITÁRIO</span>' : ''}
              </div>
              <button class="btn btn-xs btn-amber" onclick="bumpProject('${safeWs}')">⚡ Dar Prioridade</button>
            </div>
          `;
        }).join('');
      } else {
        pCard.style.display = 'none';
      }

      const qList = document.getElementById('queuedList');
      if (!data.queued || data.queued.length === 0) {
        qList.innerHTML = `
          <div style="text-align:center; padding:16px 8px; color:var(--text-dim); font-size:11.5px;">
            Nenhum áudio aguardando na fila.<br>
            <span style="font-size:10px;">Qualquer áudio enviado pelos seus MCPs aparece aqui ao vivo!</span>
          </div>`;
      } else {
        qList.innerHTML = data.queued.map(j => {
          const proj = j.workspace ? j.workspace.split(/[\\/]/).pop() : 'Geral';
          const fileName = j.input_path ? j.input_path.split(/[\\/]/).pop() : j.job_id;
          const isHigh = (j.priority || 100) < 50;
          return `
            <div class="q-item">
              <div class="q-top">
                <div class="tags">
                  <span class="tag tag-aud">${j.strength}%</span>
                  <span class="tag tag-proj">${proj}</span>
                  ${isHigh ? '<span class="tag tag-prio">⚡ ALTA</span>' : ''}
                </div>
                <span style="font-size:10px; color:var(--text-dim);">${(j.created_at || '').slice(11, 19)}</span>
              </div>
              <div class="q-prompt">${fileName}</div>
              <div class="q-actions">
                ${!isHigh ? `<button class="btn btn-xs btn-glass" onclick="bumpJob('${j.job_id}')">⬆️ Subir</button>` : ''}
                <button class="btn btn-xs btn-rose" onclick="cancelJob('${j.job_id}')">✕ Cancelar</button>
              </div>
            </div>
          `;
        }).join('');
      }

      const cList = document.getElementById('completedList');
      if (!data.recent_completed || data.recent_completed.length === 0) {
        cList.innerHTML = '<div style="font-size:11px; color:var(--text-dim);">Nenhum áudio finalizado ainda.</div>';
      } else {
        cList.innerHTML = data.recent_completed.slice(0, 5).map(c => {
          const proj = c.workspace ? c.workspace.split(/[\\/]/).pop() : 'MCP';
          const outName = c.file_path ? c.file_path.split(/[\\/]/).pop() : '';
          return `
            <div style="display:flex; justify-content:space-between; align-items:center; padding:6px 0; border-bottom:1px solid rgba(255,255,255,0.05); font-size:11px;">
              <div style="overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:270px; color:#cbd5e1;">
                ✅ <strong style="color:#fff;">[${proj}]</strong> ${outName}
              </div>
              <span class="tag tag-ok">SALVO</span>
            </div>
          `;
        }).join('');
      }
    }

    async function refreshHealth() {
      const h = await safeFetch('/health');
      const dot = document.getElementById('topDot');
      const txt = document.getElementById('topStatusText');

      if (!h || !h.ok) {
        dot.style.background = '#ef4444';
        txt.innerText = 'Daemon Reconectando...';
        return;
      }

      document.getElementById('sysChrome').innerHTML = h.browser_connected
        ? `<span style="color:#10b981;">Conectado (Porta ${h.chrome_port})</span>`
        : `<span style="color:#94a3b8;">Standby (Porta ${h.chrome_port})</span>`;

      document.getElementById('sysLogin').innerHTML = h.login_completed_marker
        ? '<span style="color:#10b981;">Autenticado ✓</span>'
        : '<span style="color:#f59e0b;">Requer Login Inicial</span>';

      document.getElementById('sysVis').innerHTML = h.browser_hidden
        ? '<span style="color:#10b981;">Invisível (Off-Screen + Sem Ícone)</span>'
        : '<span style="color:#f59e0b;">Visível na Tela</span>';

      if (h.pool && h.pool.accounts) {
        const activeId = h.pool.active_account;
        const activeAcc = h.pool.accounts.find(a => a.id === activeId) || h.pool.accounts[0];
        if (activeAcc) {
          document.getElementById('quotaAccLabel').innerText = activeAcc.email || activeAcc.label || activeAcc.id;
          document.getElementById('quotaTierBadge').innerText = activeAcc.tier_label || 'Gratuito (1h/dia)';
          const lim = activeAcc.quota_limit_min || 60;
          const rem = activeAcc.quota_remaining_min !== undefined ? activeAcc.quota_remaining_min : lim;
          const pct = Math.max(0, Math.min(100, (rem / lim) * 100));
          document.getElementById('quotaProgressBar').style.width = pct + '%';
          document.getElementById('quotaRemText').innerText = `Restam: ${activeAcc.quota_remaining_human || (rem + 'm')}`;
          document.getElementById('quotaResetText').innerText = `Usado: ${activeAcc.quota_used_human || '0m'} • Reseta: ${activeAcc.quota_reset_in_human || '-'}`;
        }
        const poolEl = document.getElementById('accountsPoolList');
        poolEl.innerHTML = h.pool.accounts.map(a => `
          <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(0,0,0,0.28); padding:5px 8px; border-radius:7px; font-size:10.5px;">
            <div>
              <strong style="color:${a.is_active ? '#10b981' : '#e2e8f0'};">${a.is_active ? '● ' : ''}${a.email || a.label}</strong>
              <span style="color:var(--text-dim); margin-left:4px;">(${a.quota_remaining_human} livre)</span>
            </div>
            ${!a.is_active ? `<button class="btn btn-xs btn-glass" onclick="switchAccount('${a.id}')">Usar</button>` : '<span class="tag tag-ok">ATIVA</span>'}
          </div>
        `).join('');
      }

      if (h.active_job_id) {
        dot.style.background = '#f59e0b';
        dot.style.boxShadow = '0 0 8px #f59e0b';
        txt.innerText = 'Aprimorando no Adobe Podcast...';
      } else {
        dot.style.background = '#10b981';
        dot.style.boxShadow = '0 0 8px #10b981';
        txt.innerText = 'Pronto • Paralelo ao Flow';
      }
    }

    async function addNewAccount() {
      toast('Criando novo perfil Chrome e abrindo para login...');
      const res = await safeFetch('/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'add', open_login: true })
      }, 45000);
      if (res && res.ok) {
        toast(`Perfil ${res.account ? res.account.id : ''} aberto na tela para login!`);
        refreshHealth();
      } else {
        toast('Erro ao criar nova conta');
      }
    }

    async function switchAccount(accId) {
      toast(`Alternando para ${accId}...`);
      const res = await safeFetch('/accounts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'switch', account_id: accId })
      }, 45000);
      if (res && res.ok) {
        toast(`Conta ${accId} ativada!`);
        refreshHealth();
      } else {
        toast('Erro ao alternar conta');
      }
    }

    function refreshAll() {
      refreshQueue();
      refreshHealth();
      toast('Atualizado!');
    }

    async function submitSingle() {
      const input_path = document.getElementById('singleInput').value.trim();
      if (!input_path) return toast('Informe o caminho do arquivo de áudio!');

      const payload = {
        input_path,
        strength: parseInt(document.getElementById('singleStrength').value || '90'),
        background_strength: parseInt(document.getElementById('singleBg').value || '10'),
        model_version: document.getElementById('singleModel').value,
        track_component: document.getElementById('singleComp').value,
        priority: parseInt(document.getElementById('singlePrio').value || '100'),
        output_path: document.getElementById('singleOutput').value.trim(),
        workspace: 'Painel Tray',
        wait: false
      };

      toast('Adicionando à fila...');
      const res = await safeFetch('/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (res && res.ok) {
        document.getElementById('singleInput').value = '';
        toast('Adicionado à Fila Central!');
        switchTab('queue');
      } else {
        toast('Erro ao adicionar na fila');
      }
    }

    async function submitBatch() {
      const raw = document.getElementById('batchText').value || '';
      const lines = raw.split('\n').map(x => x.trim()).filter(x => x && !x.startsWith('#'));
      if (lines.length === 0) return toast('Cole pelo menos 1 caminho de arquivo!');

      const strength = parseInt(document.getElementById('batchStrength').value || '90');
      const priority = parseInt(document.getElementById('batchPrio').value || '100');
      const output_path = document.getElementById('batchOutput').value.trim();

      const items = lines.map(p => ({
        input_path: p,
        strength,
        priority,
        output_path,
        workspace: 'Lote TXT',
        wait: false
      }));

      toast(`Enfileirando ${items.length} áudios...`);
      const res = await safeFetch('/enqueue', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items, wait: false })
      }, 15000);

      if (res && res.ok) {
        document.getElementById('batchText').value = '';
        updateBatchCounter();
        toast(`${items.length} áudios enviados para a fila!`);
        switchTab('queue');
      } else {
        toast('Erro ao enfileirar lote');
      }
    }

    async function bumpJob(jobId) {
      await safeFetch(`/jobs/${jobId}/priority`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ priority: 10 })
      });
      toast('Prioridade máxima definida!');
      refreshQueue();
    }

    async function bumpProject(workspace) {
      await safeFetch('/projects/priority', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ workspace, priority: 10 })
      });
      toast('Projeto priorizado na fila!');
      refreshQueue();
    }

    async function cancelJob(jobId) {
      await safeFetch(`/jobs/${jobId}/cancel`, { method: 'POST' });
      toast('Item removido da fila');
      refreshQueue();
    }

    async function runBrowserCmd(action) {
      toast('Processando comando no navegador...');
      const res = await safeFetch(`/browser/${action}`, { method: 'POST' }, 30000);
      if (res && res.ok) {
        if (action === 'clear_tracks') {
          toast(`${res.deleted_tracks || 0} faixas removidas do histórico!`);
        } else {
          toast(res.visible ? 'Chrome (9343) visível na tela!' : 'Chrome (9343) oculto fora da tela!');
        }
        refreshHealth();
      } else {
        toast('Ação concluída');
      }
    }

    async function runDiagnose() {
      toast('Inspecionando Shadow-DOM e Rede do Adobe Podcast...');
      const res = await safeFetch('/diagnose', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ take_screenshot: true })
      }, 30000);
      if (res && res.ok) {
        toast('Snapshot Auto-Healing salvo com sucesso!');
      } else {
        toast('Falha ao coletar diagnóstico');
      }
    }

    refreshQueue();
    refreshHealth();
    setInterval(refreshQueue, 2000);
    setInterval(refreshHealth, 3500);
  </script>
</body>
</html>
""".replace("__DAEMON_URL__", DAEMON_URL)


class AdobePodcastFlyoutController:
    def __init__(self):
        self.window = None
        self.hwnd = None
        self.tray_icon = None
        self.is_running = True
        self.is_visible = False
        self.initial_load_done = False
        self.last_show_ts = 0.0
        self.last_hide_ts = 0.0

    def find_and_style_hwnd(self):
        if os.name != "nt":
            return None
        user32 = ctypes.windll.user32
        ole32 = ctypes.windll.ole32
        dwmapi = ctypes.windll.dwmapi
        my_pid = os.getpid()
        found_hwnd = None

        def cb(hwnd, lparam):
            nonlocal found_hwnd
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == my_pid:
                length = user32.GetWindowTextLengthW(hwnd)
                buff = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buff, length + 1)
                if "Adobe Podcast" in buff.value:
                    found_hwnd = hwnd
                    return 0
            return 1

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(cb), 0)

        if found_hwnd:
            self.hwnd = found_hwnd
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_APPWINDOW = 0x00040000
            ex_style = user32.GetWindowLongW(found_hwnd, GWL_EXSTYLE)
            new_ex_style = (ex_style | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
            user32.SetWindowLongW(found_hwnd, GWL_EXSTYLE, new_ex_style)

            try:
                ole32.CoInitialize(None)

                class GUID(ctypes.Structure):
                    _fields_ = [
                        ("Data1", wintypes.DWORD),
                        ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_byte * 8),
                    ]

                CLSID_TaskbarList = GUID(
                    0x56FDF344, 0xFD6D, 0x11D0, (ctypes.c_byte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
                )
                IID_ITaskbarList = GUID(
                    0x56FDF342, 0xFD6D, 0x11D0, (ctypes.c_byte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90)
                )
                p_tbl = ctypes.c_void_p()
                if (
                    ole32.CoCreateInstance(
                        ctypes.byref(CLSID_TaskbarList), None, 1, ctypes.byref(IID_ITaskbarList), ctypes.byref(p_tbl)
                    )
                    == 0
                    and p_tbl.value
                ):
                    vtable = ctypes.cast(p_tbl, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                    ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[3])(p_tbl)
                    ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(vtable[5])(p_tbl, found_hwnd)
            except Exception:
                pass

            try:
                preference = ctypes.c_int(2)
                dwmapi.DwmSetWindowAttribute(found_hwnd, 33, ctypes.byref(preference), ctypes.sizeof(preference))
            except Exception:
                pass

        return found_hwnd

    def toggle_popup(self, *args):
        if not self.is_visible and (time.time() - self.last_hide_ts) < 0.55:
            return
        if self.is_visible:
            self.hide_popup()
        else:
            self.show_popup()

    def show_popup(self):
        if os.name == "nt":
            if not self.hwnd:
                self.find_and_style_hwnd()
            if self.hwnd:
                user32 = ctypes.windll.user32
                pos_x, pos_y = get_flyout_position(POPUP_WIDTH, POPUP_HEIGHT)
                SWP_SHOWWINDOW = 0x0040
                self.last_show_ts = time.time()
                self.is_visible = True
                user32.ShowWindow(wintypes.HWND(self.hwnd), 5)
                self.find_and_style_hwnd()
                user32.SetWindowPos(
                    wintypes.HWND(self.hwnd),
                    wintypes.HWND(-1),
                    int(pos_x),
                    int(pos_y),
                    int(POPUP_WIDTH),
                    int(POPUP_HEIGHT),
                    SWP_SHOWWINDOW,
                )
                try:
                    user32.keybd_event(0x12, 0, 0, 0)
                    user32.SetForegroundWindow(wintypes.HWND(self.hwnd))
                    user32.keybd_event(0x12, 0, 2, 0)
                except Exception:
                    pass
                return
        if self.window:
            self.last_show_ts = time.time()
            self.is_visible = True
            self.window.show()

    def hide_popup(self, from_blur=False):
        if from_blur:
            self.last_hide_ts = time.time()
        self.is_visible = False
        if os.name == "nt":
            if not self.hwnd:
                self.find_and_style_hwnd()
            if self.hwnd:
                ctypes.windll.user32.SetWindowPos(
                    wintypes.HWND(self.hwnd),
                    wintypes.HWND(0),
                    -32000,
                    -32000,
                    int(POPUP_WIDTH),
                    int(POPUP_HEIGHT),
                    0x0004 | 0x0010,
                )
                return
        if self.window:
            self.window.hide()

    def watch_outside_click_and_style(self):
        user32 = ctypes.windll.user32
        my_pid = os.getpid()
        was_mouse_down = False

        while self.is_running:
            time.sleep(0.04)
            if os.name != "nt":
                continue

            if not self.hwnd:
                self.find_and_style_hwnd()
                continue

            if not self.is_visible:
                was_mouse_down = False
                continue

            if (time.time() - self.last_show_ts) < 0.40:
                continue

            lmb = (user32.GetAsyncKeyState(0x01) & 0x8000) != 0
            rmb = (user32.GetAsyncKeyState(0x02) & 0x8000) != 0
            mouse_down = lmb or rmb

            if mouse_down and not was_mouse_down:
                pt = wintypes.POINT()
                rect = wintypes.RECT()
                user32.GetCursorPos(ctypes.byref(pt))
                user32.GetWindowRect(self.hwnd, ctypes.byref(rect))

                inside = (rect.left <= pt.x <= rect.right) and (rect.top <= pt.y <= rect.bottom)
                if not inside:
                    fg = user32.GetForegroundWindow()
                    fg_pid = wintypes.DWORD()
                    if fg:
                        user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
                    if fg != 0 and fg_pid.value == my_pid and fg != self.hwnd:
                        pass
                    else:
                        self.hide_popup(from_blur=True)

            was_mouse_down = mouse_down

    def trigger_browser(self, action: str):
        def _run():
            try:
                req = urllib.request.Request(
                    f"{DAEMON_URL}/browser/{action}", data=b"{}", headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=25)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def quit_all(self, *args):
        self.is_running = False
        if self.tray_icon:
            try:
                self.tray_icon.visible = False
                self.tray_icon.stop()
            except Exception:
                pass
        os._exit(0)

    def monitor_tray_status(self):
        last_status = "ready"
        while self.is_running:
            time.sleep(2.0)
            status = "ready"
            tip = "Adobe Podcast Studio — Pronto"
            try:
                with urllib.request.urlopen(f"{DAEMON_URL}/health", timeout=1.5) as resp:
                    h = json.loads(resp.read().decode())
                with urllib.request.urlopen(f"{DAEMON_URL}/queue", timeout=1.5) as resp:
                    q = json.loads(resp.read().decode())

                running_cnt = len(q.get("running", []))
                queued_cnt = len(q.get("queued", []))

                if not h.get("login_completed_marker"):
                    status = "login"
                    tip = "Adobe Podcast — Aguardando Login Inicial"
                elif running_cnt > 0:
                    status = "processing"
                    tip = f"Adobe Podcast — Aprimorando ({queued_cnt} na fila)"
                else:
                    status = "ready"
                    tip = f"Adobe Podcast — Pronto ({queued_cnt} na fila)"
            except Exception:
                status = "offline"
                tip = "Adobe Podcast — Conectando ao Daemon..."

            if self.tray_icon:
                try:
                    self.tray_icon.title = tip
                    if status != last_status:
                        self.tray_icon.icon = create_tray_icon_image(status)
                        last_status = status
                except Exception:
                    pass

    def setup_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("🎙️ Alternar Painel Adobe Podcast", self.toggle_popup, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("👁️ Mostrar Navegador (/showbrowser)", lambda icon, item: self.trigger_browser("show")),
            pystray.MenuItem("👻 Esconder Navegador (/hidebrowser)", lambda icon, item: self.trigger_browser("hide")),
            pystray.MenuItem("🔑 Login Adobe Podcast (/loginbrowser)", lambda icon, item: self.trigger_browser("login")),
            pystray.MenuItem("🧹 Limpar Histórico de Faixas", lambda icon, item: self.trigger_browser("clear_tracks")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("❌ Sair do Adobe Podcast Tray", self.quit_all),
        )

        self.tray_icon = pystray.Icon(
            "adobe-podcast",
            create_tray_icon_image("ready"),
            "Adobe Podcast Studio — Pronto",
            menu,
        )
        self.tray_icon.run_detached()
        threading.Thread(target=self.monitor_tray_status, daemon=True).start()
        threading.Thread(target=self.watch_outside_click_and_style, daemon=True).start()


class LightweightBridge:
    def __init__(self, ctrl: AdobePodcastFlyoutController):
        self._ctrl = ctrl

    def close_flyout(self):
        self._ctrl.hide_popup()
        return {"ok": True}


def enforce_single_tray_instance():
    """Garante que apenas 1 instancia do podcast_tray.py rode por vez sem afetar o flow_tray.py."""
    pid_dir = Path("C:/adobe-podcast-mcp-profile") if os.name == "nt" else Path.home() / ".adobe-podcast-profile"
    pid_dir.mkdir(parents=True, exist_ok=True)
    pid_file = pid_dir / "podcast_tray.pid"
    my_pid = os.getpid()

    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            if old_pid != my_pid:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/F", "/PID", str(old_pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
        except Exception:
            pass

    try:
        pid_file.write_text(str(my_pid))
    except Exception:
        pass


def main():
    enforce_single_tray_instance()
    ensure_daemon()
    ctrl = AdobePodcastFlyoutController()
    ctrl.setup_tray()
    bridge = LightweightBridge(ctrl)

    pos_x, pos_y = get_flyout_position(POPUP_WIDTH, POPUP_HEIGHT)

    ctrl.window = webview.create_window(
        title="Adobe Podcast Flyout",
        html=HTML_DASHBOARD,
        js_api=bridge,
        width=POPUP_WIDTH,
        height=POPUP_HEIGHT,
        x=pos_x,
        y=pos_y,
        resizable=False,
        frameless=True,
        easy_drag=True,
        on_top=True,
    )

    def on_shown():
        ctrl.find_and_style_hwnd()
        if not ctrl.initial_load_done:
            ctrl.initial_load_done = True
            ctrl.show_popup()

    def on_closing():
        ctrl.hide_popup()
        return False

    ctrl.window.events.shown += on_shown
    ctrl.window.events.closing += on_closing

    webview.start(debug=False)


if __name__ == "__main__":
    main()
