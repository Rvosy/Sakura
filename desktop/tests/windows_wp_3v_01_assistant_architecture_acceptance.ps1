param()

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $IsWindows) {
    throw "WP-3V-01 real Assistant architecture acceptance requires Windows."
}

Push-Location $repo
try {
    cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to build the WP-3V-01 debug Tauri candidate."
    }
    & runtime\python.exe tests\fixtures\runtime_v2\wp_3v_01\acceptance_driver.py
    if ($LASTEXITCODE -ne 0) {
        throw "WP-3V-01 real Assistant architecture acceptance failed."
    }
}
finally {
    Pop-Location
}
