from __future__ import annotations

import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tools.release.verify_updater_signature import verify


ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="需要 PowerShell 7")

# 模拟正式验签及拒绝路径；测试证书的真实密码学验签由 Windows 诊断任务覆盖。
SIGNATURE_RESULT = r'''
function Get-AuthenticodeSignature {
    param([string]$LiteralPath)
    $certificate = [pscustomobject]@{ Thumbprint = 'TEST-CERT' }
    $certificate | Add-Member ScriptMethod GetNameInfo { param($type, $issuer) $env:TEST_PUBLISHER }
    [pscustomobject]@{
        SignerCertificate = $certificate
        TimeStamperCertificate = if ($env:TEST_TIMESTAMP -eq 'yes') { $certificate } else { $null }
        Status = $env:TEST_STATUS
        StatusMessage = 'fixture verification result'
    }
}
'''


@pytest.mark.parametrize(
    ("status", "thumbprint", "publisher", "timestamp", "accepted"),
    [
        ("Valid", "", "SignPath Foundation", "yes", True),
        ("NotTrusted", "TEST-CERT", "Test certificate", "yes", False),
        ("UnknownError", "", "SignPath Foundation", "yes", False),
        ("NotTrusted", "", "SignPath Foundation", "yes", False),
        ("HashMismatch", "TEST-CERT", "Test certificate", "yes", False),
        ("Valid", "OTHER-CERT", "Test certificate", "yes", False),
        ("Valid", "", "Other publisher", "yes", False),
        ("Valid", "", "SignPath Foundation", "no", False),
    ],
)
def test_windows_signing_rejects_untrusted_or_wrong_release_identity(
    tmp_path: Path, status: str, thumbprint: str, publisher: str,
    timestamp: str, accepted: bool,
) -> None:
    script = tmp_path / "verify.ps1"
    script.write_text(
        SIGNATURE_RESULT
        + "\n& $env:TEST_VERIFIER -Path candidate.exe -TestCertificateThumbprint $env:TEST_THUMBPRINT\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)],
        env={**os.environ, "TEST_VERIFIER": str(ROOT / "tools/release/verify_windows_signature.ps1"),
             "TEST_STATUS": status, "TEST_THUMBPRINT": thumbprint,
             "TEST_PUBLISHER": publisher, "TEST_TIMESTAMP": timestamp},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="需要 Windows Authenticode")
def test_unsigned_windows_artifact_is_rejected(tmp_path: Path) -> None:
    artifact = tmp_path / "unsigned.exe"
    artifact.write_bytes(b"MZ unsigned fixture")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(ROOT / "tools/release/verify_windows_signature.ps1"),
         "-Path", str(artifact)],
        capture_output=True,
    )
    assert result.returncode != 0


def test_windows_collection_signs_the_final_installer_bytes(tmp_path: Path) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    collection = next(step["run"] for step in workflow["jobs"]["bundle"]["steps"]
                      if step.get("name") == "Collect Windows release artifacts")
    tools = tmp_path / "tools/release"
    tools.mkdir(parents=True)
    for name in ("verify_windows_signature.ps1", "verify_updater_signature.py"):
        shutil.copy2(ROOT / "tools/release" / name, tools / name)
    signed = tmp_path / "signed-installer/Sakura-setup.exe"
    signed.parent.mkdir()
    signed.write_bytes(b"MZ final bytes returned by SignPath")
    unsigned = tmp_path / "desktop/src-tauri/target/release/bundle/nsis/setup.exe"
    unsigned.parent.mkdir(parents=True)
    unsigned.write_bytes(b"MZ original unsigned bytes")
    unsigned.with_suffix(".exe.sig").write_text("obsolete signature", encoding="utf-8")

    seed = bytes(range(32))
    public = Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    public_key = base64.b64encode(
        b"untrusted comment: test key\n" + base64.b64encode(b"Ed" + b"12345678" + public) + b"\n"
    ).decode()
    signer = tmp_path / "sign.py"
    signer.write_text('''import base64
import sys
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
path = Path(sys.argv[1])
signature = key.sign(path.read_bytes())
comment = b"test installer"
document = b"untrusted comment: test signature\\n" + base64.b64encode(b"Ed" + b"12345678" + signature) + b"\\ntrusted comment: " + comment + b"\\n" + base64.b64encode(key.sign(signature + comment)) + b"\\n"
Path(str(path) + ".sig").write_text(base64.b64encode(document).decode(), encoding="utf-8")
''', encoding="utf-8")
    script = tmp_path / "collect.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n" + SIGNATURE_RESULT + r'''
function npx {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    & $env:TEST_PYTHON $env:TEST_SIGNER $Arguments[-1]
}
function python {
    param([Parameter(ValueFromRemainingArguments)][string[]]$Arguments)
    & $env:TEST_PYTHON @Arguments
}
''' + collection + "\nif ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(script)], cwd=tmp_path,
        env={**os.environ, "TEST_PYTHON": sys.executable, "TEST_SIGNER": str(signer),
             "TEST_STATUS": "Valid", "TEST_PUBLISHER": "SignPath Foundation", "TEST_TIMESTAMP": "yes",
             "SIGNPATH_TEST_CERTIFICATE_THUMBPRINT": "", "VERSION": "1.2.3",
             "SAKURA_UPDATER_PUBLIC_KEY": public_key},
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "artifacts/Sakura-1.2.3-windows-x64-setup.exe"
    assert output.read_bytes() == signed.read_bytes()
    verify(public_key, output, output.with_suffix(".exe.sig").read_text(encoding="utf-8"))
