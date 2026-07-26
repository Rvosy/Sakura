import assert from "node:assert/strict";
import test from "node:test";

import { inferTextLanguage, multilingualTextRuns } from "../pet/multilingual-text.js";

test("CJK scripts select deterministic locale-specific font language tags", () => {
  assert.equal(inferTextLanguage("这是一段中文。"), "zh-CN");
  assert.equal(inferTextLanguage("これは日本語です。"), "ja-JP");
  assert.equal(inferTextLanguage("한국어 문장입니다."), "ko-KR");
  assert.equal(inferTextLanguage("Sakura Runtime"), "en");
});

test("mixed-language replies preserve text and classify each sentence independently", () => {
  const text = "中文段落。日本語の段落。\nEnglish paragraph.";
  const runs = multilingualTextRuns(text);
  assert.deepEqual(runs.map(({ lang }) => lang), ["zh-CN", "ja-JP", "zh-CN", "en"]);
  assert.equal(runs.map(({ value }) => value).join(""), text);
});
