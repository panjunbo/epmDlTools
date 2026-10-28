"""全局 UI 字体与 ttk 主题。"""
from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Optional, Tuple

# 96 DPI 下 Tk 的默认 scaling（96/72）
_BASE_SCALING = 96.0 / 72.0

# UI：系统无衬线；等宽：日志 / 文件名
UI_FONT_CANDIDATES = (
    "Segoe UI",
    "Microsoft YaHei UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Helvetica Neue",
    "Arial",
)
MONO_FONT_CANDIDATES = (
    "Cascadia Mono",
    "Cascadia Code",
    "Consolas",
    "Lucida Console",
    "Courier New",
)

_FONTS: Optional["UiFonts"] = None
# 系统 DPI 对应的 tk scaling（启动时读一次；界面缩放不改它）
_SYSTEM_SCALING: Optional[float] = None
# 界面缩放倍率（1.0 = 跟随系统 DPI，1.25 = 再放大 25%）
_ZOOM = 1.0
# Tk 命名字体：Tk 9 下 ttk 的 Entry / Combobox / Spinbox 默认用 TkTextFont，
# 对话框和各种经典控件用 TkDefaultFont。不把它们也换成 UI 字体，就会出现
# 同一行里标签、下拉框、按钮字号不一致（下拉框按 YaHei 9，按钮按样式 10）。
_NAMED_UI_FONTS = (
    "TkDefaultFont", "TkTextFont", "TkEntryFont", "TkMenuFont",
    "TkHeadingFont", "TkTooltipFont", "TkIconFont", "TkStatusFont",
    "TkCaptionFont",
)
_NAMED_SMALL_FONTS = ("TkSmallCaptionFont",)   # 提示 / 状态栏这类小字
_NAMED_MONO_FONTS = ("TkFixedFont",)
# ttk 样式统一字体（字号随界面缩放变化）
_STYLE_FONT_NAMES = (
    ".", "TLabel", "TButton", "TCheckbutton", "TRadiobutton", "TCombobox",
    "TEntry", "TLabelframe", "TLabelframe.Label", "TMenubutton", "TSpinbox",
    "TNotebook", "TNotebook.Tab", "Treeview", "Treeview.Heading",
    "TPanedwindow", "Horizontal.TProgressbar", "Vertical.TProgressbar",
)


@dataclass(frozen=True)
class UiFonts:
    ui: str
    mono: str
    size_ui: int = 10
    size_mono: int = 10
    size_mono_sm: int = 9

    @property
    def ui_normal(self) -> Tuple[str, int]:
        return (self.ui, self.size_ui)

    @property
    def ui_bold(self) -> Tuple[str, int, str]:
        return (self.ui, self.size_ui, "bold")

    @property
    def ui_small(self) -> Tuple[str, int]:
        """端口 / 波特率 / 地址框使用的小一号字体。"""
        return (self.ui, max(5, self.size_ui - 2))

    @property
    def mono_normal(self) -> Tuple[str, int]:
        return (self.mono, self.size_mono)

    @property
    def mono_small(self) -> Tuple[str, int]:
        return (self.mono, self.size_mono_sm)


def _pick_family(candidates: tuple[str, ...], root: tk.Misc) -> str:
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    for name in candidates:
        if name in available:
            return name
    try:
        return tkfont.nametofont("TkDefaultFont").actual("family")
    except tk.TclError:
        return candidates[-1] if candidates else "TkDefaultFont"


def ui_scale(widget: tk.Misc) -> float:
    """相对 100%（96 DPI）的界面缩放，含界面缩放倍率。

    系统 100% → 1.0，150% → 1.5；再乘界面缩放倍率（layout_scale）。
    像素写死的间距、最小宽度用这个系数放大，才会和已经随缩放变大的
    字体保持同样的比例。
    """
    scale = 1.0
    try:
        pct = float(widget.tk.call("set", "::tk::scalingPct"))
        if pct >= 100:
            scale = pct / 100.0
        else:
            raise ValueError
    except (tk.TclError, TypeError, ValueError):
        try:
            scaling = _SYSTEM_SCALING or float(widget.tk.call("tk", "scaling"))
            if scaling > 0:
                scale = max(1.0, scaling / _BASE_SCALING)
        except (tk.TclError, TypeError, ValueError):
            scale = 1.0
    return scale * _ZOOM


def scale_px(widget: tk.Misc, pixels: int) -> int:
    """把按 100% 设计的像素值换成当前缩放下的像素。"""
    return max(1, int(round(pixels * ui_scale(widget))))


def init_ui_scaling(root: tk.Tk, layout_scale: float = 1.0) -> None:
    """记录系统 DPI 的 tk scaling，并应用界面缩放倍率。

    界面物理大小跟随系统显示缩放（VS Code 模型）：系统 150% 时字号
    （磅值）按 150% 换算成像素，和其他程序一样大；布局比例不变 ——
    像素写死的间距 / 最小宽度经 scale_px() 用同一系数放大。系统缩放
    变大时可视宽度变少，工具栏靠 FlowFrame 自动折行。

    layout_scale 在系统缩放的基础上再乘一档（1.25 = 比系统再大 25%），
    由 get_ui_fonts()（磅值）与 scale_px()（像素）同时放大。

    注意：这里**不**用 tk scaling 实现 zoom。Tk 改 scaling 后已创建的
    控件不会重绘（字号看似没变），zoom 只能靠改字号磅值实现，
    见 refresh_ui_fonts()。
    """
    global _SYSTEM_SCALING
    try:
        _SYSTEM_SCALING = float(root.tk.call("tk", "scaling"))
    except (tk.TclError, TypeError, ValueError):
        _SYSTEM_SCALING = _BASE_SCALING
    apply_ui_zoom(layout_scale)


def apply_ui_zoom(zoom: float) -> None:
    """只记录界面缩放倍率；字体由 refresh_ui_fonts() 重建。

    tk scaling 保持系统 DPI 不变（改它不会让已创建控件重绘）。
    """
    global _ZOOM
    try:
        zoom = float(zoom)
    except (TypeError, ValueError):
        zoom = 1.0
    _ZOOM = max(0.5, min(3.0, zoom))


def current_zoom() -> float:
    """当前界面缩放倍率。"""
    return _ZOOM


def scaled_point_size(size) -> int:
    """把磅值按界面缩放倍率换算（Tk 的 -size 只接受整数）。"""
    try:
        size = int(size)
    except (TypeError, ValueError):
        return 10
    if size == 0:
        return 0
    value = max(1, int(abs(size) * _ZOOM + 0.5))  # 四舍五入（不用 banker's）
    return value if size > 0 else -value


def _apply_named_fonts(fonts: "UiFonts") -> None:
    """把 Tk 命名字体统一成 UI 字体（字号已含界面缩放，不会被再缩一次）。"""

    def _set(name: str, family: str, size: int) -> None:
        try:
            tkfont.nametofont(name).configure(family=family, size=size)
        except (tk.TclError, TypeError, ValueError):
            pass

    for name in _NAMED_UI_FONTS:
        _set(name, fonts.ui, fonts.size_ui)
    for name in _NAMED_SMALL_FONTS:
        _set(name, fonts.ui, fonts.ui_small[1])
    for name in _NAMED_MONO_FONTS:
        _set(name, fonts.mono, fonts.size_mono)


def ellipsize(text: str, avail: int, font) -> str:
    """按可用宽度截断文字并加省略号。

    同 VS Code 的 ``text-overflow: ellipsis`` —— Tk 的 Label 只会硬裁，
    这里按字体度量算出能放下的长度。
    """
    if not text:
        return text
    if avail <= 0 or font.measure(text) <= avail:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if font.measure(text[:mid] + "…") <= avail:
            lo = mid
        else:
            hi = mid - 1
    return (text[:lo] + "…") if lo else ""


def get_ui_fonts() -> UiFonts:
    """获取当前 UI 字体；未初始化时返回合理默认值。"""
    if _FONTS is not None:
        return _FONTS
    return _make_fonts(UI_FONT_CANDIDATES[0], MONO_FONT_CANDIDATES[2])


def _make_fonts(ui: str, mono: str) -> UiFonts:
    """按界面缩放倍率生成字号（磅值随 zoom 放大，物理大小随系统 DPI）。"""
    return UiFonts(
        ui=ui,
        mono=mono,
        size_ui=scaled_point_size(10),
        size_mono=scaled_point_size(10),
        size_mono_sm=scaled_point_size(9),
    )


def _apply_fonts(root: tk.Misc, fonts: UiFonts) -> None:
    """把字体写进 option 数据库与 ttk 样式（已创建控件由各面板 refresh_fonts 更新）。

    这里刻意**不**设 ``*Font``：ttk 的 Label / Combobox / Spinbox 等创建时会把
    ``*Font`` 抓成控件自己的 -font，之后改 ttk 样式（界面缩放）就不再生效，
    表现为标签、下拉框字号不变而按钮、复选框跟着缩放。ttk 控件统一走下面的
    样式（``.`` 兜底），默认用命名字体的控件（Entry / Combobox / 对话框）
    由 _apply_named_fonts() 统一，经典控件（Text / Menu）由各面板显式设置字体。
    """
    root.option_add("*Text.Font", fonts.mono_normal)
    root.option_add("*Listbox.Font", fonts.mono_normal)
    root.option_add("*Menu.font", fonts.ui_normal)

    style = ttk.Style(root)
    for name in _STYLE_FONT_NAMES:
        try:
            style.configure(name, font=fonts.ui_normal)
        except tk.TclError:
            pass
    try:
        style.configure("TLabelframe.Label", font=fonts.ui_bold)
    except tk.TclError:
        pass
    _apply_named_fonts(fonts)


def init_ui_theme(root: tk.Tk) -> UiFonts:
    """在创建子控件前调用：设置 ttk 主题与全局字体。"""
    global _FONTS

    style = ttk.Style(root)
    if sys.platform == "win32":
        for theme in ("vista", "xpnative", "clam"):
            if theme in style.theme_names():
                try:
                    style.theme_use(theme)
                    break
                except tk.TclError:
                    continue
    elif "clam" in style.theme_names():
        style.theme_use("clam")

    if sys.platform == "win32":
        fonts = _make_fonts("Segoe UI", "Consolas")
    else:
        fonts = _make_fonts(
            _pick_family(UI_FONT_CANDIDATES, root),
            _pick_family(MONO_FONT_CANDIDATES, root),
        )
    _FONTS = fonts
    _apply_fonts(root, fonts)
    return fonts


def refresh_ui_fonts(root: tk.Misc) -> UiFonts:
    """界面缩放后重建字体（不改主题，沿用当前字体族）。

    只更新 option 数据库与 ttk 样式；Text / 图形界面上显式设了 font 的控件
    要由各自的 refresh_fonts() 重新 configure。
    """
    global _FONTS
    if _FONTS is None:
        return get_ui_fonts()
    _FONTS = _make_fonts(_FONTS.ui, _FONTS.mono)
    _apply_fonts(root, _FONTS)
    return _FONTS
