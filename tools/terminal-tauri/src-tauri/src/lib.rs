use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Read, Stdin, Write};
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex};
use std::thread;
use std::time::Duration;

use base64::engine::general_purpose::STANDARD as BASE64;
use base64::Engine;
use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WindowEvent};

const PROTOCOL_VERSION: u8 = 2;
const REQUEST_MARKER: &str = "@@SAKURA_TERMINAL_REQUEST@@";
const RESULT_MARKER: &str = "@@SAKURA_TERMINAL_RESULT@@";
const READY_MARKER: &str = "@@SAKURA_TERMINAL_READY@@";
const HOST_EVENT_MARKER: &str = "@@SAKURA_TERMINAL_EVENT@@";
const OUTPUT_EVENT: &str = "sakura://terminal-output";
const STATE_EVENT: &str = "sakura://terminal-state";
const APPROVAL_EVENT: &str = "sakura://terminal-approval";
const DEFAULT_RING_BYTES: usize = 1024 * 1024;
const MAX_RING_BYTES: usize = 4 * 1024 * 1024;
const MAX_READ_BYTES: usize = 16 * 1024;
const MAX_WRITE_BYTES: usize = 16 * 1024;
const MAX_ARGS: usize = 128;
const MAX_ARG_CHARS: usize = 4096;
const MAX_TIMEOUT_MS: u64 = 30 * 60 * 1000;
const MIN_TERMINAL_SIZE: u16 = 2;
const MAX_TERMINAL_SIZE: u16 = 500;

#[derive(Debug, Deserialize)]
struct InitRequest {
    version: u8,
    nonce: String,
    #[serde(default)]
    limits: InitLimits,
}

#[derive(Debug, Default, Deserialize)]
struct InitLimits {
    ring_bytes: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct HostRequest {
    id: String,
    version: u8,
    nonce: String,
    method: String,
    #[serde(default)]
    params: Value,
}

#[derive(Debug, Deserialize)]
struct SpawnParams {
    command: Vec<String>,
    cwd: String,
    columns: u16,
    rows: u16,
    yield_time_ms: u64,
    timeout_ms: u64,
}

#[derive(Debug, Deserialize)]
struct SessionParams {
    session_id: String,
}

#[derive(Debug, Deserialize)]
struct ReadParams {
    session_id: String,
    cursor: u64,
    max_bytes: usize,
}

#[derive(Debug, Deserialize)]
struct WriteParams {
    session_id: String,
    data_b64: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ApprovalRequest {
    id: String,
    tool_name: String,
    summary: String,
    command: Vec<String>,
    cwd: String,
    risk_level: String,
    allowed_scopes: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct ShowApprovalParams {
    approval: ApprovalRequest,
}

#[derive(Debug, Deserialize)]
struct ApprovalIdParams {
    approval_id: String,
}

#[derive(Clone, Serialize)]
struct ApprovalEvent {
    approval: Option<ApprovalRequest>,
}

#[derive(Debug, Serialize)]
struct ApprovalResolution {
    #[serde(rename = "type")]
    event_type: String,
    approval_id: String,
    decision: String,
}

#[derive(Clone, Serialize)]
struct OutputEvent {
    session_id: String,
    data_b64: String,
    cursor: u64,
}

#[derive(Clone, Serialize)]
struct StateEvent {
    session_id: String,
    state: String,
    exit_code: Option<u32>,
}

#[derive(Debug, Serialize)]
struct SessionResult {
    session_id: String,
    state: String,
    output_b64: String,
    cursor: u64,
    exit_code: Option<u32>,
    truncated: bool,
}

struct RingBuffer {
    bytes: VecDeque<u8>,
    capacity: usize,
    start_cursor: u64,
    next_cursor: u64,
}

impl RingBuffer {
    fn new(capacity: usize) -> Self {
        Self {
            bytes: VecDeque::with_capacity(capacity),
            capacity,
            start_cursor: 0,
            next_cursor: 0,
        }
    }

    fn append(&mut self, data: &[u8]) -> u64 {
        if data.len() >= self.capacity {
            let kept = &data[data.len() - self.capacity..];
            self.bytes.clear();
            self.bytes.extend(kept);
            self.next_cursor = self.next_cursor.saturating_add(data.len() as u64);
            self.start_cursor = self.next_cursor.saturating_sub(self.capacity as u64);
            return self.next_cursor;
        }
        self.bytes.extend(data);
        self.next_cursor = self.next_cursor.saturating_add(data.len() as u64);
        while self.bytes.len() > self.capacity {
            self.bytes.pop_front();
            self.start_cursor = self.start_cursor.saturating_add(1);
        }
        self.next_cursor
    }

    fn read(&self, cursor: u64, max_bytes: usize) -> (Vec<u8>, u64, bool) {
        let truncated = cursor < self.start_cursor;
        let effective_cursor = cursor.clamp(self.start_cursor, self.next_cursor);
        let offset = effective_cursor.saturating_sub(self.start_cursor) as usize;
        let count = self
            .bytes
            .len()
            .saturating_sub(offset)
            .min(max_bytes.min(MAX_READ_BYTES));
        let output = self
            .bytes
            .iter()
            .skip(offset)
            .take(count)
            .copied()
            .collect();
        (output, effective_cursor + count as u64, truncated)
    }
}

#[derive(Default)]
struct SharedProcessState {
    running: bool,
    stopped: bool,
    exit_code: Option<u32>,
}

struct SharedProcess {
    state: Mutex<SharedProcessState>,
    changed: Condvar,
}

impl SharedProcess {
    fn running() -> Self {
        Self {
            state: Mutex::new(SharedProcessState {
                running: true,
                stopped: false,
                exit_code: None,
            }),
            changed: Condvar::new(),
        }
    }

    fn mark_finished(&self, stopped: bool, exit_code: Option<u32>) -> Result<(), String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| "terminal state lock is poisoned".to_string())?;
        state.running = false;
        state.stopped |= stopped;
        if exit_code.is_some() {
            state.exit_code = exit_code;
        }
        drop(state);
        self.changed.notify_all();
        Ok(())
    }

    fn wait_for_timeout(&self, timeout: Duration) -> bool {
        let Ok(state) = self.state.lock() else {
            return false;
        };
        match self
            .changed
            .wait_timeout_while(state, timeout, |state| state.running)
        {
            Ok((state, wait)) => wait.timed_out() && state.running,
            Err(_) => false,
        }
    }
}

struct TerminalSession {
    id: String,
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
    ring: Arc<Mutex<RingBuffer>>,
    process: Arc<SharedProcess>,
}

struct TerminalHost {
    session: Mutex<Option<TerminalSession>>,
    approval: Mutex<Option<ApprovalRequest>>,
    ring_capacity: usize,
}

impl TerminalHost {
    fn new(ring_capacity: usize) -> Self {
        Self {
            session: Mutex::new(None),
            approval: Mutex::new(None),
            ring_capacity,
        }
    }

    fn set_approval(&self, approval: ApprovalRequest) -> Result<ApprovalRequest, String> {
        validate_approval(&approval)?;
        let mut pending = self
            .approval
            .lock()
            .map_err(|_| "terminal approval lock is poisoned".to_string())?;
        if let Some(current) = pending.as_ref() {
            if current.id != approval.id {
                return Err("已有终端命令等待确认。".to_string());
            }
        }
        *pending = Some(approval.clone());
        Ok(approval)
    }

    fn approval_snapshot(&self) -> Result<Option<ApprovalRequest>, String> {
        self.approval
            .lock()
            .map(|pending| pending.clone())
            .map_err(|_| "terminal approval lock is poisoned".to_string())
    }

    fn clear_approval(&self, approval_id: &str) -> Result<bool, String> {
        let mut pending = self
            .approval
            .lock()
            .map_err(|_| "terminal approval lock is poisoned".to_string())?;
        let matches = pending
            .as_ref()
            .is_some_and(|approval| approval.id == approval_id);
        if matches {
            pending.take();
        }
        Ok(matches)
    }

    fn resolve_approval(
        &self,
        approval_id: &str,
        decision: &str,
    ) -> Result<ApprovalResolution, String> {
        let mut pending = self
            .approval
            .lock()
            .map_err(|_| "terminal approval lock is poisoned".to_string())?;
        let approval = pending
            .as_ref()
            .ok_or_else(|| "当前没有等待确认的终端命令。".to_string())?;
        if approval.id != approval_id {
            return Err("终端确认请求已过期。".to_string());
        }
        if decision != "cancel"
            && !approval
                .allowed_scopes
                .iter()
                .any(|scope| scope == decision)
        {
            return Err("当前终端命令不允许该授权范围。".to_string());
        }
        if !matches!(decision, "once" | "process" | "cancel") {
            return Err("终端确认决策无效。".to_string());
        }
        pending.take();
        Ok(ApprovalResolution {
            event_type: "approval_resolved".to_string(),
            approval_id: approval_id.to_string(),
            decision: decision.to_string(),
        })
    }

    fn spawn(&self, app: &AppHandle, params: SpawnParams) -> Result<SessionResult, String> {
        validate_spawn(&params)?;
        let mut active = self
            .session
            .lock()
            .map_err(|_| "terminal session lock is poisoned".to_string())?;
        if let Some(session) = active.as_mut() {
            refresh_process_state(session)?;
            if session
                .process
                .state
                .lock()
                .map_err(|_| "terminal state lock is poisoned".to_string())?
                .running
            {
                return Err("已有终端进程正在运行。".to_string());
            }
        }

        let cwd = PathBuf::from(&params.cwd);
        let pty = native_pty_system()
            .openpty(PtySize {
                rows: params.rows,
                cols: params.columns,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| format!("无法创建 PTY：{error}"))?;
        let mut command = CommandBuilder::new(&params.command[0]);
        for argument in &params.command[1..] {
            command.arg(argument);
        }
        command.cwd(cwd);
        let child = pty
            .slave
            .spawn_command(command)
            .map_err(|error| format!("无法启动终端命令：{error}"))?;
        drop(pty.slave);
        let reader = pty
            .master
            .try_clone_reader()
            .map_err(|error| format!("无法读取 PTY：{error}"))?;
        let writer = pty
            .master
            .take_writer()
            .map_err(|error| format!("无法写入 PTY：{error}"))?;
        let killer = child.clone_killer();
        let session_id = next_session_id();
        let ring = Arc::new(Mutex::new(RingBuffer::new(self.ring_capacity)));
        let process = Arc::new(SharedProcess::running());

        spawn_reader(
            app.clone(),
            session_id.clone(),
            reader,
            Arc::clone(&ring),
            Arc::clone(&process),
        );
        spawn_timeout(
            app.clone(),
            session_id.clone(),
            killer,
            Arc::clone(&process),
            params.timeout_ms,
        );
        *active = Some(TerminalSession {
            id: session_id.clone(),
            master: pty.master,
            writer,
            child,
            ring,
            process,
        });
        drop(active);

        show_window(app)?;
        thread::sleep(Duration::from_millis(params.yield_time_ms));
        self.snapshot(&session_id, 0, MAX_READ_BYTES)
    }

    fn snapshot(
        &self,
        session_id: &str,
        cursor: u64,
        max_bytes: usize,
    ) -> Result<SessionResult, String> {
        let mut active = self
            .session
            .lock()
            .map_err(|_| "terminal session lock is poisoned".to_string())?;
        let session = require_session(active.as_mut(), session_id)?;
        refresh_process_state(session)?;
        session_result(session, cursor, max_bytes)
    }

    fn write(&self, session_id: &str, data: &[u8]) -> Result<SessionResult, String> {
        if data.is_empty() || data.len() > MAX_WRITE_BYTES {
            return Err("终端写入必须在 1 到 16 KiB 之间。".to_string());
        }
        let mut active = self
            .session
            .lock()
            .map_err(|_| "terminal session lock is poisoned".to_string())?;
        let session = require_session(active.as_mut(), session_id)?;
        refresh_process_state(session)?;
        if !session
            .process
            .state
            .lock()
            .map_err(|_| "terminal state lock is poisoned".to_string())?
            .running
        {
            return Err("终端进程已经结束。".to_string());
        }
        session
            .writer
            .write_all(data)
            .and_then(|_| session.writer.flush())
            .map_err(|error| format!("终端写入失败：{error}"))?;
        session_result(session, current_cursor(session)?, 0)
    }

    fn resize(&self, columns: u16, rows: u16) -> Result<(), String> {
        validate_size(columns, rows)?;
        let mut active = self
            .session
            .lock()
            .map_err(|_| "terminal session lock is poisoned".to_string())?;
        let session = active
            .as_mut()
            .ok_or_else(|| "当前没有终端会话。".to_string())?;
        session
            .master
            .resize(PtySize {
                rows,
                cols: columns,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| format!("终端尺寸调整失败：{error}"))
    }

    fn stop(&self, session_id: &str) -> Result<SessionResult, String> {
        let mut active = self
            .session
            .lock()
            .map_err(|_| "terminal session lock is poisoned".to_string())?;
        let session = require_session(active.as_mut(), session_id)?;
        refresh_process_state(session)?;
        let running = session
            .process
            .state
            .lock()
            .map_err(|_| "terminal state lock is poisoned".to_string())?
            .running;
        if running {
            session
                .child
                .kill()
                .map_err(|error| format!("终端进程停止失败：{error}"))?;
            let status = session
                .child
                .wait()
                .map_err(|error| format!("等待终端进程退出失败：{error}"))?;
            session
                .process
                .mark_finished(true, Some(status.exit_code()))?;
        }
        let result = session_result(session, current_cursor(session)?, 0)?;
        Ok(result)
    }

    fn stop_active(&self) {
        let session_id = self
            .session
            .lock()
            .ok()
            .and_then(|session| session.as_ref().map(|item| item.id.clone()));
        if let Some(session_id) = session_id {
            let _ = self.stop(&session_id);
        }
    }
}

fn validate_spawn(params: &SpawnParams) -> Result<(), String> {
    if params.command.is_empty() || params.command.len() > MAX_ARGS {
        return Err("终端命令 argv 数量无效。".to_string());
    }
    if params.command.iter().any(|argument| {
        argument.is_empty() || argument.contains('\0') || argument.len() > MAX_ARG_CHARS
    }) {
        return Err("终端命令参数无效。".to_string());
    }
    let cwd = PathBuf::from(&params.cwd);
    if !cwd.is_absolute() || !cwd.is_dir() {
        return Err("终端工作目录必须是存在的绝对目录。".to_string());
    }
    validate_size(params.columns, params.rows)?;
    if params.yield_time_ms > 10_000 || params.timeout_ms == 0 || params.timeout_ms > MAX_TIMEOUT_MS
    {
        return Err("终端等待或超时参数超出范围。".to_string());
    }
    Ok(())
}

fn validate_approval(approval: &ApprovalRequest) -> Result<(), String> {
    if approval.id.is_empty() || approval.id.len() > 128 {
        return Err("终端确认 ID 无效。".to_string());
    }
    if !matches!(
        approval.tool_name.as_str(),
        "terminal_exec" | "terminal_write"
    ) {
        return Err("终端确认工具无效。".to_string());
    }
    if approval.summary.len() > MAX_ARG_CHARS
        || approval.cwd.len() > MAX_ARG_CHARS
        || !matches!(
            approval.risk_level.as_str(),
            "low" | "normal" | "medium" | "high"
        )
    {
        return Err("终端确认说明无效。".to_string());
    }
    if approval.command.len() > MAX_ARGS
        || approval
            .command
            .iter()
            .any(|argument| argument.contains('\0') || argument.len() > MAX_ARG_CHARS)
    {
        return Err("终端确认命令无效。".to_string());
    }
    if approval.tool_name == "terminal_exec" && approval.command.is_empty() {
        return Err("终端执行确认缺少 argv。".to_string());
    }
    if approval.allowed_scopes.is_empty()
        || !approval.allowed_scopes.iter().any(|scope| scope == "once")
        || approval
            .allowed_scopes
            .iter()
            .any(|scope| !matches!(scope.as_str(), "once" | "process"))
    {
        return Err("终端确认授权范围无效。".to_string());
    }
    Ok(())
}

fn validate_size(columns: u16, rows: u16) -> Result<(), String> {
    if !(MIN_TERMINAL_SIZE..=MAX_TERMINAL_SIZE).contains(&columns)
        || !(MIN_TERMINAL_SIZE..=MAX_TERMINAL_SIZE).contains(&rows)
    {
        return Err("终端尺寸超出允许范围。".to_string());
    }
    Ok(())
}

fn require_session<'a>(
    session: Option<&'a mut TerminalSession>,
    session_id: &str,
) -> Result<&'a mut TerminalSession, String> {
    let session = session.ok_or_else(|| "当前没有终端会话。".to_string())?;
    if session.id != session_id {
        return Err("终端会话不存在或不属于当前 Sakura 实例。".to_string());
    }
    Ok(session)
}

fn refresh_process_state(session: &mut TerminalSession) -> Result<(), String> {
    let (running, stopped, exit_code) = {
        let state = session
            .process
            .state
            .lock()
            .map_err(|_| "terminal state lock is poisoned".to_string())?;
        (state.running, state.stopped, state.exit_code)
    };
    if stopped || exit_code.is_some() {
        return Ok(());
    }
    match session
        .child
        .try_wait()
        .map_err(|error| format!("读取终端进程状态失败：{error}"))?
    {
        Some(status) => {
            session
                .process
                .mark_finished(false, Some(status.exit_code()))?;
        }
        None if !running => {
            session
                .process
                .state
                .lock()
                .map_err(|_| "terminal state lock is poisoned".to_string())?
                .running = true;
        }
        None => {}
    }
    Ok(())
}

fn current_cursor(session: &TerminalSession) -> Result<u64, String> {
    Ok(session
        .ring
        .lock()
        .map_err(|_| "terminal ring lock is poisoned".to_string())?
        .next_cursor)
}

fn session_result(
    session: &TerminalSession,
    cursor: u64,
    max_bytes: usize,
) -> Result<SessionResult, String> {
    let (output, next_cursor, truncated) = session
        .ring
        .lock()
        .map_err(|_| "terminal ring lock is poisoned".to_string())?
        .read(cursor, max_bytes);
    let process = session
        .process
        .state
        .lock()
        .map_err(|_| "terminal state lock is poisoned".to_string())?;
    let state = if process.running {
        "running"
    } else if process.stopped {
        "stopped"
    } else {
        "exited"
    };
    Ok(SessionResult {
        session_id: session.id.clone(),
        state: state.to_string(),
        output_b64: BASE64.encode(output),
        cursor: next_cursor,
        exit_code: process.exit_code,
        truncated,
    })
}

fn spawn_reader(
    app: AppHandle,
    session_id: String,
    mut reader: Box<dyn Read + Send>,
    ring: Arc<Mutex<RingBuffer>>,
    process: Arc<SharedProcess>,
) {
    thread::spawn(move || {
        let mut buffer = [0_u8; 8192];
        loop {
            match reader.read(&mut buffer) {
                Ok(0) => break,
                Ok(count) => {
                    let cursor = match ring.lock() {
                        Ok(mut ring) => ring.append(&buffer[..count]),
                        Err(_) => break,
                    };
                    let _ = app.emit(
                        OUTPUT_EVENT,
                        OutputEvent {
                            session_id: session_id.clone(),
                            data_b64: BASE64.encode(&buffer[..count]),
                            cursor,
                        },
                    );
                }
                Err(_) => break,
            }
        }
        let (event_state, exit_code) = if let Ok(state) = process.state.lock() {
            (
                if state.stopped { "stopped" } else { "exited" }.to_string(),
                state.exit_code,
            )
        } else {
            ("exited".to_string(), None)
        };
        let _ = process.mark_finished(false, exit_code);
        let _ = app.emit(
            STATE_EVENT,
            StateEvent {
                session_id,
                state: event_state,
                exit_code,
            },
        );
    });
}

fn spawn_timeout(
    app: AppHandle,
    session_id: String,
    mut killer: Box<dyn portable_pty::ChildKiller + Send + Sync>,
    process: Arc<SharedProcess>,
    timeout_ms: u64,
) {
    thread::spawn(move || {
        if !process.wait_for_timeout(Duration::from_millis(timeout_ms)) {
            return;
        }
        // portable-pty only guarantees killing the root child. On Windows, descendants
        // require a Job Object-backed implementation to guarantee process-tree cleanup.
        let _ = killer.kill();
        let _ = process.mark_finished(true, None);
        let _ = app.emit(
            STATE_EVENT,
            StateEvent {
                session_id,
                state: "stopped".to_string(),
                exit_code: None,
            },
        );
    });
}

fn next_session_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};
    use std::time::{SystemTime, UNIX_EPOCH};
    static COUNTER: AtomicU64 = AtomicU64::new(1);
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    format!(
        "terminal-{timestamp}-{}",
        COUNTER.fetch_add(1, Ordering::Relaxed)
    )
}

fn app_state_event_from_result(result: &SessionResult) -> StateEvent {
    StateEvent {
        session_id: result.session_id.clone(),
        state: result.state.clone(),
        exit_code: result.exit_code,
    }
}

fn show_window(app: &AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("main")
        .ok_or_else(|| "终端窗口不可用。".to_string())?;
    window.show().map_err(|error| error.to_string())?;
    window.unminimize().map_err(|error| error.to_string())?;
    window.set_focus().map_err(|error| error.to_string())
}

#[tauri::command]
fn load_request(state: State<'_, TerminalHost>) -> Result<Value, String> {
    let session_id = state
        .session
        .lock()
        .map_err(|_| "terminal session lock is poisoned".to_string())?
        .as_ref()
        .map(|session| session.id.clone());
    let approval = state.approval_snapshot()?;
    Ok(json!({
        "session_id": session_id,
        "read_max_bytes": MAX_READ_BYTES,
        "approval": approval,
    }))
}

#[tauri::command]
fn terminal_snapshot(
    cursor: u64,
    max_bytes: usize,
    state: State<'_, TerminalHost>,
) -> Result<SessionResult, String> {
    let session_id = state
        .session
        .lock()
        .map_err(|_| "terminal session lock is poisoned".to_string())?
        .as_ref()
        .map(|session| session.id.clone())
        .ok_or_else(|| "当前没有终端会话。".to_string())?;
    state.snapshot(&session_id, cursor, max_bytes)
}

#[tauri::command]
fn terminal_write(
    data_b64: String,
    state: State<'_, TerminalHost>,
) -> Result<SessionResult, String> {
    let data = BASE64
        .decode(data_b64)
        .map_err(|_| "终端输入不是有效的 base64。".to_string())?;
    let session_id = state
        .session
        .lock()
        .map_err(|_| "terminal session lock is poisoned".to_string())?
        .as_ref()
        .map(|session| session.id.clone())
        .ok_or_else(|| "当前没有终端会话。".to_string())?;
    state.write(&session_id, &data)
}

#[tauri::command]
fn terminal_resize(columns: u16, rows: u16, state: State<'_, TerminalHost>) -> Result<(), String> {
    state.resize(columns, rows)
}

#[tauri::command]
fn terminal_stop(app: AppHandle, state: State<'_, TerminalHost>) -> Result<SessionResult, String> {
    let session_id = state
        .session
        .lock()
        .map_err(|_| "terminal session lock is poisoned".to_string())?
        .as_ref()
        .map(|session| session.id.clone())
        .ok_or_else(|| "当前没有终端会话。".to_string())?;
    let result = state.stop(&session_id)?;
    let _ = app.emit(STATE_EVENT, app_state_event_from_result(&result));
    Ok(result)
}

#[tauri::command]
fn terminal_resolve_approval(
    approval_id: String,
    decision: String,
    app: AppHandle,
    state: State<'_, TerminalHost>,
) -> Result<(), String> {
    let resolution = state.resolve_approval(&approval_id, &decision)?;
    let _ = app.emit(APPROVAL_EVENT, ApprovalEvent { approval: None });
    write_host_event(&resolution);
    Ok(())
}

fn read_init() -> Result<(InitRequest, BufReader<Stdin>), String> {
    let mut reader = BufReader::new(std::io::stdin());
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|error| format!("无法读取终端初始化请求：{error}"))?;
    let init: InitRequest = serde_json::from_str(line.trim())
        .map_err(|error| format!("终端初始化请求无效：{error}"))?;
    if init.version != PROTOCOL_VERSION || init.nonce.len() < 16 {
        return Err("终端初始化协议或 nonce 无效。".to_string());
    }
    Ok((init, reader))
}

fn write_host_result(id: &str, result: Result<Value, String>) {
    let payload = match result {
        Ok(value) => json!({"id": id, "ok": true, "result": value}),
        Err(error) => json!({"id": id, "ok": false, "error": error}),
    };
    if let Ok(line) = serde_json::to_string(&payload) {
        let mut stdout = std::io::stdout().lock();
        let _ = writeln!(stdout, "{RESULT_MARKER}{line}");
        let _ = stdout.flush();
    }
}

fn write_host_event<T: Serialize>(event: &T) {
    if let Ok(line) = serde_json::to_string(event) {
        let mut stdout = std::io::stdout().lock();
        let _ = writeln!(stdout, "{HOST_EVENT_MARKER}{line}");
        let _ = stdout.flush();
    }
}

fn host_loop(mut reader: BufReader<Stdin>, app: AppHandle, nonce: String) {
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) | Err(_) => break,
            Ok(_) => {}
        }
        let trimmed = line.trim_end();
        if !trimmed.starts_with(REQUEST_MARKER) {
            continue;
        }
        let parsed = serde_json::from_str::<HostRequest>(&trimmed[REQUEST_MARKER.len()..]);
        let request = match parsed {
            Ok(request) => request,
            Err(error) => {
                write_host_result("", Err(format!("终端请求 JSON 无效：{error}")));
                continue;
            }
        };
        let request_id = request.id.clone();
        let exit_after = request.method == "shutdown";
        let result = handle_host_request(&app, &nonce, request);
        write_host_result(&request_id, result);
        if exit_after {
            break;
        }
    }
    app.exit(0);
}

fn handle_host_request(
    app: &AppHandle,
    nonce: &str,
    request: HostRequest,
) -> Result<Value, String> {
    if request.version != PROTOCOL_VERSION || request.nonce != nonce || request.id.is_empty() {
        return Err("终端请求协议、nonce 或 ID 无效。".to_string());
    }
    let state = app.state::<TerminalHost>();
    match request.method.as_str() {
        "spawn" => {
            let params: SpawnParams = serde_json::from_value(request.params)
                .map_err(|error| format!("spawn 参数无效：{error}"))?;
            serde_json::to_value(state.spawn(app, params)?).map_err(|error| error.to_string())
        }
        "read" => {
            let params: ReadParams = serde_json::from_value(request.params)
                .map_err(|error| format!("read 参数无效：{error}"))?;
            serde_json::to_value(state.snapshot(
                &params.session_id,
                params.cursor,
                params.max_bytes,
            )?)
            .map_err(|error| error.to_string())
        }
        "write" => {
            let params: WriteParams = serde_json::from_value(request.params)
                .map_err(|error| format!("write 参数无效：{error}"))?;
            let data = BASE64
                .decode(params.data_b64)
                .map_err(|_| "write data_b64 无效。".to_string())?;
            serde_json::to_value(state.write(&params.session_id, &data)?)
                .map_err(|error| error.to_string())
        }
        "stop" => {
            let params: SessionParams = serde_json::from_value(request.params)
                .map_err(|error| format!("stop 参数无效：{error}"))?;
            let result = state.stop(&params.session_id)?;
            let _ = app.emit(STATE_EVENT, app_state_event_from_result(&result));
            serde_json::to_value(result).map_err(|error| error.to_string())
        }
        "show" => {
            show_window(app)?;
            Ok(json!({"shown": true}))
        }
        "show_approval" => {
            let params: ShowApprovalParams = serde_json::from_value(request.params)
                .map_err(|error| format!("show_approval 参数无效：{error}"))?;
            let approval = state.set_approval(params.approval)?;
            let _ = app.emit(
                APPROVAL_EVENT,
                ApprovalEvent {
                    approval: Some(approval),
                },
            );
            show_window(app)?;
            Ok(json!({"shown": true}))
        }
        "clear_approval" => {
            let params: ApprovalIdParams = serde_json::from_value(request.params)
                .map_err(|error| format!("clear_approval 参数无效：{error}"))?;
            let cleared = state.clear_approval(&params.approval_id)?;
            if cleared {
                let _ = app.emit(APPROVAL_EVENT, ApprovalEvent { approval: None });
            }
            Ok(json!({"cleared": cleared}))
        }
        "shutdown" => {
            state.stop_active();
            if let Ok(Some(approval)) = state.approval_snapshot() {
                let _ = state.clear_approval(&approval.id);
            }
            Ok(json!({"stopped": true}))
        }
        _ => Err("未知终端宿主方法。".to_string()),
    }
}

pub fn run() {
    let (init, reader) = match read_init() {
        Ok(value) => value,
        Err(error) => {
            eprintln!("{error}");
            std::process::exit(2);
        }
    };
    let ring_capacity = init
        .limits
        .ring_bytes
        .unwrap_or(DEFAULT_RING_BYTES)
        .clamp(64 * 1024, MAX_RING_BYTES);
    let nonce = init.nonce;
    let app = tauri::Builder::default()
        .manage(TerminalHost::new(ring_capacity))
        .invoke_handler(tauri::generate_handler![
            load_request,
            terminal_snapshot,
            terminal_write,
            terminal_resize,
            terminal_stop,
            terminal_resolve_approval
        ])
        .setup(move |app| {
            let handle = app.handle().clone();
            let host_nonce = nonce.clone();
            thread::spawn(move || host_loop(reader, handle, host_nonce));
            let mut stdout = std::io::stdout().lock();
            writeln!(stdout, "{READY_MARKER}{{\"version\":{PROTOCOL_VERSION}}}")?;
            stdout.flush()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build Sakura terminal");

    app.run(|app, event| match event {
        RunEvent::WindowEvent {
            event: WindowEvent::CloseRequested { api, .. },
            ..
        } => {
            api.prevent_close();
            let state = app.state::<TerminalHost>();
            if let Ok(Some(approval)) = state.approval_snapshot() {
                if let Ok(resolution) = state.resolve_approval(&approval.id, "cancel") {
                    let _ = app.emit(APPROVAL_EVENT, ApprovalEvent { approval: None });
                    write_host_event(&resolution);
                }
            }
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.hide();
            }
        }
        RunEvent::ExitRequested { .. } => app.state::<TerminalHost>().stop_active(),
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_buffer_tracks_absolute_cursor_and_truncation() {
        let mut ring = RingBuffer::new(5);
        assert_eq!(ring.append(b"abc"), 3);
        assert_eq!(ring.append(b"def"), 6);
        let (output, cursor, truncated) = ring.read(0, 16);
        assert_eq!(output, b"bcdef");
        assert_eq!(cursor, 6);
        assert!(truncated);
    }

    #[test]
    fn spawn_validation_rejects_shell_like_invalid_shapes_without_interpreting_them() {
        let params = SpawnParams {
            command: vec![],
            cwd: "/".to_string(),
            columns: 120,
            rows: 30,
            yield_time_ms: 1000,
            timeout_ms: 120_000,
        };
        assert!(validate_spawn(&params).is_err());
    }

    #[test]
    fn terminal_size_is_bounded() {
        assert!(validate_size(120, 30).is_ok());
        assert!(validate_size(1, 30).is_err());
        assert!(validate_size(120, 501).is_err());
    }

    fn approval(scopes: &[&str]) -> ApprovalRequest {
        ApprovalRequest {
            id: "approval-1".to_string(),
            tool_name: "terminal_exec".to_string(),
            summary: "printf hello".to_string(),
            command: vec!["printf".to_string(), "hello".to_string()],
            cwd: "/tmp".to_string(),
            risk_level: "low".to_string(),
            allowed_scopes: scopes.iter().map(|scope| (*scope).to_string()).collect(),
        }
    }

    #[test]
    fn approval_requires_known_tool_and_once_scope() {
        assert!(validate_approval(&approval(&["once", "process"])).is_ok());

        let mut unknown = approval(&["once"]);
        unknown.tool_name = "open_url".to_string();
        assert!(validate_approval(&unknown).is_err());

        let process_only = approval(&["process"]);
        assert!(validate_approval(&process_only).is_err());
    }

    #[test]
    fn approval_resolution_rejects_stale_or_disallowed_decisions() {
        let host = TerminalHost::new(DEFAULT_RING_BYTES);
        host.set_approval(approval(&["once"])).unwrap();

        assert!(host.resolve_approval("stale", "once").is_err());
        assert!(host.resolve_approval("approval-1", "process").is_err());
        assert!(host.approval_snapshot().unwrap().is_some());

        let resolved = host.resolve_approval("approval-1", "once").unwrap();
        assert_eq!(resolved.approval_id, "approval-1");
        assert_eq!(resolved.decision, "once");
        assert!(host.approval_snapshot().unwrap().is_none());
    }

    #[test]
    fn approval_can_be_cancelled_when_window_closes() {
        let host = TerminalHost::new(DEFAULT_RING_BYTES);
        host.set_approval(approval(&["once", "process"])).unwrap();

        let pending = host.approval_snapshot().unwrap().unwrap();
        let resolved = host.resolve_approval(&pending.id, "cancel").unwrap();

        assert_eq!(resolved.approval_id, "approval-1");
        assert_eq!(resolved.decision, "cancel");
        assert!(host.approval_snapshot().unwrap().is_none());
    }

    #[test]
    fn timeout_wait_is_woken_when_process_finishes() {
        use std::sync::mpsc;

        let process = Arc::new(SharedProcess::running());
        let waiting_process = Arc::clone(&process);
        let (sender, receiver) = mpsc::channel();
        let waiter = thread::spawn(move || {
            sender
                .send(waiting_process.wait_for_timeout(Duration::from_secs(5)))
                .unwrap();
        });

        thread::sleep(Duration::from_millis(20));
        process.mark_finished(false, Some(0)).unwrap();

        assert!(!receiver.recv_timeout(Duration::from_millis(250)).unwrap());
        waiter.join().unwrap();
    }

    #[test]
    fn timeout_wait_reports_elapsed_deadline() {
        let process = SharedProcess::running();
        assert!(process.wait_for_timeout(Duration::from_millis(1)));
    }
}
