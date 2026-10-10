import time
from enum import Enum
from typing import Optional

from communication.interface import CommunicationInterface

from utils.bin_parse import BinParse
from utils.logger import Logger

from .frame import (
    FrameParser, MsgIdProgrammer, RxFrame, TxFrame, pack,
)


class SizeVal(Enum):
    SIZE_128 = 128
    SIZE_256 = 256
    SIZE_512 = 512
    SIZE_1K = 1024
    SIZE_4K = 4 * 1024
    SIZE_32K = 32 * 1024
    SIZE_64K = 64 * 1024


class State(Enum):
    HANDSHAKE = 1
    BIN_LOAD = 2
    JUMP = 3
    UNLOCK = 4


class ProgrammerProtocol:
    # 握手 / 等 ACK 阶段最长无响应时间（秒）。超过后视为设备无应答。
    DEFAULT_IDLE_TIMEOUT = 10.0

    def __init__(self, comm: CommunicationInterface, bin_parse: BinParse,
                 cfg: dict, logger: Logger, unlock: bool = False):
        self.comm = comm
        self.bin_parse = bin_parse
        self.cfg = cfg
        self.logger = logger
        self.parser = FrameParser(self._on_frame, self._on_cs_error)
        self.last_frame: Optional[RxFrame] = None
        self.unlock_only = unlock

        self.state = State.UNLOCK if unlock else State.HANDSHAKE
        self.waiting_unlock = False

        self.load_addr = int(cfg.get("programmer_bin_addr", 0x50540000))
        self.load_sent = 0
        self.load_packet_len = (
            SizeVal.SIZE_512.value
            if cfg.get("comm_type", "uart") in ("uart", "spi")
            else SizeVal.SIZE_256.value
        )

        # 空闲超时：cfg 可覆盖，便于调试
        try:
            self.idle_timeout = float(cfg.get("handshake_timeout", self.DEFAULT_IDLE_TIMEOUT))
        except (TypeError, ValueError):
            self.idle_timeout = self.DEFAULT_IDLE_TIMEOUT
        self._last_activity = time.monotonic()
        self._last_wait_log = 0.0

    def _send(self, msg_id: int, payload: bytes = b"", data_len: Optional[int] = None):
        dl = data_len if data_len is not None else len(payload)
        f = TxFrame(data_len=dl, msg_id=msg_id, data=bytearray(payload))
        self.comm.send(pack(f))
        try:
            self.comm.flush()
        except Exception:
            pass

    def _on_frame(self, frame: RxFrame):
        self.last_frame = frame
        self._last_activity = time.monotonic()

    def _on_cs_error(self, calc: int, get: int, raw: bytes):
        self.logger.warn("checksum error")
        self.logger.warn(raw.hex("-"))

    def _log_waitting_reset(self, *, force: bool = False):
        """握手等待设备复位/就绪；重试时不刷 send handshake，仅周期性提示。"""
        now = time.monotonic()
        if force or (now - self._last_wait_log) >= 1.0:
            self.logger.info("waitting reset")
            self._last_wait_log = now

    def _send_handshake(self, *, log: bool = True):
        self._send(MsgIdProgrammer.HANDSHAKE.value)
        if log:
            self.logger.info("send handshake")
            self._log_waitting_reset(force=True)

    def _send_unlock(self):
        self._send(MsgIdProgrammer.UNLOCK.value)
        self.logger.info("send unlock")

    def _send_jump(self):
        addr = int(self.cfg.get("programmer_bin_addr", 0))
        self._send(MsgIdProgrammer.JUMP.value, addr.to_bytes(4, byteorder="little"))
        self.logger.info("send jump")

    def _send_load_packet(self) -> bool:
        if self.load_sent >= len(self.bin_parse):
            return False
        remaining = len(self.bin_parse) - self.load_sent
        send_len = min(self.load_packet_len, remaining)
        payload = bytearray(self.load_addr.to_bytes(4, byteorder="little"))
        payload.extend(self.bin_parse.content[self.load_sent: self.load_sent + send_len])
        self._send(MsgIdProgrammer.BIN_LOAD.value, bytes(payload), data_len=4 + send_len)
        self.load_addr += send_len
        self.load_sent += send_len
        self.logger.info(f"send data-rlen:0x{remaining:x}")
        return True

    def start(self):
        self._last_activity = time.monotonic()
        if self.unlock_only:
            self._send_unlock()
        else:
            self._send_handshake()

    def tick(self) -> Optional[int]:
        # 中断检测：UI 线程调用 comm.cancel() 后立即生效
        if self.comm.is_cancelled():
            self.logger.warn("programmer 协议被用户中断")
            return -1
        # 空闲超时：长时间无响应时主动退出循环
        idle = time.monotonic() - self._last_activity
        if idle > self.idle_timeout:
            phase = {
                State.HANDSHAKE: "握手",
                State.BIN_LOAD: "BIN_LOAD 传输",
                State.JUMP: "JUMP",
                State.UNLOCK: "UNLOCK",
            }.get(self.state, "协议")
            self.logger.error(
                f"{phase}超时 ({self.idle_timeout:.1f}s 无响应)，请检查设备与串口连接")
            return 2
        # 二次读取（参考 9062_download_tools）：避免单次 recv 漏掉刚到的字节
        buf1 = self.comm.recv(timeout_ms=0)
        buf2 = self.comm.recv(timeout_ms=0)
        buf = (buf1 or b"") + (buf2 or b"")
        if buf:
            self.parser.feed(buf)
        f = self.last_frame
        self.last_frame = None
        # 握手阶段：若本轮没收到任何 frame，重发握手（设备就绪需要时间）
        if f is None and self.state == State.HANDSHAKE:
            self._send_handshake(log=False)
            self._log_waitting_reset()
        if f is None:
            return None
        mid = f.msg_id
        ack = bytes(f.data)
        ok = ack and ack[0] == 0x01

        if self.state == State.UNLOCK:
            if mid == MsgIdProgrammer.UNLOCK.value and ok:
                self.logger.info("unlock ok")
                self.state = State.HANDSHAKE
                self._send_handshake()
                self.waiting_unlock = False
            elif mid == MsgIdProgrammer.UNLOCK.value and ack and ack[0] == 0x03:
                self.logger.info("waitting unlock finish")
                self.waiting_unlock = True
        elif self.state == State.HANDSHAKE:
            if mid == MsgIdProgrammer.HANDSHAKE.value and ok:
                self.logger.info("handshake ok")
                self.state = State.BIN_LOAD
                self._send_load_packet()
        elif self.state == State.BIN_LOAD:
            if mid == MsgIdProgrammer.BIN_LOAD.value:
                if ok:
                    if not self._send_load_packet():
                        self.state = State.JUMP
                        self._send_jump()
                else:
                    ack_hex = ack.hex() if ack else "empty"
                    self.logger.error(f"BIN_LOAD 被拒绝 (ack={ack_hex})")
                    return 1
        elif self.state == State.JUMP:
            if mid == MsgIdProgrammer.JUMP.value and ok:
                self.logger.info("programmer finish, exit")
                return 0
        return None

    def run(self) -> int:
        if not self.comm.is_open:
            self.logger.error("通信通道未打开")
            return 1
        self.start()
        # 轮询间隔对齐原版 download_programmer.py（每轮 sleep 50ms 再读串口）
        while True:
            code = self.tick()
            if code is None:
                time.sleep(0.05)
                continue
            return code
