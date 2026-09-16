use std::{
    fs,
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};

use super::{ErrorReport, ERROR_BODY_LIMIT};

const MAX_REPORTS: usize = 64;
const RETENTION: Duration = Duration::from_secs(7 * 24 * 3600);

/// Only error reports are durable. Report IDs are also the server's idempotency key.
pub(super) struct Outbox {
    directory: PathBuf,
}

impl Outbox {
    pub(super) fn new(config: &Path) -> Self {
        Self {
            directory: config.with_file_name("telemetry-pending-errors"),
        }
    }

    fn files(&self) -> Vec<PathBuf> {
        let mut files: Vec<_> = fs::read_dir(&self.directory)
            .into_iter()
            .flatten()
            .filter_map(Result::ok)
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|s| s == "json"))
            .collect();
        files.sort_by_key(|p| {
            fs::metadata(p)
                .and_then(|m| m.modified())
                .unwrap_or(SystemTime::UNIX_EPOCH)
        });
        files
    }

    pub(super) fn save(&self, report: &ErrorReport) -> Result<(), String> {
        let files = self.files();
        for file in files
            .iter()
            .take(files.len().saturating_sub(MAX_REPORTS - 1))
        {
            let _ = fs::remove_file(file);
        }
        let bytes = serde_json::to_vec(report).map_err(|_| "TELEMETRY_ENCODE_FAILED")?;
        crate::ui_config::atomic_write(
            &self.directory.join(format!("{}.json", report.report_id)),
            &bytes,
            "TELEMETRY_OUTBOX",
        )
    }

    pub(super) fn pending(&self, installation_id: Option<&str>) -> Vec<ErrorReport> {
        let mut reports = Vec::new();
        for file in self.files() {
            let report = fs::metadata(&file)
                .ok()
                .filter(|m| {
                    m.len() <= ERROR_BODY_LIMIT as u64
                        && m.modified()
                            .ok()
                            .and_then(|t| t.elapsed().ok())
                            .is_some_and(|age| age < RETENTION)
                })
                .and_then(|_| fs::read(&file).ok())
                .and_then(|b| serde_json::from_slice::<ErrorReport>(&b).ok())
                .filter(|r| r.schema == 3 && Some(r.installation_id.as_str()) == installation_id);
            if let Some(report) = report {
                reports.push(report);
            } else {
                let _ = fs::remove_file(file);
            }
        }
        reports
    }

    pub(super) fn remove(&self, id: &str) {
        let _ = fs::remove_file(self.directory.join(format!("{id}.json")));
    }

    pub(super) fn clear(&self) {
        for file in self.files() {
            let _ = fs::remove_file(file);
        }
    }
}
