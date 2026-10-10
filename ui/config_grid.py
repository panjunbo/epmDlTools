"""串口配置 / 擦除与回读 共用的六列栅格，保证上下两块面板横轴对齐。"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Optional, Tuple

from .theme import get_ui_fonts

PAD = 2
FIELD_WIDTH = 10
BTN_WIDTH = 10
FIELD_PADX = (2, 8)
FIELD_PADX_LAST = (2, 4)

_col_minsizes: Optional[Tuple[int, ...]] = None


def _compute_col_minsizes(root: tk.Misc) -> Tuple[int, ...]:
    f = tkfont.Font(root=root, font=get_ui_fonts().ui_normal)
    label_px = (
        max(f.measure(t) for t in ("端口", "日志", "下载", "地址", "大小"))
        + 2 * PAD + 6
    )
    # 下拉框、输入框、同宽按钮取最大，并留出下拉箭头与 DPI 余量
    field_px = max(
        f.measure("0") * FIELD_WIDTH,
        f.measure("串口参数"),
        f.measure("刷新端口"),
        f.measure("打开串口"),
        f.measure("回读目录"),
        f.measure("XIP回读"),
    ) + 32
    return (label_px, field_px, label_px, field_px, label_px, field_px)


def apply_six_column_grid(frame) -> None:
    global _col_minsizes
    root = frame.winfo_toplevel()
    if _col_minsizes is None:
        _col_minsizes = _compute_col_minsizes(root)
    for col, minsize in enumerate(_col_minsizes):
        frame.columnconfigure(col, minsize=minsize)


def config_label(master, text: str, row: int, column: int) -> ttk.Label:
    lbl = ttk.Label(master, text=text, anchor=tk.W)
    lbl.grid(row=row, column=column, sticky=tk.W, padx=PAD, pady=2)
    return lbl
