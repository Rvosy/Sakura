/* Local fixtures only. None of these values are read from Sakura's runtime. */
window.PLUGIN_DEMO = (() => {
  const categories = { model: "模型", voice: "语音", memory: "记忆", tools: "工具", connectivity: "连接", other: "其他" };
  const kinds = { extension: "功能扩展", provider: "能力提供方", infrastructure: "系统组件" };
  // Static copies of existing settings declarations; layout metadata does not define product behavior.
  const sections = (title, fields, actions = []) => [
    { title, fields: fields.filter((field) => field.placement !== "advanced"), actions },
    ...(fields.some((field) => field.placement === "advanced") ? [{ title: "高级设置", advanced: true, fields: fields.filter((field) => field.placement === "advanced") }] : []),
  ];
  const configurations = {
    // plugins/builtin/sakura_mobile/plugin.py
    "mobile": [
      ...sections("手机端", [
      {"key": "enabled", "label": "启用手机网页端", "type": "boolean", "default": false, "wide": true},
      {"key": "host", "label": "监听地址", "type": "string", "default": "0.0.0.0", "required": true, "maxLength": 255, "wide": true},
      {"key": "port", "label": "端口", "type": "integer", "default": 8765, "minimum": 1, "maximum": 65535, "wide": false},
      {"key": "token", "label": "访问 token", "type": "password", "default": "sakura", "required": true, "copyable": true, "maxLength": 512, "wide": true},
      {"key": "running", "label": "运行状态", "type": "readonly", "default": "未启动", "wide": true},
      {"key": "local_url", "label": "本机链接", "type": "readonly", "default": "", "copyable": true, "wide": true},
      {"key": "lan_urls", "label": "内网链接", "type": "readonly", "default": "未发现内网地址", "copyable": true, "wide": true},
      {"key": "error", "label": "错误", "type": "readonly", "default": "", "wide": true},
      ], [{ actionId: "refresh_status", label: "刷新状态" }]),
    ],
    // plugins/optional/playwright_browser/plugin.py
    "browser": [
      ...sections("Playwright 浏览器", [
      {"key": "headless", "label": "无头模式", "type": "boolean", "default": false, "description": "无头模式（Headless）", "restartRequired": true, "wide": true},
      ]),
    ],
    // plugins/builtin/sakura_mem0/plugin.py
    "memory": [
      ...sections("长期记忆", [
      {"key": "status", "label": "运行状态", "type": "status", "placement": "section_header", "default": {"state": "neutral", "label": "状态未知", "message": ""}, "description": "记忆故障不会阻断普通聊天。", "wide": true},
      {"key": "triggerTurns", "label": "自动整理间隔", "type": "integer", "default": 8, "minimum": 1, "maximum": 50, "step": 1, "description": "完成多少轮对话后尝试整理一次长期记忆。", "wide": false},
      ]),
    ],
    // plugins/builtin/sakura_genie/plugin.py
    "sakura.tts.genie": [
      { title: "本地运行组件", fields: [{"key": "bundleResource", "label": "Genie TTS 本地运行组件", "type": "resource", "subtitle": "Genie TTS CPU 整合包", "value": {"state": "succeeded", "progress": 100}, "notRequiredWhen": {"key": "endpointMode", "value": "custom"}, "wide": true}] },
      ...sections("Genie TTS 语音服务", [
      {"key": "endpointMode", "label": "服务来源", "type": "select", "default": "managed", "description": "内置服务由 Sakura 启动和停止；已有服务只负责连接。", "options": [{"label": "Sakura 内置（推荐）", "value": "managed"}, {"label": "连接已有服务", "value": "custom"}], "wide": true},
      {"key": "apiUrl", "label": "已有服务地址", "type": "string", "default": "http://127.0.0.1:9881/", "description": "仅在连接已有服务时使用。", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "timeoutSeconds", "label": "合成超时", "type": "integer", "default": 60, "minimum": 1, "maximum": 300, "step": 1, "description": "等待一次语音合成完成的最长时间（秒）。", "placement": "advanced", "wide": false},
      ]),
    ],
    // plugins/builtin/sakura_gpt_sovits/plugin.py
    "sakura.tts.gpt-sovits": [
      { title: "本地运行组件", fields: [{"key": "bundleResource", "label": "GPT-SoVITS 本地运行组件", "type": "resource", "subtitle": "GPT-SoVITS v2pro 通用整合包 · 示例推荐", "value": {"state": "idle", "progress": 0}, "notRequiredWhen": {"key": "endpointMode", "value": "custom"}, "wide": true}] },
      ...sections("GPT-SoVITS 语音服务", [
      {"key": "endpointMode", "label": "服务来源", "type": "select", "default": "managed", "description": "内置服务由 Sakura 启动和停止；已有服务只负责连接。", "options": [{"label": "Sakura 内置（推荐）", "value": "managed"}, {"label": "连接已有服务", "value": "custom"}], "wide": true},
      {"key": "customBaseUrl", "label": "已有服务地址", "type": "string", "default": "", "description": "仅在连接已有服务时使用，例如 http://127.0.0.1:9880。", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "ttsPath", "label": "接口路径", "type": "string", "default": "/tts", "description": "已有服务的语音合成接口路径。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "remoteReferenceRoot", "label": "远程参考音频目录", "type": "string", "default": "", "description": "服务位于其他设备时，用于映射角色参考音频。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "workDir", "label": "内置服务工作目录", "type": "string", "default": "", "description": "Sakura 内置 GPT-SoVITS 的程序目录。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "pythonPath", "label": "Python 解释器", "type": "string", "default": "", "description": "留空时从内置运行环境自动查找。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "ttsConfigPath", "label": "推理配置", "type": "string", "default": "", "description": "可选的 GPT-SoVITS 推理配置文件。", "placement": "advanced", "enabledWhen": {"field": "endpointMode", "equals": "custom"}, "wide": true},
      {"key": "timeoutSeconds", "label": "合成超时", "type": "integer", "default": 60, "minimum": 1, "maximum": 300, "step": 1, "description": "等待一次语音合成完成的最长时间（秒）。", "placement": "advanced", "wide": false},
      ]),
    ],
  };
  const page = (sections, description) => ({ title: "插件设置", description, sections });
  const plugin = (id, name, kind, category, description, extra = {}) => ({
    id, name, kind, category, description, author: "Sakura", version: "1.0.0", source: "bundled",
    enabled: true, state: "ready", stateLabel: "运行正常", message: "", dependencies: [],
    hostServices: [], provides: [], capabilities: [], planned: false, sample: false, ...extra,
  });
  const current = [
    plugin("sakura_mobile", "Sakura Mobile", "extension", "connectivity", "在手机上，继续和 Sakura 聊天。", { author: "pa1n9", version: "1.0.0", icon: "phone", capabilities: ["手机网页端", "局域网连接"], hostServices: ["sakura.host.mobile", "sakura.host.artifacts", "sakura.host.settings"], settingsPage: page(configurations.mobile, "管理手机网页端的访问与连接。"), stateLabel: "网页服务未启动" }),
    plugin("sakura.memory.mem0", "Mem0 Memory", "extension", "memory", "记住重要的事，让每次对话有所延续。", { version: "0.1.0", icon: "memory", settingsPage: page(configurations["memory"], "调整长期记忆的自动整理间隔。"), domain: "memory", capabilities: ["长期记忆", "上下文召回"], hostServices: ["sakura.host.context", "sakura.host.timeline", "sakura.host.storage"], stateLabel: "记忆服务就绪" }),
    plugin("sakura.tts.genie", "Genie", "provider", "voice", "为角色提供自然、流畅的语音。", { version: "0.1.0", icon: "wave", settingsPage: page(configurations["sakura.tts.genie"], "配置 Genie TTS 语音服务。"), domain: "voice", dependencies: ["sakura.tts"], capabilities: ["语音合成", "角色音色"], provides: ["sakura.tts.provider.genie"], hostServices: ["sakura.host.artifacts", "sakura.host.character"], stateLabel: "语音引擎就绪" }),
    plugin("sakura.tts.gpt-sovits", "GPT-SoVITS", "provider", "voice", "用你选择的声音，让角色开口说话。", { version: "0.1.0", icon: "wave", settingsPage: page(configurations["sakura.tts.gpt-sovits"], "配置 GPT-SoVITS 语音服务。"), domain: "voice", dependencies: ["sakura.tts"], capabilities: ["语音合成", "声音克隆"], provides: ["sakura.tts.provider.gpt-sovits"], hostServices: ["sakura.host.artifacts", "sakura.host.character", "sakura.host.settings.surface-v0"], state: "error", stateLabel: "启动失败", message: "无法连接语音服务。请打开插件设置检查服务地址，并确认服务已经启动。", reason: "DEMO_SERVICE_UNREACHABLE" }),
    plugin("sakura.tts", "语音运行时", "infrastructure", "voice", "连接语音引擎，为角色统一调度语音能力。", { version: "0.1.0", icon: "layers", displayAlias: "Sakura TTS Hub", capabilities: ["语音引擎注册", "语音任务调度"], provides: ["sakura.tts"], hostServices: ["sakura.host.character"] }),
  ];
  const upcoming = [
    plugin("playwright_browser", "Playwright Browser", "extension", "tools", "打开网页、阅读内容，帮助完成浏览器任务。", { author: "Chihiro", source: "user", icon: "globe", enabled: false, capabilities: ["网页浏览", "页面截图"], hostServices: ["sakura.host.tools", "sakura.host.artifacts"], settingsPage: page(configurations.browser, "调整 Playwright 浏览器的无头模式。") }),
    plugin("demo.model.local", "Sakura Local", "provider", "model", "在本机运行模型，让对话留在你的设备上。", { icon: "chip", planned: true, domain: "model", state: "warning", stateLabel: "模型未下载", message: "插件已启用。下载一个模型后，就可以在本机开始对话。", dependencies: ["demo.model.runtime"], capabilities: ["本地推理", "离线对话"], provides: ["demo.model.provider.local"] }),
    plugin("demo.model.remote", "Remote API", "provider", "model", "连接兼容 API 的模型服务，自由选择对话模型。", { icon: "cloud", planned: true, domain: "model", dependencies: ["demo.model.runtime"], capabilities: ["远程推理", "自定义服务"], provides: ["demo.model.provider.remote"], stateLabel: "连接就绪" }),
    plugin("demo.model.runtime", "模型运行时", "infrastructure", "model", "连接模型提供方，为对话提供统一的模型入口。", { icon: "layers", planned: true, displayAlias: "Sakura Model Runtime", capabilities: ["模型提供方注册", "模型请求路由"], provides: ["demo.model.runtime"] }),
  ];
  const ecosystem = [
    ["ollama", "Ollama", "provider", "model", "连接 Ollama，使用本地已安装的模型。", { dependencies: ["demo.model.runtime"], state: "working", stateLabel: "正在加载模型", message: "正在将模型载入内存，完成后即可使用。" }],
    ["lmstudio", "LM Studio", "provider", "model", "使用 LM Studio 管理和运行的模型。", { dependencies: ["demo.model.runtime"], enabled: false }],
    ["anthropic", "Claude API", "provider", "model", "通过 Anthropic API 使用 Claude 模型。", { dependencies: ["demo.model.runtime"] }],
    ["gemini", "Gemini API", "provider", "model", "连接 Gemini 的文本与多模态能力。", { dependencies: ["demo.model.runtime"], state: "warning", stateLabel: "需要配置", message: "尚未填写 API Key。请前往模型设置完成配置。" }],
    ["vision", "Local Vision · 多模态视觉理解模型提供方", "provider", "model", "在本地理解图像，并为对话补充视觉信息。", { dependencies: ["demo.model.runtime"], state: "error", stateLabel: "显存不足", message: "当前可用显存不足，请选择更小的模型或减少 GPU 层数。" }],
    ["embedding", "Local Embeddings", "provider", "model", "将文字转换为可检索的向量。", { dependencies: ["demo.model.runtime"] }],
    ["piper", "Piper TTS", "provider", "voice", "轻量的本地语音合成引擎。", { dependencies: ["sakura.tts"], enabled: false }],
    ["edge", "Edge TTS", "provider", "voice", "提供多语言在线合成音色。", { dependencies: ["sakura.tts"] }],
    ["kokoro", "Kokoro", "provider", "voice", "使用紧凑的模型生成自然语音。", { dependencies: ["sakura.tts"] }],
    ["whisper", "Whisper", "provider", "voice", "在本地将语音转换为文字。", { capabilities: ["语音识别", "本地转写"] }],
    ["notes", "Obsidian Notes", "extension", "memory", "让笔记中的知识参与对话。", { author: "Mika", capabilities: ["笔记检索", "知识引用"] }],
    ["journal", "Conversation Journal", "extension", "memory", "按主题整理对话中的重要片段。", { author: "Haru", enabled: false }],
    ["recall", "Semantic Recall", "extension", "memory", "按含义检索过去的记忆。", { dependencies: ["demo.embedding"], state: "warning", stateLabel: "索引待建立", message: "首次使用前，需要在记忆设置中建立索引。" }],
    ["search", "Web Search", "extension", "tools", "搜索网页，为回答提供最新参考。", { author: "Aster" }],
    ["files", "Workspace Files", "extension", "tools", "在指定工作目录中查找和阅读文件。", { enabled: false }],
    ["calendar", "Calendar Companion", "extension", "tools", "查看日程，帮助安排接下来的一天。", { author: "Mika", dependencies: ["demo.automation"] }],
    ["discord", "Discord Bridge", "extension", "connectivity", "从 Discord 与 Sakura 保持联系。", { author: "Yuki", dependencies: ["demo.connector"] }],
    ["telegram", "Telegram Bridge", "extension", "connectivity", "通过 Telegram 发送消息与文件。", { author: "Haru", dependencies: ["demo.connector"] }],
    ["hotkeys", "Quick Actions", "extension", "other", "用快捷键唤起常用操作。", { author: "Aster" }],
    ["connector", "连接运行时", "infrastructure", "connectivity", "为消息桥接插件管理连接与消息分发。", { state: "error", stateLabel: "连接失败", message: "演示连接已断开。依赖此组件的消息桥接暂时无法使用。", provides: ["demo.connector"] }],
    ["automation", "任务运行时", "infrastructure", "tools", "为工具扩展提供后台任务调度。", { provides: ["demo.automation"] }],
  ].map(([id, name, kind, category, description, extra]) => plugin(`demo.${id}`, name, kind, category, description, {
    source: "user", author: "Community Lab", sample: true,
    icon: kind === "infrastructure" ? "layers" : ({ model: "chip", voice: "wave", memory: "memory", tools: "tool", connectivity: "globe", other: "sparkles" }[category]),
    domain: ["model", "voice", "memory"].includes(category) ? category : null,
    capabilities: [categories[category] + "扩展"], ...extra,
  }));
  return Object.freeze({ categories, kinds, createScenario: (name) => structuredClone(name === "current" ? current : name === "near" ? [...current, ...upcoming] : [...current, ...upcoming, ...ecosystem]) });
})();
