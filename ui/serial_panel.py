import re
import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Callable, List, Optional, Tuple

from .theme import get_ui_fonts


def port_display_label(device: str, description: str = "") -> str:
    """端口下拉显示：``COM5 USB Serial Port``。描述里重复的 ``(COMx)`` 去掉。"""
    device = (device or "").strip()
    desc = (description or "").strip()
    if device and desc:
        desc = re.sub(
            rf"\s*\({re.escape(device)}\)\s*$",
            "",
            desc,
            flags=re.IGNORECASE,
        ).strip()
    if not device:
        return desc
    if not desc or desc.lower() in ("n/a", device.lower()):
        return device
    return f"{device} {desc}"


def list_serial_ports() -> List[Tuple[str, str]]:
    """返回 ``(端口号, 显示文字)``，显示文字含设备描述。"""
    try:
        from serial.tools import list_ports
        items = [
            (p.device, port_display_label(p.device, getattr(p, "description", "") or ""))
            for p in list_ports.comports()
            if getattr(p, "device", None)
        ]
    except Exception:
        return []

    def _sort_key(item: Tuple[str, str]):
        device = item[0]
        m = re.search(r"(\d+)$", device)
        if m:
            return (device[:m.start()].upper(), int(m.group(1)))
        return (device.upper(), 0)

    items.sort(key=_sort_key)
    return items


def fit_popdown_span(
    anchor_x: int, want: int, mon_left: int, mon_right: int, margin: int = 4,
) -> Tuple[int, int]:
    """下拉左缘对齐端口；放不下时只在同一块屏幕内左移，不跨到别的屏幕。"""
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


def _monitor_span(widget: tk.Misc) -> Optional[Tuple[int, int]]:
    """端口所在显示器的左右边界。坐标系对不上时返回 None，避免把下拉拽走。"""
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
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
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


BAUD_RATES = [
    "1200", "2400", "4800", "9600", "19200", "38400",
    "57600", "115200", "230400", "460800", "500000", "921600", "1500000", "3000000",
]

DATA_BITS = ["5", "6", "7", "8"]
PARITY = ["none", "even", "odd", "mark", "space"]
STOP_BITS = ["1", "1.5", "2"]


class SerialPanel(ttk.LabelFrame):
    def __init__(self, master,
                 on_open: Optional[Callable[[], bool]] = None,
                 on_close: Optional[Callable[[], None]] = None,
                 on_open_params: Optional[Callable[[], None]] = None):
        super().__init__(master, text="串口配置")
        self._on_open = on_open
        self._on_close = on_close
        self._on_open_params = on_open_params
        self._opened = False

        self.port = tk.StringVar()
        # 下拉框显示「COM5 USB Serial Port」；self.port 只保留端口号供打开/保存
        self.port_label = tk.StringVar()
        self._port_by_label = {}
        self.log_baud = tk.StringVar(value="500000")
        self.dl_baud = tk.StringVar(value="3000000")
        self.comm_type = tk.StringVar(value="uart")
        self.data_bits = tk.StringVar(value="8")
        self.parity = tk.StringVar(value="none")
        self.stop_bits = tk.StringVar(value="1")

        self.port.set("COM5")
        self.port_label.set("COM5")
        self._port_by_label = {"COM5": "COM5"}

        _label_padx = 2
        self.columnconfigure(1, weight=1)

        ttk.Label(self, text="端口").grid(
            row=0, column=0, sticky=tk.W, padx=_label_padx, pady=2)
        self.cmb_port = ttk.Combobox(
            self, textvariable=self.port_label, values=["COM5"],
            width=22, state="readonly")
        self.cmb_port.grid(row=0, column=1, sticky=tk.EW, padx=(4, 8), pady=2)
        self.cmb_port.configure(postcommand=self._schedule_port_popdown)
        self.cmb_port.bind("<<ComboboxSelected>>", self._on_port_selected)

        ttk.Label(self, text="日志").grid(
            row=1, column=0, sticky=tk.W, padx=_label_padx, pady=2)
        self.cmb_log = ttk.Combobox(self, textvariable=self.log_baud, values=BAUD_RATES,
                                    width=12, state="readonly")
        self.cmb_log.grid(row=1, column=1, sticky=tk.EW, padx=(4, 8), pady=2)

        ttk.Label(self, text="下载").grid(
            row=2, column=0, sticky=tk.W, padx=_label_padx, pady=2)
        self.cmb_dl = ttk.Combobox(self, textvariable=self.dl_baud, values=BAUD_RATES,
                                   width=12, state="readonly")
        self.cmb_dl.grid(row=2, column=1, sticky=tk.EW, padx=(4, 8), pady=2)
        for combo in (self.cmb_port, self.cmb_log, self.cmb_dl):
            self._apply_field_font(combo)

        self.btn_toggle = ttk.Button(self, text="打开串口", command=self._on_toggle, width=8)
        self.btn_toggle.grid(row=0, column=2, sticky=tk.W, padx=(8, 2), pady=2)
        self.btn_refresh = ttk.Button(self, text="刷新端口", command=self.refresh_ports, width=8)
        self.btn_refresh.grid(row=1, column=2, sticky=tk.W, padx=(8, 2), pady=2)
        self.btn_params = ttk.Button(self, text="串口参数", command=self._open_params, width=8)
        self.btn_params.grid(row=2, column=2, sticky=tk.W, padx=(8, 2), pady=2)

        # 主面板显示当前参数摘要（8-N-1），弹窗仅用于修改
        self.params_summary = tk.StringVar(value=self._summarize())
        self.status = tk.StringVar(value="已关闭")
        ttk.Label(self, textvariable=self.status, foreground="#888").grid(
            row=3, column=0, columnspan=2, sticky=tk.W, padx=_label_padx, pady=2)
        ttk.Label(self, textvariable=self.params_summary, foreground="#888").grid(
            row=3, column=2, sticky=tk.E, padx=_label_padx, pady=2)

    def _apply_field_font(self, combo: ttk.Combobox):
        """框内文字与下拉列表都用小一号，和端口框一致。"""
        font = get_ui_fonts().ui_small
        try:
            combo.configure(font=font)
        except tk.TclError:
            return
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
            combo.tk.call(f"{popdown}.f.l", "configure", "-font", font)
        except tk.TclError:
            pass

    def refresh_ports(self):
        ports = list_serial_ports()
        cur = self.port.get().strip()
        if not ports:
            if cur:
                ports = [(cur, cur)]
            else:
                return
        labels = []
        mapping = {}
        for device, label in ports:
            show = label
            if show in mapping and mapping[show] != device:
                show = f"{label} ({device})"
            mapping[show] = device
            labels.append(show)
        self._port_by_label = mapping
        self.cmb_port["values"] = labels
        chosen = next((label for label, device in mapping.items() if device == cur), None)
        if chosen is None:
            chosen = labels[0]
        self.port.set(mapping[chosen])
        # 只读下拉要 set 一次，Windows 才会把当前项画出来
        self.cmb_port.set(chosen)

    def _schedule_port_popdown(self):
        """弹出后按最长显示文字加宽。ttk 会先把下拉压成和输入框一样宽。"""
        self.cmb_port.after(1, self._fit_port_popdown)

    def _fit_port_popdown(self):
        combo = self.cmb_port
        try:
            popdown = combo.tk.call("ttk::combobox::PopdownWindow", combo)
            if not combo.tk.getboolean(combo.tk.call("winfo", "ismapped", popdown)):
                return
        except tk.TclError:
            return
        labels = [str(item) for item in combo.cget("values")]
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
        span = _monitor_span(combo)
        if span is None:
            # 坐标不可靠时只加宽，保留 ttk 已经对齐到端口的位置
            combo.tk.call("wm", "geometry", popdown, f"{want}x{height}{origin}")
            return
        x, width = fit_popdown_span(combo.winfo_rootx(), want, span[0], span[1])
        pos_y = re.search(r"[+-]\d+$", origin)
        if not pos_y:
            return
        combo.tk.call(
            "wm", "geometry", popdown, f"{width}x{height}{x:+d}{pos_y.group(0)}")

    def _on_port_selected(self, _event=None):
        device = self._port_by_label.get(self.cmb_port.get())
        if device:
            self.port.set(device)

    def _on_toggle(self):
        if self._opened:
            if self._on_close:
                self._on_close()
            self._set_state(False)
        else:
            ok = True
            if self._on_open:
                try:
                    ret = self._on_open()
                    ok = bool(ret) if ret is not None else True
                except Exception:
                    ok = False
            if ok:
                self._set_state(True)
            else:
                self._set_state(False)

    def _set_state(self, opened: bool):
        self._opened = opened
        if opened:
            self.status.set(f"已打开: {self.port.get()}")
            self.btn_toggle.configure(text="关闭串口", state=tk.NORMAL)
            # 串口打开期间，端口/波特率/串口参数/刷新端口 全部锁定（避免改动后状态不一致）
            self.cmb_port.configure(state="disabled")
            self.cmb_log.configure(state="disabled")
            self.cmb_dl.configure(state="disabled")
            self.btn_params.configure(state="disabled")
            self.btn_refresh.configure(state="disabled")
        else:
            self.status.set("已关闭")
            self.btn_toggle.configure(text="打开串口", state=tk.NORMAL)
            self.cmb_port.configure(state="readonly")
            self.cmb_log.configure(state="readonly")
            self.cmb_dl.configure(state="readonly")
            self.btn_params.configure(state="normal")
            self.btn_refresh.configure(state="normal")

    def mark_closed(self):
        self._set_state(False)

    def set_locked(self, locked: bool):
        """下载/擦除/回读任务进行中：禁用串口配置相关控件（含打开/关闭按钮）。
        不修改 _opened / status / btn_toggle 文字，仅切换 disabled 状态。
        locked=False 时按当前 _opened 状态完整恢复（等同 _set_state(self._opened)）。
        """
        if locked:
            for w in (self.cmb_port, self.cmb_log, self.cmb_dl,
                      self.btn_params, self.btn_refresh, self.btn_toggle):
                w.configure(state="disabled")
        else:
            # 复用 _set_state：按当前 _opened 决定是 readonly 还是 disabled
            self._set_state(self._opened)

    def _open_params(self):
        if self._on_open_params:
            self._on_open_params()
        else:
            SerialParamDialog(self, self)

    def _summarize(self) -> str:
        return f"{self.data_bits.get()}-{self.parity.get()[0].upper()}-{self.stop_bits.get()}"

    def collect(self) -> dict:
        return {
            "port": self.port.get(),
            "log_baud": self.log_baud.get(),
            "download_port": self.port.get(),
            "download_baud": self.dl_baud.get(),
            "data_bits": self.data_bits.get(),
            "parity": self.parity.get(),
            "stop_bits": self.stop_bits.get(),
            "comm_type": self.comm_type.get(),
        }


class SerialParamDialog:
    def __init__(self, parent, panel: SerialPanel):
        self.panel = panel
        self.top = tk.Toplevel(parent)
        self.top.title("串口参数")
        self.top.transient(parent)
        self.top.resizable(False, False)

        frm = ttk.Frame(self.top, padding=12)
        frm.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frm, text="数据位:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self.data_bits = tk.StringVar(value=panel.data_bits.get())
        ttk.Combobox(frm, textvariable=self.data_bits, values=DATA_BITS,
                     width=10, state="readonly").grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(frm, text="校验位:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        self.parity = tk.StringVar(value=panel.parity.get())
        ttk.Combobox(frm, textvariable=self.parity, values=PARITY,
                     width=10, state="readonly").grid(row=1, column=1, padx=4, pady=4)

        ttk.Label(frm, text="停止位:").grid(row=2, column=0, sticky=tk.W, padx=4, pady=4)
        self.stop_bits = tk.StringVar(value=panel.stop_bits.get())
        ttk.Combobox(frm, textvariable=self.stop_bits, values=STOP_BITS,
                     width=10, state="readonly").grid(row=2, column=1, padx=4, pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=3, column=0, columnspan=2, pady=12)
        ttk.Button(btns, text="确定", command=self._on_ok).pack(side=tk.LEFT, padx=8)
        ttk.Button(btns, text="取消", command=self.top.destroy).pack(side=tk.LEFT, padx=8)

        self.top.bind("<Return>", lambda _e: self._on_ok())
        self.top.bind("<Escape>", lambda _e: self.top.destroy())

        # 先定位再 grab：确保弹窗显示在主窗口所在屏幕上，而不是由 WM 丢到别的屏幕
        self._center_over(parent)
        self.top.grab_set()
        self.top.focus_set()

    def _center_over(self, parent):
        """把弹窗居中到主窗口上（跟随主窗口所在显示器）。"""
        self.top.update_idletasks()
        w = self.top.winfo_reqwidth()
        h = self.top.winfo_reqheight()
        px = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        py = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 2)
        self.top.geometry(f"+{px}+{py}")

    def _on_ok(self):
        try:
            self.panel.data_bits.set(self.data_bits.get())
            self.panel.parity.set(self.parity.get())
            self.panel.stop_bits.set(self.stop_bits.get())
            self.panel.params_summary.set(self.panel._summarize())
        finally:
            # 无论是否异常都释放 grab 并销毁弹窗，避免 grab 卡死整个程序
            try:
                self.top.grab_release()
            except Exception:
                pass
            self.top.destroy()
