use std::{
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
use windows::Win32::System::Threading::CREATE_NO_WINDOW;

use serde::Serialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};

use crate::{
    managed_process_tree::{ManagedProcessSpec, ManagedProcessTree, WaitOutcome},
    platform::{FilesystemRuntimeLocator, RuntimeLocationRequest, RuntimeLocator},
    product_shell,
    runtime_log::{Correlation, RuntimeLogEvent, RuntimeLogService, Severity},
    ShellLifecycleState,
};

pub const LEGACY_IMPORT_PROGRESS_EVENT: &str = "sakura://legacy-import-progress";
const CORE_VALIDATION_DEADLINE: Duration = Duration::from_secs(60);

pub fn recover_interrupted(request: &RuntimeLocationRequest) -> Result<bool, String> {
    let pending = fs::read_dir(&request.user_root)
        .map_err(|_| "LEGACY_IMPORT_RECOVERY_FAILED".to_string())?
        .filter_map(Result::ok)
        .any(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.starts_with(".legacy-import-journal-") && name.ends_with(".json")
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
    if state.snapshot()?.state == "staging"
        || state.snapshot()?.state == "validating"
        || state.snapshot()?.state == "committing"
        || state.snapshot()?.state == "core_validating"
    {
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
    let (source, import_id, snapshot) = {
        let mut inner = state
            .inner
            .lock()
            .map_err(|_| "LEGACY_IMPORT_STATE_UNAVAILABLE".to_string())?;
        if inner.snapshot.state != "selected" {
            return Err("LEGACY_IMPORT_NOT_READY".to_string());
        }
        let selection = inner
            .selection
            .as_ref()
            .filter(|selection| selection.id == selection_id)
            .cloned()
            .ok_or_else(|| "LEGACY_IMPORT_SELECTION_INVALID".to_string())?;
        let import_id = uuid::Uuid::new_v4().simple().to_string();
        inner.import_id = Some(import_id.clone());
        inner.snapshot.state = "staging".to_string();
        inner.snapshot.stage = "staging".to_string();
        inner.snapshot.percent = 1;
        inner.snapshot.message = "迁移已开始，正在准备数据".to_string();
        inner.snapshot.cancellable = true;
        inner.snapshot.warnings.clear();
        inner.snapshot.error = None;
        (selection.source, import_id, inner.snapshot.clone())
    };
    let request = state.request.clone();
    thread::spawn(move || run_import_worker(app, state, request, source, import_id));
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
        snapshot.message = "正在取消迁移".to_string();
        snapshot.cancellable = false;
    })
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
    let outcome = stream_run(&request, &source, &import_id, &runtime_log, |publication| {
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
    });
    match outcome {
        Ok(result)
            if result.get("state").and_then(Value::as_str) == Some("core_validating")
                || commit_is_pending_core_validation(&request, &import_id) =>
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
            log_import_step(
                &app,
                Severity::Error,
                "legacy_import.result_invalid",
                "迁移结果与事务状态不一致",
                &import_id,
                json!({
                    "code": "LEGACY_IMPORT_RESULT_INVALID",
                    "diagnostic": "迁移子进程结果与持久事务状态不一致",
                    "error_type": "LegacyImportProtocolError",
                    "reason_code": "LEGACY_IMPORT_RESULT_INVALID",
                    "stage": "result_validation"
                }),
            );
            fail_publication(
                &app,
                &state,
                json!({"code":"LEGACY_IMPORT_RESULT_INVALID","stage":"staging"}),
            );
        }
        Err(error) => {
            let cancelled =
                error.get("code").and_then(Value::as_str) == Some("LEGACY_IMPORT_CANCELLED");
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
                let completed = app
                    .state::<product_shell::FirstRunGuideState>()
                    .complete()
                    .is_ok();
                if !completed {
                    rollback_after_core_failure(
                        app,
                        state,
                        request,
                        import_id,
                        "LEGACY_COMPLETION_FAILED",
                    );
                    return;
                }
                if run_simple_action(request, "finalize", import_id).is_err() {
                    rollback_after_core_failure(
                        app,
                        state,
                        request,
                        import_id,
                        "LEGACY_FINALIZE_FAILED",
                    );
                    return;
                }
                if let Some(main) = app.get_webview_window("main") {
                    let _ = main.show();
                    let _ = product_shell::sync_product_tray_visibility(app, true);
                }
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
                if run_simple_action(request, "finalize", import_id).is_err() {
                    rollback_after_core_failure(
                        app,
                        state,
                        request,
                        import_id,
                        "LEGACY_FINALIZE_FAILED",
                    );
                    return;
                }
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
    let rollback = run_simple_action(request, "rollback", import_id);
    let public_code = if rollback.is_ok() {
        code
    } else {
        "LEGACY_ROLLBACK_FAILED"
    };
    log_import_step(
        app,
        Severity::Error,
        "legacy_import.core_validation_failed",
        "迁移数据校验失败，已执行回滚",
        import_id,
        json!({
            "code": public_code,
            "diagnostic": if rollback.is_ok() {
                "迁移数据未通过 Core 校验，事务已回滚"
            } else {
                "迁移数据未通过 Core 校验，事务回滚也失败"
            },
            "error_type": if rollback.is_ok() {
                "CoreValidationError"
            } else {
                "LegacyImportRollbackError"
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
    let layout = FilesystemRuntimeLocator
        .locate(request)
        .map_err(|_| json!({"code":"LEGACY_RUNTIME_UNAVAILABLE","stage":"staging"}))?;
    let bootstrap = python_bootstrap(
        &layout.python_path_entries,
        &layout.core_root,
        &layout.distribution_root,
    )
    .map_err(|_| json!({"code":"LEGACY_RUNTIME_UNAVAILABLE","stage":"staging"}))?;
    let mut spec = ManagedProcessSpec::new(layout.python_executable);
    spec.arg("-B")
        .arg("-c")
        .arg(bootstrap)
        .arg("run")
        .arg("--source")
        .arg(source)
        .arg("--target")
        .arg(&request.user_root)
        .arg("--import-id")
        .arg(import_id)
        .current_dir(layout.core_root);
    let (mut child, pipes) = ManagedProcessTree::spawn_piped(&spec)
        .map_err(|_| json!({"code":"LEGACY_RUNTIME_UNAVAILABLE","stage":"staging"}))?;
    log(
        Severity::Info,
        "legacy_import.python_started",
        "迁移 Python 子进程已启动",
        json!({}),
    );
    drop(pipes.stdin);
    let mut stderr = pipes.stderr;
    let stderr_drain = thread::spawn(move || {
        let _ = std::io::copy(&mut stderr, &mut std::io::sink());
    });
    let mut result = None;
    let mut public_error = None;
    let mut stdout = BufReader::new(pipes.stdout);
    let mut line = Vec::new();
    loop {
        line.clear();
        match stdout.read_until(b'\n', &mut line) {
            Ok(0) => break,
            Ok(_) => {}
            Err(error) => {
                log(
                    Severity::Error,
                    "legacy_import.stdout_read_failed",
                    "读取迁移 Python 输出失败",
                    json!({
                        "diagnostic": error.to_string(),
                        "error_type": std::any::type_name_of_val(&error),
                        "reason_code": "LEGACY_IMPORT_STDOUT_READ_FAILED",
                        "stage": "protocol_read"
                    }),
                );
                break;
            }
        }
        let value = match serde_json::from_slice::<Value>(&line) {
            Ok(value) => value,
            Err(error) => {
                log(
                    Severity::Error,
                    "legacy_import.stdout_json_invalid",
                    "迁移 Python 输出不是有效 UTF-8 JSON",
                    json!({
                        "bytes": line.len(),
                        "diagnostic": error.to_string(),
                        "error_type": "serde_json::Error",
                        "reason_code": "LEGACY_IMPORT_PROTOCOL_INVALID",
                        "stage": "protocol_decode"
                    }),
                );
                continue;
            }
        };
        match value.get("type").and_then(Value::as_str) {
            Some("progress" | "inspection") => progress(&value),
            Some("diagnostic") => {
                let Some(event) = value.get("event").and_then(Value::as_str) else {
                    continue;
                };
                let Some((event, message)) = legacy_import_event(event) else {
                    continue;
                };
                let severity = match value.get("severity").and_then(Value::as_str) {
                    Some("error") => Severity::Error,
                    Some("warning" | "warn") => Severity::Warning,
                    Some("info") => Severity::Info,
                    _ => continue,
                };
                let attributes = value
                    .get("attributes")
                    .filter(|attributes| attributes.is_object())
                    .cloned()
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
                result = Some(value);
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
    }
    log(
        Severity::Info,
        "legacy_import.stdout_closed",
        "迁移 Python 输出流已关闭",
        json!({}),
    );
    let status = child
        .wait(Duration::from_secs(5))
        .map_err(|_| json!({"code":"LEGACY_RUNTIME_FAILED","stage":"staging"}))?;
    let succeeded = match status {
        WaitOutcome::Exited(code) => code == 0,
        WaitOutcome::TimedOut => {
            let _ = child.terminate_tree(1);
            let _ = child.wait(Duration::from_secs(5));
            false
        }
    };
    let exit_attributes = if succeeded {
        json!({"succeeded": true})
    } else {
        json!({
            "succeeded": false,
            "code": "LEGACY_RUNTIME_FAILED",
            "diagnostic": "迁移 Python 子进程未正常完成",
            "error_type": "LegacyImportProcessExit",
            "reason_code": "LEGACY_RUNTIME_FAILED",
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
    let _ = stderr_drain.join();
    if succeeded {
        if let Some(result) = result {
            return Ok(result);
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
        Err(public_error
            .unwrap_or_else(|| json!({"code":"LEGACY_RUNTIME_FAILED","stage":"staging"})))
    }
}

fn commit_is_pending_core_validation(request: &RuntimeLocationRequest, import_id: &str) -> bool {
    let journal = request
        .user_root
        .join(format!(".legacy-import-journal-{import_id}.json"));
    fs::read_to_string(journal)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .and_then(|value| {
            value
                .get("state")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .as_deref()
        == Some("pending_core_validation")
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

fn run_python(
    request: &RuntimeLocationRequest,
    action: &str,
    arguments: &[(&str, &Path)],
) -> Result<Vec<Value>, String> {
    let mut command = python_command(request)?;
    command.arg(action);
    for (name, value) in arguments {
        command.arg(name).arg(value);
    }
    let output = command
        .stderr(Stdio::null())
        .output()
        .map_err(|_| "LEGACY_RUNTIME_UNAVAILABLE".to_string())?;
    let values = String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .collect::<Vec<_>>();
    if !output.status.success() {
        return Err(values
            .iter()
            .find_map(|value| value.pointer("/error/code").and_then(Value::as_str))
            .unwrap_or("LEGACY_RUNTIME_FAILED")
            .to_string());
    }
    Ok(values)
}

fn python_command(request: &RuntimeLocationRequest) -> Result<Command, String> {
    let layout = FilesystemRuntimeLocator
        .locate(request)
        .map_err(|_| "LEGACY_RUNTIME_UNAVAILABLE".to_string())?;
    let bootstrap = python_bootstrap(
        &layout.python_path_entries,
        &layout.core_root,
        &layout.distribution_root,
    )?;
    let mut command = Command::new(layout.python_executable);
    #[cfg(windows)]
    command.creation_flags(CREATE_NO_WINDOW.0);
    command
        .arg("-B")
        .arg("-c")
        .arg(bootstrap)
        .current_dir(layout.core_root);
    Ok(command)
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

#[cfg(test)]
mod tests {
    use super::*;

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
    }
}
