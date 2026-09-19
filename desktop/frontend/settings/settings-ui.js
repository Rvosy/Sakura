import { createIcon } from "../core/icons.js";

const legacyPages = { screen_awareness: "interaction", providers: "providers", model: "model", voice: "voice", memory: "memory" };
export function sectionDestination(section) {
  if (section.placement) return section.placement.pageId;
  return legacyPages[section.surface] ? `host:${legacyPages[section.surface]}` : null;
}

export function createSettingsUI({ document, showPage, createSection }) {
  const pages = new Map();
  const sections = new Map();
  const containers = new Map();
  const groups = new Map();
  const element = (tag, className, text = "") => {
    const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
  };
  const append = (parent, child, index) => {
    if (parent.children[index] !== child) parent.insertBefore(child, parent.children[index] || null);
  };
  function render(plugins) {
    const active = plugins.filter(p => p.enabled);
    const descriptors = active.flatMap(plugin => (plugin.pages || []).map(page => ({ ...page, plugin })))
      .sort((a, b) => a.order - b.order || a.pageId.localeCompare(b.pageId));
    const wantedPages = new Set(descriptors.map(p => p.pageId));
    for (const [id, page] of pages) {
      if (wantedPages.has(id)) continue;
      const selected = page.element.classList.contains("is-active"); page.element.remove(); page.button.remove(); pages.delete(id);
      if (selected) showPage("plugins");
    }
    for (const descriptor of descriptors) {
      let page = pages.get(descriptor.pageId);
      if (!page) {
        const button = element("button", "nav-item"); button.type = "button"; button.dataset.page = descriptor.pageId;
        const icon = element("span", "nav-item-icon"); icon.append(createIcon(document, descriptor.icon));
        const label = element("span", "nav-item-label", descriptor.title); button.append(icon, label);
        button.addEventListener("click", () => showPage(descriptor.pageId));
        const content = element("section", "settings-page"); content.id = `page-${descriptor.pageId}`; content.hidden = true;
        content.dataset.pageTitle = descriptor.title;
        document.querySelector(".page-scroll")?.append(content);
        const empty = element("p", "empty-state", "暂无设置"); content.append(empty);
        page = { button, element: content, empty }; pages.set(descriptor.pageId, page);
      }
      page.element.dataset.pageTitle = descriptor.title;
      page.button.querySelector(".nav-item-label").textContent = descriptor.title;
      document.getElementById(`navgrp-${descriptor.group}`)?.parentElement.append(page.button);
    }
    const contributions = active.flatMap(plugin => (plugin.settings || []).filter(section => {
      if (section.placement) return true;
      // Legacy voice/memory have their established controllers and layouts.
      return ["providers", "model", "screen_awareness"].includes(section.surface);
    }).map(section => ({ plugin, section, destination: sectionDestination(section) })))
      .sort((a, b) => (a.section.placement?.order ?? a.section.order ?? 100) - (b.section.placement?.order ?? b.section.order ?? 100)
        || `${a.plugin.plugin_id}:${a.section.section_id}`.localeCompare(`${b.plugin.plugin_id}:${b.section.section_id}`));
    const live = new Set(); const liveGroups = new Set(); const offsets = new Map();
    for (const { plugin, section, destination } of contributions) {
      if (section.placement?.available === false) continue;
      const pageId = destination?.startsWith("host:") ? destination.slice(5) : destination;
      const legacyRoot = { interaction: "screenAwarenessSurface", providers: "modelProviderSurface", model: "modelSettingsSurface" };
      const page = document.getElementById(`page-${pageId}`) || document.getElementById(legacyRoot[pageId]);
      if (!page) continue;
      const region = section.placement?.region || "content";
      const containerKey = `${destination}/${region}`;
      let container = containers.get(containerKey);
      if (!container || !page.contains(container)) {
        container = element("div", "settings-contributions"); container.dataset.settingsRegion = region;
        (region === "content" && document.getElementById(legacyRoot[pageId]) || page).append(container); containers.set(containerKey, container);
      }
      const key = `${plugin.id}:${section.section_id}`; live.add(key);
      let entry = sections.get(key);
      // Values do not define a component's identity; status refreshes must not steal focus.
      const schema = JSON.stringify([section.instance_id, section.presentation, section.fields.map(({ value, ...f }) => f), section.actions, section.collections, section.reason_code]);
      if (!entry || entry.schema !== schema) {
        entry?.component.dispose();
        const component = createSection(plugin, section);
        entry = { schema, component }; sections.set(key, entry);
      }
      let parent = container;
      if (section.presentation?.component !== "connection-editor") {
        const groupKey = `${containerKey}/${section.presentation?.group || key}`; liveGroups.add(groupKey);
        let group = groups.get(groupKey);
        if (!group) {
          const collapsible = section.presentation?.collapsible;
          group = element(collapsible ? "details" : "fieldset", collapsible ? "settings-group advanced-section" : "settings-group");
          if (section.presentation?.alignedUnits) group.classList.add("aligned-controls");
          group.append(element(collapsible ? "summary" : "legend", "", section.title)); groups.set(groupKey, group);
        }
        if (!offsets.has(groupKey)) {
          const index = offsets.get(containerKey) || 0; append(container, group, index); offsets.set(containerKey, index + 1); offsets.set(groupKey, 1);
        }
        parent = group;
        append(parent, entry.component.element, offsets.get(groupKey)); offsets.set(groupKey, offsets.get(groupKey) + 1);
      } else {
        const index = offsets.get(containerKey) || 0; append(parent, entry.component.element, index); offsets.set(containerKey, index + 1);
      }
      entry.component.update?.();
      if (!entry.mounted) { entry.component.mounted?.(); entry.mounted = true; }
    }
    for (const [key, entry] of sections) if (!live.has(key)) { entry.component.dispose(); sections.delete(key); }
    for (const [key, group] of groups) if (!liveGroups.has(key)) { group.remove(); groups.delete(key); }
    for (const [key, container] of containers) if (!container.childElementCount) { container.remove(); containers.delete(key); }
    for (const page of pages.values()) page.empty.hidden = Boolean(page.element.querySelector(".settings-contributions"));
  }
  return { render, dispose() {
    sections.forEach(entry => entry.component.dispose()); sections.clear();
    containers.forEach(node => node.remove()); containers.clear(); groups.clear();
    pages.forEach(page => { page.element.remove(); page.button.remove(); }); pages.clear();
  } };
}
