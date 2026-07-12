from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def keep_qapplication_alive(qapp: Any) -> Iterable[None]:
    """让 UI 测试全程持有 QApplication，避免 pytest-qt 处理事件时对象已被回收。"""
    _ = qapp
    yield
