import os
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from tkinter import ttk, filedialog, messagebox
from typing import Callable, Dict, List, Optional

from utils.app_paths import dialog_initial_dir

from .theme import ellipsize, get_ui_fonts, scale_px

_STATUS_MAX_LINES = 400


class DownloadPanel(ttk.LabelFrame):
    BINARY_KEYS = ("programmer", "bootloader", "mcu_a", "mcu_b", "mcu_c")
    KEY_DISPLAY = {
        "programmer": "prog",
        "bootloader": "boot",
        "mcu_a": "mcua",
        "mcu_b": "mcub",
        "mcu_c": "mcuc",
    }
    # 统一列宽，使各行列文件名左缘对齐（含 MCU_A.bin 与全小写 bin）
    _KEY_COL_WIDTH = 5
    _LEAD_COL_MINSIZE = 26   # 与 Checkbutton 占位一致
    _COLOR_AUTO = "#268bd2"
    _COLOR_MANUAL = "#b58900"
    _COLOR_OK = "#859900"
    _COLOR_ERR = "#dc322f"
    _COLOR_MUTED = "#888"

    @classmethod
    def display_key(cls, key: str) -> str:
        return cls.KEY_DISPLAY.get(key, key)

    def __init__(self, master,
                 get_chip_files: Optional[Callable[[str], Dict[str, dict]]] = None,
                 chip_types: Optional[List[str]] = None,
                 on_download: Optional[Callable[[dict], None]] = None,
                 on_cancel: Optional[Callable[[], None]] = None,
                 on_refresh: Optional[Callable[[], None]] = None,
                 on_bin_selection_changed: Optional[Callable[[], None]] = None,
                 on_reload_bin_selection: Optional[Callable[[], None]] = None):
        super().__init__(master, text="下载")
        self._on_download = on_download
        self._on_cancel = on_cancel
        self._on_refresh = on_refresh
        self._on_bin_selection_changed = on_bin_selection_changed
        self._on_reload_bin_selection = on_reload_bin_selection
        self._get_chip_files = get_chip_files or (lambda _chip: {})
        self._chip_files = self._get_chip_files(chip_types[0] if chip_types else "")
        self._chip_types = chip_types or ["9032", "9062"]
        self._check_vars: Dict[str, tk.BooleanVar] = {}
        self._bin_selected: Dict[str, bool] = {key: False for key in self.BINARY_KEYS}
        self._file_labels: Dict[str, ttk.Label] = {}
        self._status_labels: Dict[str, ttk.Label] = {}
        self._file_buttons: Dict[str, ttk.Button] = {}
        self._reset_buttons: Dict[str, ttk.Button] = {}
        self._id_log_labels: Dict[str, ttk.Label] = {}
        self._id_log_status: Dict[str, ttk.Label] = {}
        self._id_log_buttons: Dict[str, ttk.Button] = {}
        self._id_log_reset_buttons: Dict[str, ttk.Button] = {}
        self._full_text: Dict[str, str] = {}          # bin 文件名完整文本（截断前）
        self._full_idlog_text: Dict[str, str] = {}    # id log 文件名完整文本
        self._fit_font: Optional[tkfont.Font] = None   # 截断用的字体度量（缩放后重建）
        self._fit_job: Optional[str] = None            # after_idle 合并多次重排
        self._resolved: Dict[str, Optional[str]] = {}     # bin 文件名
        self._paths: Dict[str, Optional[str]] = {}        # bin 绝对路径（手动选择时）
        self._manual_selection: Dict[str, bool] = {}      # bin 手动选择标记
        self._id_log_paths: Dict[str, Optional[str]] = {} # id_log 绝对路径（手动选择时）
        self._id_log_manual: Dict[str, bool] = {}         # id_log 手动选择标记

        dir_frame = ttk.Frame(self)
        dir_frame.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW, padx=2, pady=2)

        bar1 = ttk.Frame(dir_frame)
        bar1.pack(fill=tk.X, padx=2, pady=(2, 0))
        # 下载后是否执行系统复位（reset），编辑框为复位 rdelay 参数（秒，默认 2）；
        # 清空则用芯片规格默认值（9032: 2 / 9062: 2）
        self.do_reset = tk.BooleanVar(value=True)
        self.rdelay = tk.StringVar(value="2")
        self.chk_reset = ttk.Checkbutton(
            bar1, text="reset", variable=self.do_reset,
            command=self._apply_reset_rdelay_state)
        self.chk_reset.pack(side=tk.LEFT, padx=(0, 2))
        self.entry_rdelay = ttk.Entry(bar1, textvariable=self.rdelay, width=4)
        self.entry_rdelay.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(bar1, text="路径").pack(side=tk.LEFT, padx=(0, 2))
        self.bin_dir = tk.StringVar(value="./bin")
        self.entry_bin_dir = ttk.Entry(
            bar1, textvariable=self.bin_dir, font=get_ui_fonts().ui_small)
        self.entry_bin_dir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
        self.btn_browse_dir = ttk.Button(bar1, text="…", command=self._browse_dir, width=3)
        self.btn_browse_dir.pack(side=tk.LEFT)

        bar2 = ttk.Frame(dir_frame)
        bar2.pack(fill=tk.X, padx=2, pady=(2, 0))
        bar2.columnconfigure(0, weight=0)
        for c in range(1, 4):
            bar2.columnconfigure(c, weight=0, uniform="dl_btn")

        ttk.Label(bar2, text="芯片").grid(row=0, column=0, sticky=tk.W)
        self.chip_type = tk.StringVar(value=self._chip_types[0])
        self.cmb_chip = ttk.Combobox(bar2, textvariable=self.chip_type,
                     values=self._chip_types, width=8, state="readonly")
        self.cmb_chip.grid(row=0, column=1, padx=(2, 0), sticky=tk.EW)
        self.btn_refresh = ttk.Button(bar2, text="刷新", command=self._refresh_all)
        self.btn_refresh.grid(row=0, column=2, padx=2, sticky=tk.EW)
        self.btn_dl = ttk.Button(bar2, text="开始下载", command=self._do_download)
        self.btn_dl.grid(row=0, column=3, padx=2, sticky=tk.EW)
        self.progress = ttk.Progressbar(bar2, mode="indeterminate", orient=tk.HORIZONTAL)
        self.progress.grid(row=0, column=4, padx=2, sticky=tk.EW, ipady=2)

        bar2.columnconfigure(4, weight=1)

        ttk.Separator(dir_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=2, pady=(2, 0))

        # 上 = Bin 文件（5 行），下 = ID Log（3 行 mcu_*）；中间留小间隙
        bins_stack = ttk.Frame(dir_frame)
        bins_stack.pack(fill=tk.X, padx=2, pady=(2, 0))

        self._bins_top = ttk.LabelFrame(bins_stack, text="Bin 文件", padding=2)
        self._bins_top.pack(fill=tk.X, pady=(0, 4))
        self._bins_bot = ttk.LabelFrame(bins_stack, text="ID Log (mcu_* 核)", padding=2)
        self._bins_bot.pack(fill=tk.X, pady=(4, 0))

        # 有 id_log 的 key（programmer/bootloader 没有，chip_bin_files 中为 None）
        self._ID_LOG_KEYS = tuple(k for k in self.BINARY_KEYS if k.startswith("mcu_"))

        # 全表 grid：复选框列 + 固定宽 key 列 + 文件名列，各行文件名左缘对齐。
        # 文件名列 weight=1，不按全文要宽度，否则 150% 下会把右侧整栏撑破。
        lead = scale_px(self, self._LEAD_COL_MINSIZE)
        self._bins_top.columnconfigure(0, minsize=lead)
        self._bins_top.columnconfigure(1, minsize=0)
        self._bins_top.columnconfigure(2, weight=1)
        self._bins_bot.columnconfigure(0, minsize=lead)
        self._bins_bot.columnconfigure(1, minsize=0)
        self._bins_bot.columnconfigure(2, weight=1)

        file_font = get_ui_fonts().mono_small

        for i, key in enumerate(self.BINARY_KEYS):
            self._check_vars[key] = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                self._bins_top, variable=self._check_vars[key],
                command=lambda k=key: self._toggle_check(k),
            ).grid(row=i, column=0, padx=(2, 0), sticky=tk.W)
            ttk.Label(
                self._bins_top, text=self.display_key(key),
                width=self._KEY_COL_WIDTH, anchor=tk.W, foreground="#444",
            ).grid(row=i, column=1, padx=(0, 4), sticky=tk.W)
            self._file_labels[key] = ttk.Label(
                self._bins_top, text="(未配置)", foreground="#888",
                anchor=tk.W, font=file_font, width=8,
            )
            self._file_labels[key].grid(row=i, column=2, sticky=tk.EW, padx=2)
            self._status_labels[key] = ttk.Label(self._bins_top, text="", width=6)
            self._status_labels[key].grid(row=i, column=3, padx=2)
            self._file_buttons[key] = ttk.Button(
                self._bins_top, text="…", width=3,
                command=lambda k=key: self._browse_bin(k),
            )
            self._file_buttons[key].grid(row=i, column=4, padx=2)
            self._reset_buttons[key] = ttk.Button(
                self._bins_top, text="自动", width=4,
                command=lambda k=key: self._reset_bin_manual(k),
                state=tk.DISABLED,
            )
            self._reset_buttons[key].grid(row=i, column=5, padx=(0, 2))

            if key not in self._ID_LOG_KEYS:
                self._id_log_labels[key] = None
                self._id_log_status[key] = None
                self._id_log_buttons[key] = None

        for i, key in enumerate(self._ID_LOG_KEYS):
            ttk.Label(
                self._bins_bot, text=self.display_key(key),
                width=self._KEY_COL_WIDTH, anchor=tk.W, foreground="#888",
            ).grid(row=i, column=1, padx=(0, 4), sticky=tk.W)
            self._id_log_labels[key] = ttk.Label(
                self._bins_bot, text="(无)", foreground="#888",
                anchor=tk.W, font=file_font, width=8,
            )
            self._id_log_labels[key].grid(row=i, column=2, sticky=tk.EW, padx=2)
            self._id_log_status[key] = ttk.Label(self._bins_bot, text="", width=6)
            self._id_log_status[key].grid(row=i, column=3, padx=2)
            self._id_log_buttons[key] = ttk.Button(
                self._bins_bot, text="…", width=3,
                command=lambda k=key: self._browse_id_log(k),
            )
            self._id_log_buttons[key].grid(row=i, column=4, padx=2)
            self._id_log_reset_buttons[key] = ttk.Button(
                self._bins_bot, text="自动", width=4,
                command=lambda k=key: self._reset_id_log_manual(k),
                state=tk.DISABLED,
            )
            self._id_log_reset_buttons[key].grid(row=i, column=5, padx=(0, 2))

        # 文件名截断绑在容器上：标签自身宽度会随截断结果变化，绑标签会来回抖
        for frame in (self._bins_top, self._bins_bot):
            frame.bind("<Configure>", lambda e: self._schedule_fit())

        self._build_status_panel(dir_frame)

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._refresh_enabled = False
        self.chip_type.trace_add("write", lambda *_: self._on_chip_changed())
        self.bin_dir.trace_add("write", lambda *_: self.refresh_bins())

    def enable_refresh(self):
        self._refresh_enabled = True

    def _rescan_files(self) -> dict:
        """重扫 bin 目录，返回 {key: (旧文件名, 新文件名)}。

        只统计自动识别的行：手动选的文件不走目录自动匹配。
        """
        before = dict(self._resolved)
        self.refresh_bins()
        changed = {}
        for key in self.BINARY_KEYS:
            if self._manual_selection.get(key):
                continue
            old, new = before.get(key), self._resolved.get(key)
            if old != new:
                changed[key] = (old, new)
        return changed

    def _warn_stale_manual_selection(self):
        """手动选的文件比目录里同关键词更新的文件还旧 → 提醒可能烧错版本。"""
        bin_dir = self.bin_dir.get()
        if not bin_dir or not os.path.isdir(bin_dir):
            return
        for key in self.BINARY_KEYS:
            if not self._manual_selection.get(key):
                continue
            path = self._paths.get(key)
            if not path:
                continue
            info = self._chip_files.get(key)
            keyword = info.get("keyword", "") if info else ""
            if not keyword:
                continue
            newest = self._find_bin_file(bin_dir, keyword, self.chip_type.get())
            if not newest:
                continue
            newest_path = os.path.join(bin_dir, newest)
            if os.path.abspath(newest_path) == os.path.abspath(path):
                continue
            mine = self._file_mtime(path)
            theirs = self._file_mtime(newest_path)
            if mine is None or theirs is None or theirs <= mine:
                continue
            self.append_status(
                f"⚠ {self.display_key(key)} 手动选的文件比目录里更新的 "
                f"{newest} 还旧，确认是否烧错版本",
                "WARN",
            )

    def _apply_reset_rdelay_state(self):
        """rdelay 编辑框跟随 reset 选择框：勾选可编辑，取消置灰。"""
        self.entry_rdelay.configure(
            state=tk.NORMAL if self.do_reset.get() else tk.DISABLED)

    def reset_rdelay_value(self, default: int = 0) -> int:
        """解析 rdelay 编辑框；为空或非法时返回芯片规格默认值。"""
        raw = self.rdelay.get().strip()
        if raw:
            try:
                return int(raw, 0)
            except ValueError:
                pass
        return default

    def collect_manual_paths_state(self) -> tuple:
        """返回 (bin 绝对路径 dict, id_log 绝对路径 dict)，供写入 config。"""
        bin_paths = {}
        for key in self.BINARY_KEYS:
            if self._manual_selection.get(key) and self._paths.get(key):
                bin_paths[key] = self._paths[key]
        id_log_paths = {}
        for key in self._ID_LOG_KEYS:
            if self._id_log_manual.get(key) and self._id_log_paths.get(key):
                id_log_paths[key] = self._id_log_paths[key]
        return bin_paths, id_log_paths

    def set_bin_selected(self, selected: Optional[Dict[str, bool]] = None):
        """写入 ini 中的勾选意图。真正勾上还要等文件存在。"""
        selected = selected or {}
        for key in self.BINARY_KEYS:
            self._bin_selected[key] = bool(selected.get(key))

    def collect_bin_selected(self) -> Dict[str, bool]:
        return {key: bool(self._bin_selected.get(key)) for key in self.BINARY_KEYS}

    def _notify_bin_selection_changed(self):
        if self._on_bin_selection_changed is not None:
            self._on_bin_selection_changed()

    def apply_manual_paths_state(
        self,
        bin_paths: Optional[Dict[str, str]] = None,
        id_log_paths: Optional[Dict[str, str]] = None,
    ):
        """从 config 恢复手动选择的 bin / id_log 绝对路径。"""
        for key in self.BINARY_KEYS:
            self._manual_selection[key] = False
            self._paths.pop(key, None)
        for key in self._ID_LOG_KEYS:
            self._id_log_manual[key] = False
            self._id_log_paths.pop(key, None)

        for key, path in (bin_paths or {}).items():
            if key not in self.BINARY_KEYS or not path:
                continue
            self._paths[key] = path
            self._manual_selection[key] = True
            self._resolved[key] = os.path.basename(path)

        for key, path in (id_log_paths or {}).items():
            if key not in self._ID_LOG_KEYS or not path:
                continue
            self._id_log_paths[key] = path
            self._id_log_manual[key] = True

        self._chip_files = self._get_chip_files(self.chip_type.get())
        self._drop_manual_paths_for_other_chip()

    def _build_status_panel(self, parent: ttk.Frame):
        """占满下载区剩余高度：系统 / 下载 / 配置等状态信息（非串口数据）。"""
        box = ttk.LabelFrame(parent, text="状态", padding=4)
        box.pack(fill=tk.BOTH, expand=True, padx=2, pady=(8, 2))
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)

        text_frame = ttk.Frame(box)
        text_frame.grid(row=0, column=0, sticky=tk.NSEW)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        mono = get_ui_fonts().mono_small
        self._status_text = tk.Text(
            text_frame,
            width=20, height=4,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=mono,
            bg="#fafafa",
            fg="#333333",
            borderwidth=0,
            highlightthickness=0,
        )
        self._status_text.grid(row=0, column=0, sticky=tk.NSEW)
        sb = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self._status_text.yview)
        sb.grid(row=0, column=1, sticky=tk.NS)
        self._status_text.configure(yscrollcommand=sb.set)

        for level, color in (
            ("INFO", "#333333"),
            ("WARN", "#b58900"),
            ("ERROR", "#dc322f"),
            ("DEBUG", "#268bd2"),
        ):
            self._status_text.tag_configure(f"lv_{level}", foreground=color)

        self.append_status("就绪", "INFO")

    def clear_status(self):
        if not hasattr(self, "_status_text"):
            return
        self._status_text.configure(state=tk.NORMAL)
        self._status_text.delete("1.0", tk.END)
        self._status_text.configure(state=tk.DISABLED)

    def append_status(self, msg: str, level: str = "INFO"):
        if not hasattr(self, "_status_text") or not msg:
            return
        level = (level or "INFO").upper()
        if level not in ("INFO", "WARN", "ERROR", "DEBUG"):
            level = "INFO"
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg.rstrip()}\n"
        tag = f"lv_{level}"
        try:
            self._status_text.configure(state=tk.NORMAL)
            self._status_text.insert(tk.END, line, tag)
            line_count = int(self._status_text.index("end-1c").split(".")[0])
            if line_count > _STATUS_MAX_LINES:
                self._status_text.delete("1.0", f"{line_count - _STATUS_MAX_LINES}.0")
            self._status_text.see(tk.END)
            self._status_text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass

    def _on_chip_changed(self):
        self._chip_files = self._get_chip_files(self.chip_type.get())
        self._drop_manual_paths_for_other_chip()
        self.refresh_bins()

    def _drop_manual_paths_for_other_chip(self):
        """切换芯片后，清掉仍指向其它芯片 bin 的手动路径（避免 9062 路径误用于 9032）。"""
        chip = self.chip_type.get()
        other_chips = [c for c in self._chip_types if c != chip]
        for key in self.BINARY_KEYS:
            if not self._manual_selection.get(key):
                continue
            path = self._paths.get(key, "")
            fname = os.path.basename(path)
            info = self._chip_files.get(key)
            keyword = info.get("keyword", "") if info else ""
            drop = False
            if keyword and keyword not in fname:
                drop = True
            for oc in other_chips:
                if oc in fname:
                    drop = True
                    break
            if drop:
                self._clear_manual_selection(key)

    def _refresh_all(self):
        """重新加载配置文件中的芯片配置，再按 ini 勾选并刷新文件匹配。"""
        self._chip_files = self._get_chip_files(self.chip_type.get())
        if self._on_reload_bin_selection is not None:
            self._on_reload_bin_selection()
        self.refresh_bins()
        if self._on_refresh is not None:
            self._on_refresh()

    def set_chip_files(self, chip_files: Dict[str, dict]):
        self._chip_files = chip_files
        self.refresh_bins()

    def _toggle_check(self, key: str):
        if self._check_vars[key].get() and not self._bin_file_exists(key):
            self._check_vars[key].set(False)
            return
        self._bin_selected[key] = bool(self._check_vars[key].get())
        self._notify_bin_selection_changed()
        if key == "programmer" and not self._check_vars[key].get():
            messagebox.showinfo(
                "调试下载",
                "未勾选 prog。\n将跳过 programmer 阶段，进入调试下载。",
                parent=self.winfo_toplevel(),
            )

    def _browse_dir(self):
        d = filedialog.askdirectory(initialdir=dialog_initial_dir(self.bin_dir.get()))
        if d:
            self.bin_dir.set(d)
            self.refresh_bins()

    def _find_bin_file(
        self, bin_dir: str, keyword: str, chip: Optional[str] = None,
    ) -> Optional[str]:
        """在目录内按关键词匹配 bin 文件（区分大小写），优先前缀与当前 chip 匹配。

        同分时取修改时间更新的那个：目录里同时留着新旧两个版本时，
        不能靠 listdir 顺序碰运气（碰上哪个算哪个，就会烧到旧版本）。
        """
        if not keyword or not bin_dir or not os.path.isdir(bin_dir):
            return None
        chip = chip or self.chip_type.get()
        other_chips = [c for c in self._chip_types if c != chip]
        want_idlog = "idlog" in keyword.lower()
        best, best_key = None, (-999, -1.0)
        for name in os.listdir(bin_dir):
            if not name.lower().endswith(".bin"):
                continue
            # id log 文件只在关键词本身指向它时才参与匹配
            if ("idlog" in name.lower()) != want_idlog:
                continue
            if keyword not in name:
                continue
            score = 2 if name.startswith(keyword) else 1
            if chip and chip in name:
                score += 4
            for oc in other_chips:
                if oc in name:
                    score -= 4
            mtime = self._file_mtime(os.path.join(bin_dir, name))
            # (匹配分, 修改时间)：分数高的优先，同分取更新的
            key = (score, mtime if mtime is not None else -1.0)
            if key > best_key:
                best, best_key = name, key
        return best

    @staticmethod
    def _file_mtime(path: str) -> Optional[float]:
        """文件修改时间；取不到时返回 None。"""
        try:
            return os.path.getmtime(path)
        except OSError:
            return None

    def _file_dialog_start(self, saved_path: str, fallback_name: str = "") -> tuple:
        """再次选择时打开上次文件所在目录；还没有手动选择时用下载路径。"""
        saved_path = (saved_path or "").strip()
        if saved_path:
            folder = os.path.dirname(os.path.abspath(saved_path))
            if os.path.isdir(folder):
                name = os.path.basename(saved_path) if os.path.isfile(saved_path) else ""
                return folder, name
        folder = dialog_initial_dir(self.bin_dir.get())
        name = os.path.basename(fallback_name) if fallback_name else ""
        if not name or name.startswith("("):
            name = ""
        elif not os.path.isfile(os.path.join(folder, name)):
            name = ""
        return folder, name

    def _browse_bin(self, key: str):
        """手动选择该 bin 关联的文件，覆盖自动识别。"""
        initial_dir, initial_file = self._file_dialog_start(
            self._paths.get(key) or "", self._resolved.get(key) or "")
        path = filedialog.askopenfilename(
            title=f"选择 {self.display_key(key)} bin 文件",
            initialdir=initial_dir,
            initialfile=initial_file,
            filetypes=[("Bin files", "*.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        self._paths[key] = path
        self._manual_selection[key] = True
        self._resolved[key] = os.path.basename(path)
        self._bin_selected[key] = True
        self._refresh_bin_row(key)
        self._sync_manual_reset_buttons()
        self._notify_bin_selection_changed()

    def _browse_id_log(self, key: str):
        """手动选择该 key 的 id_log 文件，覆盖自动识别。"""
        cur = ""
        lbl = self._id_log_labels.get(key)
        if lbl is not None:
            cur = lbl.cget("text")
        initial_dir, initial_file = self._file_dialog_start(
            self._id_log_paths.get(key) or "", cur)
        path = filedialog.askopenfilename(
            title=f"选择 {self.display_key(key)} ID Log 文件",
            initialdir=initial_dir,
            initialfile=initial_file,
            filetypes=[("Bin files", "*.bin"), ("All files", "*.*")],
        )
        if not path:
            return
        self._id_log_paths[key] = path
        self._id_log_manual[key] = True
        self._refresh_id_log_row(key, os.path.basename(path), self.bin_dir.get())
        self._sync_manual_reset_buttons()

    def _clear_manual_selection(self, key: str):
        """清除手动选择，重新走自动识别。"""
        self._manual_selection[key] = False
        self._paths.pop(key, None)

    def _clear_id_log_manual(self, key: str):
        self._id_log_manual[key] = False
        self._id_log_paths.pop(key, None)

    def _sync_manual_reset_buttons(self):
        """按当前手动选择状态更新「自动」按钮可用性。"""
        for key, btn in self._reset_buttons.items():
            btn.configure(
                state=tk.NORMAL if self._manual_selection.get(key) else tk.DISABLED)
        for key, btn in self._id_log_reset_buttons.items():
            btn.configure(
                state=tk.NORMAL if self._id_log_manual.get(key) else tk.DISABLED)

    def _reset_bin_manual(self, key: str):
        """取消该行 bin 的手动路径，恢复目录自动匹配。"""
        if not self._manual_selection.get(key):
            return
        self._clear_manual_selection(key)
        bin_dir = self.bin_dir.get()
        info = self._chip_files.get(key)
        keyword = info.get("keyword", "") if info else ""
        self._resolved[key] = self._find_bin_file(bin_dir, keyword, self.chip_type.get())
        self._refresh_bin_row(key)
        id_log_name = info.get("id_log") if info else None
        self._refresh_id_log(key, bin_dir, id_log_name)
        self._sync_manual_reset_buttons()

    def _reset_id_log_manual(self, key: str):
        """取消该行 id_log 的手动路径，恢复默认文件名匹配。"""
        if not self._id_log_manual.get(key):
            return
        self._clear_id_log_manual(key)
        bin_dir = self.bin_dir.get()
        info = self._chip_files.get(key)
        id_log_name = info.get("id_log") if info else None
        self._refresh_id_log(key, bin_dir, id_log_name)
        self._sync_manual_reset_buttons()

    def refresh_bins(self):
        if not self._refresh_enabled:
            return
        bin_dir = self.bin_dir.get()
        for key in self.BINARY_KEYS:
            info = self._chip_files.get(key)
            keyword = info.get("keyword", "") if info else ""
            if not keyword:
                self._resolved.pop(key, None)
                self._paths.pop(key, None)
                self._manual_selection[key] = False
                self._id_log_paths.pop(key, None)
                self._id_log_manual[key] = False
                # 该芯片无此 bin（如 9032 无 mcu_c）：取消勾选，
                # 避免残留上一个芯片（9062）的勾选状态
                self._check_vars[key].set(False)
                self._refresh_bin_row(key, keyword_empty=True)
                self._refresh_id_log(key, None)
                continue
            # 手动选择时跳过自动识别，只检查文件是否还存在
            if not self._manual_selection.get(key):
                fname = self._find_bin_file(bin_dir, keyword, self.chip_type.get())
                self._resolved[key] = fname
            self._refresh_bin_row(key)
            # id log 状态更新（mcu_* 才显示）
            id_log_name = info.get("id_log") if info else None
            self._refresh_id_log(key, bin_dir, id_log_name)
        self._sync_manual_reset_buttons()

    def _refresh_bin_row(self, key: str, keyword_empty: bool = False):
        """刷新单个 bin 行的显示。"""
        info = self._chip_files.get(key)
        if keyword_empty or not self._resolved.get(key):
            # 清掉截断用的原文，否则之后一 resize / 缩放又会被旧文件名顶回来
            self._full_text.pop(key, None)
            self._file_labels[key].configure(text="(未配置)", foreground=self._COLOR_MUTED)
            self._status_labels[key].configure(text="", foreground=self._COLOR_MUTED)
            self._check_vars[key].set(False)
            return
        fname = self._resolved[key]
        is_manual = bool(self._manual_selection.get(key) and self._paths.get(key))
        # 文件存在性：手动选择用绝对路径，否则 bin_dir/fname
        full = self._full_path(key)
        exists = os.path.isfile(full)
        addr = info.get("addr", 0) if info else 0
        fg = self._COLOR_MANUAL if is_manual else self._COLOR_AUTO
        if exists:
            self._full_text[key] = f"{fname}  →  0x{addr:08x}"
            self._file_labels[key].configure(
                text=self._full_text[key], foreground=fg)
            self._schedule_fit()
            # 状态列宽度有限（width=6），只放"自动/手动"；文件大小在下载日志里
            # （file=0x… / erase=0x…）能看到
            status = "✓ 手动" if is_manual else "✓ 自动"
            self._status_labels[key].configure(text=status, foreground=self._COLOR_OK)
        else:
            self._full_text[key] = f"{fname}  (不存在)"
            self._file_labels[key].configure(
                text=self._full_text[key], foreground=self._COLOR_ERR)
            self._status_labels[key].configure(text="✗ 缺失", foreground=self._COLOR_ERR)
        # ini 里记的是勾选意图；文件不存在时不能勾上
        self._check_vars[key].set(bool(self._bin_selected.get(key)) and exists)

    def refresh_fonts(self):
        """界面缩放后重设字体（Tk 已创建的字体不会自动重绘）。"""
        fonts = get_ui_fonts()
        if self._fit_font is not None:
            # 复用同一个命名 Tk 字体：每档缩放都新建一个会越攒越多
            self._fit_font.configure(family=fonts.mono, size=fonts.size_mono_sm)
        for key in self.BINARY_KEYS:
            for lbl in (self._file_labels.get(key), self._id_log_labels.get(key)):
                if lbl is None:
                    continue
                try:
                    lbl.configure(font=fonts.mono_small)
                except tk.TclError:
                    pass
        try:
            self._status_text.configure(font=fonts.mono_small)
        except tk.TclError:
            pass
        self._schedule_fit()

    def _schedule_fit(self):
        """合并多次尺寸变化，等几何稳定后再统一截断文件名。"""
        if self._fit_job is not None:
            return
        try:
            self._fit_job = self.after_idle(self._fit_all_labels)
        except tk.TclError:
            self._fit_job = None

    def _fit_all_labels(self):
        """按可用宽度给文件名加省略号（VS Code 的 text-overflow）。"""
        self._fit_job = None
        if self._fit_font is None:
            self._fit_font = tkfont.Font(
                root=self, font=get_ui_fonts().mono_small)
        for labels, texts in (
            (self._file_labels, self._full_text),
            (self._id_log_labels, self._full_idlog_text),
        ):
            for key, label in labels.items():
                full = texts.get(key)
                if label is None or not full:
                    continue
                avail = label.winfo_width()
                if avail <= 1:
                    continue
                try:
                    label.configure(text=ellipsize(full, avail, self._fit_font))
                except tk.TclError:
                    pass

    def _full_path(self, key: str) -> str:
        """该 key 当前生效的 bin 路径（手动选择的绝对路径优先）。"""
        if self._manual_selection.get(key) and self._paths.get(key):
            return self._paths[key]
        fname = self._resolved.get(key) or ""
        bin_dir = self.bin_dir.get()
        return os.path.join(bin_dir, fname) if bin_dir else fname

    def _bin_file_exists(self, key: str) -> bool:
        if not self._resolved.get(key):
            return False
        return os.path.isfile(self._full_path(key))

    def _refresh_id_log(self, key: str, bin_dir: Optional[str],
                         id_log_name: Optional[str] = None):
        """更新右面 id log 文件的显示。仅 mcu_* key 有意义。"""
        if self._id_log_labels.get(key) is None:
            return
        if not id_log_name:
            self._full_idlog_text.pop(key, None)
            self._id_log_labels[key].configure(text="(无)", foreground="#888")
            self._id_log_status[key].configure(text="", foreground="#888")
            return
        # 手动选择时刷新绝对路径
        self._refresh_id_log_row(key, id_log_name, bin_dir)

    def _refresh_id_log_row(self, key: str, id_log_name: str, bin_dir: Optional[str]):
        """id log 单行刷新（被 _refresh_id_log 和手动选择共用）。"""
        if self._id_log_labels.get(key) is None:
            return
        is_manual = bool(self._id_log_manual.get(key) and self._id_log_paths.get(key))
        display_name = (
            os.path.basename(self._id_log_paths[key]) if is_manual else id_log_name)
        fg = self._COLOR_MANUAL if is_manual else self._COLOR_AUTO
        self._full_idlog_text[key] = display_name
        self._id_log_labels[key].configure(text=display_name, foreground=fg)
        self._schedule_fit()
        # 文件存在性：手动选择用绝对路径，否则 bin_dir/id_log_name
        if is_manual:
            full = self._id_log_paths[key]
        else:
            full = os.path.join(bin_dir, id_log_name) if bin_dir else id_log_name
        if os.path.isfile(full):
            status = "✓ 手动" if is_manual else "✓ 自动"
            self._id_log_status[key].configure(text=status, foreground=self._COLOR_OK)
        elif bin_dir and os.path.isdir(bin_dir):
            self._id_log_status[key].configure(text="✗ 缺失", foreground=self._COLOR_ERR)
        else:
            self._id_log_status[key].configure(text="", foreground=self._COLOR_MUTED)

    def collect(self) -> dict:
        chip = self.chip_type.get()
        bin_dir = self.bin_dir.get()
        selected = []
        for key in self.BINARY_KEYS:
            if not self._check_vars[key].get():
                continue
            info = self._chip_files.get(key)
            if not info:
                continue
            fname = self._resolved.get(key)
            if not fname:
                continue
            # bin：手动选择用绝对路径，否则 bin_dir/fname
            full = self._full_path(key)
            # id_log：手动选择用绝对路径，否则保持原文件名（app.py 端拼 bin_dir）
            id_log_name = info.get("id_log")
            id_log_path = None
            if self._id_log_manual.get(key) and self._id_log_paths.get(key):
                id_log_path = self._id_log_paths[key]
            selected.append({
                "key": key,
                "label": info.get("label", key),
                "filename": fname,
                "path": full,
                "addr": info["addr"],
                "id_log": id_log_name,
                "id_log_path": id_log_path,
                "mode": info.get("mode", "flashload"),
            })
        return {
            "chip_type": chip,
            "bin_dir": bin_dir,
            "selected_bins": selected,
        }

    def _do_download(self):
        if self._on_download is None:
            return
        # 下载前再扫一次目录：编译可能发生在界面上次扫描之后，
        # 不重扫就会照着旧的文件名烧旧版本
        changed = self._rescan_files()
        for key, (old, new) in changed.items():
            self.append_status(
                f"下载前重扫：{self.display_key(key)} 由 {old or '(无)'} "
                f"改为 {new or '(无)'}",
                "WARN",
            )
        self._warn_stale_manual_selection()
        self._on_download(self.collect())

    def set_running(self, running: bool, serial_opened: bool = False):
        """设置下载/擦除/回读任务的运行状态。

        running=True:
          - 开始按钮变"取消"，按下即触发 _on_cancel
          - 路径/芯片/刷新/开始下载 以及每行的"…" 按钮置灰，避免运行中改配置
        running=False:
          - 全部恢复可操作。芯片/路径/刷新只影响下次下载选哪些 bin，
            与已打开的 log 串口无冲突，无需按 serial_opened 保持灰化
            （serial_opened 参数仅为兼容调用方签名而保留）。
        """
        if running:
            self.btn_dl.configure(
                state=tk.NORMAL, text="取消",
                command=self._on_cancel or (lambda: None),
            )
            # 路径 / 芯片 / 浏览 / 刷新 / 开始下载 全部置灰
            self.entry_bin_dir.configure(state=tk.DISABLED)
            self.btn_browse_dir.configure(state=tk.DISABLED)
            self.chk_reset.configure(state=tk.DISABLED)
            self.entry_rdelay.configure(state=tk.DISABLED)
            self.cmb_chip.configure(state="disabled")
            self.btn_refresh.configure(state=tk.DISABLED)
            # 注：Checkbutton 没有 disabled state；通过禁用按钮来防护
            # 每行 bin / id_log 的 "…" 手动选择按钮
            for btn in self._file_buttons.values():
                if btn is not None:
                    btn.configure(state=tk.DISABLED)
            for btn in self._id_log_buttons.values():
                if btn is not None:
                    btn.configure(state=tk.DISABLED)
            for btn in self._reset_buttons.values():
                btn.configure(state=tk.DISABLED)
            for btn in self._id_log_reset_buttons.values():
                btn.configure(state=tk.DISABLED)
            self.progress.start(10)
        else:
            self.btn_dl.configure(
                state=tk.NORMAL, text="开始下载",
                command=self._do_download,
            )
            self.entry_bin_dir.configure(state=tk.NORMAL)
            self.btn_browse_dir.configure(state=tk.NORMAL)
            self.chk_reset.configure(state=tk.NORMAL)
            self._apply_reset_rdelay_state()
            self.cmb_chip.configure(state="readonly")
            self.btn_refresh.configure(state=tk.NORMAL)
            for btn in self._file_buttons.values():
                if btn is not None:
                    btn.configure(state=tk.NORMAL)
            for btn in self._id_log_buttons.values():
                if btn is not None:
                    btn.configure(state=tk.NORMAL)
            self._sync_manual_reset_buttons()
            self.progress.stop()
