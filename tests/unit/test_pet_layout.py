"""compute_pet_layout 单窗口布局模型的纯函数单测。

覆盖：默认参数逐像素复刻重构前几何、极端 input_offset/vertical_offset 的包围盒包含性、
MIN_STAGE_HEIGHT 钳制、以及「立绘底边锚点」不变量。
"""

from __future__ import annotations

import pytest

from app.ui.control_panel_layout import (
    INPUT_BAR_HEIGHT,
    MIN_STAGE_HEIGHT,
    PORTRAIT_BOTTOM_PAD,
    STAGE_WIDTH_PANEL_PAD,
    PetLayout,
    compute_pet_layout,
)


def _portrait_bottom(layout: PetLayout) -> int:
    x, y, w, h = layout.portrait_rect
    return y + h


def _rect_bottom(rect: tuple[int, int, int, int]) -> int:
    return rect[1] + rect[3]


def test_default_layout_geometry() -> None:
    """默认参数（立绘 560x570、控制组 640、气泡 128、偏移 0）的单窗口几何。

    宽度为「立绘/控制组较宽者 + 边距」的动态包络（640+96=736），不再固定 860;
    纵向几何（立绘底边锚点、各元素相对位置）仍复刻重构前。
    """
    layout = compute_pet_layout(portrait_width=560, portrait_height=570)

    assert layout.window_size == (736, 640)
    # 立绘水平居中、顶部留白 8、底边距窗口底 62。
    assert layout.portrait_rect == (88, 8, 560, 570)
    assert _portrait_bottom(layout) == 578
    assert layout.window_size[1] - _portrait_bottom(layout) == PORTRAIT_BOTTOM_PAD
    assert layout.portrait_anchor == (368, 578)
    # 气泡：宽 640 居中，底边在立绘底边上方 84。
    assert layout.bubble_rect == (48, 366, 640, 128)
    assert _rect_bottom(layout.bubble_rect) == 578 - 84
    # 输入栏：与气泡同宽同 x，落在窗口内。
    assert layout.input_rect == (48, 504, 640, 52)
    assert _rect_bottom(layout.input_rect) <= layout.window_size[1]


def test_bubble_and_input_share_x_and_width() -> None:
    layout = compute_pet_layout(
        portrait_width=560, portrait_height=570, control_panel_width=520
    )
    assert layout.bubble_rect[0] == layout.input_rect[0]
    assert layout.bubble_rect[2] == layout.input_rect[2]


@pytest.mark.parametrize("input_offset", [0, 50, 120, 200])
@pytest.mark.parametrize("vertical_offset", [-200, -80, 0, 120, 200])
def test_input_bar_always_inside_window(input_offset: int, vertical_offset: int) -> None:
    """任意 input_offset/vertical_offset 组合下，输入栏与气泡都必须落在窗口内（子控件不被裁切）。"""
    layout = compute_pet_layout(
        portrait_width=560,
        portrait_height=570,
        bubble_height=260,
        vertical_offset=vertical_offset,
        input_bar_offset=input_offset,
    )
    _, window_h = layout.window_size
    for rect in (layout.portrait_rect, layout.bubble_rect, layout.input_rect):
        assert rect[1] >= 0
        assert _rect_bottom(rect) <= window_h


def test_large_input_offset_grows_window_downward() -> None:
    """输入栏大幅下移时窗口向下扩容；立绘屏幕锚点（底边）保持在原位（本地 y 不变）。"""
    base = compute_pet_layout(portrait_width=560, portrait_height=570)
    lowered = compute_pet_layout(
        portrait_width=560, portrait_height=570, input_bar_offset=200
    )
    # 立绘底边本地 y 不变（窗口靠向下加高，立绘相对窗口顶位置不动）。
    assert lowered.portrait_anchor[1] == base.portrait_anchor[1]
    assert lowered.window_size[1] > base.window_size[1]
    assert _rect_bottom(lowered.input_rect) <= lowered.window_size[1]


def test_min_stage_height_clamp_keeps_portrait_bottom_pad() -> None:
    """小立绘触发 MIN_STAGE_HEIGHT 钳制时，余量落在顶部，立绘底边仍距窗口底 62。"""
    layout = compute_pet_layout(portrait_width=300, portrait_height=200)
    assert layout.window_size[1] == MIN_STAGE_HEIGHT
    assert layout.window_size[1] - _portrait_bottom(layout) == PORTRAIT_BOTTOM_PAD


def test_window_width_is_content_envelope() -> None:
    # 窗口宽 = max(立绘, 控制组) + 边距:窄面板时由立绘决定,宽面板时由面板决定,无 860 保底。
    narrow = compute_pet_layout(portrait_width=560, portrait_height=570, control_panel_width=420)
    wide = compute_pet_layout(portrait_width=560, portrait_height=570, control_panel_width=860)
    assert narrow.window_size[0] == 560 + STAGE_WIDTH_PANEL_PAD  # 立绘 560 > 面板 420
    assert wide.window_size[0] == 860 + STAGE_WIDTH_PANEL_PAD     # 面板 860 > 立绘 560


def test_portrait_anchor_matches_portrait_bottom_center() -> None:
    """portrait_anchor 恒等于立绘底边中心，供 PetWindow 钉屏幕位置用。"""
    layout = compute_pet_layout(
        portrait_width=480, portrait_height=500, bubble_height=180, input_bar_offset=60
    )
    x, y, w, h = layout.portrait_rect
    assert layout.portrait_anchor == (layout.window_size[0] // 2, y + h)


def test_normalizes_out_of_range_inputs() -> None:
    """越界参数走归一化，不抛异常且仍产出合法布局。"""
    layout = compute_pet_layout(
        portrait_width=560,
        portrait_height=570,
        control_panel_width=99999,
        bubble_height=-100,
        vertical_offset=99999,
        input_bar_offset=-50,
    )
    assert layout.window_size[0] > 0 and layout.window_size[1] >= MIN_STAGE_HEIGHT
    assert layout.input_rect[3] == INPUT_BAR_HEIGHT
