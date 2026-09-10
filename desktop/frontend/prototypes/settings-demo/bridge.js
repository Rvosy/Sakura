import { clone, theme, appearanceLimits, capabilityManifest } from "./data.js";

export function createBridge(win, state, hooks) {
  const events = new Map();
  const errors = [];
  const calls = [];
  const generation = () => "demo-" + state.generation;
  const identity = () => ({
    schemaVersion: 1,
    windowGeneration: 1,
    coreGenerationId: generation(),
  });
  const character = (id = state.current) =>
    state.characters.find((c) => c.id === id);
  const catalog = () => ({
    schemaVersion: 1,
    revision: state.revision,
    currentCharacterId: state.current,
    characters: state.characters.map((c) => ({
      id: c.id,
      displayName: c.name,
      hasVoice: c.hasVoice,
      hasExportableVoice: c.hasVoice,
    })),
  });
  const appearance = (id = state.current) => ({
    schemaVersion: 1,
    windowGeneration: 1,
    limits: appearanceLimits,
    presentation: {
      generationId: generation(),
      characterId: id,
      displayName: character(id).name,
      themeTokens: theme,
      portraitKeys: ["__default__", "开心"],
      portraitResourceUrls: {
        __default__: "/prototypes/asr/assets/navi.png",
        开心: "/prototypes/asr/assets/navi.png",
      },
    },
    appearance: {
      schemaVersion: 1,
      coreGenerationId: generation(),
      characterId: id,
      values: state.appearance,
    },
  });
  const originalPlugins = () =>
    state.plugins.filter(
      (p) =>
        !p.pluginId.startsWith("example.") &&
        p.pluginId !== "sakura.visual.portrait",
    );
  const currentVoice = () => state.voices[state.current];
  const memories = () => state.memories[state.current];
  const plugins = () => ({
    ...identity(),
    revision: state.revision.toString(16).padStart(16, "0"),
    state: "ready",
    reasonCode: "READY",
    plugins: hooks.version === "original" ? originalPlugins() : state.plugins,
  });
  const provider = () => ({
    ...state.provider,
    core_generation_id: generation(),
  });
  const voice = () => ({
    ...identity(),
    character: { characterId: state.current, displayName: character().name },
    selection: {
      configured: character().hasVoice,
      enabled: character().hasVoice && currentVoice().enabled,
      providerId: currentVoice().providerId,
      available: true,
    },
    providers: state.plugins
      .filter((p) =>
        ["sakura.tts.gpt-sovits", "sakura.tts.genie"].includes(p.pluginId),
      )
      .map((p) => ({
        providerId: p.pluginId,
        label: p.pluginId === "sakura.tts.genie" ? "Genie TTS" : "GPT-SoVITS",
        available: p.enabled,
      })),
    sections: state.plugins
      .filter((p) =>
        ["sakura.tts.gpt-sovits", "sakura.tts.genie"].includes(p.pluginId),
      )
      .flatMap((p) =>
        p.sections
          .filter((s) => s.surface === "voice")
          .map((s) => {
            const { surface, ...rest } = s;
            return { pluginId: p.pluginId, ...rest };
          }),
      ),
  });
  const storage = () => ({
    schemaVersion: 1,
    userRoot: "演示 / Sakura",
    ttsRoot: state.ttsRoot,
    ttsRootSource: state.ttsRoot === "默认语音目录" ? "default" : "custom",
    ttsRootAvailable: true,
    reasonCode: null,
  });
  const lifecycle = () => ({
    supervisor: {
      generationNumber: state.generation,
      generationId: generation(),
      state: "running",
    },
    snapshot: { generationId: generation(), readiness: "ready" },
    characterPresentation: {
      generationId: generation(),
      characterId: state.current,
    },
  });
  const telemetry = () => ({
    schemaVersion: 1,
    enabled: state.telemetry,
    installationId: "00000000-0000-4000-8000-000000000001",
  });
  const asr = () => ({
    hubPluginId: "sakura.asr",
    selectedProviderId: "sakura.asr.sensevoice",
    inputDeviceId: state.asr.inputDeviceId,
    providers: [
      {
        providerId: "sakura.asr.sensevoice",
        label: "SenseVoice",
        state: "unloaded",
        processingLocation: "local",
        available: true,
      },
    ],
    sections: [],
  });
  const timings = () => ({
    schemaVersion: 1,
    windowGeneration: 1,
    values: state.timing,
    limits: {
      subtitleTypingIntervalMs: [0, 200, 35],
      replySegmentPauseMs: [0, 3000, 300],
    },
  });
  const bubble = () => ({
    schemaVersion: 1,
    windowGeneration: 1,
    values: state.bubble,
    limits: { autoHideDelaySeconds: [1, 300, 15] },
  });
  function saveFields(pluginId, sectionId, values) {
    const s = state.plugins
      .find((p) => p.pluginId === pluginId)
      ?.sections.find((s) => s.sectionId === sectionId);
    if (!s) return;
    Object.assign(s.values, values);
    s.fields.forEach((f) => (f.value = s.values[f.key]));
    state.revision++;
  }
  async function command(name, args = {}) {
    calls.push({ name, args: clone(args) });
    switch (name) {
      case "settings_capability_manifest": {
        const m = capabilityManifest();
        win.document
          .querySelectorAll("[data-settings-feature]")
          .forEach((el) => {
            const f = el.dataset.settingsFeature;
            const page =
              el.closest(".settings-page")?.id.replace("page-", "") || "system";
            if (m.sections[page]) m.sections[page].features[f] = "available";
          });
        return m;
      }
      case "runtime_lifecycle_snapshot":
        return lifecycle();
      case "interaction_latency_diagnostics_enabled":
        return false;
      case "reveal_settings_window":
        hooks.revealed?.();
        return null;
      case "settings_characters_get":
        return catalog();
      case "settings_character_appearance_get":
        return appearance();
      case "settings_character_visual_preview":
        return { ...appearance(args.characterId), revision: args.revision };
      case "settings_character_appearance_save":
        state.appearance = clone(args.values);
        return appearance().appearance;
      case "settings_character_appearance_preview":
        return { ...appearance(), revision: args.revision || 1 };
      case "settings_character_appearance_cancel_preview":
        return null;
      case "settings_character_appearance_scale_gesture":
      case "settings_character_appearance_scale_frame":
      case "settings_character_appearance_layout_frame":
      case "settings_character_appearance_layout_gesture":
        return null;
      case "settings_character_select": {
        const previous = generation();
        state.current = args.characterId;
        state.generation++;
        state.revision++;
        return {
          schemaVersion: 1,
          previousCoreGenerationId: previous,
          restartState: "requested",
          targetCharacterId: state.current,
          snapshot: catalog(),
        };
      }
      case "settings_character_choose_import":
        hooks.info?.(
          "导入角色包",
          "演示中不会读取本机文件。可以通过顶部的演示场景查看不同角色资源。",
        );
        return null;
      case "settings_character_choose_export":
        hooks.info?.(
          "导出角色",
          "你选择的导出范围已保留在这个操作示例中，演示不会生成真实角色包。",
        );
        return null;
      case "open_character_studio":
        hooks.studio?.(args.characterId);
        return null;
      case "settings_provider_model_get":
        return provider();
      case "settings_provider_model_save": {
        const d = args.draft;
        state.provider.providers = (
          d.profiles ||
          d.providers ||
          state.provider.providers
        ).map((p) => ({
          id: p.id,
          alias: p.alias,
          base_url: p.base_url,
          configured:
            p.credential?.action === "clear"
              ? false
              : p.credential?.action === "replace"
                ? true
                : (state.provider.providers.find((old) => old.id === p.id)
                    ?.configured ?? false),
          models: p.models || [],
        }));
        if (d.model_slots) {
          state.provider.model_slots = state.provider.model_slots.map((s) => ({
            ...s,
            selection: d.model_slots[s.identity] || s.selection,
          }));
        }
        if (d.settings) Object.assign(state.provider.settings, d.settings);
        return { change_plan: "applied", save_state: "complete" };
      }
      case "settings_provider_model_probe":
        return args.kind === "list_models"
          ? {
              models: ["deepseek-chat", "deepseek-reasoner", "qwen3:8b"],
              message: "演示模型列表",
            }
          : {
              success: true,
              ok: true,
              model: args.profile?.models?.[0] || "deepseek-chat",
              message: "连接成功（模拟）",
            };
      case "settings_provider_model_cancel":
        return null;
      case "settings_plugins_get":
        return plugins();
      case "settings_plugins_enabled_set": {
        const p = state.plugins.find((p) => p.installId === args.installId);
        if (!p) throw Error("未找到演示插件");
        p.enabled = args.enabled;
        p.state = p.enabled ? "active" : "disabled";
        p.reasonCode = p.enabled ? "ACTIVE" : "DISABLED";
        state.revision++;
        hooks.pluginChanged?.();
        return {
          ...plugins(),
          managementAction: "enabled_changed",
          installId: p.installId,
          pluginId: p.pluginId,
          desiredSaved: true,
          applicationState: "applied",
          applicationReasonCode: "READY",
        };
      }
      case "settings_plugins_save":
        saveFields(args.pluginId, args.sectionId, args.values);
        return {
          saved: true,
          pluginId: args.pluginId,
          sectionId: args.sectionId,
          changePlan: "applied",
          applicationState: "applied",
          applicationReasonCode: "READY",
        };
      case "settings_plugins_uninstall": {
        const p = state.plugins.find((p) => p.installId === args.installId);
        if (!p?.canUninstall) throw Error("此演示插件不能卸载");
        state.plugins = state.plugins.filter((item) => item !== p);
        state.revision++;
        hooks.pluginChanged?.();
        return {
          ...plugins(),
          managementAction: "uninstalled",
          installId: p.installId,
          pluginId: p.pluginId,
        };
      }
      case "settings_plugins_install":
        hooks.info?.(
          "安装插件",
          "演示不会安装真实插件。请在“缺少插件”场景中体验安装入口。",
        );
        return null;
      case "settings_plugins_action":
        hooks.info?.(
          "插件操作",
          "此操作使用演示数据，不会启动服务或下载模型。",
        );
        return {
          ok: true,
          message: "演示操作已完成",
          changePlan: "applied",
          applicationState: "applied",
          applicationReasonCode: "READY",
        };
      case "settings_plugins_collection": {
        const r = args.payload || args;
        const operation = args.operation || "query";
        if (operation === "create") {
          const item = {
            itemId: crypto.randomUUID(),
            values: { ...r.values, updatedAt: new Date().toISOString() },
          };
          memories().push(item);
          return item;
        }
        if (operation === "delete") {
          state.memories[state.current] = memories().filter(
            (m) => m.itemId !== r.itemId,
          );
          return { deleted: true };
        }
        if (operation === "update") {
          const m = memories().find((m) => m.itemId === r.itemId);
          if (!m) throw Error("未找到演示记忆");
          Object.assign(m.values, r.values, {
            updatedAt: new Date().toISOString(),
          });
          return m;
        }
        const items = memories().filter(
          (m) =>
            (!r.search || m.values.content.includes(r.search)) &&
            Object.entries(r.filters || {}).every(
              ([key, value]) => m.values[key] === value,
            ),
        );
        return { items, total: items.length, nextCursor: null };
      }
      case "settings_voice_get":
        return voice();
      case "settings_voice_save": {
        const d = args.draft;
        Object.assign(currentVoice(), {
          enabled: d.enabled ?? d.selection?.enabled ?? currentVoice().enabled,
          providerId:
            d.provider ??
            d.providerId ??
            d.selection?.providerId ??
            currentVoice().providerId,
        });
        for (const s of d.sections || [])
          saveFields(s.pluginId, s.sectionId, s.values);
        return {
          snapshot: voice(),
          applicationState: "applied",
          saveState: "complete",
          savedSections: [],
          selectionSaved: true,
          reasonCode: "READY",
        };
      }
      case "settings_asr_get":
        return asr();
      case "settings_asr_devices":
        return {
          devices: [{ id: "demo-mic", label: "内置麦克风" }],
          defaultDeviceId: "demo-mic",
        };
      case "settings_asr_save":
        Object.assign(state.asr, args.payload);
        return asr();
      case "settings_asr_test_start":
        hooks.info?.("测试麦克风", "这是设置交互演示，不会读取麦克风。");
        return { accepted: false };
      case "settings_asr_test_cancel":
        return null;
      case "settings_chat_presentation_timing_get":
        return timings();
      case "settings_chat_presentation_timing_save":
        state.timing = clone(args.values);
        return timings();
      case "settings_bubble_auto_hide_get":
        return bubble();
      case "settings_bubble_auto_hide_save":
        state.bubble = clone(args.values);
        return bubble();
      case "settings_screen_awareness_get":
        return { ...identity(), settings: state.screen };
      case "settings_screen_awareness_save":
        state.screen = clone(args.settings);
        return { ...identity(), settings: state.screen };
      case "settings_tools_get":
        return { ...identity(), runtimeLimits: state.limits };
      case "settings_tools_save":
        state.limits = clone(args.runtimeLimits);
        return { ...identity(), runtimeLimits: state.limits };
      case "settings_autostart_get":
        return {
          schemaVersion: 1,
          windowGeneration: 1,
          launchAtLogin: state.launchAtLogin,
        };
      case "settings_autostart_save":
        state.launchAtLogin = args.launchAtLogin;
        return {
          schemaVersion: 1,
          windowGeneration: 1,
          launchAtLogin: state.launchAtLogin,
        };
      case "settings_storage_get":
        return storage();
      case "settings_storage_choose_tts_root":
        state.ttsRoot = "演示 / 我的语音";
        return storage();
      case "settings_storage_reset_tts_root":
        state.ttsRoot = "默认语音目录";
        return storage();
      case "settings_storage_open_user_root":
        hooks.info?.("数据目录", "这是演示目录，不会打开或修改实际数据。");
        return null;
      case "settings_legacy_data_import_choose":
        hooks.info?.(
          "导入旧数据",
          "演示不读取旧数据；正式程序仍使用原有导入流程。",
        );
        return null;
      case "settings_telemetry_get":
        return telemetry();
      case "settings_telemetry_set_enabled":
        state.telemetry = args.enabled;
        return telemetry();
      case "settings_telemetry_regenerate_installation_id":
        return telemetry();
      case "settings_update_get":
      case "settings_update_cached_get":
        return {
          schemaVersion: 1,
          currentVersion: "0.9.10",
          mode: "installed",
          available: false,
          version: null,
          notes: null,
          pubDate: null,
          downloadUrl: null,
        };
      case "settings_update_preferences_get":
        return { schemaVersion: 1, autoCheckEnabled: state.autoCheck };
      case "settings_update_preferences_set":
        state.autoCheck = args.autoCheckEnabled;
        return { schemaVersion: 1, autoCheckEnabled: state.autoCheck };
      case "settings_about_get":
        return {
          schemaVersion: 1,
          version: "0.9.10",
          repositoryUrl: "https://github.com/Rvosy/Sakura",
        };
      case "resolve_settings_close":
        if (args.discard) hooks.close?.();
        return null;
      case "resolve_settings_exit":
      case "acknowledge_settings_exit":
      case "settings_first_run_guide_complete":
      case "frontend_diagnostics_report":
      case "record_runtime_diagnostics":
      case "record_frontend_diagnostics":
      case "report_frontend_diagnostics":
        return null;
      default:
        if (
          name.startsWith("settings_about_open_") ||
          name === "settings_telemetry_open_documentation" ||
          name.startsWith("settings_macos_open_")
        ) {
          hooks.info?.("外部入口", "演示保留入口，不会打开外部应用。");
          return null;
        }
        errors.push(name);
        throw Error("演示尚未覆盖操作：" + name);
    }
  }
  win.__TAURI__ = {
    core: { invoke: async (name, args) => clone(await command(name, args)) },
    event: {
      listen: async (name, fn) => {
        if (!events.has(name)) events.set(name, new Set());
        events.get(name).add(fn);
        return () => events.get(name).delete(fn);
      },
    },
    window: {
      getCurrentWindow: () => ({
        onCloseRequested: async () => () => {},
        onFocusChanged: async () => () => {},
        setFocus: async () => {},
        startDragging: async () => {},
      }),
    },
  };
  win.__DEMO_BRIDGE__ = {
    state,
    errors,
    calls,
    emit: (name, payload) => events.get(name)?.forEach((fn) => fn({ payload })),
    plugins,
  };
  return win.__DEMO_BRIDGE__;
}
