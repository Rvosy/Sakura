# Sakura Telemetry Edge PoC

This directory contains the first remote error-ingestion proof of concept for Sakura.

The PoC intentionally accepts only a small allowlisted payload. It does **not** accept or store logs, exception messages, stack traces, prompts, chat content, file paths, character data, model configuration, API URLs, or credentials.

## Payload

`POST /v1/errors`

```json
{
  "schema": 1,
  "version": "1.0.3",
  "platform": "windows",
  "arch": "x86_64",
  "component": "webview",
  "event": "webview.error.unhandled",
  "errorCode": "WEBVIEW_UNHANDLED_ERROR",
  "fingerprint": "8a31f5d2"
}
```

All string fields are bounded tokens rather than free-form text. Unknown fields are rejected. The request body is limited to 4 KiB.

## First deployment

Requirements: Node.js/npm and a Cloudflare account.

```bash
cd infra/sakura-telemetry-edge
npm install
npx wrangler login
npm run deploy
```

`wrangler.jsonc` contains a draft D1 binding without a database ID. Current Wrangler can provision the D1 resource during deployment and write the generated resource ID back to the local config.

If automatic provisioning is not available for the account, create the database explicitly instead:

```bash
npx wrangler d1 create sakura-telemetry --binding DB --update-config
npm run deploy
```

After the D1 resource exists, apply the schema:

```bash
npm run db:migrate:remote
```

Then deploy once more if the first deployment happened before the migration:

```bash
npm run deploy
```

## Verify

Check Worker + D1 availability:

```bash
curl https://<worker-host>/health
```

Expected response:

```json
{"ok":true,"service":"sakura-telemetry-edge"}
```

Send one synthetic error:

```bash
curl -X POST https://<worker-host>/v1/errors \
  -H 'content-type: application/json' \
  --data '{
    "schema":1,
    "version":"1.0.3",
    "platform":"windows",
    "arch":"x86_64",
    "component":"webview",
    "event":"webview.error.unhandled",
    "errorCode":"WEBVIEW_UNHANDLED_ERROR",
    "fingerprint":"test-001"
  }'
```

Expected HTTP status: `202`.

Query the remote database:

```bash
npx wrangler d1 execute sakura-telemetry --remote \
  --command='SELECT id, received_at, app_version, platform, component, error_code, fingerprint FROM error_events ORDER BY id DESC LIMIT 10;'
```

## Deliberately deferred

This PoC does not yet include client integration, installation IDs, authentication, request signing, custom domains, dashboards, retention jobs, ratings, general usage telemetry, or log upload.

Before production use, add abuse controls and freeze the remote telemetry contract in the Sakura Service specification.
