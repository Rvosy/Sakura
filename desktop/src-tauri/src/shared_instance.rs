pub const SHARED_MUTEX_NAME: &str = r"Local\SakuraDesktop.SharedUserData.v1";

#[cfg(windows)]
use windows::{
    core::PCWSTR,
    Win32::{
        Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, HANDLE},
        System::Threading::{CreateMutexW, ReleaseMutex},
    },
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
}

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

    #[cfg(not(windows))]
    pub fn acquire() -> AcquireOutcome {
        AcquireOutcome::Fatal(1)
    }
}

#[cfg(windows)]
impl Drop for SharedInstanceGuard {
    fn drop(&mut self) {
        let _ = unsafe { ReleaseMutex(self.handle) };
        let _ = unsafe { CloseHandle(self.handle) };
    }
}

#[cfg(all(test, windows))]
mod tests {
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
}
