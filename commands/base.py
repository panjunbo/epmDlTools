from __future__ import annotations

from abc import ABC, abstractmethod
import time
from contextlib import contextmanager
from typing import Optional

from communication import LogSerial, create
from communication.interface import CommunicationInterface
from utils.logger import Logger


class SerialManager:
    def __init__(self, log_port: str, log_baud: int,
                 download_port: str, download_baud: int,
                 comm_type: str = "uart",
                 on_log_line=None,
                 flush_timeout_ms: int = 100):
        self._log_port = log_port
        self._log_baud = log_baud
        self._download_port = download_port
        self._download_baud = download_baud
        self._comm_type = comm_type
        self.log_serial: Optional[LogSerial] = None
        self.download_serial: Optional[CommunicationInterface] = None
        self._on_log_line = on_log_line
        self._flush_timeout_ms = max(0, int(flush_timeout_ms))

    def set_flush_timeout_ms(self, ms: int):
        """运行中调整 log 串口的 flush 超时（0 = 禁用）。"""
        ms = max(0, int(ms))
        self._flush_timeout_ms = ms
        if self.log_serial is not None:
            try:
                self.log_serial.set_flush_timeout_ms(ms)
            except Exception:
                pass

    def open_log(self) -> bool:
        need_new = (
            self.log_serial is None
            or self.log_serial.port != self._log_port
            or self.log_serial.baudrate != self._log_baud
        )
        if need_new and self.log_serial is not None:
            try:
                self.log_serial.close()
            except Exception:
                pass
            self.log_serial = None
        if self.log_serial is None:
            self.log_serial = LogSerial(
                self._log_port, self._log_baud,
                on_line=self._on_log_line,
                flush_timeout_ms=self._flush_timeout_ms,
            )
        else:
            try:
                self.log_serial.set_flush_timeout_ms(self._flush_timeout_ms)
            except Exception:
                pass
        ok = self.log_serial.open()
        if not ok:
            try:
                self.log_serial.close()
            except Exception:
                pass
            self.log_serial = None
        return ok

    def close_log(self):
        if self.log_serial is not None:
            try:
                self.log_serial.close()
            except Exception:
                pass
            self.log_serial = None

    @contextmanager
    def enter_download(self):
        self.close_log()
        self.download_serial = create(
            self._comm_type, self._download_port, self._download_baud,
        )
        self.download_serial.reset_cancel()
        ok = self.download_serial.open()
        if not ok:
            detail = getattr(self.download_serial, "last_error", "")
            self.download_serial = None
            hint = ""
            if any(x in detail for x in ("PermissionError", "拒绝访问", "Access is denied")):
                hint = "（串口可能被占用，请先关闭 GUI 串口或其它程序）"
            raise RuntimeError(
                f"打开下载串口失败: {self._download_port}{hint}")
        try:
            yield self.download_serial
        finally:
            try:
                self.download_serial.close()
            except Exception:
                pass
            self.download_serial = None
            # 不在这里自动 open_log；log 串口恢复由上层 _restore_log_state 决定
            # 否则用户没开 log 时也会被自动打开，导致 SerialAssistant 显示 device 数据

    def cancel(self):
        """转发取消事件给当前下载通道（若有）；也清日志是为了让日志线程退出阻塞 recv。"""
        if self.download_serial is not None:
            self.download_serial.cancel()
        if self.log_serial is not None:
            self.log_serial.cancel()

class Command(ABC):
    name: str = "command"

    def __init__(self, args, cfg: dict, logger: Logger,
                 serial_manager: Optional[SerialManager] = None):
        self.args = args
        self.cfg = cfg
        self.logger = logger
        self.serial_manager = serial_manager

    @abstractmethod
    def execute(self) -> int:
        pass

    @contextmanager
    def download_mode(self):
        if self.serial_manager is None:
            raise RuntimeError("SerialManager 未配置")
        with self.serial_manager.enter_download() as comm:
            yield comm

    @contextmanager
    def local_download_mode(self, comm_type: str = "uart",
                            port: str = "COM5", baudrate: int = 500000):
        """CLI 用：直接通过 communication.create() 打开下载串口，不走 SerialManager，
        也不开 log 串口。串口打开失败抛 RuntimeError。

        与 download_mode() 的区别：
        - 没有 log_serial 概念（CLI 不开日志）
        - 不切换 log / download 状态机
        """
        comm = create(comm_type, port, baudrate)
        comm.reset_cancel()
        ok = comm.open()
        if not ok:
            detail = getattr(comm, "last_error", "")
            hint = ""
            if any(x in detail for x in ("PermissionError", "拒绝访问", "Access is denied")):
                hint = "（串口可能被占用，请先关闭 GUI 串口或其它程序）"
            elif any(x in detail for x in ("FileNotFoundError", "找不到", "cannot find")):
                hint = "（端口不存在，请检查 COM 号或设备连接）"
            raise RuntimeError(f"打开下载串口失败: {port} @ {baudrate}{hint}")
        # CLI 冷启动：对齐 UI 先 close log 再 open download 的端口释放窗口
        time.sleep(0.05)
        try:
            yield comm
        finally:
            try:
                comm.close()
            except Exception:
                pass

    def _enter_comm(self):
        """comm 上下文管理器选择器：
        - UI 路径（serial_manager 已注入）：走 SerialManager，保持原行为
        - CLI 路径（serial_manager=None）：走 local_download_mode，按 args 拿串口参数

        调用方：`with self._enter_comm() as comm: ...`
        """
        if self.serial_manager is not None:
            return self.download_mode()
        a = self.args
        return self.local_download_mode(
            comm_type=getattr(a, "comm_type", "uart"),
            port=getattr(a, "uart_port", "COM5"),
            baudrate=int(getattr(a, "uart_baud", "500000")),
        )
