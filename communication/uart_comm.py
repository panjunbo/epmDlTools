from __future__ import annotations

import time
from typing import Optional

try:
    import serial
except ImportError as e:
    raise ImportError(
        "缺少 pyserial 依赖，请先 `pip install pyserial`"
    ) from e

from .interface import CommunicationInterface


class UartComm(CommunicationInterface):
    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 1.0):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None
        self._opened = False
        self.last_error: str = ""

    def open(self) -> bool:
        if self._opened and self._serial is not None:
            return True
        try:
            # timeout=0 让 serial 永久非阻塞：recv() 完全靠 in_waiting 主动轮询，
            # 省掉每次 SetCommTimeouts 的 Win32 调用。protocol tight polling
            # 一帧内调 recv() 2 次，每秒几千 tick，省下的累计时间就是几秒。
            self._serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                bytesize=serial.EIGHTBITS,
                timeout=0,
            )
            # pyserial 在 Windows 下 close→open 切换波特率时偶发 driver 缓存旧 DCB，
            # 显式触发一次 setter 再调一次 SetCommState 确保覆盖
            self._serial.baudrate = self.baudrate
            try:
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
            except Exception:
                pass
            self._opened = True
            self.last_error = ""
            return True
        except serial.SerialException as e:
            self._serial = None
            self._opened = False
            self.last_error = str(e)
            print(f"[UartComm] 打开 {self.port} 失败: {e}")
            return False

    def close(self):
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
        self._serial = None
        self._opened = False
        # Windows 驱动释放 COM 端口需要时间，立即 open 可能拿到旧 DCB
        time.sleep(0.05)

    def send(self, data: bytes) -> int:
        if not self._opened or self._serial is None:
            return 0
        return self._serial.write(data)

    def recv(self, timeout_ms: int = 0) -> bytes:
        """读取已到达的串口数据。

        默认 timeout_ms=0（非阻塞）：仅靠 in_waiting 检查 + read，不触发任何
        SetCommTimeouts Win32 调用。protocol tight polling 在每帧内调用本函数
        两次，省下的累计时间在大文件烧录下很明显（之前每次 recv 都要
        gettimeout/settimeout/settimeout 3 次 Win32 调用）。

        timeout_ms > 0：阻塞路径（极少数用例：log 线程等待一行）。
        """
        if not self._opened or self._serial is None:
            return b""
        if timeout_ms <= 0:
            # 快路径：直接看 in_waiting
            n = self._serial.in_waiting
            if n > 0:
                return bytes(self._serial.read(n))
            return b""
        # 慢路径：临时切到阻塞模式，读一次后恢复非阻塞
        self._serial.timeout = timeout_ms / 1000.0
        try:
            n = self._serial.in_waiting
            if n > 0:
                return bytes(self._serial.read(n))
            # in_waiting 为 0 时做一次短阻塞 read，等首个字节到达
            return bytes(self._serial.read(1))
        finally:
            self._serial.timeout = 0

    def flush(self):
        if not self._opened or self._serial is None:
            return
        try:
            self._serial.flush()
        except Exception:
            pass
        # 不加 sleep：flush() 本身已保证 output buffer 排空，
        # 加 sleep 是早期版本应对 Windows 驱动 race 的兜底，目前协议只在
        # BAUD_CHANGE 后调一次 flush()，多睡 5ms 没有收益反而拖慢切波特率。

    @property
    def in_waiting(self) -> int:
        if not self._opened or self._serial is None:
            return 0
        try:
            return self._serial.in_waiting
        except Exception:
            return 0
