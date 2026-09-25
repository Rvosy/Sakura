const KANA = /[\u3040-\u30ff\u31f0-\u31ff]/u;
const HANGUL = /[\u1100-\u11ff\u3130-\u318f\uac00-\ud7af]/u;
const HAN = /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u;
const CYRILLIC = /[\u0400-\u052f]/u;
const ARABIC = /[\u0600-\u06ff\u0750-\u077f]/u;
const LATIN = /[A-Za-z\u00c0-\u024f]/u;

export function inferTextLanguage(text, fallback = "zh-CN") {
  const value = String(text ?? "");
  if (KANA.test(value)) return "ja-JP";
  if (HANGUL.test(value)) return "ko-KR";
  if (HAN.test(value)) return "zh-CN";
  if (CYRILLIC.test(value)) return "ru";
  if (ARABIC.test(value)) return "ar";
  if (LATIN.test(value)) return "en";
  return fallback;
}

function cjkBaseLanguage(text, fallback) {
  const value = String(text ?? "");
  const kanaCount = [...value].filter((character) => KANA.test(character)).length;
  const hangulCount = [...value].filter((character) => HANGUL.test(character)).length;
  const hanCount = [...value].filter((character) => HAN.test(character)).length;
  if (hangulCount > 0 && hangulCount >= hanCount) return "ko-KR";
  if (kanaCount > 0) return "ja-JP";
  if (hanCount > 0) return "zh-CN";
  return fallback;
}

function characterLanguage(character, cjkBase) {
  if (KANA.test(character)) return "ja-JP";
  if (HANGUL.test(character)) return "ko-KR";
  if (HAN.test(character)) return cjkBase;
  if (CYRILLIC.test(character)) return "ru";
  if (ARABIC.test(character)) return "ar";
  if (LATIN.test(character)) return "en";
  return null;
}

function appendScriptRuns(runs, value, fallback) {
  if (!value) return;
  const cjkBase = cjkBaseLanguage(value, fallback);
  let buffer = "";
  let language = null;
  for (const character of value) {
    const nextLanguage = characterLanguage(character, cjkBase);
    if (nextLanguage && language && nextLanguage !== language) {
      pushRun(runs, buffer, language);
      buffer = "";
    }
    if (nextLanguage) language = nextLanguage;
    buffer += character;
  }
  if (buffer) pushRun(runs, buffer, language || runs.at(-1)?.lang || fallback);
}

function pushRun(runs, value, lang) {
  const previous = runs.at(-1);
  if (previous?.lang === lang) {
    runs[runs.length - 1] = Object.freeze({ value: previous.value + value, lang });
  } else {
    runs.push(Object.freeze({ value, lang }));
  }
}

export function multilingualTextRuns(text, fallback = "zh-CN") {
  const runs = [];
  let buffer = "";
  for (const character of String(text ?? "")) {
    buffer += character;
    if ("。！？.!?\n，,、；;：:".includes(character)) {
      appendScriptRuns(runs, buffer, fallback);
      buffer = "";
    }
  }
  if (buffer || !runs.length) appendScriptRuns(runs, buffer, fallback);
  return Object.freeze(runs);
}

export function renderMultilingualText(viewport, text, fallback = "zh-CN") {
  const documentRef = viewport?.ownerDocument;
  if (!documentRef || typeof viewport.replaceChildren !== "function") {
    throw new Error("multilingual text viewport requires a DOM document");
  }
  const fragment = documentRef.createDocumentFragment();
  const runs = multilingualTextRuns(text, fallback);
  runs.forEach((run) => {
    const span = documentRef.createElement("span");
    span.lang = run.lang;
    span.dataset.selectableText = "true";
    span.textContent = run.value;
    fragment.append(span);
  });
  viewport.replaceChildren(fragment);
}

const subtitleLayouts = new WeakMap();

function subtitleLine(documentRef, value, index, language) {
  const line = documentRef.createElement("span");
  const isJapanese = language === "bilingual_ja" ? index === 0 : index === 1;
  line.className = `subtitle-line subtitle-line--${isJapanese ? "ja" : "zh"} subtitle-line--${index === 0 ? "primary" : "secondary"}`;
  line.lang = isJapanese ? "ja-JP" : "zh-CN";
  line.dataset.selectableText = "true";
  line.textContent = value;
  return line;
}

// Read the browser's actual line breaks, including font metrics, punctuation and word wrapping.
// Offsets refer to the full reply so an unfinished word cannot move earlier subtitle pairs.
function wrappedLines(line) {
  const value = line.textContent;
  const node = line.firstChild;
  if (!node) return [];
  const range = line.ownerDocument.createRange();
  const result = [[]];
  let start = 0;
  let top = null;
  for (const { segment, index } of new Intl.Segmenter(undefined, { granularity: "grapheme" }).segment(value)) {
    const end = index + segment.length;
    if (segment.includes("\n")) {
      result.at(-1).push({ start, end });
      result.push([]);
      start = end;
      top = null;
      continue;
    }
    range.setStart(node, index);
    range.setEnd(node, end);
    const nextTop = range.getBoundingClientRect().top;
    if (top !== null && Math.abs(nextTop - top) > 1) {
      result.at(-1).push({ start, end: index });
      start = index;
    }
    top = nextTop;
  }
  if (start < value.length) result.at(-1).push({ start, end: value.length });
  return result;
}

export function renderSubtitleText(viewport, text, subtitleTracks = [], fullSubtitleTracks = subtitleTracks, language = "bilingual") {
  if (subtitleTracks.length !== 2) {
    subtitleLayouts.delete(viewport);
    renderMultilingualText(viewport, text);
    return;
  }
  const documentRef = viewport.ownerDocument;
  const style = documentRef.defaultView.getComputedStyle(viewport);
  const width = viewport.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  const key = [width, style.fontFamily, style.fontSize, style.fontWeight, style.letterSpacing,
    style.getPropertyValue("--font-zh"), style.getPropertyValue("--font-ja"), language, ...fullSubtitleTracks];
  let layout = subtitleLayouts.get(viewport);
  if (!layout || key.some((value, index) => value !== layout.key[index])) {
    const probe = documentRef.createElement("span");
    probe.className = "subtitle-measure";
    probe.style.width = `${Math.max(1, width)}px`;
    const tracks = fullSubtitleTracks.map((value, index) => subtitleLine(documentRef, value, index, language));
    probe.append(...tracks);
    viewport.append(probe);
    layout = { key, lines: tracks.map(wrappedLines) };
    probe.remove();
    subtitleLayouts.set(viewport, layout);
  }
  const fragment = documentRef.createDocumentFragment();
  const paragraphCount = Math.max(...layout.lines.map((paragraphs) => paragraphs.length));
  for (let paragraph = 0; paragraph < paragraphCount; paragraph += 1) {
    const lines = layout.lines.map((paragraphs) => paragraphs[paragraph] || []);
    const count = Math.max(...lines.map((track) => track.length));
    for (let row = 0; row < count; row += 1) {
      const values = lines.map((track, index) => {
        const bounds = track[row];
        return bounds ? subtitleTracks[index].slice(bounds.start, bounds.end).replace(/\r?\n$/u, "") : "";
      });
      if (!values.some(Boolean)) continue;
      const pair = documentRef.createElement("span");
      pair.className = "subtitle-pair";
      values.forEach((value, index) => pair.append(subtitleLine(documentRef, value, index, language)));
      fragment.append(pair);
    }
  }
  viewport.replaceChildren(fragment);
}
