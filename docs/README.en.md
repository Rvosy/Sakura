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
| [Setup Guide](SETUP.md) | Full install steps, character packs, TTS setup, updating |
| [API Config Guide](API_CONFIG.md) | Base URL, API key, model selection, relay-provider setup |
| [macOS Setup](MACOS_SETUP.md) | Apple Silicon/Rosetta, SSL cert fix, GPT-SoVITS on Mac |
| [Technical README](TECHNICAL_README.md) | Runtime architecture, bootstrap, project layout, config reference |
| [Plugin SDK](SAKURA_PLUGIN_SDK.md) | Plugin development |

## Acknowledgements and Open Source License Notice

Sakura Desktop Pet is inspired by several open source projects across desktop Agents, desktop companion interactions, and plugin ecosystems. Special thanks to the [Shinsekai](https://github.com/RachelForster/Shinsekai) project and its plugin ecosystem for exploring desktop companions, character interaction, and plugin extensibility, which informed Sakura's compatibility design and feature evolution.

This project is open source under the MIT License. You may freely use, copy, modify, merge, publish, distribute, sublicense, or sell copies of this project's code, provided that you retain this project's copyright notice and MIT License text.

Copyright © 2026 Rvosy

### Third-Party Code and Compatibility Notes

The built-in plugin `plugins/playwright_browser` includes code and modifications based on the following MIT-licensed open source project:

- Project: [`shinsekai-playwright-browser`](https://github.com/RachelForster/shinsekai-playwright-browser)
- License: MIT License
- Copyright: Copyright © 2026 Chihiro

Sakura adapts and modifies this work to provide Playwright browser automation capabilities.

Thanks to all open source project authors and contributors.

## Star History

<a href="https://www.star-history.com/?repos=Rvosy%2Fsakura&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Rvosy/sakura&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Rvosy/sakura&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Rvosy/sakura&type=date&legend=top-left" />
 </picture>
</a>
