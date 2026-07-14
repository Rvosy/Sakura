export class SubtitleController {
  constructor({
    target,
    language = "zh",
    typingIntervalMs = 35,
    segmentPauseMs = 100,
    setTimer = (callback, delay) => window.setTimeout(callback, delay),
    clearTimer = (timer) => window.clearTimeout(timer),
    onSegment = () => {},
    onStart = () => {},
    onTextChange = () => {},
    onCancel = () => {},
    onComplete = () => {},
  }) {
    this.target = target;
    this.language = language === "ja" ? "ja" : "zh";
    this.typingIntervalMs = Math.max(5, Math.min(200, Number(typingIntervalMs) || 35));
    this.segmentPauseMs = Math.max(0, Math.min(3000, Number(segmentPauseMs) || 0));
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.onSegment = onSegment;
    this.onStart = onStart;
    this.onTextChange = onTextChange;
    this.onCancel = onCancel;
    this.onComplete = onComplete;
    this.timer = null;
    this.sequence = 0;
    this.segments = [];
    this.segmentIndex = 0;
  }

  configure({ language, typingIntervalMs, segmentPauseMs } = {}) {
    if (language) this.language = language === "ja" ? "ja" : "zh";
    if (typingIntervalMs != null) {
      this.typingIntervalMs = Math.max(5, Math.min(200, Number(typingIntervalMs) || 35));
    }
    if (segmentPauseMs != null) {
      this.segmentPauseMs = Math.max(0, Math.min(3000, Number(segmentPauseMs) || 0));
    }
  }

  showSegments(segments) {
    this.#reset();
    this.segments = Array.isArray(segments) ? segments.filter(Boolean) : [];
    this.segmentIndex = 0;
    const sequence = this.sequence;
    this.onStart();
    this.#setText("");
    if (!this.segments.length) {
      this.onComplete();
      return;
    }
    this.#startSegment(sequence);
  }

  setText(text) {
    this.#reset();
    this.onStart();
    const replacement = String(text ?? "");
    this.#setText(replacement);
    this.onCancel(replacement);
  }

  cancel(replacement = "") {
    this.#reset();
    this.onStart();
    const text = String(replacement ?? "");
    this.#setText(text);
    this.onCancel(text);
  }

  #reset() {
    this.sequence += 1;
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
    this.segments = [];
    this.segmentIndex = 0;
  }

  #setText(text) {
    this.target.textContent = String(text ?? "");
    this.onTextChange(this.target.textContent);
  }

  #startSegment(sequence) {
    if (sequence !== this.sequence) return;
    const segment = this.segments[this.segmentIndex];
    if (!segment) {
      this.onComplete();
      return;
    }
    this.onSegment(segment);
    const text = this.language === "ja" ? segment.ja || segment.zh || "" : segment.zh || segment.ja || "";
    const characters = Array.from(text);
    this.#setText("");
    let index = 0;
    const typeNext = () => {
      if (sequence !== this.sequence) return;
      if (index < characters.length) {
        this.#setText(this.target.textContent + characters[index]);
        index += 1;
        this.timer = this.setTimer(typeNext, this.typingIntervalMs);
        return;
      }
      this.segmentIndex += 1;
      if (this.segmentIndex >= this.segments.length) {
        this.timer = null;
        this.onComplete();
        return;
      }
      this.timer = this.setTimer(() => this.#startSegment(sequence), this.segmentPauseMs);
    };
    this.timer = this.setTimer(typeNext, this.typingIntervalMs);
  }
}
