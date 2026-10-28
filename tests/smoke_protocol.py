import struct
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from communication.interface import CommunicationInterface
from utils import BinParse, Logger, make_default_logger
from protocol import FlashloadProtocol, ProgrammerProtocol, MsgIdFlashload, MsgIdProgrammer


class MockComm(CommunicationInterface):
    def __init__(self):
        self.tx_log = []
        self.rx_buf = bytearray()
        self._opened = False

    def open(self) -> bool:
        self._opened = True
        return True

    def close(self):
        self._opened = False

    def send(self, data: bytes) -> int:
        self.tx_log.append(bytes(data))
        return len(data)

    def push_rx(self, data: bytes):
        self.rx_buf.extend(data)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        if not self.rx_buf:
            return b""
        out = bytes(self.rx_buf)
        self.rx_buf.clear()
        return out

    def flush(self):
        pass


def make_rx_frame(msg_id: int, payload: bytes = b"\x01") -> bytes:
    sync = struct.pack("<I", 0xB51ABABA)
    size = len(payload)
    cs = 0
    for b in payload:
        cs += b
    for b in sync:
        cs += b
    for b in struct.pack("<I", size):
        cs += b
    cs += msg_id
    cs &= 0xFFFFFFFF
    return sync + struct.pack("<I", size) + bytes([msg_id]) + payload + struct.pack("<I", cs)


class FakeBP:
    def __init__(self, content: bytes = b""):
        self.content = bytearray(content)
        self.saved = None

    def __len__(self):
        return len(self.content)

    def parse(self, path):
        return True

    def save(self, data):
        self.saved = bytes(data) if isinstance(data, (bytes, bytearray)) else data
        return "/tmp/_x.bin"


def drive_erase():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "erase",
        "comm_type": "uart",
        "flash_port_num": 0,
        "erase_addr": 0x100000,
        "erase_size": 0x1000,
        "src_crc_init": 0x1234, "src_crc_poly": 0x3d65,
        "src_crc_len": 2, "src_crc_ref_in": 0, "src_crc_ref_out": 1,
        "src_crc_xor_out": 0xffff,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdFlashload.SECTOR_ERASE_4K.value, b"\x01"))
    code = proto.tick()
    print(f"  erase exit code = {code}")
    assert code == 0
    assert len(comm.tx_log) == 2
    print(f"  tx[0] = FL_SELECT? {comm.tx_log[0][8] == 0x80}")
    assert comm.tx_log[0][8] == 0x80
    assert comm.tx_log[1][8] == 0x82
    print("  PASS")


def drive_erase_load():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"\xAA" * 1024)
    logger = make_default_logger()
    cfg = {
        "cmd": "erase_load",
        "comm_type": "uart",
        "flash_port_num": 0,
        "erase_load_addr": 0x100000,
        "erase_load_size": 0x1000,
        "flashload_bin_path": "/tmp/x.bin",
        "src_crc_init": 0x1234, "src_crc_poly": 0x3d65,
        "src_crc_len": 2, "src_crc_ref_in": 0, "src_crc_ref_out": 1,
        "src_crc_xor_out": 0xffff,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdFlashload.SECTOR_ERASE_4K.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdFlashload.PAGE_PROGRAM.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdFlashload.PAGE_PROGRAM.value, b"\x01"))
    code = proto.tick()
    print(f"  erase_load exit code = {code}")
    assert code == 0
    sent = [t[8] for t in comm.tx_log]
    print(f"  tx msg ids = {[hex(s) for s in sent]}")
    assert 0x80 in sent and 0x82 in sent and 0x85 in sent
    print("  PASS")


def drive_erase_load_empty_bin():
    """空 bin：擦完立刻报错退出，不发 PAGE_PROGRAM，也不空等 ACK。"""
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "erase_load",
        "comm_type": "uart",
        "flash_port_num": 0,
        "erase_load_addr": 0x100000,
        "erase_load_size": 0x1000,
        "flashload_bin_path": "/tmp/empty.bin",
        "src_crc_init": 0x1234, "src_crc_poly": 0x3d65,
        "src_crc_len": 2, "src_crc_ref_in": 0, "src_crc_ref_out": 1,
        "src_crc_xor_out": 0xffff,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdFlashload.SECTOR_ERASE_4K.value, b"\x01"))
    code = proto.tick()
    print(f"  erase_load(empty) exit code = {code}")
    assert code == 1, f"空 bin 应立即失败, 实际 {code}"
    sent = [t[8] for t in comm.tx_log]
    assert MsgIdFlashload.PAGE_PROGRAM.value not in sent, "空 bin 不应发 PAGE_PROGRAM"
    print("  PASS")


def drive_erase_all():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "erase_all",
        "comm_type": "uart",
        "flash_port_num": 0,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    assert comm.tx_log[1][8] == MsgIdFlashload.ERASE_CHIP.value, \
        f"期望 ERASE_CHIP(0x81), 实际 0x{comm.tx_log[1][8]:02x}"
    comm.push_rx(make_rx_frame(MsgIdFlashload.ERASE_CHIP.value, b"\x01"))
    code = proto.tick()
    print(f"  erase_all exit code = {code}")
    assert code == 0
    print("  PASS")


def drive_flash_read_single():
    """单包 flash_read：device 返回 ack+addr+256B 数据，应正确拼接 + 保存。"""
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "flash_read",
        "comm_type": "uart",
        "flash_port_num": 0,
        "flash_read_addr": 0x0,
        "flash_read_size": 0x100,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    payload = b"\x01" + b"\x00\x00\x00\x00" + (b"\xAA" * 256)
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_READ.value, payload))
    code = proto.tick()
    print(f"  flash_read exit code = {code}")
    assert code == 0xFF
    assert bp.saved == b"\xAA" * 256, f"saved len={len(bp.saved) if bp.saved else 0}"
    print("  PASS")


def drive_flash_read_multi():
    """多包 flash_read：768B（512 + 256）分两包，需正确拼接。"""
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "flash_read",
        "comm_type": "uart",
        "flash_port_num": 0,
        "flash_read_addr": 0x0,
        "flash_read_size": 768,  # 0x300
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    # 第 1 包：512B → _on_flash_read_ack 拼完数据后立即触发下一包发送
    # 此时 _send_flash_read 已把 remain 从 256 减到 0，并已发出第 2 包请求
    p1 = b"\x01" + b"\x00\x00\x00\x00" + (b"\xBB" * 512)
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_READ.value, p1))
    proto.tick()
    assert proto.flash_read_remain == 0, \
        f"remain 应为 0（已发第 2 包请求）, 实际 {proto.flash_read_remain}"
    assert len(proto.flash_read_buf) == 512
    # 第 2 包：256B
    p2 = b"\x01" + b"\x00\x02\x00\x00" + (b"\xCC" * 256)
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_READ.value, p2))
    code = proto.tick()
    print(f"  flash_read multi-packet exit code = {code}")
    assert code == 0xFF
    assert len(bp.saved) == 768
    assert bp.saved[:512] == b"\xBB" * 512
    assert bp.saved[512:] == b"\xCC" * 256
    print("  PASS")


def drive_flash_xip_read():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "flash_xip_read",
        "comm_type": "uart",
        "flash_port_num": 0,
        "flash_read_addr": 0x0,
        "flash_read_size": 0x100,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    proto.tick()
    payload = b"\x01" + b"\x00\x00\x00\x00" + (b"\xDD" * 256)
    comm.push_rx(make_rx_frame(MsgIdFlashload.XIP_FLASH_READ.value, payload))
    code = proto.tick()
    print(f"  flash_xip_read exit code = {code}")
    assert code == 0xFF
    assert bp.saved == b"\xDD" * 256
    print("  PASS")


def drive_reset():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"")
    logger = make_default_logger()
    cfg = {
        "cmd": "reset",
        "comm_type": "uart",
        "flash_port_num": 0,
        "rdelay": 5,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, bp, cfg, logger)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdFlashload.FLASH_SELECT.value, b"\x01"))
    code = proto.tick()
    print(f"  reset exit code = {code}")
    assert code == 0xFF
    sent_ids = [t[8] for t in comm.tx_log]
    assert sent_ids[0] == MsgIdFlashload.FLASH_SELECT.value
    assert sent_ids[1] == MsgIdFlashload.RESET.value
    print("  PASS")


def drive_programmer():
    comm = MockComm()
    comm.open()
    bp = FakeBP(b"\x10" * 256)
    logger = make_default_logger()
    cfg = {
        "comm_type": "uart",
        "programmer_bin_addr": 0x08080000,
    }
    proto = ProgrammerProtocol(comm, bp, cfg, logger, unlock=False)
    proto.start()
    comm.push_rx(make_rx_frame(MsgIdProgrammer.HANDSHAKE.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdProgrammer.BIN_LOAD.value, b"\x01"))
    proto.tick()
    comm.push_rx(make_rx_frame(MsgIdProgrammer.JUMP.value, b"\x01"))
    code = proto.tick()
    print(f"  programmer exit code = {code}")
    assert code == 0
    sent = [t[8] for t in comm.tx_log]
    print(f"  tx msg ids = {[hex(s) for s in sent]}")
    assert 0x01 in sent and 0x02 in sent and 0x03 in sent
    print("  PASS")


if __name__ == "__main__":
    print("--- drive_erase ---")
    drive_erase()
    print("--- drive_erase_load ---")
    drive_erase_load()
    print("--- drive_erase_load_empty_bin ---")
    drive_erase_load_empty_bin()
    print("--- drive_erase_all ---")
    drive_erase_all()
    print("--- drive_flash_read_single ---")
    drive_flash_read_single()
    print("--- drive_flash_read_multi ---")
    drive_flash_read_multi()
    print("--- drive_flash_xip_read ---")
    drive_flash_xip_read()
    print("--- drive_reset ---")
    drive_reset()
    print("--- drive_programmer ---")
    drive_programmer()
    print("=== ALL PROTOCOL TESTS PASSED ===")
