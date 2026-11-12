const MOTION = { duration: 420, easing: "cubic-bezier(.22, 1, .36, 1)" };

// 相册只管理排列和手势，图片读取与释放由 history-images 负责。
export function createImageAlbum({ document, images, makeThumbnail, state, scroll, expandable = true }) {
  state.index %= images.length;
  const group = document.createElement("div");
  group.className = "history-image-gallery image-album";
  group.setAttribute("role", "group");
  group.setAttribute("aria-label", `${images.length} 张图片`);
  const rail = document.createElement("div");
  rail.className = "image-album-controls";
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "image-album-toggle";
  rail.append(toggle);
  if (expandable) group.append(rail);
  const cards = new Map(images.map((image, index) => {
    const card = makeThumbnail(image, index);
    card.classList.add("image-album-card");
    card.hidden = true;
    group.append(card);
    return [index, card];
  }));
  const animations = new Set();
  let disposed = false;
  let drag = null;
  let dragged = false;
  let previousWidth = 0;

  function dimensions(image, width, height) {
    const ratio = image.width && image.height ? image.width / image.height : 4 / 3;
    return { width: Math.min(width, height * ratio), height: Math.min(height, width / ratio) };
  }

  function cancelMotion() {
    for (const animation of animations) animation.cancel();
    animations.clear();
  }

  function animate(element, frames) {
    const animation = element.animate(frames, MOTION);
    animations.add(animation);
    animation.finished.then(() => animations.delete(animation), () => animations.delete(animation));
  }

  function transform(target) {
    return `translate3d(${target.x}px, ${target.y}px, 0) rotate(${target.rotation}deg) scale(${target.scale})`;
  }

  function arrangement(selected = state.index) {
    const width = group.clientWidth;
    const expanded = state.expanded;
    const cardWidth = Math.min(280, width - (expandable ? 68 : 32));
    const sizes = images.map(image => dimensions(image, cardWidth, 280));
    const stackHeight = Math.max(...sizes.map(size => size.height), 48);
    const size = sizes[selected];
    const slotLeft = expandable ? width - cardWidth - 16 : (width - cardWidth) / 2;
    const front = { x: slotLeft + (cardWidth - size.width) / 2, y: (stackHeight - size.height) / 2, ...size };
    const targets = new Map();
    let height = 0;
    for (let offset = 0; offset < images.length; offset += 1) {
      const index = expanded ? offset : (selected + offset) % images.length;
      // 深处的卡片共用后排位置，翻过的图片仍留在堆叠里。
      const depth = Math.min(offset, 2);
      const item = expanded ? dimensions(images[index], Math.min(320, width - 68), 320)
        : sizes[index];
      const scale = expanded || offset === 0 ? 1 : .94;
      const target = expanded
        ? { x: width - item.width, y: height, ...item, scale, rotation: 0 }
        : { x: front.x + (front.width - item.width * scale) / 2 + depth * 8,
          y: front.y + (front.height - item.height * scale) / 2 + depth * 3,
          ...item, scale, rotation: depth * 1.5 };
      target.z = images.length - offset;
      targets.set(index, target);
      if (expanded) height += item.height + 10;
    }
    return { targets, cardWidth, width, stackHeight, height: expanded ? height - 10 : stackHeight + 12 };
  }

  function sideTransform(old, distance) {
    const progress = Math.min(1, Math.abs(distance) / old.width);
    return `translateX(${distance}px) ${old.transform} rotate(${Math.sign(distance) * 12 * progress}deg) scale(${1 - .08 * progress})`;
  }

  function snapshot() {
    return new Map([...cards].filter(([, card]) => !card.hidden).map(([index, card]) => {
      const style = getComputedStyle(card);
      return [index, { transform: style.transform, width: parseFloat(style.width), height: parseFloat(style.height) }];
    }));
  }

  function layout(motion = false, departing = null, direction = 0) {
    if (!group.clientWidth || disposed) return;
    previousWidth = group.clientWidth;
    const before = snapshot();
    const oldHeight = group.getBoundingClientRect().height;
    cancelMotion();
    drag = null;
    const { targets, cardWidth, width, stackHeight, height } = arrangement();
    const expanded = state.expanded;
    group.classList.toggle("is-expanded", expanded);
    group.style.height = `${height}px`;
    toggle.textContent = expanded ? "收起" : `展开 ${images.length}`;
    toggle.setAttribute("aria-expanded", String(expanded));
    toggle.setAttribute("aria-label", expanded ? "收起图片" : `展开 ${images.length} 张图片`);
    rail.style.top = `${Math.max(0, (expanded ? targets.get(0).height : stackHeight) / 2 - 15)}px`;
    rail.style.right = `${Math.min(width - 48, (expanded ? targets.get(0).width : cardWidth + 16) + 12)}px`;
    if (motion && oldHeight !== height) {
      animate(group, [{ height: `${oldHeight}px` }, { height: `${height}px` }]);
    }
    for (const [index, target] of targets) {
      const card = cards.get(index);
      card.hidden = false;
      card.style.width = `${target.width}px`;
      card.style.height = `${target.height}px`;
      card.style.zIndex = String(target.z);
      const end = transform(target);
      card.style.transform = end;
      const interactive = expanded || index === state.index;
      card.tabIndex = interactive ? 0 : -1;
      card.setAttribute("aria-hidden", String(!interactive));
      card.style.pointerEvents = interactive ? "auto" : "none";
      const old = before.get(index);
      if (motion && old) {
        const start = `${old.transform} scale(${old.width / target.width}, ${old.height / target.height})`;
        if (index === departing) {
          const side = sideTransform({ ...old, transform: start }, -direction * old.width * .9);
          animate(card, [
            { transform: start, zIndex: images.length + 1, offset: 0 },
            { transform: side, zIndex: images.length + 1, offset: .45 },
            { transform: side, zIndex: target.z, offset: .451 },
            { transform: end, zIndex: target.z, offset: 1 },
          ]);
        } else {
          animate(card, [{ transform: start }, { transform: end }]);
        }
      }
    }
  }

  function turn(direction) {
    if (state.expanded) return;
    const departing = state.index;
    state.index = (state.index + direction + images.length) % images.length;
    layout(true, departing, direction);
  }
  toggle.addEventListener("click", () => {
    state.expanded = !state.expanded;
    if (!state.expanded && group.getBoundingClientRect().top < scroll.getBoundingClientRect().top) {
      scroll.scrollTop += group.getBoundingClientRect().top - scroll.getBoundingClientRect().top - 12;
    }
    layout(true);
  });
  group.addEventListener("keydown", event => {
    if (state.expanded || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const onCard = event.target.classList.contains("image-album-card");
    turn(event.key === "ArrowRight" ? 1 : -1);
    if (onCard) cards.get(state.index)?.focus({ preventScroll: true });
  });
  group.addEventListener("pointerdown", event => {
    if (state.expanded || event.button !== 0 || !event.target.closest(".image-album-card")) return;
    dragged = false;
    drag = { x: event.clientX, y: event.clientY, dx: 0, before: snapshot() };
  });
  group.addEventListener("pointermove", event => {
    if (!drag) return;
    const dx = event.clientX - drag.x;
    if (!dragged && (Math.abs(dx) < 6 || Math.abs(dx) < Math.abs(event.clientY - drag.y))) return;
    if (!dragged) { cancelMotion(); group.setPointerCapture(event.pointerId); }
    dragged = true;
    drag.dx = dx;
    const front = drag.before.get(state.index);
    const progress = Math.min(1, Math.abs(dx) / (front.width * .85));
    const direction = dx < 0 ? 1 : -1;
    const next = (state.index + direction + images.length) % images.length;
    const { targets } = arrangement(next);
    const passed = Math.abs(dx) >= front.width * .55;
    for (const [index, card] of cards) {
      const old = drag.before.get(index);
      const target = targets.get(index);
      if (index === state.index) {
        card.style.transform = sideTransform(old, dx);
        card.style.zIndex = String(passed ? target.z : images.length + 1);
      } else if (index === next) {
        const from = new DOMMatrix(old.transform);
        const to = new DOMMatrix(transform(target));
        const matrix = ["a", "b", "c", "d", "e", "f"].map(key => from[key] + (to[key] - from[key]) * progress);
        card.style.transform = `matrix(${matrix.join(",")})`;
        card.style.zIndex = String(target.z);
      } else {
        card.style.transform = old.transform;
        card.style.zIndex = String(target.z);
      }
    }
    event.preventDefault();
  });
  function endDrag(cancelled) {
    if (!drag) return;
    const { dx, before } = drag;
    drag = null;
    if (!dragged) return;
    if (!cancelled && Math.abs(dx) >= before.get(state.index).width * .55) {
      state.index = (state.index + (dx < 0 ? 1 : -1) + images.length) % images.length;
    }
    layout(true);
  }
  group.addEventListener("pointerup", () => endDrag(false));
  group.addEventListener("pointercancel", () => endDrag(true));
  group.addEventListener("lostpointercapture", () => endDrag(true));
  group.addEventListener("click", event => {
    if (!dragged) return;
    dragged = false;
    event.preventDefault();
    event.stopPropagation();
  }, true);
  const resize = new ResizeObserver(() => {
    if (group.clientWidth === previousWidth) return;
    layout();
  });
  resize.observe(group);
  return { element: group, layout, dispose() {
    disposed = true;
    resize.disconnect();
    cancelMotion();
    for (const card of cards.values()) card.disposeThumbnail();
    cards.clear();
  } };
}
