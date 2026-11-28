import { createIcon } from "../../core/icons.js";
import { createMorphIcon } from "../../core/morph-icon.js";
import { createLinearWavyProgress } from "./wavy-progress.js";

const PROVIDER_PRESETS = [
  {
    key: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    host: "api.deepseek.com",
    iconUrl: "data:image/svg+xml," + encodeURIComponent('<svg height="1em" style="flex:none;line-height:1" viewBox="0 0 24 24" width="1em" xmlns="http://www.w3.org/2000/svg"><title>DeepSeek</title><path d="M23.748 4.482c-.254-.124-.364.113-.512.234-.051.039-.094.09-.137.136-.372.397-.806.657-1.373.626-.829-.046-1.537.214-2.163.848-.133-.782-.575-1.248-1.247-1.548-.352-.156-.708-.311-.955-.65-.172-.241-.219-.51-.305-.774-.055-.16-.11-.323-.293-.35-.2-.031-.278.136-.356.276-.313.572-.434 1.202-.422 1.84.027 1.436.633 2.58 1.838 3.393.137.093.172.187.129.323-.082.28-.18.552-.266.833-.055.179-.137.217-.329.14a5.526 5.526 0 01-1.736-1.18c-.857-.828-1.631-1.742-2.597-2.458a11.365 11.365 0 00-.689-.471c-.985-.957.13-1.743.388-1.836.27-.098.093-.432-.779-.428-.872.004-1.67.295-2.687.684a3.055 3.055 0 01-.465.137 9.597 9.597 0 00-2.883-.102c-1.885.21-3.39 1.102-4.497 2.623C.082 8.606-.231 10.684.152 12.85c.403 2.284 1.569 4.175 3.36 5.653 1.858 1.533 3.997 2.284 6.438 2.14 1.482-.085 3.133-.284 4.994-1.86.47.234.962.327 1.78.397.63.059 1.236-.03 1.705-.128.735-.156.684-.837.419-.961-2.155-1.004-1.682-.595-2.113-.926 1.096-1.296 2.746-2.642 3.392-7.003.05-.347.007-.565 0-.845-.004-.17.035-.237.23-.256a4.173 4.173 0 001.545-.475c1.396-.763 1.96-2.015 2.093-3.517.02-.23-.004-.467-.247-.588zM11.581 18c-2.089-1.642-3.102-2.183-3.52-2.16-.392.024-.321.471-.235.763.09.288.207.486.371.739.114.167.192.416-.113.603-.673.416-1.842-.14-1.897-.167-1.361-.802-2.5-1.86-3.301-3.307-.774-1.393-1.224-2.887-1.298-4.482-.02-.386.093-.522.477-.592a4.696 4.696 0 011.529-.039c2.132.312 3.946 1.265 5.468 2.774.868.86 1.525 1.887 2.202 2.891.72 1.066 1.494 2.082 2.48 2.914.348.292.625.514.891.677-.802.09-2.14.11-3.054-.614zm1-6.44a.306.306 0 01.415-.287.302.302 0 01.2.288.306.306 0 01-.31.307.303.303 0 01-.304-.308zm3.11 1.596c-.2.081-.399.151-.59.16a1.245 1.245 0 01-.798-.254c-.274-.23-.47-.358-.552-.758a1.73 1.73 0 01.016-.588c.07-.327-.008-.537-.239-.727-.187-.156-.426-.199-.688-.199a.559.559 0 01-.254-.078c-.11-.054-.2-.19-.114-.358.028-.054.16-.186.192-.21.356-.202.767-.136 1.146.016.352.144.618.408 1.001.782.391.451.462.576.685.914.176.265.336.537.445.848.067.195-.019.354-.25.452z" fill="#4D6BFE"></path></svg>\n'),
  },
  {
    key: "google",
    label: "Google 官方",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
    host: "generativelanguage.googleapis.com",
    iconUrl: "data:image/svg+xml," + encodeURIComponent('<svg height="1em" style="flex:none;line-height:1" viewBox="0 0 24 24" width="1em" xmlns="http://www.w3.org/2000/svg"><title>Google</title><path d="M23 12.245c0-.905-.075-1.565-.236-2.25h-10.54v4.083h6.186c-.124 1.014-.797 2.542-2.294 3.569l-.021.136 3.332 2.53.23.022C21.779 18.417 23 15.593 23 12.245z" fill="#4285F4"></path><path d="M12.225 23c3.03 0 5.574-.978 7.433-2.665l-3.542-2.688c-.948.648-2.22 1.1-3.891 1.1a6.745 6.745 0 01-6.386-4.572l-.132.011-3.465 2.628-.045.124C4.043 20.531 7.835 23 12.225 23z" fill="#34A853"></path><path d="M5.84 14.175A6.65 6.65 0 015.463 12c0-.758.138-1.491.361-2.175l-.006-.147-3.508-2.67-.115.054A10.831 10.831 0 001 12c0 1.772.436 3.447 1.197 4.938l3.642-2.763z" fill="#FBBC05"></path><path d="M12.225 5.253c2.108 0 3.529.892 4.34 1.638l3.167-3.031C17.787 2.088 15.255 1 12.225 1 7.834 1 4.043 3.469 2.197 7.062l3.63 2.763a6.77 6.77 0 016.398-4.572z" fill="#EB4335"></path></svg>\n'),
  },
];

function createEyeSvg(document, open) {
  const svgNS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(svgNS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", "15");
  svg.setAttribute("height", "15");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const addPath = (d) => {
    const p = document.createElementNS(svgNS, "path");
    p.setAttribute("d", d);
    svg.append(p);
  };
  if (open) {
    addPath("M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0");
    const circle = document.createElementNS(svgNS, "circle");
    circle.setAttribute("cx", "12");
    circle.setAttribute("cy", "12");
    circle.setAttribute("r", "3");
    svg.append(circle);
  } else {
    addPath("M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49");
    addPath("M14.084 14.158a3 3 0 0 1-4.242-4.242");
    addPath("M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143");
    addPath("m2 2 20 20");
  }
  return svg;
}

function providerHost(url) {
  const text = String(url || "").trim();
  if (!text) return "";
  try {
    return new URL(text).host;
  } catch {
    return text.replace(/^https?:\/\//, "").split("/")[0];
  }
}

export function createRedesignedProviderEditor({
  document,
  window,
  read,
  write,
  probe,
  cancel,
  onError,
  notify,
  confirmAction,
  onCatalogChanged = () => {},
  getModelSlots = () => [],
  codexController,
}) {
  let disposed = false;
  let lastValue = "";
  let secretVisible = false;
  const overlays = new Set();
  // `${providerId}\0${modelId}` -> { converge, dispose }
  const activeWaveControllers = new Map();
  let liveDetail = null;

  const state = {
    profiles: [],
    selectedId: "",
    search: "",
    modelFilter: "",
    filterOpen: false,
    // profileId -> Map<modelId, { state: "working" | "converging" | "ready" | "error", startedAt?: number, error?: unknown }>
    modelTestStates: new Map(),
  };

  function waveKey(providerId, modelId) {
    return `${providerId}\0${modelId}`;
  }

  function disposeWaveController(providerId, modelId) {
    const key = waveKey(providerId, modelId);
    const ctrl = activeWaveControllers.get(key);
    if (ctrl) {
      ctrl.dispose();
      activeWaveControllers.delete(key);
    }
  }

  function disposeProviderWaves(providerId) {
    const prefix = `${providerId}\0`;
    for (const [key, ctrl] of activeWaveControllers) {
      if (key.startsWith(prefix)) {
        ctrl.dispose();
        activeWaveControllers.delete(key);
      }
    }
  }

  function disposeAllWaves() {
    for (const ctrl of activeWaveControllers.values()) {
      ctrl.dispose();
    }
    activeWaveControllers.clear();
  }

  function disposeLiveRowIcons() {
    if (!liveDetail) return;
    for (const rowView of liveDetail.rows.values()) {
      rowView.morphIcon?.dispose();
      rowView.morphIcon = null;
    }
  }

  function getModelTestMap(profileId) {
    let map = state.modelTestStates.get(profileId);
    if (!map) {
      map = new Map();
      state.modelTestStates.set(profileId, map);
    }
    return map;
  }

  function hasActiveTest(profileId) {
    const map = state.modelTestStates.get(profileId);
    if (!map) return false;
    for (const entry of map.values()) {
      if (entry.state === "working") return true;
    }
    return false;
  }

  function clearProfileTests(profileId) {
    disposeProviderWaves(profileId);
    state.modelTestStates.delete(profileId);
  }

  const el = (tag, className = "", text = "") => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  };

  const root = el("div", "connection-editor provider-redesign");
  const workbench = el("div", "provider-workbench");

  // 左侧列表
  const listShell = el("section", "provider-list-shell");
  listShell.setAttribute("aria-label", "模型服务列表");
  const listHead = el("div", "provider-list-head");
  const searchWrap = el("div", "provider-search-wrap");
  const searchIcon = createIcon(document, "search");
  searchIcon.classList.add("provider-search-icon");
  const searchInput = el("input", "provider-search-input");
  searchInput.type = "search";
  searchInput.placeholder = "搜索服务";
  searchInput.setAttribute("aria-label", "搜索模型服务");
  searchInput.autocomplete = "off";
  searchInput.spellcheck = false;
  const searchClear = el("button", "provider-search-clear");
  searchClear.type = "button";
  searchClear.hidden = true;
  searchClear.setAttribute("aria-label", "清空搜索");
  searchClear.append(createIcon(document, "x"));
  searchWrap.append(searchIcon, searchInput, searchClear);

  const addBtn = el("button", "secondary-button provider-add-btn");
  addBtn.type = "button";
  addBtn.setAttribute("aria-label", "添加模型服务");
  addBtn.append(createIcon(document, "plus"));
  listHead.append(searchWrap, addBtn);

  const listBody = el("div", "provider-list");
  listShell.append(listHead, listBody);

  // 右侧详情
  const detailShell = el("aside", "provider-detail");
  detailShell.setAttribute("aria-label", "模型服务详情");

  workbench.append(listShell, detailShell);
  root.append(workbench);

  function loadInitial() {
    const raw = read() || [];
    state.profiles = raw.map((profile) => ({
      id: profile.id,
      alias: profile.alias || profile.id || "新模型服务",
      base_url: profile.base_url || "",
      api_key: profile.api_key || "",
      configured: Boolean(profile.configured),
      credential_action: profile.credential_action || "keep",
      timeout_seconds: profile.timeout_seconds || 60,
      createdAt: profile.createdAt || 0,
      models: Array.isArray(profile.models) ? profile.models.map(String) : [],
    }));
    lastValue = JSON.stringify(state.profiles);
    if (!allProviders().some((p) => p.id === state.selectedId)) {
      state.selectedId = allProviders()[0]?.id || "";
    }
  }

  function commitWrite() {
    const cloned = structuredClone(state.profiles);
    lastValue = JSON.stringify(cloned);
    write(cloned);
    onCatalogChanged();
  }

  function allProviders() {
    return [
      ...state.profiles.map((p) => ({
        ...p,
        kind: "api",
        refServiceKey: "sakura.model.openai_compatible",
        refProfileId: p.id,
      })),
      ...codexController.listServices().map((s) => ({
        id: s.id,
        alias: s.label,
        description: s.description,
        configured: s.configured,
        createdAt: s.createdAt || 0,
        models: s.models || [],
        kind: "codex",
        service: s,
        refServiceKey: "sakura.model.codex",
        refProfileId: s.profileId,
      })),
    ].sort((a, b) => (a.createdAt || 0) - (b.createdAt || 0));
  }

  function nextCreatedAt() {
    return Math.max(Date.now(), ...allProviders().map((p) => (p.createdAt || 0) + 1));
  }

  function presetForProfile(profile) {
    const host = providerHost(profile.base_url);
    const alias = String(profile.alias || "").toLowerCase();
    return PROVIDER_PRESETS.find((preset) => preset.host === host || preset.label.toLowerCase() === alias) || null;
  }

  function applyAvatar(avatar, { iconUrl, iconName, initial } = {}) {
    avatar.setAttribute("aria-hidden", "true");
    avatar.replaceChildren();
    avatar.classList.remove("is-initial");
    if (iconName) {
      avatar.append(createIcon(document, iconName));
    } else if (iconUrl) {
      const img = document.createElement("img");
      img.className = "provider-avatar-img";
      img.src = iconUrl;
      img.alt = "";
      avatar.append(img);
    } else {
      avatar.classList.add("is-initial");
      avatar.textContent = (initial || "?").trim().charAt(0).toUpperCase() || "?";
    }
  }

  function createAvatar(provider) {
    const avatar = el("span", "provider-avatar");
    const preset = provider.service || presetForProfile(provider);
    applyAvatar(avatar, {
      iconUrl: preset?.iconUrl,
      iconName: preset?.iconName,
      initial: provider.alias || "?",
    });
    return avatar;
  }

  function slotUsagesForProvider(provider) {
    return getModelSlots().filter(
      (slot) => slot.selection?.serviceKey === provider.refServiceKey && slot.selection?.profileId === provider.refProfileId,
    );
  }

  function slotUsagesForModel(provider, modelId) {
    return slotUsagesForProvider(provider).filter((slot) => slot.selection?.modelId === modelId);
  }

  function filteredProviders() {
    const all = allProviders();
    const q = state.search.trim().toLowerCase();
    if (!q) return all;
    return all.filter((item) =>
      [item.alias, item.description, item.base_url, ...(item.models || [])]
        .join(" ")
        .toLowerCase()
        .includes(q),
    );
  }

  function selectProvider(id) {
    if (id !== state.selectedId) {
      state.modelFilter = "";
      state.filterOpen = false;
      secretVisible = false;
      void cancel();
    }
    state.selectedId = id;
    renderAll();
  }

  function renderList() {
    listBody.replaceChildren();
    const all = allProviders();
    const filtered = filteredProviders();
    searchClear.hidden = !state.search.trim();

    if (!filtered.length) {
      const empty = el("div", "provider-list-empty");
      if (all.length) {
        empty.append(el("p", "", "没有匹配的服务"));
      } else {
        empty.append(el("p", "", "尚未添加模型服务"));
        const cta = el("button", "primary-button", "添加模型服务");
        cta.type = "button";
        cta.addEventListener("click", openAddDialog);
        empty.append(cta);
      }
      listBody.append(empty);
      return;
    }

    for (const item of filtered) {
      const card = el("button", "provider-card");
      card.type = "button";
      card.classList.toggle("is-selected", item.id === state.selectedId);
      card.addEventListener("click", () => selectProvider(item.id));

      const main = el("div", "provider-card-main");
      const top = el("div", "provider-card-top");
      const title = el("strong", "", item.alias || item.id);
      top.append(title);

      const hostText = item.kind === "codex"
        ? (item.service.account.loggedIn ? "已登录" : "未登录")
        : (providerHost(item.base_url) || "未设置地址");
      const meta = el("div", "provider-card-meta");
      meta.append(
        el("span", "provider-card-host", hostText),
        el("span", "provider-card-models", ` · ${(item.models || []).length} 模型`),
      );
      main.append(top, meta);

      card.append(createAvatar(item), main);
      listBody.append(card);
    }
  }

  function renderDetail() {
    disposeLiveRowIcons();
    disposeAllWaves();
    liveDetail = null;
    detailShell.replaceChildren();
    const provider = allProviders().find((p) => p.id === state.selectedId);
    if (!provider) {
      const empty = el("div", "provider-detail-empty");
      empty.append(el("p", "", allProviders().length ? "请选择模型服务" : "尚未添加模型服务"));
      if (!allProviders().length) {
        const btn = el("button", "primary-button", "添加模型服务");
        btn.type = "button";
        btn.addEventListener("click", openAddDialog);
        empty.append(btn);
      }
      detailShell.append(empty);
      return;
    }

    if (provider.kind === "codex") {
      renderCodexDetail(provider);
    } else {
      const mutable = state.profiles.find((p) => p.id === provider.id);
      if (mutable) renderApiDetail(mutable, provider);
    }
  }

  function applyRowProbeState(providerId, modelId, rowView, entry, anyWorking, { animateSend = false, animateReveal = false } = {}) {
    if (!entry && rowView.probeTrack) {
      rowView.checkRevealed = false;
    }
    if (entry?.state === "working") {
      rowView.checkRevealed = false;
    }

    const probeState = entry?.state || "idle";
    const isWorking = probeState === "working";
    const isConverging = probeState === "converging";
    const isTrackVisible = isWorking || isConverging;
    const isPassed = probeState === "ready" || (isConverging && rowView.checkRevealed);
    const isFailed = probeState === "error";

    rowView.row.classList.toggle("is-testing", isTrackVisible);
    rowView.row.classList.toggle("has-probe-track", isTrackVisible);
    rowView.row.classList.toggle("has-probe-state", Boolean(entry));

    const testBtn = rowView.testBtn;
    testBtn.disabled = Boolean(anyWorking && !isWorking);
    testBtn.classList.toggle("is-passed", isPassed);
    testBtn.classList.toggle("is-failed", isFailed);
    testBtn.classList.toggle("is-working", isWorking || (isConverging && !rowView.checkRevealed));

    if (isTrackVisible) {
      if (!rowView.probeTrack) {
        const track = el("div", "provider-probe-track");
        track.setAttribute("aria-hidden", "true");
        rowView.row.insertBefore(track, rowView.actions);
        rowView.probeTrack = track;
        const key = waveKey(providerId, modelId);
        activeWaveControllers.get(key)?.dispose();
        const ctrl = createLinearWavyProgress(track, {
          startedAt: entry.startedAt ?? window.performance.now(),
        });
        activeWaveControllers.set(key, ctrl);
      }
    } else if (rowView.probeTrack) {
      disposeWaveController(providerId, modelId);
      rowView.probeTrack.remove();
      rowView.probeTrack = null;
    }

    if (isFailed) {
      rowView.morphIcon?.dispose();
      rowView.morphIcon = null;
      testBtn.replaceChildren(createIcon(document, "triangle-alert"));
      testBtn.setAttribute("aria-label", `${modelId} 测试失败，点击查看详情`);
      return;
    }

    const targetMorph = isPassed ? "check" : (isWorking || isConverging) ? "stop" : "send-horizontal";
    if (!rowView.morphIcon) {
      const morphHost = el("span", "sakura-morph-icon");
      testBtn.replaceChildren(morphHost);
      rowView.morphIcon = createMorphIcon({ element: morphHost, initial: targetMorph });
    } else {
      if (animateSend) rowView.morphIcon.accent("send");
      rowView.morphIcon.set(targetMorph);
    }

    if (animateReveal) {
      const target = testBtn.firstElementChild;
      if (target) {
        target.classList.remove("provider-check-pop");
        void target.offsetWidth;
        target.classList.add("provider-check-pop");
      }
    }

    if (isPassed) {
      testBtn.setAttribute("aria-label", `${modelId} 测试通过，点击重测`);
    } else if (isWorking || isConverging) {
      testBtn.setAttribute("aria-label", `取消测试 ${modelId}`);
    } else {
      testBtn.setAttribute("aria-label", `测试 ${modelId}`);
    }
  }

  function syncLiveProbeStates(providerId) {
    if (!liveDetail || liveDetail.providerId !== providerId) return false;
    const isWorking = hasActiveTest(providerId);
    if (liveDetail.testTopBtn) {
      liveDetail.testTopBtn.textContent = isWorking ? "取消测试" : "测试连接";
    }
    const testMap = state.modelTestStates.get(providerId);
    for (const [modelId, rowView] of liveDetail.rows) {
      applyRowProbeState(providerId, modelId, rowView, testMap?.get(modelId), isWorking);
    }
    return true;
  }

  function renderModelsSection(provider, { editable, profileRef, onTestModel, onCancelTest, testTopBtn = null }) {
    const section = el("section", "provider-models-section");
    const models = provider.models || [];

    const head = el("div", "provider-models-head");
    const titleWrap = el("div", "provider-models-title");
    titleWrap.append(
      el("h3", "", "模型"),
      el("span", "provider-models-count", String(models.length)),
    );

    const tools = el("div", "provider-models-tools");

    let filterInput = null;
    if (models.length >= 6 || state.filterOpen || state.modelFilter) {
      if (state.filterOpen || state.modelFilter) {
        filterInput = el("input", "provider-model-filter");
        filterInput.type = "search";
        filterInput.placeholder = "筛选模型";
        filterInput.value = state.modelFilter;
        filterInput.setAttribute("aria-label", "筛选当前服务的模型");
        tools.append(filterInput);
      }
      const toggleFilter = el("button", "provider-filter-toggle");
      toggleFilter.type = "button";
      toggleFilter.setAttribute("aria-pressed", String(state.filterOpen || Boolean(state.modelFilter)));
      toggleFilter.setAttribute("aria-label", "筛选模型");
      toggleFilter.append(createIcon(document, "search"));
      toggleFilter.addEventListener("click", () => {
        state.filterOpen = !state.filterOpen;
        if (!state.filterOpen) state.modelFilter = "";
        renderDetail();
        if (state.filterOpen) {
          detailShell.querySelector(".provider-model-filter")?.focus();
        }
      });
      tools.append(toggleFilter);
    }

    if (editable) {
      const fetchBtn = el("button", "secondary-button", "获取模型列表");
      fetchBtn.type = "button";
      fetchBtn.addEventListener("click", () => handleFetchModels(profileRef, fetchBtn));
      tools.append(fetchBtn);
    }

    head.append(titleWrap, tools);
    section.append(head);

    const listBox = el("div", "provider-model-list");
    const rowViews = new Map();
    liveDetail = { providerId: provider.id, testTopBtn, rows: rowViews };

    const renderRows = () => {
      disposeLiveRowIcons();
      disposeProviderWaves(provider.id);
      rowViews.clear();
      listBox.replaceChildren();
      const q = state.modelFilter.trim().toLowerCase();
      const visible = q ? models.filter((m) => m.toLowerCase().includes(q)) : models;

      if (!visible.length) {
        const empty = el("div", "provider-model-empty");
        empty.textContent = models.length
          ? "没有匹配的模型"
          : (editable ? "尚未添加模型" : "暂无可用模型");
        listBox.append(empty);
        return;
      }

      const testMap = state.modelTestStates.get(provider.id);
      const anyWorking = hasActiveTest(provider.id);
      for (const modelId of visible) {
        const row = el("div", "provider-model-row");
        let entry = testMap?.get(modelId);
        if (entry?.state === "converging") {
          entry = { state: "ready" };
          testMap.set(modelId, entry);
        }

        const main = el("div", "provider-model-main");
        const idSpan = el("span", "provider-model-id", modelId);
        idSpan.addEventListener("mouseenter", () => {
          if (idSpan.scrollWidth > idSpan.clientWidth) idSpan.title = modelId;
          else idSpan.removeAttribute("title");
        });
        main.append(idSpan);

        const usedSlots = slotUsagesForModel(provider, modelId);
        if (usedSlots.length) {
          main.append(el("span", "provider-model-note is-slot", usedSlots.map((s) => s.label).join(" · ")));
        }

        const actions = el("div", "provider-model-actions");
        const testBtn = el("button", "provider-icon-btn");
        testBtn.type = "button";
        testBtn.addEventListener("click", () => {
          const current = state.modelTestStates.get(provider.id)?.get(modelId);
          if (current?.state === "working") {
            onCancelTest();
          } else if (current?.state === "error") {
            onError(current.error, `${modelId} 连接失败`);
          } else {
            onTestModel(modelId);
          }
        });
        actions.append(testBtn);

        if (editable) {
          const removeBtn = el("button", "provider-icon-btn is-danger");
          removeBtn.type = "button";
          removeBtn.setAttribute("aria-label", `移除 ${modelId}`);
          removeBtn.append(createIcon(document, "x"));
          removeBtn.addEventListener("click", () => {
            disposeWaveController(provider.id, modelId);
            profileRef.models = profileRef.models.filter((m) => m !== modelId);
            testMap?.delete(modelId);
            renderAll();
            commitWrite();
          });
          actions.append(removeBtn);
        }

        row.append(main, actions);
        listBox.append(row);

        const rowView = { row, actions, testBtn, probeTrack: null, morphIcon: null, checkRevealed: false };
        rowViews.set(modelId, rowView);
        applyRowProbeState(provider.id, modelId, rowView, entry, anyWorking);
      }
    };

    if (filterInput) {
      filterInput.addEventListener("input", () => {
        state.modelFilter = filterInput.value;
        renderRows();
      });
    }

    renderRows();
    section.append(listBox);

    if (editable) {
      const addRow = el("div", "provider-model-add");
      const input = el("input");
      input.type = "text";
      input.placeholder = "手动添加模型 ID";
      const addModelBtn = el("button", "secondary-button compact-button", "添加");
      addModelBtn.type = "button";

      const commitManual = () => {
        const val = input.value.trim();
        if (!val) return;
        if (profileRef.models.includes(val)) {
          notify("该模型已存在。", "info");
          input.select();
          return;
        }
        profileRef.models.push(val);
        input.value = "";
        renderAll();
        commitWrite();
      };
      addModelBtn.addEventListener("click", commitManual);
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          commitManual();
        }
      });
      addRow.append(input, addModelBtn);
      section.append(addRow);
    }

    return section;
  }

  function renderApiDetail(profile, providerView) {
    // 1. Header（对齐 #pluginDetail 的简洁摘要行）
    const head = el("header", "provider-detail-head");
    const identity = el("div", "provider-detail-identity");
    const titleEl = el("h2", "provider-detail-title", profile.alias || profile.id);
    identity.append(createAvatar(providerView), titleEl);

    const headActions = el("div", "provider-head-actions");
    const isWorking = hasActiveTest(profile.id);
    const testTopBtn = el("button", "secondary-button", isWorking ? "取消测试" : "测试连接");
    testTopBtn.type = "button";
    testTopBtn.addEventListener("click", () => {
      if (hasActiveTest(profile.id)) void cancelProbeFor(profile.id);
      else void runModelProbe(profile, profile.models[0] || "");
    });
    headActions.append(testTopBtn);
    head.append(identity, headActions);

    // 2. Form Rows（直接使用 Sakura 标准 .form-row，不套外框）
    const form = el("div", "provider-form");

    // 名称
    const aliasRow = el("div", "form-row");
    const aliasLabel = el("label", "", "名称");
    aliasLabel.htmlFor = `redesign-alias-${profile.id}`;
    const aliasInput = el("input");
    aliasInput.id = aliasLabel.htmlFor;
    aliasInput.type = "text";
    aliasInput.value = profile.alias;
    aliasInput.addEventListener("input", () => {
      profile.alias = aliasInput.value;
      titleEl.textContent = aliasInput.value.trim() || profile.id;
      renderList();
      commitWrite();
    });
    aliasRow.append(aliasLabel, aliasInput);

    // API 地址
    const urlRow = el("div", "form-row");
    const urlLabel = el("label", "", "API 地址");
    urlLabel.htmlFor = `redesign-url-${profile.id}`;
    const urlInput = el("input");
    urlInput.id = urlLabel.htmlFor;
    urlInput.type = "text";
    urlInput.value = profile.base_url;
    urlInput.placeholder = "通常以 /v1 结尾";
    urlInput.addEventListener("input", () => {
      profile.base_url = urlInput.value;
      urlInput.classList.remove("is-invalid");
      clearProfileTests(profile.id);
      renderList();
      commitWrite();
    });
    urlRow.append(urlLabel, urlInput);

    // API Key（默认密文保留完整密钥，点击眼睛切换明文查看）
    const keyRow = el("div", "form-row");
    const keyLabel = el("label", "", "API Key");
    keyLabel.htmlFor = `redesign-key-${profile.id}`;
    const secretWrap = el("div", "provider-secret-wrap");
    const keyInput = el("input");
    keyInput.id = keyLabel.htmlFor;
    keyInput.type = secretVisible ? "text" : "password";
    keyInput.value = profile.api_key;
    keyInput.placeholder = "填写 API Key";

    const eyeBtn = el("button", "provider-secret-toggle");
    eyeBtn.type = "button";
    eyeBtn.setAttribute("aria-label", secretVisible ? "隐藏 API Key" : "显示 API Key");
    eyeBtn.append(createEyeSvg(document, secretVisible));
    eyeBtn.addEventListener("click", () => {
      secretVisible = !secretVisible;
      keyInput.type = secretVisible ? "text" : "password";
      eyeBtn.setAttribute("aria-label", secretVisible ? "隐藏 API Key" : "显示 API Key");
      eyeBtn.replaceChildren(createEyeSvg(document, secretVisible));
    });
    secretWrap.append(keyInput, eyeBtn);

    keyInput.addEventListener("input", () => {
      profile.api_key = keyInput.value;
      const hasKey = Boolean(keyInput.value.trim());
      profile.configured = hasKey;
      profile.credential_action = hasKey ? "replace" : "clear";
      clearProfileTests(profile.id);
      commitWrite();
    });

    keyRow.append(keyLabel, secretWrap);
    form.append(aliasRow, urlRow, keyRow);

    // 3. 模型列表区
    const modelsSection = renderModelsSection(providerView, {
      editable: true,
      profileRef: profile,
      testTopBtn,
      onTestModel: (modelId) => void runModelProbe(profile, modelId),
      onCancelTest: () => void cancelProbeFor(profile.id),
    });

    // 4. Footer（对齐 .plugin-detail-footer）
    const foot = el("footer", "provider-detail-foot");
    if (profile.models.length > 1) {
      const clearModelsBtn = el("button", "secondary-button provider-clear-models", "清空模型");
      clearModelsBtn.type = "button";
      clearModelsBtn.addEventListener("click", async () => {
        const ok = await confirmAction(`确定移除「${profile.alias || profile.id}」下的全部 ${profile.models.length} 个模型吗？`);
        if (!ok || disposed) return;
        profile.models = [];
        clearProfileTests(profile.id);
        renderAll();
        commitWrite();
      });
      foot.append(clearModelsBtn);
    }

    const deleteBtn = el("button", "danger-button", "删除模型服务");
    deleteBtn.type = "button";
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction(`确定删除「${profile.alias || profile.id}」吗？`);
      if (!ok || disposed) return;
      state.profiles = state.profiles.filter((item) => item.id !== profile.id);
      clearProfileTests(profile.id);
      if (state.selectedId === profile.id) {
        state.selectedId = allProviders()[0]?.id || "";
      }
      renderAll();
      commitWrite();
    });
    foot.append(deleteBtn);

    detailShell.append(head, form, modelsSection, foot);
  }

  function renderCodexDetail(provider) {
    const svc = provider.service;
    const loggedIn = svc.account.loggedIn;
    const pendingOp = svc.pendingOperation;

    // 1. Header
    const head = el("header", "provider-detail-head");
    const identity = el("div", "provider-detail-identity");
    identity.append(createAvatar(provider), el("h2", "provider-detail-title", svc.label));

    const headActions = el("div", "provider-head-actions");
    if (!loggedIn) {
      const loginBtn = el("button", "primary-button");
      loginBtn.type = "button";
      const loggingIn = pendingOp === "login";
      loginBtn.textContent = loggingIn ? "取消登录" : "登录 ChatGPT";
      loginBtn.addEventListener("click", () => {
        if (loggingIn) void codexController.cancel(svc.profileId);
        else void codexController.start(svc.profileId, "login");
      });
      headActions.append(loginBtn);
    } else {
      const syncBtn = el("button", "secondary-button", pendingOp === "sync_models" ? "取消刷新" : "刷新模型");
      syncBtn.type = "button";
      syncBtn.addEventListener("click", () => {
        if (pendingOp === "sync_models") void codexController.cancel(svc.profileId);
        else void codexController.start(svc.profileId, "sync_models");
      });
      const logoutBtn = el("button", "secondary-button", "退出登录");
      logoutBtn.type = "button";
      logoutBtn.disabled = Boolean(pendingOp);
      logoutBtn.addEventListener("click", () => void codexController.logout(svc.profileId));
      headActions.append(syncBtn, logoutBtn);
    }
    head.append(identity, headActions);

    // 2. Form Rows（与 API 服务相同层级）
    const form = el("div", "provider-form");

    const nameRow = el("div", "form-row");
    const nameLabel = el("label", "", "名称");
    nameLabel.htmlFor = `redesign-codex-name-${svc.profileId}`;
    const nameInput = el("input");
    nameInput.id = nameLabel.htmlFor;
    nameInput.type = "text";
    nameInput.value = svc.label;
    nameInput.addEventListener("change", () => {
      const next = nameInput.value.trim();
      if (!next) {
        nameInput.value = svc.label;
        return;
      }
      void codexController.rename(svc.profileId, next);
    });
    nameRow.append(nameLabel, nameInput);
    form.append(nameRow);

    const accRow = el("div", "form-row");
    accRow.append(el("label", "", "账号"));
    const accValue = el("div", "provider-account-value");
    accValue.append(
      el("span", "", loggedIn ? [svc.account.email || "已登录 ChatGPT", svc.account.plan].filter(Boolean).join(" · ") : "未登录"),
    );
    if (loggedIn) {
      const refreshUsageBtn = el("button", "provider-text-action", pendingOp === "usage" ? "取消刷新" : "刷新额度");
      refreshUsageBtn.type = "button";
      refreshUsageBtn.addEventListener("click", () => {
        if (pendingOp === "usage") void codexController.cancel(svc.profileId);
        else void codexController.start(svc.profileId, "usage");
      });
      accValue.append(refreshUsageBtn);
    }
    accRow.append(accValue);
    form.append(accRow);

    if (svc.operationResult?.userCode && pendingOp === "login") {
      const loginBox = el("div", "provider-login");
      loginBox.append(el("p", "detail-desc", "复制代码并在浏览器中完成登录。"));
      const codeRow = el("div", "provider-login-code");
      const codeEl = el("code", "", svc.operationResult.userCode);
      const copyBtn = el("button", "secondary-button compact-button", "复制代码");
      copyBtn.type = "button";
      copyBtn.addEventListener("click", async () => {
        await window.navigator.clipboard?.writeText(codeEl.textContent).catch(() => {});
        copyBtn.textContent = "已复制";
      });
      codeRow.append(codeEl, copyBtn);
      const openLoginBtn = el("button", "secondary-button", "打开登录页面");
      openLoginBtn.type = "button";
      openLoginBtn.addEventListener("click", () => notify("已打开授权页面。", "info"));
      loginBox.append(codeRow, openLoginBtn);
      form.append(loginBox);
    } else if (loggedIn && (svc.usage?.windows || []).length) {
      const quotaRow = el("div", "form-row align-start");
      quotaRow.append(el("label", "", "额度"));
      const quotaGrid = el("div", "provider-quota-inline");
      for (const item of svc.usage.windows) {
        const w = el("div", "codex-quota-window");
        const heading = el("div", "codex-quota-heading");
        const remaining = item.usedPercent == null ? null : Math.max(0, Math.min(100, 100 - item.usedPercent));
        heading.append(
          el("span", "", item.label),
          el("strong", "", remaining == null ? "—" : `剩余 ${Number(remaining.toFixed(1))}%`),
        );
        w.append(heading);
        if (remaining != null) {
          const meter = document.createElement("progress");
          meter.max = 100;
          meter.value = remaining;
          w.append(meter);
        }
        if (item.resetsAt) {
          const dt = new Date(item.resetsAt * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
          w.append(el("span", "codex-quota-reset", `${dt} 重置`));
        }
        quotaGrid.append(w);
      }
      quotaRow.append(quotaGrid);
      form.append(quotaRow);
    }

    // 3. Models Section
    const modelsSection = renderModelsSection(provider, {
      editable: false,
      profileRef: null,
      onTestModel: (modelId) => void runCodexTest(provider, modelId),
      onCancelTest: () => void cancelProbeFor(provider.id),
    });

    // 4. Footer
    const foot = el("footer", "provider-detail-foot");
    const deleteBtn = el("button", "danger-button", "删除模型服务");
    deleteBtn.type = "button";
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction(`确定删除「${svc.label}」吗？`);
      if (!ok || disposed) return;
      await codexController.remove(svc.profileId);
      if (state.selectedId === provider.id) {
        state.selectedId = allProviders()[0]?.id || "";
      }
      renderAll();
    });
    foot.append(deleteBtn);

    detailShell.append(head, form, modelsSection, foot);
  }

  async function cancelProbeFor(profileId) {
    const map = state.modelTestStates.get(profileId);
    if (map) {
      for (const [modelId, entry] of map) {
        if (entry.state === "working" || entry.state === "converging") {
          disposeWaveController(profileId, modelId);
          map.delete(modelId);
        }
      }
    }
    if (!syncLiveProbeStates(profileId)) renderDetail();
    await cancel();
  }

  async function executeModelTest(providerId, targetModel, runner) {
    const testMap = getModelTestMap(providerId);
    disposeWaveController(providerId, targetModel);
    const liveRow = liveDetail?.providerId === providerId ? liveDetail.rows.get(targetModel) : null;
    if (liveRow?.probeTrack) {
      liveRow.probeTrack.remove();
      liveRow.probeTrack = null;
    }

    const prevState = testMap.get(targetModel)?.state;
    const entry = { state: "working", startedAt: window.performance.now() };
    testMap.set(targetModel, entry);

    if (liveRow && liveDetail?.providerId === providerId) {
      if (liveDetail.testTopBtn) liveDetail.testTopBtn.textContent = "取消测试";
      for (const [id, view] of liveDetail.rows) {
        applyRowProbeState(providerId, id, view, testMap.get(id), true, {
          animateSend: id === targetModel && !prevState,
        });
      }
    } else {
      renderDetail();
    }

    try {
      await runner();
      if (disposed || testMap.get(targetModel) !== entry || entry.state !== "working") return;

      const ctrl = activeWaveControllers.get(waveKey(providerId, targetModel));
      const rowView = liveDetail?.providerId === providerId ? liveDetail.rows.get(targetModel) : null;
      if (ctrl && rowView) {
        entry.state = "converging";
        if (liveDetail.testTopBtn) liveDetail.testTopBtn.textContent = "测试连接";
        for (const [id, view] of liveDetail.rows) {
          if (id !== targetModel) {
            applyRowProbeState(providerId, id, view, testMap.get(id), false);
          }
        }
        ctrl.converge({
          onRevealCheck() {
            if (disposed || testMap.get(targetModel) !== entry || entry.state !== "converging") return;
            rowView.checkRevealed = true;
            applyRowProbeState(providerId, targetModel, rowView, entry, hasActiveTest(providerId), {
              animateReveal: true,
            });
          },
          onComplete() {
            activeWaveControllers.delete(waveKey(providerId, targetModel));
            if (disposed || testMap.get(targetModel) !== entry || entry.state !== "converging") return;
            entry.state = "ready";
            rowView.checkRevealed = false;
            applyRowProbeState(providerId, targetModel, rowView, entry, hasActiveTest(providerId));
          },
        });
      } else {
        disposeWaveController(providerId, targetModel);
        testMap.set(targetModel, { state: "ready" });
        if (!syncLiveProbeStates(providerId)) renderDetail();
      }
    } catch (error) {
      if (disposed || String(error).includes("MODEL_PROBE_CANCELLED")) return;
      if (testMap.get(targetModel) !== entry || entry.state !== "working") return;
      disposeWaveController(providerId, targetModel);
      testMap.set(targetModel, { state: "error", error });
      if (!syncLiveProbeStates(providerId)) renderDetail();
    }
  }

  async function runModelProbe(profile, modelId) {
    const baseUrl = (profile.base_url || "").trim();
    if (!baseUrl) {
      const urlInput = detailShell.querySelector(`#redesign-url-${profile.id}`);
      urlInput?.classList.add("is-invalid");
      urlInput?.focus();
      notify("请先填写 API 地址。", "error");
      return;
    }
    const targetModel = modelId || profile.models[0] || "";
    if (!targetModel) {
      notify("请先添加至少一个模型再测试。", "error");
      return;
    }
    await executeModelTest(profile.id, targetModel, () =>
      probe("test_connection", {
        profileId: profile.id,
        base_url: baseUrl,
        modelId: targetModel,
        timeout_seconds: profile.timeout_seconds || 60,
        credential: {
          action: profile.credential_action || "keep",
          value: profile.credential_action === "replace" ? profile.api_key.trim() : "",
        },
      }),
    );
  }

  async function runCodexTest(provider, modelId) {
    await executeModelTest(provider.id, modelId, () =>
      codexController.testModel(provider.service.profileId, modelId),
    );
  }

  async function handleFetchModels(profile, button) {
    const baseUrl = (profile.base_url || "").trim();
    if (!baseUrl) {
      const urlInput = detailShell.querySelector(`#redesign-url-${profile.id}`);
      urlInput?.classList.add("is-invalid");
      urlInput?.focus();
      notify("请先填写 API 地址。", "error");
      return;
    }
    const prev = button.textContent;
    button.disabled = true;
    button.textContent = "正在获取…";
    try {
      const res = await probe("list_models", {
        profileId: profile.id,
        base_url: baseUrl,
        modelId: "",
        timeout_seconds: profile.timeout_seconds || 60,
        credential: {
          action: profile.credential_action || "keep",
          value: profile.credential_action === "replace" ? profile.api_key.trim() : "",
        },
      });
      if (disposed) return;
      const items = res?.models || [];
      if (!items.length) {
        notify("未获取到模型列表。", "info");
        return;
      }
      openModelPicker(profile, items);
    } catch (error) {
      if (!disposed && !String(error).includes("MODEL_PROBE_CANCELLED")) {
        onError(error, "获取模型列表失败");
      }
    } finally {
      if (!disposed) {
        button.disabled = false;
        button.textContent = prev;
      }
    }
  }

  function openModelPicker(profile, discovered) {
    const existing = new Set(profile.models || []);
    const overlay = el("div", "confirm-overlay");
    const dialog = el("div", "confirm-dialog model-picker-dialog provider-redesign-picker");
    const heading = el("h2", "", `获取到 ${discovered.length} 个模型`);

    const search = el("input", "model-picker-search");
    search.type = "search";
    search.placeholder = "筛选模型";

    const toolbar = el("div", "model-picker-toolbar");
    const list = el("div", "model-picker-list");

    const close = () => {
      overlays.delete(overlay);
      overlay.remove();
    };

    const rows = discovered.map((entry) => {
      const modelId = typeof entry === "string" ? entry : entry.modelId;
      const item = el("label", "check-control model-picker-item");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.value = modelId;
      checkbox.checked = !existing.has(modelId);
      const name = el("span", "", existing.has(modelId) ? `${modelId}（已添加）` : modelId);
      item.append(checkbox, name);
      list.append(item);
      return { item, checkbox, modelId };
    });

    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      for (const r of rows) {
        r.item.hidden = Boolean(q && !r.modelId.toLowerCase().includes(q));
      }
    });

    const mkBtn = (text, cls, fn) => {
      const b = el("button", cls, text);
      b.type = "button";
      b.addEventListener("click", fn);
      return b;
    };

    const setVisible = (pred) => {
      for (const r of rows) {
        if (!r.item.hidden) r.checkbox.checked = pred(r);
      }
    };

    toolbar.append(
      mkBtn("全选", "secondary-button compact-button", () => setVisible(() => true)),
      mkBtn("只选新增", "secondary-button compact-button", () => setVisible((r) => !existing.has(r.modelId))),
      mkBtn("全不选", "secondary-button compact-button", () => setVisible(() => false)),
    );

    const actions = el("div", "confirm-actions");
    actions.append(
      mkBtn("取消", "secondary-button", close),
      mkBtn("添加", "primary-button", () => {
        let added = 0;
        for (const r of rows) {
          if (!r.checkbox.checked) continue;
          if (!profile.models.includes(r.modelId)) {
            profile.models.push(r.modelId);
            added += 1;
          }
        }
        close();
        if (added) {
          renderAll();
          commitWrite();
          notify(`已添加 ${added} 个模型。`, "success");
        } else {
          notify("没有新增模型。", "info");
        }
      }),
    );

    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    dialog.append(heading, search, toolbar, list, actions);
    overlay.append(dialog);
    overlays.add(overlay);
    document.body.append(overlay);
  }

  function addApiProfile(preset) {
    const profile = {
      id: `profile-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      alias: preset?.label || "新模型服务",
      base_url: preset?.base_url || "",
      api_key: "",
      configured: false,
      credential_action: "keep",
      timeout_seconds: 60,
      createdAt: nextCreatedAt(),
      models: [],
    };
    state.profiles.push(profile);
    state.search = "";
    searchInput.value = "";
    selectProvider(profile.id);
    commitWrite();
  }

  function openAddDialog() {
    const overlay = el("div", "confirm-overlay");
    const dialog = el("div", "confirm-dialog provider-add-dialog");
    const heading = el("h2", "", "添加模型服务");
    const grid = el("div", "provider-preset-grid");
    const close = () => {
      overlays.delete(overlay);
      overlay.remove();
    };

    for (const preset of PROVIDER_PRESETS) {
      const option = el("button", "provider-preset-option");
      option.type = "button";
      const avatar = el("span", "provider-avatar");
      applyAvatar(avatar, { iconUrl: preset.iconUrl, initial: preset.label });
      option.append(avatar, el("span", "", preset.label));
      option.addEventListener("click", () => {
        addApiProfile(preset);
        close();
      });
      grid.append(option);
    }

    const codexOpt = el("button", "provider-preset-option");
    codexOpt.type = "button";
    const codexAvatar = el("span", "provider-avatar");
    applyAvatar(codexAvatar, { iconName: "openai", initial: "OpenAI Official" });
    codexOpt.append(codexAvatar, el("span", "", "OpenAI Official"));
    codexOpt.addEventListener("click", async () => {
      codexOpt.disabled = true;
      try {
        const newId = await codexController.add(nextCreatedAt());
        state.search = "";
        searchInput.value = "";
        selectProvider(newId);
        close();
      } finally {
        codexOpt.disabled = false;
      }
    });
    grid.append(codexOpt);

    const customOpt = el("button", "provider-preset-option");
    customOpt.type = "button";
    const customAvatar = el("span", "provider-avatar is-initial");
    customAvatar.append(createIcon(document, "plus"));
    customOpt.append(customAvatar, el("span", "", "自定义"));
    customOpt.addEventListener("click", () => {
      addApiProfile(null);
      close();
    });
    grid.append(customOpt);

    const actions = el("div", "confirm-actions");
    const cancelBtn = el("button", "secondary-button", "取消");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", close);
    actions.append(cancelBtn);

    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    dialog.append(heading, grid, actions);
    overlay.append(dialog);
    overlays.add(overlay);
    document.body.append(overlay);
  }

  function renderAll() {
    if (disposed) return;
    renderList();
    renderDetail();
  }

  searchInput.addEventListener("input", () => {
    state.search = searchInput.value;
    renderList();
  });
  searchClear.addEventListener("click", () => {
    state.search = "";
    searchInput.value = "";
    renderList();
    searchInput.focus();
  });
  addBtn.addEventListener("click", openAddDialog);

  const onKeyDown = (e) => {
    if (e.key === "Escape" && overlays.size) {
      for (const item of overlays) item.remove();
      overlays.clear();
    }
  };
  document.addEventListener("keydown", onKeyDown);

  loadInitial();
  renderAll();

  return {
    element: root,
    update() {
      const current = read() || [];
      if (JSON.stringify(current) !== lastValue) {
        loadInitial();
      }
      renderAll();
    },
    updateContributions() {
      if (disposed) return;
      if (!allProviders().some((p) => p.id === state.selectedId)) {
        state.selectedId = allProviders()[0]?.id || "";
      }
      renderAll();
    },
    dispose() {
      disposed = true;
      disposeLiveRowIcons();
      disposeAllWaves();
      overlays.forEach((o) => o.remove());
      overlays.clear();
      document.removeEventListener("keydown", onKeyDown);
      root.remove();
    },
  };
}
