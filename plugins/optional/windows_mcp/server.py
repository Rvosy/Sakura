"""Start the released upstream server in its own interpreter and dependency root."""
import os
from pathlib import Path
import sys


def main():
    dependency = Path(sys.argv[1]).resolve(strict=True)
    config_path = str(Path(sys.argv[2]).resolve(strict=True))
    os.environ["ANONYMIZED_TELEMETRY"] = "false"
    os.environ["POSTHOG_API_KEY"] = ""
    sys.path[:0] = [str(dependency), str(dependency / "win32"), str(dependency / "win32/lib"), str(dependency / "pythonwin")]
    dlls = os.add_dll_directory(str(dependency / "pywin32_system32"))
    # -I ignores Python environment flags; set pipe encodings explicitly.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    from windows_mcp.__main__ import main as upstream
    try:
        upstream(["serve", "--transport", "stdio", "--config", config_path], prog_name="windows-mcp")
    finally:
        dlls.close()


if __name__ == "__main__":
    main()
