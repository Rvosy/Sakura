"""Real Core bridge + two real plugin processes, launched by the Rust journey.

The only argument is an isolated temporary root owned by the calling test.
"""

import sys
from pathlib import Path

from app.agent.tools import ToolRegistry
from app.core.runtime_log import log_message
from app.core_host.plugin_application import PluginApplicationHost
from app.core_host.runtime_logging import install_runtime_logging
from app.storage.runtime_roots import RuntimeRoots

root = Path(sys.argv[1])
roots = RuntimeRoots(root / "distribution", root / "user")
roots.user_root.mkdir(parents=True)
for name in ("one", "two"):
    plugin_root = roots.distribution_root / "plugins" / "builtin" / name
    plugin_root.mkdir(parents=True)
    (plugin_root / "plugin.yaml").write_text(
        f"api: 4\nid: fixture.{name}\nname: 日志示例\nversion: 1.0.0\n"
        "entry: plugin:Plugin\nprovides: []\nrequires: [sakura.host.logging]\n",
        encoding="utf-8",
    )
    (plugin_root / "plugin.py").write_text('''
class Plugin:
    def setup(self, context):
        logger = context.get("sakura.host.logging")
        logger.info("插件资源加载完成", fields={"elapsed_ms": 320, "nested": {"count": 2}})
        context.effect(lambda: logger.info("插件清理完成"))
''', encoding="utf-8")

bridge = install_runtime_logging()
host = PluginApplicationHost(roots, "generation-unified-test", ToolRegistry())
try:
    log_message("info", "Core 资源加载完成", fields={"count": 2})
    host.start()
    assert host.application.wait_until_loaded(timeout=3)
finally:
    host.close()
    bridge.close()
