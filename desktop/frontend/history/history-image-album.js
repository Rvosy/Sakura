const MOTION = { duration: 420, easing: "cubic-bezier(.22, 1, .36, 1)" };
const TURN_DISTANCE = .6;
const INSERT_DISTANCE = .65;
const EDGE_DISTANCE = .08;

// 相册只管理排列和手势，图片读取与释放由 history-images 负责。
export function createImageAlbum({ document, images, makeThumbnail, state, scroll, expandable = true }) {
  state.index = Math.min(state.index, images.length - 1);
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

  function transform({ x, y, width, height, rotation, scale }) {
    return new DOMMatrix().translate(x + width / 2, y + height / 2)
      .rotate(rotation).scale(scale).translate(-width / 2, -height / 2).toString();
  }

  function arrangement(selected = state.index) {
    const width = group.clientWidth;
    const expanded = state.expanded;
    const cardWidth = Math.min(280, width - (expandable ? 68 : 32));
    const size = dimensions({ width: 3, height: 4 }, cardWidth, 280);
    const slotLeft = expandable ? width - cardWidth - 16 : (width - cardWidth) / 2;
    const edgePosition = selected === 0 ? -1 : selected === images.length - 1 ? 1 : 0;
    const frontX = slotLeft + (cardWidth - size.width) / 2 + edgePosition * size.width * .08;
    const targets = new Map();
    let height = 0;
    for (let index = 0; index < images.length; index += 1) {
      const distance = Math.abs(index - selected);
      const side = Math.sign(index - selected);
      // 首尾露出同侧两张，中间露出前后各一张；更远的图片叠在相邻图下面。
      const depth = Math.min(distance, edgePosition === 0 ? 1 : 2);
      const item = expanded ? dimensions(images[index], Math.min(320, width - 68), 320) : size;
      const scale = expanded ? 1 : 1 - depth * .12;
      const rotation = expanded ? 0 : side * depth * 2;
      const target = expanded
        ? { x: width - item.width, y: height, ...item, scale, rotation }
        : { x: frontX + side * depth * size.width * .12, y: 0, ...item, scale, rotation };
      target.z = images.length - distance;
      targets.set(index, target);
      if (expanded) height += item.height + 10;
    }
    return { targets, cardWidth, width, stackHeight: size.height, height: expanded ? height - 10 : size.height + 12 };
  }

  function sideTransform(old, distance) {
    const progress = Math.min(1, Math.abs(distance) / (old.width * TURN_DISTANCE));
    const centerX = old.width / 2;
    const centerY = old.height / 2;
    return `translateX(${distance}px) ${old.transform} translate(${centerX}px, ${centerY}px) rotate(${Math.sign(distance) * 8 * progress}deg) scale(${1 - .15 * progress}) translate(${-centerX}px, ${-centerY}px)`;
  }

  function interpolateTransform(start, end, progress) {
    const from = new DOMMatrix(start);
    const to = new DOMMatrix(end);
    const matrix = ["a", "b", "c", "d", "e", "f"].map(key => from[key] + (to[key] - from[key]) * progress);
    return `matrix(${matrix.join(",")})`;
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
          const side = sideTransform({ ...old, transform: start }, -direction * old.width * TURN_DISTANCE);
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

  function canTurn(direction) {
    return state.index + direction >= 0 && state.index + direction < images.length;
  }

  function turn(direction) {
    if (state.expanded) return;
    if (!canTurn(direction)) {
      const old = snapshot().get(state.index);
      cancelMotion();
      animate(cards.get(state.index), [
        { transform: old.transform },
        { transform: sideTransform(old, -direction * old.width * EDGE_DISTANCE) },
        { transform: cards.get(state.index).style.transform },
      ]);
      return;
    }
    const departing = state.index;
    state.index += direction;
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
    const direction = dx < 0 ? 1 : -1;
    const available = canTurn(direction);
    // 前段向外翻，后段反向插入；两段进度都由水平拖动距离决定。
    const threshold = front.width * TURN_DISTANCE;
    const edge = front.width * EDGE_DISTANCE;
    const distance = Math.sign(dx) * (available ? Math.min(Math.abs(dx), threshold)
      : edge * Math.abs(dx) / (edge + Math.abs(dx)));
    const insertion = available ? Math.min(1, Math.max(0, (Math.abs(dx) - threshold) / (front.width * INSERT_DISTANCE))) : 0;
    const progress = Math.min(1, Math.abs(dx) / threshold);
    const next = state.index + direction;
    const { targets } = arrangement(available ? next : state.index);
    for (const [index, card] of cards) {
      const old = drag.before.get(index);
      const target = targets.get(index);
      if (index === state.index) {
        const outside = sideTransform(old, distance);
        card.style.transform = insertion > 0 ? interpolateTransform(outside, transform(target), insertion) : outside;
        card.style.zIndex = String(insertion > 0 ? target.z : images.length + 1);
      } else if (available && index === next) {
        card.style.transform = interpolateTransform(old.transform, transform(target), progress);
        card.style.zIndex = String(target.z);
      } else {
        card.style.transform = interpolateTransform(old.transform, transform(target), insertion);
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
    const direction = dx < 0 ? 1 : -1;
    const front = before.get(state.index);
    if (!cancelled && canTurn(direction) && Math.abs(dx) > front.width * TURN_DISTANCE) {
      state.index += direction;
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
