---
name: adobe-podcast
description: Aprimora áudios e vídeos com qualidade de estúdio no Adobe Podcast Enhance Speech (V2 SoundLift / V1) usando uma Fila Central Única (porta 9344) e Google Chrome isolado (porta 9343), 100% em paralelo e sem conflito com o Flow Studio (9333/9334), com controle de Strength (0-100%), modo invisível off-screen (/showbrowser, /hidebrowser, /loginbrowser) e protocolo completo de Auto-Healing (Shadow-DOM + Rede).
---

# Adobe Podcast Studio (MCP + Fila Central Única + Auto-Healing)

Esta skill instrui o agente de IA a operar, instalar e **auto-reparar (Auto-Heal)** o ecossistema **Adobe Podcast Studio MCP** para limpeza e aprimoramento de voz com IA no **Adobe Podcast Enhance Speech** (`https://podcast.adobe.com/enhance`).

---

## 1. Isolamento Total para Operação em Paralelo com o Flow Studio

Este projeto foi projetado para rodar **simultaneamente** ao lado do `flow-studio-mcp` e do `pc-gpu-bridge` sem qualquer conflito de navegador, porta ou perfil:
- **Flow Studio MCP**: Chrome na porta `9333` | Daemon na porta `9334` | Perfil `C:\flow-mcp-profile`
- **Adobe Podcast MCP**: Chrome na porta `9343` | Daemon na porta `9344` | Perfil `C:\adobe-podcast-mcp-profile` (ou `~/.adobe-podcast-profile`)

---

## 2. Instalação Zero-Toque (Caso o usuário acabe de clonar o repositório)

Se o usuário pedir para instalar ou configurar o projeto pela primeira vez:
1. Execute `python setup_mcp.py` na pasta do repositório. Ele instala as dependências e registra automaticamente o servidor MCP `adobe-podcast` e a Skill global no Antigravity, Claude Code e Cursor **preservando todos os outros MCPs existentes (como o `flow-studio`)**.
2. Em seguida, chame a ferramenta `podcast_login_browser` para abrir a janela do Google Chrome isolado (`porta 9343`) visível na tela e peça para o usuário fazer login na conta Adobe uma única vez.
3. Assim que o usuário confirmar que fez login, chame `podcast_hide_browser` para mover o navegador para fora da tela (`X = -2400, Y = 0`) e remover o ícone da barra de tarefas.

---

## 3. Ferramentas do MCP (`adobe-podcast`)

- **`podcast_enhance`**: Envia 1 arquivo de áudio ou vídeo (`.wav`, `.mp3`, `.m4a`, `.flac`, `.mp4`, `.mov`) para a fila central única (`porta 9344`) e baixa o áudio limpo em `output_path`.
  - **Regra Automática para Vídeos (`.mp4`, `.mov`, `.mkv`, etc.)**: Sempre que `input_path` for um vídeo, o sistema **extrai automaticamente apenas a faixa de áudio via FFmpeg** antes de fazer upload no Adobe Podcast (reduzindo vídeos de centenas de MBs para poucos MBs de áudio puro e evitando limites de tamanho) e entrega o arquivo de áudio limpo (`<nome>_enhanced.wav` ao lado do vídeo original) com a duração exata para o editor apenas substituir o áudio original do vídeo.
  - `input_path` (str): Caminho completo do áudio ou vídeo original.
  - `output_path` (str): Caminho completo do arquivo de saída (`.wav`, `.mp3`, `.m4a`, `.flac`) ou pasta de destino. Se terminar em `.mp3`, converte automaticamente via FFmpeg em `320kbps`.
  - `strength` (int): Intensidade do aprimoramento da fala (`0` a `100`, padrão `90`).
  - `background_strength` (int): Nível de preservação do ambiente natural (`0` a `100`, padrão `10`, modelo `v2`).
  - `music_strength` (int): Nível de preservação de música (`0` a `100`, padrão `10`).
  - `model_version` (str): `"v2"` (SoundLift AI — padrão) ou `"v1"` (Clássico).
  - `track_component` (str): `"full"` (mix completo), `"speech"` (apenas voz isolada), `"background"` (apenas som ambiente).
  - `delete_after_download` (bool): Se `True` (padrão), exclui a faixa da lista web do Adobe Podcast após o download.
  - `priority` (int): `10` (Alta Prioridade — fura fila), `100` (Normal), `200` (Baixa).
- **`podcast_enhance_batch`**: Enfileira múltiplos arquivos de áudio/vídeo de uma só vez na fila central.
- **`podcast_queue_status`**: Consulta toda a fila global (compartilhada entre múltiplos projetos) ou acompanha um `job_id`.
- **`podcast_quota_status`**: Consulta em tempo real na plataforma do Adobe Podcast (`/api/v1/user_metrics` e `/api/v1/user`) quanto tempo diário já foi usado e quanto falta (**Gratuito = 1h/dia** | **Pago/Premium = 4h/dia**), em quanto tempo a cota diária reseta e o saldo consolidado do pool multi-contas. Se receber `input_path`, mede a duração exata do áudio/vídeo e verifica se cabe no saldo diário antes de enviar.
- **`podcast_accounts`**: Gerencia o sistema **Multi-Contas (Multi-Profile Pool)** com rotação automática:
  - `action="list"`: Lista todas as contas/perfis cadastrados (`accounts.json`) e o tempo restante de cada uma hoje.
  - `action="add"`: Cria um novo perfil isolado do Chrome (`C:\adobe-podcast-mcp-profile-<account_id>`) e abre na tela para o usuário fazer login em outra conta Adobe.
  - `action="switch"`: Troca imediatamente para outra conta (`account_id`) na porta `9343`.
  - `action="login"`: Abre o Chrome visível na conta informada para login.
  - `action="set_auto_rotate"`: Ativa/desativa a troca automática de conta quando o limite diário (1h Free / 4h Pago) de uma conta acabar.
- **`podcast_show_browser` / `podcast_hide_browser` / `podcast_login_browser`**: Mostra (`X=120, Y=90`), esconde (`X=-2400, Y=0`) ou abre o Chrome exclusivo (`porta 9343`) para login na conta Adobe.
- **`podcast_clear_tracks`**: Limpa todas as faixas antigas acumuladas na conta do Adobe Podcast.
- **`podcast_diagnose` / `podcast_update_selectors` / `podcast_reload_daemon`**: Ferramentas de **Auto-Healing** em tempo real.

---

## 4. Protocolo de Auto-Healing (Auto-Reparo pelo Agente de IA)

O site do Adobe Podcast (`https://podcast.adobe.com/enhance`) utiliza **Adobe Spectrum Web Components (`<sp-theme>`, `<sp-button>`, `<sp-slider>`, `<sp-switch>`) com Shadow DOM** e se comunica com o gateway **`https://phonos-server-flex.adobe.io/api/v1/enhance_speech_tracks`**.

Se qualquer operação falhar por atualização na interface ou API do Adobe Podcast, **você (Agente de IA) DEVE diagnosticar e consertar o projeto autonomamente** seguindo este roteiro:

### Passo 1: Inspecionar a falha e o Shadow-DOM ao vivo
1. Quando `podcast_enhance` falha, ele já retorna um objeto `diagnostics` com `report_path`, `screenshot_path`, `recent_network` e `alerts_or_modals`.
2. Chame a ferramenta MCP **`podcast_diagnose(take_screenshot=True)`**.
3. Leia os dados retornados por `podcast_diagnose` (que varre recursivamente todos os `shadowRoot` da página) e visualize a imagem `diagnostics/last_diagnostic_screenshot.png`.

### Passo 2: Identificar o que mudou no Adobe Podcast
Verifique as 3 causas clássicas de mudança no Adobe Podcast:
1. **Mudança de Seletor ou `data-testid` (`selectors.json` -> `selectors`)**:
   - Compare os seletores atuais em `selectors.json` com `dom_snapshot.testids`, `dom_snapshot.inputs` e `dom_snapshot.buttons` retornados por `podcast_diagnose`.
   - Exemplo: se o input de upload mudou de `#enhance-file-upload` para outro ID, ou o botão de download mudou de `[data-testid="download-button"]`, atualize `file_input` ou `download_btn` em `selectors.json`.
2. **Mudança de Rota na API `phonos-server-flex.adobe.io` (`selectors.json` -> `api_endpoints`)**:
   - Atualmente o Adobe Podcast usa:
     - Gateway: `https://phonos-server-flex.adobe.io` com header `X-Api-Key: phonos-server-prod` e `Authorization: Bearer <adobeIMS_token>`
     - Criação e listagem de faixas: `/api/v1/enhance_speech_tracks`
     - Exportação com Strength customizado: `/api/v1/enhance_speech_tracks/{track_id}/exports`
   - Inspecione `recent_network_calls` no retorno de `podcast_diagnose` para ver as URLs reais chamadas pelo front-end quando um arquivo é enviado.
3. **Modais de Upsell (Adobe Firefly Pro) ou Limites de Conta**:
   - O Adobe Podcast exibe modais promocionais (`[data-testid="upsell-dialog-close-button"]`). Sempre feche-os com `fechar_modais_upsell(page)`.
   - Mantenha sempre o navegador em `windowState: "normal"` fora da tela (`left: -2400, top: 0`) — nunca use `--headless` nem minimize a janela, pois o Adobe IMS e o WebAudio Context exigem janela ativa.

### Passo 3: Aplicar a correção em tempo real (Sem derrubar a fila) e Subir para o GitHub
- Se a mudança for de seletor CSS/Shadow-DOM ou endpoint em `selectors.json`:
  - Chame **`podcast_update_selectors({"selectors": {"nome_do_seletor": "novo_seletor"}})`**. A alteração entra em vigor instantaneamente!
- Se precisar ajustar alguma lógica Python em `podcastapi.py`:
  - Edite `podcastapi.py` e chame **`podcast_reload_daemon()`** para recarregar o módulo em memória no daemon central (`porta 9344`).
- Reexecute `podcast_enhance` imediatamente para validar que o Auto-Healing funcionou!
- **REGRA OBRIGATÓRIA DE PUSH NO GITHUB**: Assim que atualizar qualquer seletor (`selectors.json`) ou código do Auto-Healing (`podcastapi.py`), faça **imediatamente** `git add selectors.json podcastapi.py && git commit -m "chore(auto-heal): update Adobe Podcast selectors/logic" && git push origin main` na pasta `F:\Python Projects\POD_Audio_Voz\audio-enhance-mcp` para manter o GitHub sempre atualizado!
