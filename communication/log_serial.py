import threading
import time
from typing import Callable, List, Optional

from .uart_comm import UartComm


class LogSerial(UartComm):
    def __init__(self, port: str, baudrate: int = 115200,
                 on_line: Optional[Callable[[bytes], None]] = None,
                 flush_timeout_ms: int = 100):
        """LogSerial:按行切日志串口。

        flush_timeout_ms:接收缓存里在多少毫秒内没有新字节就当作一个 chunk
        强制刷出去。0 表示禁用（只按 \\r/\\n 切行）。
        """
        super().__init__(port=port, baudrate=baudrate)
        self._on_line = on_line
        self._rx_thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()
        self._buf = bytearray()
        self._lock = threading.Lock()
        # 0 = 不启用；>0 = 多少 ms 没有新字节就强制 flush _buf
        self._flush_timeout_ms = max(0, int(flush_timeout_ms))
        self._last_rx_ms = 0  # 上次收到字节的 monotonic ms

    def set_flush_timeout_ms(self, ms: int):
        """运行中调整 flush 超时；0 表示禁用。"""
        self._flush_timeout_ms = max(0, int(ms))

    def open(self) -> bool:
        if not super().open():
            return False
        self._stop_flag.clear()
        with self._lock:
            self._buf.clear()
            self._last_rx_ms = time.monotonic_ns() // 1_000_000
        self._rx_thread = threading.Thread(
            target=self._reader_loop, name=f"LogSerialRx[{self.port}]", daemon=True,
        )
        self._rx_thread.start()
        return True

    def close(self):
        self._stop_flag.set()
        if self._rx_thread is not None:
            self._rx_thread.join(timeout=1.0)
            self._rx_thread = None
        # 退出前把残留的 buf 一次性 flush 出去，避免日志丢尾段
        self._flush_remaining()
        super().close()

    def _reader_loop(self):
        while not self._stop_flag.is_set():
            data = super().recv(timeout_ms=50)
            if data:
                now_ms = time.monotonic_ns() // 1_000_000
                with self._lock:
                    self._buf.extend(data)
                    self._last_rx_ms = now_ms
                    lines, self._buf = self._split_lines(self._buf)
                for line in lines:
                    self._emit(line)
                # 切完行后若还剩 buf，下一轮 recv 后再做 flush 判断
            else:
                # 没有新字节：检查是否需要强制 flush（超时）
                self._maybe_flush_by_timeout()

    def _maybe_flush_by_timeout(self):
        """距离上次收到字节已经超过 flush_timeout_ms 时，把 _buf 当作一个 chunk 输出。"""
        if self._flush_timeout_ms <= 0:
            return
        now_ms = time.monotonic_ns() // 1_000_000
        chunk = None
        with self._lock:
            if not self._buf:
                return
            if (now_ms - self._last_rx_ms) < self._flush_timeout_ms:
                return
            chunk = bytes(self._buf)
            self._buf.clear()
            # 重置时间戳，避免下次空 recv 又立刻 flush（虽然 buf 空也无所谓）
            self._last_rx_ms = now_ms
        if chunk:
            self._emit(chunk)

    def _flush_remaining(self):
        """close 时把残留 _buf 当 chunk 推出去；handle closed, callbacks 可能已清空."""
        with self._lock:
            if not self._buf:
                return
            chunk = bytes(self._buf)
            self._buf.clear()
        if chunk:
            self._emit(chunk)

    def _emit(self, line: bytes):
        if self._on_line:
            try:
                self._on_line(line)
            except Exception:
                pass

    @staticmethod
    def _split_lines(buf: bytearray):
        """按 \\r/\\n 切行。行内容包含结尾分隔符——ID log 二进制帧的
        payload 里可能含 0x0A/0x0D，分隔符字节必须原样保留在数据流里，
        否则帧会被抠掉字节导致 CRC 失败。显示端 feed_line 会 rstrip。
        """
        out: List[bytes] = []
        while buf:
            idx_n = buf.find(b"\n")
            idx_r = buf.find(b"\r")
            candidates = [i for i in (idx_n, idx_r) if i >= 0]
            if not candidates:
                break
            idx = min(candidates)
            if idx == idx_r and idx == len(buf) - 1:
                break
            if idx == idx_r and idx + 1 < len(buf) and buf[idx + 1: idx + 2] == b"\n":
                line = bytes(buf[: idx + 2])
                del buf[: idx + 2]
            else:
                line = bytes(buf[: idx + 1])
                del buf[: idx + 1]
            if line:
                out.append(line)
        return out, buf
