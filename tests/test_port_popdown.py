# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.serial_panel import fit_popdown_span


def test_stays_on_secondary_monitor():
    # 旧逻辑用主屏宽度把 x 钳到 0 附近，副屏上的下拉会跑到另一块屏幕
    x, width = fit_popdown_span(2100, 800, 1920, 3840)
    assert (x, width) == (2100, 800)
    assert 1920 <= x < 3840
    assert x + width <= 3840


def test_shifts_left_inside_same_monitor():
    x, width = fit_popdown_span(3600, 800, 1920, 3840)
    assert width == 800
    assert x + width <= 3840 - 4
    assert x >= 1920


def test_negative_monitor_stays_negative():
    x, width = fit_popdown_span(-200, 400, -1920, 0)
    assert width == 400
    assert x < 0
    assert x >= -1920
    assert x + width <= 0
