# -*- coding: utf-8 -*-
"""Configurable programmer idle timeout:
CLI argument > ini [flashload].prog_timeout > default 10s."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from commands.programmer import ProgrammerCommand
from epmDlTool import build_parser
from protocol import ProgrammerProtocol, parse_idle_timeout
from utils.logger import Logger


def test_parse_idle_timeout():
    assert parse_idle_timeout(None) == 10.0
    assert parse_idle_timeout("30") == 30.0
    assert parse_idle_timeout(30) == 30.0
    assert parse_idle_timeout("0.5") == 0.5
    assert parse_idle_timeout("abc") == 10.0
    assert parse_idle_timeout("") == 10.0
    # <=0 counts as invalid (0 would trip the timeout on the first tick)
    assert parse_idle_timeout("0") == 10.0
    assert parse_idle_timeout("-5") == 10.0
    assert parse_idle_timeout(None, 3.0) == 3.0


def _proto(cfg):
    # The constructor touches no hardware, comm/bin_parse can stay None
    return ProgrammerProtocol(None, None, cfg, Logger())


def test_protocol_default_when_not_configured():
    assert _proto({}).idle_timeout == 10.0


def test_protocol_reads_cfg_key():
    assert _proto({"prog_timeout": "25"}).idle_timeout == 25.0
    assert _proto({"prog_timeout": 25}).idle_timeout == 25.0


def test_protocol_falls_back_on_bad_value():
    assert _proto({"prog_timeout": "abc"}).idle_timeout == 10.0
    assert _proto({"prog_timeout": "0"}).idle_timeout == 10.0


def test_protocol_keeps_legacy_key():
    assert _proto({"handshake_timeout": "7"}).idle_timeout == 7.0


def _cmd(args, cfg):
    return ProgrammerCommand(args, cfg, Logger())


def test_resolve_timeout_default():
    assert _cmd(argparse.Namespace(), {})._resolve_timeout() == 10.0


def test_resolve_timeout_from_ini():
    cfg = {"flashload": {"prog_timeout": "20"}}
    assert _cmd(argparse.Namespace(), cfg)._resolve_timeout() == 20.0


def test_cfg_top_level_key_wins_over_ini():
    cfg = {"prog_timeout": "15", "flashload": {"prog_timeout": "20"}}
    assert _cmd(argparse.Namespace(), cfg)._resolve_timeout() == 15.0


def test_cli_arg_wins_over_ini():
    cfg = {"flashload": {"prog_timeout": "20"}}
    args = argparse.Namespace(prog_timeout=45.0)
    assert _cmd(args, cfg)._resolve_timeout() == 45.0


def test_invalid_ini_falls_back_to_default():
    cfg = {"flashload": {"prog_timeout": "abc"}}
    assert _cmd(argparse.Namespace(), cfg)._resolve_timeout() == 10.0


def test_cli_parser_accepts_prog_timeout():
    args = build_parser().parse_args(
        ["-mode", "programmer", "-prog_timeout", "45"])
    assert args.prog_timeout == 45.0
    # Not specified -> None, so the ini / default value is used
    assert build_parser().parse_args(["-mode", "programmer"]).prog_timeout is None


def test_external_ini_edit_takes_effect_after_reload():
    """外部改 ini → 重读后新值生效（界面在下载前 / 保存前会重读）。"""
    import os
    import tempfile
    from utils.config_manager import ConfigManager

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "config.ini")
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("[flashload]\nprog_timeout = 10\n")
        cm = ConfigManager(path)
        cm.ensure_defaults()
        args = argparse.Namespace(prog_timeout=None)

        def resolved():
            return ProgrammerCommand(args, dict(cm.read()), Logger())._resolve_timeout()

        assert resolved() == 10.0

        # 外部用编辑器改了 ini
        with open(path, "w", encoding="utf-8") as fp:
            fp.write("[flashload]\nprog_timeout = 55\n")

        # 内存里还是启动时的快照 → 旧值
        assert resolved() == 10.0
        # 重读磁盘 → 新值生效
        cm.reload()
        assert resolved() == 55.0
