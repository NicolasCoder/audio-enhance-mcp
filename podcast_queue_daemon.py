import asyncio
import importlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import deque
from datetime import datetime
from aiohttp import web
from playwright.async_api import async_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import podcastapi

DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = int(os.environ.get("ADOBE_DAEMON_PORT", "9344"))
PRIMARY_PROFILE_DIR = podcastapi._obter_diretorio_perfil_base("conta_1")
DB_PATH = os.path.join(PRIMARY_PROFILE_DIR, "podcast_queue.db")


def init_db():
    os.makedirs(PRIMARY_PROFILE_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            workspace TEXT,
            input_path TEXT,
            output_path TEXT,
            strength INTEGER,
            background_strength INTEGER,
            music_strength INTEGER,
            model_version TEXT,
            track_component TEXT,
            delete_after_download INTEGER,
            status TEXT,
            track_id TEXT,
            file_path TEXT,
            error TEXT,
            created_at TEXT,
            updated_at TEXT,
            priority INTEGER DEFAULT 100
        )
    """)
    conn.commit()
    conn.close()


def db_save_job(job: dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO jobs (
            job_id, workspace, input_path, output_path, strength,
            background_strength, music_strength, model_version, track_component,
            delete_after_download, status, track_id, file_path, error,
            created_at, updated_at, priority
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job["job_id"],
        job.get("workspace", ""),
        job.get("input_path", ""),
        job.get("output_path", ""),
        int(job.get("strength", 90)),
        int(job.get("background_strength", 10)),
        int(job.get("music_strength", 10)),
        job.get("model_version", "v2"),
        job.get("track_component", "full"),
        1 if job.get("delete_after_download", True) else 0,
        job.get("status", "queued"),
        job.get("track_id") or "",
        job.get("file_path") or "",
        job.get("error") or "",
        job.get("created_at", ""),
        datetime.now().isoformat(timespec="seconds"),
        int(job.get("priority", 100)),
    ))
    conn.commit()
    conn.close()


def db_list_recent_jobs(limit=50):
    if not os.path.exists(DB_PATH):
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    for r in rows:
        r["delete_after_download"] = bool(r.get("delete_after_download", 1))
    return rows


class AdobePodcastCentralDaemon:
    """
    Daemon centralizado de fila unica com Telemetria de Auto-Healing para o Adobe Podcast.
    Gerencia uma unica instancia isolada do Google Chrome na porta 9343 (separada do Flow 9333)
    e processa requisicoes de multiplos projetos/workspaces em serie sem conflito.
    """

    def __init__(self):
        self.queue = asyncio.PriorityQueue()
        self._counter = 0
        self.jobs = {}
        self.job_events = {}
        self.browser_lock = asyncio.Lock()
        self.playwright = None
        self.browser = None
        self.page = None
        self.invisivel = True
        self.active_job_id = None
        self.config_padrao = podcastapi.carregar_config()
        self.recent_network = deque(maxlen=50)
        self._attached_pages = set()

        for r in reversed(db_list_recent_jobs(100)):
            if r["status"] in ("queued", "running", "uploading", "processing", "downloading"):
                r["status"] = "interrupted"
                db_save_job(r)
            self.jobs[r["job_id"]] = r

    async def start(self):
        self.playwright = await async_playwright().start()
        asyncio.create_task(self.worker_loop())

    async def stop(self):
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass

    def _attach_network_sniffer(self, page):
        """Monitora chamadas para phonos-server-flex.adobe.io e /api/v1/ para Auto-Healing."""
        if id(page) in self._attached_pages:
            return
        self._attached_pages.add(id(page))

        async def on_response(resp):
            try:
                u = resp.url
                api_cfg = podcastapi.carregar_seletores().get("api_endpoints", {})
                gw = api_cfg.get("gateway_base", "phonos-server")
                up_kw = api_cfg.get("direct_upload_keyword", "direct_uploads")
                if "phonos-server" in u or gw in u or "/api/v1/" in u or up_kw in u:
                    txt = ""
                    try:
                        if "application/json" in (resp.headers.get("content-type") or ""):
                            txt = (await resp.text())[:500]
                    except Exception:
                        pass
                    self.recent_network.append({
                        "time": datetime.now().isoformat(timespec="seconds"),
                        "method": resp.request.method,
                        "status": resp.status,
                        "url": u[:200],
                        "preview": txt,
                    })
            except Exception:
                pass

        page.on("response", lambda r: asyncio.create_task(on_response(r)))

    async def switch_account_unlocked(self, account_id: str, force_visible=None):
        """
        Alterna o perfil do Chrome na porta 9343 para `account_id` sem tocar no Flow Studio (9333).
        Deve ser chamado dentro de `async with self.browser_lock:`.
        """
        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
        self.browser = None
        self.page = None
        self._attached_pages.clear()

        podcastapi.fechar_chrome_podcast(porta=podcastapi.CHROME_PORT)
        await asyncio.sleep(0.8)
        nova_conta = podcastapi.definir_conta_ativa(account_id)
        prof_alvo = nova_conta.get("profile_dir") or podcastapi.obter_diretorio_perfil()
        if force_visible is None:
            if podcastapi.ja_fez_login_inicial(prof_alvo):
                self.invisivel = bool(self.config_padrao.get("modo_invisivel", True))
            else:
                self.invisivel = False
        return await self.ensure_browser_and_page(force_visible=force_visible)

    async def ensure_browser_and_page(self, force_visible=None):
        profile_atual = podcastapi.obter_diretorio_perfil()
        primeiro_uso = not podcastapi.ja_fez_login_inicial(profile_atual)
        if force_visible is not None:
            self.invisivel = not force_visible
        elif primeiro_uso:
            self.invisivel = False

        # Garante que se o Chrome da porta 9343 estiver aberto em OUTRO perfil, ele seja trocado pelo perfil ativo
        perfil_rodando = podcastapi.obter_perfil_chrome_rodando(porta=podcastapi.CHROME_PORT)
        if perfil_rodando and os.path.normcase(os.path.abspath(perfil_rodando)) != os.path.normcase(os.path.abspath(profile_atual)):
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass
            self.browser = None
            self.page = None
            self._attached_pages.clear()
            podcastapi.fechar_chrome_podcast(porta=podcastapi.CHROME_PORT)
            await asyncio.sleep(0.8)

        precisa_reconectar = False
        if not self.browser or not self.browser.is_connected():
            precisa_reconectar = True
        elif self.page and self.page.is_closed():
            precisa_reconectar = True

        abriu_chrome_agora = False
        if precisa_reconectar:
            self.browser = None
            self.page = None
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{podcastapi.CHROME_PORT}"
                )
            except Exception:
                abriu_chrome_agora = True
                podcastapi.iniciar_chrome_podcast(
                    invisivel=self.invisivel,
                    porta=podcastapi.CHROME_PORT,
                    profile_dir=profile_atual,
                )
                for _ in range(22):
                    await asyncio.sleep(1)
                    try:
                        self.browser = await self.playwright.chromium.connect_over_cdp(
                            f"http://127.0.0.1:{podcastapi.CHROME_PORT}"
                        )
                        break
                    except Exception:
                        pass
                if not self.browser:
                    raise RuntimeError(
                        f"Nao foi possivel iniciar ou conectar ao Chrome do Adobe Podcast na porta {podcastapi.CHROME_PORT}"
                    )

        ctx = self.browser.contexts[0]
        if not self.page or self.page.is_closed():
            for pg in ctx.pages:
                if "podcast.adobe" in pg.url.lower():
                    self.page = pg
                    break
            if not self.page:
                for pg in ctx.pages:
                    if "adobe" in pg.url.lower():
                        self.page = pg
                        break
            if not self.page:
                self.page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        self._attach_network_sniffer(self.page)
        await podcastapi.posicionar_janela_chrome(self.page, invisivel=self.invisivel)

        enhance_url = podcastapi.carregar_seletores()["selectors"].get(
            "enhance_url", "https://podcast.adobe.com/enhance"
        )
        url_atual = self.page.url.lower()
        if "podcast.adobe.com" not in url_atual and "auth.services.adobe.com" not in url_atual and "account.adobe.com" not in url_atual:
            await self.page.goto(enhance_url, wait_until="domcontentloaded")
            await asyncio.sleep(3.0)

        await podcastapi.fechar_modais_upsell(self.page)
        params = await podcastapi.obter_parametros_pagina(self.page)

        # Aguarda a SPA do Adobe Podcast terminar o spinner inicial ("Carregar") e inicializar o Adobe IMS + DOM
        for _ in range(12):
            ui_pronta = bool(params.get("hasFileInput") or params.get("hasLoggedOutLanding"))
            ims_pronto = bool(primeiro_uso or (params.get("loggedIn") and params.get("accessToken")))
            if ui_pronta and ims_pronto:
                break
            await asyncio.sleep(1.0)
            params = await podcastapi.obter_parametros_pagina(self.page)

        logado = bool(params.get("loggedIn"))

        if logado and not podcastapi.ja_fez_login_inicial(profile_atual):
            podcastapi.marcar_login_concluido(profile_atual)

        return self.page, params, logado

    def resolver_caminho_saida(self, job: dict) -> str:
        in_path = job.get("input_path") or "audio.wav"
        base_in = os.path.splitext(os.path.basename(in_path))[0]
        nome_seguro = re.sub(r"[^A-Za-z0-9_-]", "_", base_in)[:60] or "audio"
        out_raw = (job.get("output_path") or "").strip().strip('"').strip("'")

        if not out_raw:
            pasta_origem = os.path.dirname(os.path.abspath(in_path)) if in_path and os.path.exists(in_path) else ""
            if pasta_origem and os.path.isdir(pasta_origem):
                return os.path.join(pasta_origem, f"{base_in}_enhanced.wav")
            pasta_padrao = os.path.join(BASE_DIR, "audios_aprimorados")
            os.makedirs(pasta_padrao, exist_ok=True)
            return os.path.join(pasta_padrao, f"{nome_seguro}_enhanced.wav")

        out_abs = os.path.abspath(out_raw)
        sem_ext, ext = os.path.splitext(out_abs)
        if ext.lower() in (".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"):
            os.makedirs(os.path.dirname(out_abs), exist_ok=True)
            return out_abs
        elif podcastapi.eh_arquivo_video(out_abs):
            # Sempre entrega apenas o audio aprimorado (.wav) para o usuario substituir no editor
            os.makedirs(os.path.dirname(out_abs), exist_ok=True)
            return f"{sem_ext}.wav"
        else:
            os.makedirs(out_abs, exist_ok=True)
            return os.path.join(out_abs, f"{base_in}_enhanced.wav")

    async def process_single_job(self, job: dict):
        job_id = job["job_id"]
        self.active_job_id = job_id
        job["status"] = "running"
        db_save_job(job)
        caminho_upload_temp = None

        try:
            async with self.browser_lock:
                # 1. Mede a duracao exata e, se for VIDEO, extrai SEMPRE apenas o audio antes de enviar!
                caminho_entrada = job.get("input_path") or ""
                if not caminho_entrada or not os.path.exists(caminho_entrada):
                    raise RuntimeError(f"Arquivo de entrada nao encontrado: {caminho_entrada}")

                audio_sec = podcastapi.obter_duracao_midia_segundos(caminho_entrada)
                orig_bytes = os.path.getsize(caminho_entrada)
                job["audio_duration_sec"] = audio_sec
                job["audio_duration_human"] = podcastapi.formatar_duracao(audio_sec)
                job["original_size_mb"] = round(orig_bytes / (1024 * 1024), 2)

                caminho_upload, foi_extraido = podcastapi.extrair_audio_de_video_se_necessario(caminho_entrada)
                if foi_extraido:
                    caminho_upload_temp = caminho_upload
                upload_bytes = os.path.getsize(caminho_upload) if os.path.exists(caminho_upload) else orig_bytes
                job["upload_path"] = caminho_upload
                job["extracted_from_video"] = foi_extraido
                job["upload_size_mb"] = round(upload_bytes / (1024 * 1024), 2)
                file_bytes = upload_bytes

                page, params, logado = await self.ensure_browser_and_page()
                if not logado:
                    await podcastapi.posicionar_janela_chrome(page, invisivel=False)
                    self.invisivel = False
                    raise RuntimeError(
                        "Usuario nao esta logado no Adobe Podcast. O navegador exclusivo (porta 9343) foi aberto na tela para login."
                    )

                # 2. Consulta a quota diaria real na plataforma Adobe Podcast (Free = 1h/dia | Pago = 4h/dia)
                quota_info = await podcastapi.consultar_quota_plataforma(page)
                conta_atual = quota_info.get("account_id") or podcastapi.obter_conta_ativa()["id"]
                contas_tentadas = [conta_atual]

                def conta_suporta_audio(q: dict) -> tuple:
                    if not q.get("logged_in"):
                        return False, "Conta nao autenticada"
                    f_lim_sec = float(q.get("file_duration_limit_sec") or 1800.0)
                    f_lim_mb = float(q.get("file_size_limit_mb") or 500.0)
                    rem_sec = float(q.get("quota_remaining_sec") or 0.0)
                    if audio_sec > 0 and audio_sec > f_lim_sec + 2.0:
                        return (
                            False,
                            f"O arquivo possui {podcastapi.formatar_duracao(audio_sec)}, excedendo o limite por arquivo de {q.get('file_duration_limit_min')} min da conta {q.get('tier_label')}.",
                        )
                    if file_bytes > (f_lim_mb * 1024 * 1024):
                        return (
                            False,
                            f"O arquivo possui {round(file_bytes / (1024*1024), 1)} MB, excedendo o limite de {f_lim_mb} MB da conta {q.get('tier_label')}.",
                        )
                    if audio_sec > 0 and rem_sec + 2.0 < audio_sec:
                        return (
                            False,
                            f"Saldo diario insuficiente na conta '{q.get('account_id')}' ({q.get('email') or q.get('label')}): "
                            f"restam {q.get('quota_remaining_human')} hoje (usado {q.get('quota_used_human')} de {q.get('quota_limit_human')} • reseta em {q.get('quota_reset_in_human')}), "
                            f"mas o audio tem {podcastapi.formatar_duracao(audio_sec)}.",
                        )
                    return True, ""

                suporta, motivo_bloqueio = conta_suporta_audio(quota_info)

                # 3. Rotacao Automatica Multi-Contas se a conta ativa nao tiver tempo suficiente hoje
                dados_pool = podcastapi.carregar_contas()
                if not suporta and dados_pool.get("auto_rotate", True) and len(dados_pool.get("accounts", {})) > 1:
                    while True:
                        prox_id = podcastapi.escolher_proxima_conta_com_quota(
                            audio_sec, conta_excluir=",".join(contas_tentadas)
                        )
                        if not prox_id:
                            break
                        contas_tentadas.append(prox_id)
                        print(f"[*] Rotacionando automaticamente da conta '{conta_atual}' para '{prox_id}'...")
                        page, params, logado_prox = await self.switch_account_unlocked(prox_id)
                        if not logado_prox:
                            continue
                        quota_prox = await podcastapi.consultar_quota_plataforma(page, account_id=prox_id)
                        ok_prox, motivo_prox = conta_suporta_audio(quota_prox)
                        if ok_prox:
                            job["rotated_from_account"] = conta_atual
                            conta_atual = prox_id
                            quota_info = quota_prox
                            suporta = True
                            motivo_bloqueio = ""
                            break

                if not suporta:
                    dica_multi = (
                        " Dica: Adicione outra conta Adobe ao pool com `podcast_accounts(action='add')` para rotacao automatica quando uma conta acabar!"
                        if len(dados_pool.get("accounts", {})) <= 1
                        else f" Todas as {len(contas_tentadas)} conta(s) verificadas no pool estao sem saldo suficiente para este arquivo."
                    )
                    raise RuntimeError(motivo_bloqueio + dica_multi)

                job["account_id"] = conta_atual
                job["account_email"] = quota_info.get("email") or ""
                job["status"] = "uploading"
                db_save_job(job)

                track_id, erro_up = await podcastapi.iniciar_enhance(page, job, params)

                # Fallback: se a API retornar HTTP 422 (limite diario atingido durante o POST), tenta rotacionar pelas proximas contas do pool
                while not track_id and erro_up and "[QUOTA_LIMIT_422]" in erro_up:
                    try:
                        await podcastapi.consultar_quota_plataforma(page, account_id=conta_atual)
                    except Exception:
                        pass
                    dados_pool = podcastapi.carregar_contas()
                    if conta_atual in dados_pool["accounts"]:
                        dados_pool["accounts"][conta_atual]["quota_remaining_sec"] = 0.0
                        if float(dados_pool["accounts"][conta_atual].get("quota_reset_at_epoch") or 0.0) <= 0.0:
                            import time as _t
                            dados_pool["accounts"][conta_atual]["quota_reset_at_epoch"] = round(_t.time() + 43200.0, 2)
                        podcastapi.salvar_contas(dados_pool)
                    if not (dados_pool.get("auto_rotate", True) and len(dados_pool.get("accounts", {})) > 1):
                        break
                    prox_id = podcastapi.escolher_proxima_conta_com_quota(
                        audio_sec, conta_excluir=",".join(contas_tentadas)
                    )
                    if not prox_id:
                        break
                    contas_tentadas.append(prox_id)
                    page, params, logado_prox = await self.switch_account_unlocked(prox_id, force_visible=False)
                    if not logado_prox:
                        continue
                    job["rotated_from_account"] = job.get("rotated_from_account") or conta_atual
                    conta_atual = prox_id
                    job["account_id"] = prox_id
                    track_id, erro_up = await podcastapi.iniciar_enhance(page, job, params)

                if not track_id:
                    raise RuntimeError(
                        erro_up
                        or "Falha ao enviar arquivo para o Adobe Podcast Enhance Speech (track_id vazio)."
                    )

                job["track_id"] = track_id
                job["status"] = "processing"
                db_save_job(job)

                timeout_seg = int(self.config_padrao.get("timeout_processamento_seg", 900))
                max_tentativas = max(60, timeout_seg // 3)
                pronto = False
                track_data = {}

                for tentativa in range(max_tentativas):
                    await asyncio.sleep(3.0)
                    pronto, estado, track_data, erro_proc = await podcastapi.checar_status(
                        page, track_id, params
                    )
                    if erro_proc:
                        raise RuntimeError(erro_proc)
                    if pronto:
                        break

                if not pronto:
                    raise RuntimeError(
                        f"Timeout aguardando aprimoramento de audio no Adobe Podcast (track_id={track_id})."
                    )

                caminho_final = self.resolver_caminho_saida(job)
                job["status"] = "downloading"
                db_save_job(job)

                sucesso = await podcastapi.baixar_audio_aprimorado(
                    page, track_id, caminho_final, job, track_data=track_data
                )
                if not sucesso or not os.path.exists(caminho_final):
                    raise RuntimeError(
                        f"Audio aprimorado no Adobe Podcast (ID: {track_id}), mas houve falha ao baixar para {caminho_final}."
                    )

                # 4. Atualiza a quota diaria da plataforma logo apos concluir o job
                try:
                    quota_pos = await podcastapi.consultar_quota_plataforma(page)
                    job["quota"] = {
                        "account_id": quota_pos.get("account_id"),
                        "email": quota_pos.get("email"),
                        "tier": quota_pos.get("tier"),
                        "used_human": quota_pos.get("quota_used_human"),
                        "remaining_human": quota_pos.get("quota_remaining_human"),
                        "limit_human": quota_pos.get("quota_limit_human"),
                        "remaining_min": quota_pos.get("quota_remaining_min"),
                        "reset_in_human": quota_pos.get("quota_reset_in_human"),
                    }
                except Exception:
                    pass

                job["file_path"] = caminho_final
                job["status"] = "completed"
                job["error"] = ""
                db_save_job(job)

        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            try:
                if self.page and not self.page.is_closed():
                    diag = await podcastapi.coletar_diagnostico_pagina(
                        self.page,
                        motivo=f"Job {job_id} failed: {e}",
                        recent_network=list(self.recent_network),
                        take_screenshot=True,
                    )
                    job["diagnostics"] = {
                        "report_path": diag.get("report_path"),
                        "screenshot_path": diag.get("screenshot_path"),
                        "page_url": diag.get("page_url"),
                        "alerts_or_modals": diag.get("dom_snapshot", {}).get("alerts_or_modals", []),
                        "recent_network": list(self.recent_network)[-6:],
                        "auto_healing_hint": diag.get("auto_healing_instructions"),
                    }
            except Exception:
                pass
            db_save_job(job)
        finally:
            if caminho_upload_temp and os.path.exists(caminho_upload_temp):
                try:
                    os.remove(caminho_upload_temp)
                except Exception:
                    pass
            self.active_job_id = None
            ev = self.job_events.get(job_id)
            if ev:
                ev.set()

    async def worker_loop(self):
        while True:
            _, _, job_id = await self.queue.get()
            job = self.jobs.get(job_id)
            if job and job.get("status") == "queued":
                await self.process_single_job(job)
            self.queue.task_done()

    def _rebuild_queue(self):
        new_q = asyncio.PriorityQueue()
        queued_jobs = [j for j in self.jobs.values() if j.get("status") == "queued"]
        queued_jobs.sort(key=lambda x: (int(x.get("priority", 100)), x.get("created_at", "")))
        for j in queued_jobs:
            self._counter += 1
            new_q.put_nowait((int(j.get("priority", 100)), self._counter, j["job_id"]))
        self.queue = new_q

    def set_job_priority(self, job_id: str, new_priority: int) -> bool:
        job = self.jobs.get(job_id)
        if not job or job.get("status") != "queued":
            return False
        job["priority"] = int(new_priority)
        db_save_job(job)
        self._rebuild_queue()
        return True

    def set_project_priority(self, workspace: str, new_priority: int) -> int:
        count = 0
        for job in self.jobs.values():
            if job.get("status") == "queued":
                job_ws = job.get("workspace") or ""
                if (
                    workspace.lower() in job_ws.lower()
                    or os.path.basename(job_ws.rstrip("/\\")).lower() == workspace.lower()
                ):
                    job["priority"] = int(new_priority)
                    db_save_job(job)
                    count += 1
        if count > 0:
            self._rebuild_queue()
        return count

    def cancel_job(self, job_id: str) -> bool:
        job = self.jobs.get(job_id)
        if not job:
            return False
        if job.get("status") in ("queued", "interrupted"):
            job["status"] = "cancelled"
            job["error"] = "Cancelado pelo usuario"
            db_save_job(job)
            ev = self.job_events.get(job_id)
            if ev:
                ev.set()
            self._rebuild_queue()
            return True
        return False

    def enqueue_job(self, payload: dict) -> dict:
        job_id = payload.get("job_id") or str(uuid.uuid4())
        workspace = payload.get("workspace") or ""
        priority = int(payload.get("priority", 100))

        raw_in = payload.get("input_path") or payload.get("audio_path") or payload.get("file_path") or ""
        in_abs = podcastapi.resolver_caminho_midia(raw_in, workspace=workspace)

        out_path = (payload.get("output_path") or "").strip().strip('"').strip("'")
        if out_path and not os.path.isabs(out_path) and workspace:
            out_path = os.path.abspath(os.path.join(workspace, out_path))

        strength = int(payload.get("strength", self.config_padrao.get("forca_padrao", 90)))
        bg_strength = int(payload.get("background_strength", self.config_padrao.get("ruido_fundo", 10)))
        music_strength = int(payload.get("music_strength", self.config_padrao.get("musica_fundo", 10)))
        model_ver = podcastapi.resolver_versao_modelo(
            payload.get("model_version") or self.config_padrao.get("versao_modelo", "v2")
        )
        track_comp = str(
            payload.get("track_component") or self.config_padrao.get("componente_faixa", "full")
        ).strip().lower()
        del_after = bool(
            payload.get("delete_after_download", self.config_padrao.get("limpar_apos_download", True))
        )

        job = {
            "job_id": job_id,
            "workspace": workspace,
            "input_path": in_abs,
            "output_path": out_path,
            "strength": max(0, min(100, strength)),
            "background_strength": max(0, min(100, bg_strength)),
            "music_strength": max(0, min(100, music_strength)),
            "model_version": model_ver,
            "track_component": track_comp,
            "delete_after_download": del_after,
            "status": "queued",
            "track_id": None,
            "file_path": None,
            "error": None,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "priority": priority,
        }

        self.jobs[job_id] = job
        self.job_events[job_id] = asyncio.Event()
        db_save_job(job)
        self._counter += 1
        self.queue.put_nowait((priority, self._counter, job_id))
        return job


daemon = AdobePodcastCentralDaemon()


async def handle_health(request: web.Request):
    browser_connected = bool(daemon.browser and daemon.browser.is_connected())
    pool = podcastapi.resumo_pool_contas()
    return web.json_response({
        "ok": True,
        "service": "adobe-podcast",
        "daemon_port": DAEMON_PORT,
        "chrome_port": podcastapi.CHROME_PORT,
        "profile_dir": podcastapi.obter_diretorio_perfil(),
        "active_account": pool.get("active_account"),
        "login_completed_marker": podcastapi.ja_fez_login_inicial(),
        "browser_connected": browser_connected,
        "browser_hidden": daemon.invisivel,
        "queue_size": daemon.queue.qsize(),
        "active_job_id": daemon.active_job_id,
        "codebase_dir": BASE_DIR,
        "pool": pool,
    })


async def handle_enqueue(request: web.Request):
    data = await request.json()
    wait = bool(data.get("wait", True))
    timeout = float(data.get("timeout", 960))

    items = data.get("items")
    if isinstance(items, list):
        created_jobs = [daemon.enqueue_job(it) for it in items]
        if wait:
            events = [daemon.job_events[j["job_id"]].wait() for j in created_jobs]
            await asyncio.wait_for(asyncio.gather(*events), timeout=timeout)
            created_jobs = [daemon.jobs[j["job_id"]] for j in created_jobs]
        return web.json_response({"ok": True, "jobs": created_jobs})

    job = daemon.enqueue_job(data)
    job_id = job["job_id"]
    if wait:
        ev = daemon.job_events.get(job_id)
        if ev:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        job = daemon.jobs[job_id]

    return web.json_response({
        "ok": job.get("status") != "failed",
        "job": job,
    })


async def handle_job_status(request: web.Request):
    job_id = request.match_info["job_id"]
    wait = request.query.get("wait", "0") in ("1", "true", "True")
    job = daemon.jobs.get(job_id)
    if not job:
        return web.json_response({"ok": False, "error": f"Job {job_id} nao encontrado"}, status=404)

    if wait and job["status"] in ("queued", "running", "uploading", "processing", "downloading"):
        ev = daemon.job_events.get(job_id)
        if ev:
            await asyncio.wait_for(ev.wait(), timeout=960)
        job = daemon.jobs.get(job_id)

    return web.json_response({"ok": True, "job": job})


async def handle_queue_summary(request: web.Request):
    all_jobs = list(daemon.jobs.values())
    all_jobs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    queued = [j for j in all_jobs if j["status"] == "queued"]
    queued.sort(key=lambda x: (int(x.get("priority", 100)), x.get("created_at", "")))
    running = [j for j in all_jobs if j["status"] in ("running", "uploading", "processing", "downloading")]
    completed = [j for j in all_jobs if j["status"] == "completed"][:30]
    failed = [j for j in all_jobs if j["status"] in ("failed", "cancelled", "interrupted")][:15]

    projects = {}
    for j in queued:
        ws = j.get("workspace") or "Geral"
        nome_curto = os.path.basename(ws.rstrip("\\/")) if ws != "Geral" else "Geral"
        if nome_curto not in projects:
            projects[nome_curto] = {
                "workspace": ws,
                "name": nome_curto,
                "count": 0,
                "highest_priority": 999,
            }
        projects[nome_curto]["count"] += 1
        p_val = int(j.get("priority", 100))
        if p_val < projects[nome_curto]["highest_priority"]:
            projects[nome_curto]["highest_priority"] = p_val

    return web.json_response({
        "ok": True,
        "active_job_id": daemon.active_job_id,
        "queue_pending_count": daemon.queue.qsize(),
        "running": running,
        "queued": queued,
        "recent_completed": completed,
        "recent_failed": failed,
        "projects": list(projects.values()),
        "pool": podcastapi.resumo_pool_contas(),
    })


async def handle_job_priority(request: web.Request):
    job_id = request.match_info["job_id"]
    data = await request.json()
    new_priority = int(data.get("priority", 10))
    ok = daemon.set_job_priority(job_id, new_priority)
    return web.json_response({"ok": ok, "job": daemon.jobs.get(job_id)})


async def handle_project_priority(request: web.Request):
    data = await request.json()
    workspace = data.get("workspace", "")
    new_priority = int(data.get("priority", 10))
    count = daemon.set_project_priority(workspace, new_priority)
    return web.json_response({"ok": count > 0, "updated_count": count})


async def handle_cancel_job(request: web.Request):
    job_id = request.match_info["job_id"]
    ok = daemon.cancel_job(job_id)
    return web.json_response({"ok": ok, "job_id": job_id})


async def monitor_login_and_close():
    logou_nesta_sessao = False
    atualizou_quota = False
    for _ in range(600):
        await asyncio.sleep(2)
        try:
            if not daemon.browser or not daemon.browser.is_connected():
                if logou_nesta_sessao:
                    podcastapi.marcar_login_concluido()
                    daemon.invisivel = True
                break
            if daemon.page and not daemon.page.is_closed():
                if "podcast.adobe.com" in daemon.page.url.lower():
                    params = await podcastapi.obter_parametros_pagina(daemon.page)
                    if params.get("loggedIn"):
                        logou_nesta_sessao = True
                        podcastapi.marcar_login_concluido()
                        if not atualizou_quota and params.get("accessToken"):
                            try:
                                await podcastapi.consultar_quota_plataforma(daemon.page)
                                atualizou_quota = True
                            except Exception:
                                pass
        except Exception:
            if logou_nesta_sessao:
                podcastapi.marcar_login_concluido()
                daemon.invisivel = True
            break


async def handle_browser_visibility(request: web.Request):
    action = request.match_info["action"].lower()
    async with daemon.browser_lock:
        if action in ("show", "login"):
            page, params, logado = await daemon.ensure_browser_and_page(force_visible=True)
            await podcastapi.posicionar_janela_chrome(page, invisivel=False)
            daemon.invisivel = False
            quota = await podcastapi.consultar_quota_plataforma(page) if logado else None
            if action == "login":
                asyncio.create_task(monitor_login_and_close())
            return web.json_response({
                "ok": True,
                "action": action,
                "visible": True,
                "logged_in": logado,
                "active_quota": quota,
                "login_completed_marker": podcastapi.ja_fez_login_inicial(),
                "url": page.url,
                "message": (
                    "Navegador Google Chrome (perfil isolado Adobe Podcast - porta 9343) visivel na tela! "
                    + (
                        "Voce ja esta logado no Adobe Podcast."
                        if logado
                        else "Faca login na sua conta Adobe. Quando terminar, pode fechar ou usar podcast_hide_browser."
                    )
                ),
            })
        elif action == "hide":
            page, params, logado = await daemon.ensure_browser_and_page(force_visible=False)
            await podcastapi.posicionar_janela_chrome(page, invisivel=True)
            daemon.invisivel = True
            quota = None
            if logado:
                podcastapi.marcar_login_concluido()
                try:
                    quota = await podcastapi.consultar_quota_plataforma(page)
                except Exception:
                    pass
            return web.json_response({
                "ok": True,
                "action": "hide",
                "visible": False,
                "logged_in": logado,
                "active_quota": quota,
                "url": page.url,
                "message": "Navegador do Adobe Podcast movido para fora da tela (X=-2400, Y=0) em modo invisivel.",
            })
        elif action == "clear_tracks":
            page, params, logado = await daemon.ensure_browser_and_page()
            if not logado:
                return web.json_response({"ok": False, "error": "Nao logado no Adobe Podcast"}, status=400)
            removidas = await podcastapi.limpar_todas_tracks(page)
            return web.json_response({"ok": True, "deleted_tracks": removidas, "url": page.url})
        else:
            return web.json_response({"ok": False, "error": f"Acao desconhecida: {action}"}, status=400)


async def handle_diagnose(request: web.Request):
    """Endpoint de Auto-Healing: inspeciona Shadow-DOM ao vivo, chamadas phonos-server e tira screenshot."""
    data = {}
    try:
        data = await request.json()
    except Exception:
        pass
    take_screenshot = bool(data.get("take_screenshot", True))

    async with daemon.browser_lock:
        page, params, logado = await daemon.ensure_browser_and_page()
        diag = await podcastapi.coletar_diagnostico_pagina(
            page,
            motivo=data.get("reason", "Manual AI Agent Diagnostic Call"),
            recent_network=list(daemon.recent_network),
            take_screenshot=take_screenshot,
        )
        return web.json_response({"ok": True, "diagnostics": diag})


async def handle_selectors(request: web.Request):
    """Permite que o Agente de IA atualize selectors.json em tempo real (Auto-Healing)."""
    if request.method == "GET":
        return web.json_response({"ok": True, "selectors": podcastapi.carregar_seletores()})
    data = await request.json()
    atualizado = podcastapi.atualizar_seletores(data)
    return web.json_response({"ok": True, "selectors": atualizado})


async def handle_quota(request: web.Request):
    """
    Consulta a quota diaria real na plataforma Adobe Podcast (Free = 1h/dia | Pago = 4h/dia)
    e retorna tambem o status consolidado do pool multi-contas.
    """
    data = {}
    if request.method == "POST":
        try:
            data = await request.json()
        except Exception:
            pass
    live = request.query.get("live", str(data.get("live", "1"))) not in ("0", "false", "False")
    check_file = data.get("input_path") or request.query.get("input_path", "")

    active_quota = None
    if live and not daemon.browser_lock.locked():
        try:
            async with daemon.browser_lock:
                page, params, logado = await daemon.ensure_browser_and_page()
                active_quota = await podcastapi.consultar_quota_plataforma(page)
        except Exception as e:
            active_quota = {"ok": False, "error": str(e)}

    pool = podcastapi.resumo_pool_contas()
    if not active_quota:
        ativa_id = pool.get("active_account")
        for acc in pool.get("accounts", []):
            if acc.get("id") == ativa_id:
                active_quota = acc
                break

    file_check = None
    if check_file:
        abs_f = podcastapi.resolver_caminho_midia(check_file)
        if os.path.exists(abs_f):
            dur = podcastapi.obter_duria_sec(abs_f) if hasattr(podcastapi, "obter_duria_sec") else podcastapi.obter_duracao_midia_segundos(abs_f)
            rem_min = float((active_quota or {}).get("quota_remaining_min") or 0.0)
            pool_rem_min = float(pool.get("pool_remaining_min") or 0.0)
            dur_min = round(dur / 60.0, 2)
            file_check = {
                "input_path": abs_f,
                "duration_sec": dur,
                "duration_min": dur_min,
                "duration_human": podcastapi.formatar_duracao(dur),
                "fits_active_account": rem_min * 60.0 >= dur,
                "fits_multi_account_pool": pool_rem_min * 60.0 >= dur,
            }

    return web.json_response({
        "ok": True,
        "active_quota": active_quota,
        "pool": pool,
        "file_check": file_check,
    })


async def handle_accounts(request: web.Request):
    """
    Gerencia o pool multi-contas do Adobe Podcast:
      - GET /accounts ou POST action="list": Lista todas as contas e saldos diarios
      - POST action="add": Cria um novo perfil Chrome isolado para uma nova conta Adobe
      - POST action="switch": Troca a conta ativa na porta 9343 e consulta a quota na plataforma
      - POST action="login": Troca para a conta informada e abre o Chrome visivel na tela para login
      - POST action="remove": Remove uma conta secundaria do pool
      - POST action="set_auto_rotate": Ativa/desativa a rotacao automatica quando o tempo diario acabar
    """
    if request.method == "GET":
        return web.json_response({"ok": True, "pool": podcastapi.resumo_pool_contas()})

    data = await request.json()
    action = str(data.get("action") or "list").strip().lower()
    account_id = str(data.get("account_id") or data.get("account_name") or "").strip()
    label = str(data.get("label") or "").strip()

    if action == "list":
        return web.json_response({"ok": True, "pool": podcastapi.resumo_pool_contas()})

    elif action == "add":
        nova = podcastapi.adicionar_conta(account_id=account_id, label=label)
        open_login = bool(data.get("open_login", True))
        if open_login:
            async with daemon.browser_lock:
                page, params, logado = await daemon.switch_account_unlocked(nova["id"], force_visible=True)
                await podcastapi.posicionar_janela_chrome(page, invisivel=False)
                daemon.invisivel = False
                asyncio.create_task(monitor_login_and_close())
                quota = await podcastapi.consultar_quota_plataforma(page, account_id=nova["id"]) if logado else None
                return web.json_response({
                    "ok": True,
                    "action": "add",
                    "account": nova,
                    "logged_in": logado,
                    "active_quota": quota,
                    "pool": podcastapi.resumo_pool_contas(),
                    "message": (
                        f"Novo perfil '{nova['id']}' ({nova['profile_dir']}) criado e aberto na tela (porta 9343)! "
                        "Faca login nessa nova conta Adobe para que o sistema possa puxa-la automaticamente quando o tempo da outra acabar."
                    ),
                })
        return web.json_response({
            "ok": True,
            "action": "add",
            "account": nova,
            "pool": podcastapi.resumo_pool_contas(),
        })

    elif action in ("switch", "login"):
        if not account_id:
            return web.json_response({"ok": False, "error": "Informe `account_id` para alternar de conta."}, status=400)
        force_vis = True if action == "login" else None
        async with daemon.browser_lock:
            page, params, logado = await daemon.switch_account_unlocked(account_id, force_visible=force_vis)
            if action == "login" or not logado:
                await podcastapi.posicionar_janela_chrome(page, invisivel=False)
                daemon.invisivel = False
                asyncio.create_task(monitor_login_and_close())
            quota = await podcastapi.consultar_quota_plataforma(page) if logado else None
            return web.json_response({
                "ok": True,
                "action": action,
                "active_account": podcastapi.obter_conta_ativa()["id"],
                "logged_in": logado,
                "visible": not daemon.invisivel,
                "active_quota": quota,
                "pool": podcastapi.resumo_pool_contas(),
                "message": (
                    f"Conta ativa alterada para '{account_id}'! "
                    + (
                        f"Logado como {quota.get('email') or account_id} (Restam {quota.get('quota_remaining_human')} hoje)."
                        if (logado and quota)
                        else "Navegador aberto na tela para voce fazer login nesta conta Adobe."
                    )
                ),
            })

    elif action == "remove":
        ativa_antes = podcastapi.obter_conta_ativa()["id"]
        ok = podcastapi.remover_conta(account_id)
        ativa_depois = podcastapi.obter_conta_ativa()["id"]
        if ok and ativa_antes != ativa_depois and daemon.browser and daemon.browser.is_connected():
            async with daemon.browser_lock:
                await daemon.switch_account_unlocked(ativa_depois)
        return web.json_response({
            "ok": ok,
            "action": "remove",
            "active_account": ativa_depois,
            "pool": podcastapi.resumo_pool_contas(),
        })

    elif action == "set_auto_rotate":
        dados = podcastapi.carregar_contas()
        dados["auto_rotate"] = bool(data.get("auto_rotate", True))
        podcastapi.salvar_contas(dados)
        return web.json_response({
            "ok": True,
            "auto_rotate": dados["auto_rotate"],
            "pool": podcastapi.resumo_pool_contas(),
        })

    return web.json_response({"ok": False, "error": f"Acao desconhecida em /accounts: {action}"}, status=400)


async def handle_reload(request: web.Request):
    """Recarrega o modulo podcastapi.py em memoria sem derrubar a fila."""
    importlib.reload(podcastapi)
    daemon.config_padrao = podcastapi.carregar_config()
    return web.json_response({
        "ok": True,
        "message": "Modulo podcastapi.py e selectors.json recarregados com sucesso em tempo real!",
    })


async def on_startup(app: web.Application):
    init_db()
    await daemon.start()


async def on_cleanup(app: web.Application):
    await daemon.stop()


@web.middleware
async def cors_middleware(request: web.Request, handler):
    if request.method == "OPTIONS":
        resp = web.Response()
    else:
        resp = await handler(request)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, PUT, DELETE"
    resp.headers["Access-Control-Allow-Headers"] = "*"
    return resp


def create_app() -> web.Application:
    app = web.Application(middlewares=[cors_middleware])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_route(
        "OPTIONS",
        "/{tail:.*}",
        lambda r: web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*",
            }
        ),
    )
    app.router.add_get("/health", handle_health)
    app.router.add_post("/enqueue", handle_enqueue)
    app.router.add_get("/queue", handle_queue_summary)
    app.router.add_get("/quota", handle_quota)
    app.router.add_post("/quota", handle_quota)
    app.router.add_get("/accounts", handle_accounts)
    app.router.add_post("/accounts", handle_accounts)
    app.router.add_get("/jobs/{job_id}", handle_job_status)
    app.router.add_post("/jobs/{job_id}/priority", handle_job_priority)
    app.router.add_post("/jobs/{job_id}/cancel", handle_cancel_job)
    app.router.add_post("/projects/priority", handle_project_priority)
    app.router.add_post("/browser/{action}", handle_browser_visibility)
    app.router.add_post("/diagnose", handle_diagnose)
    app.router.add_get("/selectors", handle_selectors)
    app.router.add_post("/selectors", handle_selectors)
    app.router.add_post("/reload", handle_reload)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), host=DAEMON_HOST, port=DAEMON_PORT, print=None)
