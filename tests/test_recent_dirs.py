# -*- coding: utf-8 -*-
"""下载路径下拉：最近用过的目录（最多 5 条）能存进 ini 并原样读回来。

背景：每次换 bin 目录都要开目录选择器翻半天，重复劳动。
下拉里记住最近用过的几个目录，换目录就快了；换过的目录要能跨程序重启保留。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.download_panel import DownloadPanel
from utils.config_manager import RECENT_DIR_MAX, ConfigManager, push_recent_dir


# ---------- push_recent_dir：列表本身的规则 ----------

def test_push_moves_to_front():
    assert push_recent_dir(["b", "c"], "a") == ["a", "b", "c"]


def test_push_dedupes():
    # 已经用过的目录再选一次：提到最前，不能出现两条
    assert push_recent_dir(["a", "b", "c"], "b") == ["b", "a", "c"]


def test_push_keeps_at_most():
    recent = [f"d{i}" for i in range(RECENT_DIR_MAX)]
    out = push_recent_dir(recent, "new")
    assert len(out) == RECENT_DIR_MAX
    assert out[0] == "new"
    # 最早的被挤掉
    assert f"d{RECENT_DIR_MAX - 1}" not in out


def test_push_ignores_empty():
    assert push_recent_dir(["a"], "") == ["a"]
    assert push_recent_dir(["a"], None) == ["a"]
    assert push_recent_dir(["a"], "   ") == ["a"]


def test_push_drops_blank_entries():
    assert push_recent_dir(["a", "", "  ", "b"], "c") == ["c", "a", "b"]


# ---------- ini 存取 ----------

def _cfg_path(root):
    return os.path.join(root, "config.ini")


def test_config_round_trip():
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        dirs = [os.path.join(d, f"bin{i}") for i in range(3)]
        assert cm.save_recent_dirs(dirs) is True
        assert cm.list_recent_dirs() == dirs
        cm.save()
        # 重新读一遍：写进 ini 的内容要能原样读回来
        cm2 = ConfigManager(_cfg_path(d))
        assert cm2.list_recent_dirs() == dirs


def test_config_reports_unchanged():
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        assert cm.save_recent_dirs(["a"]) is True
        # 内容没变就不用再写一遍 ini
        assert cm.save_recent_dirs(["a"]) is False


def test_config_drops_stale_entries():
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        cm.save_recent_dirs(["a", "b", "c"])
        cm.save()
        # 列表变短：过期的 recent_dir_N 要删掉，否则旧目录一直留在下拉里
        assert cm.save_recent_dirs(["a"]) is True
        cm.save()
        cm2 = ConfigManager(_cfg_path(d))
        assert cm2.list_recent_dirs() == ["a"]
        assert not cm2.has_option("bins", "recent_dir_2")
        assert not cm2.has_option("bins", "recent_dir_3")


def test_config_ignores_empty_list():
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        assert cm.save_recent_dirs([]) is False
        assert cm.list_recent_dirs() == []


def test_config_cleans_up_entries_over_the_limit():
    """ini 里留着上限调整前的旧项（recent_dir_6..10）时要清掉。"""
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        for i in range(1, 11):
            cm.set("bins", f"recent_dir_{i}", f"dir{i}")
        cm.save()
        # 读出来的只有上限内的 5 条
        assert len(cm.list_recent_dirs()) == RECENT_DIR_MAX
        # 再存一次（内容没变）也要把多余的旧项清掉
        assert cm.save_recent_dirs(cm.list_recent_dirs()) is True
        cm.save()
        cm2 = ConfigManager(_cfg_path(d))
        assert len(cm2.list_recent_dirs()) == RECENT_DIR_MAX
        assert not cm2.has_option("bins", f"recent_dir_{RECENT_DIR_MAX + 1}")
        assert not cm2.has_option("bins", "recent_dir_10")


def test_config_accepts_percent_in_path():
    # 路径里带 % 时，默认的 ini 插值语法会当成错误直接抛异常
    with tempfile.TemporaryDirectory() as d:
        cm = ConfigManager(_cfg_path(d))
        p = r"C:\work\100%\bin"
        assert cm.save_recent_dirs([p]) is True
        cm.save()
        cm2 = ConfigManager(_cfg_path(d))
        assert cm2.list_recent_dirs() == [p]


# ---------- DownloadPanel：记录/去重/回调（不建 Tk 界面） ----------

class _Combo:
    """替身：只记下 values，不真的建 Tk 控件。"""

    def __init__(self):
        self.values = ()

    def configure(self, **kw):
        if "values" in kw:
            self.values = tuple(kw["values"])


class _Var:
    """替身：只提供 get()。"""

    def __init__(self, value=""):
        self._v = value

    def get(self):
        return self._v


class _Panel:
    """借用 DownloadPanel 的最近目录逻辑。"""

    def __init__(self, current=""):
        self.cmb_bin_dir = _Combo()
        self._recent_dirs = []
        self.bin_dir = _Var(current)
        self.notified = []
        self._on_recent_dirs_changed = self.notified.append

    set_recent_dirs = DownloadPanel.set_recent_dirs
    get_recent_dirs = DownloadPanel.get_recent_dirs
    _push_recent_dir = DownloadPanel._push_recent_dir
    _sync_dir_values = DownloadPanel._sync_dir_values
    _on_bin_dir_entered = DownloadPanel._on_bin_dir_entered
    _on_bin_dir_left = DownloadPanel._on_bin_dir_left


def test_panel_records_used_dir():
    with tempfile.TemporaryDirectory() as d:
        panel = _Panel()
        panel._push_recent_dir(d)
        assert panel.get_recent_dirs() == [d]
        # 外部要收到通知，才能把目录存进 ini
        assert panel.notified == [[d]]


def test_panel_keeps_newest_first():
    with tempfile.TemporaryDirectory() as root:
        a = os.path.join(root, "a")
        b = os.path.join(root, "b")
        os.mkdir(a)
        os.mkdir(b)
        panel = _Panel()
        panel._push_recent_dir(a)
        panel._push_recent_dir(b)
        assert panel.get_recent_dirs() == [b, a]
        # 再选一次 a：提到最前，不出现两条
        panel._push_recent_dir(a)
        assert panel.get_recent_dirs() == [a, b]
        assert panel.notified[-1] == [a, b]


def test_panel_ignores_missing_dir():
    with tempfile.TemporaryDirectory() as d:
        panel = _Panel()
        panel._push_recent_dir(os.path.join(d, "no_such_dir"))
        assert panel.get_recent_dirs() == []
        assert panel.notified == []


def test_panel_caps_at_limit():
    with tempfile.TemporaryDirectory() as root:
        panel = _Panel()
        for i in range(RECENT_DIR_MAX + 5):
            sub = os.path.join(root, f"d{i}")
            os.mkdir(sub)
            panel._push_recent_dir(sub)
        recent = panel.get_recent_dirs()
        assert len(recent) == RECENT_DIR_MAX
        assert recent[0] == os.path.join(root, f"d{RECENT_DIR_MAX + 4}")


def test_panel_values_match_recent_dirs():
    with tempfile.TemporaryDirectory() as d:
        panel = _Panel()
        panel._push_recent_dir(d)
        # 下拉里的内容要和记住的列表一致
        assert tuple(panel.cmb_bin_dir.values) == tuple(panel.get_recent_dirs())


def test_panel_keeps_current_dir_in_values():
    """当前路径必须在下拉里：刚从 ini 读进来、还没用过也要能看到。"""
    panel = _Panel(current=r"H:\work\bin")
    panel.set_recent_dirs([r"H:\other\bin"])
    assert panel.cmb_bin_dir.values[0] == r"H:\work\bin"
    assert r"H:\work\bin" in panel.get_recent_dirs()


def test_panel_remembers_typed_path_on_enter():
    """手输路径后回车：要记进最近列表。"""
    with tempfile.TemporaryDirectory() as d:
        panel = _Panel(current=d)
        panel._on_bin_dir_entered()
        assert panel.get_recent_dirs() == [d]


def test_panel_remembers_typed_path_on_focus_out():
    """手输路径后直接点别处（没按回车）：也要记进最近列表。"""
    with tempfile.TemporaryDirectory() as d:
        panel = _Panel(current=d)
        panel._on_bin_dir_left()
        assert panel.get_recent_dirs() == [d]
        assert panel.notified == [[d]]


def test_panel_keeps_previous_dir_after_switching():
    """换成别的目录后，原来那个还要留在下拉里（不能凭空消失）。"""
    with tempfile.TemporaryDirectory() as root:
        a = os.path.join(root, "a")
        b = os.path.join(root, "b")
        os.mkdir(a)
        os.mkdir(b)
        panel = _Panel(current=a)
        panel.set_recent_dirs([a])
        # 换成 b（相当于在下拉里选了另一项）
        panel.bin_dir._v = b
        panel._push_recent_dir(b)
        assert panel.cmb_bin_dir.values[0] == b
        assert a in panel.cmb_bin_dir.values
        assert a in panel.get_recent_dirs()


if __name__ == "__main__":
    test_push_moves_to_front()
    test_push_dedupes()
    test_push_keeps_at_most()
    test_push_ignores_empty()
    test_push_drops_blank_entries()
    test_config_round_trip()
    test_config_reports_unchanged()
    test_config_drops_stale_entries()
    test_config_ignores_empty_list()
    test_config_cleans_up_entries_over_the_limit()
    test_config_accepts_percent_in_path()
    test_panel_records_used_dir()
    test_panel_keeps_newest_first()
    test_panel_ignores_missing_dir()
    test_panel_caps_at_limit()
    test_panel_values_match_recent_dirs()
    test_panel_keeps_current_dir_in_values()
    test_panel_remembers_typed_path_on_enter()
    test_panel_remembers_typed_path_on_focus_out()
    test_panel_keeps_previous_dir_after_switching()
    print("全部通过")
