# -*- coding: utf-8 -*-
"""擦除命令序列：头部 4K → 主体 32K → 尾部 4K，零头向上取整不丢字节"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from protocol import FlashloadProtocol, MsgIdFlashload

K4 = 0x1000
K32 = 0x8000


class MockComm:
    def __init__(self):
        self.sent = []

    def send(self, data):
        self.sent.append(bytes(data))
        return len(data)

    def flush(self):
        pass


class FakeLogger:
    def __init__(self):
        self.errors = []
        self.infos = []

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        self.infos.append(msg)

    def warn(self, msg):
        pass


def erase_plan(addr, size, cmd="erase_load"):
    """跑 _prepare_erase + _step_erase，返回 [(类型, 地址), ...]"""
    cmds, logger = erase_plan_with_log(addr, size, cmd)
    return cmds, logger.errors


def erase_plan_with_log(addr, size, cmd="erase_load"):
    """同上，但把 logger 也返回（检查日志内容用）"""
    comm = MockComm()
    logger = FakeLogger()
    cfg = {
        "cmd": cmd,
        "comm_type": "uart",
        "erase_addr": addr,
        "erase_size": size,
        "erase_load_addr": addr,
        "erase_load_size": size,
        "target_baud": 0,
    }
    proto = FlashloadProtocol(comm, None, cfg, logger)
    if not proto._prepare_erase():
        return None, logger
    while proto._step_erase():
        pass
    out = []
    for raw in comm.sent:
        mid = raw[8]
        kind = "4K" if mid == MsgIdFlashload.SECTOR_ERASE_4K.value else "32K"
        out.append((kind, int.from_bytes(raw[9:13], "little")))
    return out, logger


def coverage(cmds):
    """每条命令覆盖的字节数之和"""
    return sum(K4 if k == "4K" else K32 for k, _ in cmds)


def test_all_32k_blocks():
    cmds, errs = erase_plan(0x20000, 0x80000)
    assert not errs
    assert cmds == [("32K", 0x20000 + i * K32) for i in range(16)]
    assert coverage(cmds) == 0x80000


def test_32k_then_4k_tail():
    """0x9000 = 1 个 32K + 1 个 4K"""
    cmds, errs = erase_plan(0x20000, 0x9000)
    assert not errs
    assert [k for k, _ in cmds] == ["32K", "4K"]
    assert cmds[0] == ("32K", 0x20000)
    assert cmds[1] == ("4K", 0x28000)
    assert coverage(cmds) == 0x9000


def test_head_4k_then_32k():
    """起始地址不在 32K 边界：先用 4K 补到边界，再 32K"""
    cmds, errs = erase_plan(0x41000, 0x10000)
    assert not errs
    # 头部 7 个 4K（0x41000..0x48000），然后 1 个 32K，尾部 1 个 4K
    assert [k for k, _ in cmds] == ["4K"] * 7 + ["32K", "4K"]
    assert cmds[0] == ("4K", 0x41000)
    assert cmds[7] == ("32K", 0x48000)
    assert cmds[8] == ("4K", 0x50000)
    assert coverage(cmds) == 0x10000


def test_head_4k_only_when_range_small():
    """区间不足 32K 时全用 4K 扇区（头部把区间吃掉了，没剩 32K 块）"""
    cmds, errs = erase_plan(0x41000, 0x8000)
    assert not errs
    assert [k for k, _ in cmds] == ["4K"] * 8
    assert coverage(cmds) == 0x8000


def test_head_4k_only_when_smaller_than_32k():
    """区间不足 32K：全用 4K 扇区"""
    cmds, errs = erase_plan(0x41000, 0x3000)
    assert not errs
    assert [k for k, _ in cmds] == ["4K", "4K", "4K"]
    assert coverage(cmds) == 0x3000


def test_unaligned_size_rounds_up_and_covers():
    """size 不是 4K 对齐 → 向上取整补一次 4K 擦除，不丢字节、不报错"""
    for addr, size, want in (
        (0x0, 0x1801, 0x2000),
        (0x20000, 0x5001, 0x6000),
        (0x1000, 0x1801, 0x2000),
        (0x1000, 0x5001, 0x6000),
        (0x41000, 0x1801, 0x2000),
    ):
        cmds, errs = erase_plan(addr, size)
        assert not errs, f"addr=0x{addr:x} size=0x{size:x} 不该报错: {errs}"
        assert coverage(cmds) == want, \
            f"addr=0x{addr:x} size=0x{size:x}: 擦了 0x{coverage(cmds):x}, 期望 0x{want:x}"


def test_regions_are_contiguous():
    """擦除区间连续无缝、无重叠，且正好覆盖 [addr, addr+对齐后大小)"""
    for addr, size in ((0x0, 0xc000), (0x41000, 0x8000), (0x180000, 0x4d000),
                       (0x1000, 0x1801), (0x20000, 0x5001)):
        cmds, errs = erase_plan(addr, size)
        assert not errs
        pos = addr
        for kind, a in cmds:
            assert a == pos, f"addr=0x{addr:x}: 期望 0x{pos:x}, 实际 0x{a:x}"
            pos += K4 if kind == "4K" else K32
        want = ((size + K4 - 1) // K4) * K4
        assert pos - addr == want


def test_erase_cmd_uses_erase_addr():
    """cmd=erase 时用 erase_addr/erase_size，不是 erase_load_*"""
    cmds, errs = erase_plan(0x100000, 0x1000, cmd="erase")
    assert not errs
    assert cmds == [("4K", 0x100000)]


def test_log_shows_sent_address_and_fixed_total():
    """日志里的地址 = 实际发出的擦除地址；分母固定，分子递增"""
    cmds, logger = erase_plan_with_log(0x0, 0x4000)
    lines = [m for m in logger.infos if m.startswith("send erase-alen:")]
    assert lines == [
        "send erase-alen:0x0 (1/4)",
        "send erase-alen:0x1000 (2/4)",
        "send erase-alen:0x2000 (3/4)",
        "send erase-alen:0x3000 (4/4)",
    ], lines
    # 日志里的地址必须和实际发出的命令一致（不能是"下一块"的地址）
    logged = [int(l.split(":")[1].split()[0], 16) for l in lines]
    sent = [a for _, a in cmds]
    assert logged == sent


def test_log_progress_throttled_for_many_blocks():
    """块数多时按 10% 打点，不刷屏；最后一条一定是 (N/N)"""
    cmds, logger = erase_plan_with_log(0x0, 0x100000)   # 32 块
    lines = [m for m in logger.infos if m.startswith("send erase-alen:")]
    assert len(lines) < len(cmds), "块多时不该每块都打"
    assert lines[0].endswith("(1/32)")
    assert lines[-1].endswith("(32/32)")
    totals = {l.split("(")[1].split("/")[1].rstrip(")") for l in lines}
    assert totals == {"32"}, "分母必须固定"


if __name__ == "__main__":
    test_all_32k_blocks()
    test_32k_then_4k_tail()
    test_head_4k_then_32k()
    test_head_4k_only_when_smaller_than_32k()
    test_unaligned_size_rounds_up_and_covers()
    test_regions_are_contiguous()
    test_erase_cmd_uses_erase_addr()
    test_log_shows_sent_address_and_fixed_total()
    test_log_progress_throttled_for_many_blocks()
    print("全部通过")
