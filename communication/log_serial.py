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
        # 开串口后丢弃首段半截流，直到行/帧边界（避免首行乱码）
        self._need_resync = False
        self._resync_deadline_ms = 0

    def set_flush_timeout_ms(self, ms: int):
        """运行中调整 flush 超时；0 表示禁用。"""
        self._flush_timeout_ms = max(0, int(ms))

    def open(self) -> bool:
        if not super().open():
            return False
        self._stop_flag.clear()
        now_ms = time.monotonic_ns() // 1_000_000
        with self._lock:
            self._buf.clear()
            self._last_rx_ms = now_ms
            # 设备若正在狂吐，reset 后仍可能从半行/半帧切入；先对齐边界
            self._need_resync = True
            self._resync_deadline_ms = now_ms + 300
        self._rx_thread = threading.Thread(
            target=self._reader_loop, name=f"LogSerialRx[{self.port}]", daemon=True,
        )
        self._rx_thread.start()
        return True

    def close(self):
        """先停 RX，再吐出完整行；半截行丢弃，避免关口时出现「th」/乱码假行。"""
        self._stop_flag.set()
        if self._rx_thread is not None:
            # recv(timeout_ms=50) 最长约 50ms 返回，给足余量即可，勿等到 1s
            self._rx_thread.join(timeout=0.3)
            self._rx_thread = None
        # 线程已停：捞一次驱动缓冲里残留字节，只推送完整行
        try:
            leftover = super().recv(timeout_ms=0)
        except Exception:
            leftover = b""
        with self._lock:
            if leftover:
                self._buf.extend(leftover)
            lines, self._buf = self._split_lines(self._buf)
            # 半截行（无 \\r/\\n）丢弃：强行 flush 会变成独立假行或把二进制当文本
            self._buf.clear()
        for line in lines:
            self._emit(line)
        super().close()

    def _reader_loop(self):
        while not self._stop_flag.is_set():
            data = super().recv(timeout_ms=50)
            if self._stop_flag.is_set():
                # 关口中：本轮读到的字节留给 close() 统一切完整行
                if data:
                    with self._lock:
                        self._buf.extend(data)
                break
            if data:
                now_ms = time.monotonic_ns() // 1_000_000
                with self._lock:
                    self._buf.extend(data)
                    self._last_rx_ms = now_ms
                    self._resync_locked(now_ms)
                    if self._need_resync:
                        continue
                    lines, self._buf = self._split_lines(self._buf)
                for line in lines:
                    self._emit(line)
            else:
                self._maybe_flush_by_timeout()

    def _resync_locked(self, now_ms: int):
        """开串口后对齐到首个完整行或 ID 帧 SYNC；超时则放弃，避免一直丢数据。

        必须持有 self._lock。
        """
        if not self._need_resync:
            return
        buf = self._buf
        if not buf:
            if now_ms >= self._resync_deadline_ms:
                self._need_resync = False
            return
        # 1) 文本流：丢到第一个 \\n（含），半截首行不显示
        idx_n = buf.find(b"\n")
        if idx_n >= 0:
            del buf[: idx_n + 1]
            self._need_resync = False
            return
        # 2) ID 帧：对齐到 0x8E 0xA5，丢掉 SYNC 前的碎片（常被解成乱码）
        for i in range(len(buf) - 1):
            if buf[i] == 0x8E and buf[i + 1] == 0xA5:
                if i > 0:
                    del buf[:i]
                self._need_resync = False
                return
        # 3) 超时仍无边界：丢掉已缓冲碎片，开始正常收（总比假乱码首行好）
        if now_ms >= self._resync_deadline_ms:
            buf.clear()
            self._need_resync = False

    def _maybe_flush_by_timeout(self):
        """距离上次收到字节已经超过 flush_timeout_ms 时，把 _buf 当作一个 chunk 输出。"""
        if self._stop_flag.is_set() or self._flush_timeout_ms <= 0:
            return
        now_ms = time.monotonic_ns() // 1_000_000
        chunk = None
        with self._lock:
            if self._need_resync:
                self._resync_locked(now_ms)
                return
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
