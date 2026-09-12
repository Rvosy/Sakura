// Fictional, local-only data shaped for the production settings clients.
export const clone = (x) => structuredClone(x);
export const theme = {
  primary: "#4b9ac4",
  primaryHover: "#3b83aa",
  accent: "#e36c96",
  text: "#27445a",
  secondaryText: "#54768b",
  mutedText: "#7d99a9",
  pageBackground: "#f8fcfe",
  panelBackground: "#eaf5fa",
  inputBackground: "#ffffff",
  bubbleBackground: "#e3f1f7",
  border: "#accfde",
};
export function field(key, label, value, type = "string", extra = {}) {
  return {
    key,
    label,
    type,
    default: value,
    description: "",
    options: [],
    minimum: null,
    maximum: null,
    step: null,
    maxLength: type === "string" ? 1000 : null,
    placement: "row",
    actionIds: [],
    enabledWhen: null,
    required: false,
    readonly: ["readonly", "status", "resource"].includes(type),
    copyable: false,
    restartRequired: false,
    value,
    ...extra,
  };
}
export function section(sectionId, title, fields = [], surface = null) {
  return {
    sectionId,
    title,
    surface,
    reasonCode: "READY",
    fields,
    values: Object.fromEntries(fields.map((f) => [f.key, f.value])),
    actions: [],
    collections: [],
  };
}
export function plugin(
  pluginId,
  name,
  category,
  icon,
  sections = [],
  enabled = true,
  kind = "provider",
) {
  return {
    installId:
      "pi_bundled_" +
      Array.from(new TextEncoder().encode(pluginId), (n) =>
        n.toString(16).padStart(2, "0"),
      ).join(""),
    pluginId,
    name,
    version: "1.0.0",
    author: "Sakura",
    description: "",
    enabled,
    required: false,
    supported: true,
    source: "bundled",
    canUninstall: false,
    provides: [pluginId + ".service"],
    requires: [],
    missingServices: [],
    state: enabled ? "active" : "disabled",
    reasonCode: enabled ? "ACTIVE" : "DISABLED",
    sections,
    presentation: { kind, category, icon },
  };
}
export function visualPlugin(type) {
  const is3d = type === "vrm",
    name = is3d ? "3D 模型" : "Live2D";
  const p = plugin(
    is3d ? "example.vrm" : "example.live2d",
    name,
    "other",
    is3d ? "box" : "person-standing",
    [
      section("display", name + "设置", [
        field(
          is3d ? "shadows" : "idle",
          is3d ? "显示阴影" : "待机动画",
          true,
          "boolean",
        ),
      ]),
    ],
  );
  return {
    ...p,
    source: "user",
    canUninstall: true,
    installId: p.installId.replace("pi_bundled_", "pi_user_"),
    author: "示例作者",
    description: "用于设置评审的模拟插件。",
  };
}
function action(actionId, label, description = "") {
  return { actionId, label, description, danger: false };
}
function resourceSection(sectionId, title, key, label, surface, actions) {
  const ready = {
    applicability: "required",
    subtitle: "",
    ready: true,
    taskState: "idle",
    message: "",
    detail: "",
    progress: null,
    availableActionIds: [],
  };
  const s = section(
    sectionId,
    title,
    [
      field(key, label, ready, "resource", {
        actionIds: actions.map((a) => a.actionId),
      }),
    ],
    surface,
  );
  s.actions = actions;
  return s;
}
function bundle(title) {
  return resourceSection(
    "aboutBundle",
    title,
    "bundleResource",
    title + " 本地运行组件",
    "plugin",
    [
      action("installBundle", "安装", "下载并安装推荐组件。"),
      action("retryBundle", "重试", "重新尝试安装推荐组件。"),
      action(
        "cancelBundle",
        "取消",
        "取消下载，已下载的部分会保留，下次可以继续。",
      ),
    ],
  );
}
export function createState(scenario = "portrait") {
  const portraits = [
    {
      id: "portrait-default",
      label: "立绘",
      pluginId: "sakura.visual.portrait",
      type: "portrait",
    },
  ];
  const extra = [
    {
      id: "portrait-winter",
      label: "立绘 · 冬装",
      pluginId: "sakura.visual.portrait",
      type: "portrait",
    },
    {
      id: "live2d-main",
      label: "Live2D",
      pluginId: "example.live2d",
      type: "live2d",
    },
    { id: "vrm-main", label: "3D 模型", pluginId: "example.vrm", type: "vrm" },
  ];
  const characters = [
    {
      id: "navi",
      name: "N.A.V.I.",
      visuals: [...portraits, ...(scenario === "portrait" ? [] : extra)],
      defaultVisual: "portrait-default",
      hasVoice: true,
    },
    {
      id: "sakura",
      name: "樱",
      visuals: clone(portraits),
      defaultVisual: "portrait-default",
      hasVoice: true,
    },
    {
      id: "mio",
      name: "澪",
      visuals:
        scenario === "portrait"
          ? clone(portraits)
          : [clone(extra[1]), clone(portraits[0])],
      defaultVisual:
        scenario === "portrait" ? "portrait-default" : "live2d-main",
      hasVoice: false,
    },
  ];
  // Keep these descriptors aligned with the bundled plugins at 07285a99.
  // Values are fictional; no provider module or local configuration is loaded.
  const custom = { enabledWhen: { field: "endpointMode", equals: "custom" } };
  const advanced = { placement: "advanced", ...custom };
  const endpoint = () =>
    field("endpointMode", "服务来源", "managed", "select", {
      options: [
        { value: "managed", label: "Sakura 内置" },
        { value: "custom", label: "连接已有服务" },
      ],
    });
  const timeout = () =>
    field("timeoutSeconds", "合成超时（秒）", 60, "integer", {
      minimum: 1,
      maximum: 300,
      step: 1,
      placement: "advanced",
    });
  const gpt = section(
    "runtime",
    "GPT-SoVITS 语音服务",
    [
      endpoint(),
      field("customBaseUrl", "已有服务地址", "", "string", {
        description: "例如 http://127.0.0.1:9880",
        ...custom,
      }),
      field("ttsPath", "接口路径", "/tts", "string", advanced),
      field("remoteReferenceRoot", "远程参考音频目录", "", "string", {
        description: "服务位于其他设备时，用于映射角色参考音频。",
        ...advanced,
      }),
      field("workDir", "内置服务工作目录", "", "string", advanced),
      field("pythonPath", "Python 解释器", "", "string", {
        description: "留空时从内置运行环境自动查找。",
        ...advanced,
      }),
      field("ttsConfigPath", "推理配置文件（可选）", "", "string", advanced),
      timeout(),
    ],
    "voice",
  );
  const genie = section(
    "runtime",
    "Genie TTS 语音服务",
    [
      endpoint(),
      field(
        "apiUrl",
        "已有服务地址",
        "http://127.0.0.1:9881/",
        "string",
        custom,
      ),
      timeout(),
    ],
    "voice",
  );
  const layerOptions = [
    ["core_profile", "核心档案"],
    ["semantic", "语义记忆"],
    ["episodic", "情景记忆"],
    ["procedural", "程序记忆"],
    ["session", "会话记忆"],
  ].map(([value, label]) => ({ value, label }));
  const memory = section("memory_management", "记忆管理", [], "memory");
  const collectionFields = [
    field("content", "内容", null, "string", {
      required: true,
      maxLength: 16384,
    }),
    field("layer", "分层", "semantic", "select", {
      required: true,
      options: layerOptions,
    }),
    field("category", "类别", ""),
    field("source", "来源", "explicit"),
    field("importance", "重要度", 0.5, "number", {
      minimum: 0,
      maximum: 1,
      step: 0.05,
    }),
    field("confidence", "置信度", 0.8, "number", {
      minimum: 0,
      maximum: 1,
      step: 0.05,
    }),
  ];
  memory.collections = [
    {
      collectionId: "memories",
      title: "记忆条目",
      description: "当前角色的长期记忆。",
      columns: [
        ["content", "内容", "string"],
        ["layer", "分层", "string"],
        ["category", "类别", "string"],
        ["source", "来源", "string"],
        ["importance", "重要度", "number"],
        ["confidence", "置信度", "number"],
        ["updatedAt", "更新时间", "datetime"],
      ].map(([key, label, type]) => ({
        key,
        label,
        type,
        maxLength: key === "content" ? 16384 : null,
      })),
      fields: collectionFields.map(({ value, ...f }) => f),
      filters: [{ key: "layer", label: "分层", options: layerOptions }],
      searchable: true,
      pageSize: 25,
      canCreate: true,
      canUpdate: true,
      canDelete: true,
      deleteConfirmation: "确定删除这条长期记忆吗？此操作不能撤销。",
    },
  ];
  const memorySettings = section("memory", "长期记忆", [
    field(
      "status",
      "运行状态",
      { state: "ready", label: "可用", message: "" },
      "status",
      { placement: "section_header" },
    ),
    field("triggerTurns", "自动整理间隔（轮）", 8, "integer", {
      minimum: 1,
      maximum: 50,
      step: 1,
    }),
  ]);
  const memoryResource = resourceSection(
    "memory_embedding_component",
    "Mem0 长期记忆",
    "embeddingResource",
    "本地向量模型",
    "about",
    [
      action("downloadEmbedding", "下载本地模型"),
      action("retryEmbedding", "重试"),
      action("cancelEmbedding", "取消下载"),
    ],
  );
  const recognition = section(
    "recognition",
    "识别",
    [
      field("language", "识别语言", "auto", "select", {
        options: [
          ["auto", "自动检测"],
          ["zh", "普通话"],
          ["yue", "粤语"],
          ["en", "英语"],
          ["ja", "日语"],
          ["ko", "韩语"],
        ].map(([value, label]) => ({ value, label })),
      }),
    ],
    "voice-input",
  );
  const asrResource = resourceSection(
    "models",
    "SenseVoice",
    "models",
    "本地识别模型",
    "voice-input",
    [
      action("installModels", "安装"),
      action("retryModels", "重试"),
      action("cancelModels", "取消"),
    ],
  );
  const mobile = section("sakura_mobile", "手机端", [
    field("enabled", "启用手机网页端", false, "boolean"),
    field("host", "监听地址", "0.0.0.0", "string", {
      required: true,
      maxLength: 255,
    }),
    field("port", "端口", 8765, "integer", { minimum: 1, maximum: 65535 }),
    field("token", "访问 token", "demo-token", "password", {
      required: true,
      copyable: true,
      maxLength: 512,
    }),
    field("running", "运行状态", "未启动", "readonly"),
    field("local_url", "本机链接", "", "readonly", { copyable: true }),
    field("lan_urls", "内网链接", "未发现内网地址", "readonly", {
      copyable: true,
    }),
    field("error", "错误", "", "readonly"),
  ]);
  mobile.actions = [action("refresh_status", "刷新状态")];
  const plugins = [
    plugin("sakura.visual.portrait", "立绘", "other", "image", [
      section("display", "立绘设置", [
        field("transition", "切换效果", "fade", "select", {
          options: [
            { value: "fade", label: "淡入淡出" },
            { value: "instant", label: "直接切换" },
          ],
        }),
        field("duration", "过渡时长", 180, "integer", {
          minimum: 0,
          maximum: 800,
          step: 10,
        }),
      ]),
    ]),
    plugin(
      "sakura.tts.gpt-sovits",
      "GPT-SoVITS Provider",
      "voice",
      "audio-lines",
      [gpt, bundle("GPT-SoVITS")],
    ),
    plugin("sakura.tts.genie", "Genie TTS Provider", "voice", "audio-lines", [
      genie,
      bundle("Genie TTS"),
    ]),
    plugin(
      "sakura.memory.mem0",
      "Sakura Mem0 Memory",
      "memory",
      "brain",
      [memorySettings, memoryResource, memory],
      true,
      "extension",
    ),
    plugin(
      "sakura.asr.sensevoice",
      "SenseVoice ASR Provider",
      "voice",
      "mic",
      [recognition, asrResource],
      false,
    ),
    plugin(
      "sakura_mobile",
      "Sakura Mobile",
      "connectivity",
      "smartphone",
      [mobile],
      false,
      "extension",
    ),
    plugin(
      "sakura.tts",
      "Sakura TTS Hub",
      "voice",
      "layers",
      [],
      true,
      "infrastructure",
    ),
    plugin(
      "sakura.asr",
      "Sakura ASR Hub",
      "voice",
      "layers",
      [],
      false,
      "infrastructure",
    ),
  ];
  const metadata = [
    {
      id: "sakura.tts.gpt-sovits",
      version: "0.1.0",
      author: "Sakura",
      description: "让角色使用 GPT-SoVITS 说话。",
      provides: ["sakura.tts.provider.gpt-sovits"],
      requires: [
        "sakura.tts",
        "sakura.host.artifacts",
        "sakura.host.character",
        "sakura.host.settings",
        "sakura.host.settings.surface-v0",
      ],
    },
    {
      id: "sakura.tts.genie",
      version: "0.1.0",
      author: "Sakura",
      description: "使用 Genie 合成角色语音，支持外部服务。",
      provides: ["sakura.tts.provider.genie"],
      requires: [
        "sakura.tts",
        "sakura.host.artifacts",
        "sakura.host.character",
        "sakura.host.diagnostics",
        "sakura.host.settings",
        "sakura.host.settings.surface-v0",
      ],
    },
    {
      id: "sakura.memory.mem0",
      version: "0.1.0",
      author: "Sakura",
      description: "让 Sakura 记住长期信息，并在需要时回想起来。",
      provides: [],
      requires: [
        "sakura.host.storage",
        "sakura.host.character",
        "sakura.host.timeline",
        "sakura.host.context",
        "sakura.host.tools",
        "sakura.host.settings",
        "sakura.host.settings.collection-v0",
        "sakura.host.settings.surface-v0",
        "sakura.host.model_slots",
      ],
    },
    {
      id: "sakura.asr.sensevoice",
      version: "0.1.0",
      author: "Sakura",
      description: "使用 SenseVoice 在本机识别语音。",
      provides: ["sakura.asr.provider.sensevoice"],
      requires: [
        "sakura.asr",
        "sakura.host.audio_input",
        "sakura.host.settings",
        "sakura.host.settings.surface-v0",
      ],
    },
    {
      id: "sakura_mobile",
      version: "1.0.0",
      author: "pa1n9",
      description: "在局域网或 Tailscale 中，用手机网页和 Sakura 聊天。",
      provides: [],
      requires: [
        "sakura.host.mobile",
        "sakura.host.artifacts",
        "sakura.host.settings",
      ],
    },
    {
      id: "sakura.tts",
      version: "0.1.0",
      author: "Sakura",
      description: "为角色选择语音输出引擎。",
      provides: ["sakura.tts"],
      requires: ["sakura.host.character"],
    },
    {
      id: "sakura.asr",
      version: "0.1.0",
      author: "Sakura",
      description: "选择语音输入引擎。",
      provides: ["sakura.asr"],
      requires: ["sakura.host.audio_input"],
    },
  ];
  for (const { id, ...meta } of metadata)
    Object.assign(
      plugins.find((p) => p.pluginId === id),
      meta,
    );
  if (scenario === "multiple")
    plugins.push(visualPlugin("live2d"), visualPlugin("vrm"));
  if (scenario === "missing") characters[0].defaultVisual = "live2d-main";
  return {
    scenario,
    characters,
    current: "navi",
    generation: 1,
    revision: 1,
    visuals: {},
    plugins,
    appearance: {
      portraitScalePercent: 100,
      controlPanelWidth: 640,
      bubbleMaxHeight: 160,
      bubbleAutoExpand: true,
      controlPanelVerticalOffset: 0,
      inputBarOffset: 0,
      speechFontSize: 19,
      nameFontSize: 13,
      inputFontSize: 15,
      visualEffectMode: "gaussian_blur",
      themeTokens: clone(theme),
    },
    provider: {
      schema_version: 1,
      window_generation: 1,
      core_generation_id: "demo-1",
      providers: [
        {
          id: "deepseek",
          alias: "DeepSeek",
          base_url: "https://api.deepseek.com/v1",
          configured: true,
          models: ["deepseek-chat", "deepseek-reasoner"],
        },
        {
          id: "local",
          alias: "本地模型",
          base_url: "http://localhost:11434/v1",
          configured: true,
          models: ["qwen3:8b", "qwen2.5-vl:7b"],
        },
      ],
      model_slots: [
        {
          identity: "plugin:sakura.memory.mem0:curation",
          ownerType: "plugin",
          ownerId: "sakura.memory.mem0",
          slotId: "curation",
          label: "记忆整理模型",
          description: "继承时使用对话模型。",
          modelKind: "chat_completion",
          required: false,
          order: 30,
          reasonCode: "READY",
          selection: { profile_id: "", model: "" },
        },
        {
          identity: "core:chat",
          ownerType: "core",
          ownerId: "sakura.core",
          slotId: "chat",
          label: "对话模型",
          description: "",
          modelKind: "chat_completion",
          required: true,
          order: 10,
          reasonCode: "READY",
          selection: {
            profile_id: "deepseek",
            model: "deepseek-chat",
            context_window_tokens: 131072,
          },
        },
        {
          identity: "core:vision_chat",
          ownerType: "core",
          ownerId: "sakura.core",
          slotId: "vision_chat",
          label: "视觉模型",
          description: "",
          modelKind: "chat_completion",
          required: false,
          order: 20,
          reasonCode: "READY",
          selection: { profile_id: "local", model: "qwen2.5-vl:7b" },
        },
      ],
      settings: {
        timeout_seconds: 60,
        temperature: null,
        top_p: null,
        max_tokens: null,
      },
      setup_complete: true,
      change_plans: ["applied"],
    },
    voices: {
      navi: { enabled: true, providerId: "sakura.tts.gpt-sovits" },
      sakura: { enabled: true, providerId: "sakura.tts.genie" },
      mio: { enabled: false, providerId: null },
    },
    asr: { inputDeviceId: "" },
    timing: { subtitleTypingIntervalMs: 35, replySegmentPauseMs: 300 },
    bubble: { autoHideEnabled: false, autoHideDelaySeconds: 15 },
    screen: {
      enabled: false,
      checkIntervalMinutes: 20,
      cooldownMinutes: 10,
      batchLimit: 3,
      resolution: "1080p",
    },
    limits: {
      maxAgentStepsPerTurn: 6,
      maxToolCallsPerStep: 3,
      maxToolCallsPerTurn: 12,
    },
    launchAtLogin: false,
    telemetry: true,
    autoCheck: true,
    ttsRoot: "默认语音目录",
    memories: {
      navi: [
        {
          itemId: "m1",
          values: {
            content: "喜欢轻松的聊天方式。",
            layer: "semantic",
            category: "偏好",
            source: "explicit",
            importance: 0.7,
            confidence: 0.9,
            updatedAt: "2026-09-10T01:30:00Z",
          },
        },
        {
          itemId: "m2",
          values: {
            content: "正在学习日语。",
            layer: "semantic",
            category: "学习",
            source: "explicit",
            importance: 0.5,
            confidence: 0.8,
            updatedAt: "2026-09-09T06:20:00Z",
          },
        },
      ],
      sakura: [],
      mio: [],
    },
  };
}
export const appearanceLimits = {
  portraitScalePercent: [50, 150, 100],
  controlPanelWidth: [420, 860, 640],
  bubbleMaxHeight: [96, 400, 160],
  controlPanelVerticalOffset: [-400, 400, 0],
  inputBarOffset: [0, 400, 0],
  speechFontSize: [10, 24, 19],
  nameFontSize: [10, 20, 13],
  inputFontSize: [12, 20, 15],
};
export function capabilityManifest() {
  const groups = {
    character: ["character.manage", "character.appearance"],
    appearance: [
      "appearance.theme",
      "appearance.input_visual_effect.gaussian_blur",
      "appearance.input_visual_effect.liquid_glass",
    ],
    providers: ["providers.manage"],
    model: ["model.chat_slot"],
    voice: ["voice.tts"],
    memory: ["memory.manage"],
    interaction: [
      "chat.presentation_timing",
      "chat.bubble_auto_hide",
      "privacy.screen_awareness",
    ],
    tools: ["tools.runtime_limits"],
    plugins: ["plugins.manage"],
    system: [
      "storage.tts_root",
      "system.launch_at_login",
      "telemetry.anonymous_statistics",
      "storage.legacy_role_data_import",
    ],
    "open-help": ["system.macos_open_help"],
    about: ["updates.manage"],
  };
  return {
    schemaVersion: 1,
    windowGeneration: 1,
    sections: Object.fromEntries(
      Object.entries(groups).map(([k, v]) => [
        k,
        {
          status: "available",
          features: Object.fromEntries(v.map((f) => [f, "available"])),
        },
      ]),
    ),
    unavailableReasons: {},
  };
}
