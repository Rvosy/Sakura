use serde::{Deserialize, Serialize};

use super::RuntimeArchitecture;

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq, Hash, Serialize)]
pub enum PlatformTarget {
    #[serde(rename = "windows-x64")]
    WindowsX64,
    #[serde(rename = "macos-arm64")]
    MacOsArm64,
    #[serde(rename = "linux-x64")]
    LinuxX64,
}

impl PlatformTarget {
    #[cfg(test)]
    pub const ALL: [Self; 3] = [Self::WindowsX64, Self::MacOsArm64, Self::LinuxX64];

    pub const fn platform_id(self) -> &'static str {
        match self {
            Self::WindowsX64 => "windows-x64",
            Self::MacOsArm64 => "macos-arm64",
            Self::LinuxX64 => "linux-x64",
        }
    }

    pub const fn architecture(self) -> RuntimeArchitecture {
        match self {
            Self::WindowsX64 | Self::LinuxX64 => RuntimeArchitecture::X64,
            Self::MacOsArm64 => RuntimeArchitecture::Arm64,
        }
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
