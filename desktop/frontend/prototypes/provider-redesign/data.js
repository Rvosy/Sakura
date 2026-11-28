export const THEMES = Object.freeze({
  light: Object.freeze({
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
  }),
  dark: Object.freeze({
    primary: "#8dc8e9",
    primaryHover: "#aedcf4",
    accent: "#ee9ab7",
    text: "#e2ecf5",
    secondaryText: "#a9bcd1",
    mutedText: "#8196ae",
    pageBackground: "#1c2532",
    panelBackground: "#243142",
    inputBackground: "#18212c",
    bubbleBackground: "#2c3c50",
    border: "#465c73",
  }),
});

// 真实 OpenAI /v1/models 仅返回模型 ID，不返回上下文长度、图片或工具能力。
export const REMOTE_DISCOVERY_CATALOG = Object.freeze({
  "api.deepseek.com": [
    "deepseek-chat",
    "deepseek-reasoner",
  ],
  "generativelanguage.googleapis.com": [
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
  ],
  "api.siliconflow.cn": [
    "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-R1",
    "Qwen/Qwen2.5-72B-Instruct",
    "Qwen/Qwen2.5-32B-Instruct",
    "Qwen/Qwen2.5-Coder-32B-Instruct",
    "Qwen/Qwen2.5-VL-72B-Instruct",
    "Qwen/Qwen3-32B",
    "Qwen/Qwen3-235B-A22B",
    "THUDM/GLM-4-32B-0414",
    "THUDM/GLM-Z1-32B-0414",
    "Pro/deepseek-ai/DeepSeek-V3",
    "Pro/deepseek-ai/DeepSeek-R1",
    "Pro/Qwen/Qwen2.5-7B-Instruct",
    "moonshotai/Kimi-K2-Instruct",
    "internlm/internlm2_5-20b-chat",
  ],
  default: [
    "gpt-4.1",
    "gpt-4.1-mini",
    "o4-mini",
    "qwen3:8b",
    "qwen2.5-vl:7b",
    "llama3.2:latest",
  ],
});

// OpenAI Official (Codex) 订阅接口实际会返回 context_window 与 input_modalities，不返回 supportsTools。
const CODEX_DEFAULT_MODELS = [
  { modelId: "gpt-5.4", label: "gpt-5.4", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.4-mini", label: "gpt-5.4-mini", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.3-codex", label: "gpt-5.3-codex", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.2-codex", label: "gpt-5.2-codex", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.2", label: "gpt-5.2", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.1-codex-max", label: "gpt-5.1-codex-max", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
  { modelId: "gpt-5.1-codex-mini", label: "gpt-5.1-codex-mini", contextWindowTokens: 272000, inputModalities: ["text", "image"] },
];

export function createScenarioData(scenario = "typical") {
  const nowSec = Math.floor(Date.now() / 1000);
  if (scenario === "empty") {
    return {
      connections: [],
      codexProfiles: [],
      modelSlots: [
        { identity: "core:chat", label: "对话模型", required: true, selection: { serviceKey: "", profileId: "", modelId: "" } },
        { identity: "core:vision_chat", label: "视觉对话模型", required: false, selection: { serviceKey: "", profileId: "", modelId: "" } },
        { identity: "plugin:sakura.memory.mem0:curation", label: "记忆整理模型", required: false, selection: { serviceKey: "", profileId: "", modelId: "" } },
      ],
    };
  }

  if (scenario === "minimal") {
    return {
      connections: [
        {
          id: "demo-api",
          alias: "demo api",
          base_url: "https://api.example.com/v1",
          api_key: "",
          configured: false,
          credential_action: "keep",
          timeout_seconds: 60,
          createdAt: 1000,
          models: ["your-model-id"],
        },
      ],
      codexProfiles: [],
      modelSlots: [
        { identity: "core:chat", label: "对话模型", required: true, selection: { serviceKey: "sakura.model.openai_compatible", profileId: "demo-api", modelId: "your-model-id" } },
        { identity: "core:vision_chat", label: "视觉对话模型", required: false, selection: { serviceKey: "", profileId: "", modelId: "" } },
        { identity: "plugin:sakura.memory.mem0:curation", label: "记忆整理模型", required: false, selection: { serviceKey: "", profileId: "", modelId: "" } },
      ],
    };
  }

  const connections = [
    {
      id: "deepseek-main",
      alias: "DeepSeek",
      base_url: "https://api.deepseek.com/v1",
      api_key: "sk-demo-deepseek-9f2a4c8e1b0d",
      configured: true,
      credential_action: "replace",
      timeout_seconds: 60,
      createdAt: 1000,
      models: ["deepseek-chat", "deepseek-reasoner"],
    },
    {
      id: "siliconflow",
      alias: "硅基流动",
      base_url: "https://api.siliconflow.cn/v1",
      api_key: "sk-demo-siliconflow-7a1d3e5f8c2b",
      configured: true,
      credential_action: "replace",
      timeout_seconds: 60,
      createdAt: 2000,
      models: [
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
        "Qwen/Qwen2.5-72B-Instruct",
        "Qwen/Qwen2.5-32B-Instruct",
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "Qwen/Qwen2.5-VL-72B-Instruct",
        "Qwen/Qwen3-32B",
        "Qwen/Qwen3-235B-A22B",
        "THUDM/GLM-4-32B-0414",
        "THUDM/GLM-Z1-32B-0414",
        "Pro/deepseek-ai/DeepSeek-V3",
        "Pro/deepseek-ai/DeepSeek-R1",
        "moonshotai/Kimi-K2-Instruct",
      ],
    },
    {
      id: "google-gemini",
      alias: "Google 官方",
      base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
      api_key: "",
      configured: false,
      credential_action: "keep",
      timeout_seconds: 60,
      createdAt: 3000,
      models: ["gemini-2.5-flash", "gemini-2.5-pro"],
    },
    {
      id: "ollama-local",
      alias: "Ollama 本地",
      base_url: "http://127.0.0.1:11434/v1",
      api_key: "",
      configured: false,
      credential_action: "keep",
      timeout_seconds: 120,
      createdAt: 5000,
      models: ["qwen3:8b", "qwen2.5-vl:7b"],
    },
  ];

  const codexProfiles = [
    {
      profileId: "chatgpt",
      label: "OpenAI Official",
      createdAt: 4000,
      account: { loggedIn: true, email: "sakura.demo@example.com", plan: "Plus" },
      models: structuredClone(CODEX_DEFAULT_MODELS),
      usage: {
        updatedAt: nowSec - 90,
        windows: [
          { label: "5 小时窗口", windowSeconds: 18000, usedPercent: 18.4, resetsAt: nowSec + 11400 },
          { label: "每周额度", windowSeconds: 604800, usedPercent: 34.0, resetsAt: nowSec + 412000 },
        ],
      },
      operationResult: {},
    },
  ];

  const modelSlots = [
    {
      identity: "core:chat",
      label: "对话模型",
      required: true,
      selection: { serviceKey: "sakura.model.openai_compatible", profileId: "deepseek-main", modelId: "deepseek-chat" },
    },
    {
      identity: "core:vision_chat",
      label: "视觉对话模型",
      required: false,
      selection: scenario === "broken"
        ? { serviceKey: "sakura.model.openai_compatible", profileId: "siliconflow", modelId: "Qwen/Qwen2-VL-72B-Deprecated" }
        : { serviceKey: "sakura.model.openai_compatible", profileId: "ollama-local", modelId: "qwen2.5-vl:7b" },
    },
    {
      identity: "plugin:sakura.memory.mem0:curation",
      label: "记忆整理模型",
      required: false,
      selection: { serviceKey: "", profileId: "", modelId: "" },
    },
  ];

  return { connections, codexProfiles, modelSlots };
}

export function createCodexModels() {
  return structuredClone(CODEX_DEFAULT_MODELS);
}
