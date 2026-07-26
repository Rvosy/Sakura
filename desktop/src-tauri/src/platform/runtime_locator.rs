use std::{
    fs::{self, File},
    io::{self, Read},
    path::{Component, Path, PathBuf},
};

use serde::{Deserialize, Serialize};

use super::{
    current_platform_target, PlatformError, PlatformErrorCategory, PlatformResult, PlatformService,
    PlatformTarget, RetryAdvice, RuntimeLayout, RuntimeLocationRequest, RuntimeLocator,
    RuntimeMode,
};

const MANIFEST_FILE: &str = "runtime-manifest.json";
const PACKAGED_RUNTIME_DIRECTORY: &str = "runtime-v2";
const MANIFEST_WINDOWS_X64: &str =
    include_str!("../../runtime-layouts/windows-x64/runtime-manifest.json");
const MANIFEST_MACOS_ARM64: &str =
    include_str!("../../runtime-layouts/macos-arm64/runtime-manifest.json");
const MANIFEST_LINUX_X64: &str =
    include_str!("../../runtime-layouts/linux-x64/runtime-manifest.json");

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeArchiveManifest {
    pub file_name: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub archive_root: String,
    pub strip_components: u8,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct AssistantDependencyManifest {
    pub package: String,
    pub version: String,
    pub file_name: String,
    pub url: String,
    pub size: u64,
    pub sha256: String,
    pub development_relative_path: PathBuf,
    pub packaged_relative_path: PathBuf,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RuntimeManifest {
    pub schema_version: u32,
    pub target: PlatformTarget,
    pub python_version: String,
    pub source_id: String,
    pub archive: RuntimeArchiveManifest,
    pub assistant_dependency: AssistantDependencyManifest,
    pub packaged_python_relative_path: PathBuf,
    pub packaged_application_root_relative_path: PathBuf,
    pub packaged_core_entry_relative_path: PathBuf,
    pub development_python_relative_path: PathBuf,
    pub development_application_root_relative_path: PathBuf,
    pub development_core_entry_relative_path: PathBuf,
    pub core_module: String,
}

impl RuntimeManifest {
    fn validate(&self, expected_target: PlatformTarget) -> PlatformResult<()> {
        if self.schema_version != 1
            || self.target != expected_target
            || self.python_version != "3.12.8"
            || self.source_id.trim().is_empty()
            || self.archive.file_name.trim().is_empty()
            || self.archive.url.trim().is_empty()
            || self.archive.size == 0
            || self.archive.strip_components > 1
            || self.archive.sha256.len() != 64
            || !self
                .archive
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
            || self.assistant_dependency.package != "PyYAML"
            || self.assistant_dependency.version != "6.0.2"
            || self.assistant_dependency.file_name.trim().is_empty()
            || !self
                .assistant_dependency
                .url
                .starts_with("https://files.pythonhosted.org/")
            || self.assistant_dependency.size == 0
            || self.assistant_dependency.sha256.len() != 64
            || !self
                .assistant_dependency
                .sha256
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
            || self
                .assistant_dependency
                .development_relative_path
                .file_name()
                .is_none_or(|name| name != self.assistant_dependency.file_name.as_str())
            || self
                .assistant_dependency
                .packaged_relative_path
                .file_name()
                .is_none_or(|name| name != self.assistant_dependency.file_name.as_str())
            || self.core_module != "app.core_host"
        {
            return Err(locator_error(
                PlatformErrorCategory::IntegrityMismatch,
                "validate_manifest",
                RetryAdvice::AfterUserAction,
                "runtime manifest identity or source metadata is invalid",
            ));
        }
        ensure_safe_relative(Path::new(&self.archive.archive_root))?;
        for path in [
            &self.assistant_dependency.development_relative_path,
            &self.assistant_dependency.packaged_relative_path,
            &self.packaged_python_relative_path,
            &self.packaged_application_root_relative_path,
            &self.packaged_core_entry_relative_path,
            &self.development_python_relative_path,
            &self.development_application_root_relative_path,
            &self.development_core_entry_relative_path,
        ] {
            ensure_safe_relative(path)?;
        }
        Ok(())
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum BinaryIdentity {
    Pe(u16),
    MachO(u32),
    Elf(u16),
}

#[derive(Default)]
pub struct FilesystemRuntimeLocator;

impl RuntimeLocator for FilesystemRuntimeLocator {
    fn locate(&self, request: &RuntimeLocationRequest) -> PlatformResult<RuntimeLayout> {
        self.locate_internal(request, true)
    }
}

impl FilesystemRuntimeLocator {
    fn locate_internal(
        &self,
        request: &RuntimeLocationRequest,
        enforce_current_target: bool,
    ) -> PlatformResult<RuntimeLayout> {
        request.validate()?;
        validate_request_roots(request)?;
        if enforce_current_target {
            let Some(current) = current_platform_target() else {
                return Err(locator_error(
                    PlatformErrorCategory::UnsupportedEnvironment,
                    "select_target",
                    RetryAdvice::Never,
                    "the current build is not a formal Phase 1P target",
                ));
            };
            if request.target != current {
                return Err(locator_error(
                    PlatformErrorCategory::IncompatibleArchitecture,
                    "select_target",
                    RetryAdvice::Never,
                    format!(
                        "requested {} from a {} build",
                        request.target.platform_id(),
                        current.platform_id()
                    ),
                ));
            }
        }

        let expected = expected_manifest(request.target)?;
        expected.validate(request.target)?;
        let (
            runtime_root,
            python_relative,
            resource_relative,
            core_entry_relative,
            dependency_relative,
        ) = match request.mode {
            RuntimeMode::Packaged => {
                let root = request
                    .resource_directory
                    .join(PACKAGED_RUNTIME_DIRECTORY)
                    .join(request.target.platform_id());
                let actual = read_packaged_manifest(&root)?;
                if actual != expected {
                    return Err(locator_error(
                        PlatformErrorCategory::IntegrityMismatch,
                        "compare_manifest",
                        RetryAdvice::AfterUserAction,
                        "packaged runtime manifest differs from the compiled source manifest",
                    ));
                }
                (
                    root,
                    expected.packaged_python_relative_path.clone(),
                    expected.packaged_application_root_relative_path.clone(),
                    expected.packaged_core_entry_relative_path.clone(),
                    expected.assistant_dependency.packaged_relative_path.clone(),
                )
            }
            RuntimeMode::ExplicitDevelopment => (
                request
                    .explicit_development_root
                    .clone()
                    .expect("validated development request has a root"),
                expected.development_python_relative_path.clone(),
                expected.development_application_root_relative_path.clone(),
                expected.development_core_entry_relative_path.clone(),
                expected
                    .assistant_dependency
                    .development_relative_path
                    .clone(),
            ),
        };

        let runtime_root = canonical_existing(&runtime_root, "resolve_runtime_root")?;
        if request.mode == RuntimeMode::Packaged {
            let resource_root =
                canonical_existing(&request.resource_directory, "resolve_resource_directory")?;
            if !runtime_root.starts_with(resource_root) {
                return Err(locator_error(
                    PlatformErrorCategory::IntegrityMismatch,
                    "resolve_runtime_root",
                    RetryAdvice::AfterUserAction,
                    "packaged runtime root escapes its Tauri resource directory",
                ));
            }
        }
        let python_executable =
            canonical_child(&runtime_root, &python_relative, "resolve_python_executable")?;
        let resource_root =
            canonical_child(&runtime_root, &resource_relative, "resolve_resource_root")?;
        let assistant_dependency = canonical_child(
            &runtime_root,
            &dependency_relative,
            "resolve_assistant_dependency",
        )?;
        validate_assistant_dependency(
            &assistant_dependency,
            &expected.assistant_dependency,
            enforce_current_target,
        )?;
        let assistant_root = canonical_request_root(
            &request.assistant_root,
            "resolve_assistant_root",
            "Assistant root",
        )?;
        if !assistant_root.is_dir() {
            return Err(locator_error(
                PlatformErrorCategory::NotFound,
                "resolve_assistant_root",
                RetryAdvice::Never,
                "Assistant root is not an existing directory",
            ));
        }
        let core_entry =
            canonical_child(&runtime_root, &core_entry_relative, "resolve_core_entry")?;
        if !python_executable.is_file() || !core_entry.is_file() || !resource_root.is_dir() {
            return Err(locator_error(
                PlatformErrorCategory::NotFound,
                "validate_layout_entries",
                RetryAdvice::AfterExternalChange,
                "runtime layout is missing its Python executable, resource root, or Core entry",
            ));
        }
        validate_executable_permission(&python_executable)?;
        validate_binary_target(&python_executable, request.target)?;

        Ok(RuntimeLayout {
            target: request.target,
            architecture: request.target.architecture(),
            mode: request.mode,
            runtime_root,
            python_executable,
            python_path_entries: vec![assistant_dependency],
            resource_root: resource_root.clone(),
            assistant_root,
            core_entry,
            core_module: expected.core_module,
            working_directory: resource_root,
            source_id: expected.source_id,
        })
    }

    #[cfg(test)]
    fn locate_fixture(&self, request: &RuntimeLocationRequest) -> PlatformResult<RuntimeLayout> {
        self.locate_internal(request, false)
    }
}

fn expected_manifest(target: PlatformTarget) -> PlatformResult<RuntimeManifest> {
    let source = match target {
        PlatformTarget::WindowsX64 => MANIFEST_WINDOWS_X64,
        PlatformTarget::MacOsArm64 => MANIFEST_MACOS_ARM64,
        PlatformTarget::LinuxX64 => MANIFEST_LINUX_X64,
    };
    serde_json::from_str(source).map_err(|error| {
        locator_error(
            PlatformErrorCategory::IntegrityMismatch,
            "load_compiled_manifest",
            RetryAdvice::Never,
            format!("compiled runtime manifest is invalid: {error}"),
        )
    })
}

fn read_packaged_manifest(runtime_root: &Path) -> PlatformResult<RuntimeManifest> {
    let manifest_path = runtime_root.join(MANIFEST_FILE);
    let bytes = fs::read(&manifest_path)
        .map_err(|error| io_locator_error("read_packaged_manifest", &manifest_path, error))?;
    serde_json::from_slice(&bytes).map_err(|error| {
        locator_error(
            PlatformErrorCategory::IntegrityMismatch,
            "parse_packaged_manifest",
            RetryAdvice::AfterUserAction,
            format!("packaged runtime manifest is invalid: {error}"),
        )
    })
}

fn validate_request_roots(request: &RuntimeLocationRequest) -> PlatformResult<()> {
    if !request.executable_directory.is_absolute()
        || !request.resource_directory.is_absolute()
        || !request.assistant_root.is_absolute()
    {
        return Err(locator_error(
            PlatformErrorCategory::InvalidInput,
            "validate_location_request",
            RetryAdvice::Never,
            "executable, resource, and Assistant directories must be absolute",
        ));
    }
    if request.mode == RuntimeMode::ExplicitDevelopment
        && request
            .explicit_development_root
            .as_ref()
            .is_none_or(|path| !path.is_absolute())
    {
        return Err(locator_error(
            PlatformErrorCategory::InvalidInput,
            "validate_location_request",
            RetryAdvice::Never,
            "development runtime root must be absolute",
        ));
    }
    Ok(())
}

fn ensure_safe_relative(path: &Path) -> PlatformResult<()> {
    if path.as_os_str().is_empty()
        || path.is_absolute()
        || path.components().any(|component| {
            matches!(
                component,
                Component::ParentDir | Component::RootDir | Component::Prefix(_)
            )
        })
    {
        return Err(locator_error(
            PlatformErrorCategory::IntegrityMismatch,
            "validate_manifest_path",
            RetryAdvice::AfterUserAction,
            "runtime manifest path is not a safe relative path",
        ));
    }
    Ok(())
}

fn canonical_existing(path: &Path, operation: &'static str) -> PlatformResult<PathBuf> {
    fs::canonicalize(path).map_err(|error| io_locator_error(operation, path, error))
}

fn canonical_request_root(
    requested: &Path,
    operation: &'static str,
    label: &'static str,
) -> PlatformResult<PathBuf> {
    if requested
        .components()
        .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(locator_error(
            PlatformErrorCategory::InvalidInput,
            operation,
            RetryAdvice::Never,
            format!("{label} must already be canonical"),
        ));
    }
    let canonical = canonical_existing(requested, operation).map_err(|error| {
        if error.category == PlatformErrorCategory::NotFound {
            locator_error(
                PlatformErrorCategory::NotFound,
                operation,
                RetryAdvice::Never,
                format!("{label} does not exist"),
            )
        } else {
            error
        }
    })?;
    if !canonical_paths_equivalent(requested, &canonical) {
        return Err(locator_error(
            PlatformErrorCategory::InvalidInput,
            operation,
            RetryAdvice::Never,
            format!("{label} must already be canonical"),
        ));
    }
    Ok(canonical)
}

#[cfg(not(windows))]
fn canonical_paths_equivalent(requested: &Path, canonical: &Path) -> bool {
    requested == canonical
}

#[cfg(windows)]
fn canonical_paths_equivalent(requested: &Path, canonical: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;

    fn without_verbatim_prefix(path: &Path) -> PathBuf {
        const VERBATIM: &str = r"\\?\";
        let text = path.as_os_str().to_string_lossy();
        if let Some(stripped) = text.strip_prefix(VERBATIM) {
            PathBuf::from(stripped)
        } else {
            path.to_path_buf()
        }
    }

    without_verbatim_prefix(requested)
        .as_os_str()
        .encode_wide()
        .eq(without_verbatim_prefix(canonical).as_os_str().encode_wide())
}

fn canonical_child(
    root: &Path,
    relative: &Path,
    operation: &'static str,
) -> PlatformResult<PathBuf> {
    ensure_safe_relative(relative)?;
    let child = canonical_existing(&root.join(relative), operation)?;
    if !child.starts_with(root) {
        return Err(locator_error(
            PlatformErrorCategory::IntegrityMismatch,
            operation,
            RetryAdvice::AfterUserAction,
            "runtime manifest path escapes its controlled root",
        ));
    }
    Ok(child)
}

fn validate_assistant_dependency(
    path: &Path,
    expected: &AssistantDependencyManifest,
    verify_content: bool,
) -> PlatformResult<()> {
    let metadata = fs::metadata(path)
        .map_err(|error| io_locator_error("inspect_assistant_dependency", path, error))?;
    if !metadata.is_file() {
        return Err(locator_error(
            PlatformErrorCategory::IntegrityMismatch,
            "inspect_assistant_dependency",
            RetryAdvice::AfterUserAction,
            "Assistant dependency artifact is not a regular file",
        ));
    }
    if verify_content {
        let bytes = fs::read(path)
            .map_err(|error| io_locator_error("read_assistant_dependency", path, error))?;
        if metadata.len() != expected.size || sha256_hex(&bytes) != expected.sha256 {
            return Err(locator_error(
                PlatformErrorCategory::IntegrityMismatch,
                "verify_assistant_dependency",
                RetryAdvice::AfterUserAction,
                "Assistant dependency artifact failed size or SHA-256 verification",
            ));
        }
    }
    Ok(())
}

fn sha256_hex(input: &[u8]) -> String {
    const INITIAL: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    const ROUND: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let bit_len = (input.len() as u64).wrapping_mul(8);
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_len.to_be_bytes());
    let mut state = INITIAL;
    for chunk in padded.chunks_exact(64) {
        let mut words = [0_u32; 64];
        for (index, word) in words[..16].iter_mut().enumerate() {
            *word = u32::from_be_bytes(chunk[index * 4..index * 4 + 4].try_into().unwrap());
        }
        for index in 16..64 {
            let s0 = words[index - 15].rotate_right(7)
                ^ words[index - 15].rotate_right(18)
                ^ (words[index - 15] >> 3);
            let s1 = words[index - 2].rotate_right(17)
                ^ words[index - 2].rotate_right(19)
                ^ (words[index - 2] >> 10);
            words[index] = words[index - 16]
                .wrapping_add(s0)
                .wrapping_add(words[index - 7])
                .wrapping_add(s1);
        }
        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for index in 0..64 {
            let sum1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let choice = (e & f) ^ ((!e) & g);
            let temp1 = h
                .wrapping_add(sum1)
                .wrapping_add(choice)
                .wrapping_add(ROUND[index])
                .wrapping_add(words[index]);
            let sum0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let majority = (a & b) ^ (a & c) ^ (b & c);
            let temp2 = sum0.wrapping_add(majority);
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(temp1);
            d = c;
            c = b;
            b = a;
            a = temp1.wrapping_add(temp2);
        }
        for (slot, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *slot = slot.wrapping_add(value);
        }
    }
    state.iter().map(|word| format!("{word:08x}")).collect()
}

fn validate_executable_permission(path: &Path) -> PlatformResult<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let mode = fs::metadata(path)
            .map_err(|error| io_locator_error("read_python_permissions", path, error))?
            .permissions()
            .mode();
        if mode & 0o111 == 0 {
            return Err(locator_error(
                PlatformErrorCategory::PermissionDenied,
                "validate_python_permissions",
                RetryAdvice::AfterUserAction,
                "bundled Python is not executable",
            ));
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn validate_binary_target(path: &Path, target: PlatformTarget) -> PlatformResult<()> {
    let identity = read_binary_identity(path)?;
    let matches = match (target, identity) {
        (PlatformTarget::WindowsX64, BinaryIdentity::Pe(0x8664)) => true,
        (PlatformTarget::MacOsArm64, BinaryIdentity::MachO(0x0100_000c)) => true,
        (PlatformTarget::LinuxX64, BinaryIdentity::Elf(0x003e)) => true,
        _ => false,
    };
    if !matches {
        return Err(locator_error(
            PlatformErrorCategory::IncompatibleArchitecture,
            "validate_python_architecture",
            RetryAdvice::Never,
            format!(
                "bundled Python identity {identity:?} does not match {}",
                target.platform_id()
            ),
        ));
    }
    Ok(())
}

fn read_binary_identity(path: &Path) -> PlatformResult<BinaryIdentity> {
    let file =
        File::open(path).map_err(|error| io_locator_error("open_python_binary", path, error))?;
    let mut bytes = Vec::with_capacity(4096);
    file.take(4096)
        .read_to_end(&mut bytes)
        .map_err(|error| io_locator_error("read_python_binary", path, error))?;

    if bytes.starts_with(b"MZ") && bytes.len() >= 0x40 {
        let pe_offset =
            u32::from_le_bytes(bytes[0x3c..0x40].try_into().expect("fixed PE slice")) as usize;
        if pe_offset
            .checked_add(6)
            .is_some_and(|end| end <= bytes.len())
            && bytes[pe_offset..pe_offset + 4] == *b"PE\0\0"
        {
            return Ok(BinaryIdentity::Pe(u16::from_le_bytes([
                bytes[pe_offset + 4],
                bytes[pe_offset + 5],
            ])));
        }
    }
    if bytes.len() >= 8 && bytes[0..4] == [0xcf, 0xfa, 0xed, 0xfe] {
        return Ok(BinaryIdentity::MachO(u32::from_le_bytes(
            bytes[4..8].try_into().expect("fixed Mach-O slice"),
        )));
    }
    if bytes.len() >= 20
        && bytes[0..4] == [0x7f, b'E', b'L', b'F']
        && bytes[4] == 2
        && bytes[5] == 1
    {
        return Ok(BinaryIdentity::Elf(u16::from_le_bytes([
            bytes[18], bytes[19],
        ])));
    }
    Err(locator_error(
        PlatformErrorCategory::IntegrityMismatch,
        "inspect_python_binary",
        RetryAdvice::AfterUserAction,
        "bundled Python has an invalid or unsupported executable header",
    ))
}

fn io_locator_error(operation: &'static str, _path: &Path, error: io::Error) -> PlatformError {
    let native_code = error.raw_os_error();
    let (category, retry) = match error.kind() {
        io::ErrorKind::NotFound => (
            PlatformErrorCategory::NotFound,
            RetryAdvice::AfterExternalChange,
        ),
        io::ErrorKind::PermissionDenied => (
            PlatformErrorCategory::PermissionDenied,
            RetryAdvice::AfterUserAction,
        ),
        _ => (PlatformErrorCategory::NativeFailure, RetryAdvice::Never),
    };
    let mapped = locator_error(category, operation, retry, error.to_string());
    match native_code {
        Some(code) => mapped.with_native_code(native_error_namespace(), i64::from(code)),
        None => mapped,
    }
}

#[cfg(windows)]
const fn native_error_namespace() -> &'static str {
    "win32"
}

#[cfg(unix)]
const fn native_error_namespace() -> &'static str {
    "errno"
}

#[cfg(not(any(windows, unix)))]
const fn native_error_namespace() -> &'static str {
    "os"
}

fn locator_error(
    category: PlatformErrorCategory,
    operation: &'static str,
    retry: RetryAdvice,
    message: impl Into<String>,
) -> PlatformError {
    PlatformError::new(
        PlatformService::RuntimeLocator,
        category,
        operation,
        retry,
        message,
    )
}

#[cfg(test)]
mod tests {
    use std::{
        process,
        sync::atomic::{AtomicU64, Ordering},
    };

    use super::*;

    static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(1);
    const WP_1C_04_LIFECYCLE_GOLDEN: &str =
        include_str!("../../../../tests/fixtures/runtime_v2/wp_1c_04/lifecycle-golden.json");

    struct FixtureDirectory(PathBuf);

    impl FixtureDirectory {
        fn new(name: &str) -> Self {
            let id = NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed);
            let path = std::env::temp_dir().join(format!(
                "sakura-runtime-v2-wp-1p-02-{}-{id}-{name}",
                process::id()
            ));
            if path.exists() {
                fs::remove_dir_all(&path).expect("stale locator fixture should remove");
            }
            fs::create_dir_all(&path).expect("locator fixture should create");
            Self(
                path.canonicalize()
                    .expect("locator fixture root should be canonical"),
            )
        }

        fn path(&self) -> &Path {
            &self.0
        }
    }

    impl Drop for FixtureDirectory {
        fn drop(&mut self) {
            if self.0.exists() {
                fs::remove_dir_all(&self.0).expect("locator fixture should clean up");
            }
        }
    }

    fn binary_header(target: PlatformTarget) -> Vec<u8> {
        match target {
            PlatformTarget::WindowsX64 => {
                let mut bytes = vec![0; 128];
                bytes[0..2].copy_from_slice(b"MZ");
                bytes[0x3c..0x40].copy_from_slice(&64u32.to_le_bytes());
                bytes[64..68].copy_from_slice(b"PE\0\0");
                bytes[68..70].copy_from_slice(&0x8664u16.to_le_bytes());
                bytes
            }
            PlatformTarget::MacOsArm64 => {
                let mut bytes = vec![0; 32];
                bytes[0..4].copy_from_slice(&[0xcf, 0xfa, 0xed, 0xfe]);
                bytes[4..8].copy_from_slice(&0x0100_000cu32.to_le_bytes());
                bytes
            }
            PlatformTarget::LinuxX64 => {
                let mut bytes = vec![0; 64];
                bytes[0..6].copy_from_slice(&[0x7f, b'E', b'L', b'F', 2, 1]);
                bytes[18..20].copy_from_slice(&0x003eu16.to_le_bytes());
                bytes
            }
        }
    }

    fn make_executable(path: &Path) {
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            let mut permissions = fs::metadata(path).unwrap().permissions();
            permissions.set_mode(0o755);
            fs::set_permissions(path, permissions).unwrap();
        }
        #[cfg(not(unix))]
        let _ = path;
    }

    fn create_packaged_layout(
        fixture: &FixtureDirectory,
        target: PlatformTarget,
    ) -> RuntimeLocationRequest {
        let resource_directory = fixture.path().join("resources");
        let runtime_root = resource_directory
            .join(PACKAGED_RUNTIME_DIRECTORY)
            .join(target.platform_id());
        let manifest = expected_manifest(target).unwrap();
        fs::create_dir_all(&runtime_root).unwrap();
        fs::write(
            runtime_root.join(MANIFEST_FILE),
            serde_json::to_vec_pretty(&manifest).unwrap(),
        )
        .unwrap();
        let python = runtime_root.join(&manifest.packaged_python_relative_path);
        fs::create_dir_all(python.parent().unwrap()).unwrap();
        fs::write(&python, binary_header(target)).unwrap();
        make_executable(&python);
        let core_entry = runtime_root.join(&manifest.packaged_core_entry_relative_path);
        fs::create_dir_all(core_entry.parent().unwrap()).unwrap();
        fs::write(core_entry, b"# golden Core entry\n").unwrap();
        let dependency = runtime_root.join(&manifest.assistant_dependency.packaged_relative_path);
        fs::create_dir_all(dependency.parent().unwrap()).unwrap();
        fs::write(dependency, b"fixture dependency").unwrap();
        fs::create_dir_all(fixture.path().join("assistant-root")).unwrap();
        RuntimeLocationRequest {
            mode: RuntimeMode::Packaged,
            target,
            executable_directory: fixture.path().join("bin"),
            resource_directory,
            explicit_development_root: None,
            assistant_root: fixture.path().join("assistant-root"),
        }
    }

    #[test]
    fn all_source_manifests_are_exact_complete_and_unique() {
        let mut source_ids = Vec::new();
        let mut hashes = Vec::new();
        for target in PlatformTarget::ALL {
            let manifest = expected_manifest(target).expect("compiled manifest should parse");
            manifest.validate(target).expect("manifest should validate");
            assert_eq!(manifest.target, target);
            assert_eq!(manifest.python_version, "3.12.8");
            source_ids.push(manifest.source_id);
            hashes.push(manifest.archive.sha256);
            match target {
                PlatformTarget::WindowsX64 => {
                    assert_eq!(manifest.archive.archive_root, ".");
                    assert_eq!(manifest.archive.strip_components, 0);
                }
                PlatformTarget::MacOsArm64 | PlatformTarget::LinuxX64 => {
                    assert_eq!(manifest.archive.archive_root, "python");
                    assert_eq!(manifest.archive.strip_components, 1);
                }
            }
        }
        source_ids.sort();
        source_ids.dedup();
        hashes.sort();
        hashes.dedup();
        assert_eq!(source_ids.len(), PlatformTarget::ALL.len());
        assert_eq!(hashes.len(), PlatformTarget::ALL.len());
    }

    #[test]
    fn assistant_dependency_integrity_rejects_any_content_change() {
        let fixture = FixtureDirectory::new("assistant-dependency-integrity");
        let path = fixture.path().join("PyYAML.test.whl");
        fs::write(&path, b"abc").unwrap();
        let mut expected = expected_manifest(PlatformTarget::WindowsX64)
            .unwrap()
            .assistant_dependency;
        expected.size = 3;
        expected.sha256 =
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad".to_string();
        validate_assistant_dependency(&path, &expected, true)
            .expect("exact dependency artifact should pass");

        fs::write(&path, b"abd").unwrap();
        let error = validate_assistant_dependency(&path, &expected, true)
            .expect_err("modified dependency artifact must fail closed");
        assert_eq!(error.category, PlatformErrorCategory::IntegrityMismatch);
    }

    #[test]
    fn wp_1c_04_shared_golden_freezes_all_packaged_layout_results() {
        let golden: serde_json::Value =
            serde_json::from_str(WP_1C_04_LIFECYCLE_GOLDEN).expect("golden fixture should parse");
        assert_eq!(golden["schemaVersion"], 1);
        let layouts = golden["layouts"]
            .as_array()
            .expect("golden fixture layouts should be an array");
        assert_eq!(layouts.len(), PlatformTarget::ALL.len());
        for target in PlatformTarget::ALL {
            let layout = layouts
                .iter()
                .find(|layout| layout["target"] == target.platform_id())
                .expect("each formal target has a golden layout");
            let manifest = expected_manifest(target).expect("compiled manifest should parse");
            assert_eq!(
                layout["architecture"],
                serde_json::to_value(target.architecture()).unwrap()
            );
            assert_eq!(
                layout["packagedPythonRelativePath"],
                manifest
                    .packaged_python_relative_path
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            assert_eq!(
                layout["packagedResourceRootRelativePath"],
                manifest
                    .packaged_application_root_relative_path
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            assert_eq!(
                layout["packagedCoreEntryRelativePath"],
                manifest
                    .packaged_core_entry_relative_path
                    .to_string_lossy()
                    .replace('\\', "/")
            );
            assert_eq!(
                layout["packagedWorkingDirectoryRelativePath"],
                layout["packagedResourceRootRelativePath"]
            );
        }
    }

    #[test]
    fn golden_packaged_layouts_resolve_for_all_three_targets() {
        let locator = FilesystemRuntimeLocator;
        for target in PlatformTarget::ALL {
            let fixture = FixtureDirectory::new(target.platform_id());
            let request = create_packaged_layout(&fixture, target);
            let layout = locator
                .locate_fixture(&request)
                .expect("golden packaged layout should resolve");
            assert_eq!(layout.target, target);
            assert_eq!(layout.architecture, target.architecture());
            assert_eq!(layout.mode, RuntimeMode::Packaged);
            assert!(layout.python_executable.is_file());
            assert_ne!(layout.assistant_root, layout.resource_root);
            assert_eq!(layout.working_directory, layout.resource_root);
            assert!(layout.core_entry.is_file());
            assert!(layout
                .resource_root
                .join("app/core_host/__main__.py")
                .is_file());
            assert!(!layout.source_id.is_empty());
        }
    }

    #[test]
    fn moved_resource_root_remains_relocatable() {
        let locator = FilesystemRuntimeLocator;
        let fixture = FixtureDirectory::new("moved-resource");
        let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
        let moved = fixture.path().join("relocated resources");
        fs::rename(&request.resource_directory, &moved).unwrap();
        request.resource_directory = moved;
        let layout = locator.locate_fixture(&request).unwrap();
        assert!(layout
            .runtime_root
            .starts_with(request.resource_directory.canonicalize().unwrap()));
    }

    #[test]
    fn assistant_root_is_independent_from_the_runtime_code_root() {
        let fixture = FixtureDirectory::new("separated-assistant-root");
        let assistant_root = fixture.path().join("assistant-root");
        fs::create_dir_all(&assistant_root).unwrap();
        let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
        request.assistant_root = assistant_root.clone();

        let layout = FilesystemRuntimeLocator.locate_fixture(&request).unwrap();
        assert_eq!(
            layout.assistant_root,
            assistant_root.canonicalize().unwrap()
        );
        assert_ne!(layout.assistant_root, layout.resource_root);
        assert_eq!(layout.working_directory, layout.resource_root);
    }

    #[test]
    fn assistant_root_must_be_an_existing_absolute_directory() {
        let fixture = FixtureDirectory::new("invalid-assistant-root");
        for assistant_root in [
            PathBuf::from("relative"),
            fixture.path().join("missing-assistant-root"),
        ] {
            let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
            request.assistant_root = assistant_root;
            let error = FilesystemRuntimeLocator
                .locate_fixture(&request)
                .expect_err("invalid assistant root must fail before Core spawn");
            assert!(matches!(
                error.category,
                PlatformErrorCategory::InvalidInput | PlatformErrorCategory::NotFound
            ));
            assert_eq!(error.retry, RetryAdvice::Never);
        }
    }

    #[test]
    fn assistant_root_rejects_absolute_dot_and_parent_components() {
        let fixture = FixtureDirectory::new("non-canonical-assistant-root");
        let canonical = fixture.path().join("assistant-root");
        fs::create_dir_all(&canonical).unwrap();
        let parent = canonical.parent().unwrap();
        let separator = std::path::MAIN_SEPARATOR;
        for assistant_root in [
            PathBuf::from(format!(
                "{}{separator}.{separator}assistant-root",
                parent.display()
            )),
            PathBuf::from(format!(
                "{}{separator}..{separator}assistant-root",
                canonical.display()
            )),
        ] {
            let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
            request.assistant_root = assistant_root;
            let error = FilesystemRuntimeLocator
                .locate_fixture(&request)
                .expect_err("non-canonical Assistant root must fail closed");
            assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
            assert_eq!(error.retry, RetryAdvice::Never);
        }
    }

    #[cfg(unix)]
    #[test]
    fn assistant_root_rejects_a_directory_symlink() {
        use std::os::unix::fs::symlink;

        let fixture = FixtureDirectory::new("assistant-root-symlink");
        let target = fixture.path().join("assistant-root-target");
        let link = fixture.path().join("assistant-root-link");
        fs::create_dir_all(&target).unwrap();
        symlink(&target, &link).unwrap();
        let mut request = create_packaged_layout(&fixture, PlatformTarget::LinuxX64);
        request.assistant_root = link;

        let error = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("directory symlink Assistant root must fail closed");
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
        assert_eq!(error.retry, RetryAdvice::Never);
    }

    #[cfg(windows)]
    #[test]
    fn windows_verbatim_prefix_is_the_only_canonical_spelling_difference_allowed() {
        let fixture = FixtureDirectory::new("windows-canonical-prefix");
        let canonical = fixture.path().canonicalize().unwrap();
        let ordinary = PathBuf::from(
            canonical
                .as_os_str()
                .to_string_lossy()
                .strip_prefix(r"\\?\")
                .expect("Windows canonical path should use a verbatim prefix"),
        );
        assert!(canonical_paths_equivalent(&ordinary, &canonical));

        let mut changed_case = ordinary.as_os_str().to_string_lossy().into_owned();
        changed_case.replace_range(0..1, &changed_case[0..1].to_ascii_lowercase());
        if changed_case != ordinary.as_os_str().to_string_lossy() {
            assert!(!canonical_paths_equivalent(
                Path::new(&changed_case),
                &canonical
            ));
        }
    }

    #[test]
    fn packaged_and_development_modes_cannot_be_mixed() {
        let fixture = FixtureDirectory::new("mixed-mode");
        let mut request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
        request.explicit_development_root = Some(fixture.path().to_path_buf());
        let error = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("packaged mode must reject a development root");
        assert_eq!(error.category, PlatformErrorCategory::InvalidInput);
    }

    #[test]
    fn missing_or_modified_packaged_manifest_fails_closed() {
        let fixture = FixtureDirectory::new("manifest-failures");
        let request = create_packaged_layout(&fixture, PlatformTarget::WindowsX64);
        let manifest_path = request
            .resource_directory
            .join(PACKAGED_RUNTIME_DIRECTORY)
            .join(request.target.platform_id())
            .join(MANIFEST_FILE);
        fs::remove_file(&manifest_path).unwrap();
        let missing = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("missing manifest must fail");
        assert_eq!(missing.category, PlatformErrorCategory::NotFound);

        fs::write(&manifest_path, b"{not-json").unwrap();
        let damaged = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("damaged manifest must fail");
        assert_eq!(damaged.category, PlatformErrorCategory::IntegrityMismatch);

        let mut manifest = expected_manifest(request.target).unwrap();
        manifest.source_id.push_str("-changed");
        fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
        let changed = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("changed manifest identity must fail");
        assert_eq!(changed.category, PlatformErrorCategory::IntegrityMismatch);
    }

    #[test]
    fn missing_corrupt_and_wrong_architecture_python_are_distinct() {
        let fixture = FixtureDirectory::new("python-failures");
        let request = create_packaged_layout(&fixture, PlatformTarget::LinuxX64);
        let manifest = expected_manifest(request.target).unwrap();
        let python = request
            .resource_directory
            .join(PACKAGED_RUNTIME_DIRECTORY)
            .join(request.target.platform_id())
            .join(manifest.packaged_python_relative_path);

        fs::remove_file(&python).unwrap();
        let missing = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("missing Python must fail");
        assert_eq!(missing.category, PlatformErrorCategory::NotFound);

        fs::write(&python, b"not an executable").unwrap();
        make_executable(&python);
        let corrupt = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("corrupt Python must fail");
        assert_eq!(corrupt.category, PlatformErrorCategory::IntegrityMismatch);

        fs::write(&python, binary_header(PlatformTarget::MacOsArm64)).unwrap();
        make_executable(&python);
        let wrong_arch = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("wrong architecture must fail");
        assert_eq!(
            wrong_arch.category,
            PlatformErrorCategory::IncompatibleArchitecture
        );
    }

    #[test]
    fn manifest_paths_reject_parent_and_absolute_components() {
        let mut manifest = expected_manifest(PlatformTarget::LinuxX64).unwrap();
        manifest.packaged_python_relative_path = PathBuf::from("../outside/python3");
        let parent = manifest
            .validate(PlatformTarget::LinuxX64)
            .expect_err("parent traversal must fail");
        assert_eq!(parent.category, PlatformErrorCategory::IntegrityMismatch);

        let mut manifest = expected_manifest(PlatformTarget::WindowsX64).unwrap();
        manifest.development_core_entry_relative_path =
            std::env::temp_dir().join("outside/core.py");
        let absolute = manifest
            .validate(PlatformTarget::WindowsX64)
            .expect_err("absolute path must fail");
        assert_eq!(absolute.category, PlatformErrorCategory::IntegrityMismatch);
    }

    #[cfg(unix)]
    #[test]
    fn unix_runtime_requires_an_executable_python_file() {
        use std::os::unix::fs::PermissionsExt;

        let target = current_platform_target().expect("native Unix test uses a formal target");
        let fixture = FixtureDirectory::new("permission-denied");
        let request = create_packaged_layout(&fixture, target);
        let manifest = expected_manifest(target).unwrap();
        let python = request
            .resource_directory
            .join(PACKAGED_RUNTIME_DIRECTORY)
            .join(target.platform_id())
            .join(manifest.packaged_python_relative_path);
        let mut permissions = fs::metadata(&python).unwrap().permissions();
        permissions.set_mode(0o644);
        fs::set_permissions(&python, permissions).unwrap();
        let error = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("non-executable Python must fail");
        assert_eq!(error.category, PlatformErrorCategory::PermissionDenied);
    }

    #[cfg(unix)]
    #[test]
    fn packaged_runtime_symlink_cannot_escape_the_resource_directory() {
        use std::os::unix::fs::symlink;

        let target = current_platform_target().expect("native Unix test uses a formal target");
        let fixture = FixtureDirectory::new("symlink-escape");
        let request = create_packaged_layout(&fixture, target);
        let controlled = request
            .resource_directory
            .join(PACKAGED_RUNTIME_DIRECTORY)
            .join(target.platform_id());
        let outside = fixture.path().join("outside-runtime");
        fs::rename(&controlled, &outside).unwrap();
        symlink(&outside, &controlled).unwrap();

        let error = FilesystemRuntimeLocator
            .locate_fixture(&request)
            .expect_err("packaged Runtime symlink escape must fail");
        assert_eq!(error.category, PlatformErrorCategory::IntegrityMismatch);
    }

    #[test]
    fn public_locator_rejects_a_target_other_than_the_current_build() {
        let current = current_platform_target().expect("tests run on a formal target");
        let wrong = PlatformTarget::ALL
            .into_iter()
            .find(|target| *target != current)
            .unwrap();
        let fixture = FixtureDirectory::new("wrong-target");
        let request = create_packaged_layout(&fixture, wrong);
        let error = FilesystemRuntimeLocator
            .locate(&request)
            .expect_err("public locator must reject another target");
        assert_eq!(
            error.category,
            PlatformErrorCategory::IncompatibleArchitecture
        );
    }

    #[test]
    #[ignore = "requires the exact target archive staged by the native platform CI job"]
    fn staged_repository_runtime_is_an_explicit_development_layout() {
        let target = current_platform_target().expect("tests run on a formal target");
        let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .canonicalize()
            .unwrap();
        let request = RuntimeLocationRequest {
            mode: RuntimeMode::ExplicitDevelopment,
            target,
            executable_directory: std::env::current_exe()
                .unwrap()
                .parent()
                .unwrap()
                .to_path_buf(),
            resource_directory: repo_root.clone(),
            explicit_development_root: Some(repo_root.clone()),
            assistant_root: repo_root.clone(),
        };
        let layout = FilesystemRuntimeLocator.locate(&request).unwrap();
        let manifest = expected_manifest(target).unwrap();
        assert_eq!(layout.resource_root, repo_root);
        assert_eq!(layout.assistant_root, repo_root);
        assert_eq!(layout.working_directory, repo_root);
        assert_eq!(layout.architecture, target.architecture());
        assert_eq!(
            layout.core_entry,
            fs::canonicalize(repo_root.join(manifest.development_core_entry_relative_path))
                .unwrap()
        );
        assert_eq!(
            layout.python_executable,
            fs::canonicalize(repo_root.join(manifest.development_python_relative_path)).unwrap()
        );
        assert_eq!(layout.core_module, "app.core_host");
        assert_eq!(layout.source_id, manifest.source_id);
    }
}
