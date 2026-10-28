import os
import subprocess
import platform
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable, List, Optional

from utils.app_paths import get_readback_dir

from .theme import get_ui_fonts


def _open_in_explorer(path: str):
    """跨平台打开文件/目录（资源管理器/Finder/xdg-open）。目录不存在时自动创建。"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
    if platform.system() == "Windows":
        os.startfile(path)
    elif platform.system() == "Darwin":
        subprocess.run(["open", path])
    else:
        subprocess.run(["xdg-open", path])


class EraseReadPanel(ttk.LabelFrame):
    def __init__(self, master,
                 on_erase: Optional[Callable[[dict], None]] = None,
                 on_read: Optional[Callable[[dict], None]] = None,
                 on_open_readback_dir: Optional[Callable[[], None]] = None):
        super().__init__(master, text="擦除与回读")
        self._on_erase = on_erase
        self._on_read = on_read
        self._on_open_readback_dir = on_open_readback_dir

        self.erase_addr = tk.StringVar(value="0x0")
        self.erase_size = tk.StringVar(value="0x100000")
        self.read_addr = tk.StringVar(value="0x0")
        self.read_size = tk.StringVar(value="0x100000")
        # 回读模式：False=回读(flash_read)，True=XIP回读(flash_xip_read)。
        # 双击"回读"按钮切换；单击按当前模式触发。
        self.use_xip = tk.BooleanVar(value=False)
        self._read_click_job = None

        self._entries: List[ttk.Entry] = []
        rows = [
            ("擦除地址", self.erase_addr),
            ("擦除大小", self.erase_size),
            ("回读地址", self.read_addr),
            ("回读大小", self.read_size),
        ]
        # 不设 width：四个汉字比 width=7 个西文更宽，会被裁成「除地址」
        # 复用一个 Font 对象量文字宽度（每次新建 tkfont.Font 都会在 Tk 里
        # 留下一个命名字体，缩放到处刷新时会越攒越多）
        self._label_font = tkfont.Font(root=self, font=get_ui_fonts().ui_normal)
        self.columnconfigure(
            0, weight=0,
            minsize=max(self._label_font.measure(text) for text, _var in rows) + 6,
        )
        # 地址框所在列可伸缩：窄了收缩、宽了拉伸（不写死字符宽度）
        self.columnconfigure(1, weight=1, minsize=60)
        field_font = get_ui_fonts().ui_small
        for i, (text, var) in enumerate(rows):
            ttk.Label(self, text=text, anchor=tk.E).grid(
                row=i, column=0, sticky=tk.E, padx=(2, 4), pady=4)
            entry = ttk.Entry(self, textvariable=var, width=8, font=field_font)
            entry.grid(row=i, column=1, sticky=tk.EW, padx=(0, 8), pady=4)
            self._entries.append(entry)

        self.btn_erase = ttk.Button(self, text="擦除", command=self._do_erase, width=8)
        self.btn_erase.grid(row=0, column=2, sticky=tk.W, padx=(8, 2), pady=4)
        # 回读按钮：不设 command（避免与单击延迟任务重复触发），全部走事件绑定
        self.btn_read = ttk.Button(self, text="回读", width=8)
        self.btn_read.grid(row=2, column=2, sticky=tk.W, padx=(8, 2), pady=4)
        self.btn_read.bind("<Button-1>", self._on_read_click)
        self.btn_read.bind("<Double-Button-1>", self._on_read_double)
        self.btn_open_dir = ttk.Button(
            self, text="打开目录", command=self._do_open_dir, width=8)
        self.btn_open_dir.grid(row=3, column=2, sticky=tk.W, padx=(8, 2), pady=4)

    def refresh_fonts(self):
        """界面缩放后重设字体，并按新字号重算标签列宽。"""
        fonts = get_ui_fonts()
        for entry in self._entries:
            try:
                entry.configure(font=fonts.ui_small)
            except tk.TclError:
                pass
        self._label_font.configure(family=fonts.ui, size=fonts.size_ui)
        texts = [w.cget("text") for w in self.winfo_children()
                 if isinstance(w, ttk.Label)]
        if texts:
            self.columnconfigure(
                0, minsize=max(self._label_font.measure(t) for t in texts) + 6)

    def _do_erase(self):
        if self._on_erase:
            self._on_erase({"addr": self.erase_addr.get(),
                            "size": self.erase_size.get()})

    def _do_read(self):
        if self._on_read:
            self._on_read({"addr": self.read_addr.get(),
                           "size": self.read_size.get(),
                           "xip": self.use_xip.get()})

    def _on_read_click(self, _event=None):
        """单击：延迟触发回读（若随后来的是双击，则取消本次触发）。"""
        if self.btn_read.instate(["disabled"]):
            return
        if self._read_click_job is not None:
            return
        try:
            delay = int(self.tk.call("tk", "doubleclicktime"))
        except Exception:
            delay = 300
        self._read_click_job = self.after(delay, self._fire_read)

    def _on_read_double(self, _event=None):
        """双击：取消挂起的单击触发，切换 回读/XIP回读 模式。"""
        if self.btn_read.instate(["disabled"]):
            return
        if self._read_click_job is not None:
            self.after_cancel(self._read_click_job)
            self._read_click_job = None
        self.set_xip(not self.use_xip.get())

    def _fire_read(self):
        self._read_click_job = None
        self._do_read()

    def set_xip(self, xip: bool):
        """设置回读模式并同步按钮文字：回读 / XIP回读。"""
        self.use_xip.set(bool(xip))
        self.btn_read.configure(text="XIP回读" if self.use_xip.get() else "回读")

    def _do_open_dir(self):
        """打开回读自动保存目录（与 utils/bin_parse.py 中 BinParse.save 的路径一致）。
        若上层传入了 on_open_readback_dir 回调则委托给它，否则本地兜底。
        """
        if self._on_open_readback_dir:
            self._on_open_readback_dir()
            return
        try:
            _open_in_explorer(get_readback_dir())
        except Exception as e:
            print(f"[EraseReadPanel] 打开目录失败: {e}")

    def set_running(self, running: bool, serial_opened: bool = False):
        """下载/擦除/回读任务进行中：禁用擦除/回读按钮与地址/大小输入框，避免误触发/误改参数。
        任务结束（含取消/失败）后一律恢复可操作，不因 log 串口仍打开而保持灰色。
        serial_opened 参数保留仅为兼容调用方签名，实际不再使用。
        """
        if running:
            self.btn_erase.configure(state=tk.DISABLED)
            self.btn_read.configure(state=tk.DISABLED)
            self.btn_open_dir.configure(state=tk.DISABLED)
            for entry in self._entries:
                entry.configure(state=tk.DISABLED)
        else:
            self.btn_erase.configure(state=tk.NORMAL)
            self.btn_read.configure(state=tk.NORMAL)
            self.btn_open_dir.configure(state=tk.NORMAL)
            for entry in self._entries:
                entry.configure(state=tk.NORMAL)
