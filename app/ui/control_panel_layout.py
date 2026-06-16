"""底部控制组（对话气泡 + 输入栏）的可调布局参数、归一化与统一布局模型。

单窗口重构后，立绘 / 气泡 / 输入栏同属一个 PetWindow，气泡与输入栏是其窗口内子控件
（CardContainer），位置由本模块的 compute_pet_layout 统一计算（立绘底边为锚点 + 三者包围盒）。
这里集中存放用户可调参数的取值范围与归一化逻辑：

- control_panel_width：控制组（气泡与输入栏共用）的宽度
- bubble_height：气泡卡片的高度
- vertical_offset：控制组整体的上下偏移（正值=向上抬升，远离屏幕底部）

独立成模块是为了让 PetWindow 与 SettingsDialog 都能引用，又不引入二者之间的
循环导入（PetWindow 已经 import SettingsDialog）。本模块保持零外部依赖。
"""

from __future__ import annotations

from dataclasses import dataclass

# 控制组宽度（气泡与输入栏共用同一宽度）
DEFAULT_CONTROL_PANEL_WIDTH = 640
MIN_CONTROL_PANEL_WIDTH = 420
MAX_CONTROL_PANEL_WIDTH = 860

# 气泡卡片高度
DEFAULT_BUBBLE_HEIGHT = 128
MIN_BUBBLE_HEIGHT = 96
MAX_BUBBLE_HEIGHT = 260

# 控制组整体上下偏移：正值向上抬升，负值向下沉。0 为原始默认位置。
DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET = 0
MIN_CONTROL_PANEL_VERTICAL_OFFSET = -200
MAX_CONTROL_PANEL_VERTICAL_OFFSET = 200

# 输入栏相对气泡的额外下移：只能向下（>=0），用于加大输入栏与气泡的间距。
DEFAULT_INPUT_BAR_OFFSET = 0
MIN_INPUT_BAR_OFFSET = 0
MAX_INPUT_BAR_OFFSET = 200

# 布局固定量：输入栏高度、气泡与输入栏间距、控制组距舞台底部的基础留白。
# 取自重构前 _layout_stage 中的硬编码值，提取为常量便于布局统一引用。
INPUT_BAR_HEIGHT = 52
CONTROL_PANEL_GAP = 10
CONTROL_PANEL_BOTTOM_MARGIN = 84

# ── 单窗口布局常量（compute_pet_layout 专用）──────────────────────────
# 舞台（主窗口）尺寸保底与下限，复刻 pet_window 中的同名常量。
DEFAULT_STAGE_WIDTH = 860
MIN_STAGE_HEIGHT = 420
# 窗口宽度 = max(立绘宽, panel_width) + STAGE_WIDTH_PANEL_PAD(动态包络,无 860 保底);
# 气泡/输入栏宽度 = min(panel_width, max(MIN_CONTROL_PANEL_WIDTH, window_w - BUBBLE_INNER_PAD))。
# DEFAULT_STAGE_WIDTH 保留作初始名义尺寸/历史参考,不再参与 window_w 计算。
STAGE_WIDTH_PANEL_PAD = 96
BUBBLE_INNER_PAD = 32
# 立绘底边到窗口底边的基础留白（重构前 _layout_stage 中 `height - portrait_h - 62` 的 62）。
PORTRAIT_BOTTOM_PAD = 62
# 包围盒顶部留白（复刻默认参数下立绘顶边距窗口顶约 8px）。
PORTRAIT_TOP_PAD = 8
# 气泡底边固定在立绘底边上方的距离（vertical_offset=0 时）。
# 由原布局推导：bubble_bottom 距窗口底 = INPUT_BAR_HEIGHT+GAP+MARGIN，立绘底边距窗口底 = PORTRAIT_BOTTOM_PAD，
# 故气泡底边在立绘底边上方 (52+10+84)-62 = 84。
BUBBLE_BOTTOM_ABOVE_PORTRAIT = (
    INPUT_BAR_HEIGHT + CONTROL_PANEL_GAP + CONTROL_PANEL_BOTTOM_MARGIN - PORTRAIT_BOTTOM_PAD
)


@dataclass(frozen=True)
class PetLayout:
    """单窗口布局结果：窗口尺寸 + 三个子控件的窗口本地矩形 + 立绘底边锚点。

    所有 rect 为 (x, y, w, h)，y 轴向下，原点为窗口左上角。
    portrait_anchor 为「立绘底边中心」在窗口本地坐标的点 (x, y)；PetWindow 据此让立绘
    屏幕位置在气泡高度/输入栏下移/缩放变化时保持不动（参数变化只改窗口尺寸与子控件相对位置）。
    """

    window_size: tuple[int, int]
    portrait_rect: tuple[int, int, int, int]
    bubble_rect: tuple[int, int, int, int]
    input_rect: tuple[int, int, int, int]
    portrait_anchor: tuple[int, int]


def compute_pet_layout(
    *,
    portrait_width: int,
    portrait_height: int,
    control_panel_width: object = DEFAULT_CONTROL_PANEL_WIDTH,
    bubble_height: object = DEFAULT_BUBBLE_HEIGHT,
    vertical_offset: object = DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    input_bar_offset: object = DEFAULT_INPUT_BAR_OFFSET,
) -> PetLayout:
    """计算「立绘+气泡+输入栏」单窗口布局：窗口为三者包围盒，立绘底边为固定锚点。

    设计要点（替代重构前散落在 _stage_size_for_layout 与 _layout_stage 的锚点数学）：
    - 以「立绘底边」为参考原点(y=0)、y 向下为正排布三个元素的纵向位置；
    - 窗口高度取包围盒高度（含留白）并保底 MIN_STAGE_HEIGHT；窗口底边留白沿用 PORTRAIT_BOTTOM_PAD，
      输入栏下移过大时窗口向下扩容，保证输入栏始终落在窗口内（不会被子控件裁切）；
    - 返回 portrait_anchor，使 PetWindow 能在任意参数变化下把立绘底边钉在同一屏幕点。
    """
    panel_width = normalize_control_panel_width(control_panel_width)
    bub_h = normalize_bubble_height(bubble_height)
    v_off = normalize_control_panel_vertical_offset(vertical_offset)
    in_off = normalize_input_bar_offset(input_bar_offset)
    pw = max(0, int(portrait_width))
    ph = max(0, int(portrait_height))

    # 横向:窗口宽度取「立绘」与「控制组」中较宽者 + 边距(动态包络),不再固定保底 860,
    # 避免窄立绘/窄气泡时窗口两侧留大片空白(及随之而来的多余可点击/碰撞区)。
    window_w = max(pw, panel_width) + STAGE_WIDTH_PANEL_PAD
    bubble_w = min(panel_width, max(MIN_CONTROL_PANEL_WIDTH, window_w - BUBBLE_INNER_PAD))

    # 纵向：参考原点为立绘底边(0)，y 向下为正。
    portrait_top = -ph
    bubble_bottom = -BUBBLE_BOTTOM_ABOVE_PORTRAIT - v_off  # vertical_offset 正值上抬
    bubble_top = bubble_bottom - bub_h
    input_top = bubble_bottom + CONTROL_PANEL_GAP + in_off  # input_offset 只向下
    input_bottom = input_top + INPUT_BAR_HEIGHT

    # 包围盒：顶取最高元素并留 TOP_PAD；底边距立绘底边取「基础留白」与「输入栏底边」的较大者，
    # 后者保证输入栏下移很大时窗口随之向下长高，输入栏不越出窗口。
    top_extent = min(portrait_top, bubble_top)
    bottom_gap = max(PORTRAIT_BOTTOM_PAD, input_bottom)
    content_h = (bottom_gap - top_extent) + PORTRAIT_TOP_PAD
    window_h = max(MIN_STAGE_HEIGHT, round(content_h))

    # 立绘底边的窗口本地 y：保证窗口底边在其下方恰为 bottom_gap；MIN 钳高出来的余量落在顶部。
    portrait_bottom_local = window_h - round(bottom_gap)

    def _ly(frame_y: float) -> int:
        return round(portrait_bottom_local + frame_y)

    portrait_rect = ((window_w - pw) // 2, _ly(portrait_top), pw, ph)
    bubble_rect = ((window_w - bubble_w) // 2, _ly(bubble_top), bubble_w, bub_h)
    input_rect = ((window_w - bubble_w) // 2, _ly(input_top), bubble_w, INPUT_BAR_HEIGHT)
    portrait_anchor = (window_w // 2, portrait_bottom_local)

    return PetLayout(
        window_size=(window_w, window_h),
        portrait_rect=portrait_rect,
        bubble_rect=bubble_rect,
        input_rect=input_rect,
        portrait_anchor=portrait_anchor,
    )


def _clamp_int(value: object, *, minimum: int, maximum: int, default: int) -> int:
    """把任意输入归一化为 [minimum, maximum] 内的整数；无法解析时回退默认值。

    兼容配置文件里写成字符串/浮点的情况（如 "640"、640.0）。
    """
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def normalize_control_panel_width(value: object) -> int:
    return _clamp_int(
        value,
        minimum=MIN_CONTROL_PANEL_WIDTH,
        maximum=MAX_CONTROL_PANEL_WIDTH,
        default=DEFAULT_CONTROL_PANEL_WIDTH,
    )


def normalize_bubble_height(value: object) -> int:
    return _clamp_int(
        value,
        minimum=MIN_BUBBLE_HEIGHT,
        maximum=MAX_BUBBLE_HEIGHT,
        default=DEFAULT_BUBBLE_HEIGHT,
    )


def normalize_control_panel_vertical_offset(value: object) -> int:
    return _clamp_int(
        value,
        minimum=MIN_CONTROL_PANEL_VERTICAL_OFFSET,
        maximum=MAX_CONTROL_PANEL_VERTICAL_OFFSET,
        default=DEFAULT_CONTROL_PANEL_VERTICAL_OFFSET,
    )


def normalize_input_bar_offset(value: object) -> int:
    return _clamp_int(
        value,
        minimum=MIN_INPUT_BAR_OFFSET,
        maximum=MAX_INPUT_BAR_OFFSET,
        default=DEFAULT_INPUT_BAR_OFFSET,
    )
