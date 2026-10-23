# 🎙️ Adobe Podcast Studio MCP — Enhance Speech V2 (SoundLift) AI Server

<p align="center">
  <img src="https://img.shields.io/badge/MCP-Model%20Context%20Protocol-686df4?style=for-the-badge" alt="MCP"/>
  <img src="https://img.shields.io/badge/Adobe%20Podcast-Enhance%20Speech%20V2-10b981?style=for-the-badge" alt="Adobe Podcast"/>
  <img src="https://img.shields.io/badge/Auto--Healing-Shadow%20DOM%20%2B%20Phonos%20API-f59e0b?style=for-the-badge" alt="Auto-Healing"/>
  <img src="https://img.shields.io/badge/Parallel%20Ready-Port%209343%20%2F%209344-ec4899?style=for-the-badge" alt="Parallel Ready"/>
</p>

> **Transforme o [Adobe Podcast Enhance Speech](https://podcast.adobe.com/enhance) em um Servidor MCP + Skill + Central de Fila Multi-Projeto com System Tray Flyout e Auto-Healing nativo.**  
> Projetado para rodar **100% em paralelo com o Flow Studio MCP** (outro Chrome isolado na porta `9343` e outro Daemon na porta `9344`, sem qualquer conflito!).

---

## 🌟 Sobre o Projeto

O **Adobe Podcast Studio MCP** permite que qualquer agente de Inteligência Artificial (**Antigravity, Claude Code, Cursor, Codex**) ou você mesmo (pelo painel flutuante na bandeja do sistema) limpe e aprimore gravações de voz e vídeos com qualidade de estúdio usando o **Enhance Speech V2 (SoundLift)** — **100% em segundo plano (invisível e sem ícone na barra de tarefas)**.

### ✨ Principais Diferenciais

- **🔀 Isolamento Total para Rodar em Paralelo com o Flow Studio**:
  | Serviço | Porta Chrome CDP | Porta Daemon Fila | Perfil Exclusivo do Chrome |
  | :--- | :---: | :---: | :--- |
  | **Flow Studio MCP** | `9333` | `9334` | `C:\flow-mcp-profile` |
  | **Adobe Podcast MCP** | **`9343`** | **`9344`** | **`C:\adobe-podcast-mcp-profile`** |
  Você pode gerar vídeos/imagens no Flow e limpar narrações no Adobe Podcast **exatamente ao mesmo tempo**, cada um no seu próprio Chrome invisível e com seu próprio ícone na bandeja do sistema!
- **🎬 Extração Automática de Áudio para Vídeos (`.mp4`, `.mov`, `.mkv`)**:
  Sempre que você envia um vídeo para aprimorar a voz, o sistema **extrai automaticamente apenas a faixa de áudio via FFmpeg** antes de subir para o Adobe Podcast (reduzindo vídeos de centenas de MBs para poucos MBs de áudio puro) e entrega `<nome>_enhanced.wav` na mesma duração exata para substituir no editor.
- **🔄 Controle de Quota Diária (1h Free / 4h Pago) & Pool Multi-Contas com Rotação Automática**:
  Consulta em tempo real na API do Adobe Podcast (`/api/v1/user_metrics` e `/api/v1/user`) quantos minutos já foram usados hoje, quanto falta e quando reseta. Permite cadastrar múltiplas contas Adobe (`podcast_accounts`), puxando a próxima conta automaticamente quando o tempo diário da conta atual acabar.
- **🎚️ Controle de Strength (0–100%) e Ambiente (V2 SoundLift)**:
  Usa a matemática oficial de mixagem do Adobe Podcast (`computeTwoSliderGains` / `phonos-server-flex.adobe.io`) para exportar o áudio na intensidade exata desejada (padrão `90%`), além de converter automaticamente para `.mp3` (`320kbps`), `.m4a`, `.flac` ou manter em `.wav` lossless.
- **👻 Modo 100% Invisível (`Off-Screen` + Zero Ícone na Taskbar)**:
  Após o primeiro login na conta Adobe, tanto a janela do Chrome (`porta 9343`) quanto seu ícone na barra de tarefas ficam ocultos (`ITaskbarList::DeleteTab` filtrado exclusivamente pela porta `9343`).
- **🩹 Auto-Healing Nativo com Varredura de Shadow DOM e `selector_health`**:
  O Adobe Podcast utiliza **Adobe Spectrum Web Components (`<sp-button>`, `<sp-slider>`, `<sp-theme>`)** com Shadow DOM. Nossa ferramenta `podcast_diagnose` atravessa recursivamente todos os `shadowRoot` da página, valida cada seletor em tempo real (`selector_health`) e monitora as chamadas para `https://phonos-server-flex.adobe.io`, permitindo que a IA atualize o [`selectors.json`](./selectors.json) em tempo real via `podcast_update_selectors`.

---

## 🚀 Instalação Rápida

```bash
python setup_mcp.py
```

Para iniciar o ícone exclusivo de Ondas de Áudio na bandeja do sistema (System Tray Flyout), dê dois cliques em **[`abrir_dashboard.bat`](./abrir_dashboard.bat)** (ou execute `pythonw podcast_tray.py`).

---

## 🛠️ Ferramentas MCP Disponíveis (`adobe-podcast`)

| Ferramenta MCP | Descrição |
| :--- | :--- |
| `podcast_enhance` | Envia um áudio ou vídeo (`.wav`, `.mp3`, `.m4a`, `.mp4`, `.mov`) para a Fila Central (`9344`), extrai automaticamente o áudio se for vídeo, verifica a quota diária, aplica o Enhance Speech V2 com `strength` configurável e salva o arquivo limpo em `output_path`. |
| `podcast_enhance_batch` | Enfileira múltiplos arquivos de áudio/vídeo de uma só vez na Fila Central Única. |
| `podcast_quota_status` | Consulta em tempo real na plataforma Adobe Podcast quanto tempo diário já foi usado e quanto resta hoje (**1h/dia no Gratuito** \| **4h/dia no Pago**), tempo até o reset e se um arquivo específico cabe no saldo. |
| `podcast_accounts` | Gerencia o pool **Multi-Contas** (`action="list" \| "add" \| "switch" \| "login" \| "remove" \| "set_auto_rotate"`) com troca automática de conta quando a cota diária acabar. |
| `podcast_queue_status` | Consulta o status de um `job_id` específico ou lista todos os jobs e projetos na fila global. |
| `podcast_show_browser` | Traz o Chrome do Adobe Podcast (`porta 9343`) para a tela (`X = 120, Y = 90`). |
| `podcast_hide_browser` | Oculta o Chrome do Adobe Podcast fora da tela (`X = -2400, Y = 0`) e remove seu ícone da barra de tarefas. |
| `podcast_login_browser` | Abre o Chrome visível (`porta 9343`) para login inicial na conta Adobe. |
| `podcast_clear_tracks` | Remove todas as faixas antigas da lista web do Adobe Podcast. |
| `podcast_diagnose` | **(Auto-Healing)** Coleta Screenshot + Varredura profunda de Shadow DOM (`<sp-*>`, `data-testid`, `selector_health`) + Chamadas `phonos-server-flex.adobe.io`. |
| `podcast_update_selectors` | **(Auto-Healing)** Atualiza seletores ou endpoints em `selectors.json` a quente. |
| `podcast_reload_daemon` | **(Auto-Healing)** Recarrega `podcastapi.py` e `selectors.json` no Daemon sem derrubar a fila. |
