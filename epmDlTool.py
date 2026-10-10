import argparse
import ctypes
import sys

from utils.app_paths import config_ini_path, init_ui_exe_layout, is_frozen_exe
from utils.config_manager import ConfigManager
from utils.logger import make_default_logger


def _enable_dpi_awareness():
    """窗口使用显示器真实像素，避免 150% 时被系统整窗拉伸变糊、逻辑分辨率变小。

    界面字号仍固定在 100%（见 ui.theme.normalize_tk_scaling）。这里只用
    Per-Monitor V2，让系统主题和 Tk 画在同一套像素上。
    ``SetProcessDpiAwareness(2)`` 是旧的 V1，150% 时主题和字体会错位。
    """
    if sys.platform != "win32":
        return
    try:
        user32 = ctypes.windll.user32
        user32.SetProcessDpiAwarenessContext.restype = ctypes.c_bool
        user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = (HANDLE)-4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        # Win8.1：系统 DPI 感知（主题和字体至少按同一个 DPI）
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# CLI 顶层解析器：与 download_tools_9062.bat 的调用约定对齐（flat args）。
# 每个调用对应 bat 中的一次 DOWNLOAD_EXE 执行：programmer / programmer_unlock / flashload。
# bat 中的 addr/size 形如 0x50060000，由各 command 的 _build_cfg 通过 int(v, 0) 兼容。
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="epmDlTool",
        description=(
            "EPM 多功能烧录工具（CLI / GUI）—— download_tools.exe 的 Python 替换\n"
            "调用约定与 bat 完全一致：-mode programmer|programmer_unlock|flashload "
            "+ 串口 + bin 路径等"
        ),
    )
    # 通用
    p.add_argument("--ui", action="store_true", help="启动图形界面")
    p.add_argument("--config", default=None, help="配置文件路径")
    p.add_argument("--dry-run", action="store_true",
                   help="只解析参数并打印，不连硬件")

    # 主模式（bat -mode）
    p.add_argument("-mode",
                   choices=["programmer", "programmer_unlock", "flashload"],
                   default=None,
                   help="执行模式（bat 用法：programmer / programmer_unlock / flashload）")

    # 通信参数（bat 用法：-comm_type uart -uart_port COM20 -uart_baud 500000）
    p.add_argument("-comm_type", choices=["uart", "iic", "spi"], default="uart",
                   help="通信类型（本版本仅 uart 实现，bat 也只用 uart）")
    p.add_argument("-uart_port", default="COM5",
                   help="UART 端口（bat 用法：COM20 / COM5 等）")
    p.add_argument("-uart_baud", type=int, default=500000,
                   help="UART 波特率（bat 用法：500000 / 3000000）")

    # programmer / programmer_unlock 阶段
    p.add_argument("-programmer_bin_path", default="./bin/programmer.bin",
                   help="programmer.bin 路径")
    p.add_argument("-programmer_bin_addr", default="0x50540000",
                   help="programmer.bin 烧录地址（hex 或十进制）")
    p.add_argument("--unlock", action="store_true",
                   help="仅发送 UNLOCK 命令（-mode programmer 下的便捷别名）")

    # flashload 共享
    p.add_argument("-flash_port_num", type=int, default=0,
                   help="flash 端口号（0 或 1）")
    p.add_argument("-target_baud", type=int, default=0,
                   help="目标波特率（>0 协议会先 BAUD_CHANGE；0 不切）")
    p.add_argument("-sync_exit", choices=["true", "false"], default="false",
                   help="最后一帧是否发 SYNC 后再退出")
    p.add_argument("-rdelay", type=int, default=0,
                   help="reset 延时（reset 命令专用）")
    p.add_argument("-flashload_cmd", dest="cmd",
                   choices=["erase_all", "erase", "erase_load",
                            "erase_load_crypt", "flash_read", "flash_xip_read",
                            "reset"],
                   default=None,
                   help="flashload 子命令（erase 系列 / flash_read 系列）")

    # erase
    p.add_argument("-erase_addr", default="0x0",
                   help="erase 起始地址（4K 对齐）")
    p.add_argument("-erase_size", default="0x100000",
                   help="erase 大小")
    # erase_load / erase_load_crypt
    p.add_argument("-erase_load_addr", default="0x0",
                   help="erase_load 起始地址")
    p.add_argument("-erase_load_size", default="0x100000",
                   help="erase_load 大小")
    p.add_argument("-erase_load_bin_path", dest="flashload_bin_path",
                   default="./bin/flash.bin",
                   help="erase_load 要烧录的 bin 路径")
    # flash_read / flash_xip_read
    p.add_argument("-flash_read_addr", default="0x0",
                   help="flash_read 起始地址")
    p.add_argument("-flash_read_size", default="0x100000",
                   help="flash_read 大小")

    # CRC（crypt 模式）
    p.add_argument("-src_crc_init", default="0x1234")
    p.add_argument("-src_crc_poly", default="0x3d65")
    p.add_argument("-src_crc_len", default="2")
    p.add_argument("-src_crc_ref_in", default="0")
    p.add_argument("-src_crc_ref_out", default="1")
    p.add_argument("-src_crc_xor_out", default="0xffff")
    p.add_argument("-dst_crc_init", default="0")
    p.add_argument("-dst_crc_poly", default="0x3d65")
    return p


def _mode_dispatch():
    from commands import FlashloadCommand, ProgrammerCommand
    return {
        "programmer":        (ProgrammerCommand, {}),
        "programmer_unlock": (ProgrammerCommand, {"unlock": True}),
        "flashload":         (FlashloadCommand, {}),
    }


def _normalize_argv(argv):
    """兼容旧写法：download_tools.exe flashload ... → -mode flashload ..."""
    if not argv:
        return argv
    known = {"programmer", "programmer_unlock", "flashload"}
    if argv[0] in known:
        return ["-mode", argv[0]] + list(argv[1:])
    return argv


def run_cli(args, cfg, logger):
    if args.mode is None:
        print(
            "error: must specify -mode in {programmer, programmer_unlock, flashload}",
            file=sys.stderr,
        )
        return 2
    dispatch = _mode_dispatch()
    if args.mode not in dispatch:
        print(f"error: unknown -mode: {args.mode!r}", file=sys.stderr)
        return 2
    cls, overrides = dispatch[args.mode]
    cmd = cls(args, cfg, logger, serial_manager=None)
    # dispatch override（如 programmer_unlock → unlock=True）写到 args，
    # 这样 command 内部的 getattr(args, "unlock") 也能读到
    for k, v in overrides.items():
        setattr(args, k, v)
    try:
        return cmd.execute()
    except RuntimeError as e:
        logger.error(str(e))
        return 1
    except ValueError as e:
        logger.error(str(e))
        return 1


def run_ui(cm, logger, cfg=None):
    try:
        from ui.app import EpmDlApp
    except ImportError as e:
        print(f"UI 模块加载失败: {e}", file=sys.stderr)
        return 2
    _enable_dpi_awareness()
    app = EpmDlApp(cfg=cfg if cfg is not None else cm.read(), cm=cm, logger=logger)
    app.mainloop()
    return 0


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = _normalize_argv(argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    config_path = args.config
    if args.ui and is_frozen_exe() and config_path is None:
        init_ui_exe_layout()
        config_path = config_ini_path()

    cm = ConfigManager(config_path)
    cm.ensure_defaults()
    cfg = cm.read()

    logger = make_default_logger()

    if args.ui:
        # UI 路径不加载 commands/protocol，缩短启动时间
        return run_ui(cm, logger, cfg)
    if args.mode is None:
        parser.print_help()
        return 2
    return run_cli(args, cfg, logger)


if __name__ == "__main__":
    sys.exit(main())