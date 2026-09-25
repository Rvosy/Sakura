//! Shared, serialized repository for Runtime v2 `ui.json` domains.

use std::{
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Mutex,
    },
};

use serde_json::Value;

static NEXT_TEMP: AtomicU64 = AtomicU64::new(0);

#[derive(Clone, Debug)]
pub struct UiConfigRepository {
    path: PathBuf,
    transaction: Arc<Mutex<()>>,
}

impl UiConfigRepository {
    pub fn new(path: PathBuf) -> Self {
        Self {
            path,
            transaction: Arc::new(Mutex::new(())),
        }
    }

    pub(crate) fn path(&self) -> &Path {
        &self.path
    }

    pub fn load(&self, namespace: &str) -> Result<Value, String> {
        let _guard = self.transaction.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error(
                &code(namespace, "STATE_UNAVAILABLE"),
                source_error,
            )
        })?;
        self.load_unlocked(namespace)
    }

    pub fn update(
        &self,
        namespace: &str,
        mutate: impl FnOnce(&mut Value) -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self.transaction.lock().map_err(|source_error| {
            crate::runtime_log::diagnostic_error(
                &code(namespace, "STATE_UNAVAILABLE"),
                source_error,
            )
        })?;
        let mut document = self.load_unlocked(namespace)?;
        mutate(&mut document)?;
        let mut bytes = serde_json::to_vec_pretty(&document).map_err(|source_error| {
            crate::runtime_log::diagnostic_error(&code(namespace, "SERIALIZE_FAILED"), source_error)
        })?;
        bytes.push(b'\n');
        atomic_write(&self.path, &bytes, namespace)
    }

    fn load_unlocked(&self, namespace: &str) -> Result<Value, String> {
        if !self.path.exists() {
            return Ok(serde_json::json!({
                "schema_version": 1,
                "domain": "ui",
                "settings": {}
            }));
        }
        let bytes = fs::read(&self.path).map_err(|source_error| {
            crate::runtime_log::diagnostic_error(&code(namespace, "READ_FAILED"), source_error)
        })?;
        serde_json::from_slice(&bytes).map_err(|source_error| {
            crate::runtime_log::diagnostic_error(&code(namespace, "DOCUMENT_INVALID"), source_error)
        })
    }
}

fn code(namespace: &str, suffix: &str) -> String {
    format!("{namespace}_{suffix}")
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8], namespace: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| code(namespace, "PATH_INVALID"))?;
    fs::create_dir_all(parent).map_err(|source_error| {
        crate::runtime_log::diagnostic_error(&code(namespace, "PERMISSION_DENIED"), source_error)
    })?;
    let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
    let temp = parent.join(format!(".ui.json.{}.{}.tmp", std::process::id(), sequence));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error(
                    &code(namespace, "TEMP_CREATE_FAILED"),
                    source_error,
                )
            })?;
        file.write_all(bytes)
            .and_then(|()| file.flush())
            .and_then(|()| file.sync_all())
            .map_err(|source_error| {
                crate::runtime_log::diagnostic_error(&code(namespace, "WRITE_FAILED"), source_error)
            })?;
        drop(file);
        atomic_replace(&temp, path, namespace)?;
        sync_parent(parent, namespace)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(&temp);
    }
    result
}

#[cfg(windows)]
fn atomic_replace(temp: &Path, target: &Path, namespace: &str) -> Result<(), String> {
    use std::os::windows::ffi::OsStrExt;
    use std::{thread, time::Duration};
    use windows::{
        core::PCWSTR,
        Win32::Storage::FileSystem::{
            MoveFileExW, ReplaceFileW, MOVEFILE_WRITE_THROUGH, REPLACEFILE_WRITE_THROUGH,
        },
    };
    let wide = |path: &Path| {
        path.as_os_str()
            .encode_wide()
            .chain(std::iter::once(0))
            .collect::<Vec<_>>()
    };
    let temp_wide = wide(temp);
    let target_wide = wide(target);
    let replace = || unsafe {
        if target.is_file() {
            return ReplaceFileW(
                PCWSTR(target_wide.as_ptr()),
                PCWSTR(temp_wide.as_ptr()),
                PCWSTR::null(),
                REPLACEFILE_WRITE_THROUGH,
                None,
                None,
            );
        }
        MoveFileExW(
            PCWSTR(temp_wide.as_ptr()),
            PCWSTR(target_wide.as_ptr()),
            MOVEFILE_WRITE_THROUGH,
        )
    };
    let mut last_error = None;
    for delay_ms in [0, 60, 160, 320] {
        if delay_ms > 0 {
            thread::sleep(Duration::from_millis(delay_ms));
        }
        match replace() {
            Ok(()) => return Ok(()),
            Err(error) if matches!(error.code().0 as u32 & 0xffff, 5 | 32) => {
                last_error = Some(error);
                continue;
            }
            Err(error) => {
                return Err(crate::runtime_log::diagnostic_error(
                    &code(namespace, "ATOMIC_REPLACE_FAILED"),
                    error,
                ))
            }
        }
    }
    Err(crate::runtime_log::diagnostic_error(
        &code(namespace, "ATOMIC_REPLACE_FAILED"),
        last_error.unwrap(),
    ))
}

#[cfg(not(windows))]
fn atomic_replace(temp: &Path, target: &Path, namespace: &str) -> Result<(), String> {
    fs::rename(temp, target).map_err(|source_error| {
        crate::runtime_log::diagnostic_error(
            &code(namespace, "ATOMIC_REPLACE_FAILED"),
            source_error,
        )
    })
}

#[cfg(unix)]
fn sync_parent(parent: &Path, namespace: &str) -> Result<(), String> {
    fs::File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|source_error| {
            crate::runtime_log::diagnostic_error(
                &code(namespace, "DIRECTORY_SYNC_FAILED"),
                source_error,
            )
        })
}

#[cfg(not(unix))]
fn sync_parent(_parent: &Path, _namespace: &str) -> Result<(), String> {
    Ok(())
}
