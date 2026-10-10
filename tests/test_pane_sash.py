# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.app import initial_h_sash


def test_keeps_sixty_percent_when_right_fits():
    # 100%：右侧要 620，40% 有 768，分隔条停在 60%
    assert initial_h_sash(1920, 620, 780) == 1152


def test_gives_right_its_width_at_high_scale():
    # 150%：右侧要比 40% 更宽，分隔条左移，工具栏仍放得下
    assert initial_h_sash(1920, 860, 700) == 1920 - 860


def test_prefers_toolbar_when_both_cannot_fit():
    # 窗口偏窄时按钮不能被裁掉
    assert initial_h_sash(1600, 900, 900) == 900


def test_small_window_splits_in_half():
    assert initial_h_sash(100, 400, 400) == 50
