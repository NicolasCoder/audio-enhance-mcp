# AGENTS.md — Adobe Podcast Studio MCP (Instructions for OpenAI Codex / GPT / Cursor / Windsurf / Antigravity)

Este arquivo fornece contexto completo para qualquer Agente de IA configurar, operar e **auto-reparar (Auto-Heal)** o **Adobe Podcast Studio MCP** em paralelo ao **Flow Studio MCP**.

## 1. Isolamento Total de Portas e Navegador

- **Adobe Podcast Chrome CDP**: Porta `9343` (Perfil exclusivo `C:\adobe-podcast-mcp-profile` ou `~/.adobe-podcast-profile`)
- **Adobe Podcast Queue Daemon**: Porta `9344` (`http://127.0.0.1:9344`)
- **Zero Conflito**: Não interfere no Flow Studio (`9333`/`9334`) nem no `pc-gpu-bridge` (`9335`).

## 2. Setup Automático para o Usuário

1. Execute `python setup_mcp.py` para registrar o servidor MCP `adobe-podcast` e a skill global `adobe-podcast`.
2. Inicie o login único chamando `podcast_login_browser`.
3. Após o usuário logar na conta Adobe, chame `podcast_hide_browser` para ocultar a janela fora da tela (`X=-2400, Y=0`).

## 3. Como Usar as Tools do MCP `adobe-podcast`

- `podcast_enhance`: Envia um áudio ou vídeo (`input_path`) para a fila central (`127.0.0.1:9344`). **Sempre que a entrada for um vídeo (`.mp4`, `.mov`, `.mkv`, etc.), extrai automaticamente apenas o áudio via FFmpeg antes de enviar para o Adobe Podcast** (economizando banda/tamanho e entregando `<nome>_enhanced.wav` na mesma duração exata para substituir o áudio original do vídeo). Mede a duração do arquivo, verifica a quota diária restante na plataforma (Free = 1h/dia | Pago = 4h/dia, rotacionando automaticamente de conta se necessário), aplica o Enhance Speech (`model_version="v2"` ou `"v1"`, `strength=0..100`, `background_strength=0..100`) e salva em `output_path` (`.wav`, `.mp3`, `.m4a`, `.flac`).
- `podcast_enhance_batch`: Enfileira múltiplos arquivos de áudio/vídeo de uma só vez.
- `podcast_quota_status`: Consulta diretamente na API do Adobe Podcast (`/api/v1/user_metrics` e `/api/v1/user`) quanto tempo diário já foi usado, quanto resta hoje (1h no gratuito / 4h no pago) e quando reseta.
- `podcast_accounts`: Gerencia o pool multi-contas (`action="list" | "add" | "switch" | "login" | "remove" | "set_auto_rotate"`), criando um perfil do Chrome por conta (`C:\adobe-podcast-mcp-profile-<id>`) e puxando a próxima conta automaticamente quando o tempo diário acabar.
- `podcast_queue_status`: Consulta a fila global compartilhada entre projetos.
- `podcast_show_browser` / `podcast_hide_browser` / `podcast_login_browser`: Controla a visibilidade da janela do Chrome (`porta 9343`) em tempo real.
- `podcast_clear_tracks`: Remove faixas antigas da lista web do Adobe Podcast.

## 4. Auto-Healing (Como o Agente se Auto-Conserta se o Adobe Podcast mudar)

Se um job falhar por mudança na interface (`<sp-*>` Shadow DOM) ou API (`phonos-server-flex.adobe.io`):
1. Chame `podcast_diagnose(take_screenshot=True)`.
2. Inspecione `diagnostics/last_diagnostic_screenshot.png` e `diagnostics/last_diagnostic_report.json`.
3. Atualize o seletor ou endpoint em `selectors.json` usando `podcast_update_selectors` (ou edite `podcastapi.py` e chame `podcast_reload_daemon`).
