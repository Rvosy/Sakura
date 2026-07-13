"""旧 Qt 设置桥的兼容模块别名；实现已迁移到无 UI Core。"""

from __future__ import annotations

import sys

from app.core import settings_resource_tasks as _implementation


sys.modules[__name__] = _implementation
