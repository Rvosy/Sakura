CREATE TABLE IF NOT EXISTS error_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    schema_version INTEGER NOT NULL,
    app_version TEXT NOT NULL CHECK(length(app_version) BETWEEN 1 AND 32),
    platform TEXT NOT NULL CHECK(length(platform) BETWEEN 1 AND 32),
    arch TEXT CHECK(arch IS NULL OR length(arch) BETWEEN 1 AND 32),
    component TEXT NOT NULL CHECK(length(component) BETWEEN 1 AND 64),
    event TEXT NOT NULL CHECK(length(event) BETWEEN 1 AND 128),
    error_code TEXT NOT NULL CHECK(length(error_code) BETWEEN 1 AND 128),
    fingerprint TEXT CHECK(fingerprint IS NULL OR length(fingerprint) BETWEEN 1 AND 128)
);

CREATE INDEX IF NOT EXISTS idx_error_events_received_at
ON error_events(received_at);

CREATE INDEX IF NOT EXISTS idx_error_events_error_code
ON error_events(error_code);

CREATE INDEX IF NOT EXISTS idx_error_events_version_code
ON error_events(app_version, error_code);
