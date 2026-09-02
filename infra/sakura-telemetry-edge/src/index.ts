interface Env {
  DB: D1Database;
}

type ErrorReport = {
  schema: 1;
  version: string;
  platform: string;
  arch?: string;
  component: string;
  event: string;
  errorCode: string;
  fingerprint?: string;
};

const MAX_BODY_BYTES = 4 * 1024;
const TOKEN_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:+-]*$/;
const ALLOWED_FIELDS = new Set([
  "schema",
  "version",
  "platform",
  "arch",
  "component",
  "event",
  "errorCode",
  "fingerprint",
]);

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      if (request.method !== "GET") {
        return response(405, { ok: false, code: "METHOD_NOT_ALLOWED" });
      }
      return health(env);
    }

    if (url.pathname === "/v1/errors") {
      if (request.method !== "POST") {
        return response(405, { ok: false, code: "METHOD_NOT_ALLOWED" });
      }
      return ingestError(request, env);
    }

    return response(404, { ok: false, code: "NOT_FOUND" });
  },
};

async function health(env: Env): Promise<Response> {
  try {
    const result = await env.DB.prepare("SELECT 1 AS ok").first<{ ok: number }>();
    if (result?.ok !== 1) {
      return response(503, { ok: false, code: "DATABASE_UNAVAILABLE" });
    }
    return response(200, { ok: true, service: "sakura-telemetry-edge" });
  } catch {
    return response(503, { ok: false, code: "DATABASE_UNAVAILABLE" });
  }
}

async function ingestError(request: Request, env: Env): Promise<Response> {
  const contentType = request.headers.get("content-type") ?? "";
  if (!contentType.toLowerCase().startsWith("application/json")) {
    return response(415, { ok: false, code: "CONTENT_TYPE_REQUIRED" });
  }

  const contentLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(contentLength) && contentLength > MAX_BODY_BYTES) {
    return response(413, { ok: false, code: "PAYLOAD_TOO_LARGE" });
  }

  let raw: string;
  try {
    const body = await readBodyLimited(request, MAX_BODY_BYTES);
    if (body === null) {
      return response(413, { ok: false, code: "PAYLOAD_TOO_LARGE" });
    }
    raw = body;
  } catch {
    return response(400, { ok: false, code: "INVALID_BODY" });
  }

  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return response(400, { ok: false, code: "INVALID_JSON" });
  }

  const report = parseErrorReport(value);
  if (report === null) {
    return response(400, { ok: false, code: "INVALID_PAYLOAD" });
  }

  try {
    await env.DB.prepare(
      `INSERT INTO error_events (
        schema_version,
        app_version,
        platform,
        arch,
        component,
        event,
        error_code,
        fingerprint
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        report.schema,
        report.version,
        report.platform,
        report.arch ?? null,
        report.component,
        report.event,
        report.errorCode,
        report.fingerprint ?? null,
      )
      .run();
  } catch {
    return response(503, { ok: false, code: "DATABASE_WRITE_FAILED" });
  }

  return response(202, { ok: true });
}

function parseErrorReport(value: unknown): ErrorReport | null {
  if (!isRecord(value)) {
    return null;
  }

  for (const key of Object.keys(value)) {
    if (!ALLOWED_FIELDS.has(key)) {
      return null;
    }
  }

  if (value.schema !== 1) {
    return null;
  }

  const version = requiredToken(value.version, 32);
  const platform = requiredToken(value.platform, 32);
  const component = requiredToken(value.component, 64);
  const event = requiredToken(value.event, 128);
  const errorCode = requiredToken(value.errorCode, 128);
  const arch = optionalToken(value.arch, 32);
  const fingerprint = optionalToken(value.fingerprint, 128);

  if (
    version === null ||
    platform === null ||
    component === null ||
    event === null ||
    errorCode === null ||
    arch === false ||
    fingerprint === false
  ) {
    return null;
  }

  return {
    schema: 1,
    version,
    platform,
    component,
    event,
    errorCode,
    ...(arch === undefined ? {} : { arch }),
    ...(fingerprint === undefined ? {} : { fingerprint }),
  };
}

function requiredToken(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") {
    return null;
  }
  if (value.length < 1 || value.length > maxLength || !TOKEN_PATTERN.test(value)) {
    return null;
  }
  return value;
}

function optionalToken(value: unknown, maxLength: number): string | undefined | false {
  if (value === undefined) {
    return undefined;
  }
  const token = requiredToken(value, maxLength);
  return token === null ? false : token;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function readBodyLimited(request: Request, maxBytes: number): Promise<string | null> {
  if (request.body === null) {
    return "";
  }

  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      if (value === undefined) {
        continue;
      }

      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }

  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }

  return new TextDecoder("utf-8", { fatal: true }).decode(merged);
}

function response(status: number, body: Record<string, unknown>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      "content-type": "application/json; charset=utf-8",
      "cache-control": "no-store",
    },
  });
}
