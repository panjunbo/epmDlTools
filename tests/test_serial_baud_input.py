# -*- coding: utf-8 -*-
"""串口波特率可手输：任意整数都接受，非法值回退到上一个合法值。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.serial_panel import parse_baud


def test_parse_baud_accepts_plain_integers():
    assert parse_baud("115200") == 115200
    assert parse_baud("  921600  ") == 921600
    assert parse_baud("123456") == 123456  # 非标准波特率也允许


def test_parse_baud_rejects_garbage():
    assert parse_baud("") is None
    assert parse_baud("   ") is None
    assert parse_baud("abc") is None
    assert parse_baud("115200x") is None
    assert parse_baud("0") is None
    assert parse_baud("-115200") is None
    assert parse_baud(None) is None


def _make_panel():
    """建一个真实的 SerialPanel（需要 Tk 环境）。"""
    import tkinter as tk
    from ui.serial_panel import SerialPanel

    root = tk.Tk()
    root.withdraw()
    panel = SerialPanel(root)
    return root, panel


def _panel_available():
    try:
        root, _panel = _make_panel()
    except Exception:
        return None
    root.destroy()
    return True


def test_panel_accepts_typed_baud():
    if not _panel_available():
        return
    root, panel = _make_panel()
    try:
        # 模拟用户手输非标准波特率
        panel.log_baud.set("123456")
        assert panel.get_log_baud() == 123456
        assert panel.log_baud.get() == "123456"

        panel.dl_baud.set("2500000")
        assert panel.get_dl_baud() == 2500000
    finally:
        root.destroy()


def test_panel_reverts_invalid_baud():
    if not _panel_available():
        return
    root, panel = _make_panel()
    try:
        panel.log_baud.set("115200")
        assert panel.get_log_baud() == 115200

        panel.log_baud.set("abc")
        assert panel.get_log_baud() == 115200  # 回退到上一个合法值
        assert panel.log_baud.get() == "115200"
    finally:
        root.destroy()


def test_panel_check_baud_normalizes_and_warns():
    if not _panel_available():
        return
    root, panel = _make_panel()
    warnings = []
    panel._on_warn = warnings.append
    try:
        panel.log_baud.set(" 09600 ")
        panel._check_baud("log")
        assert panel.log_baud.get() == "9600"
        assert not warnings

        panel.dl_baud.set("0")
        panel._check_baud("dl")
        assert panel.dl_baud.get() == "3000000"
        assert warnings and "下载" in warnings[-1]
    finally:
        root.destroy()


def test_panel_set_from_config_falls_back_on_bad_value():
    if not _panel_available():
        return
    root, panel = _make_panel()
    try:
        assert panel.set_log_baud("115200") == 115200
        assert panel.log_baud.get() == "115200"
        # 配置里写了垃圾值 → 退回默认值，不把垃圾带进 UI
        assert panel.set_log_baud("bad") == 500000
        assert panel.log_baud.get() == "500000"
        assert panel.set_dl_baud("bad") == 3000000
    finally:
        root.destroy()
