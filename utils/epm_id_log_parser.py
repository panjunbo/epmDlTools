"""EPM ID_LOG 帧解析器（有状态版本）

参考 epm_driver/components/epm_id_log/epm_id_log.c 与 epm_log_frame.c。
从串口 raw bytes 中识别 0xA58E 同步帧（TYPE=0xC2/0xC3），剥离 ID log 帧。
内部维护 _buf 累积跨调用的半帧（处理 LogSerial 按 \\n 切分把帧切两半的情况）。

Frame 格式:
  SYNC(2B LE=0xA58E) | TYPE(1B) | LEN(2B LE=payload_size+1) | PAYLOAD | CRC8(1B)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple


SYNC_MAGIC = 0xA58E
TYPE_ID_LOG = 0xC2
TYPE_ID_LOG_U8_DUMP = 0xC3
FRAME_OVERHEAD = 6  # SYNC(2) + TYPE(1) + LEN(2) + CRC(1)

CPU_NAMES = {
    0: "MCU_A",
    1: "MCU_B",
    2: "MCU_C",
    0xFF: "未知",
}

# 哨兵：帧头合法但数据不足（半帧），留在 _buf 等待补全
_HALF_FRAME = object()


def _crc8(data: bytes) -> int:
    """CRC-8（多项式 0x07，初始 0x00）— 与 epm_crc8 一致。"""
    crc = 0x00
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x07) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


@dataclass
class IdLogEntry:
    timestamp: int
    cpu_id: int
    module_id: int
    log_id: int
    is_hexdump: bool
    args: List[int] = field(default_factory=list)
    data: bytes = b""
    raw: bytes = b""


class IdLogParser:
    """有状态 ID log 帧解析器，维护 _buf 累积跨调用半帧。

    feed(data) 返回 (新解析的条目, 透传的非帧字节)。
    帧按流序剥离；SYNC 之前的字节是普通文本，按序原样透传。
    只有 sync 开头的半帧保留在内部 _buf，下次 feed 拼接。
    """

    def __init__(self, max_buf: int = 4096):
        self._buf = bytearray()
        self._max_buf = max_buf
        self._recent_entries: List[IdLogEntry] = []
        self.stats = {
            "frames_ok": 0,
            "frames_bad_crc": 0,
            "frames_unknown_type": 0,
            "parse_errors": 0,
            "bytes_input": 0,
            "bytes_stripped": 0,
            "half_frame_drops": 0,
        }

    @property
    def entries(self) -> List[IdLogEntry]:
        return self._recent_entries

    def clear(self):
        self._buf.clear()
        self._recent_entries = []
        for k in self.stats:
            self.stats[k] = 0

    def feed(self, data: bytes) -> Tuple[List[IdLogEntry], bytes]:
        """返回 (新解析的条目, 透传的非帧字节)。

        帧按流序剥离；SYNC 之前的字节视为普通文本原样透传（不吞、不重排）。
        只有 sync 开头的半帧留在 _buf，下次 feed 继续补全。
        """
        if not data:
            return [], b""
        self.stats["bytes_input"] += len(data)
        self._buf.extend(data)
        new_entries: List[IdLogEntry] = []
        text = bytearray()

        while self._buf:
            buf = self._buf
            n = len(buf)
            # 找 SYNC_MAGIC (0x8E 0xA5)
            sync_pos = -1
            for i in range(n - 1):
                if buf[i] == 0x8E and buf[i + 1] == 0xA5:
                    sync_pos = i
                    break
            if sync_pos < 0:
                # 无 SYNC：全部是文本，原样透传。
                # 末尾若是 0x8E，保留 1 字节（下一 feed 可能拼出 SYNC）
                if buf[-1] == 0x8E:
                    text.extend(buf[:-1])
                    del buf[:-1]
                else:
                    text.extend(buf)
                    buf.clear()
                break
            if sync_pos > 0:
                # SYNC 之前的字节是普通文本，透传
                text.extend(buf[:sync_pos])
                del buf[:sync_pos]
                continue
            # SYNC 在头部：尝试解析完整帧
            entry = self._parse_frame_at_head()
            if entry is _HALF_FRAME:
                # 半帧：等下次 feed；但若 _buf 超限说明 SYNC 是假的（文本里
                # 恰好出现 8E A5 且后续字节拼出合法 LEN），放弃它继续扫
                if len(buf) > self._max_buf:
                    del buf[:1]
                    self.stats["half_frame_drops"] += 1
                    continue
                break
            if entry is not None:
                new_entries.append(entry)
                self._recent_entries.append(entry)
                if len(self._recent_entries) > 1000:
                    del self._recent_entries[:-1000]
            # entry is None → bad CRC / 异常 LEN / 未知 TYPE：已 resync，继续循环

        return new_entries, bytes(text)

    def _parse_frame_at_head(self):
        """_buf 头部是 SYNC，解析一帧。
        返回 IdLogEntry / None（bad CRC、异常 LEN、未知 TYPE，已 resync 跳过
        SYNC 字节）/ _HALF_FRAME（数据不足，留在 _buf 等待）。
        """
        buf = self._buf
        n = len(buf)
        if n < FRAME_OVERHEAD:
            return _HALF_FRAME
        ftype = buf[2]
        plen = buf[3] | (buf[4] << 8)
        if plen < 1 or plen > 512:
            # 异常 LEN（大概率是文本里的假 SYNC）：跳过 SYNC 字节
            del buf[:1]
            return None
        payload_size = plen - 1
        total = 2 + 1 + 2 + payload_size + 1
        if n < total:
            return _HALF_FRAME  # 半帧
        payload = bytes(buf[5:5 + payload_size])
        crc = buf[5 + payload_size]
        if _crc8(payload) != crc:
            self.stats["frames_bad_crc"] += 1
            del buf[:1]  # 跳过 SYNC 字节，重新找同步
            return None
        del buf[:total]
        self.stats["bytes_stripped"] += total
        try:
            if ftype == TYPE_ID_LOG:
                entry = self._parse_id_log(payload)
            elif ftype == TYPE_ID_LOG_U8_DUMP:
                entry = self._parse_id_log_u8(payload)
            else:
                self.stats["frames_unknown_type"] += 1
                return None
            self.stats["frames_ok"] += 1
            return entry
        except Exception:
            self.stats["parse_errors"] += 1
            return None

    def _parse_id_log(self, payload: bytes) -> IdLogEntry:
        if len(payload) < 9:
            payload = payload.ljust(9, b"\x00")
        cpu_id = payload[0]
        module_id = payload[1]
        timestamp = int.from_bytes(payload[2:6], "little")
        log_id = int.from_bytes(payload[6:9], "little")
        args_data = payload[9:]
        nwords = len(args_data) // 4
        args = [int.from_bytes(args_data[i:i + 4], "little")
                for i in range(0, nwords * 4, 4)]
        return IdLogEntry(
            timestamp=timestamp,
            cpu_id=cpu_id,
            module_id=module_id,
            log_id=log_id,
            is_hexdump=False,
            args=args,
            raw=payload,
        )

    def _parse_id_log_u8(self, payload: bytes) -> IdLogEntry:
        if len(payload) < 9:
            payload = payload.ljust(9, b"\x00")
        cpu_id = payload[0]
        module_id = payload[1]
        timestamp = int.from_bytes(payload[2:6], "little")
        log_id = int.from_bytes(payload[6:9], "little")
        data = payload[9:]
        return IdLogEntry(
            timestamp=timestamp,
            cpu_id=cpu_id,
            module_id=module_id,
            log_id=log_id,
            is_hexdump=True,
            data=bytes(data),
            raw=payload,
        )