use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum PlatformTarget {
    WindowsX64,
    MacOsArm64,
    LinuxX64,
}

impl PlatformTarget {
    pub const ALL: [Self; 3] = [Self::WindowsX64, Self::MacOsArm64, Self::LinuxX64];

    pub const fn rust_triple(self) -> &'static str {
        match self {
            Self::WindowsX64 => "x86_64-pc-windows-msvc",
            Self::MacOsArm64 => "aarch64-apple-darwin",
            Self::LinuxX64 => "x86_64-unknown-linux-gnu",
        }
    }

    pub const fn platform_id(self) -> &'static str {
        match self {
            Self::WindowsX64 => "windows-x64",
            Self::MacOsArm64 => "macos-arm64",
            Self::LinuxX64 => "linux-x64",
        }
    }

    pub fn from_rust_triple(value: &str) -> Option<Self> {
        Self::ALL
            .into_iter()
            .find(|target| target.rust_triple() == value)
    }
}

pub fn current_platform_target() -> Option<PlatformTarget> {
    #[cfg(all(target_os = "windows", target_arch = "x86_64"))]
    {
        return Some(PlatformTarget::WindowsX64);
    }
    #[cfg(all(target_os = "macos", target_arch = "aarch64"))]
    {
        return Some(PlatformTarget::MacOsArm64);
    }
    #[cfg(all(target_os = "linux", target_arch = "x86_64"))]
    {
        return Some(PlatformTarget::LinuxX64);
    }
    #[allow(unreachable_code)]
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formal_targets_have_stable_unique_ids_and_rust_triples() {
        let ids = PlatformTarget::ALL.map(PlatformTarget::platform_id);
        let triples = PlatformTarget::ALL.map(PlatformTarget::rust_triple);
        assert_eq!(ids, ["windows-x64", "macos-arm64", "linux-x64"]);
        assert_eq!(
            triples,
            [
                "x86_64-pc-windows-msvc",
                "aarch64-apple-darwin",
                "x86_64-unknown-linux-gnu"
            ]
        );
        for target in PlatformTarget::ALL {
            assert_eq!(
                PlatformTarget::from_rust_triple(target.rust_triple()),
                Some(target)
            );
        }
        assert_eq!(
            PlatformTarget::from_rust_triple("x86_64-apple-darwin"),
            None
        );
    }

    #[test]
    fn the_current_formal_build_reports_its_frozen_target() {
        let target = current_platform_target().expect("tests run on a Phase 1P formal target");
        assert!(PlatformTarget::ALL.contains(&target));
    }
}
