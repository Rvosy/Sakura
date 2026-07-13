export class SubtitleController {
  constructor({
    target,
    language = "zh",
    typingIntervalMs = 35,
    segmentPauseMs = 100,
    setTimer = (callback, delay) => window.setTimeout(callback, delay),
    clearTimer = (timer) => window.clearTimeout(timer),
    onSegment = () => {},
    onComplete = () => {},
  }) {
    this.target = target;
    this.language = language === "ja" ? "ja" : "zh";
    this.typingIntervalMs = Math.max(5, Math.min(200, Number(typingIntervalMs) || 35));
    this.segmentPauseMs = Math.max(0, Math.min(3000, Number(segmentPauseMs) || 0));
    this.setTimer = setTimer;
    this.clearTimer = clearTimer;
    this.onSegment = onSegment;
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
    this.cancel("");
    this.segments = Array.isArray(segments) ? segments.filter(Boolean) : [];
    this.segmentIndex = 0;
    const sequence = this.sequence;
    if (!this.segments.length) {
      this.onComplete();
      return;
    }
    this.#startSegment(sequence);
  }

  setText(text) {
    this.cancel(String(text ?? ""));
  }

  cancel(replacement = "") {
    this.sequence += 1;
    if (this.timer != null) this.clearTimer(this.timer);
    this.timer = null;
    this.segments = [];
    this.segmentIndex = 0;
    this.target.textContent = String(replacement ?? "");
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
    this.target.textContent = "";
    let index = 0;
    const typeNext = () => {
      if (sequence !== this.sequence) return;
      if (index < characters.length) {
        this.target.textContent += characters[index];
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
