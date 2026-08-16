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

    pub fn load(&self, namespace: &str) -> Result<Value, String> {
        let _guard = self
            .transaction
            .lock()
            .map_err(|_| code(namespace, "STATE_UNAVAILABLE"))?;
        self.load_unlocked(namespace)
    }

    pub fn update(
        &self,
        namespace: &str,
        mutate: impl FnOnce(&mut Value) -> Result<(), String>,
    ) -> Result<(), String> {
        let _guard = self
            .transaction
            .lock()
            .map_err(|_| code(namespace, "STATE_UNAVAILABLE"))?;
        let mut document = self.load_unlocked(namespace)?;
        mutate(&mut document)?;
        let mut bytes = serde_json::to_vec_pretty(&document)
            .map_err(|_| code(namespace, "SERIALIZE_FAILED"))?;
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
        let bytes = fs::read(&self.path).map_err(|_| code(namespace, "READ_FAILED"))?;
        if bytes.is_empty() || bytes.len() > 512 * 1024 {
            return Err(code(namespace, "DOCUMENT_INVALID"));
        }
        serde_json::from_slice(&bytes).map_err(|_| code(namespace, "DOCUMENT_INVALID"))
    }
}

fn code(namespace: &str, suffix: &str) -> String {
    format!("{namespace}_{suffix}")
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8], namespace: &str) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| code(namespace, "PATH_INVALID"))?;
    fs::create_dir_all(parent).map_err(|_| code(namespace, "PERMISSION_DENIED"))?;
    let sequence = NEXT_TEMP.fetch_add(1, Ordering::Relaxed);
    let temp = parent.join(format!(".ui.json.{}.{}.tmp", std::process::id(), sequence));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|_| code(namespace, "TEMP_CREATE_FAILED"))?;
        file.write_all(bytes)
            .and_then(|()| file.flush())
            .and_then(|()| file.sync_all())
            .map_err(|_| code(namespace, "WRITE_FAILED"))?;
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
    for delay_ms in [0, 60, 160, 320] {
        if delay_ms > 0 {
            thread::sleep(Duration::from_millis(delay_ms));
        }
        match replace() {
            Ok(()) => return Ok(()),
            Err(error) if matches!(error.code().0 as u32 & 0xffff, 5 | 32) => continue,
            Err(_) => return Err(code(namespace, "ATOMIC_REPLACE_FAILED")),
        }
    }
    Err(code(namespace, "ATOMIC_REPLACE_FAILED"))
}

#[cfg(not(windows))]
fn atomic_replace(temp: &Path, target: &Path, namespace: &str) -> Result<(), String> {
    fs::rename(temp, target).map_err(|_| code(namespace, "ATOMIC_REPLACE_FAILED"))
}

#[cfg(unix)]
fn sync_parent(parent: &Path, namespace: &str) -> Result<(), String> {
    fs::File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| code(namespace, "DIRECTORY_SYNC_FAILED"))
}

#[cfg(not(unix))]
fn sync_parent(_parent: &Path, _namespace: &str) -> Result<(), String> {
    Ok(())
}
