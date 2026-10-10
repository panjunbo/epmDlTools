"""验证 epmDlTool.py 的 CLI 解析器能正确解析 bat 调用约定的参数。

测试策略：构造与 download_tools_9062.bat 等价的 argv，调用 build_parser()
验证 Namespace 字段（特别是 hex 字符串如 0x50060000 应原样保留，由
各 command 的 _build_cfg 通过 int(v, 0) 转 int）。

不在此测试硬上做数据通信：smoke_protocol.py 已经覆盖协议层 + MockComm。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from epmDlTool import build_parser


def check(argv, expected):
    args = build_parser().parse_args(argv)
    for k, v in expected.items():
        got = getattr(args, k)
        assert got == v, f"{k}: expected {v!r}, got {got!r}"
    print(f"  PASS  argv={argv}")


def main():
    # 1. bat 第一步：programmer
    check([
        "-flash_port_num", "0",
        "-mode", "programmer",
        "-programmer_bin_addr", "0x50060000",
        "-programmer_bin_path", r"G:\Nanosic\tx232mpw3f\epm_driver\bin\programmer_9062.bin",
        "-comm_type", "uart",
        "-uart_port", "COM20",
    ], {
        "mode": "programmer",
        "flash_port_num": 0,
        "programmer_bin_addr": "0x50060000",
        "uart_port": "COM20",
    })

    # 2. bat 第二步：flashload erase_load（带 BAUD_CHANGE）
    check([
        "-flash_port_num", "0",
        "-mode", "flashload",
        "-flashload_cmd", "erase_load",
        "-erase_load_addr", "0x00",
        "-erase_load_size", "0x8000",
        "-erase_load_bin_path", r"G:\Nanosic\tx232mpw3f\epm_driver\bin\bootloader_packet_9062.bin",
        "-comm_type", "uart",
        "-uart_port", "COM20",
        "-uart_baud", "500000",
        "-target_baud", "3000000",
    ], {
        "mode": "flashload",
        "cmd": "erase_load",
        "erase_load_addr": "0x00",
        "erase_load_size": "0x8000",
        "uart_baud": 500000,
        "target_baud": 3000000,
    })

    # 3. bat DSP 步骤：sync_exit=true
    check([
        "-mode", "flashload",
        "-flashload_cmd", "erase_load",
        "-erase_load_addr", "0x180000",
        "-erase_load_size", "0x80000",
        "-erase_load_bin_path", r"G:\Nanosic\tx232mpw3f\epm_driver\bin\bt_audio_dsp.bin",
        "-comm_type", "uart",
        "-uart_port", "COM20",
        "-uart_baud", "3000000",
        "-sync_exit", "true",
    ], {
        "sync_exit": "true",
        "cmd": "erase_load",
        "erase_load_addr": "0x180000",
    })

    # 4. bat 最后一步：reset
    check([
        "-flash_port_num", "0",
        "-mode", "flashload",
        "-flashload_cmd", "reset",
        "-rdelay", "2",
        "-comm_type", "uart",
        "-uart_port", "COM20",
        "-uart_baud", "3000000",
    ], {
        "cmd": "reset",
        "rdelay": 2,
        "uart_baud": 3000000,
    })

    # 5. programmer_unlock 别名
    check([
        "-mode", "programmer_unlock",
        "-uart_port", "COM20",
        "-comm_type", "uart",
    ], {
        "mode": "programmer_unlock",
        "uart_port": "COM20",
    })

    # 6. flash_read 步骤
    check([
        "-mode", "flashload",
        "-flashload_cmd", "flash_read",
        "-flash_read_addr", "0x180000",
        "-flash_read_size", "0x80000",
        "-comm_type", "uart",
        "-uart_port", "COM20",
        "-uart_baud", "3000000",
    ], {
        "cmd": "flash_read",
        "flash_read_addr": "0x180000",
        "flash_read_size": "0x80000",
    })

    # 7. erase_all 步骤
    check([
        "-mode", "flashload",
        "-flashload_cmd", "erase_all",
        "-comm_type", "uart",
        "-uart_port", "COM20",
    ], {
        "cmd": "erase_all",
    })

    # 8. --dry-run 标志
    check([
        "-mode", "programmer",
        "-uart_port", "COM20",
        "--dry-run",
    ], {
        "dry_run": True,
    })

    # 9. crypt_crc 默认值
    check([
        "-mode", "flashload",
        "-flashload_cmd", "erase_load",
        "-erase_load_addr", "0",
        "-erase_load_size", "0x1000",
        "-erase_load_bin_path", "./bin/flash.bin",
        "-uart_port", "COM5",
    ], {
        "src_crc_init": "0x1234",
        "src_crc_poly": "0x3d65",
        "src_crc_len": "2",
        "dst_crc_poly": "0x3d65",
    })

    # 10. --unlock 标志 + programmer 模式
    check([
        "-mode", "programmer",
        "--unlock",
        "-uart_port", "COM20",
    ], {
        "mode": "programmer",
        "unlock": True,
    })

    print("=== CLI PARSER SMOKE PASSED ===")


if __name__ == "__main__":
    main()