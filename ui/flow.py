"""流式布局容器：宽度不够时自动换行。

Tk 的 pack/grid 不会自动换行，窗口一变窄工具栏就被裁掉。VS Code 的做法是
让工具栏 reflow（放不下就折行）。这里用 place 实现一个简易 flow layout：

- 子控件按加入顺序从左往右排
- 一行放不下就换到下一行，容器高度随之增加
- 宽度变化时自动重排（<Configure>）

用法::

    bar = FlowFrame(parent)
    bar.add(ttk.Button(...), padx=2)
    bar.add(ttk.Button(...), padx=(0, 4))   # 也支持 (left, right)

字号变化（界面缩放）后子控件的 reqwidth 会变，但容器宽度没变、
不会触发 <Configure>，要显式调用 ``reflow()``（见各面板 refresh_fonts）。
"""
import tkinter as tk
from tkinter import ttk
from typing import List, Tuple


def _norm_pad(v) -> Tuple[int, int]:
    """padding 支持 int 或 (起, 止) 二元组。"""
    if isinstance(v, (tuple, list)):
        return int(v[0]), int(v[1])
    return int(v), int(v)


class FlowFrame(ttk.Frame):
    """按宽度自动换行的容器。"""

    def __init__(self, master, align: str = "left", **kw):
        super().__init__(master, **kw)
        # [(widget, pad_l, pad_r, pad_t, pad_b)]
        self._items: List[Tuple[ttk.Widget, int, int, int, int]] = []
        self._rows = 1
        self._align = "right" if align == "right" else "left"
        # 字号变化后 ttk 控件的 reqwidth 要等空闲才更新，第一遍可能用旧值排版；
        # 每次 reflow 后再补一遍（只补一次，不会无限循环）
        self._idle_pending = False
        self.bind("<Configure>", self._on_configure)

    def add(self, widget: ttk.Widget, padx=0, pady=0) -> ttk.Widget:
        """加入一个子控件（不要再用 pack/place 摆它）。"""
        pl, pr = _norm_pad(padx)
        pt, pb = _norm_pad(pady)
        self._items.append((widget, pl, pr, pt, pb))
        widget.place(x=pl, y=pt)
        self.reflow()
        return widget

    def content_width(self) -> int:
        """不折行时的总宽度（给分隔条定位用；不受当前容器宽度影响）。"""
        return sum(w.winfo_reqwidth() + pl + pr
                   for w, pl, pr, _pt, _pb in self._items)

    def reflow(self) -> None:
        """按当前宽度重排，并在空闲时再补一次（字号/主题变化后度量会变）。"""
        self._layout()
        if not self._idle_pending:
            self._idle_pending = True
            try:
                self.after_idle(self._idle_reflow)
            except tk.TclError:
                self._idle_pending = False

    def _idle_reflow(self) -> None:
        self._idle_pending = False
        self._layout()

    def _layout(self) -> None:
        """按当前宽度重排；宽度还没确定（首帧前）时跳过，等 <Configure>。"""
        width = self.winfo_width()
        if width <= 1:
            return
        # 1) 分行：每行记下 [(item, x)]
        rows: List[List[Tuple[tuple, int]]] = []
        row: List[Tuple[tuple, int]] = []
        x = 0
        for item in self._items:
            w, pl, pr, _pt, _pb = item
            need = w.winfo_reqwidth() + pl + pr
            if row and x + need > width:
                rows.append(row)
                row = []
                x = 0
            row.append((item, x))
            x += need
        if row:
            rows.append(row)

        # 2) 每行按最高控件定行高；子控件上下居中；可选右对齐；竖分隔条拉满行高
        y = 0
        for row in rows:
            heights = [w.winfo_reqheight() + pt + pb
                       for (w, _pl, _pr, pt, pb), _x in row
                       if not self._is_vsep(w)]
            row_h = max(heights, default=1)
            if self._align == "right" and row:
                last_item, last_x = row[-1]
                lw, _pl, lpr, _pt, _pb = last_item
                used = last_x + lw.winfo_reqwidth() + lpr
                x_off = max(0, width - used)
            else:
                x_off = 0
            for (w, pl, _pr, pt, pb), x in row:
                px = x + pl + x_off
                if self._is_vsep(w):
                    w.place(x=px, y=y + pt,
                            height=max(1, row_h - pt - pb))
                else:
                    wh = w.winfo_reqheight()
                    avail = max(0, row_h - pt - pb)
                    cy = y + pt + max(0, (avail - wh) // 2)
                    w.place(x=px, y=cy)
            y += row_h

        if y != self.winfo_height() or len(rows) != self._rows:
            self._rows = len(rows)
            self.configure(height=y)

    @staticmethod
    def _is_vsep(w) -> bool:
        if not isinstance(w, ttk.Separator):
            return False
        try:
            return str(w.cget("orient")) == "vertical"
        except tk.TclError:
            return False

    def _on_configure(self, event=None):
        self.reflow()

