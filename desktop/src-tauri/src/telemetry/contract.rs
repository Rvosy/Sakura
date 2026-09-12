use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct DiagnosticDetail {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub severity: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub impact: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cause_type: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub file: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub line: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub column: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub surface: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub timeout_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub elapsed_ms: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub exit_code: Option<i64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub child_exited: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub probe_outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub primary_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub recovery_outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_exists: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub staged_exists: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub backup_exists: Option<bool>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub outcome: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fingerprint: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub occurrence_count: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dropped: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub failed: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rejected: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repair_reason: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repair_outcome: Option<String>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct RequestDiagnostic {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub fault_domain: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub reason_code: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub http_status: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub stage: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attempt_count: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub compatibility_fallback: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(super) struct DiagnosticContext {
    pub build_id: String,
    pub environment: String,
    pub generation: Option<String>,
    pub occurred_ms: u64,
}
