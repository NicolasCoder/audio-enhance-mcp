# CLAUDE.md — Adobe Podcast Studio MCP (Instruções para Claude Code / Agentes de IA)

Este repositório contém o **Adobe Podcast Studio MCP**, um servidor MCP + Daemon de Fila Central Única para limpeza e aprimoramento automatizado de áudios e vídeos (**Enhance Speech V2 SoundLift / V1**) no **Adobe Podcast** (`https://podcast.adobe.com/enhance`), operando 100% invisível fora da tela (off-screen), com **Auto-Healing (Auto-Reparo)** e **zero conflito de porta/navegador com o Flow Studio MCP**.

## 1. Isolamento Paralelo (Adobe Podcast vs Flow Studio)

- **Flow Studio MCP**: Chrome CDP na porta `9333` | Daemon na porta `9334` | Perfil `C:\flow-mcp-profile`
- **Adobe Podcast MCP**: Chrome CDP na porta `9343` | Daemon na porta `9344` | Perfil `C:\adobe-podcast-mcp-profile` (ou `~/.adobe-podcast-profile`)

Ambos podem rodar simultaneamente em paralelo na mesma máquina sem colisão de portas, janelas ou ícones da bandeja do sistema.

## 2. Como Configurar / Instalar para o Usuário

1. Execute no terminal:
   ```bash
   python setup_mcp.py
   ```
   Isso instalará as dependências e registrará o servidor MCP `"adobe-podcast"` nos arquivos `.mcp.json`, `~/.claude.json`, `~/.cursor/mcp.json` e `~/.gemini/config/mcp_config.json` **sem sobrescrever o `"flow-studio"`**.
2. Abra o navegador isolado da porta `9343` para o primeiro login do usuário chamando a ferramenta MCP `podcast_login_browser` (ou executando `python -c "import asyncio, podcast_mcp_server; print(asyncio.run(podcast_mcp_server.podcast_login_browser()))"`).
3. Peça ao usuário para fazer login na conta Adobe na janela que se abriu. Assim que ele confirmar, chame `podcast_hide_browser` para esconder a janela fora da tela (`X = -2400, Y = 0`).

## 3. Arquitetura do Projeto

- **`podcast_mcp_server.py`**: Servidor MCP (`FastMCP("adobe-podcast")`) com as ferramentas `podcast_enhance` (que extrai automaticamente apenas o áudio via FFmpeg sempre que a entrada for um vídeo `.mp4`/`.mov`/`.mkv`), `podcast_enhance_batch`, `podcast_quota_status` (consulta de saldo diário 1h Free / 4h Pago na API da Adobe), `podcast_accounts` (pool multi-contas com rotação automática), `podcast_queue_status`, `podcast_login_browser`, `podcast_show_browser`, `podcast_hide_browser`, `podcast_clear_tracks`, `podcast_diagnose`, `podcast_update_selectors` e `podcast_reload_daemon`.
- **`podcast_queue_daemon.py`**: Serviço HTTP local (`http://127.0.0.1:9344`) que mantém uma única fila global com prioridade (`asyncio.PriorityQueue` + SQLite `podcast_queue.db`).
- **`podcastapi.py`**: Motor Playwright híbrido (Shadow-DOM + API `https://phonos-server-flex.adobe.io`) que controla o Google Chrome isolado na porta `9343`.
- **`podcast_tray.py`**: Painel flutuante estilo Apple Control Center na bandeja do sistema com ícone exclusivo de Ondas de Áudio.
- **`selectors.json`**: Arquivo JSON recarregado dinamicamente em tempo real contendo seletores CSS/Shadow-DOM, `data-testid`s e rotas da API `phonos-server-flex.adobe.io`.

## 4. Protocolo de Auto-Healing (Auto-Reparo quando o Adobe Podcast mudar)

Se qualquer aprimoramento falhar ou se o Adobe Podcast atualizar seletores, Web Components `<sp-*>` ou rotas de API:
1. **Execute o Diagnóstico**: Chame `podcast_diagnose(take_screenshot=True)`.
2. **Analise o Relatório e o Screenshot**:
   - Verifique `diagnostics/last_diagnostic_screenshot.png`.
   - Compare os elementos de `dom_snapshot.testids`, `dom_snapshot.inputs`, `dom_snapshot.buttons` (extraídos via varredura recursiva de Shadow DOM) e `recent_network_calls` com o que está configurado em `selectors.json`.
3. **Aplique o Reparo Quente (Hot-Fix)**:
   - Se um seletor ou rota de API mudou, atualize `selectors.json` usando `podcast_update_selectors(...)`.
   - Se editou `podcastapi.py`, chame `podcast_reload_daemon()` para recarregar o código no daemon sem reiniciar a fila.
   - Reexecute `podcast_enhance` imediatamente!
