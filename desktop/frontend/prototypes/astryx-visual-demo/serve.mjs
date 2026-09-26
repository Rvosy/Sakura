import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { resolve, extname, sep } from "node:path";
import { DatabaseSync } from "node:sqlite";

const root = fileURLToPath(new URL("../../", import.meta.url));
const historyArgument = process.argv.indexOf("--history-root");
const historyRoot = historyArgument >= 0 ? process.argv[historyArgument + 1] : null;

async function localHistory() {
  const db = new DatabaseSync(resolve(historyRoot, "data/chat_history/timeline.sqlite3"), {readOnly:true});
  try {
    let characterId;
    try {
      const config = await readFile(resolve(historyRoot, "config/characters.yaml"), "utf8");
      characterId = config.match(/^current_character_id:\s*(.+)$/m)?.[1]?.trim().replace(/^['"]|['"]$/g, "");
    } catch (error) { if (error.code !== "ENOENT") throw error; }
    characterId ||= db.prepare("SELECT character_id FROM timeline_entries ORDER BY seq DESC LIMIT 1").get()?.character_id || "Sakura";
    const entries = db.prepare("SELECT entry_id, turn_id, kind, origin, created_at, payload_json FROM timeline_entries WHERE character_id = ? ORDER BY seq").all(characterId).map(row => ({entryId:row.entry_id, turnId:row.turn_id, kind:row.kind, origin:row.origin, createdAt:row.created_at, payload:JSON.parse(row.payload_json)}));
    return {source:"local", assistantName:characterId, characterId, entries};
  } finally { db.close(); }
}
const types = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
createServer(async (request, response) => {
  try {
    const pathname = new URL(request.url, "http://localhost").pathname;
    if (pathname === "/__sakura_review/history") {
      if (!historyRoot) { response.writeHead(404).end(); return; }
      try {
        const data = await localHistory();
        response.writeHead(200, {"Content-Type":"application/json; charset=utf-8", "Cache-Control":"no-store"}).end(JSON.stringify(data));
      } catch { response.writeHead(500).end("History read failed"); }
      return;
    }
    let path = resolve(root, "." + decodeURIComponent(pathname));
    if (path !== resolve(root) && !path.startsWith(resolve(root) + sep)) { response.writeHead(403).end(); return; }
    if ((await stat(path)).isDirectory()) path = resolve(path, "index.html");
    const data = await readFile(path);
    response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream", "Cache-Control": "no-store" });
    response.end(data);
  } catch { response.writeHead(404).end("Not found"); }
}).listen(8770, "127.0.0.1", () => console.log("http://127.0.0.1:8770/prototypes/astryx-visual-demo/"));
