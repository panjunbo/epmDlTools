# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.epm_id_log_parser import IdLogEntry
from utils.id_log_format import (
    count_format_specs,
    format_id_log_head,
    format_id_log_line,
    format_id_log_text,
)


LINK_INFO_FMT = (
    "[BT_SRV_CM_ATCI] link_info addr:%02x:%02x:%02x:%02x:%02x:%02x, "
    "handle:0x%x, state:0x%x, role:0x%x, sniff:0x%x, conecting:0x%x, "
    "disconnecting:0x%x, connected:0x%x"
)


def test_link_info_13_args():
    args = [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x100, 0x200, 0x300, 0x400, 0x500, 0x600, 0x700]
    out = format_id_log_text(LINK_INFO_FMT, args)
    assert "11:22:33:44:55:66" in out
    assert "handle:0x100" in out
    assert "%02x" not in out
    assert count_format_specs(LINK_INFO_FMT) == 13


def test_percent_d_and_u():
    assert format_id_log_text("count:%d level:%u", [42, 7]) == "count:42 level:7"


def test_percent_p():
    out = format_id_log_text("[HFP] hfp_inst %p, handle:0x%x", [0x20001234, 0x55])
    assert "0x20001234" in out
    assert "handle:0x55" in out
    assert "%p" not in out


def test_literal_percent():
    # C 源码 "100%%" 编译进 bin 后是单个 '%'
    assert format_id_log_text("100% done id:%d", [3]) == "100% done id:3"


def test_extra_args():
    out = format_id_log_text("a:%d b:%d", [1, 2, 99])
    assert out == "a:1 b:2"


def test_fewer_args_keeps_tail_specs():
    out = format_id_log_text("a:%d b:%d c:%d", [1])
    assert out.startswith("a:1 b:")
    assert "%d" in out


def test_no_args_returns_template():
    assert format_id_log_text("x:%d", []) == "x:%d"


def test_escaped_percent_in_bin():
    assert format_id_log_text("100%% done id:%d", [3]) == "100% done id:3"


def test_star_width_consumes_arg():
    assert format_id_log_text("n:%*d next:%d", [4, 7, 9]) == "n:   7 next:9"
    assert format_id_log_text("n:%*d", [4]) == "n:%*d"


def test_star_precision():
    assert format_id_log_text("%.*x", [4, 0xAB]) == "00ab"


def test_float_binary32():
    # 1.5f
    assert format_id_log_text("v:%f", [0x3FC00000]) == "v:1.500000"
    assert format_id_log_text("v:%.2f", [0x3FC00000]) == "v:1.50"


def test_string_is_pointer():
    assert format_id_log_text("name:%s", [0]) == "name:(null)"
    assert format_id_log_text("name:%s id:%d", [0x20001000, 3]) == "name:0x20001000 id:3"


def test_line_hides_mod_and_merges_id_ts():
    entry = IdLogEntry(
        timestamp=0x00375430, cpu_id=0, module_id=2, log_id=0x300001,
        is_hexdump=False, args=[],
    )
    assert format_id_log_head("ID_A", entry.timestamp) == "[ID_A:226627us]"
    assert format_id_log_line(entry, "hci_control_init") == "hci_control_init"
    assert "mod=" not in format_id_log_line(entry, "hci_control_init")
    dump = IdLogEntry(
        timestamp=0x10, cpu_id=1, module_id=4, log_id=0x300400,
        is_hexdump=True, data=b"\x0a\xff",
    )
    line = format_id_log_line(dump, "rx data")
    assert line == "rx data | 0a ff"
    assert "mod=" not in line
    missing = format_id_log_line(dump, "")
    assert missing == "log_id=0x300400 | 0a ff"


if __name__ == "__main__":
    test_link_info_13_args()
    test_percent_d_and_u()
    test_percent_p()
    test_literal_percent()
    test_extra_args()
    test_fewer_args_keeps_tail_specs()
    test_no_args_returns_template()
    test_escaped_percent_in_bin()
    test_star_width_consumes_arg()
    test_star_precision()
    test_float_binary32()
    test_string_is_pointer()
    test_line_hides_mod_and_merges_id_ts()
    print("全部通过")
