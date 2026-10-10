import time
from enum import Enum
from typing import Optional

from communication.interface import CommunicationInterface

from utils.bin_parse import BinParse
from utils.crc import CrcCalculator
from utils.logger import Logger

from .frame import (
    FrameParser, MsgIdFlashload, RxFrame, TxFrame, pack,
)


class SizeVal(Enum):
    SIZE_8 = 8
    SIZE_32 = 32
    SIZE_128 = 128
    SIZE_256 = 256
    SIZE_512 = 512
    SIZE_1K = 1024
    SIZE_4K = 4 * 1024
    SIZE_32K = 32 * 1024
    SIZE_64K = 64 * 1024


class EraseMode(Enum):
    NONE = 0
    MODE_4K = 1
    MODE_32K = 2


class FlashloadProtocol:
    STATE_SELECT = 0
    STATE_ERASE = 1
    STATE_LOAD = 2
    STATE_FLASH_READ = 3
    STATE_XIP_READ = 4
    STATE_RESET = 5

    # 等待设备响应 ACK / flash_read 数据的最长空闲时间（秒）
    DEFAULT_IDLE_TIMEOUT = 30.0

    def __init__(self, comm: CommunicationInterface, bin_parse: BinParse,
                 cfg: dict, logger: Logger):
        self.comm = comm
        self.bin_parse = bin_parse
        self.cfg = cfg
        self.logger = logger
        self.crc_calc = CrcCalculator(
            width=(int(cfg.get("src_crc_len", 2)) * 8),
            poly=int(cfg.get("src_crc_poly", 0x3d65)),
            ref_in=bool(int(cfg.get("src_crc_ref_in", 0))),
            init=int(cfg.get("src_crc_init", 0x1234)),
            ref_out=bool(int(cfg.get("src_crc_ref_out", 1))),
            xor_out=int(cfg.get("src_crc_xor_out", 0xffff)),
        )
        self.parser = FrameParser(self._on_frame, self._on_cs_error)
        self.last_frame: Optional[RxFrame] = None
        self.exit_code: Optional[int] = None

        self.cmd = cfg.get("cmd", "erase")
        self.target_baud = int(cfg.get("target_baud", 0))
        self.flash_port_num = int(cfg.get("flash_port_num", 0))
        self.flashload_bin_path = cfg.get("flashload_bin_path", "./bin/flash.bin")
        self.erase_addr = int(cfg.get("erase_addr", 0))
        self.erase_size = int(cfg.get("erase_size", 0x100000))
        self.erase_load_addr = int(cfg.get("erase_load_addr", 0))
        self.erase_load_size = int(cfg.get("erase_load_size", 0x100000))
        self.flash_read_addr = int(cfg.get("flash_read_addr", 0))
        self.flash_read_size = int(cfg.get("flash_read_size", 0x100000))
        self.sync_exit = str(cfg.get("sync_exit", "false")).lower() == "true"
        self._sync_exit_sent = False
        self.rdelay = int(cfg.get("rdelay", 0))

        self.state = self.STATE_SELECT
        self.erase_inc_addr = 0
        self.erase_4k_head_num = 0
        self.erase_32k_num = 0
        self.erase_4k_num = 0
        self.erase_mode = EraseMode.NONE

        self.load_addr = 0
        self.load_sent = 0
        self.load_packet_len = self._load_packet_len(cfg.get("comm_type", "uart"))
        self.flash_read_packet_len = self._read_packet_len(cfg.get("comm_type", "uart"))
        self.flash_read_remain = 0
        self.flash_read_buf = bytearray()

        # 空闲超时：flash 数据量大、flash_read 时间长，默认 30s 更宽裕
        try:
            self.idle_timeout = float(cfg.get("handshake_timeout", self.DEFAULT_IDLE_TIMEOUT))
        except (TypeError, ValueError):
            self.idle_timeout = self.DEFAULT_IDLE_TIMEOUT
        self._last_activity = time.monotonic()

    @staticmethod
    def _load_packet_len(comm_type: str) -> int:
        if comm_type in ("uart", "spi"):
            return SizeVal.SIZE_512.value
        return SizeVal.SIZE_256.value

    @staticmethod
    def _read_packet_len(comm_type: str) -> int:
        if comm_type in ("uart", "spi"):
            return SizeVal.SIZE_512.value
        return SizeVal.SIZE_8.value

    def _send(self, msg_id: int, payload: bytes = b"", data_len: Optional[int] = None):
        dl = data_len if data_len is not None else len(payload)
        f = TxFrame(data_len=dl, msg_id=msg_id, data=bytearray(payload))
        self.comm.send(pack(f))

    def _on_cs_error(self, calc: int, get: int, raw: bytes):
        self.logger.warn(f"checksum error: calc=0x{calc:08x}, get=0x{get:08x}")
        self.logger.warn(f"raw: {raw.hex('-').upper()}")

    def _on_frame(self, frame: RxFrame):
        self.last_frame = frame
        self._last_activity = time.monotonic()

    def start(self):
        self._last_activity = time.monotonic()
        if self.target_baud != 0:
            self._send(MsgIdFlashload.BAUD_CHANGE.value,
                       self.target_baud.to_bytes(4, byteorder="little"))
        else:
            self._send_flash_select()

    def _send_flash_select(self):
        self._send(MsgIdFlashload.FLASH_SELECT.value, bytes([self.flash_port_num]))

    def _send_erase_chip(self):
        self._send(MsgIdFlashload.ERASE_CHIP.value)

    def _send_erase(self, addr: int, mode: EraseMode):
        mid = MsgIdFlashload.SECTOR_ERASE_4K.value if mode == EraseMode.MODE_4K \
            else MsgIdFlashload.BLOCK_ERASE_32K.value
        self._send(mid, addr.to_bytes(4, byteorder="little"))
        self.erase_mode = mode

    def _send_load_packet(self):
        if self.load_sent >= len(self.bin_parse):
            return False
        remaining = len(self.bin_parse) - self.load_sent
        send_len = min(self.load_packet_len, remaining)
        addr_bytes = self.load_addr.to_bytes(4, byteorder="little")
        if self.cmd == "erase_load_crypt":
            full = bytearray(addr_bytes)
            data = self.bin_parse.content[self.load_sent: self.load_sent + send_len]
            i = 0
            while i + SizeVal.SIZE_256.value <= len(data):
                blk = data[i: i + SizeVal.SIZE_256.value]
                full.extend(blk)
                full.extend(self.crc_calc.calculate(bytes(blk)).to_bytes(4, "little"))
                i += SizeVal.SIZE_256.value
            if i < len(data):
                blk = data[i:]
                full.extend(blk)
                full.extend(self.crc_calc.calculate(bytes(blk)).to_bytes(4, "little"))
            self._send(MsgIdFlashload.PAGE_PROGRAM_CRYPT.value, bytes(full),
                       data_len=len(full))
        else:
            payload = bytearray(addr_bytes)
            payload.extend(self.bin_parse.content[self.load_sent: self.load_sent + send_len])
            self._send(MsgIdFlashload.PAGE_PROGRAM.value, bytes(payload),
                       data_len=4 + send_len)
        self.load_addr += send_len
        self.load_sent += send_len
        # 不每包都 log：每次 logger.info 会通过 self.after(0, ...) 触发
        # Tk Text widget 更新（worker thread → main thread 的跨线程调用 + 文本框 insert），
        # 1800 包 × 几 ms = 几秒纯 GUI 延迟。
        # 改为：第一包 + 每 10% 进度 + 最后一包时打印。
        total = len(self.bin_parse)
        sent = self.load_sent
        prev_pct = (sent - send_len) * 10 // total if sent >= send_len else -1
        cur_pct = sent * 10 // total
        if prev_pct != cur_pct or sent == send_len or sent >= total:
            self.logger.info(f"send data-progress:{sent}/{total} (0x{total - sent:x} left)")
        return True

    def _send_crypt_cfg(self):
        p = bytearray()
        for k in ("src_crc_init", "src_crc_poly", "src_crc_len",
                  "src_crc_ref_in", "src_crc_ref_out", "src_crc_xor_out",
                  "dst_crc_init", "dst_crc_poly"):
            p.extend(int(self.cfg.get(k, 0)).to_bytes(4, "little"))
        self._send(MsgIdFlashload.CRYPT_CFG.value, bytes(p))
        self.logger.info("send crypt_crc_cfg")

    def _send_flash_read(self):
        if self.flash_read_remain <= 0:
            return False
        send_len = min(self.flash_read_packet_len, self.flash_read_remain)
        payload = (self.flash_read_addr.to_bytes(4, "little")
                   + send_len.to_bytes(4, "little"))
        self._send(MsgIdFlashload.FLASH_READ.value, payload)
        self.flash_read_addr += send_len
        self.flash_read_remain -= send_len
        self.logger.info(f"send flash_read-rlen:0x{self.flash_read_remain:x}")
        return True

    def _send_xip_read(self):
        if self.flash_read_remain <= 0:
            return False
        send_len = min(self.flash_read_packet_len, self.flash_read_remain)
        payload = (self.flash_read_addr.to_bytes(4, "little")
                   + send_len.to_bytes(4, "little"))
        self._send(MsgIdFlashload.XIP_FLASH_READ.value, payload)
        self.flash_read_addr += send_len
        self.flash_read_remain -= send_len
        self.logger.info(f"send xip flash_read-rlen:0x{self.flash_read_remain:x}")
        return True

    def _send_reset(self):
        self._send(MsgIdFlashload.RESET.value, bytes([self.rdelay]))
        self.logger.info("send reset cmd")

    def _send_sync_exit(self):
        self._send(MsgIdFlashload.SYNC.value)
        self.logger.info("send sync exit cmd")

    def _prepare_erase(self) -> bool:
        if self.cmd == "erase":
            base, size = self.erase_addr, self.erase_size
        elif self.cmd in ("erase_load", "erase_load_crypt"):
            base, size = self.erase_load_addr, self.erase_load_size
        else:
            self.logger.error(f"cmd 不支持: {self.cmd}")
            return False
        if size == 0:
            self.logger.error("erase size 为 0")
            return False
        if base % SizeVal.SIZE_4K.value != 0:
            self.logger.error("erase addr 必须 4K 对齐")
            return False

        self.erase_inc_addr = base
        head = base % SizeVal.SIZE_32K.value
        aligned_size = size
        if head != 0:
            head_size = min(SizeVal.SIZE_32K.value - head, size)
            self.erase_4k_head_num = head_size // SizeVal.SIZE_4K.value
            aligned_size -= head_size
        else:
            self.erase_4k_head_num = 0
        self.erase_32k_num = aligned_size // SizeVal.SIZE_32K.value if aligned_size > 0 else 0
        remain = aligned_size % SizeVal.SIZE_32K.value if aligned_size > 0 else 0
        if remain > 0 and remain % SizeVal.SIZE_4K.value == 0:
            self.erase_4k_num = remain // SizeVal.SIZE_4K.value
        elif remain > 0:
            self.logger.error("erase size 不是 4K 对齐")
            return False
        else:
            self.erase_4k_num = 0
        return True

    def _erase_done(self) -> bool:
        end = (self.erase_addr + self.erase_size) if self.cmd == "erase" \
            else (self.erase_load_addr + self.erase_load_size)
        return (
            self.erase_inc_addr >= end
            and self.erase_32k_num == 0
            and self.erase_4k_num == 0
            and self.erase_4k_head_num == 0
        )

    def _step_erase(self) -> bool:
        if self.erase_4k_head_num > 0:
            self._send_erase(self.erase_inc_addr, EraseMode.MODE_4K)
            self.erase_4k_head_num -= 1
            self.erase_inc_addr += SizeVal.SIZE_4K.value
        elif self.erase_32k_num > 0:
            self._send_erase(self.erase_inc_addr, EraseMode.MODE_32K)
            self.erase_32k_num -= 1
            self.erase_inc_addr += SizeVal.SIZE_32K.value
        elif self.erase_4k_num > 0:
            self._send_erase(self.erase_inc_addr, EraseMode.MODE_4K)
            self.erase_4k_num -= 1
            self.erase_inc_addr += SizeVal.SIZE_4K.value
        else:
            self.logger.info("erase ok")
            return False
        # 不每擦一个块都 log，避免 worker thread → Tk main thread 的
        # self.after() + Text widget 更新开销。改为：每 10% 进度打一次。
        total_erases = (self.erase_4k_head_num + self.erase_32k_num +
                        self.erase_4k_num + 1)
        done_erases = total_erases - (self.erase_4k_head_num +
                                      self.erase_32k_num + self.erase_4k_num)
        if done_erases == 1 or done_erases * 10 // total_erases > (done_erases - 1) * 10 // total_erases:
            self.logger.info(
                f"send erase-alen:0x{self.erase_inc_addr:x} ({done_erases}/{total_erases})")
        return True

    def _prepare_load(self) -> bool:
        if not self.bin_parse.parse(self.flashload_bin_path):
            return False
        self.load_addr = self.erase_load_addr
        self.load_sent = 0
        return True

    def _prepare_flash_read(self):
        self.flash_read_remain = self.flash_read_size
        self.flash_read_buf = bytearray()

    def _on_ack_select(self, ack: bytes) -> Optional[int]:
        if not ack or ack[0] != 0x01:
            self.logger.error("flash num select ack error")
            return 1
        self.logger.info(f"flash num selet :0x{self.flash_port_num:02x}")
        if self.cmd == "erase_all":
            self.state = self.STATE_ERASE
            self._send_erase_chip()
        elif self.cmd in ("erase", "erase_load", "erase_load_crypt"):
            if not self._prepare_erase():
                return 1
            self.state = self.STATE_ERASE
            self._step_erase()
        elif self.cmd == "flash_read":
            self.state = self.STATE_FLASH_READ
            self._prepare_flash_read()
            self._send_flash_read()
        elif self.cmd == "flash_xip_read":
            self.state = self.STATE_XIP_READ
            self._prepare_flash_read()
            self._send_xip_read()
        elif self.cmd == "reset":
            self._send_reset()
            return 0xFF
        return None

    def _on_baud_change(self, ack: bytes) -> Optional[int]:
        if not ack or ack[0] != 0x01:
            self.logger.error("baud change ack error")
            return 1
        new_baud = int.from_bytes(ack[1:5], byteorder="little")
        if new_baud != self.target_baud:
            # 设备的 ACK 返回的是它当前的波特率（切之前），PC 端必须切到 target_baud
            # 才能与已 reconfigure 的设备匹配。
            old_baud = getattr(self.comm, "baudrate", None)
            self.logger.info(
                f"BAUD_CHANGE ack 设备当前={new_baud}，PC 从 {old_baud} 切到 {self.target_baud}")
            self.comm.flush()
            self.comm.close()
            # 关键：UartComm.baudrate 是构造时锁定的，必须同步更新，否则
            # comm.open() 会用旧波特率（与 device 失同步 → 重发 BAUD_CHANGE 收不到 → 卡死）
            if hasattr(self.comm, "baudrate"):
                self.comm.baudrate = self.target_baud
            self.cfg["download_baud"] = self.target_baud
            ok = self.comm.open()
            actual_baud = getattr(self.comm, "baudrate", None)
            # 二次验证：实际打开的 Serial 实例的 baudrate 是不是真的更新了
            actual_ser_baud = None
            inner_ser = getattr(self.comm, "_serial", None)
            if inner_ser is not None:
                actual_ser_baud = getattr(inner_ser, "baudrate", None)
            self.logger.info(
                f"BAUD_CHANGE PC 重开: open={ok}, comm.baudrate={actual_baud}, "
                f"serial.baudrate={actual_ser_baud}")
            if actual_ser_baud != self.target_baud:
                self.logger.error(
                    f"PC 端波特率未生效: 期望 {self.target_baud}, 实际 {actual_ser_baud}")
                return 1
            self._send(MsgIdFlashload.BAUD_CHANGE.value,
                       self.target_baud.to_bytes(4, byteorder="little"))
        else:
            self.logger.info(f"BAUD_CHANGE 完成: 双方均在 {self.target_baud}")
            self._send_flash_select()
        return None

    def _on_erase_ack(self, mid: int, ack: bytes) -> Optional[int]:
        ok = ack and ack[0] == 0x01 and mid == (
            MsgIdFlashload.SECTOR_ERASE_4K.value
            if self.erase_mode == EraseMode.MODE_4K
            else MsgIdFlashload.BLOCK_ERASE_32K.value
        )
        if not ok:
            self.logger.error("erase ack error")
            return 1
        if self._erase_done():
            self.logger.info("erase ok")
            if self.cmd == "erase":
                return 0
            if not self._prepare_load():
                return 1
            if self.cmd == "erase_load":
                self.state = self.STATE_LOAD
                self._send_load_packet()
            else:
                self._send_crypt_cfg()
                self.state = self.STATE_LOAD
        else:
            self._step_erase()
        return None

    def _on_load_ack(self, mid: int, ack: bytes) -> Optional[int]:
        ok = ack and ack[0] == 0x01
        expected = MsgIdFlashload.PAGE_PROGRAM_CRYPT.value \
            if self.cmd == "erase_load_crypt" \
            else MsgIdFlashload.PAGE_PROGRAM.value
        if mid == expected and ok:
            if self._send_load_packet() is False:
                self.logger.info("erase_load finish")
                return 0
        elif mid == MsgIdFlashload.CRYPT_CFG.value and ok:
            self._send_load_packet()
        else:
            self.logger.error("load ack error")
            return 1
        return None

    def _on_flash_read_ack(self, mid: int, ack: bytes) -> Optional[int]:
        ok = ack and ack[0] == 0x01 and mid == MsgIdFlashload.FLASH_READ.value
        if not ok:
            self.logger.error("read_flash ack error")
            return 1
        # data 格式: [ack(1)][addr(4)][payload(N)]；去掉前 5 字节得到真实 flash 数据
        # 必须用 ack，不能用 self.last_frame.data：tick() 已把 last_frame 清成 None
        self.flash_read_buf.extend(ack[5:])
        if self.flash_read_remain == 0:
            self.bin_parse.save(bytes(self.flash_read_buf))
            self.logger.info("read_flash finish")
            return 0xFF
        self._send_flash_read()
        return None

    def _on_xip_read_ack(self, mid: int, ack: bytes) -> Optional[int]:
        ok = ack and ack[0] == 0x01 and mid == MsgIdFlashload.XIP_FLASH_READ.value
        if not ok:
            self.logger.error("xip_read_flash ack error")
            return 1
        # 同 _on_flash_read_ack：用 ack[5:] 而不是 self.last_frame.data[5:]
        self.flash_read_buf.extend(ack[5:])
        if self.flash_read_remain == 0:
            self.bin_parse.save(bytes(self.flash_read_buf))
            self.logger.info("xip_read_flash finish")
            return 0xFF
        self._send_xip_read()
        return None

    def tick(self) -> Optional[int]:
        # 中断检测：UI 线程调用 comm.cancel() 后立即生效
        if self.comm.is_cancelled():
            self.logger.warn("flashload 协议被用户中断")
            self.exit_code = -1
            return -1
        # 空闲超时：等待设备 ACK 太久则主动退出
        idle = time.monotonic() - self._last_activity
        if idle > self.idle_timeout:
            self.logger.error(
                f"flashload 等待设备响应超时 ({self.idle_timeout:.1f}s)，请检查设备与串口")
            return 2
        # tight loop：直接读串口 + 解析（参考官方 download_flash.py:441 的 tight polling）
        # 不开后台 reader 线程，避免 GIL 争用 + 跨线程 last_frame 同步开销
        try:
            buf1 = self.comm.recv(timeout_ms=0)
            buf2 = self.comm.recv(timeout_ms=0)
            data = (buf1 or b"") + (buf2 or b"")
        except Exception:
            data = b""
        if data:
            self.parser.feed(data)
        f = self.last_frame
        self.last_frame = None
        if f is None:
            return None
        mid = f.msg_id
        ack = bytes(f.data)
        if self._sync_exit_sent:
            # SYNC 已发出，后续帧不是 PAGE_PROGRAM。不能再走 load ack，
            # 否则会把同步应答判成失败并跳过复位。
            if mid == MsgIdFlashload.SYNC.value and ack and ack[0] == 0x01:
                self.logger.info("sync exit ack")
            else:
                self.logger.info(
                    f"sync exit reply msg=0x{mid:02x} data={ack.hex()}")
            return 0
        if self.state == self.STATE_SELECT:
            if mid == MsgIdFlashload.BAUD_CHANGE.value:
                return self._on_baud_change(ack)
            if mid == MsgIdFlashload.FLASH_SELECT.value:
                return self._on_ack_select(ack)
            # device 可能回复 SYNC (0x92) + data=0x02 表示"BAUD_CHANGE 不支持"
            # 此时应直接进入 FLASH_SELECT 继续
            if mid == MsgIdFlashload.SYNC.value:
                self.logger.warn(
                    "device 不支持 BAUD_CHANGE，直接进入 FLASH_SELECT")
                self._send_flash_select()
        elif self.state == self.STATE_ERASE:
            if mid == MsgIdFlashload.SYNC.value and ack and ack[0] == 0x01:
                return 0xFF
            if self.cmd == "erase_all":
                if mid == MsgIdFlashload.ERASE_CHIP.value and ack and ack[0] == 0x01:
                    self.logger.info("erase_all finish")
                    self.comm.close()
                    return 0
            else:
                return self._on_erase_ack(mid, ack)
        elif self.state == self.STATE_LOAD:
            return self._on_load_ack(mid, ack)
        elif self.state == self.STATE_FLASH_READ:
            return self._on_flash_read_ack(mid, ack)
        elif self.state == self.STATE_XIP_READ:
            return self._on_xip_read_ack(mid, ack)
        return None

    def run(self) -> int:
        if not self.comm.is_open:
            self.logger.error("通信通道未打开")
            return 1
        self.start()
        # tight polling 循环：参考官方 download_flash.py:441
        # 每轮：读串口（若有数据）→ 解析帧 → 状态机处理
        # 无数据时不 sleep，避免把"读完一帧→立刻发下一包"的延迟拉到毫秒级
        while True:
            code = self.tick()
            if code is None:
                continue
            # erase_load 结束返回 0。需要复位时最后一包必须先 SYNC，
            # 设备才会留在下载模式，后续 reset 才能连上。
            if code == 0 and self.sync_exit and not self._sync_exit_sent:
                self._send_sync_exit()
                self._sync_exit_sent = True
                continue
            if code == 0xFF:
                if self.sync_exit and not self._sync_exit_sent:
                    self._send_sync_exit()
                    self._sync_exit_sent = True
                    continue
                return 0
            return code
