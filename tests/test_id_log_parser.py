# -*- coding: utf-8 -*-
"""IdLogParser 回归测试：帧剥离 + 文本透传。

用与固件 epm_log_frame.c 相同的格式构造帧，混入 ASCII 文本行，
模拟 LogSerial 按 \\n 切分、recv 任意边界切块的情况，验证：
1. 所有帧被解析（无 bad_crc）
2. 文本完整、按序透传（旧行为会吞掉每行前 n-3 字节）
3. 无字节重复显示（旧行为半帧/整块会回吐成乱码）
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from utils.epm_id_log_parser import IdLogParser, _crc8


def make_frame(ftype: int, payload: bytes) -> bytes:
    plen = len(payload) + 1
    return (b"\x8e\xa5" + bytes([ftype]) + plen.to_bytes(2, "little")
            + payload + bytes([_crc8(payload)]))


def make_id_log(cpu, ts, log_id, args):
    payload = bytes([cpu, 0]) + ts.to_bytes(4, "little") + log_id.to_bytes(3, "little")
    for a in args:
        payload += a.to_bytes(4, "little")
    return make_frame(0xC2, payload)


TEXTS = [
    b"ST",
    b"JUMP",
    b"this is bootloader test",
    b"jump to app:0x00000000",
    b"[FlashDB] FlashDB V2.1.1 is initialize success.",
    b"ERR: mfr_nv: record region is not_formatted",
]

FRAMES = [
    make_id_log(1, 0x00057178, 0x300000, [0x14]),
    make_id_log(0, 0x00363C58, 0x300001, []),
    make_id_log(0, 0x0037AC58, 0x300304, [0x00005D08]),
    make_frame(0xC3, bytes([0, 0]) + (0x1234).to_bytes(4, "little")
               + (0x300400).to_bytes(3, "little") + b"hexdump data"),
    make_id_log(2, 0x0000ABCD, 0x300999, [1, 2, 3, 4]),
    # args 里嵌 0x0A/0x0D：LogSerial 按 \n 切分时帧会被切开
    make_id_log(0, 0x003750C8, 0x300139, [0x0A0D0A0D, 0x00000000]),
]

EXPECTED_TEXT = b"\n".join(TEXTS) + b"\n"


def build_stream() -> bytes:
    """文本行 + 帧交错的字节流（文本带 \n，帧紧随其后）"""
    out = bytearray()
    for i, t in enumerate(TEXTS):
        out += t + b"\n"
        out += FRAMES[i]
    return bytes(out)


def run_case(name, chunks, expected_frames=None, expected_text=None):
    expected_frames = FRAMES if expected_frames is None else expected_frames
    expected_text = EXPECTED_TEXT if expected_text is None else expected_text
    p = IdLogParser()
    all_entries, all_text = [], bytearray()
    for c in chunks:
        entries, text = p.feed(c)
        all_entries.extend(entries)
        all_text.extend(text)
    assert p.stats["frames_bad_crc"] == 0, f"{name}: bad_crc={p.stats['frames_bad_crc']}"
    assert len(all_entries) == len(expected_frames), \
        f"{name}: 期望 {len(expected_frames)} 帧，实得 {len(all_entries)}"
    assert bytes(all_text) == expected_text, \
        f"{name}: 文本透传不完整\n  期望: {expected_text!r}\n  实得: {bytes(all_text)!r}"
    print(f"PASS {name}: frames={len(all_entries)} text={len(all_text)}B")


def main():
    data = build_stream()

    # 校验帧内容解析正确（cpu/ts/log_id/args/hexdump）
    p = IdLogParser()
    entries, _ = p.feed(data)
    assert entries[0].cpu_id == 1 and entries[0].log_id == 0x300000
    assert entries[1].cpu_id == 0 and entries[1].timestamp == 0x00363C58
    assert entries[2].args == [0x00005D08]
    assert entries[3].is_hexdump and entries[3].data == b"hexdump data"
    assert entries[4].args == [1, 2, 3, 4]
    assert entries[5].args == [0x0A0D0A0D, 0]
    print(f"PASS 帧内容: frames={len(entries)}")

    # 尾部不足 4 字节的参数丢弃；短 payload 仍给出条目而不是抛掉整帧
    short_args = make_frame(
        0xC2,
        bytes([0, 3]) + (0x11).to_bytes(4, "little")
        + (0x300010).to_bytes(3, "little") + (0x22).to_bytes(4, "little") + b"\xAB\xCD",
    )
    p = IdLogParser()
    got, _ = p.feed(short_args)
    assert len(got) == 1 and got[0].args == [0x22] and got[0].module_id == 3
    tiny = make_frame(0xC2, bytes([1, 2, 3]))
    p = IdLogParser()
    got, _ = p.feed(tiny)
    assert len(got) == 1 and got[0].cpu_id == 1 and got[0].log_id == 0
    print("PASS 短参数/短帧")

    # 用例1：整段一次 feed
    run_case("单次feed", [data])

    # 用例2：按 \n 切分（模拟 LogSerial._split_lines：行文本一块，帧粘连随行）
    chunks = []
    i = 0
    while i < len(data):
        nl = data.find(b"\n", i)
        if nl < 0:
            chunks.append(data[i:])
            break
        # \n 后面若是帧（8e a5），把帧也带上直到帧尾或下一个 \n
        j = nl + 1
        chunks.append(data[i:j])
        i = j
    run_case("按\\n切分", chunks)

    # 用例3：逐字节 feed（最极端边界）
    run_case("逐字节", [data[i:i + 1] for i in range(len(data))])

    # 用例4：随机块大小
    random.seed(42)
    chunks, i = [], 0
    while i < len(data):
        sz = random.randint(1, 17)
        chunks.append(data[i:i + sz])
        i += sz
    run_case("随机块", chunks)

    # 用例5：文本里出现假 SYNC (8E A5 + 合法 LEN) 能恢复透传
    fake = b"prefix\x8e\xa5\x40\x00" + b"A" * 63 + b"suffix\n"
    p = IdLogParser()
    entries, text = p.feed(fake)
    assert len(entries) == 0
    assert b"prefix" in text and b"suffix" in text, f"text={text!r}"
    print(f"PASS 假SYNC恢复: text透传{len(text)}B")

    # 用例6：LogSerial._split_lines → IdLogParser 全链路。
    # 帧 payload 含 0x0A/0x0D 时，按行切分不能丢分隔符字节
    # （旧行为 line=buf[:idx] 把帧内的 0x0A 当分隔符删掉 → 帧缺字节 → bad_crc）
    from communication.log_serial import LogSerial

    frames_with_nl = [
        make_id_log(0, 0x0037AF08, 0x0A0300, [0x7F000018]),   # log_id 首字节 0x0A
        make_id_log(0, 0x0037B5B0, 0x30030C, [0x0D, 0x01]),   # args 含 0x0D
        make_id_log(1, 0x00427400, 0x300001, [0x000A0D0A]),   # ts 后紧跟 0x0A
    ]
    stream2 = bytearray()
    expected = bytearray()
    for i, f in enumerate(frames_with_nl):
        t = b"line%d" % i
        stream2 += t + b"\r\n"
        expected += t + b"\r\n"
        stream2 += f
        expected += b""  # 帧剥离，不透传
    data2 = bytes(stream2)

    # 用 LogSerial 真实的 _split_lines 切行（分隔符保留在行内），
    # 再模拟 recv 按 7B 一块到达
    chunks, leftover = [], bytearray()
    for i in range(0, len(data2), 7):
        leftover += data2[i:i + 7]
        lines, leftover = LogSerial._split_lines(leftover)
        chunks.extend(lines)
    if leftover:
        # 末尾补 \n 冲刷残留（真实场景下后续数据会继续到来）
        lines, leftover = LogSerial._split_lines(leftover + b"\n")
        chunks.extend(lines)
        expected += b"\n"

    p = IdLogParser()
    all_entries, all_text = [], bytearray()
    for c in chunks:
        entries, text = p.feed(c)
        all_entries.extend(entries)
        all_text.extend(text)
    assert p.stats["frames_bad_crc"] == 0, f"bad_crc={p.stats['frames_bad_crc']}"
    assert len(all_entries) == len(frames_with_nl), \
        f"期望 {len(frames_with_nl)} 帧，实得 {len(all_entries)}"
    assert all_entries[0].log_id == 0x0A0300, hex(all_entries[0].log_id)
    assert all_entries[1].args == [0x0D, 0x01]
    assert bytes(all_text) == bytes(expected), f"text={bytes(all_text)!r}"
    print(f"PASS LogSerial全链路(帧内含0x0A/0x0D): frames={len(all_entries)}")

    print("\n全部通过")


if __name__ == "__main__":
    main()
