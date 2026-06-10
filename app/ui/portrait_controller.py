from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPropertyAnimation,
    QSequentialAnimationGroup,
    Qt,
)
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QMessageBox, QWidget

from app.config.character_loader import CharacterProfile
from app.llm.chat_reply import ChatSegment
from app.ui.portrait_utils import should_crossfade_portrait


PORTRAIT_TRANSITION_MS = 300
# 两张立绘同时叠加淡入淡出的比例：1.0 为完全同步交叉，0.0 为先淡出再淡入。
PORTRAIT_CROSSFADE_OVERLAP = 0.8
PORTRAIT_BASE_MAX_WIDTH = 560
PORTRAIT_BASE_MAX_HEIGHT = 570
PORTRAIT_SCALE_MIN_PERCENT = 50
PORTRAIT_SCALE_MAX_PERCENT = 150
PORTRAIT_SCALE_DEFAULT_PERCENT = 100


def normalize_portrait_scale_percent(value: object) -> int:
    """把配置里的立绘缩放百分比规整到允许范围。"""

    try:
        percent = int(str(value).strip())
    except (TypeError, ValueError):
        return PORTRAIT_SCALE_DEFAULT_PERCENT
    return max(PORTRAIT_SCALE_MIN_PERCENT, min(PORTRAIT_SCALE_MAX_PERCENT, percent))


class PortraitController(QObject):
    """负责立绘资源缓存、分段表情切换和淡入淡出动画。"""

    def __init__(
        self,
        *,
        profile: CharacterProfile,
        parent_widget: QWidget,
        main_label: QLabel,
        transition_label: QLabel,
        main_opacity_effect: QGraphicsOpacityEffect,
        transition_opacity_effect: QGraphicsOpacityEffect,
        stage_size: tuple[int, int],
        relayout: Callable[[], None],
        raise_foreground: Callable[[], None],
        on_portrait_changed: Callable[[QPixmap], None],
        portrait_scale_percent: int = PORTRAIT_SCALE_DEFAULT_PERCENT,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.profile = profile
        self.parent_widget = parent_widget
        self.main_label = main_label
        self.transition_label = transition_label
        self.main_opacity_effect = main_opacity_effect
        self.transition_opacity_effect = transition_opacity_effect
        self.stage_size = stage_size
        self.portrait_scale_percent = normalize_portrait_scale_percent(portrait_scale_percent)
        self._relayout = relayout
        self._raise_foreground = raise_foreground
        self._on_portrait_changed = on_portrait_changed

        self.current_path = profile.default_portrait_path
        self.pixmap_cache: dict[Path, QPixmap] = {}
        self.pixmap = self.load_portrait()
        self.transition_animation: QAbstractAnimation | None = None
        self.transition_id = 0

    def apply_current(self) -> None:
        # 主窗口几何统一由 PetWindow 的统一布局模型管理（见 _apply_pet_layout）。
        # 这里只负责把当前立绘贴到主标签并重新布局，绝不再 resize 主窗口，
        # 避免左上锚点 resize 与底边锚点几何相互打架产生偶发跳闪。
        self._stop_transition()
        if self.pixmap.isNull():
            return

        self._apply_pixmap_to_label(self.main_label, self.pixmap)
        self._relayout()

    def set_stage_size(self, stage_size: tuple[int, int]) -> None:
        self.stage_size = stage_size

    def set_portrait_scale_percent(self, portrait_scale_percent: int) -> None:
        self.portrait_scale_percent = normalize_portrait_scale_percent(portrait_scale_percent)

    def set_profile(self, profile: CharacterProfile) -> QPixmap:
        self.profile = profile
        self.current_path = profile.default_portrait_path
        self.pixmap = self.load_portrait()
        self.apply_current()
        self._on_portrait_changed(self.pixmap)
        return self.pixmap

    def preload_for_segment(self, segment: ChatSegment) -> None:
        next_portrait_path = self.profile.portrait_for_segment(segment.portrait, segment.tone)
        if next_portrait_path not in self.pixmap_cache:
            self.load_portrait(next_portrait_path)

    def apply_for_segment(self, segment: ChatSegment) -> None:
        next_portrait_path = self.profile.portrait_for_segment(segment.portrait, segment.tone)
        if next_portrait_path == self.current_path:
            return

        should_crossfade = should_crossfade_portrait(self.current_path, next_portrait_path)
        next_pixmap = self.load_portrait(next_portrait_path)
        self.current_path = next_portrait_path
        if should_crossfade:
            self._crossfade(next_pixmap)
        else:
            self.pixmap = next_pixmap
            self.apply_current()
        self._on_portrait_changed(self.pixmap)

    def load_portrait(self, portrait_path: Path | None = None) -> QPixmap:
        target_path = portrait_path or self.current_path
        cached = self.pixmap_cache.get(target_path)
        if cached is not None:
            return cached

        pixmap = QPixmap(str(target_path))
        if pixmap.isNull():
            QMessageBox.critical(
                self.parent_widget,
                "立绘加载失败",
                f"无法加载立绘：{target_path}",
            )
        self.pixmap_cache[target_path] = pixmap
        return pixmap

    def _apply_pixmap_to_label(self, label: QLabel, pixmap: QPixmap) -> None:
        scale = self.portrait_scale_percent / 100
        target_width = round(PORTRAIT_BASE_MAX_WIDTH * scale)
        target_height = round(PORTRAIT_BASE_MAX_HEIGHT * scale)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        label.setPixmap(scaled)
        label.resize(scaled.size())

    def _crossfade(self, next_pixmap: QPixmap) -> None:
        self._stop_transition(finish_current=True)
        self.pixmap = next_pixmap
        if self.pixmap.isNull():
            self.apply_current()
            return

        self._apply_pixmap_to_label(self.transition_label, self.pixmap)
        # 不再 resize 主窗口：交叉淡入期间舞台尺寸不变，几何由 PetWindow 收口。
        self._relayout()
        self.main_opacity_effect.setOpacity(1.0)
        self.transition_opacity_effect.setOpacity(0.0)
        self.transition_label.show()
        self.transition_label.raise_()
        self._raise_foreground()

        self.transition_id += 1
        transition_id = self.transition_id
        overlap = max(0.0, min(1.0, PORTRAIT_CROSSFADE_OVERLAP))
        fade_duration = max(1, round(PORTRAIT_TRANSITION_MS / (2.0 - overlap)))
        overlap_duration = round(fade_duration * overlap)
        fade_in_delay = max(0, fade_duration - overlap_duration)

        animation = QParallelAnimationGroup(self)

        fade_out = QPropertyAnimation(self.main_opacity_effect, b"opacity")
        fade_out.setDuration(fade_duration)
        fade_out.setStartValue(1.0)
        fade_out.setEndValue(0.0)
        fade_out.setEasingCurve(QEasingCurve.Type.InOutQuad)

        fade_in = QPropertyAnimation(self.transition_opacity_effect, b"opacity")
        fade_in.setDuration(fade_duration)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.Type.InOutQuad)

        fade_in_sequence = QSequentialAnimationGroup()
        if fade_in_delay > 0:
            fade_in_sequence.addAnimation(QPauseAnimation(fade_in_delay))
        fade_in_sequence.addAnimation(fade_in)

        animation.addAnimation(fade_out)
        animation.addAnimation(fade_in_sequence)
        animation.finished.connect(lambda: self._finish_transition(transition_id))
        self.transition_animation = animation
        animation.start()

    def _stop_transition(self, finish_current: bool = False) -> None:
        if self.transition_animation is not None:
            self.transition_animation.stop()
            self.transition_animation.deleteLater()
            self.transition_animation = None
            self.transition_id += 1
        self.transition_label.hide()
        self.transition_label.clear()
        self.main_opacity_effect.setOpacity(1.0)
        self.transition_opacity_effect.setOpacity(0.0)
        if finish_current and not self.pixmap.isNull():
            self._apply_pixmap_to_label(self.main_label, self.pixmap)
            self._relayout()

    def _finish_transition(self, transition_id: int) -> None:
        if transition_id != self.transition_id:
            return
        if self.transition_animation is not None:
            self.transition_animation.deleteLater()
            self.transition_animation = None
        self._apply_pixmap_to_label(self.main_label, self.pixmap)
        self.transition_label.hide()
        self.transition_label.clear()
        self.main_opacity_effect.setOpacity(1.0)
        self.transition_opacity_effect.setOpacity(0.0)
        self._relayout()
