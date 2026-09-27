from __future__ import annotations

import base64
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def windows_launcher_probe(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("Windows launcher subprocess contract")
    root = tmp_path_factory.mktemp("launcher-probe")
    source = root / "probe.cs"
    executable = root / "sakura.exe"
    source.write_text(
        """using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Text;
class LauncherProbe {
    static int Main() {
        var lines = new List<string>();
        foreach (DictionaryEntry item in Environment.GetEnvironmentVariables()) {
            string key = (string)item.Key;
            string lower = key.ToLowerInvariant();
            if (lower == "http_proxy" || lower == "https_proxy" || lower == "all_proxy"
                    || lower == "no_proxy" || key == "SAKURA_RUNTIME_USER_ROOT") {
                lines.Add(key + "\\t" + Convert.ToBase64String(Encoding.UTF8.GetBytes((string)item.Value)));
            }
        }
        lines.Add("cwd\\t" + Convert.ToBase64String(Encoding.UTF8.GetBytes(Environment.CurrentDirectory)));
        File.WriteAllLines(Environment.GetEnvironmentVariable("SAKURA_LAUNCHER_PROBE_OUTPUT"), lines, Encoding.UTF8);
        return 17;
    }
}
""",
        encoding="utf-8",
    )
    compiled = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "Add-Type -Path $env:SAKURA_PROBE_SOURCE -OutputAssembly $env:SAKURA_PROBE_EXE -OutputType ConsoleApplication"],
        env={**os.environ, "SAKURA_PROBE_SOURCE": str(source), "SAKURA_PROBE_EXE": str(executable)},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    return executable


@pytest.mark.parametrize("entrypoint", ["ps1", "bat"])
@pytest.mark.parametrize("use_environment_proxy", [False, True])
def test_windows_existing_data_launcher_scopes_proxy_choice_to_child(
    tmp_path: Path, windows_launcher_probe: Path, entrypoint: str, use_environment_proxy: bool,
) -> None:
    source_root = Path(__file__).resolve().parents[2]
    project_root = tmp_path / "Sakura source"
    scripts = project_root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("start-existing-data.ps1", "start-existing-data.bat"):
        shutil.copy2(source_root / "scripts" / name, scripts / name)
    executable = project_root / "desktop/src-tauri/target/debug/sakura.exe"
    executable.parent.mkdir(parents=True)
    shutil.copy2(windows_launcher_probe, executable)
    user_root = tmp_path / "existing data"
    user_root.mkdir()
    observed = tmp_path / "child-environment.txt"
    parent_observed = tmp_path / "parent-environment.json"
    proxies = {
        "Http_Proxy": "http://proxy-fixture.invalid:1111",
        "HTTPS_PROXY": "http://proxy-fixture.invalid:2222",
        "all_proxy": "socks5://proxy-fixture.invalid:3333",
        "nO_pRoXy": "localhost,fixture.invalid",
    }
    proxy_names = {name.lower() for name in proxies}
    environment = {key: value for key, value in os.environ.items() if key.lower() not in proxy_names}
    environment.update(proxies)
    environment.update({
        "SAKURA_RUNTIME_USER_ROOT": "parent-root-sentinel",
        "SAKURA_LAUNCHER_PROBE_OUTPUT": str(observed),
        "SAKURA_LAUNCHER_PARENT_OUTPUT": str(parent_observed),
        "SAKURA_LAUNCHER_SCRIPT": str(scripts / f"start-existing-data.{entrypoint}"),
        "SAKURA_LAUNCHER_USER_ROOT": str(user_root),
    })
    switch = " -UseEnvironmentProxy" if use_environment_proxy else ""
    if entrypoint == "ps1":
        wrapper = tmp_path / "invoke-launcher.ps1"
        wrapper.write_text(
            """$ErrorActionPreference = 'Stop'
function TestEnvironment {
    $values = @{}
    foreach ($name in @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'SAKURA_RUNTIME_USER_ROOT')) {
        $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    return $values
}
$before = TestEnvironment
$beforeDirectory = (Get-Location).Path
& $env:SAKURA_LAUNCHER_SCRIPT -UserRoot $env:SAKURA_LAUNCHER_USER_ROOT""" + switch + """
$code = $LASTEXITCODE
@{before=$before; after=(TestEnvironment); beforeDirectory=$beforeDirectory; afterDirectory=(Get-Location).Path} |
    ConvertTo-Json | Set-Content -LiteralPath $env:SAKURA_LAUNCHER_PARENT_OUTPUT -Encoding UTF8
exit $code
""",
            encoding="utf-8-sig",
        )
        command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)]
    else:
        # cmd.exe owns the outer command quoting; list2cmdline would escape
        # these embedded quotes with backslashes, which cmd does not consume.
        command = f'cmd.exe /d /s /c ""{scripts / "start-existing-data.bat"}" -UserRoot "{user_root}"{switch}"'
    completed = subprocess.run(
        command, cwd=tmp_path, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert observed.exists(), completed.stdout + completed.stderr
    child = {
        key.lower(): base64.b64decode(value).decode("utf-8")
        for key, value in (line.split("\t", 1) for line in observed.read_text(encoding="utf-8-sig").splitlines())
    }
    assert {key: value for key, value in child.items() if key in proxy_names} == (
        {key.lower(): value for key, value in proxies.items()} if use_environment_proxy else {}
    )
    assert Path(child["sakura_runtime_user_root"]) == user_root
    assert Path(child["cwd"]) == project_root
    assert completed.returncode == 17, completed.stdout + completed.stderr
    if entrypoint == "ps1":
        parent = json.loads(parent_observed.read_text(encoding="utf-8-sig"))
        assert parent["after"] == parent["before"]
        assert parent["afterDirectory"] == parent["beforeDirectory"] == str(tmp_path)


def test_macos_development_wrapper_installs_application_icon(tmp_path: Path) -> None:
    if os.name != "posix":
        return

    source_root = Path(__file__).resolve().parents[2]
    project_root = tmp_path / "Sakura"
    script = project_root / "scripts" / "start.sh"
    source_icon = source_root / "desktop" / "src-tauri" / "icons" / "icon.icns"
    copied_icon = project_root / "desktop" / "src-tauri" / "icons" / "icon.icns"
    shell = project_root / "desktop" / "src-tauri" / "target" / "debug" / "sakura"
    shim_root = tmp_path / "bin"

    script.parent.mkdir(parents=True)
    copied_icon.parent.mkdir(parents=True)
    shell.parent.mkdir(parents=True)
    shim_root.mkdir()
    shutil.copy2(source_root / "scripts" / "start.sh", script)
    shutil.copy2(source_icon, copied_icon)
    shell.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shell.chmod(0o755)
    for name, body in (("cargo", "#!/bin/sh\nexit 0\n"), ("uname", "#!/bin/sh\nprintf 'Darwin\\n'\n")):
        shim = shim_root / name
        shim.write_text(body, encoding="utf-8")
        shim.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = f"{shim_root}{os.pathsep}{environment['PATH']}"
    completed = subprocess.run(
        ["/bin/bash", str(script)],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    app_contents = shell.parent / ".sakura-dev" / "Sakura Runtime v2.app" / "Contents"
    with (app_contents / "Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleIconFile"] == "Sakura.icns"
    assert (app_contents / "Resources" / "Sakura.icns").read_bytes() == source_icon.read_bytes()
