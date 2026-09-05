/* Local fixtures only. None of these values are read from Sakura's runtime. */
window.PLUGIN_DEMO = (() => {
  const categories = { model: "模型", voice: "语音", memory: "记忆", tools: "工具", connectivity: "连接", other: "其他" };
  const kinds = { extension: "功能扩展", provider: "能力提供方", infrastructure: "系统组件" };
  // Form fixtures describe the proposed UI, not the real plugins' settings API.
  const speechOptions = [
    { key: "language", label: "输出语言", type: "select", value: "auto", options: [["auto", "自动识别"], ["zh", "中文"], ["ja", "日语"], ["en", "英语"]] },
    { key: "speed", label: "语速", type: "range", value: 1, min: 0.5, max: 2, step: 0.05, unit: "×", wide: true },
  ];
  const voiceAdvanced = { title: "高级设置", advanced: true, fields: [
    { key: "timeout", label: "请求超时（秒）", type: "number", value: 60, min: 5, max: 300 },
    { key: "chunkLength", label: "每段文字上限", type: "number", value: 120, min: 20, max: 500 },
    { key: "streaming", label: "流式输出", type: "checkbox", value: true, hint: "生成一段就播放一段，减少等待。", wide: true },
  ] };
  const configurations = {
    mobile: [
      { title: "连接", fields: [{ key: "address", label: "监听地址", type: "text", value: "127.0.0.1" }, { key: "port", label: "端口", type: "number", value: 8765, min: 1, max: 65535 }] },
      { title: "访问控制", fields: [{ key: "token", label: "访问令牌", type: "password", value: "demo-only-token", wide: true, hint: "示例值，不会开启任何服务。" }, { key: "allowFiles", label: "允许发送文件", type: "checkbox", value: true, wide: true }] },
    ],
    browser: [
      { title: "浏览器", fields: [{ key: "browser", label: "使用的浏览器", type: "select", value: "chromium", options: [["chromium", "Chromium"], ["system", "系统默认浏览器"]], wide: true }, { key: "visible", label: "显示浏览器窗口", type: "checkbox", value: true, wide: true }] },
      { title: "自动化", fields: [{ key: "timeout", label: "操作超时（秒）", type: "number", value: 30, min: 1, max: 300 }, { key: "screenshots", label: "保留任务截图", type: "checkbox", value: false }] },
    ],
    notebook: [
      { title: "笔记", fields: [{ key: "notebook", label: "笔记本名称", type: "text", value: "Sakura 随记", wide: true }, { key: "template", label: "记录模板", type: "textarea", value: "记录重点、待办事项和参考资料。", wide: true }] },
      { title: "记录方式", fields: [{ key: "autoSave", label: "自动整理对话片段", type: "checkbox", value: true, wide: true }] },
    ],
    "sakura.tts.genie": [
      { title: "服务连接", fields: [
        { key: "connection", label: "服务方式", type: "select", value: "managed", options: [["managed", "Sakura 管理的服务"], ["custom", "自定义服务"]], wide: true },
        { key: "url", label: "服务地址", type: "url", value: "http://127.0.0.1:9880", wide: true, when: { key: "connection", value: "custom" } },
      ] },
      { title: "声音与输出", fields: [{ key: "voice", label: "角色音色", type: "select", value: "character", options: [["character", "跟随当前角色"], ["gentle", "温柔 · 示例音色"], ["bright", "明亮 · 示例音色"]] }, ...speechOptions] },
      voiceAdvanced,
    ],
    "sakura.tts.gpt-sovits": [
      { title: "服务连接", fields: [{ key: "url", label: "服务地址", type: "url", value: "http://127.0.0.1:9880", wide: true, hint: "填写正在运行的 GPT-SoVITS 服务地址。" }] },
      { title: "声音与输出", fields: [
        { key: "voice", label: "声音模型", type: "select", value: "character", options: [["character", "跟随当前角色"], ["soft", "轻柔 · 示例模型"], ["clear", "清亮 · 示例模型"]] },
        ...speechOptions,
        { key: "reference", label: "参考音频", type: "select", value: "character", options: [["character", "使用角色参考音频"], ["calm", "平静语气 · 示例音频"], ["cheerful", "轻快语气 · 示例音频"]], wide: true },
      ] },
      voiceAdvanced,
    ],
    model: [
      { title: "模型连接", fields: [
        { key: "url", label: "API 地址", type: "url", value: "https://api.example.com/v1", wide: true },
        { key: "apiKey", label: "API Key", type: "password", value: "", placeholder: "仅使用演示值", optional: true, wide: true },
        { key: "model", label: "模型名称", type: "text", value: "demo-chat", wide: true },
      ] },
      { title: "生成设置", fields: [{ key: "temperature", label: "随机性", type: "range", value: 0.7, min: 0, max: 2, step: 0.1, wide: true }] },
      { title: "高级设置", advanced: true, fields: [{ key: "timeout", label: "请求超时（秒）", type: "number", value: 60, min: 5, max: 300 }, { key: "maxTokens", label: "最大输出长度", type: "number", value: 4096, min: 128, max: 32768, step: 128 }] },
    ],
    "demo.model.local": [
      { title: "本地模型", fields: [{ key: "model", label: "模型", type: "select", value: "small", options: [["small", "轻量模型 · 4B（示例）"], ["medium", "标准模型 · 8B（示例）"]], wide: true }, { key: "quantization", label: "量化精度", type: "select", value: "q4", options: [["q4", "Q4 · 较低内存占用"], ["q8", "Q8 · 较高精度"]], wide: true }] },
      { title: "运行参数", fields: [{ key: "context", label: "上下文长度", type: "select", value: "8192", options: [["4096", "4,096"], ["8192", "8,192"], ["16384", "16,384"]] }, { key: "gpuLayers", label: "GPU 层数", type: "number", value: 24, min: 0, max: 80 }] },
      { title: "高级设置", advanced: true, fields: [{ key: "threads", label: "CPU 线程数", type: "number", value: 8, min: 1, max: 64 }, { key: "unload", label: "闲置卸载（分钟）", type: "number", value: 15, min: 1, max: 120 }] },
    ],
    memory: [
      { title: "记忆与召回", fields: [{ key: "autoCapture", label: "自动记录长期信息", type: "checkbox", value: true, hint: "从对话中保留有用的信息。", wide: true }, { key: "results", label: "每次召回条数", type: "number", value: 5, min: 1, max: 20 }, { key: "scope", label: "记忆范围", type: "select", value: "character", options: [["character", "当前角色"], ["shared", "角色间共享"]] }, { key: "threshold", label: "相关度阈值", type: "range", value: 0.65, min: 0, max: 1, step: 0.05, wide: true }] },
      { title: "高级设置", advanced: true, fields: [{ key: "deduplicate", label: "合并相似记忆", type: "checkbox", value: true, wide: true }] },
    ],
  };
  const page = (sections, description) => ({ title: "插件设置", description, sections });
  const plugin = (id, name, kind, category, description, extra = {}) => ({
    id, name, kind, category, description, author: "Sakura", version: "1.0.0", source: "bundled",
    enabled: true, state: "ready", stateLabel: "运行正常", message: "", dependencies: [],
    hostServices: [], provides: [], capabilities: [], planned: false, sample: false, ...extra,
  });
  const current = [
    plugin("sakura_mobile", "Sakura Mobile", "extension", "connectivity", "在手机上，继续和 Sakura 聊天。", { author: "pa1n9", version: "1.0.0", icon: "phone", capabilities: ["手机网页端", "局域网连接"], hostServices: ["sakura.host.mobile", "sakura.host.artifacts", "sakura.host.settings"], settingsPage: page(configurations.mobile, "管理手机网页端的访问与连接。"), stateLabel: "网页服务运行中" }),
    plugin("sakura.memory.mem0", "Mem0 Memory", "extension", "memory", "记住重要的事，让每次对话有所延续。", { version: "0.1.0", icon: "memory", settingsPage: page(configurations["memory"], "管理长期记忆的记录与召回。"), domain: "memory", capabilities: ["长期记忆", "上下文召回"], hostServices: ["sakura.host.context", "sakura.host.timeline", "sakura.host.storage"], stateLabel: "记忆服务就绪" }),
    plugin("sakura.tts.genie", "Genie", "provider", "voice", "为角色提供自然、流畅的语音。", { version: "0.1.0", icon: "wave", settingsPage: page(configurations["sakura.tts.genie"], "配置服务连接、音色和语音输出。"), domain: "voice", dependencies: ["sakura.tts"], capabilities: ["语音合成", "角色音色"], provides: ["sakura.tts.provider.genie"], hostServices: ["sakura.host.artifacts", "sakura.host.character"], stateLabel: "语音引擎就绪" }),
    plugin("sakura.tts.gpt-sovits", "GPT-SoVITS", "provider", "voice", "用你选择的声音，让角色开口说话。", { version: "0.1.0", icon: "wave", settingsPage: page(configurations["sakura.tts.gpt-sovits"], "配置服务连接、声音模型和参考音频。"), domain: "voice", dependencies: ["sakura.tts"], capabilities: ["语音合成", "声音克隆"], provides: ["sakura.tts.provider.gpt-sovits"], hostServices: ["sakura.host.artifacts", "sakura.host.character", "sakura.host.settings.surface-v0"], state: "error", stateLabel: "启动失败", message: "无法连接语音服务。请打开插件设置检查服务地址，并确认服务已经启动。", reason: "DEMO_SERVICE_UNREACHABLE" }),
    plugin("sakura.tts", "语音运行时", "infrastructure", "voice", "连接语音引擎，为角色统一调度语音能力。", { version: "0.1.0", icon: "layers", displayAlias: "Sakura TTS Hub", capabilities: ["语音引擎注册", "语音任务调度"], provides: ["sakura.tts"], hostServices: ["sakura.host.character"] }),
  ];
  const upcoming = [
    plugin("playwright_browser", "Playwright Browser", "extension", "tools", "打开网页、阅读内容，帮助完成浏览器任务。", { author: "Chihiro", source: "user", icon: "globe", enabled: false, capabilities: ["网页浏览", "页面截图"], hostServices: ["sakura.host.tools", "sakura.host.artifacts"], settingsPage: page(configurations.browser, "选择浏览器，调整自动化行为。") }),
    plugin("demo.model.local", "Sakura Local", "provider", "model", "在本机运行模型，让对话留在你的设备上。", { icon: "chip", planned: true, settingsPage: page(configurations["demo.model.local"], "选择本地模型并调整运行参数。"), domain: "model", state: "warning", stateLabel: "模型未下载", message: "插件已启用。下载一个模型后，就可以在本机开始对话。", dependencies: ["demo.model.runtime"], capabilities: ["本地推理", "离线对话"], provides: ["demo.model.provider.local"] }),
    plugin("demo.model.remote", "Remote API", "provider", "model", "连接兼容 API 的模型服务，自由选择对话模型。", { icon: "cloud", planned: true, settingsPage: page(configurations["model"], "配置模型服务连接和生成参数。"), domain: "model", dependencies: ["demo.model.runtime"], capabilities: ["远程推理", "自定义服务"], provides: ["demo.model.provider.remote"], stateLabel: "连接就绪" }),
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
  return Object.freeze({ categories, kinds, notebookPage: page(configurations.notebook, "设置笔记保存位置和记录方式。"), createScenario: (name) => structuredClone(name === "current" ? current : name === "near" ? [...current, ...upcoming] : [...current, ...upcoming, ...ecosystem]) });
})();
