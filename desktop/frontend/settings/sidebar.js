const STORAGE_KEY = "sakura.settings.sidebar-collapsed";

export function installSettingsSidebar({ document, storage }) {
  const toggle = document.getElementById("sidebarToggle");
  const navigation = document.getElementById("settingsNavigation");
  let collapsed = storage.getItem(STORAGE_KEY) === "true";

  function updateHint(item) {
    if (collapsed) {
      item.dataset.tooltip = item.querySelector(".nav-item-label").textContent;
      item.dataset.tooltipSide = "right";
    } else {
      delete item.dataset.tooltip;
      delete item.dataset.tooltipSide;
    }
  }

  function render() {
    document.body.classList.toggle("is-sidebar-collapsed", collapsed);
    toggle.setAttribute("aria-expanded", String(!collapsed));
    const label = collapsed ? "展开侧栏" : "收起侧栏";
    toggle.setAttribute("aria-label", label);
    navigation.querySelectorAll(".nav-item").forEach(updateHint);
  }

  function toggleSidebar() {
    collapsed = !collapsed;
    render();
    storage.setItem(STORAGE_KEY, String(collapsed));
  }

  function showNavigationHint(event) {
    const item = event.target.closest(".nav-item");
    if (item) updateHint(item);
  }

  render();
  toggle.addEventListener("click", toggleSidebar);
  navigation.addEventListener("pointerover", showNavigationHint);
  navigation.addEventListener("focusin", showNavigationHint);
  return {
    dispose() {
      toggle.removeEventListener("click", toggleSidebar);
      navigation.removeEventListener("pointerover", showNavigationHint);
      navigation.removeEventListener("focusin", showNavigationHint);
    },
  };
}
