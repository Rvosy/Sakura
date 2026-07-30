import assert from "node:assert/strict";
import test from "node:test";

import { inferTextLanguage, multilingualTextRuns, renderMultilingualText } from "../pet/multilingual-text.js";

function renderFixture(text) {
  const viewport = {
    children: [],
    ownerDocument: {
      createDocumentFragment: () => ({
        children: [],
        append(node) { this.children.push(node); },
      }),
      createElement: () => ({ dataset: {}, lang: "", textContent: "" }),
    },
    replaceChildren(fragment) { this.children = [...fragment.children]; },
  };
  renderMultilingualText(viewport, text);
  return viewport.children;
}

test("CJK scripts select deterministic locale-specific font language tags", () => {
  assert.equal(inferTextLanguage("这是一段中文。"), "zh-CN");
  assert.equal(inferTextLanguage("これは日本語です。"), "ja-JP");
  assert.equal(inferTextLanguage("한국어 문장입니다."), "ko-KR");
  assert.equal(inferTextLanguage("Sakura Runtime"), "en");
});

test("mixed-language replies preserve text and classify each sentence independently", () => {
  const text = "中文段落。日本語の段落。\nEnglish paragraph.";
  const runs = multilingualTextRuns(text);
  assert.deepEqual(runs.map(({ lang }) => lang), ["zh-CN", "ja-JP", "en"]);
  assert.equal(runs.map(({ value }) => value).join(""), text);
});

test("mixed scripts inside one sentence keep their own stable font language", () => {
  const text = "这里是中文、English、かな、数字0123。";
  const runs = multilingualTextRuns(text);
  assert.deepEqual(runs.map(({ lang }) => lang), ["zh-CN", "en", "ja-JP", "zh-CN"]);
  assert.equal(runs.map(({ value }) => value).join(""), text);
});

test("rendered text runs expose only their painted inline boxes as selectable hit targets", () => {
  const spans = renderFixture("中文。English.");
  assert.equal(spans.length, 2);
  assert.ok(spans.every((span) => span.dataset.selectableText === "true"));
  assert.equal(spans.map((span) => span.textContent).join(""), "中文。English.");
});
