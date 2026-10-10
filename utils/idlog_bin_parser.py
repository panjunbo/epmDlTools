"""idlog_*.bin 解析器

参考 epm_driver/components/epm_id_log/makefile.inc、link.lds 与
EpmDevTool.exe（--id-log-base / --id-log-message-bin）约定：

  .to_be_stripped_from_bin section 起始地址见 idlog_addr_MCU_*.txt 中 __idlog_start
  （平台默认 0x30000000）；每个字符串 256 字节对齐。

bin 文件即该 section 的 dump：
  bin_offset -> address = bin_offset + base_addr
  log_id = address >> 8 = (bin_offset // 256) + (base_addr >> 8)
"""

from __future__ import annotations

import os
import re
from typing import Dict, Optional, Tuple

# 与 link.lds / EpmDevTool --id-log-base 默认一致
IDLOG_BASE_ADDR = 0x30000000

_CPU_TOKEN = {
    "mcu_a": "MCU_A",
    "mcu_b": "MCU_B",
    "mcu_c": "MCU_C",
}


def parse_idlog_base_addr(path: Optional[str]) -> Optional[int]:
    """从 idlog_addr_MCU_*.txt 解析 __idlog_start 地址。

    文件格式示例::

        30000000 D __idlog_start
        3005b000 D __idlog_end
    """
    if not path or not os.path.isfile(path):
        return None
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] == "__idlog_start":
            try:
                return int(parts[0], 16)
            except ValueError:
                return None
    return None


def find_idlog_base_addr_for(bin_dir: Optional[str], token: str) -> Optional[int]:
    """在目录中查找文件名含 token（如 MCU_A）的 idlog_addr_*.txt。"""
    if not bin_dir or not token or not os.path.isdir(bin_dir):
        return None
    token_u = token.upper()
    for name in sorted(os.listdir(bin_dir)):
        if not name.startswith("idlog_addr_") or not name.endswith(".txt"):
            continue
        if token_u not in name.upper():
            continue
        base = parse_idlog_base_addr(os.path.join(bin_dir, name))
        if base is not None:
            return base
    return None


def find_idlog_base_addr(bin_dir: Optional[str], token: Optional[str] = None) -> int:
    """查找 __idlog_start。指定 token 时只匹配该核；否则用目录中第一份 addr 文件。"""
    if token:
        found = find_idlog_base_addr_for(bin_dir, token)
        return found if found is not None else IDLOG_BASE_ADDR
    if not bin_dir or not os.path.isdir(bin_dir):
        return IDLOG_BASE_ADDR
    for name in sorted(os.listdir(bin_dir)):
        if not name.startswith("idlog_addr_") or not name.endswith(".txt"):
            continue
        base = parse_idlog_base_addr(os.path.join(bin_dir, name))
        if base is not None:
            return base
    return IDLOG_BASE_ADDR


def resolve_idlog_base_addrs(
    bin_dir: Optional[str],
    paths: Optional[Dict[str, Optional[str]]] = None,
    per_cpu: Optional[Dict[str, int]] = None,
    shared: int = 0,
) -> Dict[str, int]:
    """mcu_a/b/c 各自的 idlog 基址。

    优先级：该核 config 覆盖值、bin 目录里对应的 idlog_addr_*.txt、
    bin 文件所在目录的 addr 文件、共享 base_addr、平台默认值。
    """
    paths = paths or {}
    per_cpu = per_cpu or {}
    out: Dict[str, int] = {}
    for key, token in _CPU_TOKEN.items():
        override = int(per_cpu.get(key) or 0)
        if override:
            out[key] = override
            continue
        found = find_idlog_base_addr_for(bin_dir, token)
        if found is None:
            p = paths.get(key)
            if p:
                found = find_idlog_base_addr_for(os.path.dirname(os.path.abspath(p)), token)
        if found is not None:
            out[key] = found
            continue
        out[key] = int(shared) if shared else IDLOG_BASE_ADDR
    return out


def parse_idlog_bin(path: Optional[str], base_addr: int = IDLOG_BASE_ADDR) -> Dict[int, str]:
    """从 idlog_*.bin 解析 {log_id: format_string}。"""
    result: Dict[int, str] = {}
    if not path or not os.path.isfile(path):
        return result
    try:
        size = os.path.getsize(path)
        if size <= 0:
            return result
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return result
    if len(data) % 256 != 0:
        data = data[: len(data) // 256 * 256]
    base_log_id = int(base_addr) >> 8
    for i in range(0, len(data), 256):
        chunk = data[i:i + 256]
        nul = chunk.find(b"\x00")
        if nul <= 0:
            continue
        s = chunk[:nul]
        if len(s) < 4 or not re.search(rb"[\x20-\x7e]", s):
            continue
        try:
            text = s.decode("utf-8", errors="replace").rstrip()
        except Exception:
            continue
        if not text:
            continue
        result[(i // 256) + base_log_id] = text
    return result


def _pick_idlog_file(bin_dir: str, token: str, preferred_name: Optional[str]) -> Optional[str]:
    """按 EpmDevTool 规则：文件名含 MCU_A/B/C；优先 config 指定名，否则选最大非空文件。"""
    preferred = None
    if preferred_name:
        p = os.path.join(bin_dir, preferred_name)
        if os.path.isfile(p) and os.path.getsize(p) > 0:
            return p
        preferred = preferred_name.lower()

    candidates: list[Tuple[int, str]] = []
    token_u = token.upper()
    for name in os.listdir(bin_dir):
        low = name.lower()
        if "idlog" not in low and not low.startswith("idlog"):
            continue
        if token_u not in name.upper():
            continue
        if not name.lower().endswith(".bin"):
            continue
        full = os.path.join(bin_dir, name)
        try:
            sz = os.path.getsize(full)
        except OSError:
            continue
        if sz <= 0:
            continue
        # 完全匹配 config 文件名（不含路径）优先
        rank = 2 if preferred and low == preferred else 1
        candidates.append((rank * 10_000_000 + sz, full))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def resolve_idlog_bin_paths(
    bin_dir: Optional[str],
    names: Dict[str, Optional[str]],
    manual: Optional[Dict[str, Optional[str]]] = None,
) -> Dict[str, Optional[str]]:
    """解析 mcu_a/b/c -> idlog bin 绝对路径（对齐 tools/bin + EpmDevTool）。"""
    out: Dict[str, Optional[str]] = {k: None for k in _CPU_TOKEN}
    manual = manual or {}
    if not bin_dir or not os.path.isdir(bin_dir):
        for key in _CPU_TOKEN:
            p = manual.get(key)
            out[key] = p if p and os.path.isfile(p) else None
        return out
    for key, token in _CPU_TOKEN.items():
        if manual.get(key) and os.path.isfile(manual[key]):
            out[key] = manual[key]
            continue
        out[key] = _pick_idlog_file(bin_dir, token, names.get(key))
    return out


def merge_idlog_maps(*maps: Dict[int, str]) -> Dict[int, str]:
    """合并多个映射：后面的覆盖前面的。"""
    out: Dict[int, str] = {}
    for m in maps:
        if m:
            out.update(m)
    return out
