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
            before = self._stat(path)
            with open(path, "rb") as fp:
                data = fp.read()
            after = self._stat(path)
            # 读的过程中文件被改写（编译还没完，bin 正在被覆盖）：
            # 读到的可能是半截内容，烧进设备等于砖机，必须挡住。
            if before is not None and after is not None and before != after:
                self._log_error(
                    f"文件在读取过程中被修改，请等编译完成后再下载: {path}")
                return False
            self._buf[:] = data
            return True
        except FileNotFoundError:
            self._log_error(f"文件不存在: {path}")
            return False
        except Exception as e:
            self._log_error(f"读取失败: {path} — {e}")
            return False

    @staticmethod
    def _stat(path: str):
        """文件状态（大小 + 修改时间）；取不到返回 None。"""
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_size, st.st_mtime_ns)

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
