//! Native managed-process-tree backends and the POSIX guardian containment.

use std::{
    fs::File,
    io::{self, Read},
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

use super::{
    ManagedPipeReadOutcome, ManagedPipeReader, ManagedProcessPipes, ManagedProcessRequest,
    ManagedProcessTree, ManagedProcessTreeBackend, PlatformError, PlatformErrorCategory,
    PlatformResult, PlatformService, ProcessExitStatus, ProcessStdio, ProcessTreeFinalization,
    ProcessWaitOutcome, RetryAdvice, SpawnedProcessTree,
};

const PIPE_POLL_QUANTUM: Duration = Duration::from_millis(10);

struct NativePipeReader {
    file: File,
}

impl ManagedPipeReader for NativePipeReader {
    fn read_until(
        &mut self,
        buffer: &mut [u8],
        deadline: Instant,
        cancelled: &AtomicBool,
    ) -> PlatformResult<ManagedPipeReadOutcome> {
        native_pipe_read_until(&mut self.file, buffer, deadline, cancelled)
    }
}

#[cfg(unix)]
fn native_pipe_read_until(
    file: &mut File,
    buffer: &mut [u8],
    deadline: Instant,
    cancelled: &AtomicBool,
) -> PlatformResult<ManagedPipeReadOutcome> {
    use std::os::fd::AsRawFd;

    if buffer.is_empty() {
        return Ok(ManagedPipeReadOutcome::Read(0));
    }
    loop {
        if cancelled.load(Ordering::Acquire) {
            return Ok(ManagedPipeReadOutcome::Cancelled);
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(ManagedPipeReadOutcome::TimedOut);
        }
        let poll_for = remaining.min(PIPE_POLL_QUANTUM);
        let timeout_ms = poll_for.as_millis().max(1).min(i32::MAX as u128) as i32;
        let mut descriptor = libc::pollfd {
            fd: file.as_raw_fd(),
            events: libc::POLLIN | libc::POLLHUP | libc::POLLERR,
            revents: 0,
        };
        let poll_result = unsafe { libc::poll(&mut descriptor, 1, timeout_ms) };
        if poll_result == -1 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            return Err(pipe_io_error("poll_pipe", error));
        }
        if cancelled.load(Ordering::Acquire) {
            return Ok(ManagedPipeReadOutcome::Cancelled);
        }
        if Instant::now() >= deadline {
            return Ok(ManagedPipeReadOutcome::TimedOut);
        }
        if poll_result == 0 {
            continue;
        }
        if descriptor.revents & libc::POLLNVAL != 0 {
            return Err(pipe_native_error("poll_pipe", i64::from(libc::EBADF)));
        }
        if descriptor.revents & (libc::POLLIN | libc::POLLHUP | libc::POLLERR) == 0 {
            continue;
        }
        match file.read(buffer) {
            Ok(0) => return Ok(ManagedPipeReadOutcome::Eof),
            Ok(count) => return Ok(ManagedPipeReadOutcome::Read(count)),
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(error) if pipe_error_is_eof(&error) => return Ok(ManagedPipeReadOutcome::Eof),
            Err(error) => return Err(pipe_io_error("read_pipe", error)),
        }
    }
}

#[cfg(windows)]
fn native_pipe_read_until(
    file: &mut File,
    buffer: &mut [u8],
    deadline: Instant,
    cancelled: &AtomicBool,
) -> PlatformResult<ManagedPipeReadOutcome> {
    use std::os::windows::io::AsRawHandle;
    use windows::Win32::{
        Foundation::{
            GetLastError, ERROR_BROKEN_PIPE, ERROR_INVALID_HANDLE, ERROR_NO_DATA,
            ERROR_PIPE_NOT_CONNECTED, HANDLE,
        },
        System::Pipes::PeekNamedPipe,
    };

    if buffer.is_empty() {
        return Ok(ManagedPipeReadOutcome::Read(0));
    }
    loop {
        if cancelled.load(Ordering::Acquire) {
            return Ok(ManagedPipeReadOutcome::Cancelled);
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(ManagedPipeReadOutcome::TimedOut);
        }
        let mut available = 0_u32;
        let peek = unsafe {
            PeekNamedPipe(
                HANDLE(file.as_raw_handle()),
                None,
                0,
                None,
                Some(&mut available),
                None,
            )
        };
        if peek.is_err() {
            let code = unsafe { GetLastError() };
            if matches!(
                code,
                ERROR_BROKEN_PIPE | ERROR_PIPE_NOT_CONNECTED | ERROR_NO_DATA | ERROR_INVALID_HANDLE
            ) {
                return Ok(ManagedPipeReadOutcome::Eof);
            }
            return Err(pipe_native_error("peek_pipe", i64::from(code.0)));
        }
        if cancelled.load(Ordering::Acquire) {
            return Ok(ManagedPipeReadOutcome::Cancelled);
        }
        if Instant::now() >= deadline {
            return Ok(ManagedPipeReadOutcome::TimedOut);
        }
        if available == 0 {
            std::thread::sleep(remaining.min(PIPE_POLL_QUANTUM));
            continue;
        }
        let read_limit = buffer.len().min(available as usize);
        match file.read(&mut buffer[..read_limit]) {
            Ok(0) => return Ok(ManagedPipeReadOutcome::Eof),
            Ok(count) => return Ok(ManagedPipeReadOutcome::Read(count)),
            Err(error) if pipe_error_is_eof(&error) => return Ok(ManagedPipeReadOutcome::Eof),
            Err(error) => return Err(pipe_io_error("read_pipe", error)),
        }
    }
}

fn pipe_error_is_eof(error: &io::Error) -> bool {
    if error.kind() == io::ErrorKind::BrokenPipe {
        return true;
    }
    #[cfg(unix)]
    return error.raw_os_error() == Some(libc::EPIPE);
    #[cfg(windows)]
    return matches!(error.raw_os_error(), Some(6 | 109 | 232 | 233));
}

fn pipe_io_error(operation: &'static str, error: io::Error) -> PlatformError {
    let category = match error.kind() {
        io::ErrorKind::PermissionDenied => PlatformErrorCategory::PermissionDenied,
        io::ErrorKind::TimedOut => PlatformErrorCategory::TimedOut,
        io::ErrorKind::WouldBlock => PlatformErrorCategory::TemporarilyUnavailable,
        _ => PlatformErrorCategory::NativeFailure,
    };
    let mut platform_error = PlatformError::new(
        PlatformService::ManagedProcessTree,
        category,
        operation,
        RetryAdvice::Never,
        "native pipe read failed",
    );
    if let Some(code) = error.raw_os_error() {
        platform_error = platform_error.with_native_code(pipe_native_namespace(), i64::from(code));
    }
    platform_error
}

fn pipe_native_error(operation: &'static str, code: i64) -> PlatformError {
    PlatformError::new(
        PlatformService::ManagedProcessTree,
        PlatformErrorCategory::NativeFailure,
        operation,
        RetryAdvice::Never,
        "native pipe read failed",
    )
    .with_native_code(pipe_native_namespace(), code)
}

#[cfg(unix)]
const fn pipe_native_namespace() -> &'static str {
    "errno"
}

#[cfg(windows)]
const fn pipe_native_namespace() -> &'static str {
    "win32"
}

#[derive(Clone, Copy, Debug, Default)]
pub struct NativeManagedProcessTreeBackend;

#[cfg(windows)]
mod native {
    use super::*;
    use crate::managed_process_tree::{
        ManagedProcessSpec, ManagedProcessTree as WindowsManagedProcessTree, WaitOutcome,
    };

    pub(super) struct NativeTree {
        inner: WindowsManagedProcessTree,
    }

    // The wrapper has exclusive ownership of the Job and process handles. Moving
    // that ownership to the lifecycle worker does not permit concurrent handle use.
    unsafe impl Send for NativeTree {}

    impl ManagedProcessTree for NativeTree {
        fn root_pid(&self) -> u32 {
            self.inner.pid()
        }

        fn wait_root(&mut self, timeout: Duration) -> PlatformResult<ProcessWaitOutcome> {
            self.inner
                .wait(timeout)
                .map(|outcome| match outcome {
                    WaitOutcome::Exited(code) => {
                        ProcessWaitOutcome::Exited(ProcessExitStatus::Code(i64::from(code)))
                    }
                    WaitOutcome::TimedOut => ProcessWaitOutcome::TimedOut,
                })
                .map_err(|error| native_error("wait_root", error))
        }

        fn terminate_tree(&mut self, reason_code: u32) -> PlatformResult<()> {
            self.inner
                .terminate_tree(reason_code)
                .map_err(|error| native_error("terminate_tree", error))
        }

        fn wait_tree_exited(&self, timeout: Duration) -> PlatformResult<bool> {
            self.inner
                .verify_tree_exited(timeout)
                .map_err(|error| native_error("wait_tree_exited", error))
        }

        fn release_exited(mut self: Box<Self>) -> PlatformResult<()> {
            self.inner
                .release_exited_handles()
                .map_err(|error| native_error("release_exited", error))
        }

        fn finalize_until(
            self: Box<Self>,
            deadline: Instant,
            reason_code: u32,
        ) -> PlatformResult<ProcessTreeFinalization> {
            self.inner
                .finalize_until(deadline, reason_code)
                .map(|result| ProcessTreeFinalization {
                    root_status: ProcessExitStatus::Code(i64::from(result.exit_code)),
                    forced: result.forced,
                })
                .map_err(|error| native_error("finalize_until", error))
        }
    }

    pub(super) fn spawn(request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
        if !request.environment_overrides.is_empty() {
            return Err(PlatformError::new(
                PlatformService::ManagedProcessTree,
                PlatformErrorCategory::InvalidInput,
                "spawn",
                RetryAdvice::Never,
                "the accepted Windows backend does not allow per-process environment mutation",
            ));
        }
        let mut spec = ManagedProcessSpec::new(&request.program);
        for arg in &request.args {
            spec.arg(arg);
        }
        if let Some(directory) = &request.current_directory {
            spec.current_dir(directory);
        }
        let (inner, pipes) = match request.stdio {
            ProcessStdio::Null => (
                WindowsManagedProcessTree::spawn(&spec)
                    .map_err(|error| native_error("spawn", error))?,
                None,
            ),
            ProcessStdio::Piped => {
                let (tree, pipes) = WindowsManagedProcessTree::spawn_piped(&spec)
                    .map_err(|error| native_error("spawn_piped", error))?;
                (
                    tree,
                    Some(ManagedProcessPipes {
                        stdin: pipes.stdin,
                        stdout: Box::new(NativePipeReader { file: pipes.stdout }),
                        stderr: Box::new(NativePipeReader { file: pipes.stderr }),
                    }),
                )
            }
        };
        Ok(SpawnedProcessTree {
            tree: Box::new(NativeTree { inner }),
            pipes,
        })
    }

    fn native_error(
        operation: &'static str,
        error: crate::managed_process_tree::ManagedProcessError,
    ) -> PlatformError {
        let mut platform_error = PlatformError::new(
            PlatformService::ManagedProcessTree,
            match error {
                crate::managed_process_tree::ManagedProcessError::EmptyProgram
                | crate::managed_process_tree::ManagedProcessError::InvalidSpec(_)
                | crate::managed_process_tree::ManagedProcessError::InvalidState(_) => {
                    PlatformErrorCategory::InvalidInput
                }
                crate::managed_process_tree::ManagedProcessError::TimedOut => {
                    PlatformErrorCategory::TimedOut
                }
                crate::managed_process_tree::ManagedProcessError::Windows { .. } => {
                    PlatformErrorCategory::NativeFailure
                }
            },
            operation,
            RetryAdvice::Never,
            error.to_string(),
        );
        if let crate::managed_process_tree::ManagedProcessError::Windows { code, .. } = error {
            platform_error = platform_error.with_native_code("win32", i64::from(code));
        }
        platform_error
    }
}

#[cfg(unix)]
mod native {
    use std::{
        env,
        ffi::{OsStr, OsString},
        fs::File,
        io::{self, Read, Write},
        os::{
            fd::{AsRawFd, FromRawFd, RawFd},
            unix::{
                ffi::{OsStrExt, OsStringExt},
                process::{CommandExt, ExitStatusExt},
            },
        },
        path::{Path, PathBuf},
        process::{Child, Command, ExitStatus, Stdio},
        sync::{Mutex, MutexGuard},
        thread,
        time::Instant,
    };

    use super::*;

    const GUARDIAN_MODE_ENV: &str = "SAKURA_RUNTIME_V2_PROCESS_GUARDIAN";
    const GUARDIAN_CONTROL_FD_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_CONTROL_FD";
    const GUARDIAN_STATUS_FD_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_STATUS_FD";
    const GUARDIAN_PROGRAM_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_PROGRAM";
    const GUARDIAN_ARG_COUNT_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_ARG_COUNT";
    const GUARDIAN_STDIO_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_STDIO";
    const GUARDIAN_EXECUTABLE_ENV: &str = "SAKURA_RUNTIME_V2_GUARDIAN_EXECUTABLE";
    const GUARDIAN_READY_TIMEOUT: Duration = Duration::from_secs(5);
    const TERMINATE_GRACE: Duration = Duration::from_millis(500);
    // Guardian-only insurance while no explicit caller deadline is armed.
    // Explicit finalization carries only its remaining budget and never reaches this ceiling.
    const FORCE_WAIT: Duration = Duration::from_secs(5);
    static GUARDIAN_SPAWN_LOCK: Mutex<()> = Mutex::new(());

    pub(super) struct NativeTree {
        root_pid: u32,
        process_group_id: libc::pid_t,
        state: Mutex<TreeState>,
    }

    struct TreeState {
        guardian: Child,
        control: Option<File>,
        status: File,
        status_buffer: Vec<u8>,
        root_status: Option<ProcessExitStatus>,
        tree_exited: bool,
        released: bool,
        cleanup_deadline: Option<Instant>,
    }

    impl ManagedProcessTree for NativeTree {
        fn root_pid(&self) -> u32 {
            self.root_pid
        }

        #[cfg(test)]
        fn native_owner_pid_for_test(&self) -> Option<u32> {
            Some(self.state.lock().ok()?.guardian.id())
        }

        fn wait_root(&mut self, timeout: Duration) -> PlatformResult<ProcessWaitOutcome> {
            let mut state = lock_state(&self.state)?;
            if let Some(status) = state.root_status {
                return Ok(ProcessWaitOutcome::Exited(status));
            }
            if pump_until(&mut state, timeout, |state| state.root_status.is_some())? {
                Ok(ProcessWaitOutcome::Exited(
                    state
                        .root_status
                        .expect("root status is present after successful pump"),
                ))
            } else {
                Ok(ProcessWaitOutcome::TimedOut)
            }
        }

        fn terminate_tree(&mut self, _reason_code: u32) -> PlatformResult<()> {
            let mut state = lock_state(&self.state)?;
            state.control.take();
            Ok(())
        }

        fn wait_tree_exited(&self, timeout: Duration) -> PlatformResult<bool> {
            let mut state = lock_state(&self.state)?;
            if state.tree_exited {
                return Ok(true);
            }
            pump_until(&mut state, timeout, |state| state.tree_exited)
        }

        fn release_exited(self: Box<Self>) -> PlatformResult<()> {
            let mut state = lock_state(&self.state)?;
            if state.released {
                return Ok(());
            }
            if !state.tree_exited {
                return Err(platform_error(
                    PlatformErrorCategory::ResourceBusy,
                    "release_exited",
                    RetryAdvice::AfterExternalChange,
                    "cannot release a POSIX process tree before verified exit",
                ));
            }
            let Some(guardian_status) = reap_guardian_until(
                &mut state.guardian,
                Instant::now() + Duration::from_millis(100),
            )
            .map_err(|error| io_error("reap_guardian", error))?
            else {
                return Err(platform_error(
                    PlatformErrorCategory::ResourceBusy,
                    "release_exited",
                    RetryAdvice::AfterExternalChange,
                    "process guardian has not reached a reapable state",
                ));
            };
            if !guardian_status.success() {
                return Err(platform_error(
                    PlatformErrorCategory::NativeFailure,
                    "wait_guardian",
                    RetryAdvice::Never,
                    format!("process guardian exited unexpectedly: {guardian_status}"),
                ));
            }
            state.control.take();
            state.released = true;
            Ok(())
        }

        fn finalize_until(
            self: Box<Self>,
            deadline: Instant,
            _reason_code: u32,
        ) -> PlatformResult<ProcessTreeFinalization> {
            finalize_posix_tree(&self, deadline)
        }
    }

    impl Drop for NativeTree {
        fn drop(&mut self) {
            let Ok(mut state) = self.state.lock() else {
                unsafe {
                    libc::kill(-self.process_group_id, libc::SIGKILL);
                }
                return;
            };
            if state.released {
                return;
            }
            state.control.take();
            unsafe {
                libc::kill(-self.process_group_id, libc::SIGKILL);
            }
            let _ = state.guardian.kill();
            state.released = true;
        }
    }

    fn finalize_posix_tree(
        tree: &NativeTree,
        deadline: Instant,
    ) -> PlatformResult<ProcessTreeFinalization> {
        let mut state = lock_state(&tree.state)?;
        state.cleanup_deadline = Some(deadline);
        let expired_on_entry = Instant::now() >= deadline;

        pump_until_deadline(&mut state, Instant::now(), |_| false)
            .map_err(finalizer_platform_error)?;
        let forced = process_group_exists(tree.process_group_id)
            .map_err(|error| finalizer_io_error(error))?;
        if forced {
            arm_explicit_cleanup(&mut state, deadline)?;
            signal_group(tree.process_group_id, libc::SIGTERM).map_err(finalizer_io_error)?;
            if !expired_on_entry {
                let graceful_deadline = deadline.min(
                    Instant::now()
                        .checked_add(TERMINATE_GRACE)
                        .unwrap_or(deadline),
                );
                let _ =
                    pump_until_deadline(&mut state, graceful_deadline, |state| state.tree_exited)
                        .map_err(finalizer_platform_error)?;
            }
            if process_group_exists(tree.process_group_id).map_err(finalizer_io_error)? {
                signal_group(tree.process_group_id, libc::SIGKILL).map_err(finalizer_io_error)?;
            }
        }

        if expired_on_entry {
            immediate_posix_cleanup(&mut state, tree.process_group_id);
            return Err(finalization_timeout());
        }

        if !state.tree_exited
            && !pump_until_deadline(&mut state, deadline, |state| state.tree_exited)
                .map_err(finalizer_platform_error)?
        {
            immediate_posix_cleanup(&mut state, tree.process_group_id);
            return Err(finalization_timeout());
        }
        if process_group_exists(tree.process_group_id).map_err(finalizer_io_error)? {
            immediate_posix_cleanup(&mut state, tree.process_group_id);
            return Err(platform_error(
                PlatformErrorCategory::NativeFailure,
                "finalize_until",
                RetryAdvice::Never,
                "guardian reported completion before the process group reached zero",
            ));
        }

        let guardian_status = loop {
            if let Some(status) = state
                .guardian
                .try_wait()
                .map_err(|error| finalizer_io_error(error))?
            {
                break status;
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                immediate_posix_cleanup(&mut state, tree.process_group_id);
                return Err(finalization_timeout());
            }
            thread::sleep(Duration::from_millis(10).min(remaining));
        };
        if !guardian_status.success() {
            immediate_posix_cleanup(&mut state, tree.process_group_id);
            return Err(platform_error(
                PlatformErrorCategory::NativeFailure,
                "finalize_until",
                RetryAdvice::Never,
                "process guardian exited unsuccessfully during finalization",
            ));
        }
        let root_status = state.root_status.ok_or_else(|| {
            platform_error(
                PlatformErrorCategory::NativeFailure,
                "finalize_until",
                RetryAdvice::Never,
                "process guardian omitted the root exit status",
            )
        })?;
        state.control.take();
        state.released = true;
        Ok(ProcessTreeFinalization {
            root_status,
            forced,
        })
    }

    fn arm_explicit_cleanup(state: &mut TreeState, deadline: Instant) -> PlatformResult<()> {
        let Some(mut control) = state.control.take() else {
            return Ok(());
        };
        let remaining_nanos = deadline
            .saturating_duration_since(Instant::now())
            .as_nanos();
        control
            .write_all(format!("FINALIZE {remaining_nanos}\n").as_bytes())
            .map_err(|error| finalizer_io_error(error))?;
        Ok(())
    }

    fn immediate_posix_cleanup(state: &mut TreeState, process_group_id: libc::pid_t) {
        state.control.take();
        unsafe {
            libc::kill(-process_group_id, libc::SIGKILL);
        }
        let _ = state.guardian.kill();
        state.released = true;
    }

    fn reap_guardian_until(
        guardian: &mut Child,
        deadline: Instant,
    ) -> io::Result<Option<ExitStatus>> {
        loop {
            if let Some(status) = guardian.try_wait()? {
                return Ok(Some(status));
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Ok(None);
            }
            thread::sleep(Duration::from_millis(10).min(remaining));
        }
    }

    fn finalization_timeout() -> PlatformError {
        platform_error(
            PlatformErrorCategory::TimedOut,
            "finalize_until",
            RetryAdvice::Never,
            "managed process tree did not finalize before the caller deadline",
        )
    }

    fn finalizer_io_error(error: io::Error) -> PlatformError {
        finalizer_platform_error(io_error("finalize_until", error))
    }

    fn finalizer_platform_error(mut error: PlatformError) -> PlatformError {
        error.operation = "finalize_until";
        error.retry = RetryAdvice::Never;
        error.message = "native process tree finalization failed".into();
        error
    }

    pub(super) fn spawn(request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
        validate_request(request)?;
        let _spawn_guard = GUARDIAN_SPAWN_LOCK
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        let (control_read, control_write) =
            create_pipe().map_err(|error| io_error("create_control_pipe", error))?;
        let (status_read, status_write) =
            create_pipe().map_err(|error| io_error("create_status_pipe", error))?;
        set_cloexec(control_read.as_raw_fd(), false)
            .map_err(|error| io_error("inherit_control_pipe", error))?;
        set_cloexec(status_write.as_raw_fd(), false)
            .map_err(|error| io_error("inherit_status_pipe", error))?;

        let mut command = Command::new(guardian_executable()?);
        command
            .env(GUARDIAN_MODE_ENV, "1")
            .env(
                GUARDIAN_CONTROL_FD_ENV,
                control_read.as_raw_fd().to_string(),
            )
            .env(GUARDIAN_STATUS_FD_ENV, status_write.as_raw_fd().to_string())
            .env(
                GUARDIAN_PROGRAM_ENV,
                encode_os_string(request.program.as_os_str()),
            )
            .env(GUARDIAN_ARG_COUNT_ENV, request.args.len().to_string())
            .env(
                GUARDIAN_STDIO_ENV,
                match request.stdio {
                    ProcessStdio::Null => "null",
                    ProcessStdio::Piped => "piped",
                },
            );
        for (index, argument) in request.args.iter().enumerate() {
            command.env(
                guardian_argument_environment(index),
                encode_os_string(argument),
            );
        }
        if let Some(directory) = &request.current_directory {
            command.current_dir(directory);
        }
        command.envs(request.environment_overrides.iter().cloned());
        match request.stdio {
            ProcessStdio::Null => {
                command
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null());
            }
            ProcessStdio::Piped => {
                command
                    .stdin(Stdio::piped())
                    .stdout(Stdio::piped())
                    .stderr(Stdio::piped());
            }
        }

        let mut guardian = command
            .spawn()
            .map_err(|error| io_error("spawn_guardian", error))?;
        drop(control_read);
        drop(status_write);

        let pipes = match request.stdio {
            ProcessStdio::Null => None,
            ProcessStdio::Piped => Some(ManagedProcessPipes {
                stdin: child_stdin_file(
                    guardian
                        .stdin
                        .take()
                        .expect("piped guardian stdin must exist"),
                ),
                stdout: Box::new(NativePipeReader {
                    file: child_stdout_file(
                        guardian
                            .stdout
                            .take()
                            .expect("piped guardian stdout must exist"),
                    ),
                }),
                stderr: Box::new(NativePipeReader {
                    file: child_stderr_file(
                        guardian
                            .stderr
                            .take()
                            .expect("piped guardian stderr must exist"),
                    ),
                }),
            }),
        };
        let mut state = TreeState {
            guardian,
            control: Some(control_write),
            status: status_read,
            status_buffer: Vec::new(),
            root_status: None,
            tree_exited: false,
            released: false,
            cleanup_deadline: None,
        };
        let ready = read_status_line(&mut state, GUARDIAN_READY_TIMEOUT)?.ok_or_else(|| {
            platform_error(
                PlatformErrorCategory::TimedOut,
                "guardian_ready",
                RetryAdvice::Never,
                "process guardian did not establish containment before the deadline",
            )
        })?;
        let (root_pid, process_group_id) = parse_ready(&ready)?;
        Ok(SpawnedProcessTree {
            tree: Box::new(NativeTree {
                root_pid,
                process_group_id,
                state: Mutex::new(state),
            }),
            pipes,
        })
    }

    pub(crate) fn run_guardian_if_requested() -> bool {
        if env::var_os(GUARDIAN_MODE_ENV).as_deref() != Some(OsStr::new("1")) {
            return false;
        }
        let exit_code = match run_guardian() {
            Ok(()) => 0,
            Err(error) => {
                eprintln!(
                    "Sakura managed process guardian failed: {}",
                    sanitize_status_message(&error)
                );
                3
            }
        };
        std::process::exit(exit_code);
    }

    fn run_guardian() -> io::Result<()> {
        let control_fd = environment_fd(GUARDIAN_CONTROL_FD_ENV)?;
        let status_fd = environment_fd(GUARDIAN_STATUS_FD_ENV)?;
        set_cloexec(control_fd, true)?;
        set_cloexec(status_fd, true)?;
        let control = unsafe { File::from_raw_fd(control_fd) };
        let mut status = unsafe { File::from_raw_fd(status_fd) };
        let program = decode_environment_os_string(GUARDIAN_PROGRAM_ENV)?;
        let argument_count = env::var(GUARDIAN_ARG_COUNT_ENV)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "missing argument count"))?
            .parse::<usize>()
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid argument count"))?;
        let mut arguments = Vec::with_capacity(argument_count);
        for index in 0..argument_count {
            arguments.push(decode_environment_os_string(
                &guardian_argument_environment(index),
            )?);
        }
        let piped = env::var_os(GUARDIAN_STDIO_ENV).as_deref() == Some(OsStr::new("piped"));
        let mut child_command = Command::new(program);
        child_command.args(arguments);
        if piped {
            child_command
                .stdin(Stdio::inherit())
                .stdout(Stdio::inherit())
                .stderr(Stdio::inherit());
        } else {
            child_command
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null());
        }
        remove_guardian_environment(&mut child_command, argument_count);
        unsafe {
            child_command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(io::Error::last_os_error());
                }
                #[cfg(target_os = "linux")]
                {
                    let expected_parent = libc::getppid();
                    if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL) == -1 {
                        return Err(io::Error::last_os_error());
                    }
                    if libc::getppid() != expected_parent {
                        return Err(io::Error::new(
                            io::ErrorKind::BrokenPipe,
                            "guardian changed before parent-death insurance was established",
                        ));
                    }
                }
                Ok(())
            });
        }
        let mut child = child_command.spawn()?;
        let root_pid = child.id();
        let process_group_id = i32::try_from(root_pid)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "root pid exceeds pid_t"))?;
        writeln!(status, "READY {root_pid} {process_group_id}")?;
        status.flush()?;

        let (root_status, cleanup_mode) =
            monitor_root_or_control(&mut child, &control, process_group_id)?;
        write_root_status(&mut status, root_status)?;
        status.flush()?;
        cleanup_process_group(process_group_id, &control, cleanup_mode)?;
        writeln!(status, "TREE_EXITED")?;
        status.flush()
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum GuardianCleanupMode {
        Natural,
        Explicit(Instant),
        ParentDeath,
    }

    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum ControlEvent {
        Open,
        Explicit(Duration),
        Closed,
    }

    fn monitor_root_or_control(
        child: &mut Child,
        control: &File,
        process_group_id: libc::pid_t,
    ) -> io::Result<(ExitStatus, GuardianCleanupMode)> {
        loop {
            if let Some(status) = child.try_wait()? {
                return Ok((status, GuardianCleanupMode::Natural));
            }
            match control_event(control.as_raw_fd())? {
                ControlEvent::Open => {}
                ControlEvent::Explicit(remaining) => {
                    let deadline = Instant::now().checked_add(remaining).ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            "guardian finalization deadline overflowed",
                        )
                    })?;
                    loop {
                        if let Some(status) = child.try_wait()? {
                            return Ok((status, GuardianCleanupMode::Explicit(deadline)));
                        }
                        let remaining = deadline.saturating_duration_since(Instant::now());
                        if remaining.is_zero() {
                            return Err(io::Error::new(
                                io::ErrorKind::TimedOut,
                                "root survived explicit finalization deadline",
                            ));
                        }
                        thread::sleep(Duration::from_millis(10).min(remaining));
                    }
                }
                ControlEvent::Closed => {
                    signal_group(process_group_id, libc::SIGTERM)?;
                    let graceful_deadline = Instant::now() + TERMINATE_GRACE;
                    while Instant::now() < graceful_deadline {
                        if let Some(status) = child.try_wait()? {
                            return Ok((status, GuardianCleanupMode::ParentDeath));
                        }
                        thread::sleep(Duration::from_millis(10));
                    }
                    signal_group(process_group_id, libc::SIGKILL)?;
                    let force_deadline = Instant::now() + FORCE_WAIT;
                    while Instant::now() < force_deadline {
                        if let Some(status) = child.try_wait()? {
                            return Ok((status, GuardianCleanupMode::ParentDeath));
                        }
                        thread::sleep(Duration::from_millis(10));
                    }
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "root survived parent-death cleanup deadline",
                    ));
                }
            }
            thread::sleep(Duration::from_millis(10));
        }
    }

    fn cleanup_process_group(
        process_group_id: libc::pid_t,
        control: &File,
        mut cleanup_mode: GuardianCleanupMode,
    ) -> io::Result<()> {
        if !process_group_exists(process_group_id)? {
            return Ok(());
        }
        if cleanup_mode == GuardianCleanupMode::Natural {
            cleanup_mode = match control_event(control.as_raw_fd())? {
                ControlEvent::Open => GuardianCleanupMode::Natural,
                ControlEvent::Explicit(remaining) => GuardianCleanupMode::Explicit(
                    Instant::now().checked_add(remaining).ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::InvalidData,
                            "guardian finalization deadline overflowed",
                        )
                    })?,
                ),
                ControlEvent::Closed => GuardianCleanupMode::ParentDeath,
            };
        }
        if let GuardianCleanupMode::Explicit(deadline) = cleanup_mode {
            return wait_for_explicit_group_cleanup(process_group_id, deadline);
        }

        signal_group(process_group_id, libc::SIGTERM)?;
        let graceful_deadline = Instant::now() + TERMINATE_GRACE;
        while Instant::now() < graceful_deadline {
            if !process_group_exists(process_group_id)? {
                return Ok(());
            }
            if cleanup_mode == GuardianCleanupMode::Natural {
                match control_event(control.as_raw_fd())? {
                    ControlEvent::Open => {}
                    ControlEvent::Explicit(remaining) => {
                        let deadline = Instant::now().checked_add(remaining).ok_or_else(|| {
                            io::Error::new(
                                io::ErrorKind::InvalidData,
                                "guardian finalization deadline overflowed",
                            )
                        })?;
                        return wait_for_explicit_group_cleanup(process_group_id, deadline);
                    }
                    ControlEvent::Closed => cleanup_mode = GuardianCleanupMode::ParentDeath,
                }
            }
            thread::sleep(Duration::from_millis(10));
        }
        signal_group(process_group_id, libc::SIGKILL)?;
        // Every explicit-control branch above returns with the caller-derived deadline.
        // This ceiling therefore protects only an unarmed guardian cleanup.
        let force_deadline = Instant::now() + FORCE_WAIT;
        while Instant::now() < force_deadline {
            if !process_group_exists(process_group_id)? {
                return Ok(());
            }
            if cleanup_mode == GuardianCleanupMode::Natural {
                match control_event(control.as_raw_fd())? {
                    ControlEvent::Open => {}
                    ControlEvent::Explicit(remaining) => {
                        let deadline = Instant::now().checked_add(remaining).ok_or_else(|| {
                            io::Error::new(
                                io::ErrorKind::InvalidData,
                                "guardian finalization deadline overflowed",
                            )
                        })?;
                        return wait_for_explicit_group_cleanup(process_group_id, deadline);
                    }
                    ControlEvent::Closed => cleanup_mode = GuardianCleanupMode::ParentDeath,
                }
            }
            thread::sleep(Duration::from_millis(10));
        }
        Err(io::Error::new(
            io::ErrorKind::TimedOut,
            "process group survived forced cleanup deadline",
        ))
    }

    fn wait_for_explicit_group_cleanup(
        process_group_id: libc::pid_t,
        deadline: Instant,
    ) -> io::Result<()> {
        while process_group_exists(process_group_id)? {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "process group survived explicit finalization deadline",
                ));
            }
            thread::sleep(Duration::from_millis(10).min(remaining));
        }
        Ok(())
    }

    fn process_group_exists(process_group_id: libc::pid_t) -> io::Result<bool> {
        if unsafe { libc::kill(-process_group_id, 0) } == 0 {
            return Ok(true);
        }
        let error = io::Error::last_os_error();
        match error.raw_os_error() {
            Some(libc::ESRCH) => Ok(false),
            Some(libc::EPERM) => Ok(true),
            _ => Err(error),
        }
    }

    fn signal_group(process_group_id: libc::pid_t, signal: i32) -> io::Result<()> {
        if unsafe { libc::kill(-process_group_id, signal) } == 0 {
            return Ok(());
        }
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            Ok(())
        } else {
            Err(error)
        }
    }

    fn control_event(fd: RawFd) -> io::Result<ControlEvent> {
        let mut descriptor = libc::pollfd {
            fd,
            events: libc::POLLIN | libc::POLLHUP | libc::POLLERR,
            revents: 0,
        };
        let result = unsafe { libc::poll(&mut descriptor, 1, 0) };
        if result == -1 {
            return Err(io::Error::last_os_error());
        }
        if result == 0 {
            return Ok(ControlEvent::Open);
        }
        let mut command = [0_u8; 128];
        let read = unsafe { libc::read(fd, command.as_mut_ptr().cast(), command.len()) };
        if read > 0 {
            let command = std::str::from_utf8(&command[..read as usize]).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "invalid guardian control command",
                )
            })?;
            let remaining_nanos = command
                .strip_prefix("FINALIZE ")
                .and_then(|value| value.trim().parse::<u128>().ok())
                .ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        "invalid guardian control command",
                    )
                })?;
            let seconds = u64::try_from(remaining_nanos / 1_000_000_000).map_err(|_| {
                io::Error::new(
                    io::ErrorKind::InvalidData,
                    "guardian finalization duration overflowed",
                )
            })?;
            let nanos = (remaining_nanos % 1_000_000_000) as u32;
            return Ok(ControlEvent::Explicit(Duration::new(seconds, nanos)));
        }
        if read == 0 {
            return Ok(ControlEvent::Closed);
        }
        let error = io::Error::last_os_error();
        if matches!(
            error.kind(),
            io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
        ) {
            Ok(ControlEvent::Open)
        } else {
            Err(error)
        }
    }

    fn pump_until(
        state: &mut TreeState,
        timeout: Duration,
        complete: impl Fn(&TreeState) -> bool,
    ) -> PlatformResult<bool> {
        let deadline = Instant::now().checked_add(timeout).ok_or_else(|| {
            platform_error(
                PlatformErrorCategory::InvalidInput,
                "pump_guardian_status",
                RetryAdvice::Never,
                "guardian status deadline overflowed",
            )
        })?;
        pump_until_deadline(state, deadline, complete)
    }

    fn pump_until_deadline(
        state: &mut TreeState,
        deadline: Instant,
        complete: impl Fn(&TreeState) -> bool,
    ) -> PlatformResult<bool> {
        if complete(state) {
            return Ok(true);
        }
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            let Some(line) = read_status_line(state, remaining)? else {
                return Ok(complete(state));
            };
            apply_status_line(state, &line)?;
            if complete(state) {
                return Ok(true);
            }
        }
    }

    fn read_status_line(
        state: &mut TreeState,
        timeout: Duration,
    ) -> PlatformResult<Option<String>> {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(newline) = state.status_buffer.iter().position(|byte| *byte == b'\n') {
                let line = state.status_buffer.drain(..=newline).collect::<Vec<_>>();
                return String::from_utf8(line[..line.len() - 1].to_vec())
                    .map(Some)
                    .map_err(|_| {
                        platform_error(
                            PlatformErrorCategory::NativeFailure,
                            "read_guardian_status",
                            RetryAdvice::Never,
                            "guardian emitted non-UTF-8 status",
                        )
                    });
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            let mut descriptor = libc::pollfd {
                fd: state.status.as_raw_fd(),
                events: libc::POLLIN | libc::POLLHUP | libc::POLLERR,
                revents: 0,
            };
            let timeout_ms = remaining.as_millis().min(i32::MAX as u128) as i32;
            let poll_result = unsafe { libc::poll(&mut descriptor, 1, timeout_ms) };
            if poll_result == -1 {
                return Err(io_error("poll_guardian_status", io::Error::last_os_error()));
            }
            if poll_result == 0 {
                return Ok(None);
            }
            let mut byte = [0_u8; 1];
            match state.status.read(&mut byte) {
                Ok(0) => {
                    if state.tree_exited {
                        return Ok(None);
                    }
                    let guardian_status = state
                        .guardian
                        .try_wait()
                        .map_err(|error| io_error("wait_guardian", error))?;
                    return Err(platform_error(
                        PlatformErrorCategory::NativeFailure,
                        "read_guardian_status",
                        RetryAdvice::Never,
                        format!(
                            "guardian status pipe closed before TREE_EXITED ({guardian_status:?})"
                        ),
                    ));
                }
                Ok(_) => state.status_buffer.push(byte[0]),
                Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                Err(error) => return Err(io_error("read_guardian_status", error)),
            }
        }
    }

    fn apply_status_line(state: &mut TreeState, line: &str) -> PlatformResult<()> {
        let mut parts = line.split_whitespace();
        match parts.next() {
            Some("ROOT_CODE") => {
                let code = parse_i64(parts.next(), "root exit code")?;
                state.root_status = Some(ProcessExitStatus::Code(code));
            }
            Some("ROOT_SIGNAL") => {
                let signal = parse_i32(parts.next(), "root exit signal")?;
                state.root_status = Some(ProcessExitStatus::Signal(signal));
            }
            Some("ROOT_UNKNOWN") => state.root_status = Some(ProcessExitStatus::Unknown),
            Some("TREE_EXITED") => state.tree_exited = true,
            Some("ERROR") => {
                return Err(platform_error(
                    PlatformErrorCategory::NativeFailure,
                    "guardian",
                    RetryAdvice::Never,
                    line.strip_prefix("ERROR ").unwrap_or("guardian failed"),
                ))
            }
            _ => {
                return Err(platform_error(
                    PlatformErrorCategory::NativeFailure,
                    "guardian",
                    RetryAdvice::Never,
                    format!("guardian emitted an unknown status: {line}"),
                ))
            }
        }
        Ok(())
    }

    fn parse_ready(line: &str) -> PlatformResult<(u32, libc::pid_t)> {
        let mut parts = line.split_whitespace();
        if parts.next() != Some("READY") {
            return Err(platform_error(
                PlatformErrorCategory::NativeFailure,
                "guardian_ready",
                RetryAdvice::Never,
                format!("guardian failed before containment: {line}"),
            ));
        }
        let root_pid = parts
            .next()
            .and_then(|value| value.parse::<u32>().ok())
            .ok_or_else(|| {
                platform_error(
                    PlatformErrorCategory::NativeFailure,
                    "guardian_ready",
                    RetryAdvice::Never,
                    "guardian returned an invalid root pid",
                )
            })?;
        let process_group_id = parts
            .next()
            .and_then(|value| value.parse::<libc::pid_t>().ok())
            .ok_or_else(|| {
                platform_error(
                    PlatformErrorCategory::NativeFailure,
                    "guardian_ready",
                    RetryAdvice::Never,
                    "guardian returned an invalid process group id",
                )
            })?;
        if i64::from(root_pid) != i64::from(process_group_id) {
            return Err(platform_error(
                PlatformErrorCategory::IdentityChanged,
                "guardian_ready",
                RetryAdvice::Never,
                "root pid and process group identity differ",
            ));
        }
        Ok((root_pid, process_group_id))
    }

    fn write_root_status(status: &mut File, root_status: ExitStatus) -> io::Result<()> {
        if let Some(code) = root_status.code() {
            writeln!(status, "ROOT_CODE {code}")
        } else if let Some(signal) = root_status.signal() {
            writeln!(status, "ROOT_SIGNAL {signal}")
        } else {
            writeln!(status, "ROOT_UNKNOWN")
        }
    }

    fn validate_request(request: &ManagedProcessRequest) -> PlatformResult<()> {
        if request.program.as_os_str().is_empty() {
            return Err(platform_error(
                PlatformErrorCategory::InvalidInput,
                "spawn",
                RetryAdvice::Never,
                "managed process program is empty",
            ));
        }
        if request.program.as_os_str().as_bytes().contains(&0)
            || request
                .args
                .iter()
                .any(|argument| argument.as_bytes().contains(&0))
        {
            return Err(platform_error(
                PlatformErrorCategory::InvalidInput,
                "spawn",
                RetryAdvice::Never,
                "managed process program and arguments cannot contain NUL",
            ));
        }
        if request.environment_overrides.iter().any(|(key, _)| {
            key == OsStr::new(GUARDIAN_MODE_ENV)
                || key == OsStr::new(GUARDIAN_CONTROL_FD_ENV)
                || key == OsStr::new(GUARDIAN_STATUS_FD_ENV)
                || key == OsStr::new(GUARDIAN_PROGRAM_ENV)
                || key == OsStr::new(GUARDIAN_ARG_COUNT_ENV)
                || key == OsStr::new(GUARDIAN_STDIO_ENV)
                || key == OsStr::new(GUARDIAN_EXECUTABLE_ENV)
                || key
                    .to_string_lossy()
                    .starts_with("SAKURA_RUNTIME_V2_GUARDIAN_ARG_")
        }) {
            return Err(platform_error(
                PlatformErrorCategory::InvalidInput,
                "spawn",
                RetryAdvice::Never,
                "managed process environment overrides cannot shadow guardian controls",
            ));
        }
        Ok(())
    }

    fn create_pipe() -> io::Result<(File, File)> {
        let mut descriptors = [-1; 2];
        if unsafe { libc::pipe(descriptors.as_mut_ptr()) } == -1 {
            return Err(io::Error::last_os_error());
        }
        let read = unsafe { File::from_raw_fd(descriptors[0]) };
        let write = unsafe { File::from_raw_fd(descriptors[1]) };
        set_cloexec(read.as_raw_fd(), true)?;
        set_cloexec(write.as_raw_fd(), true)?;
        Ok((read, write))
    }

    fn set_cloexec(fd: RawFd, enabled: bool) -> io::Result<()> {
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFD) };
        if flags == -1 {
            return Err(io::Error::last_os_error());
        }
        let updated = if enabled {
            flags | libc::FD_CLOEXEC
        } else {
            flags & !libc::FD_CLOEXEC
        };
        if unsafe { libc::fcntl(fd, libc::F_SETFD, updated) } == -1 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    fn guardian_executable() -> PlatformResult<PathBuf> {
        if let Some(path) = env::var_os(GUARDIAN_EXECUTABLE_ENV) {
            let path = PathBuf::from(path);
            if path.is_absolute() {
                return Ok(path);
            }
            return Err(platform_error(
                PlatformErrorCategory::InvalidInput,
                "locate_guardian",
                RetryAdvice::Never,
                "guardian executable override must be absolute",
            ));
        }
        let current = env::current_exe().map_err(|error| io_error("locate_guardian", error))?;
        if current
            .parent()
            .and_then(Path::file_name)
            .is_some_and(|name| name == OsStr::new("deps"))
        {
            let candidate = current
                .parent()
                .and_then(Path::parent)
                .expect("Cargo test executable has target profile parent")
                .join(env!("CARGO_PKG_NAME"));
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
        Ok(current)
    }

    fn environment_fd(name: &str) -> io::Result<RawFd> {
        env::var(name)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, format!("missing {name}")))?
            .parse::<RawFd>()
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, format!("invalid {name}")))
    }

    fn encode_os_string(value: &OsStr) -> String {
        const HEX: &[u8; 16] = b"0123456789abcdef";
        let mut encoded = String::with_capacity(value.as_bytes().len() * 2);
        for byte in value.as_bytes() {
            encoded.push(char::from(HEX[(byte >> 4) as usize]));
            encoded.push(char::from(HEX[(byte & 0x0f) as usize]));
        }
        encoded
    }

    fn decode_environment_os_string(name: &str) -> io::Result<OsString> {
        let encoded = env::var(name)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, format!("missing {name}")))?;
        if encoded.len() % 2 != 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                format!("invalid {name}"),
            ));
        }
        let mut bytes = Vec::with_capacity(encoded.len() / 2);
        for pair in encoded.as_bytes().chunks_exact(2) {
            let high = decode_hex(pair[0])?;
            let low = decode_hex(pair[1])?;
            bytes.push((high << 4) | low);
        }
        Ok(OsString::from_vec(bytes))
    }

    fn decode_hex(value: u8) -> io::Result<u8> {
        match value {
            b'0'..=b'9' => Ok(value - b'0'),
            b'a'..=b'f' => Ok(value - b'a' + 10),
            _ => Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "invalid guardian hex payload",
            )),
        }
    }

    fn guardian_argument_environment(index: usize) -> String {
        format!("SAKURA_RUNTIME_V2_GUARDIAN_ARG_{index}")
    }

    fn remove_guardian_environment(command: &mut Command, argument_count: usize) {
        for name in [
            GUARDIAN_MODE_ENV,
            GUARDIAN_CONTROL_FD_ENV,
            GUARDIAN_STATUS_FD_ENV,
            GUARDIAN_PROGRAM_ENV,
            GUARDIAN_ARG_COUNT_ENV,
            GUARDIAN_STDIO_ENV,
            GUARDIAN_EXECUTABLE_ENV,
        ] {
            command.env_remove(name);
        }
        for index in 0..argument_count {
            command.env_remove(guardian_argument_environment(index));
        }
    }

    fn child_stdin_file(stdin: std::process::ChildStdin) -> File {
        let fd = std::os::fd::IntoRawFd::into_raw_fd(stdin);
        unsafe { File::from_raw_fd(fd) }
    }

    fn child_stdout_file(stdout: std::process::ChildStdout) -> File {
        let fd = std::os::fd::IntoRawFd::into_raw_fd(stdout);
        unsafe { File::from_raw_fd(fd) }
    }

    fn child_stderr_file(stderr: std::process::ChildStderr) -> File {
        let fd = std::os::fd::IntoRawFd::into_raw_fd(stderr);
        unsafe { File::from_raw_fd(fd) }
    }

    fn lock_state(state: &Mutex<TreeState>) -> PlatformResult<MutexGuard<'_, TreeState>> {
        state.lock().map_err(|_| {
            platform_error(
                PlatformErrorCategory::NativeFailure,
                "lock_tree_state",
                RetryAdvice::Never,
                "managed process tree state was poisoned",
            )
        })
    }

    fn parse_i64(value: Option<&str>, name: &str) -> PlatformResult<i64> {
        value.and_then(|value| value.parse().ok()).ok_or_else(|| {
            platform_error(
                PlatformErrorCategory::NativeFailure,
                "guardian",
                RetryAdvice::Never,
                format!("guardian returned an invalid {name}"),
            )
        })
    }

    fn parse_i32(value: Option<&str>, name: &str) -> PlatformResult<i32> {
        value.and_then(|value| value.parse().ok()).ok_or_else(|| {
            platform_error(
                PlatformErrorCategory::NativeFailure,
                "guardian",
                RetryAdvice::Never,
                format!("guardian returned an invalid {name}"),
            )
        })
    }

    fn io_error(operation: &'static str, error: io::Error) -> PlatformError {
        let category = match error.kind() {
            io::ErrorKind::NotFound => PlatformErrorCategory::NotFound,
            io::ErrorKind::PermissionDenied => PlatformErrorCategory::PermissionDenied,
            io::ErrorKind::TimedOut => PlatformErrorCategory::TimedOut,
            io::ErrorKind::InvalidInput | io::ErrorKind::InvalidData => {
                PlatformErrorCategory::InvalidInput
            }
            io::ErrorKind::WouldBlock => PlatformErrorCategory::TemporarilyUnavailable,
            _ => PlatformErrorCategory::NativeFailure,
        };
        let mut platform_error =
            platform_error(category, operation, RetryAdvice::Never, error.to_string());
        if let Some(code) = error.raw_os_error() {
            platform_error = platform_error.with_native_code("errno", i64::from(code));
        }
        platform_error
    }

    fn platform_error(
        category: PlatformErrorCategory,
        operation: &'static str,
        retry: RetryAdvice,
        message: impl Into<String>,
    ) -> PlatformError {
        PlatformError::new(
            PlatformService::ManagedProcessTree,
            category,
            operation,
            retry,
            message,
        )
    }

    fn sanitize_status_message(error: &io::Error) -> String {
        error
            .to_string()
            .replace(['\r', '\n'], " ")
            .chars()
            .take(512)
            .collect()
    }
}

impl ManagedProcessTreeBackend for NativeManagedProcessTreeBackend {
    fn spawn(&self, request: &ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
        native::spawn(request)
    }
}

#[cfg(unix)]
pub(crate) use native::run_guardian_if_requested;

#[cfg(test)]
mod tests {
    use std::{
        path::PathBuf,
        sync::atomic::AtomicBool,
        time::{Duration, Instant},
    };

    use super::*;

    #[test]
    fn native_backend_contract_is_injectable() {
        let backend = NativeManagedProcessTreeBackend;
        let _: &dyn ManagedProcessTreeBackend = &backend;
    }

    #[test]
    fn empty_program_fails_closed() {
        let error = match NativeManagedProcessTreeBackend.spawn(&ManagedProcessRequest {
            program: PathBuf::new(),
            args: Vec::new(),
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Null,
        }) {
            Ok(_) => panic!("empty programs must fail before native spawn"),
            Err(error) => error,
        };
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
    }

    struct ObservedTree {
        tree: Box<dyn ManagedProcessTree>,
        #[cfg(unix)]
        identity: PosixTreeIdentity,
    }

    #[cfg(unix)]
    #[derive(Clone, Copy, Debug)]
    struct PosixTreeIdentity {
        guardian_pid: libc::pid_t,
        process_group_id: libc::pid_t,
    }

    fn spawn_observed(request: ManagedProcessRequest) -> ObservedTree {
        let spawned = NativeManagedProcessTreeBackend
            .spawn(&request)
            .expect("native finalizer fixture should spawn");
        assert!(spawned.pipes.is_none());
        #[cfg(unix)]
        let identity = {
            let process_group_id = i32::try_from(spawned.tree.root_pid())
                .expect("root pid should fit the native pid type");
            PosixTreeIdentity {
                guardian_pid: i32::try_from(
                    spawned
                        .tree
                        .native_owner_pid_for_test()
                        .expect("POSIX tree should expose its guardian to native tests"),
                )
                .expect("guardian pid should fit the native pid type"),
                process_group_id,
            }
        };
        ObservedTree {
            tree: spawned.tree,
            #[cfg(unix)]
            identity,
        }
    }

    fn assert_finalization(
        result: ProcessTreeFinalization,
        expected_status: Option<ProcessExitStatus>,
        forced: bool,
    ) {
        if let Some(expected_status) = expected_status {
            assert_eq!(result.root_status, expected_status);
        }
        assert_eq!(result.forced, forced);
    }

    fn finalize_forced(tree: Box<dyn ManagedProcessTree>) -> ProcessTreeFinalization {
        let started = Instant::now();
        let result = tree
            .finalize_until(started + Duration::from_secs(2), 97)
            .expect("managed tree must finalize inside the one deadline");
        assert!(result.forced);
        assert!(started.elapsed() < Duration::from_secs(2));
        result
    }

    #[test]
    fn finalizer_preserves_a_naturally_exited_root_without_forcing() {
        let ObservedTree {
            mut tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(normal_exit_request());
        assert_eq!(
            tree.wait_root(Duration::from_secs(2))
                .expect("normal root observation should succeed"),
            ProcessWaitOutcome::Exited(ProcessExitStatus::Code(23))
        );
        assert!(tree
            .wait_tree_exited(Duration::from_secs(2))
            .expect("normal tree observation should reach zero"));

        let result = tree
            .finalize_until(Instant::now() + Duration::from_secs(2), 97)
            .expect("already exited tree should finalize");
        assert_finalization(result, Some(ProcessExitStatus::Code(23)), false);
        #[cfg(unix)]
        assert_posix_identity_gone(identity);
    }

    #[test]
    fn finalizer_forces_a_holding_root_inside_the_single_deadline() {
        let ObservedTree {
            tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(holding_root_request());

        assert_finalization(finalize_forced(tree), None, true);
        #[cfg(unix)]
        assert_posix_identity_gone(identity);
    }

    #[test]
    fn finalizer_reclaims_a_term_ignoring_descendant_after_root_first_exit() {
        let marker = finalizer_marker("root-first");
        let ObservedTree {
            mut tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(root_first_request(&marker));
        wait_for_marker(&marker);
        assert!(matches!(
            tree.wait_root(Duration::from_secs(2))
                .expect("root-first status should be observed"),
            ProcessWaitOutcome::Exited(_)
        ));

        assert_finalization(finalize_forced(tree), None, true);
        #[cfg(unix)]
        assert_posix_identity_gone(identity);
        let _ = std::fs::remove_file(marker);
    }

    #[test]
    fn finalizer_reclaims_two_descendant_levels() {
        let marker = finalizer_marker("two-levels");
        let ObservedTree {
            tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(two_level_request(&marker));
        wait_for_marker(&marker);

        assert_finalization(finalize_forced(tree), None, true);
        #[cfg(unix)]
        assert_posix_identity_gone(identity);
        let _ = std::fs::remove_file(marker);
    }

    #[test]
    fn finalizer_tolerates_repeated_pre_finalize_observations() {
        let ObservedTree {
            mut tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(holding_root_request());
        for _ in 0..3 {
            assert_eq!(
                tree.wait_root(Duration::ZERO)
                    .expect("zero-time root observation should succeed"),
                ProcessWaitOutcome::TimedOut
            );
            assert!(!tree
                .wait_tree_exited(Duration::ZERO)
                .expect("zero-time tree observation should succeed"));
        }

        assert_finalization(finalize_forced(tree), None, true);
        #[cfg(unix)]
        assert_posix_identity_gone(identity);
    }

    #[test]
    fn expired_finalizer_deadline_still_closes_and_kills_owned_resources() {
        let ObservedTree {
            tree,
            #[cfg(unix)]
            identity,
        } = spawn_observed(holding_root_request());
        let deadline = Instant::now()
            .checked_sub(Duration::from_millis(1))
            .expect("one millisecond should fit before the current instant");

        let error = tree
            .finalize_until(deadline, 97)
            .expect_err("expired finalization must report its exhausted budget");
        assert_eq!(error.category, PlatformErrorCategory::TimedOut);
        assert_eq!(error.operation, "finalize_until");
        #[cfg(unix)]
        assert_posix_group_gone(identity.process_group_id);
    }

    #[test]
    fn finalizer_releases_native_ownership_in_a_bounded_loop() {
        let before = native_resource_count();
        for _ in 0..8 {
            let ObservedTree {
                mut tree,
                #[cfg(unix)]
                identity,
            } = spawn_observed(normal_exit_request());
            assert!(matches!(
                tree.wait_root(Duration::from_secs(2)).unwrap(),
                ProcessWaitOutcome::Exited(_)
            ));
            tree.finalize_until(Instant::now() + Duration::from_secs(2), 97)
                .expect("bounded-loop tree should finalize");
            #[cfg(unix)]
            assert_posix_identity_gone(identity);
        }
        let after = native_resource_count();
        #[cfg(unix)]
        assert_eq!(
            after, before,
            "control/status descriptors must return to baseline"
        );
        #[cfg(windows)]
        assert!(
            after <= before + 2,
            "process/Job handles leaked: before={before}, after={after}"
        );
    }

    fn finalizer_marker(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "sakura-wp-3-01-finalizer-{label}-{}",
            std::process::id()
        ))
    }

    #[cfg(unix)]
    fn wait_for_marker(marker: &std::path::Path) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while !marker.exists() {
            assert!(Instant::now() < deadline, "fixture marker should appear");
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[cfg(windows)]
    fn wait_for_marker(_marker: &std::path::Path) {
        std::thread::sleep(Duration::from_millis(200));
    }

    #[cfg(unix)]
    fn normal_exit_request() -> ManagedProcessRequest {
        shell_request("exit 23")
    }

    #[cfg(unix)]
    fn holding_root_request() -> ManagedProcessRequest {
        shell_request("while :; do sleep 1; done")
    }

    #[cfg(unix)]
    fn root_first_request(marker: &std::path::Path) -> ManagedProcessRequest {
        shell_request(&format!(
            "(trap '' TERM; echo ready > '{}'; while :; do sleep 1; done) & exit 44",
            marker.display()
        ))
    }

    #[cfg(unix)]
    fn two_level_request(marker: &std::path::Path) -> ManagedProcessRequest {
        shell_request(&format!(
            "(/bin/sh -c 'sleep 60 & echo ready > \"{}\"; wait') & wait",
            marker.display()
        ))
    }

    #[cfg(unix)]
    fn shell_request(script: &str) -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from("/bin/sh"),
            args: vec!["-c".into(), script.into()],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Null,
        }
    }

    #[cfg(windows)]
    fn normal_exit_request() -> ManagedProcessRequest {
        windows_fixture_request("finalizer_fixture_exit_23")
    }

    #[cfg(windows)]
    fn holding_root_request() -> ManagedProcessRequest {
        windows_fixture_request("finalizer_fixture_holds")
    }

    #[cfg(windows)]
    fn root_first_request(_marker: &std::path::Path) -> ManagedProcessRequest {
        windows_fixture_request("finalizer_fixture_root_first")
    }

    #[cfg(windows)]
    fn two_level_request(_marker: &std::path::Path) -> ManagedProcessRequest {
        windows_fixture_request("finalizer_fixture_two_levels")
    }

    #[cfg(windows)]
    fn windows_fixture_request(name: &str) -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: std::env::current_exe().expect("current test executable should resolve"),
            args: vec![
                "--ignored".into(),
                "--exact".into(),
                format!("platform::process_tree_backend::tests::{name}").into(),
                "--nocapture".into(),
            ],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Null,
        }
    }

    #[cfg(unix)]
    fn native_process_exists(pid: libc::pid_t) -> bool {
        if unsafe { libc::kill(pid, 0) } == 0 {
            return true;
        }
        io::Error::last_os_error().raw_os_error() != Some(libc::ESRCH)
    }

    #[cfg(unix)]
    fn assert_posix_identity_gone(identity: PosixTreeIdentity) {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let guardian_exists = native_process_exists(identity.guardian_pid);
            let group_exists = native_process_exists(-identity.process_group_id);
            if !guardian_exists && !group_exists {
                return;
            }
            assert!(
                Instant::now() < deadline,
                "guardian/PGID must be gone: {identity:?}"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[cfg(unix)]
    fn assert_posix_group_gone(process_group_id: libc::pid_t) {
        let deadline = Instant::now() + Duration::from_secs(2);
        while native_process_exists(-process_group_id) {
            assert!(
                Instant::now() < deadline,
                "expired finalization must still kill PGID {process_group_id}"
            );
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[cfg(unix)]
    fn native_resource_count() -> u32 {
        (0..1024)
            .filter(|descriptor| {
                if unsafe { libc::fcntl(*descriptor, libc::F_GETFD) } != -1 {
                    return true;
                }
                io::Error::last_os_error().raw_os_error() != Some(libc::EBADF)
            })
            .count() as u32
    }

    #[cfg(windows)]
    fn native_resource_count() -> u32 {
        use windows::Win32::System::Threading::{GetCurrentProcess, GetProcessHandleCount};

        let mut count = 0;
        unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) }
            .expect("current process handle count should query");
        count
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "test-process fixture; launched by finalizer tests"]
    fn finalizer_fixture_exit_23() {
        std::process::exit(23);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "test-process fixture; launched by finalizer tests"]
    fn finalizer_fixture_holds() {
        std::thread::sleep(Duration::from_secs(60));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "test-process fixture; launched by finalizer tests"]
    fn finalizer_fixture_root_first() {
        std::process::Command::new(
            std::env::current_exe().expect("current test executable should resolve"),
        )
        .args([
            "--ignored",
            "--exact",
            "platform::process_tree_backend::tests::finalizer_fixture_holds",
            "--nocapture",
        ])
        .spawn()
        .expect("holding descendant should spawn");
        std::process::exit(44);
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "test-process fixture; launched by finalizer tests"]
    fn finalizer_fixture_two_levels() {
        std::process::Command::new(
            std::env::current_exe().expect("current test executable should resolve"),
        )
        .args([
            "--ignored",
            "--exact",
            "platform::process_tree_backend::tests::finalizer_fixture_spawns_leaf",
            "--nocapture",
        ])
        .spawn()
        .expect("first descendant should spawn");
        std::thread::sleep(Duration::from_secs(60));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "test-process fixture; launched by finalizer tests"]
    fn finalizer_fixture_spawns_leaf() {
        std::process::Command::new(
            std::env::current_exe().expect("current test executable should resolve"),
        )
        .args([
            "--ignored",
            "--exact",
            "platform::process_tree_backend::tests::finalizer_fixture_holds",
            "--nocapture",
        ])
        .spawn()
        .expect("second descendant should spawn");
        std::thread::sleep(Duration::from_secs(60));
    }

    #[test]
    fn native_pipe_read_times_out_while_child_holds_stdout_open() {
        let spawned = spawn_pipe_fixture(pipe_hold_open_request())
            .expect("hold-open fixture should spawn with managed pipes");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("hold-open fixture returns pipes");
        drop(pipes.stdin);
        let cancelled = AtomicBool::new(false);
        let started = Instant::now();
        let outcome = pipes
            .stdout
            .read_until(
                &mut [0_u8; 32],
                started + Duration::from_millis(50),
                &cancelled,
            )
            .expect("deadline-aware pipe read should not fail");
        assert_eq!(outcome, ManagedPipeReadOutcome::TimedOut);
        assert!(
            started.elapsed() < Duration::from_millis(500),
            "50ms pipe deadline must remain bounded"
        );
        reclaim_fixture(&mut tree);
        tree.release_exited().expect("fixture handles release");
    }

    #[test]
    fn native_pipe_read_honors_preexisting_cancellation() {
        let spawned = spawn_pipe_fixture(pipe_hold_open_request())
            .expect("hold-open fixture should spawn with managed pipes");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("hold-open fixture returns pipes");
        drop(pipes.stdin);
        let cancelled = AtomicBool::new(true);
        let outcome = pipes
            .stdout
            .read_until(
                &mut [0_u8; 32],
                Instant::now() + Duration::from_secs(1),
                &cancelled,
            )
            .expect("cancelled pipe read should not fail");
        assert_eq!(outcome, ManagedPipeReadOutcome::Cancelled);
        reclaim_fixture(&mut tree);
        tree.release_exited().expect("fixture handles release");
    }

    #[test]
    fn native_pipe_read_reports_data_then_eof() {
        let spawned = spawn_pipe_fixture(pipe_output_request())
            .expect("output fixture should spawn with managed pipes");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("output fixture returns pipes");
        drop(pipes.stdin);
        let cancelled = AtomicBool::new(false);
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut output = Vec::new();
        loop {
            let mut chunk = [0_u8; 32];
            match pipes
                .stdout
                .read_until(&mut chunk, deadline, &cancelled)
                .expect("output pipe read should succeed")
            {
                ManagedPipeReadOutcome::Read(count) => output.extend_from_slice(&chunk[..count]),
                ManagedPipeReadOutcome::Eof => break,
                other => panic!("output fixture returned unexpected outcome: {other:?}"),
            }
        }
        assert_eq!(output, b"managed-pipe");
        assert!(matches!(
            tree.wait_root(Duration::from_secs(5))
                .expect("output fixture root wait succeeds"),
            ProcessWaitOutcome::Exited(_)
        ));
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("output fixture tree exits"));
        tree.release_exited().expect("fixture handles release");
    }

    fn spawn_pipe_fixture(request: ManagedProcessRequest) -> PlatformResult<SpawnedProcessTree> {
        NativeManagedProcessTreeBackend.spawn(&request)
    }

    fn read_pipe_to_end(reader: &mut dyn ManagedPipeReader) -> Vec<u8> {
        let cancelled = AtomicBool::new(false);
        let deadline = Instant::now() + Duration::from_secs(5);
        let mut output = Vec::new();
        loop {
            let mut chunk = [0_u8; 128];
            match reader
                .read_until(&mut chunk, deadline, &cancelled)
                .expect("managed pipe should drain")
            {
                ManagedPipeReadOutcome::Read(count) => output.extend_from_slice(&chunk[..count]),
                ManagedPipeReadOutcome::Eof => return output,
                other => panic!("managed pipe returned unexpected drain outcome: {other:?}"),
            }
        }
    }

    #[cfg(unix)]
    fn pipe_hold_open_request() -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from("/bin/sh"),
            args: vec!["-c".into(), "sleep 2".into()],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        }
    }

    #[cfg(windows)]
    fn pipe_hold_open_request() -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from(std::env::var_os("COMSPEC").expect("Windows cmd path")),
            args: vec![
                "/D".into(),
                "/S".into(),
                "/C".into(),
                "ping -n 3 127.0.0.1 >NUL".into(),
            ],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        }
    }

    #[cfg(unix)]
    fn pipe_output_request() -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from("/bin/sh"),
            args: vec!["-c".into(), "printf managed-pipe".into()],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        }
    }

    #[cfg(windows)]
    fn pipe_output_request() -> ManagedProcessRequest {
        ManagedProcessRequest {
            program: PathBuf::from(std::env::var_os("COMSPEC").expect("Windows cmd path")),
            args: vec![
                "/D".into(),
                "/S".into(),
                "/C".into(),
                "<NUL set /P =managed-pipe".into(),
            ],
            current_directory: None,
            environment_overrides: Vec::new(),
            stdio: ProcessStdio::Piped,
        }
    }

    fn reclaim_fixture(tree: &mut Box<dyn ManagedProcessTree>) {
        tree.terminate_tree(93)
            .expect("fixture tree should terminate cooperatively");
        assert!(matches!(
            tree.wait_root(Duration::from_secs(5))
                .expect("fixture root wait succeeds"),
            ProcessWaitOutcome::Exited(_)
        ));
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("fixture tree exits"));
    }

    #[cfg(windows)]
    #[test]
    fn windows_backend_preserves_piped_job_spawn_wait_and_release() {
        let spawned = NativeManagedProcessTreeBackend
            .spawn(&ManagedProcessRequest {
                program: PathBuf::from(std::env::var_os("COMSPEC").expect("Windows cmd path")),
                args: vec![
                    "/D".into(),
                    "/S".into(),
                    "/C".into(),
                    "echo managed-tree".into(),
                ],
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            })
            .expect("Windows backend should delegate to the accepted Job spawn");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("piped Job spawn returns three pipes");
        drop(pipes.stdin);
        assert_eq!(
            tree.wait_root(Duration::from_secs(5))
                .expect("Job root wait succeeds"),
            ProcessWaitOutcome::Exited(ProcessExitStatus::Code(0))
        );
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("Job verification succeeds"));
        let output = String::from_utf8(read_pipe_to_end(pipes.stdout.as_mut()))
            .expect("Job stdout should be UTF-8");
        assert_eq!(output.trim(), "managed-tree");
        tree.release_exited().expect("verified Job releases");
    }

    #[cfg(unix)]
    #[test]
    fn posix_piped_spawn_wait_verify_and_release_are_bounded() {
        let spawned = NativeManagedProcessTreeBackend
            .spawn(&ManagedProcessRequest {
                program: PathBuf::from("/bin/sh"),
                args: vec!["-c".into(), "printf managed-tree; exit 7".into()],
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            })
            .expect("POSIX guardian should establish containment");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("piped spawn returns three pipes");
        drop(pipes.stdin);
        assert_eq!(
            tree.wait_root(Duration::from_secs(5))
                .expect("root wait succeeds"),
            ProcessWaitOutcome::Exited(ProcessExitStatus::Code(7))
        );
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("tree verification succeeds"));
        let stdout = String::from_utf8(read_pipe_to_end(pipes.stdout.as_mut()))
            .expect("stdout should be UTF-8");
        assert_eq!(stdout, "managed-tree");
        tree.release_exited().expect("verified tree releases");
    }

    #[cfg(unix)]
    #[test]
    fn posix_root_exit_reclaims_term_ignoring_descendant() {
        use std::{fs, thread, time::Instant};

        let marker = env_temp_marker("root-first");
        let script = format!(
            "(trap '' TERM; echo $$ > '{}'; while :; do sleep 1; done) & exit 0",
            marker.display()
        );
        let spawned = NativeManagedProcessTreeBackend
            .spawn(&ManagedProcessRequest {
                program: PathBuf::from("/bin/sh"),
                args: vec!["-c".into(), script.into()],
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Null,
            })
            .expect("root-first fixture should spawn");
        let mut tree = spawned.tree;
        let marker_deadline = Instant::now() + Duration::from_secs(3);
        while !marker.exists() && Instant::now() < marker_deadline {
            thread::sleep(Duration::from_millis(10));
        }
        assert!(marker.exists(), "descendant must start before root cleanup");
        assert!(matches!(
            tree.wait_root(Duration::from_secs(5))
                .expect("root wait succeeds"),
            ProcessWaitOutcome::Exited(_)
        ));
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("guardian verifies the entire group"));
        tree.release_exited().expect("verified tree releases");
        let _ = fs::remove_file(marker);
    }

    #[cfg(unix)]
    #[test]
    fn posix_staged_python_root_is_contained_and_released() {
        let python = std::env::var_os("SAKURA_RUNTIME_V2_TEST_PYTHON")
            .map(PathBuf::from)
            .or_else(|| {
                let candidate =
                    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../runtime/bin/python3");
                candidate.is_file().then_some(candidate)
            })
            .unwrap_or_else(|| PathBuf::from("python3"));
        let spawned = NativeManagedProcessTreeBackend
            .spawn(&ManagedProcessRequest {
                program: python,
                args: vec![
                    "-c".into(),
                    "import sys; sys.stdout.write('bundled-python'); sys.stdout.flush()".into(),
                ],
                current_directory: None,
                environment_overrides: Vec::new(),
                stdio: ProcessStdio::Piped,
            })
            .expect("staged Python root should spawn under the POSIX guardian");
        let mut tree = spawned.tree;
        let mut pipes = spawned.pipes.expect("Python spawn should be piped");
        drop(pipes.stdin);
        assert_eq!(
            tree.wait_root(Duration::from_secs(10))
                .expect("Python root wait succeeds"),
            ProcessWaitOutcome::Exited(ProcessExitStatus::Code(0))
        );
        assert!(tree
            .wait_tree_exited(Duration::from_secs(5))
            .expect("Python tree verification succeeds"));
        let output = String::from_utf8(read_pipe_to_end(pipes.stdout.as_mut()))
            .expect("Python stdout should be UTF-8");
        assert_eq!(output, "bundled-python");
        tree.release_exited().expect("Python tree releases");
    }

    #[cfg(unix)]
    fn env_temp_marker(label: &str) -> PathBuf {
        std::env::temp_dir().join(format!("sakura-wp-1p-04-{label}-{}", std::process::id()))
    }
}
