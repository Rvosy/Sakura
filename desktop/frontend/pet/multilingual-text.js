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

export function multilingualTextRuns(text, fallback = "zh-CN") {
  const runs = [];
  let buffer = "";
  for (const character of String(text ?? "")) {
    buffer += character;
    if ("。！？.!?\n".includes(character)) {
      runs.push(Object.freeze({ value: buffer, lang: inferTextLanguage(buffer, fallback) }));
      buffer = "";
    }
  }
  if (buffer || !runs.length) {
    runs.push(Object.freeze({ value: buffer, lang: inferTextLanguage(buffer, fallback) }));
  }
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
    span.textContent = run.value;
    fragment.append(span);
  });
  viewport.replaceChildren(fragment);
}
