"""ID log 格式串解析（兼容 C printf 子集）。

固件 idlog 模板来自 C 源码，占位符含 %% / %02x / %x / %d / %u / %p / %s 等。
参数按帧里的 uint32 小端字依次消耗。``*`` / ``.*`` 各多吃一个字。
``%s`` 的实参是目标机指针，帧里没有字符串内容，非 0 时显示地址。
浮点按这一个字的 IEEE-754 binary32 解释。
"""
from __future__ import annotations

import re
import struct
from typing import List, Optional, Sequence, Tuple

# 完整匹配：%% 或一个转换说明符。%% 必须写在前面，且不能再套一层 '%'。
_SPEC_RE = re.compile(
    r"(%%|%(?:[-+#0 ]*(?:\d+|\*))?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|L|z|j|t)?"
    r"[diuxXcspeEfgGo])"
)

_FLAGS = "-+#0 "


def count_format_specs(fmt: str) -> int:
    """统计格式串中实际占位符数量（%% 不计，* 不单独计数）。"""
    return sum(1 for spec, _ in _iter_format_specs(fmt) if spec != "%%")


def _iter_format_specs(fmt: str) -> List[Tuple[str, Tuple[int, int]]]:
    out: List[Tuple[str, Tuple[int, int]]] = []
    for m in _SPEC_RE.finditer(fmt):
        out.append((m.group(0), m.span()))
    return out


def _parse_spec(spec: str):
    """拆开一个含 '%' 的说明符。width/prec 为 int、'*' 或 None。"""
    body = spec[1:-1]
    conv = spec[-1]
    flags = ""
    while body and body[0] in _FLAGS:
        flags += body[0]
        body = body[1:]
    for mod in ("ll", "hh", "h", "l", "L", "z", "j", "t"):
        if body.endswith(mod):
            body = body[: -len(mod)]
            break
    width = None
    prec = None
    if "." in body:
        wpart, ppart = body.split(".", 1)
    else:
        wpart, ppart = body, None
    if wpart == "*":
        width = "*"
    elif wpart.isdigit():
        width = int(wpart)
    if ppart is not None:
        if ppart == "*":
            prec = "*"
        elif ppart.isdigit():
            prec = int(ppart)
        else:
            prec = 0
    return flags, width, prec, conv


def _pad(shown: str, width: int, left: bool, zero: bool) -> str:
    if width <= len(shown):
        return shown
    if left:
        return shown.ljust(width)
    if zero and shown[:1] in "+- ":
        return shown[0] + shown[1:].rjust(width - 1, "0")
    if zero:
        return shown.rjust(width, "0")
    return shown.rjust(width)


def _u32(value: int) -> int:
    return int(value) & 0xFFFFFFFF


def _format_int(conv: str, flags: str, width: int, prec: Optional[int], value: int) -> str:
    v = _u32(value)
    left = "-" in flags
    plus = "+" in flags
    space = " " in flags and not plus
    alt = "#" in flags
    zero = "0" in flags and not left and prec is None

    if conv in "di":
        num = v if v < 0x80000000 else v - 0x100000000
        sign = "-" if num < 0 else ("+" if plus else (" " if space else ""))
        digits = str(abs(num))
        if prec is not None:
            digits = "" if prec == 0 and num == 0 else digits.zfill(prec)
        return _pad(sign + digits, width, left, zero)

    if conv == "u":
        digits = str(v)
        if prec is not None:
            digits = "" if prec == 0 and v == 0 else digits.zfill(prec)
        sign = "+" if plus else (" " if space else "")
        return _pad(sign + digits, width, left, zero)

    if conv in "xX":
        hx = format(v, "x" if conv == "x" else "X")
        if prec is not None:
            hx = "" if prec == 0 and v == 0 else hx.zfill(prec)
        prefix = ""
        if alt and v != 0:
            prefix = "0x" if conv == "x" else "0X"
        if zero and prefix and width > len(prefix) + len(hx):
            hx = hx.rjust(width - len(prefix), "0")
        return _pad(prefix + hx, width, left, zero and not prefix)

    if conv == "o":
        digits = format(v, "o")
        if prec is not None:
            digits = "" if prec == 0 and v == 0 else digits.zfill(prec)
        if alt and digits[:1] != "0":
            digits = "0" + digits
        return _pad(digits, width, left, zero)

    return _pad(str(v), width, left, False)


def _format_float(conv: str, flags: str, width: int, prec: Optional[int], value: int) -> str:
    f = struct.unpack("<f", _u32(value).to_bytes(4, "little"))[0]
    p = 6 if prec is None or prec < 0 else prec
    if conv in "eE":
        body = format(f, f".{p}e")
        if conv == "E":
            body = body.replace("e", "E")
    elif conv in "gG":
        body = format(f, f".{p}g")
        if conv == "G":
            body = body.replace("e", "E")
    else:
        body = format(f, f".{p}f")
    if not body.startswith("-"):
        if "+" in flags:
            body = "+" + body
        elif " " in flags:
            body = " " + body
    if "#" in flags and conv in "fF" and "." not in body.split("e")[0].split("E")[0]:
        body += "."
    left = "-" in flags
    zero = "0" in flags and not left
    return _pad(body, width, left, zero)


def _format_one(conv: str, flags: str, width: int, prec: Optional[int], value: int) -> str:
    left = "-" in flags
    if conv == "p":
        return _pad(f"0x{_u32(value):x}", width, left, False)
    if conv == "c":
        b = _u32(value) & 0xFF
        ch = chr(b) if 32 <= b <= 126 else f"\\x{b:02x}"
        return _pad(ch, width, left, False)
    if conv == "s":
        # 帧里只有 char* 的数值，没有目标内存
        shown = "(null)" if _u32(value) == 0 else f"0x{_u32(value):x}"
        if prec is not None and prec >= 0:
            shown = shown[:prec]
        return _pad(shown, width, left, False)
    if conv in "eEfFgG":
        return _format_float(conv, flags, width, prec, value)
    if conv in "diuoxX":
        return _format_int(conv, flags, width, prec, value)
    return _pad(f"0x{_u32(value):x}", width, left, False)


def _take(args: Sequence[int], ai: int) -> Tuple[Optional[int], int]:
    if ai >= len(args):
        return None, ai
    return int(args[ai]), ai + 1


def _resolve_star(token: Union[int, str, None], args: Sequence[int], ai: int):
    """把 '*' 换成参数。返回 (值, 新下标, 是否失败)。负精度按 C 视为未指定。"""
    if token != "*":
        return token, ai, False
    raw, ai = _take(args, ai)
    if raw is None:
        return None, ai, True
    n = _u32(raw)
    if n >= 0x80000000:
        n = n - 0x100000000
    return n, ai, False


def format_id_log_text(fmt: str, args: Sequence[int]) -> str:
    """用 args 顺序填充 C 风格格式串；参数偏多/偏少时尽量格式化。"""
    if not fmt or "%" not in fmt:
        return fmt

    specs = _iter_format_specs(fmt)
    if not specs:
        return fmt
    if not args and "%%" not in fmt:
        return fmt

    out: List[str] = []
    pos = 0
    ai = 0
    for spec, (start, end) in specs:
        out.append(fmt[pos:start])
        pos = end
        if spec == "%%":
            out.append("%")
            continue
        flags, width, prec, conv = _parse_spec(spec)
        width, ai, bad = _resolve_star(width, args, ai)
        if bad:
            out.append(spec)
            continue
        if isinstance(width, int) and width < 0:
            flags += "-"
            width = -width
        prec, ai, bad = _resolve_star(prec, args, ai)
        if bad:
            out.append(spec)
            continue
        if isinstance(prec, int) and prec < 0:
            prec = None
        val, ai = _take(args, ai)
        if val is None:
            out.append(spec)
            continue
        try:
            w = int(width) if isinstance(width, int) else 0
            out.append(_format_one(conv, flags, w, prec if isinstance(prec, int) else None, val))
        except Exception:
            out.append(spec)
    out.append(fmt[pos:])
    return "".join(out)


def format_id_log_head(level: str, timestamp: int) -> str:
    """ID 与固件时间戳合成一个标记。时间戳单位是 1/16 us，显示为微秒。"""
    us = (int(timestamp) & 0xFFFFFFFF) // 16
    return f"[{level}:{us}us]"


def format_id_log_line(entry, text: str) -> str:
    """单条 ID log 的正文。模块号不显示。

    hexdump 帧在格式串（或 log_id）后面附上数据字节。
    """
    if getattr(entry, "is_hexdump", False):
        body = format_id_log_text(text, entry.args) if text else f"log_id=0x{entry.log_id:06x}"
        dump = entry.data.hex(" ") if entry.data else ""
        if dump:
            return f"{body} | {dump}"
        return body
    if text:
        return format_id_log_text(text, entry.args)
    return f"log_id=0x{entry.log_id:06x}"
