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
