param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-3-06 real-process compatibility acceptance requires Windows."
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the WP-3-06 debug Tauri candidate."
    }
    & runtime\python.exe tests\fixtures\runtime_v2\wp_3_06\acceptance_driver.py
    if ($LASTEXITCODE -ne 0) {
        throw "WP-3-06 real-process compatibility acceptance failed."
    }
}
finally {
    Pop-Location
}
