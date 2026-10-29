# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.app import h_sash_for_ratio, initial_h_sash


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


# --- 分隔条：用户拖出来的比例必须能站住，不能被"弹回"默认位置 ---

def test_ratio_is_kept_as_is():
    # 用户拖到哪就是哪：不再按右栏自身需要的宽度夹紧
    assert h_sash_for_ratio(1920, 0.62) == 1190


def test_ratio_is_kept_when_the_window_changes():
    # 同一个比例在别的窗口宽度下摆回去，比例不变
    assert h_sash_for_ratio(1600, 0.62) == 992
    assert h_sash_for_ratio(1280, 0.62) == 793


def test_ratio_can_go_beyond_the_panels_own_width():
    # 用户就是要窄的右栏 / 窄的左栏：照给，不夹回"默认位置"
    assert h_sash_for_ratio(1920, 0.85) == 1632
    assert h_sash_for_ratio(1920, 0.05) == 96


def test_ratio_is_clamped_to_the_window_only():
    # 只有物理边界：不能跑到窗口外面去
    assert h_sash_for_ratio(1920, 1.5) == 1920
    assert h_sash_for_ratio(1920, -0.2) == 0
    assert h_sash_for_ratio(0, 0.5) == 0


if __name__ == "__main__":
    test_keeps_sixty_percent_when_right_fits()
    test_gives_right_its_width_at_high_scale()
    test_prefers_toolbar_when_both_cannot_fit()
    test_small_window_splits_in_half()
    test_ratio_is_kept_as_is()
    test_ratio_is_kept_when_the_window_changes()
    test_ratio_can_go_beyond_the_panels_own_width()
    test_ratio_is_clamped_to_the_window_only()
    print("全部通过")
