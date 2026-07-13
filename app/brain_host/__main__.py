from __future__ import annotations

import os
import sys

# 必须在导入 bootstrap / AppContext 图之前设置，使兼容资源模块不加载 Qt。
os.environ.setdefault("SAKURA_HEADLESS", "1")

from app.brain_host.application import BrainHostApplication, BrainHostConfig  # noqa: E402
from app.brain_host.errors import BrainHostError  # noqa: E402
from app.brain_host.protocol import ProtocolError  # noqa: E402
from app.brain_host.server import run_server  # noqa: E402
from app.brain_host.transport import FramedTransport  # noqa: E402


def main() -> int:
    try:
        config = BrainHostConfig.from_environment(os.environ)
    except BrainHostError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 2

    application = BrainHostApplication(config)
    application.initialize()
    transport = FramedTransport(sys.stdin.buffer, sys.stdout.buffer)
    try:
        run_server(application, transport)
    except ProtocolError as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
