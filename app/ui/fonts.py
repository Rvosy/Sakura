from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase


def _rounded_japanese_font(pixel_size: int, weight: QFont.Weight) -> QFont:
    family = _choose_font_family([
        "BIZ UDPGothic",
        "Meiryo",
        "Yu Gothic UI",
        "Yu Gothic",
        "MS PGothic",
        "Microsoft YaHei UI",
        "Segoe UI",
    ])
    font = QFont(family)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _rounded_chinese_font(pixel_size: int, weight: QFont.Weight) -> QFont:
    family = _choose_font_family([
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
        "SimHei",
        "Segoe UI",
    ])
    font = QFont(family)
    font.setPixelSize(pixel_size)
    font.setWeight(weight)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def _choose_font_family(candidates: list[str]) -> str:
    available = set(QFontDatabase.families())
    for candidate in candidates:
        if candidate in available:
            return candidate
    return candidates[-1]
