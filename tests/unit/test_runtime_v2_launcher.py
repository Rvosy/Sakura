from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
from pathlib import Path

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
