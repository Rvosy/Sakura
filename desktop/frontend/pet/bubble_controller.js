const MIN_AUTO_HIDE_DELAY_SECONDS = 1;
const MAX_AUTO_HIDE_DELAY_SECONDS = 120;

function clampDelaySeconds(value) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isFinite(parsed)) return 5;
  return Math.max(MIN_AUTO_HIDE_DELAY_SECONDS, Math.min(MAX_AUTO_HIDE_DELAY_SECONDS, parsed));
}

export class BubbleAutoHideController {
  constructor({
    target,
    setTimer = (callback, delay) => window.setTimeout(callback, delay),
    clearTimer = (timer) => window.clearTimeout(timer),
    onHidden = () => {},
  }) {
    this.target = target;
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.onHidden = onHidden;
    this.enabled = false;
    this.delayMs = 5000;
    this.settled = false;
    this.speaking = false;
    this.hovered = false;
    this.hidden = false;
    this.timer = null;

    this.target?.addEventListener?.("pointerenter", () => {
      this.hovered = true;
      this.#clearCountdown();
    });
    this.target?.addEventListener?.("pointerleave", () => {
      this.hovered = false;
      this.#scheduleCountdown();
    });
  }

  configure({ auto_hide_enabled: enabled, auto_hide_delay_seconds: delaySeconds } = {}) {
    this.enabled = Boolean(enabled);
    this.delayMs = clampDelaySeconds(delaySeconds) * 1000;
    if (!this.enabled) {
      this.#clearCountdown();
      this.#setHidden(false);
      return;
    }
    this.#scheduleCountdown();
  }

  notifySpeaking() {
    this.speaking = true;
    this.settled = false;
    this.#clearCountdown();
    this.#setHidden(false);
  }

  notifySettled() {
    this.speaking = false;
    this.settled = true;
    this.#scheduleCountdown();
  }

  handleUserInteraction() {
    this.#setHidden(false);
    this.#scheduleCountdown();
  }

  dispose() {
    this.#clearCountdown();
  }

  #scheduleCountdown() {
    this.#clearCountdown();
    if (!this.enabled || !this.settled || this.speaking || this.hovered || this.hidden) return;
    this.timer = this.setTimer(() => {
      this.timer = null;
      if (!this.enabled || !this.settled || this.speaking || this.hovered) return;
      this.#setHidden(true);
    }, this.delayMs);
  }

  #clearCountdown() {
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
  }

  #setHidden(hidden) {
    const next = Boolean(hidden);
    if (this.hidden === next) return;
    this.hidden = next;
    this.target?.classList?.toggle?.("is-auto-hidden", next);
    this.target?.setAttribute?.("aria-hidden", String(next));
    if (next) this.onHidden();
  }
}
