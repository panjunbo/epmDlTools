import struct
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class MsgIdFlashload(Enum):
    FLASH_SELECT = 0x80
    ERASE_CHIP = 0x81
    SECTOR_ERASE_4K = 0x82
    BLOCK_ERASE_32K = 0x83
    BLOCK_ERASE_64K = 0x84
    PAGE_PROGRAM = 0x85
    PAGE_PROGRAM_CRYPT = 0x86
    FLASH_READ = 0x87
    XIP_FLASH_READ = 0x88
    CRYPT_CFG = 0x89
    BAUD_CHANGE = 0x8A
    CHECKSUM = 0x90
    RESET = 0x91
    SYNC = 0x92


class MsgIdProgrammer(Enum):
    HANDSHAKE = 0x01
    BIN_LOAD = 0x02
    JUMP = 0x03
    CHECKSUM = 0x04
    UNLOCK = 0x05


SYNC_WORD = bytes(b"\x55\xaa\x5a\x5a")
RX_SYNC_WORD = 0xB51ABABA

OFFSET_SYNC = 0
OFFSET_DATA_SIZE = 4
OFFSET_ID = 8
OFFSET_DATA = 9

LEN_SYNC = 4
LEN_DATA_SIZE = 4
LEN_ID = 1
LEN_CHECKSUM = 4
MIN_LEN = 13


@dataclass
class TxFrame:
    data_len: int
    msg_id: int
    data: bytearray


@dataclass
class RxFrame:
    msg_id: int
    data: bytearray


def calc_tx_checksum(data_len: int, msg_id: int, data: bytes) -> int:
    s = 0
    for b in data:
        s += b
    for b in SYNC_WORD:
        s += b
    size_bytes = data_len.to_bytes(4, byteorder="little")
    for b in size_bytes:
        s += b
    s += msg_id
    return s & 0xFFFFFFFF


def calc_rx_checksum(data_len: int, msg_id: int, data: bytes) -> int:
    s = 0
    for b in data:
        s += b
    sync_bytes = RX_SYNC_WORD.to_bytes(4, byteorder="little")
    for b in sync_bytes:
        s += b
    size_bytes = data_len.to_bytes(4, byteorder="little")
    for b in size_bytes:
        s += b
    s += msg_id
    return s & 0xFFFFFFFF


def pack(frame: TxFrame) -> bytes:
    out = bytearray()
    out.extend(SYNC_WORD)
    out.extend(frame.data_len.to_bytes(4, byteorder="little"))
    out.extend(frame.msg_id.to_bytes(1, byteorder="little"))
    if frame.data_len != 0:
        out.extend(frame.data)
    cs = calc_tx_checksum(frame.data_len, frame.msg_id, bytes(frame.data))
    out.extend(cs.to_bytes(4, byteorder="little"))
    return bytes(out)


class FrameParser:
    def __init__(self, on_frame: Callable[[RxFrame], None], on_checksum_error=None):
        self._buf = bytearray()
        self._on_frame = on_frame
        self._on_error = on_checksum_error

    def feed(self, data: bytes) -> Optional[RxFrame]:
        if not data:
            return None
        self._buf.extend(data)
        last = None
        while True:
            f = self._try_parse()
            if f is None:
                break
            last = f
        return last

    def _try_parse(self) -> Optional[RxFrame]:
        n = len(self._buf)
        if n < MIN_LEN:
            return None
        for i in range(0, n - 3):
            sync = int.from_bytes(self._buf[i: i + 4], byteorder="little")
            if sync != RX_SYNC_WORD:
                continue
            if i + MIN_LEN > n:
                # 找到 SYNC 但帧还未到齐：保留 buffer 等更多数据
                return None
            data_len = int.from_bytes(
                self._buf[i + OFFSET_DATA_SIZE: i + OFFSET_DATA_SIZE + LEN_DATA_SIZE],
                byteorder="little",
            )
            if i + MIN_LEN + data_len > n:
                # data 段未到齐：保留 buffer 等更多数据
                return None
            msg_id = self._buf[i + OFFSET_ID]
            data_start = i + OFFSET_DATA
            data_end = data_start + data_len
            payload = bytes(self._buf[data_start: data_end])
            cs_bytes = self._buf[data_end: data_end + LEN_CHECKSUM]
            cs = int.from_bytes(cs_bytes, byteorder="little")
            calc = calc_rx_checksum(data_len, msg_id, payload)
            if calc != cs:
                if self._on_error:
                    self._on_error(calc, cs, bytes(self._buf))
                # 校验失败：清空 buffer 重新对齐
                # （假 SYNC + 真帧的复合场景极少，损失可接受；保留下次重传即可恢复）
                self._buf.clear()
                return None
            frame = RxFrame(msg_id=msg_id, data=bytearray(payload))
            del self._buf[: i + MIN_LEN + data_len]
            try:
                self._on_frame(frame)
            except Exception:
                pass
            return frame
        # 没找到 SYNC：保留最后 3 字节（可能是跨块的 SYNC 前缀）
        self._buf[:] = self._buf[-3:]
        return None
