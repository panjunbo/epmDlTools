import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from typing import Callable, List, Optional

_FIND_HISTORY_MAX = 5
_FIND_HISTORY_SEP = "|"
_LARGE_LOG_CHARS = 512 * 1024
_TEXT_INSERT_LINES = 400
_RECV_TEXT_MAX_CHARS = 512 * 1024
_APPEND_BATCH = 32
_SAVE_FLUSH_LINES = 32
_RECV_FONT_DEFAULT = 10
_RECV_FONT_MIN = 8
_RECV_FONT_MAX = 28

from communication import UartComm
from .flow import FlowFrame
from .theme import get_ui_fonts, scaled_point_size
from utils.app_paths import get_logs_dir


class SerialAssistant(ttk.PanedWindow):
    # 接收区默认占比；其余给发送区（约 32%）
    DEFAULT_RECV_RATIO = 0.68

    LEVEL_COLOR = {
        "INFO": "#7c3aed",   # 非 ID：原 ID_B 紫
        "WARN": "#b58900",
        "ERROR": "#dc322f",
        "DEBUG": "#268bd2",
        "ID": "#6c71c4",
        "ID_A": "#222",      # MCU_A: 原非 ID 黑
        "ID_B": "#0080FF",   # MCU_B: 原 ID_A 蓝
        "ID_C": "#00A86B",   # MCU_C: 翠绿
    }

    def __init__(self, master,
                 get_active_comm: Optional[Callable[[], Optional[UartComm]]] = None,
                 on_load_log: Optional[Callable[[], None]] = None,
                 on_clear_recv: Optional[Callable[[], None]] = None,
                 on_status: Optional[Callable[[str, str], None]] = None):
        super().__init__(master, orient=tk.VERTICAL)
        try:
            style = ttk.Style()
            style.configure("Sash.Vertical.TPanedwindow", sashthickness=2)
            self.configure(style="Sash.Vertical.TPanedwindow")
        except Exception:
            pass
        self._get_active_comm = get_active_comm
        self._on_load_log = on_load_log
        self._on_clear_recv = on_clear_recv
        self._on_status = on_status
        # 查找状态（须在 _build_recv 之前初始化）
        self._find_history: List[str] = []
        self._find_pos = "1.0"
        self._find_last_start: Optional[str] = None
        self._find_last_end: Optional[str] = None
        self._find_active = False
        self._auto_scroll_saved: Optional[bool] = None
        self._find_bar_visible = False
        self._defer_scroll = False
        self._text_insert_gen = 0
        fonts = get_ui_fonts()
        self._mono_font = fonts.mono_normal
        self._recv_font_size = _RECV_FONT_DEFAULT
        # 接收区字号是用户设置（磅值），显示时再乘界面缩放
        self._recv_font = (fonts.mono, scaled_point_size(self._recv_font_size))
        self._send_count = 0
        self._send_bytes = 0
        self._recv_count = 0
        self._recv_bytes = 0
        self._append_pending: List[tuple] = []
        self._save_pending_lines = 0

        self._recv_pane = ttk.Frame(self)
        self.add(self._recv_pane, weight=1)
        self._build_recv()

        self._action_pane = ttk.Frame(self)
        self.add(self._action_pane, weight=0)
        self._build_action_area()

        self._rx_thread: Optional[threading.Thread] = None
        self._rx_stop = threading.Event()

        self._save_fp = None  # 自动保存文件句柄

    def _build_recv(self):
        # 不再用 LabelFrame，去掉"接收区/日志"标题边框；减少 padx/pady 让布局更紧凑
        frame = ttk.Frame(self._recv_pane)
        frame.pack(fill=tk.BOTH, expand=True)

        # 工具栏两行，控件均从左到右紧凑排列
        self.encoding = "latin-1"

        # 流式工具栏：宽度不够自动折行（窗口变窄 / 系统缩放变大时不被裁掉）
        self._toolbar1 = FlowFrame(frame)
        self._toolbar1.pack(fill=tk.X, padx=2, pady=(2, 0))
        toolbar1 = self._toolbar1

        toolbar1.add(ttk.Label(toolbar1, text="显示"), padx=(0, 2))
        self.show_mode = tk.StringVar(value="ASCII")
        toolbar1.add(ttk.Combobox(toolbar1, textvariable=self.show_mode,
                                  values=["ASCII", "HEX"], width=6, state="readonly"),
                     padx=(0, 6))
        self.show_mode.trace_add("write", self._on_show_mode_changed)

        toolbar1.add(ttk.Separator(toolbar1, orient=tk.VERTICAL),
                     padx=4, pady=2)

        self.show_ts = tk.BooleanVar(value=True)
        toolbar1.add(ttk.Checkbutton(toolbar1, text="时间戳", variable=self.show_ts),
                     padx=(0, 4))
        self.auto_scroll = tk.BooleanVar(value=True)
        toolbar1.add(ttk.Checkbutton(toolbar1, text="自动滚屏", variable=self.auto_scroll),
                     padx=(0, 6))

        toolbar1.add(ttk.Separator(toolbar1, orient=tk.VERTICAL),
                     padx=4, pady=2)

        # 相关的标签 + 输入框放进同一个子 Frame：折行时整组一起换行，
        # 不会出现"超时刷新(ms)"在上一行、输入框在下一行。
        timeout_grp = ttk.Frame(toolbar1)
        ttk.Label(timeout_grp, text="超时刷新(ms)").pack(
            side=tk.LEFT, padx=(0, 2))
        self.flush_timeout_var = tk.IntVar(value=100)
        self._spin_flush = ttk.Spinbox(
            timeout_grp, textvariable=self.flush_timeout_var,
            from_=0, to=60000, increment=50, width=5,
        )
        self._spin_flush.pack(side=tk.LEFT)
        toolbar1.add(timeout_grp, padx=(0, 6))
        self.flush_timeout_var.trace_add("write", self._on_flush_timeout_changed)
        self._flush_timeout_callback: Optional[Callable[[int], None]] = None

        toolbar1.add(ttk.Separator(toolbar1, orient=tk.VERTICAL),
                     padx=4, pady=2)

        self.btn_load_log = ttk.Button(
            toolbar1, text="加载Log", width=7,
            command=self._on_load_log_clicked)
        toolbar1.add(self.btn_load_log, padx=(0, 4))
        self.auto_save = tk.BooleanVar(value=False)
        save_grp = ttk.Frame(toolbar1)
        self._chk_auto_save = ttk.Checkbutton(
            save_grp, text="自动保存log", variable=self.auto_save,
            command=self._on_auto_save_toggled)
        self._chk_auto_save.pack(side=tk.LEFT, padx=(0, 2))
        self.save_path = tk.StringVar(value=self._default_save_path())
        ttk.Button(save_grp, text="…", width=2,
                   command=self._choose_save_file).pack(side=tk.LEFT)
        toolbar1.add(save_grp, padx=(0, 6))

        toolbar1.add(ttk.Separator(toolbar1, orient=tk.VERTICAL),
                     padx=4, pady=2)

        recv_btn_grp = ttk.Frame(toolbar1)
        self.btn_find = ttk.Button(
            recv_btn_grp, text="查找", width=5,
            command=self._toggle_find_bar)
        self.btn_find.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(recv_btn_grp, text="清空",
                   command=self.clear_recv, width=5).pack(
            side=tk.LEFT, padx=(0, 4))
        ttk.Button(recv_btn_grp, text="保存",
                   command=self.save_recv_to_file, width=5).pack(side=tk.LEFT)
        toolbar1.add(recv_btn_grp, padx=(0, 4))

        # 查找栏同样用流式布局：左栏变窄时不至于把按钮裁掉
        self._find_toolbar = FlowFrame(frame)
        self._find_var = tk.StringVar()
        self._find_combo = ttk.Combobox(
            self._find_toolbar, textvariable=self._find_var, width=20, values=[])
        self._find_toolbar.add(self._find_combo, padx=(0, 4))
        self._find_case = tk.BooleanVar(value=False)
        self._find_toolbar.add(
            ttk.Checkbutton(
                self._find_toolbar, text="区分大小写", variable=self._find_case,
                command=self._on_find_options_changed),
            padx=(0, 4))
        # 中文三字按钮需更宽，否则 ttk 会裁切导致字体显示异常
        self._find_toolbar.add(
            ttk.Button(
                self._find_toolbar, text="上一个", width=7,
                command=lambda: self._find_match(backward=True)),
            padx=(0, 2))
        self._find_toolbar.add(
            ttk.Button(
                self._find_toolbar, text="下一个", width=7,
                command=lambda: self._find_match(backward=False)))

        self._find_combo.bind("<Return>", lambda _e: self._find_match(backward=False))
        self._find_combo.bind("<Shift-Return>", lambda _e: self._find_match(backward=True))
        self._find_combo.bind("<KeyRelease>", self._on_find_key_release)
        self._find_combo.bind("<<ComboboxSelected>>", lambda _e: self._find_match(backward=False))
        self._find_var.trace_add("write", self._on_find_text_changed)

        # 接收区文本（自动换行，仅纵向滚动）
        text_frame = ttk.Frame(frame)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=(2, 0))
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.recv_text = tk.Text(
            text_frame, width=20, height=8, wrap=tk.WORD,
            font=self._recv_font, bg="#fafafa", fg="#1a1a1a",
            # 略深于背景，避免系统蓝选中与蓝色日志叠色
            selectbackground="#e6e6e6", selectforeground="#1a1a1a",
        )
        self.recv_text.grid(row=0, column=0, sticky=tk.NSEW)

        sb_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.recv_text.yview)
        sb_y.grid(row=0, column=1, sticky=tk.NS)
        self.recv_text.configure(yscrollcommand=sb_y.set)

        for tag, color in self.LEVEL_COLOR.items():
            self.recv_text.tag_configure(f"lv_{tag}", foreground=color)
        self.recv_text.tag_configure("find_match", background="#fff3a3")
        self.recv_text.tag_configure("find_current", background="#ffb347")

        self._setup_recv_text_bindings()

    def _setup_recv_text_bindings(self):
        """接收区只读：可选中/复制，禁止编辑；Ctrl+滚轮缩放字号。"""
        self.recv_text.bind("<Key>", self._on_recv_key)
        self.recv_text.bind("<<Paste>>", lambda _e: "break")
        self.recv_text.bind("<Control-v>", lambda _e: "break")
        self.recv_text.bind("<Control-f>", self._focus_find)
        self.recv_text.bind("<F3>", self._find_next_shortcut)
        self.recv_text.bind("<Shift-F3>", self._find_prev_shortcut)
        self.recv_text.bind("<Button-3>", self._show_recv_context_menu)
        # Windows 上 Ctrl+滚轮常以 MouseWheel+Control 位投递；两套绑定都接住
        self.recv_text.bind("<MouseWheel>", self._on_recv_wheel)
        self.recv_text.bind("<Control-MouseWheel>", self._on_recv_ctrl_wheel)
        self.recv_text.bind("<Control-Button-4>", self._on_recv_ctrl_wheel)
        self.recv_text.bind("<Control-Button-5>", self._on_recv_ctrl_wheel)
        self._recv_context_menu = tk.Menu(self.recv_text, tearoff=0)
        self._recv_context_menu.add_command(
            label="复制", command=self._copy_recv_selection, accelerator="Ctrl+C")
        self._recv_context_menu.add_command(
            label="全选", command=self._select_all_recv_text, accelerator="Ctrl+A")

    def _on_recv_wheel(self, event):
        """普通滚轮交给默认滚动；按住 Ctrl 时改为缩放字号。"""
        if event.state & 0x4:
            return self._on_recv_ctrl_wheel(event)
        return None

    def _on_recv_ctrl_wheel(self, event):
        """Ctrl + 滚轮：放大/缩小接收区 log 字号。"""
        delta = getattr(event, "delta", 0)
        if delta:
            step = 1 if delta > 0 else -1
        elif getattr(event, "num", None) == 4:
            step = 1
        elif getattr(event, "num", None) == 5:
            step = -1
        else:
            return "break"
        self._set_recv_font_size(self._recv_font_size + step)
        return "break"

    def get_recv_font_size(self) -> int:
        return int(self._recv_font_size)

    def set_recv_font_size(self, size: int):
        self._set_recv_font_size(size)

    def _set_recv_font_size(self, size: int):
        size = max(_RECV_FONT_MIN, min(_RECV_FONT_MAX, int(size)))
        if size == self._recv_font_size:
            return
        family = self._recv_font[0]
        self._recv_font_size = size
        self._recv_font = (family, scaled_point_size(size))
        try:
            self.recv_text.configure(font=self._recv_font)
        except tk.TclError:
            pass

    def _on_recv_key(self, event):
        """允许导航、选择、复制/全选/查找；拦截写入。"""
        if event.state & 0x4:
            k = event.keysym.lower()
            if k == "c":
                self._copy_recv_selection()
                return "break"
            if k == "a":
                self._select_all_recv_text()
                return "break"
            if k in ("insert",):
                return
            if k == "f":
                return self._focus_find(event)
        if event.keysym == "F3":
            if event.state & 0x1:
                return self._find_prev_shortcut(event)
            return self._find_next_shortcut(event)
        if event.keysym in (
            "Left", "Right", "Up", "Down", "Home", "End",
            "Prior", "Next", "Tab",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R", "Caps_Lock", "Num_Lock",
        ):
            return
        if event.char and event.char.isprintable():
            return "break"
        if event.keysym in ("BackSpace", "Delete", "Return", "KP_Enter", "space"):
            return "break"
        return

    def _show_recv_context_menu(self, event):
        try:
            self._recv_context_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self._recv_context_menu.grab_release()

    def _copy_recv_selection(self):
        try:
            text = self.recv_text.get(tk.SEL_FIRST, tk.SEL_LAST)
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
        except tk.TclError:
            pass

    def _select_all_recv_text(self):
        self.recv_text.tag_add(tk.SEL, "1.0", tk.END)
        self.recv_text.mark_set(tk.INSERT, "1.0")
        self.recv_text.see(tk.INSERT)

    def _build_send(self, parent):
        """发送框随分隔条伸缩；格式行固定在发送框下方。"""
        text_frame = ttk.Frame(parent)
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=0, pady=0)
        text_frame.columnconfigure(0, weight=1)
        text_frame.rowconfigure(0, weight=1)

        self.send_text = tk.Text(
            text_frame, width=20, height=3, wrap=tk.NONE, font=self._mono_font,
            bg="#fafafa", fg="#1a1a1a",
        )
        self.send_text.grid(row=0, column=0, sticky=tk.NSEW)

        sb_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.send_text.yview)
        sb_y.grid(row=0, column=1, sticky=tk.NS)
        self.send_text.configure(yscrollcommand=sb_y.set)

        sb_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.send_text.xview)
        sb_x.grid(row=1, column=0, sticky=tk.EW)
        self.send_text.configure(xscrollcommand=sb_x.set)
        ttk.Frame(text_frame, width=8).grid(row=1, column=1)

        # 发送区底栏：流式布局，窄了自动折行；相关控件成组换行
        opt = FlowFrame(parent)
        opt.pack(side=tk.TOP, fill=tk.X, padx=0, pady=0)
        self._send_opts = opt

        opt.add(ttk.Label(opt, text="格式"), padx=(0, 2))
        self.send_mode = tk.StringVar(value="ASCII")
        opt.add(ttk.Combobox(opt, textvariable=self.send_mode,
                             values=["ASCII", "HEX"], width=6, state="readonly"))
        self.append_lf = tk.BooleanVar(value=True)
        opt.add(ttk.Checkbutton(opt, text="\\n", variable=self.append_lf), padx=(2, 0))
        self.append_cr = tk.BooleanVar(value=False)
        opt.add(ttk.Checkbutton(opt, text="\\r", variable=self.append_cr), padx=(2, 0))
        self.auto_send = tk.BooleanVar(value=False)
        opt.add(ttk.Checkbutton(opt, text="定时(ms)", variable=self.auto_send), padx=(4, 0))
        self.auto_period = tk.StringVar(value="1000")
        opt.add(ttk.Entry(opt, textvariable=self.auto_period, width=5), padx=(2, 0))

        send_btn_grp = ttk.Frame(opt)
        ttk.Button(send_btn_grp, text="发送", command=self._do_send,
                   width=5).pack(side=tk.LEFT, padx=(0, 2))
        ttk.Button(send_btn_grp, text="清空", command=self.clear_send,
                   width=5).pack(side=tk.LEFT)
        opt.add(send_btn_grp, padx=(4, 0))

    def _build_action_area(self):
        parent = self._action_pane
        self.action_area = ttk.Frame(parent)
        self.action_area.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.action_content = ttk.Frame(self.action_area)
        self.action_content.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self._toggle_row = ttk.Frame(self.action_content)
        self._toggle_row.pack(side=tk.TOP, fill=tk.X, padx=0, pady=0)
        self._mode_label = ttk.Label(self._toggle_row, text="自由发送", foreground="#268bd2")
        self._mode_label.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_toggle_presets = ttk.Button(
            self._toggle_row, text="▶ 列表", command=self._toggle_action_mode, width=7,
        )
        self.btn_toggle_presets.pack(side=tk.LEFT, padx=(0, 6))
        self._traffic_stat_var = tk.StringVar(value="收0/0B 发0/0B")
        ttk.Label(
            self._toggle_row, textvariable=self._traffic_stat_var, foreground="#586e75",
            font=get_ui_fonts().mono_small,
        ).pack(side=tk.RIGHT)

        self._send_frame = ttk.Frame(self.action_content)
        self._build_send(self._send_frame)

        self.presets_container = ttk.Frame(self.action_content)
        self._populate_presets_inner()

        # 切换栏固定高度；发送/列表区 fill 剩余空间，随分隔条伸缩
        self._send_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, after=self._toggle_row)
        self.presets_visible = False
        self._show_send()

        self._auto_thread: Optional[threading.Thread] = None
        self._auto_stop = threading.Event()
        self._auto_send_running = False
        # 失败弹窗显示期间禁止重新拉起定时线程
        self._auto_block = False
        self.after(500, self._check_auto_send)

    def _toggle_action_mode(self):
        if self.presets_visible:
            self._show_send()
        else:
            self._show_presets()

    def _show_send(self):
        self.presets_container.pack_forget()
        self._send_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, after=self._toggle_row)
        self.presets_visible = False
        self.btn_toggle_presets.configure(text="▶ 列表")
        self._mode_label.configure(text="自由发送")

    def _show_presets(self):
        self._send_frame.pack_forget()
        self.presets_container.pack(
            side=tk.TOP, fill=tk.BOTH, expand=True, after=self._toggle_row)
        self.presets_visible = True
        self.btn_toggle_presets.configure(text="▼ 自由")
        self._mode_label.configure(text="列表发送")

    def apply_recv_send_split(self, recv_ratio: Optional[float] = None) -> bool:
        """按比例设置收发区分割条（接收区在上、发送区在下）。"""
        ratio = self.DEFAULT_RECV_RATIO if recv_ratio is None else recv_ratio
        ratio = max(0.20, min(float(ratio), 0.85))
        try:
            self.update_idletasks()
            total = self.winfo_height()
            if total < 160:
                return False
            action_min = self.get_action_block_min_height()
            recv_h = int(total * ratio)
            recv_h = max(80, min(recv_h, total - action_min))
            self.sashpos(0, recv_h)
            return True
        except tk.TclError:
            return False

    def get_recv_send_ratio(self) -> Optional[float]:
        """当前接收区高度占比；窗口过小时返回 None。"""
        try:
            total = self.winfo_height()
            if total < 160:
                return None
            return self.sashpos(0) / float(total)
        except tk.TclError:
            return None

    def get_action_block_min_height(self) -> int:
        """下方 action 区最小高度（发送框 + 格式行 + 切换栏）。"""
        try:
            self.update_idletasks()
            lines = max(int(self.send_text.cget("height")), 1)
            line_h = max(self.send_text.winfo_reqheight() // lines, 16)
            min_body = line_h * lines + 28
            return self._toggle_row.winfo_reqheight() + min_body + 4
        except tk.TclError:
            return 160

    def _ensure_preset_styles(self):
        """列表框和按键与下载路径框同一字号。"""
        font = get_ui_fonts().ui_small
        ttk.Style(self).configure("Preset.TButton", font=font, padding=(4, 0))
        self._preset_style_ready = True

    def toolbar_width_hint(self) -> int:
        """接收区工具栏不折行时需要的最小宽度（给分隔条初始位置用）。"""
        widths = []
        for bar in (getattr(self, "_toolbar1", None),
                    getattr(self, "_find_toolbar", None),
                    getattr(self, "_send_opts", None)):
            if bar is None:
                continue
            try:
                widths.append(bar.content_width())
            except tk.TclError:
                pass
        return max(widths, default=0)

    def refresh_fonts(self):
        """界面缩放后按新字号重设字体（已创建的控件不会自动重绘）。"""
        fonts = get_ui_fonts()
        self._mono_font = fonts.mono_normal
        self._recv_font = (fonts.mono, scaled_point_size(self._recv_font_size))
        for widget, font in (
            (getattr(self, "recv_text", None), self._recv_font),
            (getattr(self, "send_text", None), self._mono_font),
        ):
            if widget is None:
                continue
            try:
                widget.configure(font=font)
            except tk.TclError:
                pass
        self._ensure_preset_styles()
        # 预设行的输入框/下拉按新字体重建
        if hasattr(self, "_presets_rows_frame"):
            self._rebuild_preset_rows()
        # 字号变了，工具栏的 reqwidth 也变了：重新折行
        for bar in (getattr(self, "_toolbar1", None),
                    getattr(self, "_find_toolbar", None),
                    getattr(self, "_send_opts", None)):
            if bar is None:
                continue
            try:
                bar.reflow()
            except tk.TclError:
                pass

    def _apply_combo_font(self, combo: ttk.Combobox, font):
        try:
            combo.configure(font=font)
        except tk.TclError:
            return
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
            combo.tk.call(f"{popdown}.f.l", "configure", "-font", font)
        except tk.TclError:
            pass

    def _populate_presets_inner(self):
        self._ensure_preset_styles()
        toolbar = ttk.Frame(self.presets_container)
        toolbar.pack(fill=tk.X, padx=0, pady=(0, 1))
        for text, cmd in (
            ("添加", self._on_add_preset),
            ("编辑", self._on_edit_preset),
            ("删除", self._on_del_preset),
            ("清空", self._on_clear_presets),
        ):
            ttk.Button(
                toolbar, text=text, command=cmd, style="Preset.TButton",
            ).pack(side=tk.LEFT, padx=(0, 2))

        list_frame = ttk.Frame(self.presets_container)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        canvas = tk.Canvas(list_frame, highlightthickness=0)
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._presets_canvas = canvas
        self._presets_rows_frame = ttk.Frame(canvas)
        self._presets_window = canvas.create_window((0, 0), window=self._presets_rows_frame, anchor="nw")

        self._presets_rows_frame.bind(
            "<Configure>",
            lambda _e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind(
            "<Configure>",
            lambda e: canvas.itemconfigure(self._presets_window, width=e.width),
        )
        # 只滚列表自己。bind_all 不判断指针位置时，接收区滚轮也会带动这块画布。
        wheel_roots = (canvas, list_frame, self._presets_rows_frame)

        def _on_presets_wheel(event):
            if event.state & 0x4:  # Ctrl：留给接收区缩放字号
                return None
            w = event.widget
            while w is not None:
                if w in wheel_roots:
                    canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
                    return "break"
                w = getattr(w, "master", None)
            return None

        canvas.bind_all("<MouseWheel>", _on_presets_wheel)

        self._preset_row_menu = tk.Menu(canvas, tearoff=0)
        self._row_frames: List[ttk.Frame] = []
        self._row_vars: List[dict] = []

    def _rebuild_preset_rows(self):
        if not hasattr(self, "_presets_rows_frame"):
            return
        for child in self._presets_rows_frame.winfo_children():
            child.destroy()
        self._row_frames.clear()
        self._row_vars.clear()
        for i, p in enumerate(self._presets_data):
            self._add_preset_row(i, p)

    def _add_preset_row(self, idx: int, preset: dict):
        row = ttk.Frame(self._presets_rows_frame)
        row.pack(fill=tk.X, padx=2, pady=1)

        var = {
            "name": tk.StringVar(value=preset.get("name", "")),
            "mode": tk.StringVar(value=preset.get("mode", "ASCII")),
            "data": tk.StringVar(value=preset.get("data", "")),
        }

        field_font = get_ui_fonts().ui_small
        ttk.Entry(
            row, textvariable=var["name"], width=14, font=field_font,
        ).grid(row=0, column=0, padx=2)
        mode = ttk.Combobox(
            row, textvariable=var["mode"], values=["ASCII", "HEX"],
            width=6, state="readonly",
        )
        self._apply_combo_font(mode, field_font)
        mode.grid(row=0, column=1, padx=2)
        ttk.Entry(
            row, textvariable=var["data"], font=field_font,
        ).grid(row=0, column=2, padx=2, sticky=tk.EW)
        ttk.Button(
            row, text="发送", style="Preset.TButton",
            command=lambda v=var: self._send_from_row(v),
        ).grid(row=0, column=3, padx=2)
        ttk.Button(
            row, text="删", style="Preset.TButton",
            command=lambda i=idx: self._del_preset_row(i),
        ).grid(row=0, column=4, padx=2)

        row.columnconfigure(2, weight=1)
        self._bind_preset_row_context(row, idx)
        self._row_frames.append(row)
        self._row_vars.append(var)

    def _bind_preset_row_context(self, widget, idx: int):
        """整行（含输入框、下拉框、按钮）右击都弹出插入菜单。"""
        widget.bind("<Button-3>", lambda e, i=idx: self._show_preset_row_menu(e, i))
        for child in widget.winfo_children():
            self._bind_preset_row_context(child, idx)

    def _show_preset_row_menu(self, event, idx: int):
        menu = self._preset_row_menu
        menu.delete(0, "end")
        menu.add_command(
            label="在上面添加一行",
            command=lambda i=idx: self._insert_preset_row(i, below=False),
        )
        menu.add_command(
            label="在下面添加一行",
            command=lambda i=idx: self._insert_preset_row(i, below=True),
        )
        target = event.widget
        if target.winfo_class() in ("TEntry", "Entry"):
            menu.add_separator()
            menu.add_command(label="剪切", command=lambda w=target: w.event_generate("<<Cut>>"))
            menu.add_command(label="复制", command=lambda w=target: w.event_generate("<<Copy>>"))
            menu.add_command(label="粘贴", command=lambda w=target: w.event_generate("<<Paste>>"))
            menu.add_command(label="全选", command=lambda w=target: w.selection_range(0, "end"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _sync_presets_from_rows(self):
        """行内正在编辑的内容写回数据，避免重建时丢掉未保存的修改。"""
        rows = getattr(self, "_row_vars", None)
        if not rows:
            return
        self._presets_data = [
            {
                "name": var["name"].get(),
                "mode": var["mode"].get() or "ASCII",
                "data": var["data"].get(),
            }
            for var in rows
        ]

    def _insert_preset_row(self, idx: int, below: bool):
        self._sync_presets_from_rows()
        if not self._presets_data:
            return
        idx = max(0, min(int(idx), len(self._presets_data) - 1))
        mode = self._presets_data[idx].get("mode") or "ASCII"
        insert_at = idx + 1 if below else idx
        self._presets_data.insert(insert_at, {"name": "", "mode": mode, "data": ""})
        self._rebuild_preset_rows()
        self._focus_preset_row(insert_at)

    def _focus_preset_row(self, idx: int):
        if not (0 <= idx < len(self._row_frames)):
            return
        self._scroll_preset_row_into_view(idx)
        for child in self._row_frames[idx].winfo_children():
            if child.winfo_class() in ("TEntry", "Entry"):
                child.focus_set()
                child.icursor("end")
                break

    def _scroll_preset_row_into_view(self, idx: int):
        canvas = getattr(self, "_presets_canvas", None)
        if canvas is None or not (0 <= idx < len(self._row_frames)):
            return
        try:
            canvas.update_idletasks()
            row = self._row_frames[idx]
            y = row.winfo_y()
            height = max(row.winfo_height(), 1)
            total = max(self._presets_rows_frame.winfo_height(), 1)
            view_top = canvas.canvasy(0)
            view_h = max(canvas.winfo_height(), 1)
            if y < view_top:
                canvas.yview_moveto(y / total)
            elif y + height > view_top + view_h:
                canvas.yview_moveto(max(0.0, (y + height - view_h) / total))
        except tk.TclError:
            pass

    def _send_from_row(self, var: dict):
        mode = var["mode"].get()
        data = var["data"].get()
        self._do_send_raw(mode=mode, data=data)

    def _del_preset_row(self, idx: int):
        self._sync_presets_from_rows()
        if 0 <= idx < len(self._presets_data):
            del self._presets_data[idx]
            self._rebuild_preset_rows()

    def _on_clear_presets(self):
        """清空「列表发送」的全部条目：有内容时先弹窗确认。

        弹窗挂在 app 的 toplevel 上，Windows 会把它居中在主窗口内，
        不会跑到屏幕外。
        """
        if not self._presets_data:
            return
        parent = self.winfo_toplevel()
        ok = messagebox.askyesno(
            "清空列表发送",
            f"确定清空列表发送的全部 {len(self._presets_data)} 条吗？\n\n"
            "清空后无法恢复（下次启动会恢复为内置默认项）。",
            parent=parent,
        )
        if not ok:
            return
        self._presets_data.clear()
        self._rebuild_preset_rows()

    def clear_recv(self, clear_buffer: bool = True):
        self._cancel_text_insert()
        self._append_pending.clear()
        self.recv_text.delete("1.0", tk.END)
        self._find_pos = "1.0"
        self._find_last_start = None
        self._find_last_end = None
        self._clear_find_highlight()
        self._end_find_session()
        if clear_buffer:
            self._reset_recv_stat()
            if self._on_clear_recv is not None:
                self._on_clear_recv()

    def set_defer_scroll(self, defer: bool):
        self._defer_scroll = defer

    def _cancel_text_insert(self):
        self._text_insert_gen += 1
        self.set_defer_scroll(False)

    def load_log_bytes(self, data: bytes, clear_buffer: bool = True,
                       on_done: Optional[Callable[[], None]] = None):
        """纯文本 Log 原样显示（无 ID 帧时不追加时间戳、不写自动保存）。"""
        content = None
        last_err = None
        for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
            try:
                content = data.decode(enc)
                break
            except UnicodeDecodeError as e:
                last_err = e
        if content is None:
            content = data.decode("latin-1", errors="replace")
            if last_err is not None and not content.strip():
                raise last_err
        self.load_log_content(content, clear_buffer=clear_buffer, on_done=on_done)

    def load_log_content(self, content: str, clear_buffer: bool = True,
                         on_done: Optional[Callable[[], None]] = None):
        """将 Log 文本载入接收区（替换现有内容）。"""
        self._cancel_text_insert()
        self.clear_recv(clear_buffer=clear_buffer)
        self._end_find_session()
        if not content:
            if on_done:
                on_done()
            return
        if len(content) >= _LARGE_LOG_CHARS:
            lines = content.splitlines(keepends=True)
            if not lines:
                if on_done:
                    on_done()
                return
            self.set_defer_scroll(True)
            self._insert_text_lines_async(lines, 0, on_done)
            return
        try:
            self.recv_text.insert("1.0", content)
            if self.auto_scroll.get():
                self.recv_text.see(tk.END)
        except tk.TclError:
            pass
        if on_done:
            on_done()

    def _insert_text_lines_async(self, lines: List[str], start: int,
                                 on_done: Optional[Callable[[], None]],
                                 gen: Optional[int] = None):
        if gen is None:
            gen = self._text_insert_gen
        if gen != self._text_insert_gen:
            return
        end = min(start + _TEXT_INSERT_LINES, len(lines))
        block = "".join(lines[start:end])
        try:
            if start == 0:
                self.recv_text.insert("1.0", block)
            else:
                self.recv_text.insert(tk.END, block)
        except tk.TclError:
            pass
        if end < len(lines):
            self.after(
                1, lambda: self._insert_text_lines_async(lines, end, on_done, gen))
            return
        self.set_defer_scroll(False)
        if self.auto_scroll.get():
            try:
                self.recv_text.see(tk.END)
            except tk.TclError:
                pass
        if on_done:
            on_done()

    # ---------- 接收区查找 ----------

    def set_find_history(self, items: List[str]):
        seen = set()
        self._find_history = []
        for item in items:
            term = str(item).strip()
            if not term or term in seen:
                continue
            seen.add(term)
            self._find_history.append(term)
            if len(self._find_history) >= _FIND_HISTORY_MAX:
                break
        self._find_combo.configure(values=self._find_history)

    def get_find_history(self) -> List[str]:
        return list(self._find_history)

    @staticmethod
    def encode_find_history(items: List[str]) -> str:
        out = []
        seen = set()
        for item in items:
            term = str(item).strip()
            if not term or term in seen:
                continue
            seen.add(term)
            out.append(term.replace(_FIND_HISTORY_SEP, " "))
            if len(out) >= _FIND_HISTORY_MAX:
                break
        return _FIND_HISTORY_SEP.join(out)

    @staticmethod
    def decode_find_history(raw: str) -> List[str]:
        if not raw or not str(raw).strip():
            return []
        return [p.strip() for p in str(raw).split(_FIND_HISTORY_SEP) if p.strip()]

    def set_find_case_sensitive(self, enabled: bool):
        self._find_case.set(bool(enabled))

    def get_find_case_sensitive(self) -> bool:
        return bool(self._find_case.get())

    def _show_find_bar(self, focus: bool = True):
        if not self._find_bar_visible:
            self._find_toolbar.pack(
                fill=tk.X, padx=2, pady=(2, 0), after=self._toolbar1)
            self._find_bar_visible = True
        if focus:
            self._find_combo.focus_set()
            try:
                self._find_combo.selection_range(0, tk.END)
            except tk.TclError:
                pass

    def _hide_find_bar(self):
        if self._find_bar_visible:
            self._find_toolbar.pack_forget()
            self._find_bar_visible = False
            self._clear_find_highlight()
            self._end_find_session()

    def _toggle_find_bar(self):
        if self._find_bar_visible:
            self._hide_find_bar()
        else:
            self._show_find_bar(focus=True)

    def _focus_find(self, _event=None):
        self._show_find_bar(focus=True)
        return "break"

    def _find_next_shortcut(self, _event=None):
        self._show_find_bar(focus=False)
        self._find_match(backward=False)
        return "break"

    def _find_prev_shortcut(self, _event=None):
        self._show_find_bar(focus=False)
        self._find_match(backward=True)
        return "break"

    def _begin_find_session(self):
        if self._find_active:
            return
        self._auto_scroll_saved = self.auto_scroll.get()
        self.auto_scroll.set(False)
        self._find_active = True

    def _end_find_session(self):
        if not self._find_active:
            return
        if self._auto_scroll_saved is not None:
            self.auto_scroll.set(self._auto_scroll_saved)
        self._auto_scroll_saved = None
        self._find_active = False

    def _clear_find_highlight(self):
        try:
            self.recv_text.tag_remove("find_match", "1.0", tk.END)
            self.recv_text.tag_remove("find_current", "1.0", tk.END)
        except tk.TclError:
            pass

    def _clear_find(self):
        self._find_var.set("")
        self._find_pos = "1.0"
        self._find_last_start = None
        self._find_last_end = None
        self._clear_find_highlight()
        self._end_find_session()

    def _on_find_text_changed(self, *_args):
        self._find_last_start = None
        self._find_last_end = None

    def _on_find_key_release(self, _event=None):
        if not self._find_var.get().strip():
            self._clear_find()

    def _on_find_options_changed(self):
        if self._find_var.get().strip():
            self._find_last_start = None
            self._find_last_end = None
            self._find_pos = "1.0"
            self._find_match(backward=False)

    def _push_find_history(self, term: str):
        term = term.strip()
        if not term:
            return
        if term in self._find_history:
            self._find_history.remove(term)
        self._find_history.insert(0, term)
        del self._find_history[_FIND_HISTORY_MAX:]
        self._find_combo.configure(values=self._find_history)

    def _find_match(self, backward: bool = False):
        pattern = self._find_var.get()
        if not pattern:
            self._clear_find()
            return

        self._begin_find_session()
        self._push_find_history(pattern)

        nocase = not self._find_case.get()
        if backward:
            if self._find_last_start:
                start = self.recv_text.index(f"{self._find_last_start} - 1c")
            else:
                start = tk.END
            pos = self.recv_text.search(
                pattern, start, stopindex="1.0", backwards=True,
                regexp=False, nocase=nocase,
            )
            if not pos:
                pos = self.recv_text.search(
                    pattern, tk.END, stopindex="1.0", backwards=True,
                    regexp=False, nocase=nocase,
                )
        else:
            if self._find_last_end:
                start = self._find_last_end
            else:
                start = "1.0"
            pos = self.recv_text.search(
                pattern, start, stopindex=tk.END, forwards=True,
                regexp=False, nocase=nocase,
            )
            if not pos:
                pos = self.recv_text.search(
                    pattern, "1.0", stopindex=tk.END, forwards=True,
                    regexp=False, nocase=nocase,
                )

        self._clear_find_highlight()
        if not pos:
            self.recv_text.bell()
            return

        end = f"{pos}+{len(pattern)}c"
        try:
            self.recv_text.tag_add("find_current", pos, end)
            self.recv_text.see(pos)
            self.recv_text.mark_set(tk.INSERT, pos)
        except tk.TclError:
            pass

        self._find_last_start = pos
        self._find_last_end = end

    # ---------- flush timeout 控制 ----------

    def get_flush_timeout_ms(self) -> int:
        try:
            v = int(self.flush_timeout_var.get())
        except (tk.TclError, ValueError):
            return 100
        return max(0, v)

    def set_flush_timeout_ms(self, ms: int):
        """设置 Spinbox 显示值并通知回调；未打开串口时回调是 no-op。"""
        ms = max(0, int(ms))
        try:
            self.flush_timeout_var.set(ms)
        except (tk.TclError, ValueError):
            pass
        self._notify_flush_timeout(ms)

    def _on_flush_timeout_changed(self, *_args):
        ms = self.get_flush_timeout_ms()
        self._notify_flush_timeout(ms)

    def _notify_flush_timeout(self, ms: int):
        cb = getattr(self, "_flush_timeout_callback", None)
        if cb is not None:
            try:
                cb(ms)
            except Exception:
                pass

    def bind_flush_timeout_callback(self, cb: Optional[Callable[[int], None]]):
        """注册回调：UI 里的 ms 值变化时（Spinbox 修改 / set_flush_timeout_ms）会被调用。"""
        self._flush_timeout_callback = cb

    def _on_show_mode_changed(self, *_args):
        cb = getattr(self, "_show_mode_callback", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            pass

    def bind_show_mode_callback(self, cb: Optional[Callable[[], None]]):
        """显示模式 ASCII/HEX 变化时重画接收区。"""
        self._show_mode_callback = cb

    # ---------- 文件保存 ----------

    def _on_load_log_clicked(self):
        if self._on_load_log is not None:
            self._on_load_log()

    @staticmethod
    def _default_save_path() -> str:
        logs = get_logs_dir()
        os.makedirs(logs, exist_ok=True)
        return os.path.join(logs, f"serial_{datetime.now():%Y%m%d_%H%M%S}.log")

    def _on_auto_save_toggled(self):
        if self.auto_save.get():
            self._start_auto_save()
        else:
            self._close_save_file()
            self._post_status("INFO", "日志自动保存已关闭")

    def _start_auto_save(self):
        """开启自动保存：重新生成带时间戳的新文件并打开（每次开启都是新文件）"""
        try:
            path = self._default_save_path()
            self.save_path.set(path)
            self._close_save_file()
            self._save_fp = open(path, "a", encoding="utf-8")
            self._post_status("INFO", f"日志自动保存已开启: {path}")
        except Exception as e:
            messagebox.showerror("自动保存失败", str(e))
            self.auto_save.set(False)

    def restart_auto_save(self):
        """串口会话开始时调用：若自动保存开启，切换到新的日志文件"""
        if self.auto_save.get():
            self._start_auto_save()

    def _close_save_file(self):
        if self._save_fp is not None:
            try:
                self.flush_pending()
                self._save_fp.flush()
                self._save_fp.close()
            except Exception:
                pass
            self._save_fp = None
            self._save_pending_lines = 0

    def _choose_save_file(self):
        init_dir = os.path.dirname(self.save_path.get()) or get_logs_dir()
        init_file = os.path.basename(self.save_path.get())
        path = filedialog.asksaveasfilename(
            parent=self, title="选择日志保存路径",
            initialdir=init_dir, initialfile=init_file,
            defaultextension=".log",
            filetypes=[("Log 文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        self.save_path.set(path)
        # 自动保存开启中切换文件：关掉旧文件，重新打开新文件
        if self.auto_save.get():
            self._close_save_file()
            try:
                self._save_fp = open(path, "a", encoding="utf-8")
                self._post_status("INFO", f"日志自动保存路径已切换: {path}")
            except Exception as e:
                messagebox.showerror("自动保存失败", str(e))
                self.auto_save.set(False)

    def save_recv_to_file(self):
        """手动把当前接收区内容另存为文件"""
        content = self.recv_text.get("1.0", tk.END)
        if not content.strip():
            messagebox.showinfo("提示", "接收区为空")
            return
        init_dir = os.path.dirname(self.save_path.get()) or get_logs_dir()
        init_file = os.path.basename(self.save_path.get())
        path = filedialog.asksaveasfilename(
            parent=self, title="接收区另存为",
            initialdir=init_dir, initialfile=init_file,
            defaultextension=".log",
            filetypes=[("Log 文件", "*.log"), ("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            self._post_status("INFO", f"接收区已保存: {path}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def clear_send(self):
        self.send_text.delete("1.0", tk.END)
        self._reset_send_stat()

    def _format_data_bytes(self, n: int) -> str:
        if n < 1024:
            return f"{n}B"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f}K"
        return f"{n / (1024 * 1024):.2f}M"

    def _refresh_traffic_stat(self):
        self._traffic_stat_var.set(
            f"收{self._recv_count}/{self._format_data_bytes(self._recv_bytes)} "
            f"发{self._send_count}/{self._format_data_bytes(self._send_bytes)}",
        )

    def note_recv_bytes(self, nbytes: int):
        if nbytes <= 0:
            return
        self._recv_count += 1
        self._recv_bytes += nbytes
        self._refresh_traffic_stat()

    def _update_send_stat(self, nbytes: int):
        self._send_count += 1
        self._send_bytes += nbytes
        self._refresh_traffic_stat()

    def _reset_recv_stat(self):
        self._recv_count = 0
        self._recv_bytes = 0
        self._refresh_traffic_stat()

    def _reset_send_stat(self):
        self._send_count = 0
        self._send_bytes = 0
        self._refresh_traffic_stat()

    def set_presets(self, presets: List[dict]):
        self._presets_data = list(presets)
        if not self._presets_data:
            self._presets_data = self._default_presets()
        if hasattr(self, "_presets_rows_frame"):
            self._rebuild_preset_rows()

    def collect_presets(self) -> List[dict]:
        out = []
        for var in getattr(self, "_row_vars", []):
            out.append({
                "name": var["name"].get(),
                "mode": var["mode"].get(),
                "data": var["data"].get(),
            })
        return out

    @staticmethod
    def _default_presets() -> List[dict]:
        return [
            {"name": "AT",       "mode": "ASCII", "data": "AT"},
            {"name": "ATE1",     "mode": "ASCII", "data": "ATE1"},
            {"name": "查询版本",  "mode": "ASCII", "data": "AT+VER"},
            {"name": "重启",     "mode": "ASCII", "data": "AT+RST"},
            {"name": "Ping",     "mode": "HEX",   "data": "AA 55 00 01"},
        ]

    def _on_add_preset(self):
        dlg = PresetDialog(self, title="添加预设")
        self.wait_window(dlg.top)
        if dlg.result:
            self._sync_presets_from_rows()
            self._presets_data.append(dlg.result)
            self._rebuild_preset_rows()

    def _on_edit_preset(self):
        self._sync_presets_from_rows()
        sel = self._presets_rows_frame.focus_get()
        idx = None
        for i, row in enumerate(self._row_frames):
            if sel in row.winfo_children() or any(c == sel for c in row.winfo_children()):
                idx = i
                break
        if idx is None or idx >= len(self._presets_data):
            return
        cur = self._presets_data[idx]
        dlg = PresetDialog(self, title="编辑预设", init=cur)
        self.wait_window(dlg.top)
        if dlg.result:
            self._presets_data[idx] = dlg.result
            self._rebuild_preset_rows()

    def _on_del_preset(self):
        self._sync_presets_from_rows()
        if self._presets_data:
            self._presets_data.pop()
            self._rebuild_preset_rows()

    def _do_send(self):
        err = self._try_free_send()
        if err:
            self._show_send_error(*err)

    def _try_free_send(self):
        """发送自由区内容。成功或无内容返回 None，失败返回 (标题, 内容, 是否警告)。"""
        mode = self.send_mode.get()
        data = self.send_text.get("1.0", tk.END).rstrip("\n")
        suffix = b""
        if self.append_cr.get():
            suffix += b"\r"
        if self.append_lf.get():
            suffix += b"\n"
        if not data and not suffix:
            return None
        try:
            payload = self._parse_payload(data, mode) if data else b""
        except Exception as e:
            return ("发送失败", f"解析失败: {e}", False)
        payload += suffix
        return self._send_to_comm(payload)

    def _do_send_raw(self, mode: str, data: str):
        if not data:
            return
        try:
            payload = self._parse_payload(data, mode)
        except Exception as e:
            self._show_send_error("发送失败", f"解析失败: {e}", False)
            return
        err = self._send_to_comm(payload)
        if err:
            self._show_send_error(*err)

    def _show_send_error(self, title: str, message: str, warning: bool):
        parent = self.winfo_toplevel()
        if warning:
            messagebox.showwarning(title, message, parent=parent)
        else:
            messagebox.showerror(title, message, parent=parent)

    @staticmethod
    def _parse_payload(data: str, mode: str) -> bytes:
        """解析发送内容，支持转义字符。

        ASCII 模式：除 \\n/\\r/\\t/\\0/\\\\/\\xHH 之外的字符按 latin-1 编码（每个字符一字节，
                   越界字符替换为 '?'）。转义字节按出现顺序插入。
        HEX  模式：跳过空白；其余字符累积为 hex 串；转义序列按出现顺序插入对应字节。

        转义规则（两种模式通用）：
          \\n → 0x0A   \\r → 0x0D   \\t → 0x09   \\0 → 0x00
          \\\\ → 0x5C   \\xHH → 0xHH
          其它 \\X 原样保留（不识别）
        """
        out = bytearray()
        ascii_buf: List[str] = []
        hex_buf: List[str] = []

        def flush_ascii():
            if ascii_buf:
                out.extend("".join(ascii_buf).encode("latin-1", errors="replace"))
                ascii_buf.clear()

        def flush_hex():
            if not hex_buf:
                return
            s = "".join(hex_buf)
            if len(s) % 2 != 0:
                raise ValueError(f"HEX 长度必须为偶数: '{s}'")
            try:
                out.extend(bytes.fromhex(s))
            except ValueError as e:
                raise ValueError(f"HEX 解析失败: {e}")
            hex_buf.clear()

        i = 0
        while i < len(data):
            c = data[i]
            if c == '\\' and i + 1 < len(data):
                nxt = data[i + 1]
                if nxt == 'n':
                    flush_ascii(); flush_hex(); out.append(0x0A); i += 2
                elif nxt == 'r':
                    flush_ascii(); flush_hex(); out.append(0x0D); i += 2
                elif nxt == 't':
                    flush_ascii(); flush_hex(); out.append(0x09); i += 2
                elif nxt == '0':
                    flush_ascii(); flush_hex(); out.append(0x00); i += 2
                elif nxt == 'x' and i + 3 < len(data):
                    try:
                        b = int(data[i + 2:i + 4], 16)
                    except ValueError:
                        # 不是合法 hex，按字面 '\' 处理
                        if mode == "HEX":
                            hex_buf.append(c)
                        else:
                            ascii_buf.append(c)
                        i += 1
                    else:
                        flush_ascii(); flush_hex(); out.append(b); i += 4
                elif nxt == '\\':
                    flush_ascii(); flush_hex(); out.append(0x5C); i += 2
                else:
                    # 未知转义：保留 '\' 原样，下一轮循环自己处理 nxt
                    if mode == "HEX":
                        hex_buf.append(c)
                    else:
                        ascii_buf.append(c)
                    i += 1
            else:
                if mode == "HEX":
                    if c in ' \t\r\n':
                        flush_hex()  # 空白触发 hex 缓冲 flush
                    else:
                        hex_buf.append(c)
                else:
                    ascii_buf.append(c)
                i += 1

        flush_ascii()
        flush_hex()
        return bytes(out)

    def _send_to_comm(self, payload: bytes):
        """成功返回 None，失败返回 (标题, 内容, 是否警告)。"""
        comm = self._get_active_comm() if self._get_active_comm else None
        if comm is None or not getattr(comm, "is_open", False):
            return ("未打开", "请先打开串口", True)
        try:
            comm.send(payload)
            self._update_send_stat(len(payload))
        except Exception as e:
            return ("发送失败", str(e), False)
        return None

    def _encode_bytes(self, data: bytes) -> str:
        if self.show_mode.get() == "HEX":
            return " ".join(f"{b:02X}" for b in data)
        try:
            return data.decode(self.encoding, errors="replace")
        except Exception:
            return data.hex(" ")

    def format_host_ts(self) -> str:
        """主机接收时间，形如 [HH:MM:SS.mmm] 。不受“时间戳”勾选影响。"""
        now = datetime.now()
        return now.strftime("[%H:%M:%S.") + f"{now.microsecond // 1000:03d}] "

    def _ts(self) -> str:
        if not self.show_ts.get():
            return ""
        return self.format_host_ts()

    def _line_prefix(self, add_ts: bool = True, ts_prefix: Optional[str] = None) -> str:
        if ts_prefix is not None:
            return ts_prefix
        return self._ts() if add_ts else ""

    def feed_recv(self, data: bytes):
        if not data:
            return
        text = self._ts() + self._encode_bytes(data)
        self._append(text, tag="lv_INFO")

    def feed_line(self, line: bytes, add_ts: bool = True,
                  ts_prefix: Optional[str] = None, save: bool = True):
        if not line:
            return
        if self.show_mode.get() == "HEX":
            # 保留 0D/0A，十六进制要能看到换行字节
            text = self._encode_bytes(line)
        else:
            line_clean = line.rstrip(b"\r\n")
            try:
                text = line_clean.decode(self.encoding, errors="replace")
            except Exception:
                text = line_clean.hex(" ")
        prefix = self._line_prefix(add_ts, ts_prefix)
        self._append(prefix + text, tag="lv_INFO", save=save)

    def _post_status(self, level: str, msg: str):
        if self._on_status is not None:
            self._on_status(level, msg)

    def feed_log(self, level: str, msg: str, add_ts: bool = True,
                 head: Optional[str] = None, ts_prefix: Optional[str] = None,
                 save: bool = True):
        prefix = self._line_prefix(add_ts, ts_prefix)
        label = head if head else f"[{level}]"
        self._append(prefix + f"{label} {msg}", tag=f"lv_{level}", save=save)

    def _append(self, line: str, tag: str = "", save: bool = True):
        self._append_pending.append((line, tag, save))
        if len(self._append_pending) >= _APPEND_BATCH:
            self.flush_pending()

    def flush_pending(self):
        """批量写入接收区；实时串口 drain / 重放 tick 结束时调用。"""
        if not self._append_pending:
            return
        pending = self._append_pending
        self._append_pending = []
        save_lines: List[str] = []
        try:
            for line, tag, save in pending:
                if not line.endswith("\n"):
                    line = line + "\n"
                if tag:
                    self.recv_text.insert(tk.END, line, tag)
                else:
                    self.recv_text.insert(tk.END, line)
                if save:
                    save_lines.append(line)
            self._trim_recv_text()
            if (self.auto_scroll.get() and not self._find_active
                    and not self._defer_scroll):
                self.recv_text.see(tk.END)
        except Exception:
            pass
        if self._save_fp is not None and save_lines:
            try:
                self._save_fp.writelines(save_lines)
                self._save_pending_lines += len(save_lines)
                if self._save_pending_lines >= _SAVE_FLUSH_LINES:
                    self._save_fp.flush()
                    self._save_pending_lines = 0
            except Exception:
                pass

    def _trim_recv_text(self):
        """接收区超过上限时删最旧内容，避免 Text 控件无限膨胀拖慢 UI。"""
        try:
            nchars = len(self.recv_text.get("1.0", "end-1c"))
            if nchars <= _RECV_TEXT_MAX_CHARS:
                return
            cut = nchars - int(_RECV_TEXT_MAX_CHARS * 0.875)
            if cut <= 0:
                return
            self.recv_text.delete("1.0", f"1.0+{cut}c")
        except tk.TclError:
            pass

    def start(self):
        pass

    def stop(self):
        self.flush_pending()
        self._close_save_file()
        self._auto_stop.set()
        if self._auto_thread is not None:
            self._auto_thread.join(timeout=1.0)
            self._auto_thread = None

    def _check_auto_send(self):
        try:
            if not self._auto_block:
                want_auto = self.auto_send.get()
                running = self._auto_thread is not None and self._auto_thread.is_alive()
                if want_auto and not running:
                    self._auto_stop.clear()
                    self._auto_thread = threading.Thread(
                        target=self._auto_send_loop, daemon=True)
                    self._auto_thread.start()
                elif not want_auto and running:
                    self._auto_stop.set()
        except Exception:
            pass
        self.after(500, self._check_auto_send)

    def _auto_send_loop(self):
        self._auto_stop.clear()
        while not self._auto_stop.is_set() and self.auto_send.get():
            try:
                err = self._try_free_send()
            except Exception as e:
                err = ("发送失败", str(e), False)
            if err:
                self._auto_stop.set()
                self._auto_block = True
                title, message, warning = err
                try:
                    self.after(
                        0,
                        lambda t=title, m=message, w=warning: self._on_auto_send_failed(t, m, w),
                    )
                except tk.TclError:
                    self._auto_block = False
                break
            try:
                period = max(50, int(self.auto_period.get()))
            except Exception:
                period = 1000
            self._auto_stop.wait(period / 1000.0)

    def _on_auto_send_failed(self, title: str, message: str, warning: bool):
        """定时发送失败：弹一次窗，关闭后取消勾选，避免按周期反复弹出。"""
        self._auto_stop.set()
        self._auto_block = True
        try:
            self._show_send_error(title, message, warning)
        finally:
            try:
                self.auto_send.set(False)
            except tk.TclError:
                pass
            self._auto_block = False


def _host_screen_rect(host: tk.Misc) -> tuple:
    """主窗口在虚拟屏幕上的矩形。最大化时 Tk 的 winfo_rootx 会落在另一块屏。"""
    if sys.platform == "win32":
        rect = _win32_window_rect(host)
        if rect is not None:
            return rect
    x = host.winfo_rootx()
    y = host.winfo_rooty()
    return x, y, x + max(1, host.winfo_width()), y + max(1, host.winfo_height())


def _win32_window_rect(host: tk.Misc):
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

    try:
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        hwnd = wintypes.HWND(int(host.winfo_id()))
        rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        left, top, right, bottom = int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)
    except (OSError, tk.TclError, ValueError, OverflowError):
        return None
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


class PresetDialog:
    def __init__(self, parent, title: str, init: Optional[dict] = None):
        self.result = None
        host = parent.winfo_toplevel()
        self.top = tk.Toplevel(host)
        self.top.title(title)
        self.top.transient(host)
        self.top.withdraw()

        init = init or {}

        ttk.Label(self.top, text="名称:").grid(row=0, column=0, padx=4, pady=4, sticky=tk.W)
        self.name = tk.StringVar(value=init.get("name", ""))
        self.name_entry = ttk.Entry(self.top, textvariable=self.name, width=30)
        self.name_entry.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(self.top, text="格式:").grid(row=1, column=0, padx=4, pady=4, sticky=tk.W)
        self.mode = tk.StringVar(value=init.get("mode", "ASCII"))
        ttk.Combobox(self.top, textvariable=self.mode,
                     values=["ASCII", "HEX"], width=10, state="readonly").grid(
            row=1, column=1, padx=4, pady=4, sticky=tk.W)

        ttk.Label(self.top, text="内容:").grid(row=2, column=0, padx=4, pady=4, sticky=tk.NW)
        self.data = tk.Text(self.top, width=40, height=5, font=get_ui_fonts().mono_normal)
        self.data.grid(row=2, column=1, padx=4, pady=4)
        self.data.insert("1.0", init.get("data", ""))

        btns = ttk.Frame(self.top)
        btns.grid(row=3, column=0, columnspan=2, pady=8)
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side=tk.LEFT, padx=8)

        self.top.bind("<Return>", lambda _e: self._on_ok())
        self.top.bind("<Escape>", lambda _e: self.top.destroy())

        # 先定位再显示、再 grab：否则窗口管理器会把弹窗丢到别的屏幕
        self._center_over(host)
        self.top.deiconify()
        self.top.grab_set()
        self.top.focus_set()
        self.name_entry.focus_set()

    def _center_over(self, host):
        """把弹窗居中到主窗口上（跟随主窗口所在显示器）。"""
        self.top.update_idletasks()
        w = self.top.winfo_reqwidth()
        h = self.top.winfo_reqheight()
        left, top, right, bottom = _host_screen_rect(host)
        px = left + max(0, (right - left - w) // 2)
        py = top + max(0, (bottom - top - h) // 2)
        self.top.geometry(f"+{px}+{py}")

    def _on_ok(self):
        name = self.name.get().strip()
        data = self.data.get("1.0", tk.END).rstrip("\n").rstrip()
        if not name:
            messagebox.showwarning("提示", "请输入名称", parent=self.top)
            return
        self.result = {"name": name, "mode": self.mode.get(), "data": data}
        self.top.destroy()
