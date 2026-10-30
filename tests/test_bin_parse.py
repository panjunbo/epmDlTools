# -*- coding: utf-8 -*-
"""BinParse：读取过程中文件被改写时必须拒绝。

编译固件时 bin 是边写边覆盖的，app 正好读到一半就烧进设备 = 砖机。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.bin_parse import BinParse


def test_parse_reads_the_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.bin")
        with open(path, "wb") as fp:
            fp.write(b"\x01\x02\x03")
        bp = BinParse()
        assert bp.parse(path) is True
        assert bytes(bp.content) == b"\x01\x02\x03"


def test_parse_fails_when_file_is_missing():
    bp = BinParse()
    assert bp.parse(os.path.join("no", "such.bin")) is False
    assert len(bp.content) == 0


def test_parse_refuses_a_file_changed_during_read():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.bin")
        with open(path, "wb") as fp:
            fp.write(b"\x01\x02\x03")
        bp = BinParse()
        real = BinParse._stat
        # 读之前 (3, 1)，读之后 (3, 2)：文件在读的过程中被改写过
        seq = [(3, 1), (3, 2)]
        try:
            BinParse._stat = staticmethod(lambda p: seq.pop(0))
            assert bp.parse(path) is False
            assert len(bp.content) == 0
        finally:
            BinParse._stat = staticmethod(real)


def test_parse_accepts_an_unchanged_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "a.bin")
        with open(path, "wb") as fp:
            fp.write(b"\x01\x02\x03")
        bp = BinParse()
        real = BinParse._stat
        seq = [(3, 1), (3, 1)]
        try:
            BinParse._stat = staticmethod(lambda p: seq.pop(0))
            assert bp.parse(path) is True
        finally:
            BinParse._stat = staticmethod(real)


if __name__ == "__main__":
    test_parse_reads_the_file()
    test_parse_fails_when_file_is_missing()
    test_parse_refuses_a_file_changed_during_read()
    test_parse_accepts_an_unchanged_file()
    print("全部通过")
