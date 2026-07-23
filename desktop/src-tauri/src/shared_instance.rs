pub const SHARED_MUTEX_NAME: &str = r"Local\SakuraDesktop.SharedUserData.v1";
#[cfg(unix)]
pub const POSIX_LOCK_DIRECTORY: &str = "sakura";
#[cfg(unix)]
pub const POSIX_LOCK_FILE_NAME: &str = "sakura.desktop.shared-user-data.v1.lock";

use crate::platform::{
    InstanceLockAcquire, InstanceLockBackend, InstanceLockLease, PlatformError,
    PlatformErrorCategory, PlatformResult, PlatformService, RetryAdvice, SHARED_INSTANCE_ID,
};

#[cfg(windows)]
use windows::{
    core::PCWSTR,
    Win32::{
        Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE},
        System::Threading::{CreateMutexW, ReleaseMutex},
    },
};

#[cfg(unix)]
use std::{
    env,
    ffi::OsString,
    fs::{self, File, OpenOptions, Permissions},
    os::{
        fd::AsRawFd,
        unix::{fs::MetadataExt, fs::OpenOptionsExt, fs::PermissionsExt},
    },
    path::{Path, PathBuf},
};

#[derive(Debug)]
pub enum AcquireOutcome {
    Acquired(SharedInstanceGuard),
    AlreadyRunning,
    Fatal(u32),
}

#[derive(Debug)]
pub struct SharedInstanceGuard {
    #[cfg(windows)]
    handle: HANDLE,
    #[cfg(unix)]
    file: File,
    #[cfg(unix)]
    lock_path: PathBuf,
}

// Win32 kernel handles are process-wide values and may be released from a
// different thread than the one that acquired them. SharedInstanceGuard owns
// the handle exclusively, so transferring the guard cannot create aliasing.
#[cfg(windows)]
unsafe impl Send for SharedInstanceGuard {}

impl SharedInstanceGuard {
    #[cfg(windows)]
    pub fn acquire() -> AcquireOutcome {
        let wide_name = SHARED_MUTEX_NAME
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let handle = match unsafe { CreateMutexW(None, true, PCWSTR(wide_name.as_ptr())) } {
            Ok(handle) => handle,
            Err(error) => return AcquireOutcome::Fatal(error.code().0 as u32),
        };
        let error = unsafe { GetLastError() };
        if error == ERROR_ALREADY_EXISTS {
            let _ = unsafe { CloseHandle(handle) };
            return AcquireOutcome::AlreadyRunning;
        }
        AcquireOutcome::Acquired(Self { handle })
    }

    #[cfg(unix)]
    pub fn acquire() -> AcquireOutcome {
        let path = match resolve_posix_lock_path() {
            Ok(path) => path,
            Err(code) => return AcquireOutcome::Fatal(code),
        };
        acquire_posix_path(&path)
    }

    #[cfg(not(any(windows, unix)))]
    pub fn acquire() -> AcquireOutcome {
        AcquireOutcome::Fatal(1)
    }

    #[cfg(unix)]
    #[allow(dead_code)]
    pub fn lock_path(&self) -> &Path {
        &self.lock_path
    }
}

impl InstanceLockLease for SharedInstanceGuard {}

#[cfg(windows)]
impl Drop for SharedInstanceGuard {
    fn drop(&mut self) {
        let _ = unsafe { ReleaseMutex(self.handle) };
        let _ = unsafe { CloseHandle(self.handle) };
    }
}

#[cfg(unix)]
impl Drop for SharedInstanceGuard {
    fn drop(&mut self) {
        let _ = unsafe { libc::flock(self.file.as_raw_fd(), libc::LOCK_UN) };
    }
}

#[derive(Debug, Default)]
pub struct NativeInstanceLockBackend;

impl InstanceLockBackend for NativeInstanceLockBackend {
    fn acquire(&self, application_id: &str) -> PlatformResult<InstanceLockAcquire> {
        if application_id != SHARED_INSTANCE_ID {
            return Err(PlatformError::new(
                PlatformService::InstanceLock,
                PlatformErrorCategory::InvalidInput,
                "acquire",
                RetryAdvice::Never,
                "application lock identity does not match the frozen shared identity",
            ));
        }

        match SharedInstanceGuard::acquire() {
            AcquireOutcome::Acquired(guard) => Ok(InstanceLockAcquire::Acquired(Box::new(guard))),
            AcquireOutcome::AlreadyRunning => Ok(InstanceLockAcquire::AlreadyRunning),
            AcquireOutcome::Fatal(code) => Err(native_lock_error(code)),
        }
    }
}

fn native_lock_error(code: u32) -> PlatformError {
    #[cfg(windows)]
    let (category, retry, namespace) = if code == 5 {
        (
            PlatformErrorCategory::PermissionDenied,
            RetryAdvice::AfterUserAction,
            "win32",
        )
    } else {
        (
            PlatformErrorCategory::NativeFailure,
            RetryAdvice::AfterExternalChange,
            "win32",
        )
    };

    #[cfg(unix)]
    let (category, retry, namespace) = match code as i32 {
        libc::EACCES | libc::EPERM => (
            PlatformErrorCategory::PermissionDenied,
            RetryAdvice::AfterUserAction,
            "errno",
        ),
        libc::ENOENT | libc::ENOTSUP => (
            PlatformErrorCategory::UnsupportedEnvironment,
            RetryAdvice::AfterUserAction,
            "errno",
        ),
        _ => (
            PlatformErrorCategory::NativeFailure,
            RetryAdvice::AfterExternalChange,
            "errno",
        ),
    };

    #[cfg(not(any(windows, unix)))]
    let (category, retry, namespace) = (
        PlatformErrorCategory::UnsupportedEnvironment,
        RetryAdvice::Never,
        "native",
    );

    PlatformError::new(
        PlatformService::InstanceLock,
        category,
        "acquire",
        retry,
        "failed to acquire the shared desktop instance lock",
    )
    .with_native_code(namespace, i64::from(code))
}

#[cfg(unix)]
#[derive(Clone, Copy)]
enum PosixTarget {
    MacOs,
    Linux,
}

#[cfg(unix)]
fn required_absolute_root<F>(get: &F, name: &str) -> Result<Option<PathBuf>, u32>
where
    F: Fn(&str) -> Option<OsString>,
{
    let Some(raw) = get(name) else {
        return Ok(None);
    };
    if raw.is_empty() {
        return Ok(None);
    }
    let root = PathBuf::from(raw);
    if !root.is_absolute() {
        return Err(libc::EINVAL as u32);
    }
    Ok(Some(root))
}

#[cfg(unix)]
fn resolve_posix_lock_path_with<F>(target: PosixTarget, get: F) -> Result<PathBuf, u32>
where
    F: Fn(&str) -> Option<OsString>,
{
    let root = match target {
        PosixTarget::MacOs => match required_absolute_root(&get, "TMPDIR")? {
            Some(root) => root,
            None => required_absolute_root(&get, "HOME")?
                .map(|home| home.join("Library").join("Caches"))
                .ok_or(libc::ENOENT as u32)?,
        },
        PosixTarget::Linux => {
            if let Some(root) = required_absolute_root(&get, "XDG_RUNTIME_DIR")? {
                root
            } else if let Some(root) = required_absolute_root(&get, "XDG_STATE_HOME")? {
                root
            } else {
                required_absolute_root(&get, "HOME")?
                    .map(|home| home.join(".local").join("state"))
                    .ok_or(libc::ENOENT as u32)?
            }
        }
    };

    Ok(root.join(POSIX_LOCK_DIRECTORY).join(POSIX_LOCK_FILE_NAME))
}

#[cfg(unix)]
fn resolve_posix_lock_path() -> Result<PathBuf, u32> {
    #[cfg(target_os = "macos")]
    let target = PosixTarget::MacOs;
    #[cfg(target_os = "linux")]
    let target = PosixTarget::Linux;
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    return Err(libc::ENOTSUP as u32);

    resolve_posix_lock_path_with(target, env::var_os)
}

#[cfg(unix)]
fn acquire_posix_path(requested_path: &Path) -> AcquireOutcome {
    let Some(parent) = requested_path.parent() else {
        return AcquireOutcome::Fatal(libc::EINVAL as u32);
    };
    if let Err(error) = fs::create_dir_all(parent) {
        return AcquireOutcome::Fatal(os_error_code(&error));
    }
    let canonical_parent = match fs::canonicalize(parent) {
        Ok(path) => path,
        Err(error) => return AcquireOutcome::Fatal(os_error_code(&error)),
    };
    let parent_metadata = match fs::metadata(&canonical_parent) {
        Ok(metadata) => metadata,
        Err(error) => return AcquireOutcome::Fatal(os_error_code(&error)),
    };
    if !parent_metadata.is_dir() || parent_metadata.uid() != unsafe { libc::geteuid() } {
        return AcquireOutcome::Fatal(libc::EPERM as u32);
    }
    if let Err(error) = fs::set_permissions(&canonical_parent, Permissions::from_mode(0o700)) {
        return AcquireOutcome::Fatal(os_error_code(&error));
    }
    let lock_path = canonical_parent.join(POSIX_LOCK_FILE_NAME);
    let file = match OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(&lock_path)
    {
        Ok(file) => file,
        Err(error) => return AcquireOutcome::Fatal(os_error_code(&error)),
    };

    let fd = file.as_raw_fd();
    let mut metadata = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, metadata.as_mut_ptr()) } != 0 {
        return AcquireOutcome::Fatal(last_errno());
    }
    let metadata = unsafe { metadata.assume_init() };
    if metadata.st_mode & libc::S_IFMT != libc::S_IFREG
        || metadata.st_nlink != 1
        || metadata.st_uid != unsafe { libc::geteuid() }
    {
        return AcquireOutcome::Fatal(libc::EPERM as u32);
    }
    if unsafe { libc::fchmod(fd, 0o600) } != 0 {
        return AcquireOutcome::Fatal(last_errno());
    }
    if unsafe { libc::flock(fd, libc::LOCK_EX | libc::LOCK_NB) } != 0 {
        let code = last_errno();
        if code == libc::EACCES as u32 || code == libc::EAGAIN as u32 {
            return AcquireOutcome::AlreadyRunning;
        }
        return AcquireOutcome::Fatal(code);
    }

    AcquireOutcome::Acquired(SharedInstanceGuard { file, lock_path })
}

#[cfg(unix)]
fn last_errno() -> u32 {
    std::io::Error::last_os_error()
        .raw_os_error()
        .unwrap_or(libc::EIO) as u32
}

#[cfg(unix)]
fn os_error_code(error: &std::io::Error) -> u32 {
    error.raw_os_error().unwrap_or(libc::EIO) as u32
}

#[cfg(all(test, windows))]
mod windows_tests {
    use super::*;

    use std::sync::Mutex;

    use windows::{
        core::PCWSTR,
        Win32::{Foundation::CloseHandle, System::Threading::CreateEventW},
    };

    static KERNEL_OBJECT_TEST: Mutex<()> = Mutex::new(());

    #[test]
    fn uses_the_frozen_shared_user_data_object_name() {
        assert_eq!(SHARED_MUTEX_NAME, r"Local\SakuraDesktop.SharedUserData.v1");
    }

    #[test]
    fn a_second_guard_conflicts_until_the_first_is_dropped() {
        let _serial = KERNEL_OBJECT_TEST.lock().expect("test mutex should lock");
        let first = match SharedInstanceGuard::acquire() {
            AcquireOutcome::Acquired(guard) => guard,
            other => panic!("first acquisition should succeed, got {other:?}"),
        };
        assert!(matches!(
            SharedInstanceGuard::acquire(),
            AcquireOutcome::AlreadyRunning
        ));
        drop(first);
        assert!(matches!(
            SharedInstanceGuard::acquire(),
            AcquireOutcome::Acquired(_)
        ));
    }

    #[test]
    fn a_same_name_non_mutex_kernel_object_is_fatal_not_a_conflict() {
        let _serial = KERNEL_OBJECT_TEST.lock().expect("test mutex should lock");
        let wide_name = SHARED_MUTEX_NAME
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>();
        let event = unsafe { CreateEventW(None, true, false, PCWSTR(wide_name.as_ptr())) }
            .expect("same-name event should be created");
        let outcome = SharedInstanceGuard::acquire();
        unsafe { CloseHandle(event).expect("event handle should close") };

        assert!(matches!(outcome, AcquireOutcome::Fatal(code) if code != 0));
    }

    #[test]
    fn backend_rejects_an_identity_other_than_the_frozen_one() {
        let error = match NativeInstanceLockBackend.acquire("different.identity") {
            Err(error) => error,
            Ok(_) => panic!("identity drift must fail closed"),
        };
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
    }
}

#[cfg(all(test, unix))]
mod unix_tests {
    use super::*;

    use std::{
        collections::BTreeMap,
        io::{BufRead, BufReader, Write},
        process::{Child, Command, Stdio},
        time::{SystemTime, UNIX_EPOCH},
    };

    fn environment(entries: &[(&str, &str)]) -> impl Fn(&str) -> Option<OsString> {
        let values = entries
            .iter()
            .map(|(name, value)| ((*name).to_owned(), OsString::from(value)))
            .collect::<BTreeMap<_, _>>();
        move |name| values.get(name).cloned()
    }

    fn temporary_lock_path(label: &str) -> PathBuf {
        let nonce = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("clock should follow epoch")
            .as_nanos();
        env::temp_dir()
            .join(format!(
                "sakura-wp-1p-03-{}-{label}-{nonce}",
                std::process::id()
            ))
            .join(POSIX_LOCK_DIRECTORY)
            .join(POSIX_LOCK_FILE_NAME)
    }

    fn repository_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("..")
            .join("..")
    }

    fn python_probe(root: &Path, mode: &str) -> Command {
        let mut command = Command::new("python");
        command
            .arg("tests/fixtures/runtime_v2/wp_1p_03/python_lock_probe.py")
            .arg(mode)
            .current_dir(repository_root())
            .env("HOME", root);
        if cfg!(target_os = "macos") {
            command.env("TMPDIR", root);
        } else {
            command.env("XDG_RUNTIME_DIR", root);
        }
        command
    }

    fn rust_probe(root: &Path) -> Command {
        let mut command = Command::new(env::current_exe().expect("test executable should resolve"));
        command
            .arg("shared_instance::unix_tests::fixture_rust_lock_holder")
            .arg("--exact")
            .arg("--ignored")
            .arg("--nocapture")
            .current_dir(repository_root())
            .env("HOME", root);
        if cfg!(target_os = "macos") {
            command.env("TMPDIR", root);
        } else {
            command.env("XDG_RUNTIME_DIR", root);
        }
        command
    }

    fn spawn_holding_python(root: &Path) -> Child {
        let mut child = python_probe(root, "hold")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("Python lock probe should start");
        let line = {
            let stdout = child.stdout.as_mut().expect("probe stdout should be piped");
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .expect("probe should report acquisition");
            line
        };
        assert_eq!(line.trim(), "acquired");
        child
    }

    fn spawn_holding_rust(root: &Path) -> Child {
        let mut child = rust_probe(root)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .expect("Rust lock probe should start");
        let stdout = child.stdout.take().expect("probe stdout should be piped");
        let mut reader = BufReader::new(stdout);
        let mut acquired = false;
        for _ in 0..16 {
            let mut line = String::new();
            if reader
                .read_line(&mut line)
                .expect("probe output should be readable")
                == 0
            {
                break;
            }
            if line.trim() == "sakura-rust-lock-acquired" {
                acquired = true;
                break;
            }
        }
        assert!(acquired, "Rust probe should report acquisition");
        child
    }

    #[test]
    #[ignore = "test-process fixture; launched by the POSIX crash-release test"]
    fn fixture_rust_lock_holder() {
        let _guard = match SharedInstanceGuard::acquire() {
            AcquireOutcome::Acquired(guard) => guard,
            other => panic!("Rust fixture should acquire, got {other:?}"),
        };
        println!("sakura-rust-lock-acquired");
        std::io::stdout()
            .flush()
            .expect("probe stdout should flush");
        let mut release = String::new();
        std::io::stdin()
            .read_line(&mut release)
            .expect("probe stdin should be readable");
    }

    #[test]
    fn linux_and_macos_paths_match_the_python_golden_contract() {
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::Linux,
                environment(&[
                    ("XDG_RUNTIME_DIR", "/run/user/1000"),
                    ("HOME", "/home/user"),
                ]),
            )
            .expect("linux path should resolve"),
            PathBuf::from("/run/user/1000/sakura/sakura.desktop.shared-user-data.v1.lock")
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::MacOs,
                environment(&[("TMPDIR", "/private/tmp/user"), ("HOME", "/Users/user")]),
            )
            .expect("macOS path should resolve"),
            PathBuf::from("/private/tmp/user/sakura/sakura.desktop.shared-user-data.v1.lock")
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::Linux,
                environment(&[
                    ("XDG_STATE_HOME", "/home/user/.state"),
                    ("HOME", "/home/user"),
                ]),
            )
            .expect("linux state fallback should resolve"),
            PathBuf::from("/home/user/.state/sakura/sakura.desktop.shared-user-data.v1.lock")
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::Linux,
                environment(&[("HOME", "/home/user")]),
            )
            .expect("linux home fallback should resolve"),
            PathBuf::from("/home/user/.local/state/sakura/sakura.desktop.shared-user-data.v1.lock")
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::MacOs,
                environment(&[("HOME", "/Users/user")]),
            )
            .expect("macOS home fallback should resolve"),
            PathBuf::from(
                "/Users/user/Library/Caches/sakura/sakura.desktop.shared-user-data.v1.lock"
            )
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::Linux,
                environment(&[("XDG_RUNTIME_DIR", "relative"), ("HOME", "/home/user")]),
            ),
            Err(libc::EINVAL as u32)
        );
        assert_eq!(
            resolve_posix_lock_path_with(
                PosixTarget::Linux,
                environment(&[("XDG_RUNTIME_DIR", "   "), ("HOME", "/home/user")]),
            ),
            Err(libc::EINVAL as u32)
        );
    }

    #[test]
    fn a_second_guard_conflicts_and_an_unlocked_existing_file_does_not() {
        let path = temporary_lock_path("conflict");
        let first = match acquire_posix_path(&path) {
            AcquireOutcome::Acquired(guard) => guard,
            other => panic!("first acquisition should succeed, got {other:?}"),
        };
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::AlreadyRunning
        ));
        let canonical_path = first.lock_path().to_owned();
        drop(first);
        assert!(canonical_path.exists(), "ordinary lock file should remain");
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::Acquired(_)
        ));
        let _ = fs::remove_dir_all(
            path.parent()
                .and_then(Path::parent)
                .expect("test path should have a private root"),
        );
    }

    #[test]
    fn posix_lock_path_is_private_and_rejects_multiple_hard_links() {
        let path = temporary_lock_path("security");
        let root = path
            .parent()
            .and_then(Path::parent)
            .expect("test path should have a private root");
        let guard = match acquire_posix_path(&path) {
            AcquireOutcome::Acquired(guard) => guard,
            other => panic!("secure path should acquire, got {other:?}"),
        };
        let canonical_path = guard.lock_path().to_owned();
        let parent = canonical_path
            .parent()
            .expect("lock path should have a parent");
        assert_eq!(
            fs::metadata(parent)
                .expect("lock directory metadata should exist")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(&canonical_path)
                .expect("lock file metadata should exist")
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        drop(guard);

        let alias = root.join("lock-hard-link-alias");
        fs::hard_link(&canonical_path, &alias).expect("test hard link should be created");
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::Fatal(code) if code == libc::EPERM as u32
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn backend_rejects_an_identity_other_than_the_frozen_one() {
        let error = match NativeInstanceLockBackend.acquire("different.identity") {
            Err(error) => error,
            Ok(_) => panic!("identity drift must fail closed"),
        };
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
    }

    #[test]
    fn rust_and_python_conflict_in_both_directions_and_release_normally() {
        let path = temporary_lock_path("cross-language-normal");
        let root = path
            .parent()
            .and_then(Path::parent)
            .expect("test path should have a private root");
        let rust_guard = match acquire_posix_path(&path) {
            AcquireOutcome::Acquired(guard) => guard,
            other => panic!("Rust should acquire first, got {other:?}"),
        };
        let status = python_probe(root, "try")
            .status()
            .expect("Python try probe should finish");
        assert_eq!(status.code(), Some(3), "Python must observe the Rust lock");
        drop(rust_guard);

        let mut child = spawn_holding_python(root);
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::AlreadyRunning
        ));
        child
            .stdin
            .as_mut()
            .expect("probe stdin should be piped")
            .write_all(b"release\n")
            .expect("release command should be written");
        assert!(child.wait().expect("probe should exit").success());
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::Acquired(_)
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn killing_the_python_holder_releases_the_os_lock_without_deleting_the_file() {
        let path = temporary_lock_path("cross-language-kill");
        let root = path
            .parent()
            .and_then(Path::parent)
            .expect("test path should have a private root");
        let mut child = spawn_holding_python(root);
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::AlreadyRunning
        ));
        child.kill().expect("probe should be killed");
        child.wait().expect("killed probe should be reaped");
        assert!(path.exists(), "ordinary lock file should remain after kill");
        assert!(matches!(
            acquire_posix_path(&path),
            AcquireOutcome::Acquired(_)
        ));
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn killing_the_rust_holder_releases_the_os_lock_for_python() {
        let path = temporary_lock_path("cross-language-rust-kill");
        let root = path
            .parent()
            .and_then(Path::parent)
            .expect("test path should have a private root");
        let mut child = spawn_holding_rust(root);
        let blocked = python_probe(root, "try")
            .status()
            .expect("Python conflict probe should finish");
        assert_eq!(blocked.code(), Some(3), "Python must observe the Rust lock");
        child.kill().expect("Rust probe should be killed");
        child.wait().expect("killed Rust probe should be reaped");
        assert!(path.exists(), "ordinary lock file should remain after kill");
        assert!(
            python_probe(root, "try")
                .status()
                .expect("Python reacquire probe should finish")
                .success(),
            "Python should acquire after the Rust holder is killed"
        );
        let _ = fs::remove_dir_all(root);
    }
}
