# -*- coding: utf-8 -*-
"""bin 自动识别：同分取更新的文件，编译完能自动跟上新产物。

背景：编译固件是在下载工具之外做的，bin 目录随时会变。
以前目录里同时留着新旧两个版本时，选中哪个靠 listdir 顺序碰运气，
碰上旧的就会把旧版本烧进设备。
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.download_panel import DownloadPanel


class _Var:
    def __init__(self, value):
        self._v = value

    def get(self):
        return self._v


class _Panel:
    """只借用 DownloadPanel 的文件匹配逻辑，不建 Tk 界面。"""

    _chip_types = ["9032", "9062"]

    def __init__(self, chip="9032"):
        self.chip_type = _Var(chip)

    _find_bin_file = DownloadPanel._find_bin_file
    _file_mtime = staticmethod(DownloadPanel._file_mtime)


def _touch(path, mtime):
    with open(path, "wb") as fp:
        fp.write(b"\x00" * 16)
    os.utime(path, (mtime, mtime))


def test_same_score_picks_the_newer_file():
    # 两个文件匹配分一样（旧版本没删干净）：必须取更新的那个
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        _touch(os.path.join(d, "MCU_A_9032.bin"), now - 3600)
        _touch(os.path.join(d, "MCU_A_9032_v2.bin"), now)
        assert _Panel()._find_bin_file(d, "MCU_A", "9032") == "MCU_A_9032_v2.bin"


def test_picks_up_the_file_built_after_the_app_started():
    # 打开工具时目录里只有旧文件，编译之后多出新文件：重扫要跟上新的
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        _touch(os.path.join(d, "MCU_A_9032.bin"), now - 3600)
        panel = _Panel()
        assert panel._find_bin_file(d, "MCU_A", "9032") == "MCU_A_9032.bin"
        _touch(os.path.join(d, "MCU_A_9032_v2.bin"), now)
        assert panel._find_bin_file(d, "MCU_A", "9032") == "MCU_A_9032_v2.bin"


def test_prefix_match_still_wins_over_a_newer_file():
    # 匹配分高的仍然优先：文件名前缀对上是强信号，不能被"更新"盖过
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        _touch(os.path.join(d, "MCU_A.bin"), now - 3600)
        _touch(os.path.join(d, "app_MCU_A.bin"), now)
        assert _Panel()._find_bin_file(d, "MCU_A", "9032") == "MCU_A.bin"


def test_other_chip_file_is_not_picked():
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        _touch(os.path.join(d, "MCU_A_9032.bin"), now - 3600)
        _touch(os.path.join(d, "MCU_A_9062.bin"), now)
        assert _Panel()._find_bin_file(d, "MCU_A", "9032") == "MCU_A_9032.bin"


def test_idlog_file_is_not_picked_as_firmware():
    # idlog_MCU_A.bin 名字里带 MCU_A，但不是固件，不能被选上
    with tempfile.TemporaryDirectory() as d:
        now = time.time()
        _touch(os.path.join(d, "MCU_A.bin"), now - 3600)
        _touch(os.path.join(d, "idlog_MCU_A.bin"), now)
        assert _Panel()._find_bin_file(d, "MCU_A", "9032") == "MCU_A.bin"


def test_mtime_of_missing_file_is_none():
    assert _Panel()._file_mtime(os.path.join("no", "such.bin")) is None


if __name__ == "__main__":
    test_same_score_picks_the_newer_file()
    test_picks_up_the_file_built_after_the_app_started()
    test_prefix_match_still_wins_over_a_newer_file()
    test_other_chip_file_is_not_picked()
    test_idlog_file_is_not_picked_as_firmware()
    test_mtime_of_missing_file_is_none()
    print("全部通过")
