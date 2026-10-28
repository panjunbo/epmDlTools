# -*- coding: utf-8 -*-
"""擦除计划：擦除范围 = bin 文件大小（向上对齐 4K），以及区间重叠检测"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.app import (
    align_up_4k,
    find_region_overlaps,
    plan_flash_regions,
)
from utils.config_manager import ConfigManager


def _make_bin(size: int) -> str:
    fd, path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as fp:
        fp.write(b"\x00" * size)
    return path


def _sel(key, path, addr, label=None, mode="flashload"):
    return {
        "key": key,
        "label": label or key,
        "path": path,
        "addr": addr,
        "mode": mode,
    }


def test_align_up_4k():
    assert align_up_4k(0) == 0
    assert align_up_4k(-1) == 0
    assert align_up_4k(1) == 0x1000
    assert align_up_4k(0x1000) == 0x1000
    assert align_up_4k(0x1001) == 0x2000
    assert align_up_4k(0xbb80) == 0xc000
    assert align_up_4k(0x7338) == 0x8000


def test_plan_uses_file_size():
    """擦除范围 = 文件大小向上对齐 4K，跟文件走，不看固定窗口"""
    path = _make_bin(0xbb80)
    regions = plan_flash_regions("9032", [_sel("bootloader", path, 0x0, "boot")])
    assert len(regions) == 1
    r = regions[0]
    assert r["size"] == 0xbb80
    assert r["erase_size"] == 0xc000
    assert r["addr"] == 0x0
    assert r["end"] == 0xc000


def test_plan_skips_programmer():
    """programmer 烧到 SRAM，不占 flash 区间"""
    path = _make_bin(0x1000)
    regions = plan_flash_regions(
        "9032", [_sel("programmer", path, 0x08080000, "prog", mode="programmer")])
    assert regions == []


def test_plan_missing_file():
    regions = plan_flash_regions(
        "9032", [_sel("mcu_a", "/no/such/file.bin", 0x20000, "mcua")])
    assert regions[0]["size"] == 0
    assert regions[0]["erase_size"] == 0
    assert regions[0]["end"] == 0x20000


def test_no_overlap_when_adjacent():
    """相邻不重叠：mcua 到 0x100000，mcub 从 0x100000 开始"""
    a = _make_bin(0x7338)   # → [0x20000, 0x28000)
    b = _make_bin(0xaf4)    # → [0x100000, 0x101000)
    regions = plan_flash_regions(
        "9032", [_sel("mcu_a", a, 0x20000, "mcua"), _sel("mcu_b", b, 0x100000, "mcub")])
    assert find_region_overlaps(regions) == []


def test_overlap_detected():
    """mcub 的区间伸进了 mcua 的区间 → 报重叠"""
    a = _make_bin(0x80000)   # mcua → [0x20000, 0xa0000)
    b = _make_bin(0x80000)   # mcub → [0x90000, 0x110000) → 与 mcua 重叠 [0x90000, 0xa0000)
    regions = plan_flash_regions(
        "9032", [_sel("mcu_a", a, 0x20000, "mcua"), _sel("mcu_b", b, 0x90000, "mcub")])
    overlaps = find_region_overlaps(regions)
    assert len(overlaps) == 1
    ra, rb, lo, hi = overlaps[0]
    assert ra["label"] == "mcua" and rb["label"] == "mcub"
    assert lo == 0x90000 and hi == 0xa0000


def test_overlap_touching_edges_is_not_overlap():
    """区间首尾相接（end == 下一个 addr）不算重叠"""
    a = _make_bin(0x1000)   # [0x20000, 0x21000)
    b = _make_bin(0x1000)   # [0x21000, 0x22000)
    regions = plan_flash_regions(
        "9032", [_sel("mcu_a", a, 0x20000, "mcua"), _sel("mcu_b", b, 0x21000, "mcub")])
    assert find_region_overlaps(regions) == []


def test_config_still_two_fields():
    """配置格式回到 '关键词, 地址'（不再有第三段窗口）"""
    assert ConfigManager._parse_bin_entry("MCU_A, 0x20000") == ("MCU_A", 0x20000)
    assert ConfigManager._parse_bin_entry("MCU_A, zzz")[1] == 0
    assert ConfigManager._parse_bin_entry("") == ("", 0)


if __name__ == "__main__":
    test_align_up_4k()
    test_plan_uses_file_size()
    test_plan_skips_programmer()
    test_plan_missing_file()
    test_no_overlap_when_adjacent()
    test_overlap_detected()
    test_overlap_touching_edges_is_not_overlap()
    test_config_still_two_fields()
    print("全部通过")
