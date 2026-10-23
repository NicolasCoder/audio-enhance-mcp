import asyncio
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional
import aiohttp
from mcp.server.fastmcp import FastMCP

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DAEMON_SCRIPT = os.path.join(BASE_DIR, "podcast_queue_daemon.py")
DAEMON_PORT = int(os.environ.get("ADOBE_DAEMON_PORT", "9344"))
DAEMON_URL = f"http://127.0.0.1:{DAEMON_PORT}"

mcp = FastMCP("adobe-podcast")


async def ensure_daemon_running() -> Dict[str, Any]:
    """
    Verifica se o daemon central de fila unica do Adobe Podcast (porta 9344) esta ativo.
    Se nao estiver, inicia automaticamente em background fora de qualquer Job Object
    para que multiplos projetos compartilhem a mesma fila global sem conflito com o Flow Studio (porta 9334).
    """
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            async with session.get(f"{DAEMON_URL}/health") as resp:
                if resp.status == 200:
                    return await resp.json()
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
            [sys.executable, DAEMON_SCRIPT],
            cwd=BASE_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(25):
            await asyncio.sleep(0.5)
            try:
                async with session.get(f"{DAEMON_URL}/health") as resp:
                    if resp.status == 200:
                        return await resp.json()
            except Exception:
                pass

    raise RuntimeError(
        f"Nao foi possivel iniciar ou conectar ao Adobe Podcast Central Queue Daemon na porta {DAEMON_PORT}."
    )


async def daemon_request(
    method: str, path: str, payload: Optional[dict] = None, timeout_sec: float = 980.0
) -> dict:
    await ensure_daemon_running()
    timeout = aiohttp.ClientTimeout(total=timeout_sec)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        url = f"{DAEMON_URL}{path}"
        if method.upper() == "GET":
            async with session.get(url) as resp:
                return await resp.json()
        else:
            async with session.post(url, json=payload or {}) as resp:
                return await resp.json()


@mcp.tool()
async def podcast_enhance(
    input_path: str,
    output_path: str = "",
    strength: int = 90,
    background_strength: int = 10,
    music_strength: int = 10,
    model_version: str = "v2",
    track_component: str = "full",
    delete_after_download: bool = True,
    priority: int = 100,
    workspace: str = "",
    wait: bool = True,
) -> Dict[str, Any]:
    """
    Envia um arquivo de AUDIO ou VIDEO (.wav, .mp3, .m4a, .flac, .mp4, .mov) para a fila central
    unica do Adobe Podcast Enhance Speech (porta 9344 / Chrome isolado porta 9343) e baixa o
    audio limpo com qualidade de estudio.
    Se ocorrer qualquer erro de seletor ou API, retorna automaticamente um pacote `diagnostics` para Auto-Healing.

    Args:
        input_path: Caminho do arquivo de audio ou video original a ser aprimorado.
        output_path: Caminho completo do arquivo de saida (.wav, .mp3, .m4a, .flac) ou pasta de destino.
        strength: Intensidade do aprimoramento de fala (0 a 100, padrao 90).
        background_strength: Nivel de preservacao do som ambiente natural (0 a 100, padrao 10, aplicavel no modelo v2).
        music_strength: Nivel de preservacao de musica de fundo (0 a 100, padrao 10).
        model_version: Versao do modelo de IA do Adobe Podcast ("v2" SoundLift padrao ou "v1" classico).
        track_component: "full" (mix completo padrao), "speech" (apenas voz isolada), "background" (apenas ambiente).
        delete_after_download: Se True (padrao), remove a faixa da conta Adobe Podcast apos baixar para nao lotar a lista.
        priority: Prioridade na fila central (10 = Alta / Fura fila, 100 = Normal, 200 = Baixa).
        workspace: Diretorio do projeto atual (opcional, ajuda a resolver caminhos relativos e prioridades por projeto).
        wait: Se True (padrao), aguarda ate o arquivo aprimorado ser baixado e retorna `file_path` e `track_id`.
    """
    payload = {
        "input_path": input_path,
        "output_path": output_path,
        "strength": strength,
        "background_strength": background_strength,
        "music_strength": music_strength,
        "model_version": model_version,
        "track_component": track_component,
        "delete_after_download": delete_after_download,
        "priority": priority,
        "workspace": workspace or os.getcwd(),
        "wait": wait,
    }
    res = await daemon_request("POST", "/enqueue", payload, timeout_sec=1000.0 if wait else 30.0)
    job = res.get("job", {})
    return {
        "ok": res.get("ok", False),
        "job_id": job.get("job_id"),
        "track_id": job.get("track_id"),
        "file_path": job.get("file_path"),
        "audio_duration_human": job.get("audio_duration_human"),
        "account_id": job.get("account_id"),
        "account_email": job.get("account_email"),
        "rotated_from_account": job.get("rotated_from_account"),
        "quota": job.get("quota"),
        "status": job.get("status"),
        "error": job.get("error"),
        "diagnostics": job.get("diagnostics"),
        "job": job,
    }


@mcp.tool()
async def podcast_enhance_batch(
    items: List[Dict[str, Any]],
    workspace: str = "",
    wait: bool = True,
) -> Dict[str, Any]:
    """
    Enfileira multiplos arquivos de audio/video de uma so vez na fila central unica do Adobe Podcast.
    Cada item do array pode conter `input_path`, `output_path`, `strength`, `model_version` e `priority`.
    """
    ws = workspace or os.getcwd()
    norm_items = []
    for it in items:
        copy_it = dict(it)
        copy_it.setdefault("workspace", ws)
        norm_items.append(copy_it)

    return await daemon_request(
        "POST",
        "/enqueue",
        {"items": norm_items, "wait": wait, "timeout": 3600},
        timeout_sec=3650.0 if wait else 30.0,
    )


@mcp.tool()
async def podcast_queue_status(
    job_id: str = "",
    wait: bool = False,
) -> Dict[str, Any]:
    """
    Consulta o status da fila central unica do Adobe Podcast (porta 9344, compartilhada entre todos os projetos)
    ou consulta/aguarda um `job_id` especifico.
    """
    if job_id.strip():
        q = "?wait=1" if wait else ""
        return await daemon_request(
            "GET", f"/jobs/{job_id.strip()}{q}", timeout_sec=960.0 if wait else 15.0
        )
    return await daemon_request("GET", "/queue", timeout_sec=15.0)


@mcp.tool()
async def podcast_login_browser() -> Dict[str, Any]:
    """
    Abre ou traz para a tela o Google Chrome exclusivo do Adobe Podcast (porta 9343, separado do Flow 9333)
    em modo VISIVEL para o usuario fazer o primeiro login na conta Adobe.
    Apos logar e ocultar, todas as proximas execucoes rodarao 100% invisiveis fora da tela.
    """
    return await daemon_request("POST", "/browser/login", timeout_sec=45.0)


@mcp.tool()
async def podcast_show_browser() -> Dict[str, Any]:
    """
    Traz imediatamente a janela do Google Chrome do Adobe Podcast (porta 9343) para a tela (X=120, Y=90)
    para que o usuario possa visualizar o processamento em tempo real (/showbrowser).
    """
    return await daemon_request("POST", "/browser/show", timeout_sec=30.0)


@mcp.tool()
async def podcast_hide_browser() -> Dict[str, Any]:
    """
    Esconde imediatamente a janela do Google Chrome do Adobe Podcast (porta 9343) para fora da tela (X=-2400, Y=0)
    e remove o icone da barra de tarefas, mantendo a automacao rodando 100% invisivel (/hidebrowser).
    """
    return await daemon_request("POST", "/browser/hide", timeout_sec=30.0)


@mcp.tool()
async def podcast_quota_status(
    input_path: str = "",
) -> Dict[str, Any]:
    """
    Consulta na plataforma do Adobe Podcast (`phonos-server-flex.adobe.io/api/v1/user_metrics` e `/api/v1/user`)
    quanto tempo diario de aprimoramento ja foi usado, quanto falta (Gratuito = 1h/dia | Pago/Premium = 4h/dia),
    em quanto tempo a quota diaria reseta e o saldo consolidado do pool multi-contas.
    Se `input_path` for informado, mede tambem a duracao exata do arquivo e verifica se ele cabe no saldo diario.
    """
    return await daemon_request(
        "POST",
        "/quota",
        {"live": True, "input_path": input_path},
        timeout_sec=35.0,
    )


@mcp.tool()
async def podcast_accounts(
    action: str = "list",
    account_id: str = "",
    label: str = "",
    auto_rotate: bool = True,
    open_login: bool = True,
) -> Dict[str, Any]:
    """
    Gerencia o sistema Multi-Contas (Multi-Profile Pool) do Adobe Podcast com rotacao automatica de quota diaria:
      - action="list": Lista todas as contas/perfis cadastrados e quanto tempo resta em cada uma hoje.
      - action="add": Cria um novo perfil isolado do Chrome (ex: `account_id="conta_2"`) e abre na tela para login na nova conta Adobe (use `open_login=False` para apenas cadastrar o perfil sem abrir janela).
      - action="switch": Troca imediatamente para outra conta (`account_id`) na porta 9343 e atualiza seu saldo na plataforma.
      - action="login": Abre o navegador visivel na conta informada (`account_id`) para fazer login.
      - action="remove": Remove uma conta secundaria do pool.
      - action="set_auto_rotate": Ativa ou desativa a troca automatica de conta quando o limite de 1h (Free) ou 4h (Pago) acabar.
    """
    return await daemon_request(
        "POST",
        "/accounts",
        {
            "action": action,
            "account_id": account_id,
            "label": label,
            "auto_rotate": auto_rotate,
            "open_login": open_login,
        },
        timeout_sec=60.0,
    )


@mcp.tool()
async def podcast_clear_tracks() -> Dict[str, Any]:
    """
    Remove todas as faixas antigas da lista do Adobe Podcast Enhance Speech na conta logada.
    """
    return await daemon_request("POST", "/browser/clear_tracks", timeout_sec=45.0)


@mcp.tool()
async def podcast_diagnose(
    take_screenshot: bool = True,
) -> Dict[str, Any]:
    """
    [AUTO-HEALING TOOL] Inspeciona a pagina ao vivo do Adobe Podcast (incluindo todos os Shadow DOMs
    de Web Components `<sp-*>`, `data-testid`s, inputs de upload e chamadas de rede para `phonos-server-flex.adobe.io`).
    Retorna o snapshot completo, os seletores atuais de `selectors.json` e o caminho de um screenshot atualizado.
    """
    return await daemon_request(
        "POST",
        "/diagnose",
        {"take_screenshot": take_screenshot},
        timeout_sec=45.0,
    )


@mcp.tool()
async def podcast_update_selectors(
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """
    [AUTO-HEALING TOOL] Atualiza `selectors.json` em tempo real (sem precisar reiniciar o daemon).
    Permite corrigir seletores CSS/Shadow-DOM (`selectors`), endpoints da API (`api_endpoints`)
    ou versoes de modelo (`model_versions`) assim que o agente detectar mudancas via `podcast_diagnose`.
    """
    return await daemon_request("POST", "/selectors", updates, timeout_sec=20.0)


@mcp.tool()
async def podcast_reload_daemon() -> Dict[str, Any]:
    """
    [AUTO-HEALING TOOL] Recarrega o modulo `podcastapi.py` e `selectors.json` em memoria dentro do
    daemon central em execucao (porta 9344). Use apos editar `podcastapi.py` ou `selectors.json`
    para aplicar correcoes imediatamente sem perder a fila atual.
    """
    return await daemon_request("POST", "/reload", {}, timeout_sec=20.0)


if __name__ == "__main__":
    mcp.run()
