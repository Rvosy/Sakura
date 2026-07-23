use std::{ffi::OsString, fmt, fs::File, path::PathBuf, time::Duration};

#[cfg(all(test, windows))]
static LAST_ROLLED_BACK_PID: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);
#[cfg(all(test, windows))]
static PROCESS_TREE_FAILURE_TEST: std::sync::Mutex<()> = std::sync::Mutex::new(());

#[cfg(windows)]
use std::{
    ffi::OsStr,
    mem::size_of,
    os::windows::{ffi::OsStrExt, io::FromRawHandle},
    path::Path,
    thread,
    time::Instant,
};

#[cfg(windows)]
use windows::{
    core::{Error as WindowsError, PCWSTR, PWSTR},
    Win32::{
        Foundation::{
            CloseHandle, SetHandleInformation, HANDLE, HANDLE_FLAGS, HANDLE_FLAG_INHERIT,
            WAIT_OBJECT_0, WAIT_TIMEOUT,
        },
        Security::SECURITY_ATTRIBUTES,
        System::{
            JobObjects::{
                AssignProcessToJobObject, CreateJobObjectW, JobObjectBasicAccountingInformation,
                JobObjectExtendedLimitInformation, QueryInformationJobObject,
                SetInformationJobObject, TerminateJobObject,
                JOBOBJECT_BASIC_ACCOUNTING_INFORMATION, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
                JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
            },
            Pipes::CreatePipe,
            Threading::{
                CreateProcessW, GetExitCodeProcess, ResumeThread, TerminateProcess,
                WaitForSingleObject, CREATE_SUSPENDED, PROCESS_INFORMATION, STARTF_USESTDHANDLES,
                STARTUPINFOW,
            },
        },
    },
};

#[derive(Debug, Clone)]
pub struct ManagedProcessSpec {
    program: PathBuf,
    args: Vec<OsString>,
    current_directory: Option<PathBuf>,
}

impl ManagedProcessSpec {
    pub fn new(program: impl Into<PathBuf>) -> Self {
        Self {
            program: program.into(),
            args: Vec::new(),
            current_directory: None,
        }
    }

    pub fn arg(&mut self, arg: impl Into<OsString>) -> &mut Self {
        self.args.push(arg.into());
        self
    }

    pub fn current_dir(&mut self, directory: impl Into<PathBuf>) -> &mut Self {
        self.current_directory = Some(directory.into());
        self
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WaitOutcome {
    Exited(u32),
    TimedOut,
}

#[derive(Debug)]
pub enum ManagedProcessError {
    EmptyProgram,
    InvalidSpec(&'static str),
    #[cfg(not(windows))]
    UnsupportedPlatform,
    InvalidState(&'static str),
    Windows {
        operation: &'static str,
        code: i32,
    },
}

impl fmt::Display for ManagedProcessError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::EmptyProgram => write!(formatter, "managed process program is empty"),
            Self::InvalidSpec(message) => {
                write!(formatter, "invalid managed process spec: {message}")
            }
            #[cfg(not(windows))]
            Self::UnsupportedPlatform => {
                write!(
                    formatter,
                    "managed process trees are only supported on Windows"
                )
            }
            Self::InvalidState(message) => {
                write!(formatter, "invalid managed process state: {message}")
            }
            Self::Windows { operation, code } => {
                write!(formatter, "{operation} failed with Windows error {code}")
            }
        }
    }
}

impl std::error::Error for ManagedProcessError {}

pub type ManagedProcessResult<T> = Result<T, ManagedProcessError>;

#[cfg(windows)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum JobPollDecision {
    Complete,
    Continue,
    TimedOut,
}

#[cfg(windows)]
fn classify_job_poll(
    active_processes: u32,
    observed_at: Instant,
    deadline: Instant,
    allow_initial_zero_timeout_observation: bool,
) -> JobPollDecision {
    if active_processes == 0 && (observed_at <= deadline || allow_initial_zero_timeout_observation)
    {
        JobPollDecision::Complete
    } else if observed_at >= deadline {
        JobPollDecision::TimedOut
    } else {
        JobPollDecision::Continue
    }
}

#[cfg(windows)]
#[derive(Debug)]
struct OwnedHandle(HANDLE);

#[cfg(windows)]
impl OwnedHandle {
    fn new(handle: HANDLE) -> Self {
        Self(handle)
    }

    fn raw(&self) -> HANDLE {
        self.0
    }

    fn into_file(self) -> File {
        let raw = self.0 .0;
        std::mem::forget(self);
        unsafe { File::from_raw_handle(raw) }
    }
}

#[derive(Debug)]
pub struct ManagedProcessPipes {
    pub stdin: File,
    pub stdout: File,
    pub stderr: File,
}

#[cfg(windows)]
#[derive(Debug)]
struct ChildPipeSetup {
    parent_stdin: OwnedHandle,
    parent_stdout: OwnedHandle,
    parent_stderr: OwnedHandle,
    child_stdin: OwnedHandle,
    child_stdout: OwnedHandle,
    child_stderr: OwnedHandle,
}

#[cfg(windows)]
impl ChildPipeSetup {
    fn create() -> ManagedProcessResult<Self> {
        let (child_stdin, parent_stdin) = create_inherited_pipe(false)?;
        let (parent_stdout, child_stdout) = create_inherited_pipe(true)?;
        let (parent_stderr, child_stderr) = create_inherited_pipe(true)?;
        Ok(Self {
            parent_stdin,
            parent_stdout,
            parent_stderr,
            child_stdin,
            child_stdout,
            child_stderr,
        })
    }

    fn configure_startup(&self, startup: &mut STARTUPINFOW) {
        startup.dwFlags |= STARTF_USESTDHANDLES;
        startup.hStdInput = self.child_stdin.raw();
        startup.hStdOutput = self.child_stdout.raw();
        startup.hStdError = self.child_stderr.raw();
    }

    fn into_parent_pipes(self) -> ManagedProcessPipes {
        drop(self.child_stdin);
        drop(self.child_stdout);
        drop(self.child_stderr);
        ManagedProcessPipes {
            stdin: self.parent_stdin.into_file(),
            stdout: self.parent_stdout.into_file(),
            stderr: self.parent_stderr.into_file(),
        }
    }
}

#[cfg(windows)]
impl Drop for OwnedHandle {
    fn drop(&mut self) {
        let _ = unsafe { CloseHandle(self.0) };
    }
}

#[derive(Debug)]
pub struct ManagedProcessTree {
    pid: u32,
    exit_code: Option<u32>,
    #[cfg(windows)]
    job: Option<OwnedHandle>,
    #[cfg(windows)]
    process: Option<OwnedHandle>,
}

#[cfg(windows)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SpawnFailureInjection {
    None,
    Assignment,
    Resume,
}

#[cfg(windows)]
impl Drop for ManagedProcessTree {
    fn drop(&mut self) {
        // Drop is final insurance, not the normal or forced-stop API.  Closing
        // the kill-on-close Job first atomically terminates any surviving tree;
        // the retained root process handle is released afterward.
        self.job.take();
        self.process.take();
    }
}

impl ManagedProcessTree {
    #[cfg(windows)]
    pub fn spawn(spec: &ManagedProcessSpec) -> ManagedProcessResult<Self> {
        Self::spawn_internal(spec, SpawnFailureInjection::None)
    }

    #[cfg(windows)]
    pub fn spawn_piped(
        spec: &ManagedProcessSpec,
    ) -> ManagedProcessResult<(Self, ManagedProcessPipes)> {
        let (tree, pipes) = Self::spawn_internal_configured(
            spec,
            SpawnFailureInjection::None,
            Some(ChildPipeSetup::create()?),
        )?;
        Ok((tree, pipes.expect("piped spawn returns parent pipes")))
    }

    #[cfg(all(test, windows))]
    fn spawn_with_assignment_failure_for_test(
        spec: &ManagedProcessSpec,
    ) -> ManagedProcessResult<Self> {
        Self::spawn_internal(spec, SpawnFailureInjection::Assignment)
    }

    #[cfg(all(test, windows))]
    fn spawn_with_resume_failure_for_test(spec: &ManagedProcessSpec) -> ManagedProcessResult<Self> {
        Self::spawn_internal(spec, SpawnFailureInjection::Resume)
    }

    #[cfg(windows)]
    fn spawn_internal(
        spec: &ManagedProcessSpec,
        failure_injection: SpawnFailureInjection,
    ) -> ManagedProcessResult<Self> {
        Self::spawn_internal_configured(spec, failure_injection, None).map(|(tree, _)| tree)
    }

    #[cfg(windows)]
    fn spawn_internal_configured(
        spec: &ManagedProcessSpec,
        failure_injection: SpawnFailureInjection,
        pipe_setup: Option<ChildPipeSetup>,
    ) -> ManagedProcessResult<(Self, Option<ManagedProcessPipes>)> {
        validate_spec(spec)?;

        let job = OwnedHandle::new(
            unsafe { CreateJobObjectW(None, PCWSTR::null()) }
                .map_err(|error| windows_error("CreateJobObjectW", error))?,
        );
        configure_kill_on_close(&job)?;

        let application = wide_null(spec.program.as_os_str());
        let mut command_line = build_command_line(&spec.program, &spec.args);
        let mut startup = STARTUPINFOW {
            cb: size_of::<STARTUPINFOW>() as u32,
            ..Default::default()
        };
        if let Some(pipe_setup) = &pipe_setup {
            pipe_setup.configure_startup(&mut startup);
        }
        let current_directory = spec
            .current_directory
            .as_ref()
            .map(|directory| wide_null(directory.as_os_str()));
        let mut process_info = PROCESS_INFORMATION::default();
        unsafe {
            CreateProcessW(
                PCWSTR(application.as_ptr()),
                Some(PWSTR(command_line.as_mut_ptr())),
                None,
                None,
                pipe_setup.is_some(),
                CREATE_SUSPENDED,
                None,
                current_directory
                    .as_ref()
                    .map_or(PCWSTR::null(), |directory| PCWSTR(directory.as_ptr())),
                &mut startup,
                &mut process_info,
            )
        }
        .map_err(|error| windows_error("CreateProcessW", error))?;

        let process = OwnedHandle::new(process_info.hProcess);
        let thread_handle = OwnedHandle::new(process_info.hThread);
        if failure_injection == SpawnFailureInjection::Assignment {
            #[cfg(test)]
            LAST_ROLLED_BACK_PID.store(
                process_info.dwProcessId,
                std::sync::atomic::Ordering::SeqCst,
            );
            rollback_suspended_process(&process)?;
            return Err(ManagedProcessError::Windows {
                operation: "AssignProcessToJobObject (injected)",
                code: -1,
            });
        }
        if let Err(error) = unsafe { AssignProcessToJobObject(job.raw(), process.raw()) } {
            rollback_suspended_process(&process)?;
            return Err(windows_error("AssignProcessToJobObject", error));
        }
        if failure_injection == SpawnFailureInjection::Resume {
            #[cfg(test)]
            LAST_ROLLED_BACK_PID.store(
                process_info.dwProcessId,
                std::sync::atomic::Ordering::SeqCst,
            );
            rollback_assigned_tree(&job, &process)?;
            return Err(ManagedProcessError::Windows {
                operation: "ResumeThread (injected)",
                code: -1,
            });
        }
        if unsafe { ResumeThread(thread_handle.raw()) } == u32::MAX {
            let error = WindowsError::from_win32();
            rollback_assigned_tree(&job, &process)?;
            return Err(windows_error("ResumeThread", error));
        }
        drop(thread_handle);

        let pipes = pipe_setup.map(ChildPipeSetup::into_parent_pipes);
        Ok((
            Self {
                pid: process_info.dwProcessId,
                exit_code: None,
                job: Some(job),
                process: Some(process),
            },
            pipes,
        ))
    }

    #[cfg(not(windows))]
    pub fn spawn(_spec: &ManagedProcessSpec) -> ManagedProcessResult<Self> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }

    #[cfg(not(windows))]
    pub fn spawn_piped(
        _spec: &ManagedProcessSpec,
    ) -> ManagedProcessResult<(Self, ManagedProcessPipes)> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }

    pub fn pid(&self) -> u32 {
        self.pid
    }

    #[cfg(windows)]
    pub fn terminate_tree(&mut self, reason_code: u32) -> ManagedProcessResult<()> {
        let Some(job) = self.job.as_ref() else {
            return if self.exit_code.is_some() {
                Ok(())
            } else {
                Err(ManagedProcessError::InvalidState("job handle was released"))
            };
        };
        if active_processes(job)? == 0 {
            return Ok(());
        }
        unsafe { TerminateJobObject(job.raw(), reason_code) }
            .map_err(|error| windows_error("TerminateJobObject", error))
    }

    #[cfg(not(windows))]
    pub fn terminate_tree(&mut self, _reason_code: u32) -> ManagedProcessResult<()> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }

    #[cfg(windows)]
    pub fn wait(&mut self, timeout: Duration) -> ManagedProcessResult<WaitOutcome> {
        if let Some(exit_code) = self.exit_code {
            return Ok(WaitOutcome::Exited(exit_code));
        }
        let process = self
            .process
            .as_ref()
            .ok_or(ManagedProcessError::InvalidState(
                "process handle was released",
            ))?;
        match unsafe { WaitForSingleObject(process.raw(), duration_millis(timeout)) } {
            WAIT_OBJECT_0 => {
                let mut exit_code = 0;
                unsafe { GetExitCodeProcess(process.raw(), &mut exit_code) }
                    .map_err(|error| windows_error("GetExitCodeProcess", error))?;
                self.exit_code = Some(exit_code);
                Ok(WaitOutcome::Exited(exit_code))
            }
            WAIT_TIMEOUT => Ok(WaitOutcome::TimedOut),
            _ => Err(windows_error(
                "WaitForSingleObject",
                WindowsError::from_win32(),
            )),
        }
    }

    #[cfg(not(windows))]
    pub fn wait(&mut self, _timeout: Duration) -> ManagedProcessResult<WaitOutcome> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }

    #[cfg(windows)]
    pub fn verify_tree_exited(&self, timeout: Duration) -> ManagedProcessResult<bool> {
        let job = self
            .job
            .as_ref()
            .ok_or(ManagedProcessError::InvalidState("job handle was released"))?;
        wait_for_job_empty(job, timeout)
    }

    #[cfg(not(windows))]
    pub fn verify_tree_exited(&self, _timeout: Duration) -> ManagedProcessResult<bool> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }

    #[cfg(windows)]
    pub fn release_exited_handles(&mut self) -> ManagedProcessResult<()> {
        if self.process.is_none() && self.job.is_none() {
            return Ok(());
        }
        if !self.verify_tree_exited(Duration::ZERO)? {
            return Err(ManagedProcessError::InvalidState(
                "cannot release handles while the job still has active processes",
            ));
        }
        self.process.take();
        self.job.take();
        Ok(())
    }

    #[cfg(not(windows))]
    pub fn release_exited_handles(&mut self) -> ManagedProcessResult<()> {
        Err(ManagedProcessError::UnsupportedPlatform)
    }
}

#[cfg(windows)]
fn rollback_suspended_process(process: &OwnedHandle) -> ManagedProcessResult<()> {
    unsafe { TerminateProcess(process.raw(), 1) }
        .map_err(|error| windows_error("TerminateProcess during spawn rollback", error))?;
    if unsafe { WaitForSingleObject(process.raw(), 5_000) } != WAIT_OBJECT_0 {
        return Err(ManagedProcessError::InvalidState(
            "spawn rollback process did not exit before deadline",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn rollback_assigned_tree(job: &OwnedHandle, process: &OwnedHandle) -> ManagedProcessResult<()> {
    unsafe { TerminateJobObject(job.raw(), 1) }
        .map_err(|error| windows_error("TerminateJobObject during spawn rollback", error))?;
    if unsafe { WaitForSingleObject(process.raw(), 5_000) } != WAIT_OBJECT_0 {
        return Err(ManagedProcessError::InvalidState(
            "assigned spawn rollback root did not exit before deadline",
        ));
    }
    if !wait_for_job_empty(job, Duration::from_secs(5))? {
        return Err(ManagedProcessError::InvalidState(
            "assigned spawn rollback Job did not empty before deadline",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn wait_for_job_empty(job: &OwnedHandle, timeout: Duration) -> ManagedProcessResult<bool> {
    let deadline = Instant::now() + timeout;
    let mut initial_observation = true;
    loop {
        let active = active_processes(job)?;
        let observed_at = Instant::now();
        match classify_job_poll(
            active,
            observed_at,
            deadline,
            initial_observation && timeout.is_zero(),
        ) {
            JobPollDecision::Complete => return Ok(true),
            JobPollDecision::TimedOut => return Ok(false),
            JobPollDecision::Continue => {
                initial_observation = false;
                let remaining = deadline.saturating_duration_since(observed_at);
                thread::sleep(Duration::from_millis(10).min(remaining));
            }
        }
    }
}

#[cfg(windows)]
fn validate_spec(spec: &ManagedProcessSpec) -> ManagedProcessResult<()> {
    if spec.program.as_os_str().is_empty() {
        return Err(ManagedProcessError::EmptyProgram);
    }
    if spec.program.as_os_str().encode_wide().any(|unit| unit == 0) {
        return Err(ManagedProcessError::InvalidSpec(
            "program contains an embedded NUL",
        ));
    }
    if spec
        .args
        .iter()
        .any(|argument| argument.encode_wide().any(|unit| unit == 0))
    {
        return Err(ManagedProcessError::InvalidSpec(
            "argument contains an embedded NUL",
        ));
    }
    if spec
        .current_directory
        .as_ref()
        .is_some_and(|directory| directory.as_os_str().encode_wide().any(|unit| unit == 0))
    {
        return Err(ManagedProcessError::InvalidSpec(
            "current directory contains an embedded NUL",
        ));
    }
    Ok(())
}

#[cfg(windows)]
fn create_inherited_pipe(parent_reads: bool) -> ManagedProcessResult<(OwnedHandle, OwnedHandle)> {
    let attributes = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: std::ptr::null_mut(),
        bInheritHandle: true.into(),
    };
    let mut read = HANDLE::default();
    let mut write = HANDLE::default();
    unsafe { CreatePipe(&mut read, &mut write, Some(&attributes), 0) }
        .map_err(|error| windows_error("CreatePipe", error))?;
    let read = OwnedHandle::new(read);
    let write = OwnedHandle::new(write);
    let parent = if parent_reads { &read } else { &write };
    unsafe { SetHandleInformation(parent.raw(), HANDLE_FLAG_INHERIT.0, HANDLE_FLAGS(0)) }
        .map_err(|error| windows_error("SetHandleInformation", error))?;
    Ok((read, write))
}

#[cfg(windows)]
fn configure_kill_on_close(job: &OwnedHandle) -> ManagedProcessResult<()> {
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    unsafe {
        SetInformationJobObject(
            job.raw(),
            JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        )
    }
    .map_err(|error| windows_error("SetInformationJobObject", error))
}

#[cfg(windows)]
fn active_processes(job: &OwnedHandle) -> ManagedProcessResult<u32> {
    let mut accounting = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION::default();
    unsafe {
        QueryInformationJobObject(
            Some(job.raw()),
            JobObjectBasicAccountingInformation,
            (&mut accounting as *mut JOBOBJECT_BASIC_ACCOUNTING_INFORMATION).cast(),
            size_of::<JOBOBJECT_BASIC_ACCOUNTING_INFORMATION>() as u32,
            None,
        )
    }
    .map_err(|error| windows_error("QueryInformationJobObject", error))?;
    Ok(accounting.ActiveProcesses)
}

#[cfg(windows)]
fn wide_null(value: &OsStr) -> Vec<u16> {
    value.encode_wide().chain(std::iter::once(0)).collect()
}

#[cfg(windows)]
fn build_command_line(program: &Path, args: &[OsString]) -> Vec<u16> {
    let mut command_line = quote_windows_argument(program.as_os_str());
    for arg in args {
        command_line.push(' ' as u16);
        command_line.extend(quote_windows_argument(arg));
    }
    command_line.push(0);
    command_line
}

#[cfg(windows)]
fn quote_windows_argument(argument: &OsStr) -> Vec<u16> {
    let units = argument.encode_wide().collect::<Vec<_>>();
    let needs_quotes = units.is_empty()
        || units
            .iter()
            .any(|unit| *unit == b' ' as u16 || *unit == b'\t' as u16 || *unit == b'"' as u16);
    if !needs_quotes {
        return units;
    }

    let mut quoted = vec![b'"' as u16];
    let mut backslashes = 0;
    for unit in units {
        if unit == b'\\' as u16 {
            backslashes += 1;
        } else if unit == b'"' as u16 {
            quoted.extend(std::iter::repeat_n(b'\\' as u16, backslashes * 2 + 1));
            quoted.push(unit);
            backslashes = 0;
        } else {
            quoted.extend(std::iter::repeat_n(b'\\' as u16, backslashes));
            quoted.push(unit);
            backslashes = 0;
        }
    }
    quoted.extend(std::iter::repeat_n(b'\\' as u16, backslashes * 2));
    quoted.push(b'"' as u16);
    quoted
}

#[cfg(windows)]
fn duration_millis(duration: Duration) -> u32 {
    duration.as_millis().min(u32::MAX as u128) as u32
}

#[cfg(windows)]
fn windows_error(operation: &'static str, error: WindowsError) -> ManagedProcessError {
    ManagedProcessError::Windows {
        operation,
        code: error.code().0,
    }
}

#[cfg(all(test, windows))]
mod tests {
    use std::{
        fs::{self, OpenOptions},
        io::Write,
        process::{self, Command},
        thread,
        time::{Duration, Instant},
    };

    use super::{
        classify_job_poll, JobPollDecision, ManagedProcessSpec, ManagedProcessTree, WaitOutcome,
    };
    use windows::core::PCWSTR;
    use windows::Win32::{
        Foundation::{CloseHandle, WAIT_OBJECT_0},
        System::{
            JobObjects::{AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob},
            Threading::{
                GetCurrentProcess, GetProcessHandleCount, OpenProcess, WaitForSingleObject,
                PROCESS_SYNCHRONIZE,
            },
        },
    };

    fn fixture_spec(name: &str) -> ManagedProcessSpec {
        let mut spec = ManagedProcessSpec::new(
            std::env::current_exe().expect("current Rust test executable should resolve"),
        );
        spec.arg("--ignored")
            .arg("--exact")
            .arg(format!("managed_process_tree::tests::{name}"))
            .arg("--nocapture");
        spec
    }

    #[test]
    fn job_poll_rejects_an_empty_observation_made_after_the_deadline() {
        let deadline = Instant::now();

        assert_eq!(
            classify_job_poll(0, deadline + Duration::from_millis(1), deadline, false),
            JobPollDecision::TimedOut
        );
        assert_eq!(
            classify_job_poll(0, deadline, deadline, false),
            JobPollDecision::Complete
        );
        assert_eq!(
            classify_job_poll(1, deadline - Duration::from_millis(1), deadline, false),
            JobPollDecision::Continue
        );
        assert_eq!(
            classify_job_poll(0, deadline + Duration::from_millis(1), deadline, true),
            JobPollDecision::Complete,
            "the initial zero-timeout observation must still support release_exited_handles"
        );
    }

    fn tree_fixture_dir(root_pid: u32) -> std::path::PathBuf {
        std::env::temp_dir().join(format!("sakura-runtime-v2-wp-1b-01-{root_pid}"))
    }

    fn append_fixture_pid() {
        let directory = std::env::var_os("SAKURA_WP_1B_01_TREE_DIR")
            .map(std::path::PathBuf::from)
            .expect("fixture tree directory should be inherited");
        let mut marker = OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join("descendant-pids.txt"))
            .expect("descendant marker should open");
        writeln!(marker, "{}", process::id()).expect("descendant pid should be recorded");
        marker.flush().expect("descendant pid marker should flush");
    }

    fn process_is_not_running(pid: u32) -> bool {
        let Ok(handle) = (unsafe { OpenProcess(PROCESS_SYNCHRONIZE, false, pid) }) else {
            return true;
        };
        let exited = unsafe { WaitForSingleObject(handle, 0) } == WAIT_OBJECT_0;
        let _ = unsafe { CloseHandle(handle) };
        exited
    }

    fn wait_for_fixture_process(
        child: &mut process::Child,
        timeout: Duration,
    ) -> process::ExitStatus {
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(status) = child.try_wait().expect("fixture status should query") {
                return status;
            }
            if Instant::now() >= deadline {
                child.kill().expect("timed-out fixture should terminate");
                let _ = child.wait();
                panic!("fixture process exceeded its deadline");
            }
            thread::sleep(Duration::from_millis(20));
        }
    }

    #[test]
    fn normal_root_exit_is_observed_and_releases_exited_handles_explicitly() {
        let mut tree = ManagedProcessTree::spawn(&fixture_spec("fixture_exit_23"))
            .expect("managed process should spawn");

        assert_ne!(tree.pid(), 0);
        assert_eq!(
            tree.wait(Duration::from_secs(3))
                .expect("root wait should succeed"),
            WaitOutcome::Exited(23)
        );
        assert!(tree
            .verify_tree_exited(Duration::from_secs(1))
            .expect("job verification should succeed"));
        tree.release_exited_handles()
            .expect("exited handles should release");
        tree.release_exited_handles()
            .expect("repeated exited-handle release should be idempotent");
    }

    #[test]
    fn timeout_and_forced_tree_termination_have_distinct_explicit_semantics() {
        let mut tree = ManagedProcessTree::spawn(&fixture_spec("fixture_holds"))
            .expect("managed process should spawn");

        assert_eq!(
            tree.wait(Duration::from_millis(50))
                .expect("bounded wait should succeed"),
            WaitOutcome::TimedOut
        );
        assert!(tree.release_exited_handles().is_err());

        tree.terminate_tree(71)
            .expect("forced tree termination should succeed");
        assert_eq!(
            tree.wait(Duration::from_secs(3))
                .expect("terminated root should exit"),
            WaitOutcome::Exited(71)
        );
        assert!(tree
            .verify_tree_exited(Duration::from_secs(1))
            .expect("terminated job should become empty"));
        tree.terminate_tree(71)
            .expect("repeated tree termination should be idempotent");
        tree.release_exited_handles()
            .expect("terminated handles should release explicitly");
    }

    #[test]
    fn forced_termination_reclaims_one_level_and_multi_level_descendants() {
        let mut tree = ManagedProcessTree::spawn(&fixture_spec("fixture_spawns_descendants"))
            .expect("managed process should spawn");
        let fixture_dir = tree_fixture_dir(tree.pid());
        let marker = fixture_dir.join("descendant-pids.txt");
        let deadline = Instant::now() + Duration::from_secs(5);
        let descendant_pids = loop {
            let pids = fs::read_to_string(&marker)
                .unwrap_or_default()
                .lines()
                .filter_map(|line| line.parse::<u32>().ok())
                .collect::<Vec<_>>();
            if pids.len() == 2 {
                break pids;
            }
            assert!(
                Instant::now() < deadline,
                "two descendants should report readiness"
            );
            thread::sleep(Duration::from_millis(20));
        };
        assert!(descendant_pids.iter().all(|pid| *pid != tree.pid()));
        assert!(!tree.verify_tree_exited(Duration::ZERO).unwrap());

        tree.terminate_tree(72).unwrap();
        assert_eq!(
            tree.wait(Duration::from_secs(3)).unwrap(),
            WaitOutcome::Exited(72)
        );
        assert!(tree.verify_tree_exited(Duration::from_secs(1)).unwrap());
        tree.release_exited_handles().unwrap();
        fs::remove_dir_all(&fixture_dir).expect("isolated fixture directory should remove");
    }

    #[test]
    fn root_exit_does_not_hide_a_surviving_descendant() {
        let mut tree =
            ManagedProcessTree::spawn(&fixture_spec("fixture_root_exits_with_descendant_holding"))
                .expect("managed process should spawn");
        let fixture_dir = tree_fixture_dir(tree.pid());

        assert_eq!(
            tree.wait(Duration::from_secs(3)).unwrap(),
            WaitOutcome::Exited(44)
        );
        let marker = fixture_dir.join("descendant-pids.txt");
        let deadline = Instant::now() + Duration::from_secs(3);
        while fs::read_to_string(&marker)
            .unwrap_or_default()
            .trim()
            .is_empty()
        {
            assert!(
                Instant::now() < deadline,
                "surviving descendant should report readiness"
            );
            thread::sleep(Duration::from_millis(20));
        }
        assert!(!tree.verify_tree_exited(Duration::ZERO).unwrap());

        tree.terminate_tree(73).unwrap();
        assert!(tree.verify_tree_exited(Duration::from_secs(1)).unwrap());
        tree.release_exited_handles().unwrap();
        fs::remove_dir_all(&fixture_dir).expect("isolated fixture directory should remove");
    }

    #[test]
    fn assignment_failure_terminates_the_suspended_unmanaged_process() {
        let _serial = super::PROCESS_TREE_FAILURE_TEST.lock().unwrap();
        let error = ManagedProcessTree::spawn_with_assignment_failure_for_test(&fixture_spec(
            "fixture_holds",
        ))
        .expect_err("injected Job assignment should fail safely");
        assert!(error.to_string().contains("AssignProcessToJobObject"));

        let rolled_back_pid = super::LAST_ROLLED_BACK_PID.load(std::sync::atomic::Ordering::SeqCst);
        assert_ne!(rolled_back_pid, 0);
        assert!(process_is_not_running(rolled_back_pid));
    }

    #[test]
    fn resume_failure_terminates_the_already_assigned_job_tree() {
        let _serial = super::PROCESS_TREE_FAILURE_TEST.lock().unwrap();
        let error =
            ManagedProcessTree::spawn_with_resume_failure_for_test(&fixture_spec("fixture_holds"))
                .expect_err("injected thread resume should fail safely");
        assert!(error.to_string().contains("ResumeThread"));

        let rolled_back_pid = super::LAST_ROLLED_BACK_PID.load(std::sync::atomic::Ordering::SeqCst);
        assert_ne!(rolled_back_pid, 0);
        assert!(process_is_not_running(rolled_back_pid));
    }

    #[test]
    fn embedded_nul_in_program_or_arguments_is_rejected_before_win32_spawn() {
        let mut nul_argument = fixture_spec("fixture_exit_23");
        nul_argument.arg("before\0after");
        assert!(super::validate_spec(&nul_argument).is_err());

        let nul_program = ManagedProcessSpec::new(std::path::PathBuf::from("bad\0program.exe"));
        assert!(super::validate_spec(&nul_program).is_err());
    }

    #[test]
    fn windows_command_line_quoting_covers_spaces_quotes_and_trailing_backslashes() {
        fn quoted(value: &str) -> String {
            String::from_utf16(&super::quote_windows_argument(std::ffi::OsStr::new(value)))
                .expect("quoted argument should remain UTF-16")
        }

        assert_eq!(quoted("simple"), "simple");
        assert_eq!(quoted("two words"), "\"two words\"");
        assert_eq!(quoted("say\"hi"), "\"say\\\"hi\"");
        assert_eq!(
            quoted("C:\\path with space\\"),
            "\"C:\\path with space\\\\\""
        );
        assert_eq!(quoted(""), "\"\"");
    }

    #[test]
    fn caller_already_in_a_windows_job_can_create_an_independent_managed_tree() {
        let mut child = Command::new(
            std::env::current_exe().expect("current Rust test executable should resolve"),
        )
        .args([
            "--ignored",
            "--exact",
            "managed_process_tree::tests::fixture_nested_job_parent",
            "--nocapture",
        ])
        .spawn()
        .expect("nested Job validation fixture should spawn");

        let status = wait_for_fixture_process(&mut child, Duration::from_secs(5));
        assert!(
            status.success(),
            "nested Job validation fixture failed: {status}"
        );
    }

    #[test]
    fn drop_is_final_insurance_for_a_live_multi_level_tree() {
        let tree = ManagedProcessTree::spawn(&fixture_spec("fixture_spawns_descendants"))
            .expect("managed process should spawn");
        let root_pid = tree.pid();
        let fixture_dir = tree_fixture_dir(root_pid);
        let marker = fixture_dir.join("descendant-pids.txt");
        let deadline = Instant::now() + Duration::from_secs(5);
        let descendant_pids = loop {
            let pids = fs::read_to_string(&marker)
                .unwrap_or_default()
                .lines()
                .filter_map(|line| line.parse::<u32>().ok())
                .collect::<Vec<_>>();
            if pids.len() == 2 {
                break pids;
            }
            assert!(
                Instant::now() < deadline,
                "two descendants should report readiness"
            );
            thread::sleep(Duration::from_millis(20));
        };

        drop(tree);
        let deadline = Instant::now() + Duration::from_secs(3);
        loop {
            let all_exited = process_is_not_running(root_pid)
                && descendant_pids
                    .iter()
                    .all(|pid| process_is_not_running(*pid));
            if all_exited {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "Drop should reclaim the complete Job tree"
            );
            thread::sleep(Duration::from_millis(20));
        }
        fs::remove_dir_all(&fixture_dir).expect("isolated fixture directory should remove");
    }

    #[test]
    fn spawn_failures_and_repeated_release_do_not_leak_process_handles() {
        fn current_handle_count() -> u32 {
            let mut count = 0;
            unsafe { GetProcessHandleCount(GetCurrentProcess(), &mut count) }
                .expect("current process handle count should query");
            count
        }

        let missing = ManagedProcessSpec::new(
            std::env::temp_dir().join("sakura-wp-1b-01-definitely-missing.exe"),
        );
        let mut warmup = ManagedProcessTree::spawn(&fixture_spec("fixture_exit_23"))
            .expect("success path should warm up");
        assert_eq!(
            warmup.wait(Duration::from_secs(3)).unwrap(),
            WaitOutcome::Exited(23)
        );
        warmup.release_exited_handles().unwrap();
        let _ = ManagedProcessTree::spawn(&missing)
            .expect_err("failure path should warm up without creating a process");

        let before = current_handle_count();
        for _ in 0..12 {
            let mut tree = ManagedProcessTree::spawn(&fixture_spec("fixture_exit_23"))
                .expect("managed process should spawn");
            assert_eq!(
                tree.wait(Duration::from_secs(3)).unwrap(),
                WaitOutcome::Exited(23)
            );
            assert!(tree.verify_tree_exited(Duration::from_secs(1)).unwrap());
            tree.release_exited_handles().unwrap();
            tree.release_exited_handles().unwrap();
        }
        for _ in 0..12 {
            let error = ManagedProcessTree::spawn(&missing)
                .expect_err("missing program should fail before a process exists");
            assert!(error.to_string().contains("CreateProcessW"));
        }
        let after = current_handle_count();
        assert!(
            after <= before + 2,
            "managed process handles leaked: before={before}, after={after}"
        );
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_exit_23() {
        process::exit(23);
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_holds() {
        thread::sleep(Duration::from_secs(60));
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_spawns_descendants() {
        let directory = tree_fixture_dir(process::id());
        fs::create_dir_all(&directory).expect("fixture directory should create");
        fs::write(directory.join("descendant-pids.txt"), "")
            .expect("descendant marker should initialize");
        let mut child = Command::new(
            std::env::current_exe().expect("current Rust test executable should resolve"),
        );
        child
            .args([
                "--ignored",
                "--exact",
                "managed_process_tree::tests::fixture_spawns_grandchild",
                "--nocapture",
            ])
            .env("SAKURA_WP_1B_01_TREE_DIR", &directory)
            .spawn()
            .expect("first descendant should spawn");
        thread::sleep(Duration::from_secs(60));
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_root_exits_with_descendant_holding() {
        let directory = tree_fixture_dir(process::id());
        fs::create_dir_all(&directory).expect("fixture directory should create");
        fs::write(directory.join("descendant-pids.txt"), "")
            .expect("descendant marker should initialize");
        Command::new(std::env::current_exe().expect("current Rust test executable should resolve"))
            .args([
                "--ignored",
                "--exact",
                "managed_process_tree::tests::fixture_leaf_holds",
                "--nocapture",
            ])
            .env("SAKURA_WP_1B_01_TREE_DIR", &directory)
            .spawn()
            .expect("surviving descendant should spawn");
        process::exit(44);
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_spawns_grandchild() {
        append_fixture_pid();
        let directory = std::env::var_os("SAKURA_WP_1B_01_TREE_DIR")
            .expect("fixture tree directory should be inherited");
        let mut grandchild = Command::new(
            std::env::current_exe().expect("current Rust test executable should resolve"),
        );
        grandchild
            .args([
                "--ignored",
                "--exact",
                "managed_process_tree::tests::fixture_leaf_holds",
                "--nocapture",
            ])
            .env("SAKURA_WP_1B_01_TREE_DIR", directory)
            .spawn()
            .expect("second descendant should spawn");
        thread::sleep(Duration::from_secs(60));
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_leaf_holds() {
        append_fixture_pid();
        thread::sleep(Duration::from_secs(60));
    }

    #[test]
    #[ignore = "test-process fixture; launched by ManagedProcessTree tests"]
    fn fixture_nested_job_parent() {
        let outer_job = unsafe { CreateJobObjectW(None, PCWSTR::null()) }
            .expect("outer validation Job should create");
        unsafe { AssignProcessToJobObject(outer_job, GetCurrentProcess()) }
            .expect("fixture should enter the outer validation Job");
        let mut is_in_job = windows::core::BOOL::default();
        unsafe { IsProcessInJob(GetCurrentProcess(), None, &mut is_in_job) }
            .expect("outer Job membership should query");
        assert!(is_in_job.as_bool());

        let mut tree = ManagedProcessTree::spawn(&fixture_spec("fixture_exit_23"))
            .expect("nested managed Job should spawn");
        assert_eq!(
            tree.wait(Duration::from_secs(3)).unwrap(),
            WaitOutcome::Exited(23)
        );
        assert!(tree.verify_tree_exited(Duration::from_secs(1)).unwrap());
        tree.release_exited_handles().unwrap();
        unsafe { CloseHandle(outer_job) }.expect("outer validation Job should close");
    }
}
