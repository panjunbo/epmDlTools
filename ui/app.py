import argparse
import os
import platform
import subprocess
import threading
import time
import tkinter as tk
from collections import deque
from tkinter import ttk, messagebox, filedialog
from typing import TYPE_CHECKING, Deque, List, Optional, Tuple

from utils.app_paths import app_icon_path, get_logs_dir, get_readback_dir
from utils.config_manager import ConfigManager
from utils.version import __build_time__, __version__
from utils.epm_id_log_parser import IdLogParser
from utils.id_log_format import format_id_log_head, format_id_log_line
from utils.idlog_bin_parser import (
    merge_idlog_maps,
    parse_idlog_bin,
    resolve_idlog_base_addrs,
    resolve_idlog_bin_paths,
)
from utils.logger import Logger

from .download_panel import DownloadPanel
from .erase_read_panel import EraseReadPanel
from .serial_assistant import SerialAssistant
from .serial_panel import SerialPanel, SerialParamDialog
from .theme import (
    apply_ui_zoom, init_ui_theme, init_ui_scaling,
    refresh_ui_fonts, scale_px,
)

if TYPE_CHECKING:
    from commands.base import SerialManager

# 串口原始 log 缓存上限（供 idlog 重解析）
RECV_LOG_RAW_MAX = 50 * 1024 * 1024
# 超过此大小的 log 加载/重放时分批刷新 UI，避免卡顿
LARGE_LOG_BYTES = 512 * 1024
_REPLAY_CHUNK_BATCH = 150
# 实时串口：合并 after 回调，每 tick 最多处理 N 块 raw 数据
_LIVE_LOG_BATCH = 64
_LIVE_LOG_DRAIN_MS = 16

# (原始字节, 主机时间戳)。时间为 None 表示整段来自加载的文件，刷新时不另加时间戳。
LogSeg = Tuple[bytes, Optional[str]]


def replay_host_ts(stored: Optional[str], show_ts: bool) -> Optional[str]:
    """刷新时要用的主机时间戳。

    None：这段没有记录过接收时间（加载的 log），调用方不要再加前缀。
    字符串：实时接收时记下的前缀；取消勾选“时间戳”时返回空串。
    """
    if stored is None:
        return None
    return stored if show_ts else ""


def trim_log_segments(
    segs: List[LogSeg], length: int, max_bytes: int,
) -> Tuple[List[LogSeg], int, bool]:
    """丢掉缓存前部，使总长度不超过 max_bytes。切点对齐到换行，并保留尾部时间戳。"""
    overflow = length - max_bytes
    if overflow <= 0:
        return segs, length, False
    cut = _log_trim_cut(segs, length, overflow)
    new_segs, new_len = _drop_log_prefix(segs, length, cut)
    return new_segs, new_len, True


def _log_trim_cut(segs: List[LogSeg], length: int, overflow: int) -> int:
    if overflow >= length:
        return length
    pos = 0
    for i, (data, _ts) in enumerate(segs):
        end = pos + len(data)
        if end <= overflow:
            pos = end
            continue
        nl = data.find(b"\n", overflow - pos)
        if nl >= 0:
            return pos + nl + 1
        pos = end
        for data2, _ts2 in segs[i + 1:]:
            nl = data2.find(b"\n")
            if nl >= 0:
                return pos + nl + 1
            pos += len(data2)
        return overflow
    return overflow


def _drop_log_prefix(
    segs: List[LogSeg], length: int, cut: int,
) -> Tuple[List[LogSeg], int]:
    if cut <= 0:
        return list(segs), length
    if cut >= length:
        return [], 0
    pos = 0
    for i, (data, ts) in enumerate(segs):
        end = pos + len(data)
        if end <= cut:
            pos = end
            continue
        rel = cut - pos
        out: List[LogSeg] = []
        tail = data[rel:]
        if tail:
            out.append((tail, ts))
        out.extend(segs[i + 1:])
        return out, length - cut
    return [], 0

# 每芯片下载规格 — 对应 download_tools_9062.bat / download_tools_9032.bat 的命令序列
# mode:        programmer | flashload
# sync_exit:   仅 DSP 烧录需要
# baud_phase:  initial=使用 download_baud 起步；target=使用 config [flashload].target_baud
# 注：addr 不在此处；统一从 [chip_xxx] 段读 ini 配置（用户可覆盖）
# 注：擦除范围由 bin 文件大小决定（向上对齐 4K），不再有固定窗口
# 注：target_baud 在 bootloader 阶段用于设备端切换波特率
DOWNLOAD_SPEC: dict = {
    "9032": {
        # 9032 bat 在 programmer 之后立即切到 target_baud：
        # bootloader 阶段（首个 flashload）由 BAUD_CHANGE 切换，之后阶段直接用目标波特率
        "programmer":  {"mode": "programmer", "baud_phase": "initial"},
        "bootloader":  {"mode": "flashload",  "baud_phase": "initial"},
        "mcu_a":       {"mode": "flashload",  "baud_phase": "target"},
        "mcu_b":       {"mode": "flashload",  "baud_phase": "target"},
        # 9032 无 C 核：ini 默认不带 mcu_c，但保留规格以支持用户手动选择文件
        "mcu_c":       {"mode": "flashload",  "baud_phase": "target", "sync_exit": True},
        "_reset":      {"rdelay": 2},
    },
    "9062": {
        # 9062 bat 在 bootloader 之后才切到 target_baud
        "programmer":  {"mode": "programmer", "baud_phase": "initial"},
        "bootloader":  {"mode": "flashload",  "baud_phase": "initial"},
        "mcu_a":       {"mode": "flashload",  "baud_phase": "target"},
        "mcu_b":       {"mode": "flashload",  "baud_phase": "target"},
        "mcu_c":       {"mode": "flashload",  "baud_phase": "target", "sync_exit": True},
        "_reset":      {"rdelay": 2},
    },
}

# 擦除的最小单位：4K（flash 支持 4K/32K 块擦除）
ERASE_ALIGN = 0x1000


def align_up_4k(size: int) -> int:
    """向上对齐到 4K。0 或负数返回 0。"""
    if size <= 0:
        return 0
    return (size + ERASE_ALIGN - 1) & ~(ERASE_ALIGN - 1)


def plan_flash_regions(chip: str, selected_bins: List[dict]) -> List[dict]:
    """各 flashload 阶段实际会擦除/写入的区间 [addr, addr+align4k(size))。

    擦除范围由 bin 文件大小决定（向上对齐 4K）：写多大就擦多大。
    programmer 烧到 SRAM，不占 flash 区间。
    """
    spec = DOWNLOAD_SPEC.get(chip, {})
    regions = []
    for b in selected_bins:
        key = b.get("key")
        bin_spec = spec.get(key, {})
        mode = b.get("mode") or bin_spec.get("mode", "flashload")
        if mode == "programmer":
            continue
        path = b.get("path", "")
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        addr = int(b.get("addr", 0) or 0)
        erase_size = align_up_4k(size)
        regions.append({
            "key": key,
            "label": b.get("label", key),
            "path": path,
            "addr": addr,
            "size": size,
            "erase_size": erase_size,
            "end": addr + erase_size,
        })
    return regions


def find_region_overlaps(regions: List[dict]) -> List[tuple]:
    """两两区间重叠的组合 [(a, b, 重叠起点, 重叠终点), ...]。"""
    out = []
    for i in range(len(regions)):
        for j in range(i + 1, len(regions)):
            a, b = regions[i], regions[j]
            lo = max(a["addr"], b["addr"])
            hi = min(a["end"], b["end"])
            if hi > lo:
                out.append((a, b, lo, hi))
    return out


def _is_no_file_association(exc: BaseException) -> bool:
    """os.startfile 因没有可用的打开程序而失败。"""
    winerror = getattr(exc, "winerror", None)
    if winerror is None:
        return False
    return (int(winerror) & 0xFFFFFFFF) in {
        31,          # SE_ERR_NOASSOC
        1155,        # ERROR_NO_ASSOCIATION
        0x800401F5,  # CO_E_APPNOTFOUND
    }


def _open_with_dialog(path: str, owner: int = 0) -> None:
    """弹出系统「打开方式」。用户取消时直接返回。"""
    import ctypes
    from ctypes import wintypes

    class OPENASINFO(ctypes.Structure):
        _fields_ = [
            ("pcszFile", wintypes.LPCWSTR),
            ("pcszClass", wintypes.LPCWSTR),
            ("oaifInFlags", wintypes.DWORD),
        ]

    # 与资源管理器双击无关联文件时相同：可选「始终使用」，并打开文件
    OAIF_ALLOW_REGISTRATION = 0x1
    OAIF_REGISTER_EXT = 0x2
    OAIF_EXEC = 0x4
    COINIT_APARTMENTTHREADED = 0x2
    ERROR_CANCELLED = 0x800704C7

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.HRESULT
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    shell32.SHOpenWithDialog.argtypes = [wintypes.HWND, ctypes.POINTER(OPENASINFO)]
    shell32.SHOpenWithDialog.restype = ctypes.HRESULT

    info = OPENASINFO(
        os.path.abspath(path),
        None,
        OAIF_ALLOW_REGISTRATION | OAIF_REGISTER_EXT | OAIF_EXEC,
    )
    hr_init = ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
    try:
        hr = shell32.SHOpenWithDialog(wintypes.HWND(owner), ctypes.byref(info))
    finally:
        if hr_init == 0:
            ole32.CoUninitialize()

    if hr in (0, 1) or (hr & 0xFFFFFFFF) == ERROR_CANCELLED:
        return
    raise OSError(None, f"打开方式失败 (0x{hr & 0xFFFFFFFF:08X})", path)


def initial_h_sash(total: int, right_need: int, left_need: int) -> int:
    """左日志 / 右配置 的初始分隔位置（像素）。

    默认左侧 60%。150% 缩放时右侧控件比 40% 更宽，就把分隔条左移；
    两边都放不下时优先保住左侧工具栏，避免按钮被裁掉。
    """
    if total <= 200:
        return max(0, total // 2)
    right_need = max(0, right_need)
    left_need = max(0, left_need)
    pos = int(total * 0.6)
    if left_need + right_need <= total:
        if total - pos < right_need:
            pos = total - right_need
        if pos < left_need:
            pos = left_need
        return pos
    return max(0, min(left_need, total - 80))


def h_sash_for_ratio(total: int, ratio: float) -> int:
    """按用户拖出来的比例算分隔条位置。

    不设"合法区间"：用户拖到哪就是哪，缩放/重启都按同一个比例摆回去。
    之前在这里按右栏自身需要的宽度夹紧，会把用户拖好的比例顶掉 —— 下次
    缩放分隔条就弹回默认位置（存的比例和屏幕上的位置对不上，来回跳）。

    左栏窄了工具栏会折行、右栏窄了控件会被裁，都是用户自己选的。
    """
    if total <= 0:
        return 0
    return max(0, min(int(total * ratio), total))


class EpmDlApp(tk.Tk):
    # 窗口尺寸下限（会被右栏内容的实际需要抬高，见 _apply_scaled_minsize）
    MIN_WIDTH = 640
    MIN_HEIGHT = 480

    def __init__(self, cfg: dict, cm: ConfigManager, logger: Logger):
        super().__init__()
        # 界面缩放跟随系统显示缩放（VS Code 模型）：物理大小跟系统走，
        # 布局比例不变（像素间距与字号用同一系数放大）。
        # layout_scale 可再乘一档（1.25 = 比系统再大 25%）。
        try:
            layout_scale = float(cm.get("ui", "layout_scale", "1.0") or 1.0)
        except (TypeError, ValueError):
            layout_scale = 1.0
        self._zoom = layout_scale
        init_ui_scaling(self, layout_scale)
        self._startup_layout_done = False
        # 用户拖好的左右分隔比例：窗口最大化 / 换显示器重排后按它摆回去
        self._hpaned_ratio: Optional[float] = None
        # 样式的字号与命名字体（TkDefaultFont 等）都在这里按 layout_scale 设好，
        # 子控件创建时拿到的就是缩放后的字体
        init_ui_theme(self)
        self.title(f"EPM Download Tool  v{__version__}")
        icon = app_icon_path()
        if icon:
            try:
                self.iconbitmap(default=icon)
            except Exception:
                pass
        self.cm = cm
        self._base_logger = logger

        # 先设置 minsize，启动后默认最大化（不读 config 里保存的尺寸）
        self._apply_scaled_minsize()

        # 顶层 hpaned：左 = 接收区+ID Log，右 = 配置区
        self.hpaned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        left_frame = ttk.Frame(self.hpaned)    # 接收区 + ID Log
        right_frame = ttk.Frame(self.hpaned)   # 配置区
        self.hpaned.add(left_frame, weight=3)
        self.hpaned.add(right_frame, weight=2)
        self._left_frame = left_frame
        self._right_frame = right_frame

        # right 内用 grid：上半 serial+erase_read 横排，下半 dl_panel 撑满
        self.serial_panel = SerialPanel(
            right_frame,
            on_open=self._open_serial,
            on_close=self._close_serial,
            on_open_params=self._open_param_dialog,
        )

        self.erase_read_panel = EraseReadPanel(
            right_frame,
            on_erase=self._start_erase,
            on_read=self._start_read,
            on_open_readback_dir=self._open_readback_dir,
        )

        self.dl_panel = DownloadPanel(
            right_frame,
            get_chip_files=self._reload_chip_files,
            chip_types=["9032", "9062"],
            on_download=self._start_download,
            on_cancel=self._cancel_download,
            on_refresh=self._on_download_panel_refresh,
            on_bin_selection_changed=self._persist_bin_selection,
            on_reload_bin_selection=self._load_bin_selection,
        )

        # 串口/加载 log 的原始字节缓存，供刷新 idlog bin 后重解析。
        # 实时接收的每一块都带主机时间戳，刷新时按原时间重放。
        self._recv_log_segs: List[LogSeg] = []
        self._recv_log_len = 0
        self._recv_log_trim_warned = False
        self._replay_state = None
        # 实时串口 log 待处理队列（rx 线程只入队，主线程批量 drain）
        self._live_log_queue: Deque[bytes] = deque()
        self._live_log_drain_after_id: Optional[str] = None

        # 接收区在左半
        self.assistant = SerialAssistant(
            left_frame,
            get_active_comm=lambda: self._active_comm,
            on_load_log=self._load_log_file,
            on_clear_recv=self._clear_recv_log_buffer,
            on_status=self._post_status,
        )
        # UI 里的 "无换行刷新(ms)" 调整时，立即下发到 SerialManager / LogSerial
        self.assistant.bind_flush_timeout_callback(
            lambda ms: self._apply_flush_timeout(ms)
        )
        self.assistant.bind_show_mode_callback(self._reparse_recv_log)
        # ID_LOG 帧解析器：识别 0xA58E 同步帧，把 [MCU_X] 标记打到 SerialAssistant
        self._id_log_parser = IdLogParser()

        self._build_menu()
        self._build_layout()
        self._apply_cfg_to_ui()
        self._wire_logger_to_ui()

        self._serial_manager: Optional["SerialManager"] = None
        self._active_comm = None
        self._worker: Optional[threading.Thread] = None
        self._cancel_requested = False

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(0, self._startup_first_frame)

    def _startup_first_frame(self):
        """首帧：先最大化显示，布局与扫描放到 idle/短 delay。"""
        self._show_startup_window()
        self.after_idle(self._complete_startup)

    def _show_startup_window(self):
        """先最大化并显示窗口；分隔条等首帧后再调（避免阻塞式 update）。"""
        try:
            if platform.system() == "Windows":
                self.state("zoomed")
            else:
                self.attributes("-zoomed", True)
        except tk.TclError:
            try:
                sw = self.winfo_screenwidth()
                sh = self.winfo_screenheight()
                self.geometry(f"{sw}x{sh}+0+0")
            except Exception:
                pass

    def _complete_startup(self):
        """首帧后一次性完成分隔条与延迟加载（不阻塞窗口首次显示）。"""
        if self._startup_layout_done:
            return
        self.update_idletasks()
        if self.assistant.winfo_height() < 160:
            self.after(1, self._complete_startup)
            return
        self._set_initial_pane_positions()
        self._startup_layout_done = True
        # 串口枚举 / bin 扫描不挡首帧显示
        self.after(50, self._deferred_startup_refresh)

    def _deferred_startup_refresh(self):
        self.serial_panel.refresh_ports()
        self.dl_panel.enable_refresh()
        self.dl_panel.refresh_bins()
        # bin 文件名填上之后右侧需要的宽度才稳定，再摆一次分隔条、算一次最小窗口
        self._apply_scaled_minsize()
        self._set_hpaned_initial_position()

    def _apply_scaled_minsize(self):
        """最小窗口随系统缩放变大，且不小于右栏内容的实际需要。

        固定值只是底线：真正决定下限的是右栏（串口 + 擦除 + 下载）的
        reqwidth/reqheight，否则窗口缩小到最后会把下面几行控件裁掉。
        上限不超过当前屏幕（小屏 + 大缩放时仍能打开）。
        """
        sw = max(1, self.winfo_screenwidth())
        sh = max(1, self.winfo_screenheight())
        need_w = scale_px(self, self.MIN_WIDTH)
        need_h = scale_px(self, self.MIN_HEIGHT)
        right = getattr(self, "_right_frame", None)
        if right is not None:
            try:
                self.update_idletasks()
                need_w = max(need_w,
                             right.winfo_reqwidth() + scale_px(self, 240))
                need_h = max(need_h, right.winfo_reqheight() + scale_px(self, 8))
            except tk.TclError:
                pass
        self.minsize(
            min(need_w, max(640, sw - 40)),
            min(need_h, max(480, sh - 80)),
        )

    def _build_menu(self):
        menubar = tk.Menu(self)
        self._menubar = menubar
        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="打开配置文件", command=self._open_config_file)
        m_file.add_command(label="加载 Log...", command=self._load_log_file)
        m_file.add_separator()
        m_file.add_command(label="保存配置", command=self._save_config_now)
        m_file.add_separator()
        m_file.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=m_file)
        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_command(label="界面放大", accelerator="Ctrl+=",
                           command=self._zoom_in)
        m_view.add_command(label="界面缩小", accelerator="Ctrl+-",
                           command=self._zoom_out)
        m_view.add_command(label="界面缩放重置", accelerator="Ctrl+0",
                           command=self._zoom_reset)
        m_view.add_separator()
        m_view.add_command(label="接收区字号增大", command=lambda: self._recv_font_step(1))
        m_view.add_command(label="接收区字号减小", command=lambda: self._recv_font_step(-1))
        m_view.add_command(label="接收区字号重置", command=self._recv_font_reset)
        menubar.add_cascade(label="视图", menu=m_view)
        m_help = tk.Menu(menubar, tearoff=0)
        m_help.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=m_help)
        m_guide = tk.Menu(menubar, tearoff=0)
        m_guide.add_command(label="回读模式切换", command=self._show_guide_read_toggle)
        menubar.add_cascade(label="使用指南", menu=m_guide)
        self.config(menu=menubar)
        # 快捷键：界面缩放（同 VS Code 的 Ctrl+= / Ctrl+- / Ctrl+0）
        self.bind_all("<Control-Key-equal>", lambda e: self._zoom_in())
        self.bind_all("<Control-Key-plus>", lambda e: self._zoom_in())
        self.bind_all("<Control-Key-minus>", lambda e: self._zoom_out())
        self.bind_all("<Control-Key-0>", lambda e: self._zoom_reset())

    def _build_layout(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        # 顶层 hpaned 占满 self
        self.hpaned.grid(row=0, column=0, sticky=tk.NSEW)
        # 窗口变窄时夹紧分隔条，保证右栏控件不被裁（同 VS Code 侧栏最小宽）
        self.hpaned.bind("<Configure>", self._on_hpaned_resize)
        # 拖完分隔条记住比例（拖拽事件落在 hpaned 自己身上，不会冒泡到子控件）
        self.hpaned.bind("<ButtonRelease-1>", self._remember_hpaned_ratio, add="+")
        try:
            style = ttk.Style()
            style.configure("Sash.Horizontal.TPanedwindow", sashthickness=6)
            self.hpaned.configure(style="Sash.Horizontal.TPanedwindow")
        except Exception:
            pass

        left = self._left_frame
        right = self._right_frame

        # 左半配置：SerialAssistant 撑满
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        self.assistant.grid(in_=left, row=0, column=0,
                              sticky=tk.NSEW, padx=4, pady=2)

        # 右半配置区：上半 serial+erase_read 横排（高度一致），下半 dl_panel 撑满剩余空间。
        # 不用 uniform：150% 时串口栏比擦除栏宽，等宽会把端口框挤短。
        right.columnconfigure(0, weight=1)
        right.columnconfigure(1, weight=1)
        right.rowconfigure(0, weight=0)   # 上半按内容最高决定
        right.rowconfigure(1, weight=1)   # 下半（下载区）撑开

        # sticky=NSEW 让两个 panel 撑满同一行高度
        self.serial_panel.grid(in_=right, row=0, column=0,
                                 sticky=tk.NSEW, padx=4, pady=2)
        self.erase_read_panel.grid(in_=right, row=0, column=1,
                                    sticky=tk.NSEW, padx=4, pady=2)
        self.dl_panel.grid(in_=right, row=1, column=0, columnspan=2,
                            sticky=tk.NSEW, padx=4, pady=2)

    def _set_initial_pane_positions(self):
        self._set_hpaned_initial_position()
        self._set_recv_send_initial_position()

    def _set_recv_send_initial_position(self):
        ratio = None
        if self.cm.has_option("ui", "recv_send_sash_ratio"):
            raw = self.cm.get("ui", "recv_send_sash_ratio", "")
            try:
                ratio = float(raw)
            except (TypeError, ValueError):
                ratio = None
        self.assistant.apply_recv_send_split(ratio)

    def _set_hpaned_initial_position(self):
        try:
            self.update_idletasks()
            total = self.hpaned.winfo_width()
            if total <= 200:
                return
            # 仅当 ini 里显式写过比例时恢复；不要用 ensure_defaults 的占位值
            # 覆盖用户拖拽结果。
            ratio = None
            if self.cm.has_option("ui", "hpaned_sash_ratio"):
                raw = self.cm.get("ui", "hpaned_sash_ratio", "")
                try:
                    ratio = float(raw)
                except (TypeError, ValueError):
                    ratio = None
            if ratio is not None:
                ratio = max(0.0, min(ratio, 1.0))
                # 按用户存的比例原样摆回去：ini 里存的比例和屏幕上的位置必须
                # 一致，否则下次缩放分隔条会"弹回"默认位置。
                pos = h_sash_for_ratio(total, ratio)
            else:
                # 第一次用（ini 里没存过比例）：给个默认位置
                right_need = self._right_frame.winfo_reqwidth() + scale_px(self, 8)
                left_need = scale_px(self, 200)
                try:
                    # FlowFrame 用 place 摆子控件，reqwidth 不代表内容宽度，
                    # 由工具栏自己给出不折行时的宽度
                    left_need = self.assistant.toolbar_width_hint() + scale_px(self, 8)
                except Exception:
                    pass
                pos = initial_h_sash(total, right_need, left_need)
            self.hpaned.sashpos(0, pos)
            self._hpaned_ratio = pos / float(total)
        except Exception:
            pass

    def _remember_hpaned_ratio(self, event=None):
        """记下用户拖好的左右分隔比例。"""
        if event is not None and event.widget is not self.hpaned:
            return
        try:
            total = self.hpaned.winfo_width()
            if total > 200:
                self._hpaned_ratio = self.hpaned.sashpos(0) / float(total)
        except tk.TclError:
            pass

    def _on_hpaned_resize(self, event=None):
        """窗口尺寸变化后按用户设的比例重摆分隔条。

        ttk.PanedWindow 在窗口变大时把多出来的宽度按 weight(3:2) 分给两栏，
        所以最大化、或者从别的显示器拖回来（会先还原再最大化）之后，用户
        拖窄的那一栏会被撑开，看上去就像分隔条“恢复默认值”。这里记住比例，
        尺寸一变就按比例摆回去。

        比例是用户拖出来的，原样摆回去，不再按右栏需要的宽度夹紧 —— 夹紧
        会顶掉用户的选择，下次缩放分隔条又弹回默认位置。
        """
        try:
            total = self.hpaned.winfo_width()
            if total <= 200:
                return
            if self._hpaned_ratio is None:
                return
            target = h_sash_for_ratio(total, self._hpaned_ratio)
            if abs(target - self.hpaned.sashpos(0)) > 1:
                self.hpaned.sashpos(0, target)
        except tk.TclError:
            pass

    def _get_hpaned_sash_ratio(self) -> Optional[float]:
        try:
            total = self.hpaned.winfo_width()
            if total <= 200:
                return None
            return self.hpaned.sashpos(0) / float(total)
        except Exception:
            return None

    def _apply_cfg_to_ui(self):
        sp = self.serial_panel
        log_port = self.cm.get("serial", "log_port", "COM5")
        dl_port = self.cm.get("serial", "download_port", log_port)
        if dl_port and dl_port != log_port:
            self._post_status(
                "WARN",
                f"配置 log_port({log_port}) 与 download_port({dl_port}) 不一致，"
                f"UI 统一使用 {log_port}",
            )
            self.cm.set("serial", "download_port", log_port)
        sp.port.set(log_port or sp.port.get())
        sp.log_baud.set(self.cm.get("serial", "log_baud", "500000"))
        sp.dl_baud.set(self.cm.get("serial", "download_baud", "3000000"))
        sp.comm_type.set(self.cm.get("flashload", "comm_type", "uart"))
        sp.data_bits.set(self.cm.get("serial", "data_bits", "8"))
        sp.parity.set(self.cm.get("serial", "parity", "none"))
        sp.stop_bits.set(self.cm.get("serial", "stop_bits", "1"))

        # 无换行刷新超时（默认 100ms）。0 表示禁用
        try:
            ft_ms = int(self.cm.get("serial", "flush_timeout_ms", "100"))
        except ValueError:
            ft_ms = 100
        self.assistant.set_flush_timeout_ms(ft_ms)

        chip = self.cm.get("bins", "default_chip", "9032")
        self.dl_panel.bin_dir.set(self.cm.get("bins", "default_dir", "./bin"))
        self.dl_panel.chip_type.set(chip)
        # 下载后 reset：是否执行 + rdelay（秒，默认 2）
        self.dl_panel.do_reset.set(
            self._cfg_bool(self.cm.get("flashload", "reset_after_download"), default=True))
        self.dl_panel.rdelay.set(self.cm.get("flashload", "reset_rdelay", "2") or "2")
        self.dl_panel._apply_reset_rdelay_state()

        bin_paths = {}
        id_log_paths = {}
        for key in DownloadPanel.BINARY_KEYS:
            path = self.cm.get("bins", f"manual_path_{key}", "")
            if path:
                bin_paths[key] = path
        for key in DownloadPanel.BINARY_KEYS:
            if not key.startswith("mcu_"):
                continue
            path = self.cm.get("bins", f"manual_idlog_{key}", "")
            if path:
                id_log_paths[key] = path
        self.dl_panel.apply_manual_paths_state(bin_paths, id_log_paths)
        self._load_bin_selection()

        # 回读模式（双击"回读"按钮切换）初始值取配置，切换后随保存写回
        self.erase_read_panel.set_xip(
            self._cfg_bool(self.cm.get("flashload", "xip_read", "false")))
        self._apply_erase_read_fields()

        saved = self.cm.list_presets()
        self.assistant.set_presets(saved if saved else self.assistant._default_presets())

        find_hist = self.assistant.decode_find_history(
            self.cm.get("ui", "find_history", ""))
        self.assistant.set_find_history(find_hist)
        find_case = str(self.cm.get("ui", "find_case_sensitive", "false")).lower()
        self.assistant.set_find_case_sensitive(find_case in ("1", "true", "yes", "on"))
        self.assistant.auto_scroll.set(
            self._cfg_bool(self.cm.get("ui", "auto_scroll", "true"), default=True))
        try:
            font_size = int(self.cm.get("ui", "recv_font_size", "10"))
        except (TypeError, ValueError):
            font_size = 10
        self.assistant.set_recv_font_size(font_size)

    @staticmethod
    def _cfg_bool(val, default: bool = False) -> bool:
        if val is None:
            return default
        return str(val).lower() in ("1", "true", "yes", "on")

    def _post_status(self, level: str, msg: str):
        """线程安全：写入下载区「状态」面板（非串口接收区）。"""
        text = msg.rstrip()
        self.after(0, lambda lv=level, t=text: self.dl_panel.append_status(t, lv))

    def _wire_logger_to_ui(self):
        def ui_sink(level: str, msg: str):
            # 程序日志 → 状态面板；串口接收区仅显示设备数据 / 解析 ID log
            self._post_status(level, msg.rstrip())
        self._base_logger.set_sinks(regular_sink=ui_sink)

    def _collect_ui_to_cfg(self):
        sp = self.serial_panel
        port = sp.port.get()
        self.cm.set("serial", "log_port", port)
        self.cm.set("serial", "download_port", port)
        self.cm.set("serial", "log_baud", sp.log_baud.get())
        self.cm.set("serial", "download_baud", sp.dl_baud.get())
        self.cm.set("serial", "data_bits", sp.data_bits.get())
        self.cm.set("serial", "parity", sp.parity.get())
        self.cm.set("serial", "stop_bits", sp.stop_bits.get())
        self.cm.set("serial", "flush_timeout_ms", str(self.assistant.get_flush_timeout_ms()))
        self.cm.set("flashload", "comm_type", sp.comm_type.get())
        self.cm.set("flashload", "xip_read",
                    "true" if self.erase_read_panel.use_xip.get() else "false")
        self._store_erase_read_fields()
        self.cm.set("flashload", "reset_after_download",
                    "true" if self.dl_panel.do_reset.get() else "false")
        self.cm.set("flashload", "reset_rdelay", self.dl_panel.rdelay.get())
        self.cm.set("bins", "default_chip", self.dl_panel.chip_type.get())
        self.cm.set("bins", "default_dir", self.dl_panel.bin_dir.get())
        bin_paths, id_log_paths = self.dl_panel.collect_manual_paths_state()
        for key in DownloadPanel.BINARY_KEYS:
            self.cm.set("bins", f"manual_path_{key}", bin_paths.get(key, ""))
        for key in DownloadPanel.BINARY_KEYS:
            if not key.startswith("mcu_"):
                continue
            self.cm.set("bins", f"manual_idlog_{key}", id_log_paths.get(key, ""))
        for key, checked in self.dl_panel.collect_bin_selected().items():
            self.cm.set("bins", f"selected_{key}", "true" if checked else "false")
        self.cm.save_presets(self.assistant.collect_presets())
        self.cm.set(
            "ui", "find_history",
            self.assistant.encode_find_history(self.assistant.get_find_history()),
        )
        self.cm.set(
            "ui", "find_case_sensitive",
            "true" if self.assistant.get_find_case_sensitive() else "false",
        )
        self.cm.set(
            "ui", "auto_scroll",
            "true" if self.assistant.auto_scroll.get() else "false",
        )
        self.cm.set(
            "ui", "recv_font_size",
            str(self.assistant.get_recv_font_size()),
        )
        sash_ratio = self._hpaned_ratio
        if sash_ratio is None:
            sash_ratio = self._get_hpaned_sash_ratio()
        if sash_ratio is not None:
            self.cm.set("ui", "hpaned_sash_ratio", f"{sash_ratio:.4f}")
        recv_ratio = self.assistant.get_recv_send_ratio()
        if recv_ratio is not None:
            self.cm.set("ui", "recv_send_sash_ratio", f"{recv_ratio:.4f}")

    def _apply_erase_read_fields(self):
        """擦除/回读地址和大小从 [flashload] 恢复。缺项时保留界面默认值。"""
        erp = self.erase_read_panel
        fields = (
            ("erase_addr", erp.erase_addr),
            ("erase_size", erp.erase_size),
            ("flash_read_addr", erp.read_addr),
            ("flash_read_size", erp.read_size),
        )
        for key, var in fields:
            saved = self.cm.get("flashload", key, "")
            if str(saved).strip():
                var.set(str(saved).strip())

    def _store_erase_read_fields(self):
        erp = self.erase_read_panel
        self.cm.set("flashload", "erase_addr", erp.erase_addr.get().strip())
        self.cm.set("flashload", "erase_size", erp.erase_size.get().strip())
        self.cm.set("flashload", "flash_read_addr", erp.read_addr.get().strip())
        self.cm.set("flashload", "flash_read_size", erp.read_size.get().strip())

    def _load_bin_selection(self):
        """从 ini 读出勾选意图。界面是否勾上由 refresh 再看文件是否存在。"""
        selected = {}
        for key in DownloadPanel.BINARY_KEYS:
            raw = self.cm.get("bins", f"selected_{key}", None)
            if raw is None or not str(raw).strip():
                selected[key] = True
            else:
                selected[key] = self._cfg_bool(raw)
        self.dl_panel.set_bin_selected(selected)

    def _persist_bin_selection(self):
        for key, checked in self.dl_panel.collect_bin_selected().items():
            self.cm.set("bins", f"selected_{key}", "true" if checked else "false")
        try:
            self.cm.save()
        except Exception as e:
            self.logger.error(f"保存 bin 勾选失败: {e}")

    def _save_config_now(self):
        self._collect_ui_to_cfg()
        self.cm.save()
        self.logger.info(f"配置已保存: {self.cm.path}")

    def _open_config_file(self):
        if not os.path.exists(self.cm.path):
            self._save_config_now()
        path = os.path.abspath(self.cm.path)
        self.logger.info(f"配置文件路径: {path}")
        try:
            if platform.system() == "Windows":
                try:
                    os.startfile(path)
                except OSError as e:
                    if not _is_no_file_association(e):
                        raise
                    self.logger.info("该文件没有默认打开程序，请在「打开方式」中选择")
                    owner = 0
                    try:
                        owner = int(self.winfo_id())
                    except (tk.TclError, ValueError, OSError):
                        pass
                    _open_with_dialog(path, owner)
            elif platform.system() == "Darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            self.logger.error(f"打开配置文件失败: {e}")
            messagebox.showerror("失败", str(e))

    def _load_log_file(self):
        initial_dir = get_logs_dir()
        try:
            save_dir = os.path.dirname(self.assistant.save_path.get())
            if save_dir and os.path.isdir(save_dir):
                initial_dir = save_dir
        except Exception:
            pass
        path = filedialog.askopenfilename(
            parent=self,
            title="加载 Log",
            initialdir=initial_dir,
            filetypes=[
                ("Log 文件", "*.log"),
                ("文本文件", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        if self._replay_state is not None:
            messagebox.showinfo("提示", "正在加载 Log，请稍候")
            return
        try:
            self._replay_log_file(path)
        except Exception as e:
            self._set_log_load_busy(False)
            self.logger.error(f"加载 Log 失败: {path} - {e}")
            messagebox.showerror("加载失败", str(e))

    def _reload_chip_files(self, chip: str) -> dict:
        self.cm.reload()
        return self.cm.chip_bin_files(chip)

    def _chip_files_for_key(self, chip: str, key: str) -> Optional[dict]:
        """读取 chip 配置，给出单个 bin 的 selected_bins 格式（用于自动补 programmer）。"""
        info = self.cm.chip_bin_files(chip).get(key)
        if not info or not info.get("keyword"):
            return None
        dp = self.dl_panel
        if dp._manual_selection.get(key) and dp._paths.get(key):
            full = dp._paths[key]
            fname = os.path.basename(full)
            if os.path.isfile(full):
                id_log_path = None
                if dp._id_log_manual.get(key) and dp._id_log_paths.get(key):
                    id_log_path = dp._id_log_paths[key]
                return {
                    "key": key,
                    "label": info.get("label", key),
                    "filename": fname,
                    "path": full,
                    "addr": info["addr"],
                    "id_log": info.get("id_log"),
                    "id_log_path": id_log_path,
                    "mode": info.get("mode", "flashload"),
                }
        bin_dir = dp.bin_dir.get()
        fname = dp._find_bin_file(bin_dir, info["keyword"], chip)
        if not fname:
            return None
        full = os.path.join(bin_dir, fname) if bin_dir else fname
        return {
            "key": key,
            "label": info.get("label", key),
            "filename": fname,
            "path": full,
            "addr": info["addr"],
            "id_log": info.get("id_log"),
            "mode": info.get("mode", "flashload"),
        }

    def _open_param_dialog(self):
        SerialParamDialog(self, self.serial_panel)

    def _show_guide_read_toggle(self):
        """使用指南第一项：回读 / XIP回读 的切换方式。"""
        messagebox.showinfo(
            "使用指南 - 回读模式切换",
            "回读按钮支持单击 / 双击两种操作：\n\n"
            "· 单击「回读」：按当前模式立即执行回读\n"
            "  - 普通模式执行 flash_read\n"
            "  - XIP 模式执行 flash_xip_read\n\n"
            "· 双击「回读」：在「回读」↔「XIP回读」之间切换，\n"
            "  按钮文字会显示当前模式（回读 / XIP回读）。\n"
            "  切换不会触发回读，双击后的第一次单击才执行。\n\n"
            "· 模式会随配置保存（[flashload].xip_read），\n"
            "  下次启动时保持上次选择的模式。\n\n"
            "注意：单击与双击的区分有约半个双击时长的延迟，属正常现象。",
        )

    def _show_about(self):
        build_time = (
            __build_time__
            if __build_time__ != "dev"
            else "开发环境（未打包）"
        )
        messagebox.showinfo(
            "关于",
            "EPM Download Tool\n\n"
            f"版本：{__version__}\n"
            f"构建时间：{build_time}\n\n"
            f"配置：{self.cm.path}\n"
            "支持 UI + CLI / 芯片 9032/9062",
        )

    def _ensure_manager(self) -> "SerialManager":
        from commands import SerialManager

        sp = self.serial_panel
        ft_ms = self.assistant.get_flush_timeout_ms()
        if self._serial_manager is None:
            self._serial_manager = SerialManager(
                log_port=sp.port.get(),
                log_baud=int(sp.log_baud.get()),
                download_port=sp.port.get(),
                download_baud=int(sp.dl_baud.get()),
                comm_type=sp.comm_type.get(),
                on_log_line=lambda b: self._safe_log_line(b),
                flush_timeout_ms=ft_ms,
            )
        else:
            # 每次都同步 UI 当前值，防止用户改 port/baud 后用旧值
            self._serial_manager._log_port = sp.port.get()
            self._serial_manager._log_baud = int(sp.log_baud.get())
            self._serial_manager._download_port = sp.port.get()
            self._serial_manager._download_baud = int(sp.dl_baud.get())
            self._serial_manager._comm_type = sp.comm_type.get()
            self._serial_manager.set_flush_timeout_ms(ft_ms)
        return self._serial_manager

    def _apply_flush_timeout(self, ms: int):
        """SerialAssistant 回调：把 UI 当前 ms 推到 SerialManager / LogSerial。
        未打开时不创建 manager；打开后再调一次 ensure_manager 即可生效。"""
        if self._serial_manager is None:
            return
        self._serial_manager.set_flush_timeout_ms(ms)

    def _open_serial(self) -> bool:
        mgr = self._ensure_manager()
        # 清 ID log parser 累计统计
        if hasattr(self, "_id_log_parser"):
            self._id_log_parser.clear()
        # 同步 UI 的"下载"波特率到 ini 的 [serial].download_baud 和 [flashload].target_baud，
        # 这样用户改 Combobox 就直接生效，不用手动改 ini。
        # （target_baud=0 的语义保留：表示不切换，保持 500K）
        try:
            port = self.serial_panel.port.get()
            dl_baud = int(self.serial_panel.dl_baud.get())
            self.cm.set("serial", "log_port", port)
            self.cm.set("serial", "download_port", port)
            self.cm.set("serial", "download_baud", str(dl_baud))
            self.cm.set("flashload", "target_baud", str(dl_baud))
            self.cm.save()
        except Exception:
            pass
        self._cancel_live_log_drain()
        self._live_log_queue.clear()
        ok = mgr.open_log()
        if ok:
            self._active_comm = mgr.log_serial
            self.logger.info(f"串口已打开: {self.serial_panel.port.get()} @ {self.serial_panel.log_baud.get()}")
            # 新的串口会话：自动保存开启时切换到新的日志文件
            self.assistant.restart_auto_save()
        else:
            self.logger.error(f"打开串口失败: {self.serial_panel.port.get()}")
            self.serial_panel.mark_closed()
        return ok

    def _close_serial(self):
        self._flush_live_log_queue()
        self.assistant.stop()
        if self._serial_manager is not None:
            self._serial_manager.close_log()
        self._active_comm = None
        self.logger.info("串口已关闭")

    def _cancel_live_log_drain(self):
        aid = self._live_log_drain_after_id
        self._live_log_drain_after_id = None
        if aid is not None:
            try:
                self.after_cancel(aid)
            except Exception:
                pass

    def _flush_live_log_queue(self):
        """关串口/退出前：取消定时 drain 并处理完队列中剩余 raw 块。"""
        self._cancel_live_log_drain()
        while self._live_log_queue:
            raw = self._live_log_queue.popleft()
            self._emit_log_chunk(raw)
        self.assistant.set_defer_scroll(False)
        self.assistant.flush_pending()

    def _safe_log_line(self, raw: bytes):
        if not raw:
            return
        self._live_log_queue.append(raw)
        if self._live_log_drain_after_id is None:
            self._live_log_drain_after_id = self.after(
                _LIVE_LOG_DRAIN_MS, self._drain_live_log_queue)

    def _drain_live_log_queue(self):
        self._live_log_drain_after_id = None
        if not self._live_log_queue:
            self.assistant.set_defer_scroll(False)
            self.assistant.flush_pending()
            return
        backlog = len(self._live_log_queue)
        self.assistant.set_defer_scroll(backlog > _LIVE_LOG_BATCH)
        n = min(_LIVE_LOG_BATCH, backlog)
        for _ in range(n):
            self._emit_log_chunk(self._live_log_queue.popleft())
        self.assistant.flush_pending()
        if self._live_log_queue:
            self._live_log_drain_after_id = self.after(
                _LIVE_LOG_DRAIN_MS, self._drain_live_log_queue)
            return
        self.assistant.set_defer_scroll(False)
        if self.assistant.auto_scroll.get():
            try:
                self.assistant.recv_text.see(tk.END)
            except tk.TclError:
                pass

    @staticmethod
    def _split_log_chunks(data: bytes) -> List[bytes]:
        """按 LogSerial 相同规则切分字节流，保持文本与 ID 帧的交错顺序。"""
        from communication.log_serial import LogSerial

        buf = bytearray(data)
        chunks: List[bytes] = []
        while True:
            lines, buf = LogSerial._split_lines(buf)
            chunks.extend(lines)
            if not buf:
                break
            chunks.append(bytes(buf))
            break
        return chunks

    def _clear_recv_log_buffer(self):
        self._cancel_log_replay()
        self._cancel_live_log_drain()
        self._live_log_queue.clear()
        self._set_log_load_busy(False)
        self._recv_log_segs = []
        self._recv_log_len = 0
        self._recv_log_trim_warned = False

    def _append_recv_log_raw(self, data: bytes, ts: Optional[str]):
        if not data:
            return
        self._recv_log_segs.append((data, ts))
        self._recv_log_len += len(data)
        self._trim_recv_log_raw()

    def _trim_recv_log_raw(self, *, warn: bool = True):
        new_segs, new_len, trimmed = trim_log_segments(
            self._recv_log_segs, self._recv_log_len, RECV_LOG_RAW_MAX)
        if not trimmed:
            return
        self._recv_log_segs = new_segs
        self._recv_log_len = new_len
        if warn and not self._recv_log_trim_warned:
            self._recv_log_trim_warned = True
            mb = RECV_LOG_RAW_MAX // (1024 * 1024)
            self.logger.warn(f"串口原始 log 缓存已超过 {mb}MB，较早数据已丢弃")

    def _cancel_log_replay(self):
        self._replay_state = None

    def _set_log_load_busy(self, busy: bool):
        try:
            state = tk.DISABLED if busy else tk.NORMAL
            self.assistant.btn_load_log.configure(state=state)
        except Exception:
            pass

    def _on_download_panel_refresh(self):
        """下载区刷新：重载 idlog bin，并重解析接收区中的 ID log。"""
        self._id_log_maps_cache = None
        self._reparse_recv_log()

    def _replay_log_file(self, path: str):
        """加载 Log 文件：先清空接收区，再按 idlog bin 解析 ID_LOG 帧后显示。"""
        self._cancel_log_replay()
        with open(path, "rb") as fp:
            data = fp.read()
        self._recv_log_segs = [(data, None)] if data else []
        self._recv_log_len = len(data)
        self._trim_recv_log_raw()
        self._set_log_load_busy(True)

        def _done():
            self._set_log_load_busy(False)
            self.logger.info(f"已加载 Log: {path}")

        self._replay_log_bytes(data, on_done=_done)

    def _reparse_recv_log(self):
        """用当前 idlog bin 重新解析接收区缓存，并保留实时接收时的主机时间戳。"""
        if self._recv_log_len <= 0 or not self._recv_log_segs:
            return
        self._cancel_log_replay()
        segs = list(self._recv_log_segs)
        # 加载的文件是一整段、没有接收时间。文本里的时间戳已经在内容里。
        if len(segs) == 1 and segs[0][1] is None:
            self._replay_log_bytes(segs[0][0])
            return
        self._replay_log_segments(segs)

    def _replay_log_bytes(self, data: bytes, on_done=None):
        """重放原始 log 字节流（不写入缓存、不清空缓存）。"""
        self.assistant.clear_recv(clear_buffer=False)
        self.assistant._end_find_session()
        if not data:
            if on_done:
                on_done()
            return
        if b"\x8e\xa5" not in data and self.assistant.show_mode.get() != "HEX":
            self.assistant.load_log_bytes(data, clear_buffer=False, on_done=on_done)
            return
        chunks = self._split_log_chunks(data)
        self._begin_log_replay(chunks, None, len(data), on_done, already_cleared=True)

    def _replay_log_segments(self, segs: List[LogSeg], on_done=None):
        """按实时接收时的分块重放，套用当时记下的主机时间戳。"""
        show = self.assistant.show_ts.get()
        chunks: List[bytes] = []
        prefixes: List[str] = []
        total = 0
        for raw, ts in segs:
            if not raw:
                continue
            shown = replay_host_ts(ts, show)
            chunks.append(raw)
            prefixes.append("" if shown is None else shown)
            total += len(raw)
        self._begin_log_replay(chunks, prefixes, total, on_done)

    def _begin_log_replay(self, chunks: List[bytes], ts_prefixes: Optional[List[str]],
                          total_len: int, on_done=None, already_cleared: bool = False):
        if not already_cleared:
            self.assistant.clear_recv(clear_buffer=False)
            self.assistant._end_find_session()
        if not chunks:
            if on_done:
                on_done()
            return
        if total_len < LARGE_LOG_BYTES:
            parser = IdLogParser()
            id_maps = self._build_id_log_maps()
            ini_map = self.cm.list_id_log_map()
            for i, chunk in enumerate(chunks):
                prefix = None if ts_prefixes is None else ts_prefixes[i]
                self._emit_log_chunk(
                    chunk, parser, id_maps, ini_map,
                    record=False, add_ts=False, ts_prefix=prefix, save=False)
            self.assistant.flush_pending()
            if on_done:
                on_done()
            return
        self._replay_state = {
            "chunks": chunks,
            "ts_prefixes": ts_prefixes,
            "index": 0,
            "parser": IdLogParser(),
            "id_maps": self._build_id_log_maps(),
            "ini_map": self.cm.list_id_log_map(),
            "batch": _REPLAY_CHUNK_BATCH,
            "on_done": on_done,
        }
        self.assistant.set_defer_scroll(True)
        self._replay_log_tick()

    def _replay_log_tick(self):
        st = self._replay_state
        if not st:
            return
        end = min(st["index"] + st["batch"], len(st["chunks"]))
        prefixes = st.get("ts_prefixes")
        for i in range(st["index"], end):
            prefix = None if prefixes is None else prefixes[i]
            self._emit_log_chunk(
                st["chunks"][i], st["parser"], st["id_maps"], st["ini_map"],
                record=False, add_ts=False, ts_prefix=prefix, save=False)
        st["index"] = end
        self.assistant.flush_pending()
        if st["index"] < len(st["chunks"]):
            self.after(1, self._replay_log_tick)
            return
        self._replay_state = None
        self.assistant.set_defer_scroll(False)
        if self.assistant.auto_scroll.get():
            try:
                self.assistant.recv_text.see(tk.END)
            except tk.TclError:
                pass
        cb = st.get("on_done")
        if cb:
            cb()

    def _emit_log_chunk(self, raw: bytes, parser=None, id_maps=None, ini_map=None,
                        record: bool = True, add_ts: bool = True,
                        ts_prefix: Optional[str] = None, save: bool = True):
        """解析 ID_LOG 帧并写入接收区（与实时串口显示逻辑一致）。

        实时接收时记下主机时间戳。刷新重放传入 ts_prefix，避免时间被刷成空。
        """
        if not raw:
            return
        if record:
            captured = self.assistant.format_host_ts()
            self._append_recv_log_raw(raw, captured)
            self.assistant.note_recv_bytes(len(raw))
            ts_prefix = captured if self.assistant.show_ts.get() else ""
            add_ts = False
        if parser is None:
            parser = self._id_log_parser
        entries, remaining = parser.feed(raw)
        if self.assistant.show_mode.get() == "HEX":
            self.assistant.feed_line(
                raw, add_ts=add_ts, ts_prefix=ts_prefix, save=save)
            return
        if remaining and remaining.strip(b"\r\n"):
            self.assistant.feed_line(
                remaining, add_ts=add_ts, ts_prefix=ts_prefix, save=save)
        if entries:
            if id_maps is None:
                id_maps = self._build_id_log_maps()
            if ini_map is None:
                ini_map = self.cm.list_id_log_map()
            for e in entries:
                level = self._cpu_to_id_level(e.cpu_id)
                self.assistant.feed_log(
                    level, self._format_id_log(e, id_maps, ini_map), add_ts=add_ts,
                    head=format_id_log_head(level, e.timestamp),
                    ts_prefix=ts_prefix, save=save)

    def _idlog_bin_dir(self) -> str:
        dp = getattr(self, "dl_panel", None)
        return dp.bin_dir.get() if dp is not None else ""

    def _idlog_base_addrs(self, paths: dict) -> dict:
        """各核基址。单核 config 覆盖 > 对应 idlog_addr_*.txt > 共享 base_addr。"""
        per_cpu = {
            key: self.cm.get_hex("id_log", f"base_addr_{key}", 0)
            for key in ("mcu_a", "mcu_b", "mcu_c")
        }
        shared = self.cm.get_hex("id_log", "base_addr", 0)
        return resolve_idlog_base_addrs(self._idlog_bin_dir(), paths, per_cpu, shared)

    def _idlog_bin_paths(self) -> dict:
        """mcu_a/b/c -> idlog bin 绝对路径（含手动选择、tools/bin 自动发现）。"""
        dp = getattr(self, "dl_panel", None)
        bin_dir = self._idlog_bin_dir()
        names = {
            key: self.cm.get("id_log", key) or None
            for key in ("mcu_a", "mcu_b", "mcu_c")
        }
        manual = {}
        if dp is not None:
            for key in ("mcu_a", "mcu_b", "mcu_c"):
                if dp._id_log_manual.get(key) and dp._id_log_paths.get(key):
                    manual[key] = dp._id_log_paths[key]
        return resolve_idlog_bin_paths(bin_dir, names, manual)

    def _idlog_bin_path(self, key: str) -> Optional[str]:
        return self._idlog_bin_paths().get(key)

    @staticmethod
    def _cpu_to_id_level(cpu_id: int) -> str:
        """cpu_id → SerialAssistant level：ID_A / ID_B / ID_C / ID"""
        return {0: "ID_A", 1: "ID_B", 2: "ID_C"}.get(cpu_id, "ID")

    def _build_id_log_maps(self) -> dict:
        """按 cpu_id 加载所有可用 idlog_*.bin（A/B/C）。
        不按 chip 过滤：9032 设备虽然没 C 核，但 bin_dir 下可能有 idlog_MCU_C.bin
        （9062 的 bin），加载它能保证收到 C 核 log 时也能解析。
        返回 {cpu_id: {log_id: text}}；0xFF (未知核) 用所有 bin 的合并。
        结果按 (bin 路径, mtime) 缓存，避免每条 ID log 都重新解析 bin 文件。
        """
        _CPU_KEYS = ("mcu_a", "mcu_b", "mcu_c")
        _CPU_ID = {key: i for i, key in enumerate(_CPU_KEYS)}
        paths = self._idlog_bin_paths()
        bases = self._idlog_base_addrs(paths)
        cache_entries = []
        for key in _CPU_KEYS:
            p = paths.get(key)
            base = bases.get(key)
            if not p:
                cache_entries.append((key, None, None, base))
                continue
            try:
                mtime = os.path.getmtime(p)
            except OSError:
                mtime = None
            cache_entries.append((key, p, mtime, base))
        cache_key = tuple(cache_entries)
        cached = getattr(self, "_id_log_maps_cache", None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]
        # 按 cpu_id 绑定 idlog bin（A=0, B=1, C=2），避免缺文件时索引错位
        out = {0: {}, 1: {}, 2: {}}
        for key in _CPU_KEYS:
            p = paths.get(key)
            if not p:
                continue
            out[_CPU_ID[key]] = parse_idlog_bin(p, bases.get(key))
        # 未知核：所有 bin 合并（兜底）
        out[0xFF] = merge_idlog_maps(out[0], out[1], out[2])
        self._id_log_maps_cache = (cache_key, out)
        return out

    @staticmethod
    def _format_id_log(entry, id_maps: dict, ini_map: dict) -> str:
        """把 ID_LOG 条目转成可读文本。

        正文。固件时间戳与 ID 标合并显示，模块号不显示。
        - 正常映射：格式串填好参数
        - 参数不够：剩下的占位符原样保留
        - 没找到映射：log_id=0x......
        hexdump 帧在正文后附上数据字节。
        """
        text = ini_map.get(entry.log_id, "")
        if not text:
            bin_map = id_maps.get(entry.cpu_id) or id_maps.get(0xFF, {})
            text = bin_map.get(entry.log_id, "")
        return format_id_log_line(entry, text)

    def _cancel_download(self):
        """用户点击"取消" — 设置串口 cancel 标志，工作线程下次 tick 会退出协议循环。"""
        if self._worker is None or not self._worker.is_alive():
            return
        self._cancel_requested = True
        self.logger.warn("=== 收到取消请求，正在中断下载... ===")
        if self._serial_manager is not None:
            self._serial_manager.cancel()
        # 取消按钮置灰，避免重复触发
        self.dl_panel.btn_dl.configure(state=tk.DISABLED, text="取消中...")

    @staticmethod
    def _bin_stamp(path: str) -> str:
        """bin 的修改时间（'MM-DD HH:MM:SS'），取不到返回 '?'。

        打进下载日志：出现"烧到旧版本"时，这条时间就是判断依据——
        它是这次真正烧进去的那份产物的编译时间。
        """
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return "?"
        return time.strftime("%m-%d %H:%M:%S", time.localtime(mtime))

    @staticmethod
    def _file_size(path: str) -> int:
        try:
            return os.path.getsize(path)
        except OSError:
            return 0

    def _start_download(self, params: dict):
        if self._worker is not None and self._worker.is_alive():
            self.logger.warn("已有任务在进行中")
            return
        chip = params["chip_type"]
        spec = DOWNLOAD_SPEC.get(chip)
        if spec is None:
            self.logger.error(f"不支持的芯片: {chip}")
            messagebox.showerror("不支持", f"未配置 {chip} 的下载流程，请编辑 config.ini")
            return
        if not params.get("selected_bins"):
            self.logger.warn("未选择任何 bin，已取消")
            messagebox.showwarning("未选择", "请先勾选至少一个 bin")
            return
        # 未勾选 prog：不自动补 programmer，直接烧已勾选的 bin（调试下载）
        has_programmer = any(b["key"] == "programmer" for b in params["selected_bins"])
        if not has_programmer:
            self.logger.warn("未勾选 prog，跳过 programmer 阶段，进入调试下载")
        # 擦除区间按各 bin 的大小算：区间重叠 = 后写的覆盖先写的 → 提前提示
        self._warn_region_overlap(chip, params["selected_bins"])
        # 启动新一轮下载：清掉旧的 cancel 状态
        self._cancel_requested = False
        if self._serial_manager is not None and self._serial_manager.download_serial is not None:
            self._serial_manager.download_serial.reset_cancel()
        # 记录下载前的 log 串口状态，下载结束后按此恢复
        was_log_open = self.serial_panel._opened
        # 打印当前每个 bin 的解析结果，便于排查 ini 配置问题
        for b in params["selected_bins"]:
            path = b.get("path", "")
            self.logger.info(
                f"  bin[{b['key']}]: file={os.path.basename(path)}, "
                f"mtime={self._bin_stamp(path)}, size=0x{self._file_size(path):x}, "
                f"addr=0x{b.get('addr',0):08x}, label={b.get('label','')}")
        mgr = self._ensure_manager()
        sp = self.serial_panel
        mgr._log_port = sp.port.get()
        mgr._log_baud = int(sp.log_baud.get())
        mgr._download_port = sp.port.get()
        mgr._download_baud = int(sp.dl_baud.get())
        mgr._comm_type = sp.comm_type.get()
        self._serial_manager = mgr
        # 同步 UI "下载" 波特率到 ini 的 [serial].download_baud 和 [flashload].target_baud，
        # 这样 _run_download 读到的 target_baud_cfg 就是 Combobox 当前值
        try:
            self.cm.set("serial", "download_baud", str(int(sp.dl_baud.get())))
            self.cm.set("flashload", "target_baud", str(int(sp.dl_baud.get())))
            self.cm.save()
        except Exception:
            pass

        self._set_running(True, serial_opened=was_log_open)
        self.logger.info(
            f"开始下载: chip={chip}, bins={[b['key'] for b in params['selected_bins']]}")
        self._worker = threading.Thread(
            target=self._run_download,
            args=(mgr, params, spec, was_log_open), daemon=True,
        )
        self._worker.start()

    def _warn_region_overlap(self, chip: str, selected_bins: List[dict]):
        """各 bin 的擦除/写入区间若互相重叠，提示后写的会覆盖先写的。"""
        regions = plan_flash_regions(chip, selected_bins)
        overlaps = find_region_overlaps(regions)
        if not overlaps:
            return
        lines = []
        for a, b, lo, hi in overlaps:
            lines.append(
                f"{a['label']} [0x{a['addr']:08x}, 0x{a['end']:08x}) 与 "
                f"{b['label']} [0x{b['addr']:08x}, 0x{b['end']:08x}) "
                f"重叠 0x{hi - lo:x} @ 0x{lo:08x}")
        for line in lines:
            self.logger.warn(f"擦除区间重叠: {line}")
        messagebox.showwarning(
            "擦除区间重叠",
            "以下 bin 的擦除/写入区间互相重叠，后写的会覆盖先写的：\n\n"
            + "\n".join(lines)
            + "\n\n请检查 config.ini 里 [chip_xxx] 的地址，或换一个更小的 bin。",
            parent=self,
        )

    def _zoom_in(self):
        self._set_zoom(self._zoom * 1.15)

    def _zoom_out(self):
        self._set_zoom(self._zoom / 1.15)

    def _zoom_reset(self):
        self._set_zoom(1.0)

    def _set_zoom(self, zoom: float):
        """界面缩放（相对系统显示缩放的倍率，1.0 = 跟随系统）。

        不能靠改 tk scaling：Tk 改 scaling 后已创建的控件不会重绘。
        这里改字号磅值（ttk 样式 + 命名字体 + 各面板显式字体），像素间距
        由 scale_px() 按同一倍率放大，整界面等比缩放（同 VS Code 的 zoom）。
        """
        try:
            zoom = round(max(0.5, min(3.0, float(zoom))), 3)
        except (TypeError, ValueError):
            zoom = 1.0
        self._zoom = zoom
        self.cm.set("ui", "layout_scale", f"{zoom:g}")
        try:
            self.cm.save()
        except Exception as e:
            self.logger.error(f"保存界面缩放失败: {e}")
        # 记录当前分隔条比例：字号变大后按比例重摆，不跳回 ini 里的旧值
        h_ratio = self._get_hpaned_sash_ratio()
        recv_ratio = self.assistant.get_recv_send_ratio()
        apply_ui_zoom(zoom)
        # 顺序：ttk 样式 + 命名字体 → 各面板显式字体 → 折行重排
        fonts = refresh_ui_fonts(self)
        self._apply_menu_font(fonts)
        self.serial_panel.refresh_fonts()
        self.erase_read_panel.refresh_fonts()
        self.dl_panel.refresh_fonts()
        self.assistant.refresh_fonts()
        self.update_idletasks()
        self._apply_scaled_minsize()
        self._restore_sash_ratios(h_ratio, recv_ratio)
        self.logger.info(f"界面缩放: {zoom * 100:.0f}%")

    def _restore_sash_ratios(self, h_ratio, recv_ratio):
        """缩放后按原比例重摆分隔条（字体变大后右栏/发送区的最小尺寸也变大）。"""
        try:
            total = self.hpaned.winfo_width()
            if h_ratio is not None:
                self._hpaned_ratio = h_ratio
            if h_ratio is not None and total > 200:
                pos = h_sash_for_ratio(total, h_ratio)
                self.hpaned.sashpos(0, pos)
        except tk.TclError:
            pass
        if recv_ratio is not None:
            self.assistant.apply_recv_send_split(recv_ratio)

    def _apply_menu_font(self, fonts):
        """菜单是 tk.Menu（不走 ttk 样式），缩放后要逐个重设字体。"""
        def walk(menu):
            try:
                menu.configure(font=fonts.ui_normal)
            except tk.TclError:
                return
            for child in menu.winfo_children():
                if isinstance(child, tk.Menu):
                    walk(child)
        menubar = getattr(self, "_menubar", None)
        if menubar is not None:
            walk(menubar)

    def _recv_font_step(self, step: int):
        self.assistant.set_recv_font_size(
            self.assistant.get_recv_font_size() + step)
        # 读回实际值：set 会夹到 8~28，不能把越界的请求值存进 ini
        size = self.assistant.get_recv_font_size()
        self.cm.set("ui", "recv_font_size", str(size))
        try:
            self.cm.save()
        except Exception as e:
            self.logger.error(f"保存接收区字号失败: {e}")
        self.logger.info(f"接收区字号: {size}")

    def _recv_font_reset(self):
        self.assistant.set_recv_font_size(10)
        self.cm.set("ui", "recv_font_size", "10")
        try:
            self.cm.save()
        except Exception as e:
            self.logger.error(f"保存接收区字号失败: {e}")
        self.logger.info("接收区字号: 10")

    def _set_running(self, running: bool, serial_opened: bool = False):
        """下载/擦除/回读任务进行状态：同时控制 下载面板 / 擦除回读面板 / 串口面板 的灰化。
        running=True:  所有相关按钮/输入置灰（避免运行中误触发/改配置）
        running=False: 按 serial_opened 决定是否恢复（串口面板按 _opened 恢复）
        """
        self.dl_panel.set_running(running, serial_opened=serial_opened)
        self.erase_read_panel.set_running(running, serial_opened=serial_opened)
        # 串口配置：协议正在用 port/baud/参数，必须锁住；btn_toggle 也一并灰，
        # 避免运行中点"关闭串口"导致 download 与 log 共用的 UartComm 被打断
        self.serial_panel.set_locked(running)

    def _start_erase(self, params: dict):
        self._start_flash_cmd("erase", params, "擦除")

    def _start_read(self, params: dict):
        # XIP/non-XIP 由"回读"按钮当前模式决定（双击切换）：true→flash_xip_read，false→flash_read
        use_xip = bool(params.get("xip"))
        cmd = "flash_xip_read" if use_xip else "flash_read"
        label = f"回读(XIP)" if use_xip else "回读"
        self._start_flash_cmd(cmd, params, label)

    def _open_readback_dir(self):
        """打开回读自动保存目录（与 utils/bin_parse.py BinParse.save 一致）。"""
        readback_dir = get_readback_dir()
        os.makedirs(readback_dir, exist_ok=True)
        self.logger.info(f"打开回读目录: {readback_dir}")
        try:
            if platform.system() == "Windows":
                os.startfile(readback_dir)
            elif platform.system() == "Darwin":
                subprocess.run(["open", readback_dir])
            else:
                subprocess.run(["xdg-open", readback_dir])
        except Exception as e:
            self.logger.error(f"打开回读目录失败: {e}")
            messagebox.showerror("失败", str(e))

    def _start_flash_cmd(self, cmd: str, params: dict, label: str):
        if self._worker is not None and self._worker.is_alive():
            self.logger.warn("已有任务在进行中")
            return
        try:
            addr = int(str(params["addr"]), 0)
            size = int(str(params["size"]), 0)
        except ValueError:
            self.logger.error(f"{label}地址/大小格式错误: addr={params['addr']}, size={params['size']}")
            return
        # 擦除/回读不看 prog 勾选（与调试下载不同）：无论是否勾选，都自动烧 programmer.bin。
        # device 必须先跑 flashload 固件才能响应 FLASH_SELECT/ERASE_*/FLASH_READ。
        chip = self.dl_panel.chip_type.get()
        programmer_info = self._chip_files_for_key(chip, "programmer")
        if programmer_info is None:
            self.logger.error(
                f"{label}需要 programmer.bin：请先在 Bin 文件界面配置 programmer 后再执行")
            messagebox.showerror(
                "缺少 programmer.bin",
                f"{label}依赖 programmer.bin。\n"
                "请先在 Bin 文件界面为当前芯片配置 programmer 后再执行。")
            return

        mgr = self._ensure_manager()
        sp = self.serial_panel
        mgr._log_port = sp.port.get()
        mgr._log_baud = int(sp.log_baud.get())
        mgr._download_port = sp.port.get()
        # 擦除/回读不切波特率：programmer JUMP 后 device 处于 500K，
        # PC 端必须同步开 500K；后续 flashload 命令由 protocol 把 target_baud=0
        # 传过去，跳过 BAUD_CHANGE 步骤。
        mgr._download_baud = 500000
        mgr._comm_type = sp.comm_type.get()
        self._serial_manager = mgr

        self.logger.info(f"开始{label}: addr=0x{addr:x}, size=0x{size:x}")
        # 记录擦除/回读前的 log 状态，结束后按此恢复
        was_log_open = self.serial_panel._opened
        # 把下载面板的"开始下载"置灰（避免误触发），
        # 串口相关控件的灰化保持打开串口时的状态
        self._set_running(True, serial_opened=was_log_open)
        self._worker = threading.Thread(
            target=self._run_flash_cmd,
            args=(mgr, cmd, addr, size, label, programmer_info, was_log_open),
            daemon=True,
        )
        self._worker.start()

    def _run_flash_cmd(self, mgr: "SerialManager", cmd: str, addr: int, size: int,
                       label: str, programmer_info: dict,
                       was_log_open: bool = False):
        """擦除/回读的统一入口：先烧 programmer.bin 让 device 进入 flashload 模式，
        再用 500K + target_baud=0（不切波特率）跑对应 flashload 命令。
        """
        from commands import FlashloadCommand

        try:
            # 步骤 1：先烧 programmer.bin（与 bat 一致：programmer 总是首阶段）
            prog_path = programmer_info["path"]
            prog_addr = programmer_info.get("addr", 0)
            self.logger.info(
                f"=== [programmer] 自动烧录: {os.path.basename(prog_path)} "
                f"-> 0x{prog_addr:08x} ===")
            prog_rc = self._run_programmer(mgr, prog_path, prog_addr)
            if prog_rc != 0:
                self.logger.error(
                    f"programmer 烧录失败 (rc={prog_rc})，中止{label}")
                return
            # 步骤 2：执行擦除/回读，固定 500K + target_baud=0（不切波特率）
            cfg = dict(self.cm.read())
            cfg["comm_type"] = self.serial_panel.comm_type.get()
            cfg["cmd"] = cmd
            cfg["target_baud"] = 0
            cfg["flash_port_num"] = 0
            cfg["sync_exit"] = "false"
            cfg["rdelay"] = 0
            cfg["src_crc_init"] = self.cm.get_hex("crypt_crc", "src_crc_init", 0x1234)
            cfg["src_crc_poly"] = self.cm.get_hex("crypt_crc", "src_crc_poly", 0x3d65)
            cfg["src_crc_len"] = self.cm.get_int("crypt_crc", "src_crc_len", 2)
            cfg["src_crc_ref_in"] = self.cm.get_int("crypt_crc", "src_crc_ref_in", 0)
            cfg["src_crc_ref_out"] = self.cm.get_int("crypt_crc", "src_crc_ref_out", 1)
            cfg["src_crc_xor_out"] = self.cm.get_hex("crypt_crc", "src_crc_xor_out", 0xffff)
            cfg["dst_crc_init"] = self.cm.get_hex("crypt_crc", "dst_crc_init", 0)
            cfg["dst_crc_poly"] = self.cm.get_hex("crypt_crc", "dst_crc_poly", 0x3d65)
            args = argparse.Namespace(
                comm_type=cfg["comm_type"],
                cmd=cmd,
                flashload_bin_path="",
                erase_addr=hex(addr),
                erase_size=hex(size),
                erase_load_addr=hex(addr),
                erase_load_size=hex(size),
                flash_read_addr=hex(addr),
                flash_read_size=hex(size),
                flash_port_num="0",
                target_baud="0",
                sync_exit="false",
                rdelay="0",
                src_crc_init=hex(cfg["src_crc_init"]),
                src_crc_poly=hex(cfg["src_crc_poly"]),
                src_crc_len=str(cfg["src_crc_len"]),
                src_crc_ref_in=str(cfg["src_crc_ref_in"]),
                src_crc_ref_out=str(cfg["src_crc_ref_out"]),
                src_crc_xor_out=hex(cfg["src_crc_xor_out"]),
                dst_crc_init=hex(cfg["dst_crc_init"]),
                dst_crc_poly=hex(cfg["dst_crc_poly"]),
                dry_run=False,
            )
            FlashloadCommand(args, cfg, self._base_logger, serial_manager=mgr).execute()
            self.logger.info(f"=== {label} 完成 ===")
        except Exception as e:
            self.logger.error(f"{label}异常: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
        finally:
            self._base_logger.close_id_file()
            def _finalize():
                self._restore_log_state(was_log_open)
                # 与下载结束一致：擦除/回读结束后（含取消/失败）面板一律恢复可操作，
                # 不因 log 串口仍打开而保持锁定
                self._set_running(False, serial_opened=False)
            self.after(0, _finalize)

    def _run_download(self, mgr: "SerialManager", params: dict, spec: dict,
                      was_log_open: bool = False):
        """按 bat 文件的固定顺序执行下载：每个 bin 阶段对应不同的 erase_load_size、
        baud_phase 与可选的 sync_exit；全部 bin 完成后做系统复位。
        支持中途取消（_cancel_requested 或 comm.is_cancelled()）。
        """
        try:
            chip = params["chip_type"]
            selected_by_key = {b["key"]: b for b in params["selected_bins"]}
            target_baud_cfg = self.cm.get_hex("flashload", "target_baud", 0)
            all_ok = True
            cancelled = False
            # boot 未勾选时，第一个 flashload 仍要从 500K 做 BAUD_CHANGE，
            # 不能直接按 mcu 的 target 阶段去连已经切过的波特率
            saw_flashload = False
            # sync_exit 原先只挂在 mcuc 上。不烧 mcuc 时最后一包不发 SYNC，
            # 设备会退出下载模式，后面的 reset 无法再握手。
            flashload_keys = [
                k for k in DownloadPanel.BINARY_KEYS
                if k in selected_by_key
                and spec.get(k, {}).get("mode", "flashload") != "programmer"
            ]
            last_flashload = flashload_keys[-1] if flashload_keys else None
            want_reset = self.dl_panel.do_reset.get()

            # 按 BINARY_KEYS 顺序逐阶段烧录，与 bat 一致
            for key in DownloadPanel.BINARY_KEYS:
                if self._cancel_requested:
                    cancelled = True
                    break
                if key not in selected_by_key:
                    continue
                bin_info = selected_by_key[key]
                bin_spec = spec.get(key)
                if bin_spec is None:
                    self.logger.warn(f"[{bin_info['label']}] 无下载规格，跳过")
                    continue

                # 阶段波特率策略（对齐 bat）：
                #   baud_phase="initial"（首个 flashload）→ PC 端用 500K 打开，
                #     protocol 的 target_baud = target_baud_cfg（让设备切到目标波特率）
                #   baud_phase="target"（切换后）→ PC 端已用目标波特率打开，
                #     target_baud = 0（不再 BAUD_CHANGE，直接 FLASH_SELECT）
                baud_phase = bin_spec.get("baud_phase", "initial")

                bin_path = bin_info["path"]
                if not os.path.isfile(bin_path):
                    self.logger.error(f"[{bin_info['label']}] bin 不存在: {bin_path}")
                    all_ok = False
                    continue

                # 地址直接读 ini（[chip_xxx] 段）；spec 不参与，避免 0x00 被误以为是 fallback
                addr = bin_info.get("addr", 0)
                mode = bin_spec.get("mode", "flashload")

                if mode == "programmer":
                    self.logger.info(
                        f"=== [{bin_info['label']}] programmer 烧录: "
                        f"{os.path.basename(bin_path)} -> 0x{addr:08x} ===")
                    ok = self._run_programmer(mgr, bin_path, addr)
                else:
                    # 擦除范围由 bin 大小决定：向上对齐 4K，写多大擦多大
                    try:
                        file_size = os.path.getsize(bin_path)
                    except OSError:
                        file_size = 0
                    if file_size == 0:
                        self.logger.error(
                            f"[{bin_info['label']}] bin 为空文件: {bin_path}，跳过该阶段")
                        all_ok = False
                        continue
                    size = align_up_4k(file_size)
                    sync_exit = bool(bin_spec.get("sync_exit", False))
                    if want_reset and key == last_flashload:
                        sync_exit = True
                    if not saw_flashload:
                        baud_phase = "initial"
                    saw_flashload = True
                    if baud_phase == "target":
                        # 切换后的 flashload：device 已切到目标波特率，
                        # PC 必须同步切到此值才能继续通信
                        # （target_baud_cfg=0 表示没有 BAUD_CHANGE 发生，device 仍在 500K，
                        #  此时不能切到 dl_baud 的 3M，否则 PC/device 失同步）
                        if target_baud_cfg > 0:
                            mgr._download_baud = target_baud_cfg
                        else:
                            # 没有 BAUD_CHANGE：device 仍处于 programmer 留下的 500K，
                            # 强制 PC 也用 500K（不能用 dl_baud combobox 的 3M）
                            mgr._download_baud = 500000
                        target_baud_for_cmd = 0
                    else:
                        # 首个 flashload：device 在 programmer JUMP 后仍处于 500K，
                        # PC 端必须以 500K 打开串口与 device 同步（handshake），
                        # 然后由 protocol 通过 BAUD_CHANGE 把 device 切到 target_baud。
                        # 如果 target_baud_cfg=0，则全程 500K，不切。
                        mgr._download_baud = 500000
                        target_baud_for_cmd = target_baud_cfg
                    self.logger.info(
                        f"=== [{bin_info['label']}] flashload 烧录: "
                        f"{os.path.basename(bin_path)} -> 0x{addr:08x}, "
                        f"file=0x{file_size:x}, erase=0x{size:x} "
                        f"[0x{addr:08x}, 0x{addr + size:08x}), sync_exit={sync_exit}, "
                        f"pc_baud={mgr._download_baud}, target_baud={target_baud_for_cmd} ===")
                    ok = self._run_flashload(mgr, bin_path, addr, size, sync_exit,
                                              target_baud=target_baud_for_cmd)
                    if ok == 0 and target_baud_for_cmd > 0:
                        # 这一包已经把设备切到目标波特率，复位必须沿用该波特率
                        mgr._download_baud = target_baud_for_cmd

                # -1 表示协议层收到 cancel；后续 bin 不再执行
                if ok == -1 or self._cancel_requested:
                    cancelled = True
                    self.logger.warn(f"=== [{bin_info['label']}] 已被取消 ===")
                    break
                if ok != 0:
                    # 与 bat 的 if %ERRORLEVEL% EQU 0 一致：任一 bin 失败则中止后续
                    all_ok = False
                    self.logger.error(
                        f"=== [{bin_info['label']}] 失败 (rc={ok})，中止后续 bin ===")
                    break
                self.logger.info(f"=== [{bin_info['label']}] 完成 ===")

            # bat 末尾的 system reset；用户取消时不复位，避免误动作。
            # 是否复位由下载界面 reset 选择框决定，rdelay 取其编辑框值
            # （留空/非法则用芯片规格默认值：9032: 2 / 9062: 2）
            rdelay = spec.get("_reset", {}).get("rdelay", 0)
            if cancelled:
                self.logger.warn("=== 下载已取消，跳过最终复位 ===")
            elif all_ok:
                if self.dl_panel.do_reset.get():
                    rdelay = self.dl_panel.reset_rdelay_value(rdelay)
                    self.logger.info(f"=== 系统复位 (rdelay={rdelay}) ===")
                    self._run_reset(mgr, rdelay)
                else:
                    self.logger.info("=== 未勾选 reset，跳过系统复位 ===")
                self.logger.info("=== 全部下载完成 ===")
            else:
                self.logger.warn("=== 存在失败步骤，跳过最终复位 ===")
        except Exception as e:
            self.logger.error(f"下载异常: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
        finally:
            self._base_logger.close_id_file()
            # 先恢复 log 串口（按 was_log_open），再解除任务灰化。
            # 下载结束（含成功/失败/取消/协议超时退出）后擦除回读面板一律恢复可操作，
            # 不因 log 串口仍打开而保持锁定。
            def _finalize():
                self._restore_log_state(was_log_open)
                self._set_running(False, serial_opened=False)
            self.after(0, _finalize)

    def _restore_log_state(self, was_log_open: bool):
        """下载结束后按下载前的 log 状态恢复：开则重开，关则保持关。"""
        if was_log_open:
            self._open_serial()
        else:
            self.serial_panel.mark_closed()

    def _run_programmer(self, mgr: "SerialManager", bin_path: str, addr: int) -> int:
        from commands import ProgrammerCommand

        cfg = dict(self.cm.read())
        cfg["comm_type"] = self.serial_panel.comm_type.get()
        cfg["programmer_bin_path"] = bin_path
        cfg["programmer_bin_addr"] = addr
        args = argparse.Namespace(
            comm_type=cfg["comm_type"],
            programmer_bin_path=bin_path,
            programmer_bin_addr=hex(addr),
            unlock=False, dry_run=False,
        )
        # 固定 500K（与 bat 中 programmer 阶段一致：不开 -uart_baud，
        # PC 端按 500K 打开串口与处于 bootloader 的 device 通讯）
        saved_baud = mgr._download_baud
        mgr._download_baud = 500000
        try:
            cmd = ProgrammerCommand(args, cfg, self._base_logger, serial_manager=mgr)
            rc = cmd.execute()
        finally:
            mgr._download_baud = saved_baud
        # programmer 跳转到 SRAM 后，device 需要时间初始化 flashload 固件
        # bat 中两次调 download_tools.exe 之间由 OS 启动延迟兜底，Python 内手动补
        if rc == 0:
            time.sleep(0.3)
            self.logger.info("等待 device 进入 flashload 模式 (300ms)")
        return rc

    def _run_flashload(self, mgr: "SerialManager", bin_path: str, addr: int,
                       size: int = 0, sync_exit: bool = False,
                       target_baud: int = 0) -> int:
        """size = 擦除大小（调用方按 bin 文件大小向上对齐 4K 算出）。"""
        from commands import FlashloadCommand

        cfg = dict(self.cm.read())
        cfg["comm_type"] = self.serial_panel.comm_type.get()
        cfg["cmd"] = "erase_load"
        cfg["flashload_bin_path"] = bin_path
        cfg["erase_load_addr"] = addr
        cfg["erase_load_size"] = size
        cfg["target_baud"] = target_baud
        erase_addr = self.cm.get_hex("flashload", "erase_addr", addr)
        erase_size = self.cm.get_hex("flashload", "erase_size", 0x100000)
        args = argparse.Namespace(
            comm_type=cfg["comm_type"],
            cmd="erase_load",
            flashload_bin_path=bin_path,
            erase_addr=hex(erase_addr),
            erase_size=hex(erase_size),
            erase_load_addr=hex(addr),
            erase_load_size=hex(size),
            flash_read_addr="0x0",
            flash_read_size="0x100000",
            flash_port_num="0",
            target_baud=str(target_baud),
            sync_exit=str(sync_exit).lower(),
            rdelay="0",
            src_crc_init=hex(self.cm.get_hex("crypt_crc", "src_crc_init", 0x1234)),
            src_crc_poly=hex(self.cm.get_hex("crypt_crc", "src_crc_poly", 0x3d65)),
            src_crc_len=str(self.cm.get_int("crypt_crc", "src_crc_len", 2)),
            src_crc_ref_in=str(self.cm.get_int("crypt_crc", "src_crc_ref_in", 0)),
            src_crc_ref_out=str(self.cm.get_int("crypt_crc", "src_crc_ref_out", 1)),
            src_crc_xor_out=hex(self.cm.get_hex("crypt_crc", "src_crc_xor_out", 0xffff)),
            dst_crc_init=hex(self.cm.get_hex("crypt_crc", "dst_crc_init", 0)),
            dst_crc_poly=hex(self.cm.get_hex("crypt_crc", "dst_crc_poly", 0x3d65)),
            dry_run=False,
        )
        cmd = FlashloadCommand(args, cfg, self._base_logger, serial_manager=mgr)
        return cmd.execute()

    def _run_reset(self, mgr: "SerialManager", rdelay: int = 0) -> int:
        from commands import FlashloadCommand

        cfg = dict(self.cm.read())
        cfg["comm_type"] = self.serial_panel.comm_type.get()
        cfg["cmd"] = "reset"
        cfg["rdelay"] = rdelay
        args = argparse.Namespace(
            comm_type=cfg["comm_type"],
            cmd="reset",
            flashload_bin_path="",
            erase_addr="0x0",
            erase_size="0x0",
            erase_load_addr="0x0",
            erase_load_size="0x0",
            flash_read_addr="0x0",
            flash_read_size="0x0",
            flash_port_num="0",
            target_baud="0",
            sync_exit="false",
            rdelay=str(rdelay),
            src_crc_init="0x1234",
            src_crc_poly="0x3d65",
            src_crc_len="2",
            src_crc_ref_in="0",
            src_crc_ref_out="1",
            src_crc_xor_out="0xffff",
            dst_crc_init="0",
            dst_crc_poly="0x3d65",
            dry_run=False,
        )
        cmd = FlashloadCommand(args, cfg, self._base_logger, serial_manager=mgr)
        return cmd.execute()

    @property
    def logger(self) -> Logger:
        return self._base_logger

    def _on_close(self):
        self._collect_ui_to_cfg()
        try:
            self.cm.save()
        except Exception as e:
            print(f"[EpmDlApp] save config failed: {e}")
        self._flush_live_log_queue()
        self.assistant.stop()
        if self._serial_manager is not None:
            self._serial_manager.close_log()
        self._base_logger.close_id_file()
        self.destroy()
