# -*- coding: utf-8 -*-
"""下拉框弹出窗口（popdown）的宽度与位置调整。

ttk::combobox 弹出的列表宽度按最长候选项算。端口描述、bin 目录这类长文本
会把下拉撑到屏幕外（多显示器时还可能跑到另一块屏上）。这里按显示器可用
宽度夹住，并保证下拉左缘落在同一块屏幕上。

串口面板的端口下拉和下载面板的路径下拉共用这套逻辑。
"""
import re
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import List, Optional, Tuple


def fit_popdown_span(
    anchor_x: int, want: int, mon_left: int, mon_right: int, margin: int = 4,
) -> Tuple[int, int]:
    """下拉左缘对齐 anchor_x；放不下时只在同一块屏幕内左移，不跨屏。"""
    width = max(1, want)
    usable = mon_right - mon_left - 2 * margin
    if usable > 0:
        width = min(width, usable)
    x = anchor_x
    if x + width > mon_right - margin:
        x = mon_right - margin - width
    if x < mon_left + margin:
        x = mon_left + margin
    return x, width


def monitor_span(widget: tk.Misc) -> Optional[Tuple[int, int]]:
    """控件所在显示器的左右边界。坐标系对不上时返回 None，避免把下拉拽走。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HWND, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        hwnd = wintypes.HWND(int(widget.winfo_toplevel().winfo_id()))
        monitor = user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        left, right = int(info.rcWork.left), int(info.rcWork.right)
    except (OSError, tk.TclError, ValueError, OverflowError):
        return None
    anchor_x = widget.winfo_rootx()
    if not (left - 8 <= anchor_x < right + 8):
        return None
    return left, right


def fit_combobox_popdown(combo: ttk.Combobox,
                          labels: Optional[List[str]] = None) -> None:
    """把下拉按最长候选项加宽，并夹在控件所在的显示器内。

    labels 默认为下拉框自己的候选项。坐标不可靠（拿不到显示器信息）时
    只加宽，保留 ttk 已经摆好的位置。
    """
    try:
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
        if not combo.tk.getboolean(combo.tk.call("winfo", "ismapped", popdown)):
            return
    except tk.TclError:
        return
    if labels is None:
        try:
            labels = [str(item) for item in combo.cget("values")]
        except tk.TclError:
            return
    labels = [str(item) for item in labels]
    if not labels:
        return
    try:
        font = tkfont.Font(font=str(combo.cget("font")))
    except tk.TclError:
        font = tkfont.nametofont("TkDefaultFont")
    text_px = max(font.measure(label) for label in labels)
    try:
        max_rows = int(combo.cget("height"))
    except (tk.TclError, TypeError, ValueError):
        max_rows = 10
    pad = 28 if len(labels) > max_rows else 12
    want = max(text_px + pad, combo.winfo_width())
    try:
        geo = str(combo.tk.call("wm", "geometry", popdown))
    except tk.TclError:
        return
    match = re.match(r"\d+x(\d+)([+-]\d+[+-]\d+)", geo)
    if not match:
        return
    height, origin = match.group(1), match.group(2)
    span = monitor_span(combo)
    if span is None:
        # 坐标不可靠时只加宽，保留 ttk 已经对齐到控件的位置
        combo.tk.call("wm", "geometry", popdown, f"{want}x{height}{origin}")
        return
    x, width = fit_popdown_span(combo.winfo_rootx(), want, span[0], span[1])
    pos_y = re.search(r"[+-]\d+$", origin)
    if not pos_y:
        return
    combo.tk.call(
        "wm", "geometry", popdown, f"{width}x{height}{x:+d}{pos_y.group(0)}")
