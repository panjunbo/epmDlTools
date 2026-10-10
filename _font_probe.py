import struct
import zlib
import tkinter as tk
from tkinter import ttk
from pathlib import Path


def save_png(root, path):
    import ctypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd = root.winfo_id()
    w = root.winfo_width()
    h = root.winfo_height()
    hdc = user32.GetDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    hbmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, hbmp)
    user32.PrintWindow(hwnd, mem, 2)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32),
            ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32),
            ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16),
            ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32),
            ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32),
            ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)

    raw = buf.raw
    rows = []
    for y in range(h):
        row = bytearray([0])
        src = raw[y * w * 4:(y + 1) * w * 4]
        for x in range(w):
            b, g, r, _a = src[x * 4:x * 4 + 4]
            row += bytes((r, g, b))
        rows.append(bytes(row))

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
        + chunk(b"IEND", b"")
    )
    Path(path).write_bytes(png)
    print("saved", path, w, h)


def main():
    root = tk.Tk()
    root.geometry("700x280+40+40")
    style = ttk.Style(root)
    style.theme_use("vista")
    cur = style.theme_use()
    style.theme_use("clam")
    clam_layout = style.layout("TCombobox")
    style.theme_use(cur)
    style.layout("Port.TCombobox", clam_layout)
    style.configure("Port.TCombobox", font=("Segoe UI", 8))
    style.map(
        "Port.TCombobox",
        fieldbackground=[("readonly", "white")],
        selectbackground=[("readonly", "white")],
        selectforeground=[("readonly", "black")],
    )

    text = "COM9 USB-Enhanced-SERIAL CH343"
    v = tk.StringVar(value=text)
    ttk.Label(root, text="1 vista width=28").pack(anchor="w")
    ttk.Combobox(root, textvariable=v, values=[text], width=28, state="readonly").pack(
        anchor="w", padx=8, pady=2
    )
    ttk.Label(root, text="2 clam-layout font 8 width=36").pack(anchor="w")
    ttk.Combobox(
        root,
        textvariable=v,
        values=[text],
        width=36,
        state="readonly",
        style="Port.TCombobox",
    ).pack(anchor="w", padx=8, pady=2)
    ttk.Label(root, text="3 tk.Entry Segoe 8").pack(anchor="w")
    tk.Entry(
        root,
        textvariable=v,
        font=("Segoe UI", 8),
        width=42,
        state="readonly",
        readonlybackground="white",
    ).pack(anchor="w", padx=8, pady=2)
    root.update_idletasks()
    root.update()
    save_png(root, r"h:\GitHub\PythonCode\epmDlTools\_port_font_test.png")
    root.destroy()


if __name__ == "__main__":
    main()
