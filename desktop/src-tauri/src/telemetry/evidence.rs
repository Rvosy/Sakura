use std::collections::BTreeMap;

use serde_json::Value;

/// The diagnostic fields captured at the failure site. Text is not a token:
/// filenames, provider messages, dependency frames and Unicode stay readable.
pub(super) type ErrorEvidence = BTreeMap<String, Value>;

pub(super) fn from_attributes(attributes: Option<&Value>) -> ErrorEvidence {
    const FIELDS: &[&str] = &[
        "diagnostic",
        "exception_chain",
        "exception_stack",
        "recovery_diagnostic",
        "exception_site",
        "error_type",
        "cause_type",
        "errno",
        "winerror",
        "plugin_id",
        "plugin_name",
        "provider",
        "model",
        "endpoint",
        "url",
        "path",
        "source_file",
        "source_line",
        "stderr",
        "exit_code",
        "status",
        "stage",
        "command",
        "request_id",
        "window_label",
        "provider_error_code",
        "provider_error_type",
        "timeout_ms",
        "repair_reason",
        "repair_outcome",
    ];
    let mut result = BTreeMap::new();
    for key in FIELDS {
        if let Some(value) = attributes.and_then(|a| a.get(key)) {
            if value.is_string() || value.is_number() || value.is_boolean() {
                result.insert((*key).into(), value.clone());
            }
        }
    }
    result
}

pub(super) fn bound(evidence: &mut ErrorEvidence) {
    for (key, value) in evidence {
        if let Some(text) = value.as_str() {
            let limit = if matches!(key.as_str(), "exception_stack" | "exception_chain") {
                8192
            } else if matches!(
                key.as_str(),
                "diagnostic" | "recovery_diagnostic" | "stderr"
            ) {
                4096
            } else {
                1024
            };
            *value = Value::String(crate::runtime_log::sanitize_diagnostic(text, &[], limit));
        }
    }
}
