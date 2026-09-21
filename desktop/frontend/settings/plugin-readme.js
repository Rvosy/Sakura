import { Marked, Renderer } from "../vendor/marked/marked.esm.js";

const escape = value => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

export function documentationUrl(value, base) {
  try {
    const url = new URL(value, base);
    return url.protocol === "https:" && !url.username && !url.password ? url.href : "";
  } catch { return ""; }
}

export function renderPluginReadme(markdown, base) {
  const parser = new Marked({ gfm: true, async: false, renderer: {
    html: ({ text }) => escape(text),
    heading({ depth, tokens }) {
      const level = Math.min(depth + 2, 6);
      return `<h${level}>${this.parser.parseInline(tokens)}</h${level}>`;
    },
    table(token) {
      const html = Renderer.prototype.table.call(this, token);
      const metadata = token.rows.some(row => /^(插件\s*ID|Plugin\s*ID|Plugin\s*API)$/i.test(row[0]?.text.trim()));
      return metadata ? `<details class="detail-disclosure" data-disclosure="readme-metadata"><summary data-readme-metadata>项目资料（作者提供）</summary>${html}</details>` : html;
    },
    link({ href, tokens }) {
      const label = this.parser.parseInline(tokens), url = documentationUrl(href, base);
      return url ? `<a href="${escape(url)}" data-document-link>${label}</a>` : label;
    },
    image({ href, text }) {
      const url = documentationUrl(href, base), label = escape(text || "查看图片");
      return url ? `<a href="${escape(url)}" data-document-link>${label}</a>` : label;
    },
  } });
  const tokens = parser.lexer(markdown);
  if (tokens[0]?.type === "heading" && tokens[0].depth === 1) tokens.shift();
  return parser.parser(tokens);
}
