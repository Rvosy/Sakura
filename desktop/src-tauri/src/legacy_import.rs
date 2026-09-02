use std::{
    ffi::OsString,
    fs,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};

use crate::{
    platform::{
        FilesystemRuntimeLocator, ManagedPipeReadOutcome, ManagedPipeReader, ManagedProcessRequest,
        ManagedProcessTreeBackend, NativeManagedProcessTreeBackend, ProcessExitStatus,
        ProcessStdio, RuntimeLocationRequest, RuntimeLocator,
    },
    product_shell,
    runtime_log::{looks_absolute_path, Correlation, RuntimeLogEvent, RuntimeLogService, Severity},
    ShellLifecycleState,
};

pub const LEGACY_IMPORT_PROGRESS_EVENT: &str = "sakura://legacy-import-progress";
const CORE_VALIDATION_DEADLINE: Duration = Duration::from_secs(60);
const LEGACY_PIPE_POLL_INTERVAL: Duration = Duration::from_secs(1);
const LEGACY_PROCESS_FINALIZE_DEADLINE: Duration = Duration::from_secs(10);
const LEGACY_PROCESS_TERMINATE_REASON: u32 = 76;
const LEGACY_IMPORT_OPERATION_TIMEOUT: &str = "LEGACY_IMPORT_OPERATION_TIMEOUT";
const LEGACY_IMPORT_PROCESS_TERMINATION_FAILED: &str = "LEGACY_IMPORT_PROCESS_TERMINATION_FAILED";
const LEGACY_IMPORT_CORE_STOP_FAILED: &str = "LEGACY_IMPORT_CORE_STOP_FAILED";

fn legacy_action_deadline(action: &str) -> Result<Duration, String> {
    match action {
        "inspect-data" => Ok(Duration::from_secs(15 * 60)),
        "inspect" | "recover" | "finalize" | "rollback" | "apply-data" => {
            Ok(Duration::from_secs(30 * 60))
        }
        "run" => Ok(Duration::from_secs(2 * 60 * 60)),
        _ => Err("LEGACY_IMPORT_ACTION_INVALID".to_string()),
    }
}

fn process_tree_state_is_unknown(code: &str) -> bool {
    code == LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
}

fn fail_incremental_finalize_with_unknown_process<T>(
    error: String,
    stop_core: impl FnOnce(Duration) -> Result<(), &'static str>,
) -> Result<T, String> {
    stop_core(Duration::from_secs(15)).map_err(|_| LEGACY_IMPORT_CORE_STOP_FAILED.to_string())?;
    Err(error)
}

#[derive(Clone, Debug, PartialEq, Eq)]
enum JournalState {
    Missing,
    Readable(String),
    Unreadable,
}

pub fn recover_interrupted(request: &RuntimeLocationRequest) -> Result<bool, String> {
    let pending = fs::read_dir(&request.user_root)
        .map_err(|_| "LEGACY_IMPORT_RECOVERY_FAILED".to_string())?
        .filter_map(Result::ok)
        .any(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.starts_with(".legacy-import-journal-")
                || name.starts_with(".legacy-import-backup-")
                || name.starts_with(".legacy-import-staging-")
                || name.starts_with(".legacy-import-cancel-")
        });
    if !pending {
        return Ok(false);
    }
    let output = run_python(
        request,
        "recover",
        &[("--target", request.user_root.as_path())],
    )?;
    if output
        .iter()
        .any(|value| value.get("type").and_then(Value::as_str) == Some("recovery"))
    {
        Ok(true)
    } else {
        Err("LEGACY_IMPORT_RECOVERY_FAILED".to_string())
    }
}

#[derive(Clone)]
struct Selection {
    id: String,
    source: PathBuf,
    overwrite_domains: Vec<String>,
}

#[derive(Clone)]
struct DataSelection {
    id: String,
    source: PathBuf,
    plan_token: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct LegacyImportSnapshot {
    schema_version: u32,
    state: String,
    selection_id: Option<String>,
    source_label: Option<String>,
    inspection: Option<Value>,
    stage: String,
    percent: u8,
    message: String,
    cancellable: bool,
    requires_setup: bool,
    warnings: Vec<Value>,
    error: Option<Value>,
}

impl Default for LegacyImportSnapshot {
    fn default() -> Self {
        Self {
            schema_version: 1,
            state: "idle".to_string(),
            selection_id: None,
            source_label: None,
            inspection: None,
            stage: "idle".to_string(),
            percent: 0,
            message: String::new(),
            cancellable: false,
            requires_setup: false,
            warnings: Vec::new(),
            error: None,
        }
    }
}

struct Inner {
    selection: Option<Selection>,
    data_selection: Option<DataSelection>,
    import_id: Option<String>,
    snapshot: LegacyImportSnapshot,
}

pub struct LegacyImportState {
    request: RuntimeLocationRequest,
    inner: Mutex<Inner>,
}

impl LegacyImportState {
    pub fn new(request: RuntimeLocationRequest) -> Self {
        Self {
            request,
            inner: Mutex::new(Inner {
                selection: None,
                data_selection: None,
                import_id: None,
                snapshot: LegacyImportSnapshot::default(),
            }),
        }
    }

    fn snapshot(&self) -> Result<LegacyImportSnapshot, String> {
        self.inner
            .lock()
            .map(|inner| inner.snapshot.clone())
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())
    }

    fn publish(
        &self,
        app: &AppHandle,
        mutate: impl FnOnce(&mut LegacyImportSnapshot),
    ) -> Result<LegacyImportSnapshot, String> {
        let snapshot = {
            let mut inner = self
                .inner
                .lock()
                .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
            mutate(&mut inner.snapshot);
            inner.snapshot.clone()
        };
        // WebView event delivery must not run inline on the import worker. On
        // Windows an inline `emit` can wait on the WebView thread while that
        // same thread is invoking/polling this state, freezing the worker at
        // its first progress update. The snapshot remains the source of truth;
        // the event is only a low-latency hint for the polling UI.
        let dispatch = app.clone();
        let publication = snapshot.clone();
        app.run_on_main_thread(move || {
            let _ = dispatch.emit_to(
                product_shell::SETTINGS_WINDOW_LABEL,
                LEGACY_IMPORT_PROGRESS_EVENT,
                publication,
            );
        })
        .map_err(|_| "LEGACY_IMPORT_PROGRESS_PUBLISH_FAILED".to_string())?;
        Ok(snapshot)
    }
}

#[tauri::command]
pub fn legacy_import_choose_source(
    window: WebviewWindow,
    state: State<'_, Arc<LegacyImportState>>,
    first_run: State<'_, product_shell::FirstRunGuideState>,
) -> Result<LegacyImportSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    ensure_first_run_pending(&first_run)?;
    if matches!(
        state.snapshot()?.state.as_str(),
        "inspecting" | "staging" | "validating" | "committing" | "core_validating"
    ) {
        return Err("LEGACY_IMPORT_BUSY".to_string());
    }
    let Some(source) = rfd::FileDialog::new()
        .set_title("选择旧版本 Sakura 目录")
        .pick_folder()
    else {
        return state.snapshot();
    };
    let source = source
        .canonicalize()
        .map_err(|_| "LEGACY_SOURCE_UNAVAILABLE".to_string())?;
    let source_label = source
        .file_name()
        .and_then(|value| value.to_str())
        .filter(|value| !value.is_empty())
        .unwrap_or("已选择旧版本")
        .chars()
        .take(120)
        .collect::<String>();
    let selection_id = uuid::Uuid::new_v4().simple().to_string();
    let snapshot = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        inner.selection = Some(Selection {
            id: selection_id.clone(),
            source,
            overwrite_domains: Vec::new(),
        });
        inner.import_id = None;
        inner.snapshot = LegacyImportSnapshot {
            schema_version: 1,
            state: "selected".to_string(),
            selection_id: Some(selection_id),
            source_label: Some(source_label),
            inspection: None,
            stage: "selected".to_string(),
            percent: 0,
            message: String::new(),
            cancellable: false,
            requires_setup: false,
            warnings: Vec::new(),
            error: None,
        };
        inner.snapshot.clone()
    };
    Ok(snapshot)
}

#[tauri::command]
pub async fn legacy_import_inspect(
    window: WebviewWindow,
    selection_id: String,
    state: State<'_, Arc<LegacyImportState>>,
    first_run: State<'_, product_shell::FirstRunGuideState>,
) -> Result<LegacyImportSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    ensure_first_run_pending(&first_run)?;
    let app = window.app_handle().clone();
    log_import_step(
        &app,
        Severity::Info,
        "legacy_import.inspect_started",
        "开始检查旧版本迁移来源",
        &selection_id,
        json!({}),
    );
    let (source, request) = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        if inner.snapshot.state != "selected" {
            return Err(if inner.snapshot.state == "inspecting" {
                "LEGACY_IMPORT_BUSY".to_string()
            } else {
                "LEGACY_IMPORT_NOT_READY".to_string()
            });
        }
        let source = inner
            .selection
            .as_ref()
            .filter(|selection| selection.id == selection_id)
            .map(|selection| selection.source.clone())
            .ok_or_else(|| "LEGACY_IMPORT_SELECTION_INVALID".to_string())?;
        inner.snapshot.state = "inspecting".to_string();
        inner.snapshot.stage = "inspecting".to_string();
        inner.snapshot.percent = 0;
        inner.snapshot.message = "正在扫描旧版本数据，文件较多时可能需要几分钟。".to_string();
        inner.snapshot.cancellable = false;
        inner.snapshot.error = None;
        (source, state.request.clone())
    };
    if let Err(code) = state.publish(&app, |_| {}) {
        let _ = restore_selected_after_inspection_failure(&state, &app, &selection_id);
        return Err(code);
    }
    let inspected = tauri::async_runtime::spawn_blocking(move || {
        run_python(
            &request,
            "inspect",
            &[
                ("--source", source.as_path()),
                ("--target", request.user_root.as_path()),
            ],
        )
    })
    .await;
    let values = match inspected {
        Ok(Ok(values)) => values,
        Ok(Err(code)) => {
            let _ = restore_selected_after_inspection_failure(&state, &app, &selection_id);
            log_import_step(
                &app,
                Severity::Error,
                "legacy_import.inspect_failed",
                "旧版本迁移来源检查失败",
                &selection_id,
                json!({"code": code, "stage": "inspect"}),
            );
            return Err(code);
        }
        Err(_) => {
            let _ = restore_selected_after_inspection_failure(&state, &app, &selection_id);
            log_import_step(
                &app,
                Severity::Error,
                "legacy_import.inspect_failed",
                "旧版本迁移来源检查失败",
                &selection_id,
                json!({"code": "LEGACY_RUNTIME_FAILED", "stage": "inspect"}),
            );
            return Err("LEGACY_RUNTIME_FAILED".to_string());
        }
    };
    let parsed = (|| {
        let inspection = values
            .iter()
            .find(|value| value.get("type").and_then(Value::as_str) == Some("inspection"))
            .and_then(|value| value.get("inspection"))
            .cloned()
            .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?;
        let overwrite_domains = inspection
            .get("overwriteDomains")
            .and_then(Value::as_array)
            .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?
            .iter()
            .map(|value| {
                value
                    .as_str()
                    .map(str::to_string)
                    .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        Ok::<_, String>((inspection, overwrite_domains))
    })();
    let (inspection, overwrite_domains) = match parsed {
        Ok(parsed) => parsed,
        Err(code) => {
            let _ = restore_selected_after_inspection_failure(&state, &app, &selection_id);
            log_import_step(
                &app,
                Severity::Error,
                "legacy_import.inspect_failed",
                "旧版本迁移来源检查失败",
                &selection_id,
                json!({"code": code, "stage": "inspect"}),
            );
            return Err(code);
        }
    };
    let blocker_codes = inspection
        .get("blockers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| item.get("code").and_then(Value::as_str))
        .take(32)
        .collect::<Vec<_>>();
    let warning_codes = inspection
        .get("warnings")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| item.get("code").and_then(Value::as_str))
        .take(32)
        .collect::<Vec<_>>();
    log_import_step(
        &app,
        if blocker_codes.is_empty() {
            Severity::Info
        } else {
            Severity::Warning
        },
        "legacy_import.inspect_completed",
        "旧版本迁移来源检查完成",
        &selection_id,
        json!({
            "compatible": inspection.get("compatible"),
            "detected_version": inspection.get("detectedVersion"),
            "source_platform": inspection.get("sourcePlatform"),
            "required_bytes": inspection.get("requiredBytes"),
            "available_bytes": inspection.get("availableBytes"),
            "overwrite_domains": overwrite_domains.len(),
            "blocker_codes": blocker_codes,
            "warning_codes": warning_codes,
        }),
    );
    let snapshot = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        let selection = inner
            .selection
            .as_mut()
            .filter(|selection| selection.id == selection_id)
            .ok_or_else(|| "LEGACY_IMPORT_SELECTION_INVALID".to_string())?;
        selection.overwrite_domains = overwrite_domains;
        inner.snapshot.state = "ready".to_string();
        inner.snapshot.stage = "ready".to_string();
        inner.snapshot.message.clear();
        inner.snapshot.inspection = Some(inspection);
        inner.snapshot.clone()
    };
    Ok(snapshot)
}

fn restore_selected_after_inspection_failure(
    state: &LegacyImportState,
    app: &AppHandle,
    selection_id: &str,
) -> Result<LegacyImportSnapshot, String> {
    state.publish(app, |snapshot| {
        restore_selected_snapshot_after_inspection_failure(snapshot, selection_id);
    })
}

fn restore_selected_snapshot_after_inspection_failure(
    snapshot: &mut LegacyImportSnapshot,
    selection_id: &str,
) {
    if snapshot.selection_id.as_deref() != Some(selection_id) || snapshot.state != "inspecting" {
        return;
    }
    snapshot.state = "selected".to_string();
    snapshot.stage = "selected".to_string();
    snapshot.percent = 0;
    snapshot.message.clear();
    snapshot.cancellable = false;
}

#[tauri::command]
pub fn legacy_import_state(
    window: WebviewWindow,
    state: State<'_, Arc<LegacyImportState>>,
) -> Result<LegacyImportSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    state.snapshot()
}

#[tauri::command]
pub fn legacy_import_start(
    window: WebviewWindow,
    selection_id: String,
    confirmed_overwrite_domains: Vec<String>,
    state: State<'_, Arc<LegacyImportState>>,
    first_run: State<'_, product_shell::FirstRunGuideState>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<LegacyImportSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    ensure_first_run_pending(&first_run)?;
    let handle = lifecycle
        .handle
        .as_ref()
        .ok_or_else(|| "LEGACY_CORE_UNAVAILABLE".to_string())?;
    if !handle
        .is_stopped()
        .map_err(|_| "LEGACY_CORE_STATE_UNAVAILABLE".to_string())?
    {
        return Err("LEGACY_IMPORT_CORE_RUNNING".to_string());
    }
    let app = window.app_handle().clone();
    let state = state.inner().clone();
    let (source, overwrite_domains, import_id, snapshot) = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        if inner.snapshot.state != "ready" {
            return Err("LEGACY_IMPORT_NOT_READY".to_string());
        }
        let selection = inner
            .selection
            .as_ref()
            .filter(|selection| selection.id == selection_id)
            .cloned()
            .ok_or_else(|| "LEGACY_IMPORT_SELECTION_INVALID".to_string())?;
        if selection.overwrite_domains != confirmed_overwrite_domains {
            return Err("LEGACY_IMPORT_CONFIRMATION_REQUIRED".to_string());
        }
        let import_id = uuid::Uuid::new_v4().simple().to_string();
        inner.import_id = Some(import_id.clone());
        inner.snapshot.state = "staging".to_string();
        inner.snapshot.stage = "staging".to_string();
        inner.snapshot.percent = 1;
        inner.snapshot.message = "迁移已开始，正在准备数据".to_string();
        inner.snapshot.cancellable = true;
        inner.snapshot.warnings.clear();
        inner.snapshot.error = None;
        (
            selection.source,
            selection.overwrite_domains,
            import_id,
            inner.snapshot.clone(),
        )
    };
    let request = state.request.clone();
    thread::spawn(move || {
        run_import_worker(app, state, request, source, overwrite_domains, import_id)
    });
    Ok(snapshot)
}

#[tauri::command]
pub fn legacy_import_cancel(
    window: WebviewWindow,
    state: State<'_, Arc<LegacyImportState>>,
    first_run: State<'_, product_shell::FirstRunGuideState>,
) -> Result<LegacyImportSnapshot, String> {
    product_shell::validate_settings_window(&window)?;
    ensure_first_run_pending(&first_run)?;
    let import_id = {
        let inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        if !inner.snapshot.cancellable {
            return Err("LEGACY_IMPORT_NOT_CANCELLABLE".to_string());
        }
        inner
            .import_id
            .clone()
            .ok_or_else(|| "LEGACY_IMPORT_NOT_RUNNING".to_string())?
    };
    fs::write(
        state
            .request
            .user_root
            .join(format!(".legacy-import-cancel-{import_id}")),
        b"cancel\n",
    )
    .map_err(|_| "LEGACY_IMPORT_CANCEL_FAILED".to_string())?;
    state.publish(window.app_handle(), |snapshot| {
        snapshot.message = "正在取消迁移并清理暂存文件，大型 TTS 可能需要几分钟".to_string();
        snapshot.cancellable = false;
    })
}

#[tauri::command]
pub async fn settings_legacy_data_import_choose(
    window: WebviewWindow,
    state: State<'_, Arc<LegacyImportState>>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Option<Value>, String> {
    product_shell::validate_settings_window(&window)?;
    let selected = tauri::async_runtime::spawn_blocking(|| {
        rfd::FileDialog::new()
            .set_title("选择 Sakura 0.9.x 目录")
            .pick_folder()
    })
    .await
    .map_err(|_| "LEGACY_DATA_SOURCE_CHOOSER_FAILED".to_string())?;
    let Some(source) = selected else {
        return Ok(None);
    };
    let source = source
        .canonicalize()
        .map_err(|_| "LEGACY_SOURCE_UNAVAILABLE".to_string())?;
    let request = state.request.clone();
    let handle = lifecycle
        .handle
        .clone()
        .ok_or_else(|| "LEGACY_CORE_UNAVAILABLE".to_string())?;
    let work_handle = handle.clone();
    let work_source = source.clone();
    let result = tauri::async_runtime::spawn_blocking(move || {
        work_handle
            .stop_and_wait(Duration::from_secs(15))
            .map_err(str::to_string)?;
        let inspected = run_data_python(
            &request,
            "inspect-data",
            &[
                ("--source", work_source.to_string_lossy().as_ref()),
                ("--target", request.user_root.to_string_lossy().as_ref()),
            ],
        );
        if inspected
            .as_ref()
            .is_err_and(|error| process_tree_state_is_unknown(error))
        {
            return inspected;
        }
        let restarted = start_core_and_wait_usable(&work_handle).map_err(str::to_string);
        match (inspected, restarted) {
            (Ok(values), Ok(())) => Ok(values),
            (_, Err(error)) => Err(error),
            (Err(error), Ok(())) => Err(error),
        }
    })
    .await
    .map_err(|_| "LEGACY_RUNTIME_FAILED".to_string())??;
    let mut plan = result
        .iter()
        .find(|value| value.get("type").and_then(Value::as_str) == Some("data-import-plan"))
        .and_then(|value| value.get("plan"))
        .cloned()
        .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?;
    let plan_token = plan
        .get("planToken")
        .and_then(Value::as_str)
        .filter(|value| value.len() == 64)
        .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?
        .to_string();
    let selection_id = uuid::Uuid::new_v4().simple().to_string();
    state
        .inner
        .lock()
        .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?
        .data_selection = Some(DataSelection {
        id: selection_id.clone(),
        source,
        plan_token,
    });
    plan.as_object_mut()
        .ok_or_else(|| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?
        .insert("selectionId".to_string(), Value::String(selection_id));
    Ok(Some(plan))
}

#[tauri::command]
pub async fn settings_legacy_data_import_apply(
    window: WebviewWindow,
    selection_id: String,
    plan_token: String,
    overwrite_conflicts: bool,
    state: State<'_, Arc<LegacyImportState>>,
    lifecycle: State<'_, ShellLifecycleState>,
) -> Result<Value, String> {
    product_shell::validate_settings_window(&window)?;
    let selection = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        let selection = inner
            .data_selection
            .as_ref()
            .filter(|selection| selection.id == selection_id && selection.plan_token == plan_token)
            .cloned()
            .ok_or_else(|| "LEGACY_DATA_IMPORT_SELECTION_INVALID".to_string())?;
        // A plan authorizes one apply attempt. Every retry must inspect the
        // current source and target again, including after a stale-plan error.
        inner.data_selection = None;
        selection
    };
    let request = state.request.clone();
    let handle = lifecycle
        .handle
        .clone()
        .ok_or_else(|| "LEGACY_CORE_UNAVAILABLE".to_string())?;
    let import_id = uuid::Uuid::new_v4().simple().to_string();
    let work_import_id = import_id.clone();
    let work = tauri::async_runtime::spawn_blocking(move || {
        handle
            .stop_and_wait(Duration::from_secs(15))
            .map_err(str::to_string)?;
        let mut arguments = vec![
            ("--source", selection.source.to_string_lossy().to_string()),
            ("--target", request.user_root.to_string_lossy().to_string()),
            ("--import-id", work_import_id.clone()),
            ("--plan-token", plan_token),
        ];
        if overwrite_conflicts {
            arguments.push(("--overwrite-conflicts", String::new()));
        }
        let borrowed = arguments
            .iter()
            .map(|(name, value)| (*name, value.as_str()))
            .collect::<Vec<_>>();
        let applied = run_data_python(&request, "apply-data", &borrowed);
        let values = match applied {
            Ok(values) => values,
            Err(error) if process_tree_state_is_unknown(&error) => return Err(error),
            Err(error) => {
                recover_transaction(&request, &work_import_id)?;
                return match start_core_and_wait_usable(&handle) {
                    Ok(()) => Err(error),
                    Err(restart_error) => Err(restart_error.to_string()),
                };
            }
        };
        if commit_journal_state(&request, &work_import_id)
            != JournalState::Readable("pending_core_validation".to_string())
        {
            recover_transaction(&request, &work_import_id)?;
            start_core_and_wait_usable(&handle).map_err(str::to_string)?;
            return Err("LEGACY_IMPORT_RESULT_INVALID".to_string());
        }
        let report = match values
            .iter()
            .find(|value| value.get("type").and_then(Value::as_str) == Some("data-import-result"))
            .and_then(|value| value.get("report"))
            .cloned()
        {
            Some(report) => report,
            None => {
                recover_transaction(&request, &work_import_id)?;
                start_core_and_wait_usable(&handle).map_err(str::to_string)?;
                return Err("LEGACY_IMPORT_PROTOCOL_INVALID".to_string());
            }
        };
        if let Err(error) = start_core_and_wait_usable(&handle) {
            let _ = handle.stop_and_wait(Duration::from_secs(15));
            recover_transaction(&request, &work_import_id)?;
            return match start_core_and_wait_usable(&handle) {
                Ok(()) => Err(error.to_string()),
                Err(restart_error) => Err(restart_error.to_string()),
            };
        }
        if let Err(error) = run_simple_action(&request, "finalize", &work_import_id) {
            if process_tree_state_is_unknown(&error) {
                return fail_incremental_finalize_with_unknown_process(error, |timeout| {
                    handle.stop_and_wait(timeout)
                });
            }
            let _ = handle.stop_and_wait(Duration::from_secs(15));
            recover_transaction(&request, &work_import_id)?;
            start_core_and_wait_usable(&handle).map_err(str::to_string)?;
            return Err(error);
        }
        if commit_journal_state(&request, &work_import_id) != JournalState::Missing {
            let _ = handle.stop_and_wait(Duration::from_secs(15));
            recover_transaction(&request, &work_import_id)?;
            start_core_and_wait_usable(&handle).map_err(str::to_string)?;
        }
        Ok(report)
    })
    .await
    .map_err(|_| "LEGACY_RUNTIME_FAILED".to_string())??;
    let mut report = work;
    if let Some(object) = report.as_object_mut() {
        object.insert(
            "outcome".to_string(),
            Value::String("completed".to_string()),
        );
    }
    Ok(report)
}

fn start_core_and_wait_usable(
    handle: &crate::shell_lifecycle::ShellLifecycleHandle,
) -> Result<(), &'static str> {
    handle.start_core_and_wait_available(CORE_VALIDATION_DEADLINE)?;
    let deadline = Instant::now() + CORE_VALIDATION_DEADLINE;
    loop {
        match handle.readiness()? {
            Some(readiness)
                if matches!(readiness.as_str(), "ready" | "degraded" | "setup_required") =>
            {
                return Ok(())
            }
            _ if Instant::now() >= deadline => return Err("CORE_START_TIMEOUT"),
            _ => thread::sleep(Duration::from_millis(20)),
        }
    }
}

fn ensure_first_run_pending(state: &product_shell::FirstRunGuideState) -> Result<(), String> {
    if state.snapshot()?.completed {
        return Err("LEGACY_IMPORT_FIRST_RUN_ONLY".to_string());
    }
    Ok(())
}

fn run_import_worker(
    app: AppHandle,
    state: Arc<LegacyImportState>,
    request: RuntimeLocationRequest,
    source: PathBuf,
    confirmed_overwrite_domains: Vec<String>,
    import_id: String,
) {
    log_import_step(
        &app,
        Severity::Info,
        "legacy_import.worker_started",
        "迁移工作线程已启动",
        &import_id,
        json!({}),
    );
    let runtime_log = app.state::<RuntimeLogService>().inner().clone();
    let outcome = stream_run(
        &request,
        &source,
        &confirmed_overwrite_domains,
        &import_id,
        &runtime_log,
        |publication| {
            if publication.get("type").and_then(Value::as_str) == Some("inspection") {
                let inspection = publication.get("inspection").cloned();
                let _ = state.publish(&app, |snapshot| {
                    snapshot.inspection = inspection;
                });
                return;
            }
            if publication.get("type").and_then(Value::as_str) != Some("progress") {
                return;
            }
            let stage = publication
                .get("stage")
                .and_then(Value::as_str)
                .unwrap_or("staging")
                .to_string();
            let percent = publication
                .get("percent")
                .and_then(Value::as_u64)
                .and_then(|value| u8::try_from(value).ok())
                .unwrap_or(0);
            let message = publication
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string();
            let cancellable = publication
                .get("cancellable")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            log_import_step(
                &app,
                Severity::Info,
                "legacy_import.progress_received",
                "桌面端收到迁移进度",
                &import_id,
                json!({"stage": stage, "percent": percent, "message": message}),
            );
            let _ = state.publish(&app, |snapshot| {
                snapshot.state = stage.clone();
                snapshot.stage = stage;
                snapshot.percent = percent;
                snapshot.message = message;
                snapshot.cancellable = cancellable;
            });
        },
    );
    match outcome {
        Ok(result)
            if result.get("state").and_then(Value::as_str) == Some("core_validating")
                && commit_is_pending_core_validation(&request, &import_id) =>
        {
            let warnings = result
                .get("report")
                .and_then(|report| report.get("warnings"))
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            let _ = state.publish(&app, |snapshot| {
                snapshot.warnings = warnings;
            });
            log_import_step(
                &app,
                Severity::Info,
                "legacy_import.core_validation_entered",
                "桌面端开始 Core 校验",
                &import_id,
                json!({}),
            );
            let _ = state.publish(&app, |snapshot| {
                snapshot.state = "core_validating".to_string();
                snapshot.stage = "core_validating".to_string();
                snapshot.percent = 98;
                snapshot.message = "正在启动 Sakura Core 并校验数据".to_string();
                snapshot.cancellable = false;
            });
            validate_with_core(&app, &state, &request, &import_id);
        }
        Ok(_result) => {
            let recovery = recover_transaction(&request, &import_id);
            let code = if recovery.is_ok() {
                "LEGACY_IMPORT_RESULT_INVALID"
            } else {
                "LEGACY_IMPORT_RECOVERY_FAILED"
            };
            log_import_step(
                &app,
                Severity::Error,
                "legacy_import.result_invalid",
                "迁移结果与事务状态不一致",
                &import_id,
                json!({
                    "code": code,
                    "diagnostic": "迁移子进程结果与持久事务状态不一致",
                    "error_type": "LegacyImportProtocolError",
                    "reason_code": code,
                    "stage": "result_validation"
                }),
            );
            fail_publication(&app, &state, json!({"code":code,"stage":"staging"}));
        }
        Err(mut error) => {
            let original_code = error.get("code").and_then(Value::as_str).map(str::to_owned);
            let process_state_unknown = original_code
                .as_deref()
                .is_some_and(process_tree_state_is_unknown);
            if !process_state_unknown {
                match recover_transaction(&request, &import_id) {
                    Ok(()) => {
                        if original_code.as_deref() == Some(LEGACY_IMPORT_OPERATION_TIMEOUT) {
                            let restart = app
                                .state::<ShellLifecycleState>()
                                .handle
                                .as_ref()
                                .ok_or("LEGACY_CORE_UNAVAILABLE")
                                .and_then(start_core_and_wait_usable);
                            if let Err(code) = restart {
                                error = json!({"code":code,"stage":"core_restart"});
                            }
                        }
                    }
                    Err(code) => {
                        let code = if process_tree_state_is_unknown(&code) {
                            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
                        } else {
                            "LEGACY_IMPORT_RECOVERY_FAILED"
                        };
                        error = json!({"code":code,"stage":"recovery"});
                    }
                }
            }
            let cancelled =
                error.get("code").and_then(Value::as_str) == Some("LEGACY_IMPORT_CANCELLED");
            if !cancelled {
                let code = error
                    .get("code")
                    .and_then(Value::as_str)
                    .unwrap_or("LEGACY_IMPORT_FAILED");
                log_import_step(
                    &app,
                    Severity::Error,
                    "legacy_import.failed",
                    "旧版本迁移失败",
                    &import_id,
                    json!({
                        "code": code,
                        "reason_code": code,
                        "error_type": "LegacyImportError",
                    }),
                );
            }
            let _ = state.publish(&app, |snapshot| {
                snapshot.state = if cancelled { "cancelled" } else { "failed" }.to_string();
                snapshot.stage = error
                    .get("stage")
                    .and_then(Value::as_str)
                    .unwrap_or("staging")
                    .to_string();
                snapshot.message.clear();
                snapshot.cancellable = false;
                snapshot.error = if cancelled { None } else { Some(error) };
            });
        }
    }
}

fn validate_with_core(
    app: &AppHandle,
    state: &Arc<LegacyImportState>,
    request: &RuntimeLocationRequest,
    import_id: &str,
) {
    let lifecycle = app.state::<ShellLifecycleState>();
    let Some(handle) = lifecycle.handle.clone() else {
        rollback_after_core_failure(app, state, request, import_id, "LEGACY_CORE_UNAVAILABLE");
        return;
    };
    if handle.start_core().is_err() {
        log_import_step(
            app,
            Severity::Error,
            "legacy_import.core_start_failed",
            "迁移后 Core 启动请求失败",
            import_id,
            json!({
                "code": "LEGACY_CORE_START_FAILED",
                "diagnostic": "迁移后的 Core 启动请求未能提交",
                "error_type": "CoreStartError",
                "reason_code": "LEGACY_CORE_START_FAILED",
                "stage": "core_start"
            }),
        );
        rollback_after_core_failure(app, state, request, import_id, "LEGACY_CORE_START_FAILED");
        return;
    }
    log_import_step(
        app,
        Severity::Info,
        "legacy_import.core_start_submitted",
        "迁移后 Core 启动请求已提交",
        import_id,
        json!({}),
    );
    let deadline = Instant::now() + CORE_VALIDATION_DEADLINE;
    let completed_with_warnings = state
        .snapshot()
        .map(|snapshot| {
            snapshot.warnings.iter().any(|warning| {
                warning
                    .get("code")
                    .and_then(Value::as_str)
                    .is_some_and(|code| code.ends_with("_SKIPPED"))
            })
        })
        .unwrap_or(false);
    let mut previous_readiness: Option<String> = None;
    while Instant::now() < deadline {
        match handle.readiness() {
            Ok(Some(readiness)) if matches!(readiness.as_str(), "ready" | "degraded") => {
                log_import_step(
                    app,
                    Severity::Info,
                    "legacy_import.core_ready",
                    "迁移数据通过 Core 校验",
                    import_id,
                    json!({"readiness": readiness}),
                );
                let finalization = run_simple_action(request, "finalize", import_id);
                if finalization
                    .as_ref()
                    .is_err_and(|error| process_tree_state_is_unknown(error))
                {
                    let _ = handle.stop_and_wait(Duration::from_secs(15));
                    fail_publication(
                        app,
                        state,
                        json!({"code":LEGACY_IMPORT_PROCESS_TERMINATION_FAILED,"stage":"finalize"}),
                    );
                    return;
                }
                if finalization.is_err()
                    || commit_journal_state(request, import_id) != JournalState::Missing
                {
                    rollback_after_core_failure(
                        app,
                        state,
                        request,
                        import_id,
                        "LEGACY_FINALIZE_FAILED",
                    );
                    return;
                }
                let completed = app
                    .state::<product_shell::FirstRunGuideState>()
                    .complete()
                    .is_ok();
                if !completed {
                    fail_publication(
                        app,
                        state,
                        json!({"code":"LEGACY_COMPLETION_FAILED","stage":"completed"}),
                    );
                    return;
                }
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.show();
                    let _ = product_shell::sync_product_tray_visibility(app, true);
                }
                log_import_step(
                    app,
                    Severity::Info,
                    "legacy_import.completed",
                    "旧版本迁移已完成",
                    import_id,
                    json!({}),
                );
                let _ = state.publish(app, |snapshot| {
                    snapshot.state = "completed".to_string();
                    snapshot.stage = "completed".to_string();
                    snapshot.percent = 100;
                    snapshot.message = if completed_with_warnings {
                        "核心聊天和记忆已迁移，部分角色或语音资源可稍后补充".to_string()
                    } else {
                        "迁移完成".to_string()
                    };
                    snapshot.requires_setup = false;
                    snapshot.cancellable = false;
                });
                return;
            }
            Ok(Some(readiness)) if readiness == "setup_required" => {
                log_import_step(
                    app,
                    Severity::Info,
                    "legacy_import.core_setup_required",
                    "Core 已读取迁移数据，仍需补充首次设置",
                    import_id,
                    json!({"readiness": readiness}),
                );
                let finalization = run_simple_action(request, "finalize", import_id);
                if finalization
                    .as_ref()
                    .is_err_and(|error| process_tree_state_is_unknown(error))
                {
                    let _ = handle.stop_and_wait(Duration::from_secs(15));
                    fail_publication(
                        app,
                        state,
                        json!({"code":LEGACY_IMPORT_PROCESS_TERMINATION_FAILED,"stage":"finalize"}),
                    );
                    return;
                }
                if finalization.is_err()
                    || commit_journal_state(request, import_id) != JournalState::Missing
                {
                    rollback_after_core_failure(
                        app,
                        state,
                        request,
                        import_id,
                        "LEGACY_FINALIZE_FAILED",
                    );
                    return;
                }
                log_import_step(
                    app,
                    Severity::Info,
                    "legacy_import.completed",
                    "旧版本迁移已完成",
                    import_id,
                    json!({}),
                );
                let _ = state.publish(app, |snapshot| {
                    snapshot.state = "completed".to_string();
                    snapshot.stage = "completed".to_string();
                    snapshot.percent = 100;
                    snapshot.message = if completed_with_warnings {
                        "核心聊天和记忆已迁移，部分资源已跳过，请补充首次设置".to_string()
                    } else {
                        "数据迁移完成，请补充首次设置".to_string()
                    };
                    snapshot.requires_setup = true;
                    snapshot.cancellable = false;
                });
                return;
            }
            Ok(Some(readiness)) if readiness == "failed" => {
                log_import_step(
                    app,
                    Severity::Error,
                    "legacy_import.core_validation_failed",
                    "迁移数据未通过 Core 校验",
                    import_id,
                    json!({
                        "code": "LEGACY_CORE_VALIDATION_FAILED",
                        "diagnostic": "Core readiness 报告迁移数据校验失败",
                        "error_type": "CoreValidationError",
                        "reason_code": "LEGACY_CORE_VALIDATION_FAILED",
                        "stage": "core_validation",
                        "readiness": readiness
                    }),
                );
                rollback_after_core_failure(
                    app,
                    state,
                    request,
                    import_id,
                    "LEGACY_CORE_VALIDATION_FAILED",
                );
                return;
            }
            Err(_) => {
                rollback_after_core_failure(
                    app,
                    state,
                    request,
                    import_id,
                    "LEGACY_CORE_VALIDATION_FAILED",
                );
                return;
            }
            Ok(readiness) => {
                if readiness != previous_readiness {
                    log_import_step(
                        app,
                        Severity::Info,
                        "legacy_import.core_readiness_changed",
                        "Core 校验状态已变化",
                        import_id,
                        json!({"readiness": readiness}),
                    );
                    previous_readiness = readiness;
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
    }
    rollback_after_core_failure(
        app,
        state,
        request,
        import_id,
        "LEGACY_CORE_VALIDATION_TIMEOUT",
    );
}

fn rollback_after_core_failure(
    app: &AppHandle,
    state: &Arc<LegacyImportState>,
    request: &RuntimeLocationRequest,
    import_id: &str,
    code: &str,
) {
    if let Some(handle) = app.state::<ShellLifecycleState>().handle.as_ref() {
        let _ = handle.stop_and_wait(Duration::from_secs(10));
    }
    let recovery = recover_transaction(request, import_id);
    let public_code = match recovery.as_ref() {
        Ok(()) => code,
        Err(error) if process_tree_state_is_unknown(error) => {
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
        }
        Err(_) => "LEGACY_IMPORT_RECOVERY_FAILED",
    };
    log_import_step(
        app,
        Severity::Error,
        "legacy_import.core_validation_failed",
        "迁移数据校验失败，已执行回滚",
        import_id,
        json!({
            "code": public_code,
            "diagnostic": if recovery.is_ok() {
                "迁移数据未通过 Core 校验，事务已回滚"
            } else {
                "迁移数据未通过 Core 校验，事务恢复失败"
            },
            "error_type": if recovery.is_ok() {
                "CoreValidationError"
            } else {
                "LegacyImportRecoveryError"
            },
            "reason_code": public_code,
            "stage": "core_validating"
        }),
    );
    fail_publication(
        app,
        state,
        json!({"code":public_code,"stage":"core_validating"}),
    );
}

fn fail_publication(app: &AppHandle, state: &Arc<LegacyImportState>, error: Value) {
    let _ = state.publish(app, |snapshot| {
        snapshot.state = "failed".to_string();
        snapshot.stage = error
            .get("stage")
            .and_then(Value::as_str)
            .unwrap_or("internal")
            .to_string();
        snapshot.message.clear();
        snapshot.cancellable = false;
        snapshot.error = Some(error);
    });
}

fn stream_run(
    request: &RuntimeLocationRequest,
    source: &Path,
    confirmed_overwrite_domains: &[String],
    import_id: &str,
    runtime_log: &RuntimeLogService,
    mut progress: impl FnMut(&Value),
) -> Result<Value, Value> {
    let log = |severity, event: &'static str, message: &'static str, attributes| {
        let _ = runtime_log.submit(
            RuntimeLogEvent::rust(severity, "legacy_import", event, message)
                .correlation(Correlation {
                    operation_id: Some(import_id.to_string()),
                    ..Correlation::default()
                })
                .attributes(attributes),
        );
    };
    let mut arguments = vec![
        OsString::from("--source"),
        source.as_os_str().to_owned(),
        OsString::from("--target"),
        request.user_root.as_os_str().to_owned(),
        OsString::from("--import-id"),
        OsString::from(import_id),
    ];
    for domain in confirmed_overwrite_domains {
        arguments.push(OsString::from("--confirmed-overwrite-domain"));
        arguments.push(OsString::from(domain));
    }
    log(
        Severity::Info,
        "legacy_import.python_started",
        "迁移 Python 子进程已启动",
        json!({}),
    );
    let mut result = None;
    let mut public_error = None;
    let run = run_managed_python(request, "run", &arguments, |value| {
        match value.get("type").and_then(Value::as_str) {
            Some("progress" | "inspection") => progress(value),
            Some("diagnostic") => {
                let Some(event) = value.get("event").and_then(Value::as_str) else {
                    return;
                };
                let Some((event, message)) = legacy_import_event(event) else {
                    return;
                };
                let severity = match value.get("severity").and_then(Value::as_str) {
                    Some("error") => Severity::Error,
                    Some("warning" | "warn") => Severity::Warning,
                    Some("info") => Severity::Info,
                    _ => return,
                };
                let attributes = value
                    .get("attributes")
                    .filter(|attributes| attributes.is_object())
                    .map(sanitize_legacy_import_attributes)
                    .unwrap_or_else(|| json!({}));
                let _ = runtime_log.submit(
                    RuntimeLogEvent::rust(severity, "legacy_import", event, message)
                        .correlation(Correlation {
                            operation_id: Some(import_id.to_string()),
                            ..Correlation::default()
                        })
                        .attributes(attributes),
                );
            }
            Some("result") => {
                log(
                    Severity::Info,
                    "legacy_import.result_received",
                    "桌面端收到迁移结果",
                    json!({"state": value.get("state")}),
                );
                result = Some(value.clone());
            }
            Some("error") => {
                let child_error = value.get("error").and_then(Value::as_object);
                let code = child_error
                    .and_then(|error| error.get("code"))
                    .and_then(Value::as_str)
                    .filter(|value| {
                        !value.is_empty()
                            && value.len() <= 64
                            && value.bytes().all(|byte| {
                                byte.is_ascii_uppercase() || byte.is_ascii_digit() || byte == b'_'
                            })
                    })
                    .unwrap_or("LEGACY_IMPORT_CHILD_FAILED");
                let stage = child_error
                    .and_then(|error| error.get("stage"))
                    .and_then(Value::as_str)
                    .filter(|value| {
                        !value.is_empty()
                            && value.len() <= 64
                            && value.bytes().all(|byte| {
                                byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'_'
                            })
                    })
                    .unwrap_or("child_process");
                log(
                    Severity::Error,
                    "legacy_import.error_received",
                    "桌面端收到迁移错误",
                    json!({
                        "code": code,
                        "diagnostic": "迁移子进程报告执行失败",
                        "error_type": "LegacyImportChildError",
                        "reason_code": code,
                        "stage": stage
                    }),
                );
                public_error = value.get("error").cloned();
            }
            _ => {}
        }
    });
    log(
        Severity::Info,
        "legacy_import.stdout_closed",
        "迁移 Python 输出流已关闭",
        json!({}),
    );
    let succeeded = run.is_ok();
    let process_error_code = run.as_ref().err().map(String::as_str);
    let exit_attributes = if succeeded {
        json!({"succeeded": true})
    } else {
        let code = process_error_code.unwrap_or("LEGACY_RUNTIME_FAILED");
        json!({
            "succeeded": false,
            "code": code,
            "diagnostic": "迁移 Python 子进程未正常完成",
            "error_type": "LegacyImportProcessExit",
            "reason_code": code,
            "stage": "child_exit"
        })
    };
    log(
        if succeeded {
            Severity::Info
        } else {
            Severity::Error
        },
        "legacy_import.python_exited",
        "迁移 Python 子进程已退出",
        exit_attributes,
    );
    if succeeded {
        if public_error.is_some() {
            return Err(public_error.expect("public error is present"));
        }
        if commit_is_pending_core_validation(request, import_id) {
            if let Some(result) = result {
                return Ok(result);
            }
        }
        if commit_is_pending_core_validation(request, import_id) {
            log(
                Severity::Warning,
                "legacy_import.result_recovered_from_journal",
                "未收到迁移结果，已从持久事务状态恢复",
                json!({"state": "pending_core_validation"}),
            );
            return Ok(json!({
                "type": "result",
                "state": "core_validating",
                "recoveredFromJournal": true
            }));
        }
        Err(json!({"code":"LEGACY_IMPORT_RESULT_INVALID","stage":"staging"}))
    } else {
        let code = run
            .err()
            .unwrap_or_else(|| "LEGACY_RUNTIME_FAILED".to_string());
        Err(public_error.unwrap_or_else(|| json!({"code":code,"stage":"staging"})))
    }
}

fn commit_is_pending_core_validation(request: &RuntimeLocationRequest, import_id: &str) -> bool {
    commit_journal_state(request, import_id)
        == JournalState::Readable("pending_core_validation".to_string())
}

fn commit_journal_state(request: &RuntimeLocationRequest, import_id: &str) -> JournalState {
    let journal = request
        .user_root
        .join(format!(".legacy-import-journal-{import_id}.json"));
    read_journal_state(&journal)
}

fn read_journal_state(journal: &Path) -> JournalState {
    let text = match fs::read_to_string(journal) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return JournalState::Missing,
        Err(_) => return JournalState::Unreadable,
    };
    match serde_json::from_str::<Value>(&text).ok().and_then(|value| {
        value
            .get("state")
            .and_then(Value::as_str)
            .map(str::to_owned)
    }) {
        Some(state)
            if matches!(
                state.as_str(),
                "committing" | "pending_core_validation" | "rolling_back" | "finalizing"
            ) =>
        {
            JournalState::Readable(state)
        }
        None => JournalState::Unreadable,
        Some(_) => JournalState::Unreadable,
    }
}

fn log_import_step(
    app: &AppHandle,
    severity: Severity,
    event: &'static str,
    message: &'static str,
    import_id: &str,
    attributes: Value,
) {
    let runtime_log = app.state::<RuntimeLogService>();
    let _ = runtime_log.submit(
        RuntimeLogEvent::rust(severity, "legacy_import", event, message)
            .correlation(Correlation {
                operation_id: Some(import_id.to_string()),
                ..Correlation::default()
            })
            .attributes(attributes),
    );
}

fn run_simple_action(
    request: &RuntimeLocationRequest,
    action: &str,
    import_id: &str,
) -> Result<(), String> {
    let output = run_python(
        request,
        action,
        &[
            ("--target", request.user_root.as_path()),
            ("--import-id", Path::new(import_id)),
        ],
    )?;
    if output
        .iter()
        .any(|value| value.get("type").and_then(Value::as_str) == Some("error"))
    {
        return Err("LEGACY_IMPORT_ACTION_FAILED".to_string());
    }
    Ok(())
}

fn recover_transaction(request: &RuntimeLocationRequest, _import_id: &str) -> Result<(), String> {
    let output = run_python(
        request,
        "recover",
        &[("--target", request.user_root.as_path())],
    )?;
    if !output
        .iter()
        .any(|value| value.get("type").and_then(Value::as_str) == Some("recovery"))
    {
        return Err("LEGACY_IMPORT_RECOVERY_FAILED".to_string());
    }
    match commit_journal_state(request, _import_id) {
        JournalState::Missing => Ok(()),
        JournalState::Readable(_) | JournalState::Unreadable => {
            Err("LEGACY_IMPORT_RECOVERY_FAILED".to_string())
        }
    }
}

fn run_python(
    request: &RuntimeLocationRequest,
    action: &str,
    arguments: &[(&str, &Path)],
) -> Result<Vec<Value>, String> {
    let mut encoded = Vec::with_capacity(arguments.len() * 2);
    for (name, value) in arguments {
        encoded.push(OsString::from(name));
        encoded.push(value.as_os_str().to_owned());
    }
    run_managed_python(request, action, &encoded, |_| {})
}

fn run_data_python(
    request: &RuntimeLocationRequest,
    action: &str,
    arguments: &[(&str, &str)],
) -> Result<Vec<Value>, String> {
    let mut encoded = Vec::with_capacity(arguments.len() * 2);
    for (name, value) in arguments {
        encoded.push(OsString::from(name));
        if !value.is_empty() {
            encoded.push(OsString::from(value));
        }
    }
    run_managed_python(request, action, &encoded, |_| {})
}

fn run_managed_python(
    request: &RuntimeLocationRequest,
    action: &str,
    arguments: &[OsString],
    on_value: impl FnMut(&Value),
) -> Result<Vec<Value>, String> {
    let process = python_process_request(request, action, arguments)?;
    let operation_deadline = Instant::now() + legacy_action_deadline(action)?;
    run_managed_protocol(
        &NativeManagedProcessTreeBackend,
        process,
        operation_deadline,
        on_value,
    )
}

fn python_process_request(
    request: &RuntimeLocationRequest,
    action: &str,
    arguments: &[OsString],
) -> Result<ManagedProcessRequest, String> {
    let layout = FilesystemRuntimeLocator
        .locate(request)
        .map_err(|_| "LEGACY_RUNTIME_UNAVAILABLE".to_string())?;
    let bootstrap = python_bootstrap(
        &layout.python_path_entries,
        &layout.core_root,
        &layout.distribution_root,
    )?;
    let mut args = vec![
        OsString::from("-B"),
        OsString::from("-c"),
        OsString::from(bootstrap),
        OsString::from(action),
    ];
    args.extend_from_slice(arguments);
    Ok(ManagedProcessRequest {
        program: layout.python_executable,
        args,
        current_directory: Some(layout.core_root),
        environment_overrides: Vec::new(),
        stdio: ProcessStdio::Piped,
    })
}

fn run_managed_protocol(
    backend: &dyn ManagedProcessTreeBackend,
    request: ManagedProcessRequest,
    operation_deadline: Instant,
    mut on_value: impl FnMut(&Value),
) -> Result<Vec<Value>, String> {
    let spawned = backend
        .spawn(&request)
        .map_err(|_| "LEGACY_RUNTIME_UNAVAILABLE".to_string())?;
    let mut tree = spawned.tree;
    let Some(pipes) = spawned.pipes else {
        let finalization = tree.finalize_until(
            Instant::now() + LEGACY_PROCESS_FINALIZE_DEADLINE,
            LEGACY_PROCESS_TERMINATE_REASON,
        );
        return Err(if finalization.is_ok() {
            "LEGACY_RUNTIME_UNAVAILABLE".to_string()
        } else {
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED.to_string()
        });
    };
    drop(pipes.stdin);
    let cancelled = Arc::new(AtomicBool::new(false));
    let stderr_cancelled = cancelled.clone();
    let stderr_drain = thread::spawn(move || drain_managed_pipe(pipes.stderr, stderr_cancelled));
    let mut stdout = pipes.stdout;
    let mut pending = Vec::new();
    let mut buffer = [0_u8; 8192];
    let mut values = Vec::new();
    let mut protocol_error = None;
    let mut operation_timed_out = false;
    loop {
        let now = Instant::now();
        if now >= operation_deadline {
            operation_timed_out = true;
            break;
        }
        let poll_deadline = (now + LEGACY_PIPE_POLL_INTERVAL).min(operation_deadline);
        match stdout.read_until(&mut buffer, poll_deadline, cancelled.as_ref()) {
            Ok(ManagedPipeReadOutcome::Read(count)) => {
                pending.extend_from_slice(&buffer[..count]);
                while let Some(index) = pending.iter().position(|byte| *byte == b'\n') {
                    let line = pending.drain(..=index).collect::<Vec<_>>();
                    if let Err(error) = decode_protocol_line(&line, &mut values, &mut on_value) {
                        protocol_error = Some(error);
                        break;
                    }
                }
                if protocol_error.is_some() {
                    break;
                }
            }
            Ok(ManagedPipeReadOutcome::Eof) => break,
            Ok(ManagedPipeReadOutcome::TimedOut) => {
                if Instant::now() >= operation_deadline {
                    operation_timed_out = true;
                    break;
                }
                match tree.wait_root(Duration::from_millis(1)) {
                    Ok(crate::platform::ProcessWaitOutcome::Exited(_)) => break,
                    Ok(crate::platform::ProcessWaitOutcome::TimedOut) => continue,
                    Err(_) => {
                        protocol_error = Some("LEGACY_RUNTIME_FAILED".to_string());
                        break;
                    }
                }
            }
            Ok(ManagedPipeReadOutcome::Cancelled) => {
                protocol_error = Some("LEGACY_RUNTIME_FAILED".to_string());
                break;
            }
            Err(_) => {
                protocol_error = Some("LEGACY_IMPORT_PROTOCOL_INVALID".to_string());
                break;
            }
        }
    }
    if protocol_error.is_none() && !pending.iter().all(u8::is_ascii_whitespace) {
        if let Err(error) = decode_protocol_line(&pending, &mut values, &mut on_value) {
            protocol_error = Some(error);
        }
    }
    cancelled.store(true, Ordering::Release);
    let finalization = tree
        .finalize_until(
            Instant::now() + LEGACY_PROCESS_FINALIZE_DEADLINE,
            LEGACY_PROCESS_TERMINATE_REASON,
        )
        .map_err(|_| LEGACY_IMPORT_PROCESS_TERMINATION_FAILED.to_string());
    let stderr_result = stderr_drain
        .join()
        .unwrap_or_else(|_| Err("LEGACY_RUNTIME_FAILED".to_string()));
    if operation_timed_out {
        if finalization.is_err() {
            return Err(LEGACY_IMPORT_PROCESS_TERMINATION_FAILED.to_string());
        }
        return Err(LEGACY_IMPORT_OPERATION_TIMEOUT.to_string());
    }
    let finalization = finalization?;
    stderr_result?;
    if let Some(error) = protocol_error {
        return Err(error);
    }
    if finalization.root_status != ProcessExitStatus::Code(0) {
        return Err(values
            .iter()
            .find_map(|value| value.pointer("/error/code").and_then(Value::as_str))
            .unwrap_or("LEGACY_RUNTIME_FAILED")
            .to_string());
    }
    Ok(values)
}

fn decode_protocol_line(
    line: &[u8],
    values: &mut Vec<Value>,
    on_value: &mut impl FnMut(&Value),
) -> Result<(), String> {
    if line.iter().all(u8::is_ascii_whitespace) {
        return Ok(());
    }
    let value = serde_json::from_slice::<Value>(line)
        .map_err(|_| "LEGACY_IMPORT_PROTOCOL_INVALID".to_string())?;
    on_value(&value);
    values.push(value);
    Ok(())
}

fn drain_managed_pipe(
    mut pipe: Box<dyn ManagedPipeReader>,
    cancelled: Arc<AtomicBool>,
) -> Result<(), String> {
    let mut buffer = [0_u8; 8192];
    loop {
        match pipe.read_until(
            &mut buffer,
            Instant::now() + LEGACY_PIPE_POLL_INTERVAL,
            cancelled.as_ref(),
        ) {
            Ok(ManagedPipeReadOutcome::Read(_)) | Ok(ManagedPipeReadOutcome::TimedOut) => {}
            Ok(ManagedPipeReadOutcome::Eof) => return Ok(()),
            Ok(ManagedPipeReadOutcome::Cancelled) if cancelled.load(Ordering::Acquire) => {
                return Ok(())
            }
            Err(_) if cancelled.load(Ordering::Acquire) => return Ok(()),
            Ok(ManagedPipeReadOutcome::Cancelled) | Err(_) => {
                return Err("LEGACY_RUNTIME_FAILED".to_string())
            }
        }
    }
}

fn python_bootstrap(
    entries: &[PathBuf],
    core_root: &Path,
    distribution_root: &Path,
) -> Result<String, String> {
    let mut roots = entries
        .iter()
        .map(|path| path.to_string_lossy().to_string())
        .collect::<Vec<_>>();
    if !roots.iter().any(|path| Path::new(path) == core_root) {
        roots.insert(0, core_root.to_string_lossy().to_string());
    }
    if !roots
        .iter()
        .any(|path| Path::new(path) == distribution_root)
    {
        roots.insert(
            1.min(roots.len()),
            distribution_root.to_string_lossy().to_string(),
        );
    }
    let memory_dependencies = distribution_root.join("plugins/dependencies/sakura.memory.mem0");
    if memory_dependencies.is_dir()
        && !roots
            .iter()
            .any(|path| Path::new(path) == memory_dependencies)
    {
        roots.push(memory_dependencies.to_string_lossy().to_string());
    }
    let roots =
        serde_json::to_string(&roots).map_err(|_| "LEGACY_RUNTIME_UNAVAILABLE".to_string())?;
    Ok(format!(
        "import runpy,sys;sys.path[:0]={roots};sys.argv[0]='app.legacy_import';runpy.run_module('app.legacy_import',run_name='__main__')"
    ))
}

fn legacy_import_event(event: &str) -> Option<(&'static str, &'static str)> {
    Some(match event {
        "legacy_import.started" => ("legacy_import.started", "旧版本迁移开始"),
        "legacy_import.inspection_summary" => {
            ("legacy_import.inspection_summary", "旧版本迁移检查摘要")
        }
        "legacy_import.stage_started" => ("legacy_import.stage_started", "迁移阶段开始"),
        "legacy_import.stage_completed" => ("legacy_import.stage_completed", "迁移阶段完成"),
        "legacy_import.compatibility_applied" => (
            "legacy_import.compatibility_applied",
            "旧版脏数据已按兼容规则修复",
        ),
        "legacy_import.validation_failed" => {
            ("legacy_import.validation_failed", "迁移数据加载器校验失败")
        }
        "legacy_import.configuration_skipped" => (
            "legacy_import.configuration_skipped",
            "旧版本配置无法安全加载，已隔离并继续",
        ),
        "legacy_import.domain_quarantined" => {
            ("legacy_import.domain_quarantined", "迁移数据已隔离并继续")
        }
        "legacy_import.staged" => ("legacy_import.staged", "旧版本迁移已提交，等待 Core 校验"),
        "legacy_import.failed" => ("legacy_import.failed", "旧版本迁移失败"),
        "legacy_import.memory_copy_started" => (
            "legacy_import.memory_copy_started",
            "开始复制旧版本长期记忆",
        ),
        "legacy_import.memory_snapshot_started" => (
            "legacy_import.memory_snapshot_started",
            "开始创建长期记忆 SQLite 快照",
        ),
        "legacy_import.memory_snapshot_source_opened" => (
            "legacy_import.memory_snapshot_source_opened",
            "旧版本长期记忆数据库已打开",
        ),
        "legacy_import.memory_snapshot_completed" => (
            "legacy_import.memory_snapshot_completed",
            "长期记忆 SQLite 快照创建完成",
        ),
        "legacy_import.memory_snapshot_failed" => (
            "legacy_import.memory_snapshot_failed",
            "长期记忆 SQLite 快照创建失败",
        ),
        "legacy_import.memory_completed" => {
            ("legacy_import.memory_completed", "旧版本长期记忆迁移完成")
        }
        "legacy_import.memory_model_reused" => (
            "legacy_import.memory_model_reused",
            "目标中的记忆模型已通过校验",
        ),
        "legacy_import.memory_model_copied" => (
            "legacy_import.memory_model_copied",
            "随旧版本迁移的记忆模型已通过校验",
        ),
        "legacy_import.memory_model_prepared" => (
            "legacy_import.memory_model_prepared",
            "当前记忆模型已写入迁移事务并通过校验",
        ),
        "legacy_import.memory_model_failed" => (
            "legacy_import.memory_model_failed",
            "迁移所需的记忆模型准备失败",
        ),
        "legacy_import.memory_model_skipped" => (
            "legacy_import.memory_model_skipped",
            "记忆模型准备失败，已保留长期记忆数据",
        ),
        "legacy_import.memory_history_quarantined" => (
            "legacy_import.memory_history_quarantined",
            "旧版本长期记忆数据库无法读取，已隔离并继续迁移",
        ),
        "legacy_import.tts_copy_started" => ("legacy_import.tts_copy_started", "TTS 资源复制开始"),
        "legacy_import.tts_copy_completed" => {
            ("legacy_import.tts_copy_completed", "TTS 资源复制完成")
        }
        "legacy_import.tts_copy_failed" => ("legacy_import.tts_copy_failed", "TTS 资源复制失败"),
        "legacy_import.tts_copy_preflight_completed" => (
            "legacy_import.tts_copy_preflight_completed",
            "TTS 资源复制预扫描完成",
        ),
        "legacy_import.tts_copy_robocopy_started" => (
            "legacy_import.tts_copy_robocopy_started",
            "TTS 系统复制开始",
        ),
        "legacy_import.tts_copy_robocopy_completed" => (
            "legacy_import.tts_copy_robocopy_completed",
            "TTS 系统复制完成",
        ),
        "legacy_import.tts_copy_robocopy_failed" => {
            ("legacy_import.tts_copy_robocopy_failed", "TTS 系统复制失败")
        }
        "legacy_import.tts_onnx_started" => (
            "legacy_import.tts_onnx_started",
            "开始合并旧版 TTS ONNX 资源",
        ),
        "legacy_import.tts_profiles_adapted" => (
            "legacy_import.tts_profiles_adapted",
            "旧版 TTS 托管配置已适配",
        ),
        "legacy_import.tts_runtime_paths_sanitized" => (
            "legacy_import.tts_runtime_paths_sanitized",
            "旧版 TTS Python 路径已适配",
        ),
        "legacy_import.tts_absolute_links_skipped" => (
            "legacy_import.tts_absolute_links_skipped",
            "旧版 TTS 绝对链接未复制",
        ),
        "legacy_import.tts_completed" => ("legacy_import.tts_completed", "旧版本 TTS 资源迁移完成"),
        "legacy_import.tts_skipped" => (
            "legacy_import.tts_skipped",
            "TTS 资源迁移失败，已保留核心数据",
        ),
        "legacy_import.tts_config_skipped" => (
            "legacy_import.tts_config_skipped",
            "TTS 配置迁移失败，已保留核心数据",
        ),
        "legacy_import.tts_onnx_binding_skipped" => (
            "legacy_import.tts_onnx_binding_skipped",
            "TTS ONNX 角色绑定失败，模型已作为可恢复资源保留",
        ),
        "legacy_import.characters_skipped" => (
            "legacy_import.characters_skipped",
            "角色包迁移失败，已保留核心数据",
        ),
        "legacy_import.character_validation_failed" => (
            "legacy_import.character_validation_failed",
            "迁移后的角色包校验失败",
        ),
        _ => return None,
    })
}

fn sanitize_legacy_import_attributes(attributes: &Value) -> Value {
    const SAFE_KEYS: &[&str] = &[
        "actual_bytes",
        "actual_files",
        "artifacts",
        "available_bytes",
        "bytes",
        "byte_delta",
        "cause_errno",
        "cause_reason_code",
        "cause_sqlite_errorcode",
        "cause_sqlite_errorname",
        "cause_type",
        "cause_winerror",
        "code",
        "copy_method",
        "database_bytes",
        "detected_version",
        "detail_stage",
        "discovered_characters",
        "domain",
        "domains_present",
        "entries",
        "errno",
        "error_type",
        "expected_bytes",
        "expected_files",
        "fallbacks",
        "files",
        "import_id",
        "items",
        "journal_mode",
        "line",
        "links",
        "model_bytes",
        "model_files",
        "overwrite_domains",
        "page_count",
        "pending_core_validation",
        "profiles",
        "pth_files",
        "quarantined",
        "quarantined_servers",
        "quick_check",
        "readiness",
        "reason_code",
        "records",
        "relative_path",
        "remaining_pages",
        "required_bytes",
        "return_code",
        "skipped",
        "snapshot_bytes",
        "shm_bytes",
        "source_bytes",
        "source_column",
        "source_files",
        "source_line",
        "source_platform",
        "source_records",
        "sqlite_errorcode",
        "sqlite_errorname",
        "sqlite_version",
        "stage",
        "timeline_entries",
        "total_pages",
        "validation_component",
        "wal_bytes",
        "warnings",
        "winerror",
    ];
    let Some(object) = attributes.as_object() else {
        return json!({});
    };
    let mut sanitized = serde_json::Map::new();
    for (key, value) in object {
        if !SAFE_KEYS.contains(&key.as_str()) {
            continue;
        }
        let safe_value = match value {
            Value::Bool(_) | Value::Number(_) => Some(value.clone()),
            Value::String(text)
                if text.len() <= 512 && !text.contains('\r') && !text.contains('\n') =>
            {
                if key == "relative_path"
                    && (looks_absolute_path(text)
                        || text.split(['/', '\\']).any(|part| part == ".."))
                {
                    None
                } else {
                    Some(Value::String(text.clone()))
                }
            }
            _ => None,
        };
        if let Some(value) = safe_value {
            sanitized.insert(key.clone(), value);
        }
    }
    Value::Object(sanitized)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::platform::{
        ManagedProcessPipes, ManagedProcessTree, PlatformError, PlatformErrorCategory,
        PlatformResult, PlatformService, ProcessTreeFinalization, ProcessTreeFinalizationFailure,
        ProcessTreeFinalizationResult, ProcessWaitOutcome, RetryAdvice, SpawnedProcessTree,
    };
    use std::{
        collections::VecDeque,
        sync::atomic::{AtomicBool as TestAtomicBool, Ordering},
    };

    #[test]
    fn failed_inspection_restores_only_the_matching_selection() {
        let mut snapshot = LegacyImportSnapshot {
            state: "inspecting".to_string(),
            stage: "inspecting".to_string(),
            selection_id: Some("selection-a".to_string()),
            percent: 37,
            message: "private diagnostic".to_string(),
            cancellable: true,
            ..LegacyImportSnapshot::default()
        };

        restore_selected_snapshot_after_inspection_failure(&mut snapshot, "selection-b");
        assert_eq!(snapshot.state, "inspecting");

        restore_selected_snapshot_after_inspection_failure(&mut snapshot, "selection-a");
        assert_eq!(snapshot.state, "selected");
        assert_eq!(snapshot.stage, "selected");
        assert_eq!(snapshot.percent, 0);
        assert!(snapshot.message.is_empty());
        assert!(!snapshot.cancellable);
    }

    struct ScriptedPipe {
        chunks: VecDeque<Vec<u8>>,
    }

    impl ManagedPipeReader for ScriptedPipe {
        fn read_until(
            &mut self,
            buffer: &mut [u8],
            _deadline: Instant,
            _cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            let Some(chunk) = self.chunks.pop_front() else {
                return Ok(ManagedPipeReadOutcome::Eof);
            };
            buffer[..chunk.len()].copy_from_slice(&chunk);
            Ok(ManagedPipeReadOutcome::Read(chunk.len()))
        }
    }

    struct FinalizationTree {
        finalized: Arc<TestAtomicBool>,
        fail_finalization: bool,
    }

    impl ManagedProcessTree for FinalizationTree {
        fn root_pid(&self) -> u32 {
            42
        }

        fn wait_root(&mut self, _timeout: Duration) -> PlatformResult<ProcessWaitOutcome> {
            Ok(ProcessWaitOutcome::TimedOut)
        }

        fn terminate_tree(&mut self, _reason_code: u32) -> PlatformResult<()> {
            self.finalized.store(true, Ordering::Release);
            Ok(())
        }

        fn wait_tree_exited(&self, _timeout: Duration) -> PlatformResult<bool> {
            Ok(self.finalized.load(Ordering::Acquire))
        }

        fn release_exited(self: Box<Self>) -> PlatformResult<()> {
            Ok(())
        }

        fn finalize_until(
            self: Box<Self>,
            _deadline: Instant,
            _reason_code: u32,
        ) -> ProcessTreeFinalizationResult {
            self.finalized.store(true, Ordering::Release);
            if self.fail_finalization {
                return Err(ProcessTreeFinalizationFailure::new(
                    PlatformError::new(
                        PlatformService::ManagedProcessTree,
                        PlatformErrorCategory::TimedOut,
                        "fixture_finalize",
                        RetryAdvice::Never,
                        "fixture tree remained active",
                    ),
                    self,
                ));
            }
            Ok(ProcessTreeFinalization {
                root_status: ProcessExitStatus::Code(0),
                forced: true,
            })
        }
    }

    struct FinalizationBackend {
        finalized: Arc<TestAtomicBool>,
    }

    impl ManagedProcessTreeBackend for FinalizationBackend {
        fn spawn(&self, _request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
            #[cfg(unix)]
            let stdin = fs::File::open("/dev/null").unwrap();
            #[cfg(windows)]
            let stdin = fs::File::open("NUL").unwrap();
            Ok(SpawnedProcessTree {
                tree: Box::new(FinalizationTree {
                    finalized: self.finalized.clone(),
                    fail_finalization: false,
                }),
                pipes: Some(ManagedProcessPipes {
                    stdin,
                    stdout: Box::new(ScriptedPipe {
                        chunks: VecDeque::from([b"not-json\n".to_vec()]),
                    }),
                    stderr: Box::new(ScriptedPipe {
                        chunks: VecDeque::new(),
                    }),
                }),
            })
        }
    }

    struct TimedOutPipe {
        cancellation_seen: Arc<TestAtomicBool>,
    }

    impl ManagedPipeReader for TimedOutPipe {
        fn read_until(
            &mut self,
            _buffer: &mut [u8],
            deadline: Instant,
            cancelled: &AtomicBool,
        ) -> PlatformResult<ManagedPipeReadOutcome> {
            while Instant::now() < deadline && !cancelled.load(Ordering::Acquire) {
                thread::yield_now();
            }
            if cancelled.load(Ordering::Acquire) {
                self.cancellation_seen.store(true, Ordering::Release);
                Ok(ManagedPipeReadOutcome::Cancelled)
            } else {
                Ok(ManagedPipeReadOutcome::TimedOut)
            }
        }
    }

    struct DeadlineBackend {
        finalization_attempted: Arc<TestAtomicBool>,
        stderr_cancellation_seen: Arc<TestAtomicBool>,
        fail_finalization: bool,
    }

    impl ManagedProcessTreeBackend for DeadlineBackend {
        fn spawn(&self, _request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
            #[cfg(unix)]
            let stdin = fs::File::open("/dev/null").unwrap();
            #[cfg(windows)]
            let stdin = fs::File::open("NUL").unwrap();
            Ok(SpawnedProcessTree {
                tree: Box::new(FinalizationTree {
                    finalized: self.finalization_attempted.clone(),
                    fail_finalization: self.fail_finalization,
                }),
                pipes: Some(ManagedProcessPipes {
                    stdin,
                    stdout: Box::new(TimedOutPipe {
                        cancellation_seen: Arc::new(TestAtomicBool::new(false)),
                    }),
                    stderr: Box::new(TimedOutPipe {
                        cancellation_seen: self.stderr_cancellation_seen.clone(),
                    }),
                }),
            })
        }
    }

    fn fixture_request() -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from("fixture"),
            args: Vec::new(),
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        }
    }

    fn temporary_root(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "sakura-legacy-{name}-{}",
            uuid::Uuid::new_v4().simple()
        ))
    }

    #[test]
    fn packaged_python_bootstrap_includes_importer_and_memory_roots() {
        let root = std::env::temp_dir().join(format!(
            "sakura-legacy-bootstrap-test-{}",
            uuid::Uuid::new_v4().simple()
        ));
        let core_root = root.join("core");
        let distribution_root = root.join("distribution");
        let site_packages = root.join("site-packages");
        let memory_dependencies = distribution_root.join("plugins/dependencies/sakura.memory.mem0");
        fs::create_dir_all(&memory_dependencies).unwrap();

        let bootstrap = python_bootstrap(
            std::slice::from_ref(&site_packages),
            &core_root,
            &distribution_root,
        )
        .unwrap();
        let roots_json = bootstrap
            .split_once(";sys.path[:0]=")
            .unwrap()
            .1
            .split_once(";sys.argv[0]")
            .unwrap()
            .0;
        let roots: Vec<PathBuf> = serde_json::from_str(roots_json).unwrap();

        assert_eq!(roots[0], core_root);
        assert_eq!(roots[1], distribution_root);
        assert!(roots.contains(&site_packages));
        assert!(roots.contains(&memory_dependencies));
        assert!(!bootstrap.contains("SAKURA_RUNTIME_LOG_PATH"));
        assert!(!bootstrap.contains("sakura-runtime.log"));

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn child_diagnostics_accept_only_catalogued_events_and_fixed_messages() {
        assert_eq!(
            legacy_import_event("legacy_import.memory_snapshot_failed"),
            Some((
                "legacy_import.memory_snapshot_failed",
                "长期记忆 SQLite 快照创建失败"
            ))
        );
        assert_eq!(legacy_import_event("legacy_import.private.payload"), None);
        assert_eq!(
            legacy_import_event("legacy_import.stage_completed"),
            Some(("legacy_import.stage_completed", "迁移阶段完成"))
        );
    }

    #[test]
    fn child_diagnostic_attributes_are_scalar_allowlisted_and_path_safe() {
        let sanitized = sanitize_legacy_import_attributes(&json!({
            "stage": "configuration",
            "files": 4,
            "relative_path": "config/api.yaml",
            "diagnostic": "secret value",
            "output_tail": "C:\\Users\\private\\source",
            "nested": {"private": true}
        }));
        assert_eq!(
            sanitized,
            json!({
                "stage": "configuration",
                "files": 4,
                "relative_path": "config/api.yaml"
            })
        );
        assert_eq!(
            sanitize_legacy_import_attributes(&json!({
                "relative_path": "C:\\Users\\private\\api.yaml"
            })),
            json!({})
        );
    }

    #[test]
    fn journal_state_distinguishes_missing_readable_and_unreadable() {
        let root = temporary_root("journal-state");
        fs::create_dir_all(&root).unwrap();
        let journal = root.join("journal.json");

        assert_eq!(read_journal_state(&journal), JournalState::Missing);
        fs::write(&journal, br#"{"state":"pending_core_validation"}"#).unwrap();
        assert_eq!(
            read_journal_state(&journal),
            JournalState::Readable("pending_core_validation".to_string())
        );
        fs::write(&journal, b"{broken").unwrap();
        assert_eq!(read_journal_state(&journal), JournalState::Unreadable);
        fs::write(&journal, br#"{"state":"unknown"}"#).unwrap();
        assert_eq!(read_journal_state(&journal), JournalState::Unreadable);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn protocol_failure_finalizes_the_managed_tree_owner() {
        let finalized = Arc::new(TestAtomicBool::new(false));
        let backend = FinalizationBackend {
            finalized: finalized.clone(),
        };
        let request = fixture_request();

        let result = run_managed_protocol(
            &backend,
            request,
            Instant::now() + Duration::from_secs(1),
            |_| {},
        );

        assert_eq!(result.unwrap_err(), "LEGACY_IMPORT_PROTOCOL_INVALID");
        assert!(finalized.load(Ordering::Acquire));
    }

    #[test]
    fn action_deadlines_match_the_import_contract() {
        assert_eq!(
            legacy_action_deadline("inspect-data").unwrap(),
            Duration::from_secs(15 * 60)
        );
        for action in ["inspect", "recover", "finalize", "rollback", "apply-data"] {
            assert_eq!(
                legacy_action_deadline(action).unwrap(),
                Duration::from_secs(30 * 60)
            );
        }
        assert_eq!(
            legacy_action_deadline("run").unwrap(),
            Duration::from_secs(2 * 60 * 60)
        );
        assert_eq!(
            legacy_action_deadline("unknown").unwrap_err(),
            "LEGACY_IMPORT_ACTION_INVALID"
        );
    }

    #[test]
    fn a_timed_out_running_process_is_finalized_and_its_drains_are_cancelled() {
        let finalization_attempted = Arc::new(TestAtomicBool::new(false));
        let stderr_cancellation_seen = Arc::new(TestAtomicBool::new(false));
        let backend = DeadlineBackend {
            finalization_attempted: finalization_attempted.clone(),
            stderr_cancellation_seen: stderr_cancellation_seen.clone(),
            fail_finalization: false,
        };

        let result = run_managed_protocol(
            &backend,
            fixture_request(),
            Instant::now() + Duration::from_millis(5),
            |_| {},
        );

        assert_eq!(result.unwrap_err(), LEGACY_IMPORT_OPERATION_TIMEOUT);
        assert!(finalization_attempted.load(Ordering::Acquire));
        assert!(stderr_cancellation_seen.load(Ordering::Acquire));
    }

    #[test]
    fn a_timed_out_process_with_uncertain_termination_fails_closed() {
        let finalization_attempted = Arc::new(TestAtomicBool::new(false));
        let backend = DeadlineBackend {
            finalization_attempted: finalization_attempted.clone(),
            stderr_cancellation_seen: Arc::new(TestAtomicBool::new(false)),
            fail_finalization: true,
        };

        let result = run_managed_protocol(
            &backend,
            fixture_request(),
            Instant::now() + Duration::from_millis(5),
            |_| {},
        );

        assert_eq!(
            result.unwrap_err(),
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
        );
        assert!(finalization_attempted.load(Ordering::Acquire));
        assert!(process_tree_state_is_unknown(
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
        ));
        assert!(!process_tree_state_is_unknown(
            LEGACY_IMPORT_OPERATION_TIMEOUT
        ));
    }

    #[test]
    fn incremental_finalize_unknown_stops_core_before_returning_the_process_error() {
        let stop_attempted = Arc::new(TestAtomicBool::new(false));
        let observed = stop_attempted.clone();
        let result: Result<(), String> = fail_incremental_finalize_with_unknown_process(
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED.to_string(),
            move |timeout| {
                assert_eq!(timeout, Duration::from_secs(15));
                observed.store(true, Ordering::Release);
                Ok(())
            },
        );

        assert_eq!(
            result.unwrap_err(),
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED
        );
        assert!(stop_attempted.load(Ordering::Acquire));
    }

    #[test]
    fn incremental_finalize_unknown_escalates_an_uncertain_core_stop() {
        let result: Result<(), String> = fail_incremental_finalize_with_unknown_process(
            LEGACY_IMPORT_PROCESS_TERMINATION_FAILED.to_string(),
            |_| Err("LIFECYCLE_STOP_TIMEOUT"),
        );

        assert_eq!(result.unwrap_err(), LEGACY_IMPORT_CORE_STOP_FAILED);
    }

    #[test]
    fn intentional_pipe_cancellation_is_clean() {
        let cancellation_seen = Arc::new(TestAtomicBool::new(false));
        let cancelled = Arc::new(TestAtomicBool::new(true));

        let result = drain_managed_pipe(
            Box::new(TimedOutPipe {
                cancellation_seen: cancellation_seen.clone(),
            }),
            cancelled,
        );

        assert_eq!(result, Ok(()));
        assert!(cancellation_seen.load(Ordering::Acquire));
    }
}
