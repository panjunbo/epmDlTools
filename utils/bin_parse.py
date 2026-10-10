import os
import sys
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from utils.app_paths import get_readback_dir

if TYPE_CHECKING:
    from utils.logger import Logger


class BinParse:
    def __init__(self, logger: Optional["Logger"] = None):
        self._buf = bytearray()
        self._logger = logger

    @property
    def content(self) -> bytearray:
        return self._buf

    def __len__(self) -> int:
        return len(self._buf)

    def _log_error(self, msg: str):
        if self._logger is not None:
            self._logger.error(msg)
        else:
            print(f"[BinParse] {msg}", file=sys.stderr)

    def parse(self, path: str) -> bool:
        try:
            with open(path, "rb") as fp:
                self._buf[:] = fp.read()
            return True
        except FileNotFoundError:
            self._log_error(f"文件不存在: {path}")
            return False
        except Exception as e:
            self._log_error(f"读取失败: {path} — {e}")
            return False

    def save(self, data, sub_dir: str = "readback") -> str:
        if sub_dir == "readback":
            out_dir = get_readback_dir()
        else:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
            out_dir = os.path.join(base, "bin", sub_dir)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(
            out_dir, datetime.now().strftime("%Y-%m-%d_%H-%M-%S") + ".bin"
        )
        with open(path, "wb") as fp:
            if isinstance(data, (bytes, bytearray)):
                fp.write(bytes(data))
            else:
                fp.write(data)
        return path

    def hex_dump(self, sep: str = ",") -> str:
        return self._buf.hex(sep)
