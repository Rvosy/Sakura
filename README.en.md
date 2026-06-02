[中文](README.md)

# Sakura Desktop Pet

A desktop companion Agent — chats, changes expressions, speaks, remembers what you allow, and helps with tasks after confirmation. It is not just a "desktop pet + chat" but a desktop companion Agent.

![Sakura Preview](_pet_style_preview.png)

## Quick Start

**Prerequisites:** Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Edit data/config/api.yaml with your API Key
notepad data/config/api.yaml

python main.py
```

**Minimal `data/config/api.yaml`:**

```yaml
llm:
  base_url: https://api.openai.com/v1
  api_key: your_api_key_here
  model: gpt-4.1-mini
  timeout_seconds: 60
```

## Project Structure

```
app/
  agent/         # Agent decision layer (AgentRuntime, tools, memory, MCP)
  core/          # App core (AppContext, bootstrap, ChatPipeline, debug)
  config/        # Config management (YAML read/write, models, migrations)
  llm/           # LLM client (OpenAI-compatible, ChatReply, prompts)
  plugins/       # Native plugin system (discovery, capabilities, manager)
  storage/       # Storage layer (StoragePaths, chat history, visual obs)
  ui/            # UI components (PetWindow, settings, history, portrait)
  voice/         # TTS providers (GPT-SoVITS, playback)
sdk/             # Shinsekai compat layer (deprecated, use app/plugins/)
plugins/         # Local plugins
data/config/     # YAML configuration files
tests/           # pytest tests
docs/            # Documentation (ARCHITECTURE.md, etc.)
```

## Configuration

All config in YAML under `data/config/`:

| YAML Path | Description | Default |
|---|---|---|
| `api.yaml: llm.base_url` | API base URL | `https://api.openai.com/v1` |
| `api.yaml: llm.api_key` | API Key | (empty) |
| `api.yaml: llm.model` | Model name | `gpt-4.1-mini` |
| `system_config.yaml: ui.subtitle_language` | Subtitle lang (`ja`/`zh`) | `ja` |
| `system_config.yaml: proactive_care.enabled` | Proactive care | `false` |
| `system_config.yaml: debug.enabled` | Debug logging | `false` |

## Testing

```powershell
python -m pytest
```
