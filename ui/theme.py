"""全局 UI 字体与 ttk 主题。"""
from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk
from typing import Optional, Tuple

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
        return (self.ui, max(8, self.size_ui - 2))

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


def get_ui_fonts() -> UiFonts:
    """获取当前 UI 字体；未初始化时返回合理默认值。"""
    if _FONTS is not None:
        return _FONTS
    return UiFonts(
        ui=UI_FONT_CANDIDATES[0],
        mono=MONO_FONT_CANDIDATES[2],
    )


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
        fonts = UiFonts(ui="Segoe UI", mono="Consolas")
    else:
        fonts = UiFonts(
            ui=_pick_family(UI_FONT_CANDIDATES, root),
            mono=_pick_family(MONO_FONT_CANDIDATES, root),
        )
    _FONTS = fonts

    root.option_add("*Font", fonts.ui_normal)
    root.option_add("*Text.Font", fonts.mono_normal)
    root.option_add("*Entry.Font", fonts.ui_normal)
    root.option_add("*Listbox.Font", fonts.mono_normal)
    root.option_add("*Menu.font", fonts.ui_normal)

    for name in (
        ".",
        "TLabel",
        "TButton",
        "TCheckbutton",
        "TRadiobutton",
        "TCombobox",
        "TEntry",
        "TLabelframe",
        "TLabelframe.Label",
        "TMenubutton",
        "TSpinbox",
        "TNotebook",
        "TNotebook.Tab",
        "Treeview",
        "Treeview.Heading",
        "TPanedwindow",
        "Horizontal.TProgressbar",
        "Vertical.TProgressbar",
    ):
        try:
            style.configure(name, font=fonts.ui_normal)
        except tk.TclError:
            pass

    try:
        style.configure("TLabelframe.Label", font=fonts.ui_bold)
    except tk.TclError:
        pass

    return fonts
