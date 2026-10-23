import asyncio
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
import wave
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SELECTORS_PATH = os.path.join(BASE_DIR, "selectors.json")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ACCOUNTS_PATH = os.path.join(BASE_DIR, "accounts.json")
DIAGNOSTICS_DIR = os.path.join(BASE_DIR, "diagnostics")

# =====================================================================
# PERFIL ISOLADO E PORTA CDP EXCLUSIVA (ZERO CONFLITO COM FLOW STUDIO)
# - Flow Studio usa: Chrome 9333 | Daemon 9334 | C:\flow-mcp-profile
# - Adobe Podcast usa: Chrome 9343 | Daemon 9344 | C:\adobe-podcast-mcp-profile
# - Pool Multi-Contas: C:\adobe-podcast-mcp-profile, C:\adobe-podcast-mcp-profile-conta_2, ...
# =====================================================================
CHROME_PORT = int(os.environ.get("ADOBE_CHROME_PORT", "9343"))


def _obter_diretorio_perfil_base(account_id: str = "conta_1") -> str:
    env_dir = os.environ.get("ADOBE_PROFILE_DIR")
    if env_dir and account_id in ("", "conta_1", "default"):
        return env_dir
    base = r"C:\adobe-podcast-mcp-profile" if os.name == "nt" else os.path.join(os.path.expanduser("~"), ".adobe-podcast-profile")
    if not account_id or account_id in ("conta_1", "default"):
        return base
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", account_id.strip().lower())
    return f"{base}-{slug}"


def _criar_registro_conta_padrao(account_id: str = "conta_1", label: str = "Conta 1 (Principal)") -> dict:
    prof_dir = _obter_diretorio_perfil_base(account_id)
    return {
        "id": account_id,
        "label": label,
        "profile_dir": prof_dir,
        "email": "",
        "display_name": "",
        "adobe_id": "",
        "tier": "free",
        "has_premium": False,
        "logged_in": os.path.exists(os.path.join(prof_dir, ".login_completed")),
        "quota_limit_sec": 3600.0,
        "quota_used_sec": 0.0,
        "quota_remaining_sec": 3600.0,
        "quota_reset_at_epoch": 0.0,
        "file_duration_limit_sec": 1800.0,
        "file_size_limit_bytes": 524288000,
        "last_checked_at": "",
    }


def carregar_contas() -> dict:
    """
    Carrega o pool de contas do Adobe Podcast de `accounts.json`.
    Se o horario de reset diario de alguma conta ja passou, zera automaticamente o consumo em cache.
    """
    padrao = {
        "active_account": "conta_1",
        "auto_rotate": True,
        "accounts": {
            "conta_1": _criar_registro_conta_padrao("conta_1", "Conta 1 (Principal)"),
        },
    }
    dados = padrao
    if os.path.exists(ACCOUNTS_PATH):
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                lido = json.load(f)
                if isinstance(lido, dict) and isinstance(lido.get("accounts"), dict) and lido["accounts"]:
                    dados = lido
        except Exception:
            pass

    if "conta_1" not in dados["accounts"]:
        dados["accounts"]["conta_1"] = _criar_registro_conta_padrao("conta_1", "Conta 1 (Principal)")

    if dados.get("active_account") not in dados["accounts"]:
        dados["active_account"] = next(iter(dados["accounts"].keys()))

    agora = time.time()
    mudou = False
    for acc_id, acc in dados["accounts"].items():
        prof = acc.get("profile_dir") or _obter_diretorio_perfil_base(acc_id)
        acc["profile_dir"] = prof
        reset_ep = float(acc.get("quota_reset_at_epoch") or 0.0)
        if reset_ep > 0 and agora >= reset_ep:
            lim = float(acc.get("quota_limit_sec") or 3600.0)
            acc["quota_used_sec"] = 0.0
            acc["quota_remaining_sec"] = lim
            acc["quota_reset_at_epoch"] = 0.0
            mudou = True

    if mudou or not os.path.exists(ACCOUNTS_PATH):
        salvar_contas(dados)

    return dados


def salvar_contas(dados: dict) -> dict:
    try:
        with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(dados, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Aviso ao salvar accounts.json: {e}")
    return dados


def obter_conta_ativa() -> dict:
    dados = carregar_contas()
    acc_id = dados.get("active_account", "conta_1")
    return dados["accounts"].get(acc_id) or next(iter(dados["accounts"].values()))


def obter_diretorio_perfil(account_id: str = "") -> str:
    dados = carregar_contas()
    alvo = account_id.strip() if account_id else dados.get("active_account", "conta_1")
    acc = dados["accounts"].get(alvo)
    if acc and acc.get("profile_dir"):
        return acc["profile_dir"]
    return _obter_diretorio_perfil_base(alvo)


def adicionar_conta(account_id: str = "", label: str = "") -> dict:
    """Cria um novo perfil do Chrome para uma nova conta Adobe no pool multi-contas."""
    dados = carregar_contas()
    contas = dados["accounts"]
    if not account_id or not account_id.strip():
        idx = len(contas) + 1
        while f"conta_{idx}" in contas:
            idx += 1
        account_id = f"conta_{idx}"
    else:
        account_id = re.sub(r"[^A-Za-z0-9_-]", "_", account_id.strip().lower())

    if account_id in contas:
        if label:
            contas[account_id]["label"] = label
            salvar_contas(dados)
        return contas[account_id]

    num = len(contas) + 1
    rotulo = label.strip() if label else f"Conta {num} ({account_id})"
    nova = _criar_registro_conta_padrao(account_id, rotulo)
    os.makedirs(nova["profile_dir"], exist_ok=True)
    contas[account_id] = nova
    salvar_contas(dados)
    return nova


def definir_conta_ativa(account_id: str) -> dict:
    """Altera a conta ativa no pool multi-contas (criando-a caso ainda nao exista)."""
    global CHROME_PROFILE_DIR, LOGIN_MARKER_FILE
    dados = carregar_contas()
    account_id = re.sub(r"[^A-Za-z0-9_-]", "_", account_id.strip().lower())
    if account_id not in dados["accounts"]:
        adicionar_conta(account_id=account_id)
        dados = carregar_contas()

    dados["active_account"] = account_id
    salvar_contas(dados)
    CHROME_PROFILE_DIR = dados["accounts"][account_id]["profile_dir"]
    LOGIN_MARKER_FILE = os.path.join(CHROME_PROFILE_DIR, ".login_completed")
    return dados["accounts"][account_id]


def remover_conta(account_id: str) -> bool:
    dados = carregar_contas()
    account_id = re.sub(r"[^A-Za-z0-9_-]", "_", (account_id or "").strip().lower())
    if account_id not in dados["accounts"] or len(dados["accounts"]) <= 1:
        return False
    era_ativa = dados.get("active_account") == account_id
    del dados["accounts"][account_id]
    if era_ativa:
        nova_ativa = next(iter(dados["accounts"].keys()))
        dados["active_account"] = nova_ativa
        salvar_contas(dados)
        definir_conta_ativa(nova_ativa)
    else:
        salvar_contas(dados)
    return True


CHROME_PROFILE_DIR = obter_diretorio_perfil()
LOGIN_MARKER_FILE = os.path.join(CHROME_PROFILE_DIR, ".login_completed")


def ja_fez_login_inicial(profile_dir: str = "") -> bool:
    alvo = profile_dir or obter_diretorio_perfil()
    return os.path.exists(os.path.join(alvo, ".login_completed"))


def marcar_login_concluido(profile_dir: str = ""):
    alvo = profile_dir or obter_diretorio_perfil()
    os.makedirs(alvo, exist_ok=True)
    with open(os.path.join(alvo, ".login_completed"), "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat())
    dados = carregar_contas()
    for acc in dados["accounts"].values():
        if os.path.normcase(acc.get("profile_dir", "")) == os.path.normcase(alvo):
            acc["logged_in"] = True
    salvar_contas(dados)


def detectar_executavel_chrome() -> str:
    """Detecta o executavel do Google Chrome em qualquer computador (Windows, macOS ou Linux)."""
    for env_var in ("ADOBE_CHROME_EXE", "FLOW_CHROME_EXE"):
        env_chrome = os.environ.get(env_var)
        if env_chrome and os.path.exists(env_chrome):
            return env_chrome

    sistema = platform.system().lower()
    candidatos = []
    if sistema == "windows":
        local_app = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        prog_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        candidatos = [
            os.path.join(prog_files, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(prog_x86, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
            os.path.join(prog_files, r"Google\Chrome Beta\Application\chrome.exe"),
        ]
    elif sistema == "darwin":
        candidatos = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
        ]
    else:
        for cmd in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium"):
            achado = shutil.which(cmd)
            if achado:
                candidatos.append(achado)

    for c in candidatos:
        if c and os.path.exists(c):
            return c
    raise FileNotFoundError(
        "Google Chrome nao encontrado automaticamente. Defina a variavel de ambiente ADOBE_CHROME_EXE."
    )


# ==========================================
# HOT-RELOAD DE SELETORES E APIs (AUTO-HEALING)
# ==========================================
DEFAULT_SELECTORS = {
    "api_endpoints": {
        "gateway_base": "https://phonos-server-flex.adobe.io",
        "api_key": "phonos-server-prod",
        "tracks_path": "/api/v1/enhance_speech_tracks",
        "user_path": "/api/v1/user",
        "user_metrics_path": "/api/v1/user_metrics",
        "direct_upload_keyword": "direct_uploads",
    },
    "selectors": {
        "enhance_url": "https://podcast.adobe.com/enhance",
        "file_input": '#enhance-file-upload, input[type="file"][id*="enhance"], input[type="file"]',
        "choose_files_btn": '[data-testid="choose-files-button"], sp-button:has-text("Choose files"), sp-button:has-text("Escolher arquivos"), button:has-text("Choose files"), button:has-text("Escolher arquivos")',
        "download_btn": '[data-testid="download-button"], sp-button:has-text("Download"), sp-button:has-text("Baixar"), button:has-text("Download"), button:has-text("Baixar")',
        "delete_track_btn": '[data-testid="delete-track-button"], sp-action-button[label="Delete"], sp-action-button[label="Excluir"]',
        "processing_indicator": '[data-testid="enhance-speech-track-processing-animated-text"]',
        "upsell_close_btn": '[data-testid="upsell-dialog-close-button"], [aria-label="Fechar modal"], [aria-label="Close modal"], sp-close-button',
        "sign_in_btn": 'sp-button:has-text("Sign in"), sp-button:has-text("Fazer logon"), sp-button:has-text("Entrar"), button:has-text("Sign in"), button:has-text("Fazer logon")',
        "logged_out_landing": '[data-testid="logged-out-landing"]',
        "profile_dropdown_btn": '[data-testid="user-profile-dropdown-button"]',
        "track_list_container": '[data-testid="track-list-container"]',
        "action_bar": '[data-testid="action-bar"]',
        "enhance_switch": '[data-testid="enhance-preview-toggle"], sp-switch',
        "strength_slider": '[data-testid="speech-strength-container"], sp-slider, input[type="range"]',
        "model_version_picker": 'sp-picker, [data-testid*="model-version"]',
    },
    "model_versions": {
        "v2": "v2",
        "2": "v2",
        "soundlift": "v2",
        "padrao": "v2",
        "default": "v2",
        "v1": "v1",
        "1": "v1",
        "classic": "v1",
    },
}


def carregar_seletores() -> dict:
    """Recarrega selectors.json em tempo real para permitir Auto-Healing sem reiniciar processos."""
    dados = json.loads(json.dumps(DEFAULT_SELECTORS))
    if os.path.exists(SELECTORS_PATH):
        try:
            with open(SELECTORS_PATH, "r", encoding="utf-8") as f:
                custom = json.load(f)
                for k, v in custom.items():
                    if isinstance(v, dict) and k in dados:
                        dados[k].update(v)
                    else:
                        dados[k] = v
        except Exception as e:
            print(f"[!] Aviso ao ler selectors.json: {e}")
    return dados


def atualizar_seletores(novos_dados: dict) -> dict:
    atual = carregar_seletores()
    for k, v in novos_dados.items():
        if isinstance(v, dict) and k in atual and isinstance(atual[k], dict):
            atual[k].update(v)
        elif not isinstance(v, dict) and k in atual.get("selectors", {}):
            atual["selectors"][k] = v
        elif not isinstance(v, dict) and k in atual.get("api_endpoints", {}):
            atual["api_endpoints"][k] = v
        elif not isinstance(v, dict) and k in atual.get("model_versions", {}):
            atual["model_versions"][k] = v
        else:
            atual[k] = v
    with open(SELECTORS_PATH, "w", encoding="utf-8") as f:
        json.dump(atual, f, indent=2, ensure_ascii=False)
    return atual


def carregar_config() -> dict:
    padrao = {
        "processos_simultaneos": 1,
        "espera_minima": 2,
        "espera_maxima": 5,
        "modo_invisivel": True,
        "versao_modelo": "v2",
        "forca_padrao": 90,
        "ruido_fundo": 10,
        "musica_fundo": 10,
        "componente_faixa": "full",
        "limpar_apos_download": True,
        "timeout_processamento_seg": 900,
    }
    for p in ("config.json", CONFIG_PATH):
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    padrao.update(json.load(f))
                break
            except Exception:
                pass
    return padrao


def resolver_versao_modelo(versao: str) -> str:
    sels = carregar_seletores()
    if not versao:
        return "v2"
    chave = str(versao).strip().lower()
    return sels.get("model_versions", {}).get(chave, "v2" if "2" in chave else "v1")


def resolver_caminho_midia(caminho: str, workspace: str = "") -> str:
    if not caminho:
        return ""
    caminho = str(caminho).strip().strip('"').strip("'")
    if os.path.isabs(caminho) and os.path.exists(caminho):
        return os.path.abspath(caminho)
    if workspace:
        cand = os.path.join(workspace, caminho)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    if os.path.exists(caminho):
        return os.path.abspath(caminho)
    for pasta in (
        "audios_entrada",
        "audios_aprimorados",
        os.path.join(BASE_DIR, "audios_entrada"),
        os.path.join(BASE_DIR, "audios_aprimorados"),
    ):
        cand = os.path.join(pasta, caminho)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return os.path.abspath(caminho)


# ============================================================
# MEDICAO EXATA DE DURACAO DE AUDIO/VIDEO E FORMATACAO
# ============================================================
def obter_duracao_midia_segundos(caminho_arquivo: str) -> float:
    """
    Descobre a duracao exata (em segundos) do arquivo de audio ou video de entrada
    antes de enviar ao Adobe Podcast, usando ffprobe, ffmpeg ou wave nativo.
    """
    if not caminho_arquivo or not os.path.exists(caminho_arquivo):
        return 0.0

    # 1. Tenta via ffprobe
    ffprobe_exe = shutil.which("ffprobe")
    if ffprobe_exe:
        try:
            res = subprocess.run(
                [
                    ffprobe_exe,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    caminho_arquivo,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            val = float((res.stdout or "").strip())
            if val > 0:
                return round(val, 2)
        except Exception:
            pass

    # 2. Tenta via ffmpeg -i (parseia Duration: HH:MM:SS.ms no stderr)
    ffmpeg_exe = shutil.which("ffmpeg")
    if ffmpeg_exe:
        try:
            res = subprocess.run(
                [ffmpeg_exe, "-i", caminho_arquivo],
                capture_output=True,
                text=True,
                timeout=15,
            )
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", res.stderr or "")
            if m:
                h, mins, secs = int(m.group(1)), int(m.group(2)), float(m.group(3))
                total = h * 3600.0 + mins * 60.0 + secs
                if total > 0:
                    return round(total, 2)
        except Exception:
            pass

    # 3. Fallback nativo Python para arquivos .wav
    if caminho_arquivo.lower().endswith(".wav"):
        try:
            with wave.open(caminho_arquivo, "rb") as wf:
                frames = wf.getnframes()
                rate = wf.getframerate()
                if rate > 0:
                    return round(frames / float(rate), 2)
        except Exception:
            pass

    return 0.0


def formatar_duracao(segundos: float) -> str:
    """Formata segundos em string amigavel (ex: '14m 32s' ou '1h 12m 05s')."""
    seg = max(0, int(round(float(segundos or 0))))
    h = seg // 3600
    m = (seg % 3600) // 60
    s = seg % 60
    if h > 0:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def formatar_tempo_reset(segundos: float) -> str:
    """Formata o tempo ate o reset diario da quota do Adobe Podcast."""
    seg = max(0, int(round(float(segundos or 0))))
    if seg <= 0:
        return "Pronto / Resetado"
    h = seg // 3600
    m = (seg % 3600) // 60
    if h > 0:
        return f"{h}h {m:02d}m"
    return f"{m} min"


VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v",
    ".flv", ".wmv", ".ts", ".mts", ".m2ts", ".3gp", ".mpeg", ".mpg",
}


def eh_arquivo_video(caminho_arquivo: str) -> bool:
    if not caminho_arquivo:
        return False
    _, ext = os.path.splitext(caminho_arquivo)
    return ext.lower() in VIDEO_EXTENSIONS


def extrair_audio_de_video_se_necessario(caminho_entrada: str) -> tuple:
    """
    REGRA PADRAO DE ECONOMIA E FLUXO DE EDICAO:
    Se `caminho_entrada` for um arquivo de video (.mp4, .mov, .mkv, etc.), extrai automaticamente
    APENAS a faixa de audio via FFmpeg antes de enviar para o Adobe Podcast.
    Isso reduz videos pesados (ex: 300MB-2GB) para poucos megabytes de audio puro, acelera o upload
    em 10x, evita estourar o limite de 500MB da conta gratuita e entrega o audio limpo na mesma
    duracao exata para o editor apenas substituir a faixa original do video.
    Retorna `(caminho_para_upload, foi_extraido_temp)`.
    """
    if not caminho_entrada or not os.path.exists(caminho_entrada):
        return caminho_entrada, False

    if not eh_arquivo_video(caminho_entrada):
        return caminho_entrada, False

    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        return caminho_entrada, False

    pasta_temp = os.path.join(BASE_DIR, "temp_extracted_audio")
    os.makedirs(pasta_temp, exist_ok=True)

    base_nome = os.path.splitext(os.path.basename(caminho_entrada))[0]
    nome_limpo = re.sub(r"[^A-Za-z0-9_-]", "_", base_nome)[:60] or "audio"
    duracao_sec = obter_duracao_midia_segundos(caminho_entrada)

    # Para audios ate 40 min, extrai em WAV PCM 48kHz 16-bit Mono (~5.5 MB/min, zero perda).
    # Acima de 40 min, usa M4A AAC 256kbps para garantir arquivo sempre leve (< 80 MB).
    if duracao_sec > 2400:
        caminho_audio_temp = os.path.join(pasta_temp, f"{nome_limpo}_{uuid.uuid4().hex[:6]}.m4a")
        cmd = [
            ffmpeg_exe, "-y", "-i", caminho_entrada,
            "-vn", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
            caminho_audio_temp,
        ]
    else:
        caminho_audio_temp = os.path.join(pasta_temp, f"{nome_limpo}_{uuid.uuid4().hex[:6]}.wav")
        cmd = [
            ffmpeg_exe, "-y", "-i", caminho_entrada,
            "-vn", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "1",
            caminho_audio_temp,
        ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=180)
        if os.path.exists(caminho_audio_temp) and os.path.getsize(caminho_audio_temp) > 500:
            return caminho_audio_temp, True
    except Exception as e:
        print(f"[!] Aviso ao extrair audio do video {caminho_entrada}: {e}")

    return caminho_entrada, False


# ============================================================
# DIAGNOSTICO AUTO-HEALING (DEEP SHADOW-DOM + REDE ADOBE)
# ============================================================
async def coletar_diagnostico_pagina(page, motivo="", recent_network=None, take_screenshot=True) -> dict:
    """
    Coleta um snapshot completo da pagina do Adobe Podcast, atravessando recursivamente
    todos os Shadow DOMs (<sp-theme>, <sp-button>, <sp-slider>, <overlay-trigger>),
    data-testids, estado do Adobe IMS e chamadas de rede para `phonos-server-flex.adobe.io`.
    """
    os.makedirs(DIAGNOSTICS_DIR, exist_ok=True)
    screenshot_path = os.path.join(DIAGNOSTICS_DIR, "last_diagnostic_screenshot.png")
    report_path = os.path.join(DIAGNOSTICS_DIR, "last_diagnostic_report.json")

    diag = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "reason": motivo,
        "selectors_file": SELECTORS_PATH,
        "podcastapi_file": os.path.abspath(__file__),
        "current_selectors": carregar_seletores(),
        "recent_network_calls": recent_network or [],
        "page_url": None,
        "params": {},
        "dom_snapshot": {},
        "screenshot_path": None,
        "auto_healing_instructions": (
            "Se um seletor CSS/Shadow-DOM ou endpoint do Adobe Podcast mudou: "
            "1) Analise `dom_snapshot.buttons`, `dom_snapshot.inputs`, `dom_snapshot.testids`, `dom_snapshot.spectrum_tags` e `recent_network_calls`. "
            "2) Visualize `screenshot_path` para inspecionar a tela atual do Adobe Podcast. "
            "3) Atualize `selectors.json` usando a ferramenta MCP `podcast_update_selectors` (ou edite `podcastapi.py` e chame `podcast_reload_daemon`). "
            "4) Reexecute `podcast_enhance` imediatamente."
        ),
    }

    if not page or page.is_closed():
        diag["error"] = "Page is closed or unavailable"
        return diag

    try:
        diag["page_url"] = page.url
        diag["params"] = await obter_parametros_pagina(page)
        diag["dom_snapshot"] = await page.evaluate("""() => {
            function walkShadow(root, out = []) {
                if (!root || !root.querySelectorAll) return out;
                for (const el of root.querySelectorAll('*')) {
                    const tag = el.tagName ? el.tagName.toLowerCase() : '';
                    const testid = el.getAttribute ? (el.getAttribute('data-testid') || '') : '';
                    const aria = el.getAttribute ? (el.getAttribute('aria-label') || el.getAttribute('label') || '') : '';
                    const id = el.id || '';
                    const type = el.getAttribute ? (el.getAttribute('type') || '') : '';
                    const text = (el.innerText || el.textContent || '').trim().slice(0, 90);
                    const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : {width: 0, height: 0};
                    const visible = (rect.width > 0 && rect.height > 0) || type === 'file' || id.includes('upload');
                    out.push({ tag, id, testid, aria, type, text, visible });
                    if (el.shadowRoot) {
                        walkShadow(el.shadowRoot, out);
                    }
                }
                return out;
            }

            const all = walkShadow(document);
            const buttons = all.filter(x =>
                (x.tag.includes('button') || x.tag.includes('picker') || x.tag.includes('slider') || x.tag.includes('switch') || x.testid || x.aria)
                && (x.visible || x.testid)
            ).slice(0, 140);

            const inputs = all.filter(x => x.tag === 'input' || x.type === 'file' || x.id.includes('upload'));
            const testids = Array.from(new Set(all.map(x => x.testid).filter(Boolean))).sort();
            const spectrumTags = Array.from(new Set(all.map(x => x.tag).filter(t => t.includes('-')))).sort();
            const allAlerts = all.filter(x =>
                x.testid.includes('error') || x.testid.includes('upsell') || x.testid.includes('modal') || x.tag.includes('toast')
            );
            const visibleAlerts = allAlerts.filter(x => x.visible).map(x => ({ tag: x.tag, testid: x.testid, text: x.text })).slice(0, 20);

            return {
                title: document.title,
                testids,
                spectrum_tags: spectrumTags,
                inputs,
                buttons,
                alerts_or_modals: visibleAlerts,
                hidden_modals_in_dom_count: allAlerts.length - visibleAlerts.length
            };
        }""")

        # Verifica em tempo real a saude de cada seletor do selectors.json na pagina atual
        sels_map = diag["current_selectors"].get("selectors", {})
        selector_context_map = {
            "file_input": "always_when_logged_in",
            "choose_files_btn": "always_when_logged_in",
            "profile_dropdown_btn": "always_when_logged_in",
            "model_version_picker": "always_when_logged_in",
            "download_btn": "when_track_loaded",
            "delete_track_btn": "when_track_loaded",
            "track_list_container": "when_track_loaded",
            "action_bar": "when_track_loaded",
            "enhance_switch": "when_track_loaded_or_landing",
            "strength_slider": "when_track_loaded",
            "processing_indicator": "when_track_processing",
            "upsell_close_btn": "modal_template",
            "sign_in_btn": "when_logged_out",
            "logged_out_landing": "when_logged_out",
        }
        selector_health = {}
        for sel_key, sel_val in sels_map.items():
            if sel_key == "enhance_url" or not isinstance(sel_val, str):
                continue
            ctx_label = selector_context_map.get(sel_key, "general")
            try:
                cnt = await page.locator(sel_val).count()
                selector_health[sel_key] = {
                    "selector": sel_val,
                    "context": ctx_label,
                    "matched_count": cnt,
                    "matched": cnt > 0,
                }
            except Exception as sel_err:
                selector_health[sel_key] = {
                    "selector": sel_val,
                    "context": ctx_label,
                    "matched_count": 0,
                    "matched": False,
                    "error": str(sel_err),
                }
        diag["selector_health"] = selector_health

        if take_screenshot:
            await page.screenshot(path=screenshot_path, full_page=False)
            diag["screenshot_path"] = screenshot_path
    except Exception as e:
        diag["collect_error"] = str(e)

    try:
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(diag, f, indent=2, ensure_ascii=False)
        diag["report_path"] = report_path
    except Exception:
        pass

    return diag


# ==============================================================
# CONTROLE DO CHROME ISOLADO (PORTA 9343) E JANELA OFF-SCREEN
# ==============================================================
def iniciar_chrome_podcast(invisivel=True, porta=CHROME_PORT, profile_dir=CHROME_PROFILE_DIR):
    """Inicia o Google Chrome em perfil isolado exclusivo do Adobe Podcast na porta 9343."""
    chrome_exe = detectar_executavel_chrome()
    os.makedirs(profile_dir, exist_ok=True)
    enhance_url = carregar_seletores()["selectors"].get("enhance_url", "https://podcast.adobe.com/enhance")
    pos_arg = "--window-position=-2400,0" if invisivel else "--window-position=120,90"
    args_list = [
        f"--remote-debugging-port={porta}",
        f'--user-data-dir="{profile_dir}"' if os.name == "nt" else f"--user-data-dir={profile_dir}",
        "--window-size=1280,820",
        pos_arg,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        f'"{enhance_url}"' if os.name == "nt" else enhance_url,
    ]
    print(
        f"[*] Abrindo Google Chrome Adobe Podcast ({'MODO INVISIVEL OFF-SCREEN' if invisivel else 'VISIVEL NA TELA'}) na porta {porta}..."
    )
    if os.name == "nt":
        cmd_str = f'"{chrome_exe}" ' + " ".join(args_list)
        ps_cmd = (
            f"Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
            f"-Arguments @{{CommandLine='{cmd_str}'}} | Out-Null"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        subprocess.Popen(
            [chrome_exe] + args_list,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def obter_perfil_chrome_rodando(porta: int = CHROME_PORT) -> str:
    """
    Retorna o caminho de `--user-data-dir` do processo do Google Chrome que esta rodando na `porta` (9343),
    ou string vazia `""` se nenhum processo Chrome estiver escutando nessa porta.
    """
    try:
        import psutil

        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                nome = (p.info.get("name") or "").lower()
                if "chrome" in nome:
                    cmdline = p.info.get("cmdline") or []
                    cmd = " ".join(cmdline)
                    if f"--remote-debugging-port={porta}" in cmd and "--type=" not in cmd:
                        for arg in cmdline:
                            if arg.startswith("--user-data-dir="):
                                return arg.split("=", 1)[1].strip('"').strip("'")
                        m = re.search(r'--user-data-dir=(?:"([^"]+)"|(\S+))', cmd)
                        if m:
                            return (m.group(1) or m.group(2) or "").strip()
            except Exception:
                pass
    except Exception:
        pass
    return ""


def fechar_chrome_podcast(porta: int = CHROME_PORT):
    """
    Encerra APENAS os processos do Google Chrome iniciados na porta `porta` (9343),
    permitindo alternar entre perfis/contas sem tocar no Flow Studio (9333) nem no Chrome pessoal.
    """
    try:
        import psutil

        raizes = []
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                nome = (p.info.get("name") or "").lower()
                if "chrome" in nome:
                    cmd = " ".join(p.info.get("cmdline") or [])
                    if f"--remote-debugging-port={porta}" in cmd and "--type=" not in cmd:
                        raizes.append(p)
            except Exception:
                pass

        todos_procs = []
        for proc in raizes:
            try:
                filhos = proc.children(recursive=True)
                for f in filhos:
                    todos_procs.append(f)
                    try:
                        f.terminate()
                    except Exception:
                        pass
                todos_procs.append(proc)
                proc.terminate()
            except Exception:
                pass

        _, alive = psutil.wait_procs(todos_procs, timeout=3)
        for p in alive:
            try:
                p.kill()
            except Exception:
                pass
        if alive:
            psutil.wait_procs(alive, timeout=2)
    except Exception as e:
        print(f"[!] Aviso ao fechar Chrome da porta {porta}: {e}")


def controlar_visibilidade_taskbar(visivel: bool, porta: int = CHROME_PORT):
    """
    No Windows, oculta ou exibe o icone da janela do Chrome da porta `porta` (9343) na Taskbar
    usando ITaskbarList (DeleteTab / AddTab), sem tocar no Chrome do Flow (9333) nem no Chrome pessoal.
    """
    if platform.system() != "Windows":
        return False
    try:
        import ctypes
        from ctypes import wintypes
        import psutil

        user32 = ctypes.windll.user32
        ole32 = ctypes.windll.ole32

        h_default = user32.OpenDesktopW("Default", 0, False, 0x10000000)
        if h_default:
            user32.SetThreadDesktop(h_default)

        ole32.CoInitialize(None)

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_byte * 8),
            ]

        CLSID_TaskbarList = GUID(
            0x56FDF344,
            0xFD6D,
            0x11D0,
            (ctypes.c_byte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
        )
        IID_ITaskbarList = GUID(
            0x56FDF342,
            0xFD6D,
            0x11D0,
            (ctypes.c_byte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90),
        )

        p_tbl = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(
            ctypes.byref(CLSID_TaskbarList),
            None,
            1,
            ctypes.byref(IID_ITaskbarList),
            ctypes.byref(p_tbl),
        )
        if hr != 0 or not p_tbl.value:
            return False

        vtable = ctypes.cast(p_tbl, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        hr_init = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[3])
        hr_init(p_tbl)

        add_tab = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(vtable[4])
        del_tab = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.HWND)(vtable[5])

        target_pids = set()
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            if p.info["name"] == "chrome.exe":
                cmd = " ".join(p.info["cmdline"] or [])
                if f"--remote-debugging-port={porta}" in cmd:
                    target_pids.add(p.info["pid"])

        def cb(hwnd, lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in target_pids:
                len_cls = 256
                cls_name = ctypes.create_unicode_buffer(len_cls)
                user32.GetClassNameW(hwnd, cls_name, len_cls)
                if "Chrome_WidgetWin_1" in cls_name.value:
                    if visivel:
                        add_tab(p_tbl, hwnd)
                    else:
                        del_tab(p_tbl, hwnd)
            return 1

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        return True
    except Exception:
        return False


async def posicionar_janela_chrome(page, invisivel=True):
    """Move a janela do Chrome do Adobe Podcast para fora da tela (-2400, 0) ou para a tela (120, 90)."""
    try:
        cdp = await page.context.new_cdp_session(page)
        win_info = await cdp.send("Browser.getWindowForTarget")
        win_id = win_info["windowId"]
        left_pos = -2400 if invisivel else 120
        top_pos = 0 if invisivel else 90
        await cdp.send(
            "Browser.setWindowBounds",
            {
                "windowId": win_id,
                "bounds": {
                    "left": left_pos,
                    "top": top_pos,
                    "width": 1280,
                    "height": 820,
                    "windowState": "normal",
                },
            },
        )
        if not invisivel:
            try:
                await cdp.send("Page.bringToFront")
            except Exception:
                pass
        await cdp.detach()

        controlar_visibilidade_taskbar(visivel=not invisivel, porta=CHROME_PORT)
        return True
    except Exception as e:
        print(f"[!] Aviso ao posicionar janela via CDP: {e}")
        return False


# ============================================================
# AUTENTICACAO ADOBE IMS E PARAMETROS DE SESSAO
# ============================================================
async def obter_parametros_pagina(page) -> dict:
    """
    Verifica se o usuario esta autenticado no Adobe Podcast (via window.adobeIMS e DOM/Shadow-DOM)
    e extrai o Bearer token ativo para consultas/exports diretos na API phonos-server-flex.adobe.io.
    Respeita dinamicamente os seletores configurados em `selectors.json` (Auto-Healing).
    """
    sels = carregar_seletores().get("selectors", {})
    sel_file_input = sels.get("file_input", '#enhance-file-upload, input[type="file"]')
    sel_profile_btn = sels.get("profile_dropdown_btn", '[data-testid="user-profile-dropdown-button"]')
    sel_logged_out = sels.get("logged_out_landing", '[data-testid="logged-out-landing"]')

    try:
        params = await page.evaluate(
            """(selArgs) => {
            const [selFile, selProf, selOut] = selArgs;

            function queryShadow(root, selector) {
                if (!root || !selector) return null;
                try {
                    const direct = root.querySelector(selector);
                    if (direct) return direct;
                } catch (e) {}
                if (!root.querySelectorAll) return null;
                for (const el of root.querySelectorAll('*')) {
                    if (el.shadowRoot) {
                        const found = queryShadow(el.shadowRoot, selector);
                        if (found) return found;
                    }
                }
                return null;
            }

            let imsSigned = false;
            let accessToken = '';
            try {
                if (window.adobeIMS && typeof window.adobeIMS.isSignedInUser === 'function') {
                    imsSigned = Boolean(window.adobeIMS.isSignedInUser());
                }
                if (window.adobeIMS && typeof window.adobeIMS.getAccessToken === 'function') {
                    const tkObj = window.adobeIMS.getAccessToken();
                    if (tkObj && tkObj.token) accessToken = tkObj.token;
                }
            } catch (e) {}

            if (!accessToken) {
                try {
                    for (let i = 0; i < sessionStorage.length; i++) {
                        const k = sessionStorage.key(i);
                        if (k && k.includes('adobeid_ims_access_token')) {
                            const raw = sessionStorage.getItem(k) || '';
                            if (raw.startsWith('{')) {
                                const parsed = JSON.parse(raw);
                                if (parsed.tokenValue) accessToken = parsed.tokenValue;
                            }
                        }
                    }
                } catch (e) {}
            }

            const hasFileInput = Boolean(queryShadow(document, selFile));
            const hasProfileBtn = Boolean(queryShadow(document, selProf));
            const hasLoggedOutLanding = Boolean(queryShadow(document, selOut));
            const loggedIn = Boolean(imsSigned || accessToken || hasProfileBtn || (hasFileInput && !hasLoggedOutLanding));

            return {
                loggedIn,
                imsSigned,
                hasAccessToken: Boolean(accessToken),
                accessToken: accessToken || '',
                hasFileInput,
                hasProfileBtn,
                hasLoggedOutLanding,
                pathname: window.location.pathname,
                url: window.location.href
            };
        }""",
            [sel_file_input, sel_profile_btn, sel_logged_out],
        )
        return params
    except Exception:
        return {"loggedIn": False, "accessToken": "", "url": page.url if page else ""}


async def fechar_modais_upsell(page):
    """Fecha automaticamente qualquer modal promocional do Adobe Firefly Pro que apareca na tela."""
    sels = carregar_seletores()["selectors"]
    try:
        close_btn = page.locator(sels["upsell_close_btn"]).first
        if await close_btn.count() > 0 and await close_btn.is_visible():
            await close_btn.click(timeout=2000)
            await asyncio.sleep(0.3)
    except Exception:
        pass
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass


# ============================================================
# ACOMPANHAMENTO DE QUOTA DIARIA (1H FREE / 4H PAGO) E POOL MULTI-CONTAS
# ============================================================
async def consultar_quota_plataforma(page, account_id: str = "") -> dict:
    """
    Consulta diretamente na plataforma do Adobe Podcast (`phonos-server-flex.adobe.io`):
      - `/api/v1/user_metrics`: `quota_used_ms`, `quota_limit_ms`, `quota_reset_time_ms`
      - `/api/v1/user`: `has_premium_access`, `enhance_speech_limits` (1h/dia Free vs 4h/dia Premium)
      - `window.adobeIMS.getProfile()`: e-mail e nome da conta Adobe logada
    Atualiza automaticamente o registro da conta em `accounts.json` e retorna o relatorio completo.
    """
    sels = carregar_seletores()
    api_cfg = sels["api_endpoints"]
    gateway_base = api_cfg.get("gateway_base", "https://phonos-server-flex.adobe.io")
    api_key = api_cfg.get("api_key", "phonos-server-prod")
    user_path = api_cfg.get("user_path", "/api/v1/user")
    metrics_path = api_cfg.get("user_metrics_path", "/api/v1/user_metrics")

    dados_contas = carregar_contas()
    alvo_id = account_id.strip() if account_id else dados_contas.get("active_account", "conta_1")
    if alvo_id not in dados_contas["accounts"]:
        adicionar_conta(alvo_id)
        dados_contas = carregar_contas()

    acc = dados_contas["accounts"][alvo_id]
    params = await obter_parametros_pagina(page)
    token = params.get("accessToken", "")
    logado = bool(params.get("loggedIn") and token)

    if not logado:
        acc["logged_in"] = False
        acc["last_checked_at"] = datetime.now().isoformat(timespec="seconds")
        salvar_contas(dados_contas)
        return {
            "ok": False,
            "logged_in": False,
            "account_id": alvo_id,
            "account": acc,
            "error": "Conta nao autenticada no Adobe Podcast.",
        }

    raw = await page.evaluate(
        """async (args) => {
            const [gatewayBase, metricsPath, userPath, apiKey, token] = args;
            const headers = {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'X-Api-Key': apiKey,
                'Authorization': `Bearer ${token}`
            };
            let metrics = null;
            let currentUser = null;
            let profile = null;
            try {
                const r1 = await fetch(`${gatewayBase}${metricsPath}?time=${Date.now()}`, { method: 'GET', headers });
                if (r1.ok) metrics = await r1.json();
            } catch (e) {}
            try {
                const r2 = await fetch(`${gatewayBase}${userPath}?time=${Date.now()}`, { method: 'GET', headers });
                if (r2.ok) {
                    const uData = await r2.json();
                    currentUser = uData.current_user || uData.currentUser || uData;
                }
            } catch (e) {}
            try {
                if (window.adobeIMS && typeof window.adobeIMS.getProfile === 'function') {
                    profile = await window.adobeIMS.getProfile();
                }
            } catch (e) {}
            return { metrics, currentUser, profile };
        }""",
        [gateway_base, metrics_path, user_path, api_key, token],
    )

    metrics = (raw or {}).get("metrics") or {}
    curr_user = (raw or {}).get("currentUser") or {}
    profile = (raw or {}).get("profile") or {}
    limits = curr_user.get("enhance_speech_limits") or curr_user.get("enhanceSpeechLimits") or {}

    has_premium = bool(curr_user.get("has_premium_access") or curr_user.get("hasPremiumAccess"))
    tier = "premium" if has_premium else "free"

    # Limite diario oficial do Adobe Podcast: Free = 3600s (1 hora), Premium = 14400s (4 horas)
    default_limit_ms = 14400000 if has_premium else 3600000
    limit_from_user_ms = int(limits.get("daily_duration_limit_seconds") or 0) * 1000
    quota_limit_ms = int(metrics.get("quota_limit_ms") or metrics.get("quotaLimitMs") or limit_from_user_ms or default_limit_ms)
    quota_used_ms = int(metrics.get("quota_used_ms") or metrics.get("quotaUsedMs") or 0)
    quota_reset_ms = int(metrics.get("quota_reset_time_ms") or metrics.get("quotaResetTimeMs") or 0)
    quota_remaining_ms = max(0, quota_limit_ms - quota_used_ms)

    quota_limit_sec = round(quota_limit_ms / 1000.0, 2)
    quota_used_sec = round(quota_used_ms / 1000.0, 2)
    quota_remaining_sec = round(quota_remaining_ms / 1000.0, 2)
    quota_reset_sec = round(quota_reset_ms / 1000.0, 2)

    file_dur_limit_sec = float(limits.get("file_duration_limit_seconds") or (7200.0 if has_premium else 1800.0))
    file_size_limit_bytes = int(limits.get("file_size_limit_bytes") or (1073741824 if has_premium else 524288000))

    email = profile.get("email") or curr_user.get("email") or acc.get("email") or ""
    display_name = profile.get("displayName") or profile.get("name") or acc.get("display_name") or ""
    adobe_id = curr_user.get("adobe_id") or profile.get("userId") or acc.get("adobe_id") or ""

    agora = time.time()
    acc.update({
        "email": email,
        "display_name": display_name,
        "adobe_id": adobe_id,
        "tier": tier,
        "has_premium": has_premium,
        "logged_in": True,
        "quota_limit_sec": quota_limit_sec,
        "quota_used_sec": quota_used_sec,
        "quota_remaining_sec": quota_remaining_sec,
        "quota_reset_at_epoch": round(agora + quota_reset_sec, 2) if quota_reset_sec > 0 else 0.0,
        "file_duration_limit_sec": file_dur_limit_sec,
        "file_size_limit_bytes": file_size_limit_bytes,
        "last_checked_at": datetime.now().isoformat(timespec="seconds"),
    })
    dados_contas["accounts"][alvo_id] = acc
    salvar_contas(dados_contas)
    marcar_login_concluido(acc["profile_dir"])

    return {
        "ok": True,
        "logged_in": True,
        "account_id": alvo_id,
        "label": acc.get("label", alvo_id),
        "email": email,
        "display_name": display_name,
        "tier": tier,
        "tier_label": "Pago / Premium (4h/dia)" if has_premium else "Gratuito / Free (1h/dia)",
        "has_premium": has_premium,
        "quota_limit_sec": quota_limit_sec,
        "quota_used_sec": quota_used_sec,
        "quota_remaining_sec": quota_remaining_sec,
        "quota_limit_min": round(quota_limit_sec / 60.0, 2),
        "quota_used_min": round(quota_used_sec / 60.0, 2),
        "quota_remaining_min": round(quota_remaining_sec / 60.0, 2),
        "quota_limit_human": formatar_duracao(quota_limit_sec),
        "quota_used_human": formatar_duracao(quota_used_sec),
        "quota_remaining_human": formatar_duracao(quota_remaining_sec),
        "quota_usage_percent": round((quota_used_sec / quota_limit_sec) * 100.0, 1) if quota_limit_sec > 0 else 0.0,
        "quota_reset_in_sec": quota_reset_sec,
        "quota_reset_in_human": formatar_tempo_reset(quota_reset_sec),
        "file_duration_limit_sec": file_dur_limit_sec,
        "file_duration_limit_min": round(file_dur_limit_sec / 60.0, 1),
        "file_size_limit_mb": round(file_size_limit_bytes / (1024 * 1024), 1),
        "profile_dir": acc.get("profile_dir"),
        "last_checked_at": acc.get("last_checked_at"),
    }


def resumo_pool_contas() -> dict:
    """Retorna o resumo consolidado de todas as contas cadastradas no pool multi-contas."""
    dados = carregar_contas()
    ativa_id = dados.get("active_account", "conta_1")
    agora = time.time()
    lista = []
    total_restante_sec = 0.0
    total_limite_sec = 0.0

    for acc_id, acc in dados["accounts"].items():
        lim = float(acc.get("quota_limit_sec") or 3600.0)
        usado = float(acc.get("quota_used_sec") or 0.0)
        rest = max(0.0, float(acc.get("quota_remaining_sec") if acc.get("quota_remaining_sec") is not None else (lim - usado)))
        reset_ep = float(acc.get("quota_reset_at_epoch") or 0.0)
        reset_in = max(0.0, reset_ep - agora) if reset_ep > 0 else 0.0

        total_restante_sec += rest
        total_limite_sec += lim

        lista.append({
            "id": acc_id,
            "label": acc.get("label") or acc_id,
            "is_active": acc_id == ativa_id,
            "email": acc.get("email") or "",
            "display_name": acc.get("display_name") or "",
            "tier": acc.get("tier") or "free",
            "tier_label": "Premium (4h/dia)" if acc.get("has_premium") else "Gratuito (1h/dia)",
            "logged_in": bool(acc.get("logged_in") or os.path.exists(os.path.join(acc.get("profile_dir", ""), ".login_completed"))),
            "profile_dir": acc.get("profile_dir"),
            "quota_limit_min": round(lim / 60.0, 2),
            "quota_used_min": round(usado / 60.0, 2),
            "quota_remaining_min": round(rest / 60.0, 2),
            "quota_limit_human": formatar_duracao(lim),
            "quota_used_human": formatar_duracao(usado),
            "quota_remaining_human": formatar_duracao(rest),
            "quota_reset_in_human": formatar_tempo_reset(reset_in),
            "last_checked_at": acc.get("last_checked_at") or "",
        })

    return {
        "active_account": ativa_id,
        "auto_rotate": bool(dados.get("auto_rotate", True)),
        "total_accounts": len(lista),
        "pool_remaining_min": round(total_restante_sec / 60.0, 2),
        "pool_limit_min": round(total_limite_sec / 60.0, 2),
        "pool_remaining_human": formatar_duracao(total_restante_sec),
        "accounts": lista,
    }


def escolher_proxima_conta_com_quota(duracao_audio_sec: float, conta_excluir: str = "") -> str:
    """
    Procura no pool de contas (`accounts.json`) outra conta que ja tenha feito login
    e possua saldo de tempo diario suficiente para processar `duracao_audio_sec`.
    Retorna o `account_id` escolhido ou string vazia `""` se nenhuma estiver disponivel.
    """
    dados = carregar_contas()
    excluir = {s.strip() for s in conta_excluir.split(",") if s.strip()}
    candidatas = []
    nao_verificadas = []
    for idx, (acc_id, acc) in enumerate(dados["accounts"].items()):
        if acc_id in excluir:
            continue
        prof_dir = acc.get("profile_dir") or ""
        tem_login = bool(acc.get("logged_in") or os.path.exists(os.path.join(prof_dir, ".login_completed")))
        if not tem_login:
            continue
        rest = float(acc.get("quota_remaining_sec") or 0.0)
        file_lim = float(acc.get("file_duration_limit_sec") or 1800.0)
        if duracao_audio_sec <= 0 or (rest + 2.0 >= duracao_audio_sec and file_lim + 2.0 >= duracao_audio_sec):
            candidatas.append((rest, -idx, acc_id))
        elif not acc.get("last_checked_at") or float(acc.get("quota_reset_at_epoch") or 0.0) <= 0.0:
            nao_verificadas.append(acc_id)

    if candidatas:
        # Prioriza a conta com maior tempo restante (desempatando pela ordem de cadastro)
        candidatas.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return candidatas[0][2]
    if nao_verificadas:
        return nao_verificadas[0]
    return ""


# ============================================================
# CONSULTAS E OPERACOES HIBRIDAS (DOM + PHONOS API)
# ============================================================
async def listar_tracks_api(page) -> list:
    """Lista todas as tracks de Enhance Speech na conta do usuario via phonos-server-flex.adobe.io."""
    sels = carregar_seletores()
    api_cfg = sels["api_endpoints"]
    params = await obter_parametros_pagina(page)
    token = params.get("accessToken", "")
    if not token:
        return []

    res = await page.evaluate(
        """async (args) => {
            const [gatewayBase, tracksPath, apiKey, token] = args;
            try {
                const url = `${gatewayBase}${tracksPath}?time=${Date.now()}`;
                const r = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-Api-Key': apiKey,
                        'Authorization': `Bearer ${token}`
                    }
                });
                if (!r.ok) return [];
                const data = await r.json();
                return Array.isArray(data) ? data : [];
            } catch (e) {
                return [];
            }
        }""",
        [api_cfg["gateway_base"], api_cfg["tracks_path"], api_cfg["api_key"], token],
    )
    return res if isinstance(res, list) else []


async def consultar_track_api(page, track_id: str) -> dict:
    """Consulta o status detalhado de uma track especifica na API do Adobe Podcast."""
    sels = carregar_seletores()
    api_cfg = sels["api_endpoints"]
    params = await obter_parametros_pagina(page)
    token = params.get("accessToken", "")
    if not token or not track_id:
        return {}

    res = await page.evaluate(
        """async (args) => {
            const [gatewayBase, tracksPath, trackId, apiKey, token] = args;
            try {
                const url = `${gatewayBase}${tracksPath}/${trackId}?time=${Date.now()}`;
                const r = await fetch(url, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-Api-Key': apiKey,
                        'Authorization': `Bearer ${token}`
                    }
                });
                if (!r.ok) return { _http_status: r.status };
                const data = await r.json();
                data._http_status = r.status;
                return data;
            } catch (e) {
                return { _error: String(e) };
            }
        }""",
        [api_cfg["gateway_base"], api_cfg["tracks_path"], track_id, api_cfg["api_key"], token],
    )
    return res if isinstance(res, dict) else {}


async def deletar_track_api(page, track_id: str) -> bool:
    """Remove uma track processada da lista do Adobe Podcast para nao acumular arquivos antigos."""
    sels = carregar_seletores()
    api_cfg = sels["api_endpoints"]
    params = await obter_parametros_pagina(page)
    token = params.get("accessToken", "")
    if not token or not track_id or track_id.startswith("dom_"):
        return False

    ok = await page.evaluate(
        """async (args) => {
            const [gatewayBase, tracksPath, trackId, apiKey, token] = args;
            try {
                const url = `${gatewayBase}${tracksPath}/${trackId}?time=${Date.now()}`;
                const r = await fetch(url, {
                    method: 'DELETE',
                    headers: {
                        'Accept': 'application/json',
                        'Content-Type': 'application/json',
                        'X-Api-Key': apiKey,
                        'Authorization': `Bearer ${token}`
                    }
                });
                return r.ok;
            } catch (e) {
                return false;
            }
        }""",
        [api_cfg["gateway_base"], api_cfg["tracks_path"], track_id, api_cfg["api_key"], token],
    )
    return bool(ok)


async def limpar_todas_tracks(page) -> int:
    """Limpa todas as tracks antigas da conta no Adobe Podcast."""
    tracks = await listar_tracks_api(page)
    removidas = 0
    for t in tracks:
        tid = t.get("id")
        if tid and await deletar_track_api(page, tid):
            removidas += 1
    try:
        await page.reload(wait_until="domcontentloaded")
        await asyncio.sleep(2.5)
    except Exception:
        pass
    return removidas


# ============================================================
# UPLOAD E DISPARO DO ENHANCE SPEECH
# ============================================================
async def iniciar_enhance(page, job: dict, params: dict):
    """
    Faz upload do arquivo de audio/video no Adobe Podcast Enhance Speech
    e captura o `track_id` criado na API `POST /api/v1/enhance_speech_tracks`.
    Retorna `(track_id, erro_msg)`.
    """
    sels_all = carregar_seletores()
    sels = sels_all["selectors"]
    tracks_path = sels_all["api_endpoints"]["tracks_path"]

    caminho_original = job.get("input_path") or ""
    caminho_entrada = job.get("upload_path") or caminho_original
    if not caminho_entrada or not os.path.exists(caminho_entrada):
        return None, f"Arquivo de entrada nao encontrado: {caminho_entrada}"

    # Garante que se for video (.mp4, .mov, .mkv, etc.), extraia sempre apenas o audio antes de subir
    extraido_local = False
    if eh_arquivo_video(caminho_entrada):
        caminho_entrada, extraido_local = extrair_audio_de_video_se_necessario(caminho_entrada)

    nome_arquivo = os.path.basename(caminho_entrada)

    try:
        if "podcast.adobe.com" not in page.url.lower() or "enhance" not in page.url.lower():
            await page.goto(sels["enhance_url"], wait_until="domcontentloaded")
            await asyncio.sleep(3.0)

        await fechar_modais_upsell(page)

        # Snapshot dos IDs de tracks antes do upload
        tracks_antes = await listar_tracks_api(page)
        ids_antes = {t.get("id") for t in tracks_antes if t.get("id")}

        captured_tracks = []
        captured_errors = []

        async def on_response(resp):
            try:
                if tracks_path in resp.url and resp.request.method == "POST" and "/exports" not in resp.url:
                    status = resp.status
                    if status in (200, 201):
                        data = await resp.json()
                        tid = data.get("id")
                        if tid:
                            captured_tracks.append(tid)
                    elif status == 429:
                        captured_errors.append("Limite de requisicoes atingido no Adobe Podcast (HTTP 429 - Rate Limit).")
                    elif status == 413:
                        captured_errors.append("Arquivo excede o limite de tamanho da sua conta Adobe Podcast (HTTP 413).")
                    elif status == 422:
                        captured_errors.append("[QUOTA_LIMIT_422] Limite diario de horas de aprimoramento atingido na conta Adobe Podcast (HTTP 422).")
                    elif status == 409:
                        captured_errors.append("Limite de arquivos simultaneos atingido no Adobe Podcast (HTTP 409).")
                    elif status == 415:
                        captured_errors.append("Formato ou extensao de arquivo nao suportado pelo Adobe Podcast (HTTP 415).")
                    else:
                        captured_errors.append(f"Erro HTTP {status} ao criar track no Adobe Podcast.")
            except Exception:
                pass

        page.on("response", on_response)
        try:
            # Localiza o <input type="file" id="enhance-file-upload"> (aguardando a SPA terminar de montar se necessario)
            file_input = page.locator(sels["file_input"]).first
            for _ in range(16):
                if await file_input.count() > 0:
                    break
                await fechar_modais_upsell(page)
                await asyncio.sleep(0.8)

            if await file_input.count() > 0:
                await file_input.set_input_files(caminho_entrada)
            else:
                choose_btn = page.locator(sels["choose_files_btn"]).first
                if await choose_btn.count() == 0:
                    return None, "Elemento de upload (#enhance-file-upload / choose-files-button) nao encontrado na pagina."
                async with page.expect_file_chooser(timeout=15000) as fc_info:
                    await choose_btn.click()
                fc = await fc_info.value
                await fc.set_files(caminho_entrada)

            # Aguarda o upload para o S3 (DirectUpload) concluir e o POST /enhance_speech_tracks disparar
            # Para arquivos grandes de video/audio, aguarda ate 180 segundos pelo upload
            for step in range(360):
                if captured_tracks:
                    return captured_tracks[0], None
                if captured_errors:
                    return None, captured_errors[0]

                if step > 4 and step % 6 == 0:
                    tracks_atuais = await listar_tracks_api(page)
                    for t in tracks_atuais:
                        tid = t.get("id")
                        tname = t.get("track_name") or t.get("trackName") or ""
                        if tid and tid not in ids_antes:
                            return tid, None
                        if tid and tname == nome_arquivo and tid not in ids_antes:
                            return tid, None

                await asyncio.sleep(0.5)

        finally:
            try:
                page.remove_listener("response", on_response)
            except Exception:
                pass
            if extraido_local and os.path.exists(caminho_entrada):
                try:
                    os.remove(caminho_entrada)
                except Exception:
                    pass

        # Fallback DOM caso a API tenha mudado de rota
        dom_processing = await page.locator(sels["processing_indicator"]).count()
        dom_download = await page.locator(sels["download_btn"]).count()
        if dom_processing > 0 or dom_download > 0:
            return f"dom_{uuid.uuid4().hex[:10]}", None

        return None, "Timeout aguardando criacao da track no Adobe Podcast apos upload do arquivo."
    except Exception as e:
        return None, f"Excecao ao iniciar enhance no Adobe Podcast: {e}"


# ============================================================
# POLLING DE STATUS DE PROCESSAMENTO
# ============================================================
async def checar_status(page, track_id: str, params: dict):
    """
    Verifica se o processamento de IA do Adobe Podcast terminou.
    Retorna `(pronto: bool, estado: str, track_data: dict, erro: str | None)`.
    """
    sels = carregar_seletores()["selectors"]

    # Se for fallback puramente DOM
    if track_id.startswith("dom_"):
        proc_cnt = await page.locator(sels["processing_indicator"]).count()
        dl_btn = page.locator(sels["download_btn"]).first
        if proc_cnt == 0 and await dl_btn.count() > 0:
            disabled = await dl_btn.get_attribute("disabled")
            if disabled is None:
                return True, "finished", {"id": track_id}, None
        return False, "processing", {"id": track_id}, None

    track = await consultar_track_api(page, track_id)
    if not track or track.get("_error"):
        # Tenta buscar na lista geral de tracks
        todas = await listar_tracks_api(page)
        for t in todas:
            if t.get("id") == track_id:
                track = t
                break

    if track:
        error_type = track.get("error_type") or track.get("errorType")
        if error_type:
            return False, "failed", track, f"Adobe Podcast retornou erro na faixa: {error_type}"

        finished_at = track.get("finished_processing_at") or track.get("finishedProcessingAt")
        enh_url = track.get("enhance_speech_audio_url") or track.get("enhanceSpeechAudioUrl")
        if finished_at and enh_url:
            return True, "finished", track, None
        if finished_at:
            return True, "finished", track, None

        started_at = track.get("started_processing_at") or track.get("startedProcessingAt")
        orig_url = track.get("original_audio_url") or track.get("originalAudioUrl")
        if not orig_url:
            return False, "uploading", track, None
        if not started_at:
            return False, "queued", track, None
        return False, "processing", track, None

    return False, "processing", {}, None


# ============================================================
# CALCULO DE GANHOS (FORMULA OFICIAL ADOBE PODCAST V2 / SL4W)
# ============================================================
def calcular_ganhos_export(
    strength: int = 90,
    background_strength: int = 10,
    music_strength: int = 10,
    model_version: str = "v2",
    soundlift_version: str = "",
) -> dict:
    """
    Replica exatamente a matematica de mixagem (`computeTwoSliderGains` e `computeSl4wGains`)
    do bundle oficial do Adobe Podcast para o endpoint `POST /api/v1/enhance_speech_tracks/{id}/exports`.
    """
    s_pct = max(0, min(100, int(strength)))
    bg_pct = max(0, min(100, int(background_strength)))
    mus_pct = max(0, min(100, int(music_strength)))

    def gain_for_db_percent(pct: int) -> float:
        if pct <= 0:
            return 0.0
        if pct >= 100:
            return 1.0
        removal = 1.0 - (pct / 100.0)
        min_dbfs = -36.0
        db = min_dbfs * (removal ** 2)
        return math.pow(10.0, db / 20.0)

    enhanced_gain = s_pct / 100.0
    isolated_gain = 1.0 - enhanced_gain
    bg_gain = gain_for_db_percent(bg_pct)
    mus_gain = gain_for_db_percent(mus_pct)

    if model_version == "v1":
        return {
            "strength": s_pct,
            "enhancement_enabled": True,
        }

    payload = {
        "enhanced_speech_gain": round(enhanced_gain, 6),
        "isolated_speech_gain": round(isolated_gain, 6),
        "background_gain": round(bg_gain, 6),
        "enhancement_enabled": True,
    }
    if soundlift_version == "sl4w":
        payload["music_gain"] = round(mus_gain, 6)
    return payload


def converter_formato_audio_se_necessario(caminho_wav_temp: str, caminho_final: str) -> bool:
    """
    O Adobe Podcast entrega arquivos `.wav`. Se o usuario pediu `.mp3`, `.m4a`, `.flac`, `.ogg` ou `.aac`
    em `output_path` e o `ffmpeg` estiver disponivel, converte automaticamente em alta qualidade.
    """
    _, ext = os.path.splitext(caminho_final)
    ext = ext.lower()
    if ext in ("", ".wav"):
        shutil.move(caminho_wav_temp, caminho_final)
        return os.path.exists(caminho_final)

    ffmpeg_exe = shutil.which("ffmpeg")
    if not ffmpeg_exe:
        shutil.move(caminho_wav_temp, caminho_final)
        return os.path.exists(caminho_final)

    codec_args = []
    if ext == ".mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
    elif ext in (".m4a", ".aac"):
        codec_args = ["-c:a", "aac", "-b:a", "256k"]
    elif ext == ".flac":
        codec_args = ["-c:a", "flac"]
    elif ext == ".ogg":
        codec_args = ["-c:a", "libvorbis", "-q:a", "8"]

    try:
        cmd = [ffmpeg_exe, "-y", "-i", caminho_wav_temp] + codec_args + [caminho_final]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=180)
        try:
            os.remove(caminho_wav_temp)
        except Exception:
            pass
        return os.path.exists(caminho_final) and os.path.getsize(caminho_final) > 1000
    except Exception:
        shutil.move(caminho_wav_temp, caminho_final)
        return os.path.exists(caminho_final)


# ============================================================
# DOWNLOAD DO AUDIO APRIMORADO (EXPORT API + PRESIGNED S3 + UI)
# ============================================================
async def baixar_audio_aprimorado(page, track_id: str, caminho_final: str, job: dict, track_data: dict = None) -> bool:
    """
    Baixa o audio aprimorado do Adobe Podcast usando 3 camadas de resiliencia:
      1) Export API (`/api/v1/enhance_speech_tracks/{id}/exports`) quando `strength` customizado e suportado;
      2) Presigned S3 URL direta via `enhance_speech_audio_url` (funciona em contas Free e Pro);
      3) Fallback via clique real no botao `[data-testid="download-button"]` com `page.expect_download()`.
    """
    sels_all = carregar_seletores()
    sels = sels_all["selectors"]
    api_cfg = sels_all["api_endpoints"]

    pasta_saida = os.path.dirname(caminho_final)
    if pasta_saida:
        os.makedirs(pasta_saida, exist_ok=True)

    caminho_temp = caminho_final + f".tmp_{uuid.uuid4().hex[:6]}.wav"
    strength = int(job.get("strength", 90))
    bg_strength = int(job.get("background_strength", 10))
    music_strength = int(job.get("music_strength", 10))
    track_component = str(job.get("track_component") or "full").strip().lower()

    if not track_data and not track_id.startswith("dom_"):
        track_data = await consultar_track_api(page, track_id)

    params = await obter_parametros_pagina(page)
    token = params.get("accessToken", "")
    download_url_final = None

    # ------------------------------------------------------------------
    # CAMADA 1: Export API com Strength customizado (Contas Premium / V2)
    # ------------------------------------------------------------------
    if token and track_data and not track_id.startswith("dom_") and (strength != 100 or track_component != "full"):
        model_ver = track_data.get("model_version") or track_data.get("modelVersion") or job.get("model_version", "v2")
        sl_ver = track_data.get("soundlift_model_version") or track_data.get("soundliftModelVersion") or ""
        export_body = calcular_ganhos_export(
            strength=strength,
            background_strength=bg_strength,
            music_strength=music_strength,
            model_version=model_ver,
            soundlift_version=sl_ver,
        )
        export_body["track_component"] = track_component

        export_url = await page.evaluate(
            """async (args) => {
                const [gatewayBase, tracksPath, trackId, apiKey, token, bodyObj] = args;
                try {
                    const postUrl = `${gatewayBase}${tracksPath}/${trackId}/exports?time=${Date.now()}`;
                    const r = await fetch(postUrl, {
                        method: 'POST',
                        headers: {
                            'Accept': 'application/json',
                            'Content-Type': 'application/json',
                            'X-Api-Key': apiKey,
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify(bodyObj)
                    });
                    if (!r.ok) return null;
                    const exp = await r.json();
                    if (exp.url) return exp.url;
                    const expId = exp.id;
                    if (!expId) return null;

                    for (let i = 0; i < 60; i++) {
                        await new Promise(res => setTimeout(res, 2000));
                        const pollUrl = `${gatewayBase}${tracksPath}/${trackId}/exports/${expId}?time=${Date.now()}`;
                        const pr = await fetch(pollUrl, {
                            method: 'GET',
                            headers: {
                                'Accept': 'application/json',
                                'X-Api-Key': apiKey,
                                'Authorization': `Bearer ${token}`
                            }
                        });
                        if (!pr.ok) break;
                        const pdata = await pr.json();
                        if (pdata.url) return pdata.url;
                        if (pdata.error_type || pdata.errorType) break;
                    }
                    return null;
                } catch (e) {
                    return null;
                }
            }""",
            [api_cfg["gateway_base"], api_cfg["tracks_path"], track_id, api_cfg["api_key"], token, export_body],
        )
        if export_url:
            download_url_final = export_url

    # ------------------------------------------------------------------
    # CAMADA 2: Presigned S3 URL via `enhance_speech_audio_url`
    # ------------------------------------------------------------------
    if not download_url_final and token and track_data and not track_id.startswith("dom_"):
        enh_path = track_data.get("enhance_speech_audio_url") or track_data.get("enhanceSpeechAudioUrl")
        if enh_path:
            presigned = await page.evaluate(
                """async (args) => {
                    const [gatewayBase, enhPath, apiKey, token] = args;
                    try {
                        if (enhPath.startsWith('https://') && !enhPath.includes('phonos-server')) {
                            return enhPath;
                        }
                        const fullUrl = enhPath.startsWith('http') ? enhPath : `${gatewayBase}${enhPath}`;
                        const sep = fullUrl.includes('?') ? '&' : '?';
                        const r = await fetch(`${fullUrl}${sep}time=${Date.now()}`, {
                            method: 'GET',
                            headers: {
                                'Accept': 'application/json',
                                'X-Api-Key': apiKey,
                                'Authorization': `Bearer ${token}`
                            }
                        });
                        if (!r.ok) return null;
                        const data = await r.json();
                        return data.url || null;
                    } catch (e) {
                        return null;
                    }
                }""",
                [api_cfg["gateway_base"], enh_path, api_cfg["api_key"], token],
            )
            if presigned:
                download_url_final = presigned

    if download_url_final:
        try:
            resp = await page.context.request.get(download_url_final, timeout=180000)
            if resp.status == 200:
                body = await resp.body()
                if len(body) > 1000:
                    with open(caminho_temp, "wb") as f:
                        f.write(body)
                    sucesso = converter_formato_audio_se_necessario(caminho_temp, caminho_final)
                    if sucesso:
                        if job.get("delete_after_download", True):
                            await deletar_track_api(page, track_id)
                        return True
        except Exception as e:
            print(f"[!] Aviso no download direto via URL assinada: {e}")

    # ------------------------------------------------------------------
    # CAMADA 3: Clique real no botao Download da UI com expect_download
    # ------------------------------------------------------------------
    try:
        await fechar_modais_upsell(page)
        dl_btn = page.locator(sels["download_btn"]).first
        if await dl_btn.count() > 0:
            async with page.expect_download(timeout=120000) as dl_info:
                await dl_btn.click()
            dl = await dl_info.value
            await dl.save_as(caminho_temp)
            if os.path.exists(caminho_temp) and os.path.getsize(caminho_temp) > 1000:
                sucesso = converter_formato_audio_se_necessario(caminho_temp, caminho_final)
                if sucesso:
                    if job.get("delete_after_download", True) and not track_id.startswith("dom_"):
                        await deletar_track_api(page, track_id)
                    return True
    except Exception as e:
        print(f"[!] Erro no fallback de download via botao UI: {e}")

    if os.path.exists(caminho_temp):
        try:
            os.remove(caminho_temp)
        except Exception:
            pass
    return False
