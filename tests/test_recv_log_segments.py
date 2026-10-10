# -*- coding: utf-8 -*-
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ui.app import replay_host_ts, trim_log_segments


def test_replay_keeps_live_timestamp_and_skips_loaded_file():
    assert replay_host_ts("[12:00:01.234] ", True) == "[12:00:01.234] "
    assert replay_host_ts("[12:00:01.234] ", False) == ""
    assert replay_host_ts(None, True) is None


def test_trim_drops_whole_lines_and_keeps_later_timestamps():
    segs = [
        (b"aaaa\n", "[1] "),
        (b"bbbb\n", "[2] "),
        (b"cccc\n", "[3] "),
    ]
    new_segs, new_len, trimmed = trim_log_segments(segs, 15, 10)
    assert trimmed
    assert new_segs == [(b"cccc\n", "[3] ")]
    assert new_len == 5


def test_trim_splits_segment_and_keeps_its_timestamp():
    segs = [(b"aaaa\nbbbb\ncccc\n", "[1] ")]
    new_segs, new_len, trimmed = trim_log_segments(segs, 15, 10)
    assert trimmed
    assert new_segs == [(b"cccc\n", "[1] ")]
    assert new_len == 5


def test_trim_cut_can_land_in_a_later_segment():
    segs = [(b"abcdefghij", "[1] "), (b"KL\nMNOP", "[2] ")]
    new_segs, new_len, trimmed = trim_log_segments(segs, 17, 10)
    assert trimmed
    assert new_segs == [(b"MNOP", "[2] ")]
    assert new_len == 4


def test_trim_without_newline_cuts_at_overflow():
    segs = [(b"abcdefghij", "[1] "), (b"KLMNOP", "[2] ")]
    new_segs, new_len, trimmed = trim_log_segments(segs, 16, 10)
    assert trimmed
    assert new_segs == [(b"ghij", "[1] "), (b"KLMNOP", "[2] ")]
    assert new_len == 10


def test_trim_noop_when_under_limit():
    segs = [(b"aa\n", "[1] ")]
    new_segs, new_len, trimmed = trim_log_segments(segs, 3, 10)
    assert not trimmed
    assert new_segs == segs
    assert new_len == 3


if __name__ == "__main__":
    test_replay_keeps_live_timestamp_and_skips_loaded_file()
    test_trim_drops_whole_lines_and_keeps_later_timestamps()
    test_trim_splits_segment_and_keeps_its_timestamp()
    test_trim_cut_can_land_in_a_later_segment()
    test_trim_without_newline_cuts_at_overflow()
    test_trim_noop_when_under_limit()
    print("全部通过")
