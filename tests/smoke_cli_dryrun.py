"""验证 epmDlTool.py CLI 端到端 dispatch：每个 mode 在 --dry-run 下不应碰硬件。
不依赖真实串口；纯 dispatch / cfg 构建 / 日志路径校验。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from epmDlTool import build_parser, run_cli
from utils.config_manager import ConfigManager
from utils.logger import make_default_logger


def run(argv):
    args = build_parser().parse_args(argv)
    cm = ConfigManager()
    cm.ensure_defaults()
    cfg = cm.read()
    return run_cli(args, cfg, make_default_logger())


def main():
    # 1. programmer dry-run
    rc = run([
        "-mode", "programmer",
        "--dry-run",
        "-uart_port", "COM20",
        "-programmer_bin_path", "./bin/programmer.bin",
        "-programmer_bin_addr", "0x50060000",
    ])
    print(f"  programmer rc={rc}")
    assert rc == 0

    # 2. programmer_unlock dry-run
    rc = run([
        "-mode", "programmer_unlock",
        "--dry-run",
        "-uart_port", "COM20",
    ])
    print(f"  programmer_unlock rc={rc}")
    assert rc == 0

    # 3. flashload erase_load dry-run
    rc = run([
        "-mode", "flashload",
        "--dry-run",
        "-flashload_cmd", "erase_load",
        "-erase_load_addr", "0x00",
        "-erase_load_size", "0x8000",
        "-erase_load_bin_path", "./bin/flash.bin",
        "-uart_port", "COM20",
        "-uart_baud", "500000",
        "-target_baud", "3000000",
    ])
    print(f"  flashload erase_load rc={rc}")
    assert rc == 0

    # 4. flashload erase dry-run
    rc = run([
        "-mode", "flashload",
        "--dry-run",
        "-flashload_cmd", "erase",
        "-erase_addr", "0x100000",
        "-erase_size", "0x100000",
        "-uart_port", "COM20",
    ])
    print(f"  flashload erase rc={rc}")
    assert rc == 0

    # 5. flashload flash_read dry-run
    rc = run([
        "-mode", "flashload",
        "--dry-run",
        "-flashload_cmd", "flash_read",
        "-flash_read_addr", "0x0",
        "-flash_read_size", "0x100000",
        "-uart_port", "COM20",
    ])
    print(f"  flashload flash_read rc={rc}")
    assert rc == 0

    # 6. flashload reset dry-run
    rc = run([
        "-mode", "flashload",
        "--dry-run",
        "-flashload_cmd", "reset",
        "-rdelay", "2",
        "-uart_port", "COM20",
    ])
    print(f"  flashload reset rc={rc}")
    assert rc == 0

    # 7. flashload erase_all dry-run
    rc = run([
        "-mode", "flashload",
        "--dry-run",
        "-flashload_cmd", "erase_all",
        "-uart_port", "COM20",
    ])
    print(f"  flashload erase_all rc={rc}")
    assert rc == 0

    # 8. 缺 -mode 时应报错
    rc = run(["-uart_port", "COM20"])
    print(f"  no-mode rc={rc} (expect 2)")
    assert rc == 2

    # 9. 错误的 -mode 值会被 argparse 拦截（在 run() 之前）
    try:
        run(["-mode", "bogus", "-uart_port", "COM20"])
        rc = 0  # argparse 抛 SystemExit，但 run_cli 不应该被调到
    except SystemExit as e:
        rc = e.code
    print(f"  bad-mode rc={rc} (expect !=0)")
    assert rc != 0

    print("=== CLI DRY-RUN SMOKE PASSED ===")


if __name__ == "__main__":
    main()