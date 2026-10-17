"""在隔离目录执行真实 NSIS 安装钩子，核对统计选择和已有配置保留。"""

import json
import os
from pathlib import Path
import subprocess

import pytest


@pytest.fixture(scope="module")
def installer(tmp_path_factory):
    if os.name != "nt":
        pytest.skip("需要 Windows NSIS")
    compiler = Path(os.environ["LOCALAPPDATA"]) / "tauri/NSIS/makensis.exe"
    if not compiler.exists():
        pytest.skip("未安装 NSIS")
    root = tmp_path_factory.mktemp("installer")
    executable = root / "installer.exe"
    hooks = Path(__file__).resolve().parents[2] / "desktop/src-tauri/installer-hooks.nsh"
    script = root / "installer.nsi"
    script.write_text(fr'''Unicode true
!include MUI2.nsh
!include FileFunc.nsh
!include "{hooks}"
Name "Sakura installer test"
OutFile "{executable}"
RequestExecutionLevel user
Var UpdateMode
Var PassiveMode
Var ProbeResult
Var ModeProbe
!undef MUI_PAGE_CUSTOMFUNCTION_SHOW
!define MUI_PAGE_CUSTOMFUNCTION_SHOW TestWelcomeShow
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
Function .onInit
  ${{GetOptions}} $CMDLINE "/CHOICE=" $SakuraTelemetryChoice
  ${{GetOptions}} $CMDLINE "/UPDATE=" $UpdateMode
  ${{GetOptions}} $CMDLINE "/PROBE=" $ProbeResult
  ${{GetOptions}} $CMDLINE "/PASSIVE=" $PassiveMode
  ${{GetOptions}} $CMDLINE "/MODEPROBE=" $ModeProbe
FunctionEnd
Function TestWelcomeShow
  ${{If}} $ModeProbe != ""
    FileOpen $2 $ModeProbe w
    FileWriteUTF16LE $2 "$PassiveMode$\r$\n$INSTDIR"
    FileClose $2
    Quit
  ${{EndIf}}
  Call SakuraPrivacyShow
  ${{If}} $ProbeResult != ""
    ${{NSD_GetState}} $SakuraTelemetryCheckbox $1
    FileOpen $2 $ProbeResult w
    FileWrite $2 "$1"
    ${{NSD_Uncheck}} $SakuraTelemetryCheckbox
    Call SakuraPrivacyLeave
    Call SakuraPrivacyShow
    ${{NSD_GetState}} $SakuraTelemetryCheckbox $1
    FileWrite $2 "$1"
    FileClose $2
    Quit
  ${{EndIf}}
FunctionEnd
Section
  !insertmacro NSIS_HOOK_POSTINSTALL
SectionEnd
''', encoding="utf-8")
    subprocess.run([str(compiler), "/V2", str(script)], check=True)
    return executable


def test_welcome_defaults_checked_and_remembers_unchecked_choice(installer, tmp_path):
    result = tmp_path / "checkbox.txt"
    startup = subprocess.STARTUPINFO()
    startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    subprocess.run([str(installer), f"/PROBE={result}"], check=True, startupinfo=startup, timeout=15)
    assert result.read_text() == "10"


@pytest.mark.parametrize("enabled", [True, False])
def test_install_saves_choice_before_first_launch(installer, tmp_path, enabled):
    destination = tmp_path / "安装目录"
    subprocess.run([str(installer), "/S", f"/CHOICE={int(enabled)}", f"/D={destination}"], check=True)
    document = json.loads((destination / "config/ui.json").read_text(encoding="utf-8"))
    assert document["settings"]["telemetry"]["enabled"] is enabled


@pytest.mark.parametrize("update", [False, True])
def test_reinstall_preserves_existing_user_settings(installer, tmp_path, update):
    config = tmp_path / "config/ui.json"
    config.parent.mkdir()
    saved = '{"schema_version":1,"domain":"ui","settings":{"telemetry":{"enabled":false},"theme":"dark"}}'
    config.write_text(saved, encoding="utf-8")
    subprocess.run([str(installer), "/S", "/CHOICE=1", f"/UPDATE={int(update)}", f"/D={tmp_path}"], check=True)
    assert config.read_text(encoding="utf-8") == saved


@pytest.mark.parametrize("explicit_directory", [False, True])
def test_automatic_upgrade_selects_directory_for_old_clients(installer, tmp_path, explicit_directory):
    result = tmp_path / "upgrade-mode.txt"
    destination = tmp_path / "Sakura 空格目录"
    arguments = [str(installer), "/PASSIVE=1", "/UPDATE=1", f"/MODEPROBE={result}"]
    if explicit_directory:
        arguments.extend(["/SAKURA_CURRENT_DIR", f"/D={destination}"])
    # NSIS consumes /D through its own parser: it must remain the unquoted final argument.
    command = subprocess.list2cmdline(arguments[:-1]) + " " + arguments[-1] if explicit_directory else subprocess.list2cmdline(arguments)
    subprocess.run(command, check=True, timeout=15)
    lines = result.read_text(encoding="utf-16-le").splitlines()
    assert lines[0] == ("1" if explicit_directory else "0")
    if explicit_directory:
        assert lines[1] == str(destination)
