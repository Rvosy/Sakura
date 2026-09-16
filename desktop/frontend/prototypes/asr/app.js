// Visual prototype only: no microphone, provider, network, persistence or Assistant calls.
const $ = (id) => document.getElementById(id);
const fixedText = "今晚陪我打游戏吧，想玩点轻松的。";
const input = $("composer-input");
let phase = "idle";
let taskTimer, waveTimer, feedbackTimer;
let savedCaret = 0;
let recordingStarted = 0;
let levelHistory = [];
let hasAttachment = false;
let heldState = false;

function stopTimers() {
  clearTimeout(taskTimer);
  clearInterval(waveTimer);
  clearTimeout(feedbackTimer);
}
function feedback(text = "") {
  $("voice-feedback").textContent = text;
  $("voice-feedback").hidden = !text;
}
function closeTools() {
  $("composer-tool-dock").hidden = true;
  $("composer-attachment").setAttribute("aria-expanded", "false");
}

function layoutInput() {
  const voiceRow = ["preparing", "recording", "recognizing"].includes(phase);
  // Measure the original text column, then expand only for wrapping or attachments.
  $("composer").dataset.inputExpanded = "false";
  input.style.height = "40px";
  const expanded = !voiceRow && (hasAttachment || input.scrollHeight > 42);
  let height = 52;
  if (expanded) {
    $("composer").dataset.inputExpanded = "true";
    input.style.height = "0px";
    const textHeight = Math.max(23, Math.min(68, input.scrollHeight));
    input.style.height = `${textHeight}px`;
    $("composer").style.setProperty("--input-text-height", `${textHeight}px`);
    input.dataset.overflow = String(input.scrollHeight > 68);
    height = textHeight + 60;
  }
  $("pet-stage").style.setProperty("--input-height", `${height}px`);
}

function render(next) {
  phase = next;
  $("composer").dataset.state = phase;
  const busy = ["preparing", "recording", "recognizing"].includes(phase);
  input.hidden = busy;
  input.readOnly = busy;
  $("voice-status").hidden = phase !== "preparing" && phase !== "recognizing";
  $("voice-status").textContent =
    phase === "preparing"
      ? "正在准备"
      : phase === "recognizing"
        ? "正在识别"
        : "";
  $("voice-recording").hidden = phase !== "recording";
  const leftButton = $("composer-attachment");
  leftButton.dataset.action = busy ? "cancel" : "tools";
  leftButton.setAttribute("aria-label", busy ? "取消语音输入" : "添加附件");
  leftButton.title = busy ? "取消 · Esc" : "添加附件";
  if (busy) leftButton.removeAttribute("aria-expanded");
  else
    leftButton.setAttribute(
      "aria-expanded",
      String(!$("composer-tool-dock").hidden),
    );
  $("composer-attachments").hidden = !hasAttachment || busy;
  $("remove-attachment").disabled = busy;
  $("composer-send").disabled = busy || (!input.value.trim() && !hasAttachment);
  $("voice-mic").disabled =
    phase === "preparing" || phase === "recognizing" || phase === "unavailable";
  $("voice-mic").innerHTML =
    phase === "recording"
      ? '<span class="voice-stop" aria-hidden="true"></span>'
      : busy
        ? '<span class="voice-spinner" aria-hidden="true"></span>'
        : '<span class="sakura-icon icon-mic" aria-hidden="true"></span>';
  const label =
    phase === "recording"
      ? "结束录音并识别"
      : phase === "unavailable"
        ? "语音输入暂不可用"
        : "开始语音输入";
  $("voice-mic").setAttribute("aria-label", label);
  $("voice-mic").title = label;
  $("preview-state").value = phase;
  layoutInput();
}

function focusInput() {
  input.focus({ preventScroll: true });
  input.setSelectionRange(savedCaret, savedCaret);
}
function cancel() {
  stopTimers();
  heldState = false;
  render("idle");
  feedback();
  focusInput();
}

function record() {
  render("recording");
  feedback();
  recordingStarted = performance.now();
  levelHistory = [];
  const count = Math.max(8, Math.floor($("waveform").clientWidth / 6));
  $("waveform").replaceChildren(
    ...Array.from({ length: count }, () => document.createElement("i")),
  );
  waveTimer = setInterval(
    () => {
      const t = (performance.now() - recordingStarted) / 1000;
      const seconds = Math.min(60, Math.floor(t));
      // Slower example envelope and history movement, not live microphone volume.
      const waveTime = t * 0.4;
      const level =
        $("quiet").checked || waveTime % 5 > 4
          ? 0
          : 0.14 +
            Math.abs(Math.sin(waveTime * 8) * Math.cos(waveTime * 3)) * 0.86;
      levelHistory.push(level);
      levelHistory = levelHistory.slice(-count);
      for (let i = 0; i < count; i++)
        $("waveform").children[i].style.height =
          `${3 + (levelHistory[i - count + levelHistory.length] || 0) * 27}px`;
      if (seconds === 60 && !heldState) recognize();
    },
    150,
  );
}

function fillTranscript() {
  const left = input.value.slice(0, savedCaret),
    right = input.value.slice(savedCaret);
  const text = `${left && !/\s$/.test(left) ? " " : ""}${fixedText}${right && !/^\s/.test(right) ? " " : ""}`;
  input.value = left + text + right;
  savedCaret = left.length + text.length;
  render("filled");
  feedback();
  focusInput();
}
function recognize() {
  stopTimers();
  heldState = false;
  render("recognizing");
  feedback();
  taskTimer = setTimeout(fillTranscript, 1100);
}
$("voice-mic").addEventListener("click", () => {
  closeTools();
  feedback();
  if (phase === "recording") {
    recognize();
    return;
  }
  stopTimers();
  heldState = false;
  savedCaret = input.selectionEnd;
  render("preparing");
  taskTimer = setTimeout(record, 550);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    if (["preparing", "recording", "recognizing"].includes(phase)) cancel();
    closeTools();
  }
});
input.addEventListener("input", () => {
  feedback();
  render("idle");
});
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    $("composer").requestSubmit();
  }
});
$("composer").addEventListener("submit", (event) => {
  event.preventDefault();
  if ($("composer-send").disabled) return;
  input.value = "";
  hasAttachment = false;
  savedCaret = 0;
  render("idle");
  feedback("已发送（模拟）");
  focusInput();
  feedbackTimer = setTimeout(() => feedback(), 1600);
});
$("composer-attachment").addEventListener("click", () => {
  if (["preparing", "recording", "recognizing"].includes(phase)) {
    cancel();
    return;
  }
  feedback();
  const open = $("composer-tool-dock").hidden;
  $("composer-tool-dock").hidden = !open;
  $("composer-attachment").setAttribute("aria-expanded", String(open));
});
$("add-screenshot").addEventListener("click", () => {
  hasAttachment = true;
  closeTools();
  render("idle");
});
$("remove-attachment").addEventListener("click", () => {
  hasAttachment = false;
  render(phase);
});
$("preview-state").addEventListener("change", (event) => {
  stopTimers();
  closeTools();
  feedback();
  heldState = true;
  savedCaret = input.selectionEnd;
  const selected = event.target.value;
  if (selected === "recording") {
    record();
    return;
  }
  if (selected === "filled") {
    input.value = fixedText;
    savedCaret = input.value.length;
  }
  render(selected);
  const labels = {
    silence: "没有检测到人声，请再试一次。",
    error: "识别失败，请重试。",
    unavailable: "语音输入暂不可用，请检查引擎配置。",
  };
  feedback(labels[selected] || "");
});
$("dark-background").addEventListener("change", (event) => {
  document.body.dataset.background = event.target.checked ? "dark" : "light";
});
$("reset").addEventListener("click", () => {
  stopTimers();
  heldState = false;
  input.value = "";
  hasAttachment = false;
  savedCaret = 0;
  $("quiet").checked = false;
  closeTools();
  feedback();
  render("idle");
});
function fitStage() {
  const scale = Math.min(1, (innerWidth - 32) / 600, (innerHeight - 72) / 656);
  $("stage-frame").style.width = `${600 * scale}px`;
  $("stage-frame").style.height = `${656 * scale}px`;
  $("pet-stage").style.setProperty("--content-scale", String(scale));
}
window.addEventListener("resize", fitStage);
window.addEventListener("pagehide", stopTimers);
fitStage();
render("idle");
