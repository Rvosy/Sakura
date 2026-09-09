// Shared by pet, settings and studio. Keep help outside scroll/transform containers.
export function installTooltips(doc = document) {
  const win = doc.defaultView;
  const tip = doc.createElement("div");
  tip.id = "sakura-tooltip";
  tip.className = "sakura-tooltip";
  tip.dataset.interactive = "true";
  tip.setAttribute("role", "tooltip");
  tip.setAttribute("popover", "manual");
  tip.hidden = true;
  doc.body.append(tip);
  let anchor = null;
  let timer;
  let pinned = false;
  let publishedBounds = null;

  function publishBounds(bounds) {
    if (bounds === null && publishedBounds === null) return;
    if (bounds && publishedBounds && bounds.every((value, index) => value === publishedBounds[index])) return;
    publishedBounds = bounds;
    doc.dispatchEvent(new win.CustomEvent("sakura-tooltip-bounds", { detail: bounds }));
  }

  function describe(add) {
    if (!anchor) return;
    const ids = new Set((anchor.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    if (add) ids.add(tip.id);
    else ids.delete(tip.id);
    if (ids.size) anchor.setAttribute("aria-describedby", [...ids].join(" "));
    else anchor.removeAttribute("aria-describedby");
  }

  function hide() {
    win.clearTimeout(timer);
    observer.disconnect();
    describe(false);
    if (tip.matches(":popover-open")) tip.hidePopover();
    tip.hidden = true;
    publishBounds(null);
    anchor = null;
    pinned = false;
  }

  function position() {
    const rect = anchor.getBoundingClientRect();
    const width = tip.offsetWidth;
    const height = tip.offsetHeight;
    const inset = 8;
    const left = Math.max(inset, Math.min(rect.left + (rect.width - width) / 2, win.innerWidth - width - inset));
    const above = rect.top - height - inset;
    const top = above >= inset ? above : Math.min(rect.bottom + inset, win.innerHeight - height - inset);
    tip.style.left = `${left}px`;
    tip.style.top = `${Math.max(inset, top)}px`;
    // The transparent pet window must include the tooltip in its native visible/input region.
    const bounds = tip.getBoundingClientRect();
    publishBounds([bounds.x, bounds.y, bounds.width, bounds.height]);
  }

  function refresh() {
    if (!anchor?.isConnected || !anchor.dataset.tooltip?.trim() || !anchor.getClientRects().length
      || anchor.closest("[hidden], [inert]") || win.getComputedStyle(anchor).visibility === "hidden") {
      hide();
      return;
    }
    if (tip.textContent !== anchor.dataset.tooltip) tip.textContent = anchor.dataset.tooltip;
    if (!tip.hidden) position();
  }

  const observer = new win.MutationObserver(refresh);
  function show(target, delay = 400) {
    win.clearTimeout(timer);
    if (anchor === target && !tip.hidden) return;
    hide();
    anchor = target;
    observer.observe(doc.body, { subtree: true, childList: true, attributes: true,
      attributeFilter: ["data-tooltip", "hidden", "class"] });
    timer = win.setTimeout(() => {
      refresh();
      if (!anchor) return;
      (anchor.closest("dialog") || doc.body).append(tip);
      tip.hidden = false;
      tip.showPopover?.();
      position();
      describe(true);
    }, delay);
  }

  function targetOf(event) {
    return event.target.closest?.("[data-tooltip]");
  }
  function leave(event) {
    if (!anchor || pinned || anchor.contains(event.relatedTarget) || tip.contains(event.relatedTarget)) return;
    if (anchor.contains(doc.activeElement)) return;
    win.clearTimeout(timer);
    timer = win.setTimeout(hide, 120);
  }
  const listeners = [
    [doc, "pointerover", (event) => {
      if (tip.contains(event.target)) { win.clearTimeout(timer); return; }
      const target = targetOf(event);
      if (target && (!pinned || target !== anchor)) show(target);
    }],
    [doc, "pointerout", leave],
    [doc, "focusin", (event) => { const target = targetOf(event); if (target) show(target, 0); }],
    [doc, "focusout", (event) => { if (anchor?.contains(event.target)) hide(); }],
    [doc, "pointerdown", (event) => {
      if (!event.target.closest?.(".setting-help") && !tip.contains(event.target)) hide();
    }],
    [doc, "click", (event) => {
      const target = event.target.closest?.(".setting-help[data-tooltip]");
      if (!target) return;
      if (anchor === target && pinned) hide();
      else { show(target, 0); pinned = true; }
    }],
    [doc, "keydown", (event) => {
      if (event.key === "Escape" && anchor) {
        hide();
        event.preventDefault();
        event.stopPropagation();
      }
    }, true],
    [doc, "scroll", hide, true],
    [doc, "sakura-tooltip-dismiss", hide],
    [win, "resize", hide],
    [win, "blur", hide],
  ];
  for (const [source, type, listener, capture] of listeners) source.addEventListener(type, listener, capture);
  return () => {
    hide();
    for (const [source, type, listener, capture] of listeners) source.removeEventListener(type, listener, capture);
    tip.remove();
  };
}

if (typeof document !== "undefined") installTooltips();
