[中文](../README.md)

# Sakura Desktop Pet

A desktop companion Agent — chats, changes expressions, speaks, remembers what you allow, and helps with tasks after confirmation. It is not just a "desktop pet + chat" but a desktop companion Agent.

![Sakura Preview](../assets/sakura_01.png)

## Quick Start

> **On macOS?** See [MACOS_SETUP.md](MACOS_SETUP.md) before you begin.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Fill in your API Key
notepad data/config/api.yaml

python main.py
```

Minimal `data/config/api.yaml`:

```yaml
llm:
  base_url: https://api.openai.com/v1
  api_key: your_api_key_here
  model: gpt-4.1-mini
  timeout_seconds: 60
```

## Features

- **Character-pack driven.** Personality card, portraits, voice references, and GPT-SoVITS weights are all bundled per character.
- **Proactive.** Sakura observes context on a timer and speaks up on her own — you don't have to always start the conversation.
- **Bilingual replies.** Model outputs Japanese dialogue + Chinese subtitle + mood tag; UI drives subtitles, expressions, and voice in sync.
- **Screen observation.** On-demand screenshots and autonomous visual summaries fed into the conversation context.
- **Tool use.** Browser control, desktop actions, file read, web search, reminders, notes, and memory.
- **Permission gate.** High-risk tool calls ask for user confirmation before executing.
- **Long-term memory.** Candidate → confirmed pipeline with automatic curation.
- **Plugins & MCP.** Local plugins, MCP servers, and a built-in web-search MCP server.

## Docs

| Doc | Contents |
|---|---|
| [Setup Guide](SETUP.md) | Full install steps, API key config, character packs, updating |
| [macOS Setup](MACOS_SETUP.md) | Apple Silicon/Rosetta, SSL cert fix, GPT-SoVITS on Mac |
| [Technical README](TECHNICAL_README.md) | Runtime architecture, bootstrap, project layout, config reference |
| [Plugin SDK](SAKURA_PLUGIN_SDK.md) | Plugin development |
