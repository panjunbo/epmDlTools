# -*- coding: utf-8 -*-
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.idlog_bin_parser import (
    IDLOG_BASE_ADDR,
    find_idlog_base_addr,
    parse_idlog_base_addr,
    parse_idlog_bin,
    resolve_idlog_base_addrs,
    resolve_idlog_bin_paths,
)
from utils.logger import Logger

_REF_BIN_DIR = r"G:\Nanosic\tx232mpw3f\epm_driver\tools\bin"
_REF_ADDR = r"G:\Nanosic\tx232mpw3f\epm_driver\solution\bt24g_headset\remote\remote_d25f\debug\idlog_addr_MCU_A.txt"


def test_parse_idlog_base_addr():
    if not os.path.isfile(_REF_ADDR):
        return
    assert parse_idlog_base_addr(_REF_ADDR) == 0x30000000


def test_find_idlog_base_addr_default():
    assert find_idlog_base_addr(None) == IDLOG_BASE_ADDR


def test_resolve_tools_bin_layout():
    if not os.path.isdir(_REF_BIN_DIR):
        return
    paths = resolve_idlog_bin_paths(
        _REF_BIN_DIR,
        {"mcu_a": "idlog_MCU_A.bin", "mcu_b": "idlog_MCU_B.bin", "mcu_c": "idlog_MCU_C.bin"},
    )
    assert paths["mcu_a"] and os.path.isfile(paths["mcu_a"])
    assert paths["mcu_a"].endswith("idlog_MCU_A.bin")
    # tools/bin 里 MCU_B bin 可能为空，允许 None
    assert paths["mcu_c"] and os.path.isfile(paths["mcu_c"])


def test_per_cpu_base_addr():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "idlog_addr_MCU_A.txt"), "w", encoding="utf-8") as f:
            f.write("30000000 D __idlog_start\n")
        with open(os.path.join(d, "idlog_addr_MCU_B.txt"), "w", encoding="utf-8") as f:
            f.write("31000000 D __idlog_start\n")
        bases = resolve_idlog_base_addrs(d, {}, {}, 0x30000000)
        assert bases["mcu_a"] == 0x30000000
        assert bases["mcu_b"] == 0x31000000
        assert bases["mcu_c"] == 0x30000000
        forced = resolve_idlog_base_addrs(d, {}, {"mcu_b": 0x32000000}, 0)
        assert forced["mcu_b"] == 0x32000000
        assert find_idlog_base_addr(d, "MCU_B") == 0x31000000


def test_parse_uses_base():
    chunk = b"hello %d\x00" + b"\x00" * (256 - len(b"hello %d\x00"))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "idlog_MCU_B.bin")
        with open(path, "wb") as f:
            f.write(chunk)
        m = parse_idlog_bin(path, 0x31000000)
        assert m[0x310000] == "hello %d"
        assert 0x300000 not in m


def test_set_id_file_does_not_create_bin():
    with tempfile.TemporaryDirectory() as d:
        missing = os.path.join(d, "idlog_MCU_A.bin")
        lg = Logger()
        lg.set_id_file(missing)
        assert not os.path.exists(missing)
        assert lg.id_file_path() is None
        text = os.path.join(d, "run.log")
        lg.set_id_file(text)
        assert os.path.isfile(text)
        lg.close_id_file()


def test_link_info_log_id_from_tools_bin():
    if not os.path.isdir(_REF_BIN_DIR):
        return
    p = os.path.join(_REF_BIN_DIR, "idlog_MCU_A.bin")
    if not os.path.isfile(p):
        return
    m = parse_idlog_bin(p, IDLOG_BASE_ADDR)
    text = m.get(0x30017b, "")
    assert "link_info addr:" in text
    assert text.count("%02x") >= 6


if __name__ == "__main__":
    test_parse_idlog_base_addr()
    test_find_idlog_base_addr_default()
    test_per_cpu_base_addr()
    test_parse_uses_base()
    test_set_id_file_does_not_create_bin()
    test_resolve_tools_bin_layout()
    test_link_info_log_id_from_tools_bin()
    print("全部通过")
