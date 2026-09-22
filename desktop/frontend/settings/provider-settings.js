import { createProviderModelController } from "./provider-model-runtime.js";

const emptyReference = () => ({ serviceKey: "", profileId: "", modelId: "" });

export function createProviderSettingsFeature({ document, onDirty, onError, invoke, enhanceSelect = () => {}, getProviderCatalog = () => [] }) {
  const container = document.getElementById("modelSlots");
  let snapshot = null;
  let selections = {};
  let disposed = false;
  const manual = new Map();
  const controller = createProviderModelController({ invoke,
    readDraft: () => ({ model_slots: structuredClone(selections) }),
    readProviders: () => mergedProviders(),
    applySnapshot(value, { draft } = {}) {
      snapshot = value;
      selections = Object.fromEntries(value.model_slots.map(slot => [slot.identity, { ...(draft?.model_slots?.[slot.identity] || slot.selection) }]));
      manual.clear();
      render();
    }, onDirty, onError,
  });

  function mergedProviders() {
    const drafts = getProviderCatalog();
    return [...(snapshot?.providers || []).filter(p => !drafts.some(d => d.serviceKey === p.serviceKey)), ...drafts];
  }
  function option(select, value, label) {
    const item = document.createElement("option"); item.value = value; item.textContent = label; select.append(item);
  }
  function render() {
    if (!container || !snapshot || disposed) return;
    container.textContent = "";
    if (snapshot.configuration_issue) {
      const issue = document.createElement("p"); issue.className = "error"; issue.setAttribute("role", "alert");
      issue.textContent = snapshot.configuration_issue.message; container.append(issue);
    }
    const profiles = mergedProviders().flatMap(provider => provider.profiles.map(profile => ({ ...profile, serviceKey: provider.serviceKey, providerLabel: provider.label })));
    for (const slot of snapshot.model_slots) {
      const selection = selections[slot.identity];
      const row = document.createElement("div"); row.className = "form-row model-slot-row";
      row.dataset.slot = slot.identity;
      const label = document.createElement("label"); label.className = "setting-title"; label.textContent = slot.label;
      const controls = document.createElement("div"); controls.className = "slot-controls";
      const provider = document.createElement("select"); provider.setAttribute("aria-label", `${slot.label}模型服务`);
      provider.dataset.slotProvider = slot.identity;
      option(provider, "", "请选择模型服务");
      for (const item of profiles) option(provider, JSON.stringify([item.serviceKey, item.profileId]), item.label);
      const key = selection.serviceKey ? JSON.stringify([selection.serviceKey, selection.profileId]) : "";
      if (key && !profiles.some(item => item.serviceKey === selection.serviceKey && item.profileId === selection.profileId)) option(provider, key, `${selection.profileId}（不可用）`);
      provider.value = key;
      const model = document.createElement("select"); model.setAttribute("aria-label", slot.label); model.dataset.slotModel = slot.identity;
      option(model, "", "请选择模型");
      const chosen = profiles.find(item => item.serviceKey === selection.serviceKey && item.profileId === selection.profileId);
      for (const item of chosen?.models || []) option(model, item.modelId, item.label || item.modelId);
      if (selection.modelId && !chosen?.models.some(item => item.modelId === selection.modelId)) option(model, selection.modelId, `${selection.modelId}（不可用）`);
      model.value = selection.modelId;
      label.htmlFor = `model-slot-${slot.identity}`; model.id = label.htmlFor;
      provider.addEventListener("change", () => {
        if (!provider.value) selections[slot.identity] = emptyReference();
        else {
          const [serviceKey, profileId] = JSON.parse(provider.value);
          const choice = profiles.find(item => item.serviceKey === serviceKey && item.profileId === profileId);
          selections[slot.identity] = { serviceKey, profileId, modelId: choice?.models[0]?.modelId || "" };
        }
        render(); onDirty();
      });
      model.addEventListener("change", () => { selections[slot.identity].modelId = model.value; render(); onDirty(); });
      if (!slot.required) {
        row.classList.add("has-inherit");
        const inherited = !selection.serviceKey;
        const inherit = document.createElement("label"); inherit.className = "check-control slot-inherit";
        const checkbox = document.createElement("input"); checkbox.type = "checkbox"; checkbox.checked = inherited; checkbox.dataset.slotInherit = slot.identity;
        const text = document.createElement("span"); text.textContent = "继承";
        inherit.append(checkbox, text); controls.append(inherit);
        row.classList.toggle("is-inherited", inherited);
        provider.disabled = model.disabled = inherited;
        if (inherited) {
          const chat = selections["core:chat"];
          const inheritedProfile = profiles.find(item => item.serviceKey === chat?.serviceKey && item.profileId === chat?.profileId);
          provider.value = chat?.serviceKey ? JSON.stringify([chat.serviceKey, chat.profileId]) : "";
          model.textContent = "";
          option(model, chat?.modelId || "", inheritedProfile?.models.find(item => item.modelId === chat.modelId)?.label || chat?.modelId || "请选择模型");
          model.value = chat?.modelId || "";
        }
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) { manual.set(slot.identity, { ...selections[slot.identity] }); selections[slot.identity] = emptyReference(); }
          else selections[slot.identity] = manual.get(slot.identity) || { ...selections["core:chat"] };
          render(); onDirty();
        });
      }
      controls.append(provider, model);
      row.append(label, controls); container.append(row); enhanceSelect(provider); enhanceSelect(model);
    }
  }
  return Object.freeze({ initialize: () => controller.refreshCurrent(), save: controller.save, validate: controller.validate, isDirty: controller.isDirty,
    refreshChoices: render,
    refreshCurrent: controller.refreshCurrent, rebindIdentity: controller.rebindIdentity,
    hasModelSettings: pluginId => Boolean(snapshot?.model_slots.some(slot => slot.ownerId === pluginId)),
    onPageChanged(page) { if (page === "model" && !controller.isDirty()) controller.refreshCurrent({ preserveDraft: true }).catch(onError); },
    dispose() { disposed = true; controller.dispose(); },
  });
}
