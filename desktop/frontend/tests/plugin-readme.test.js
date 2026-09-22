import assert from "node:assert/strict";
import test from "node:test";
import { documentationUrl, renderPluginReadme } from "../settings/plugin-readme.js";

const base = "https://github.com/author/plugin/blob/123/README.md";
test("project documentation formats instructions and resolves relative project links", () => {
  const html = renderPluginReadme("# Usage\n\n1. **Enable** the plugin\n2. Open settings\n\n[License](LICENSE)\n\n| Format | Support |\n| --- | --- |\n| JSON | Yes |", base);
  assert.match(html, /<ol>/); assert.match(html, /<strong>Enable<\/strong>/); assert.match(html, /<table>/);
  assert.match(html, /href="https:\/\/github.com\/author\/plugin\/blob\/123\/LICENSE"/);
});

test("repository Markdown cannot inject HTML or executable URLs", () => {
  const html = renderPluginReadme('<script>alert(1)</script>\n\n[bad](javascript:alert%281%29)\n\n![image](https://example.com/image.png)\n\n<img src=x onerror=alert(1)>\n\n```html\n<iframe src=x>\n```', base);
  assert.doesNotMatch(html, /<(?:script|iframe|img)\b/);
  assert.doesNotMatch(html, /href="javascript:/);
  assert.match(html, /&lt;script&gt;/);
  assert.match(html, /href="https:\/\/example.com\/image.png"/);
  for (const url of ['javascript:alert(1)', 'file:///secret', 'data:text/html,x', 'https://user:secret@example.com']) {
    assert.equal(documentationUrl(url, base), "");
  }
});
