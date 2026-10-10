import os
import sys
import threading
from datetime import datetime
from typing import Callable, Optional


class Logger:
    SinkFn = Callable[[str, str], None]

    def __init__(self, regular_sink: Optional[SinkFn] = None,
                 id_sink: Optional[SinkFn] = None,
                 id_file: Optional[str] = None):
        self._regular_sink = regular_sink
        self._id_sink = id_sink
        self._id_file = id_file
        self._id_lock = threading.Lock()
        self._id_fp = None

    def set_sinks(self, regular_sink: Optional[SinkFn] = None,
                  id_sink: Optional[SinkFn] = None):
        if regular_sink is not None:
            self._regular_sink = regular_sink
        if id_sink is not None:
            self._id_sink = id_sink

    def set_id_file(self, path: Optional[str], append: bool = True):
        self.close_id_file()
        self._id_file = None
        if not path:
            return
        # idlog_*.bin 是格式串表。按文本追加会在文件缺失时新建空 bin。
        if os.path.splitext(path)[1].lower() == ".bin":
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        mode = "a" if append else "w"
        self._id_fp = open(path, mode, encoding="utf-8")
        self._id_file = path

    def id_file_path(self) -> Optional[str]:
        return self._id_file

    def close_id_file(self):
        if self._id_fp is not None:
            try:
                self._id_fp.flush()
                self._id_fp.close()
            except Exception:
                pass
            self._id_fp = None

    def _emit_regular(self, level: str, msg: str):
        if self._regular_sink is None:
            return
        try:
            self._regular_sink(level, msg)
        except Exception as e:
            print(f"[Logger] regular sink 调用失败: {e}", file=sys.stderr)

    def _emit_id(self, level: str, msg: str):
        if self._id_sink is not None:
            try:
                self._id_sink(level, msg)
            except Exception as e:
                print(f"[Logger] id sink 调用失败: {e}", file=sys.stderr)
        if self._id_fp is not None:
            with self._id_lock:
                try:
                    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                    self._id_fp.write(f"[{ts}][{level}] {msg}\n")
                    self._id_fp.flush()
                except Exception as e:
                    print(f"[Logger] id file 写入失败: {e}", file=sys.stderr)

    def info(self, msg: str):
        self._emit_regular("INFO", msg)

    def warn(self, msg: str):
        self._emit_regular("WARN", msg)

    def error(self, msg: str):
        self._emit_regular("ERROR", msg)

    def debug(self, msg: str):
        self._emit_regular("DEBUG", msg)

    def id(self, msg: str):
        self._emit_id("ID", msg)


def default_regular_sink(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}][{level}] {msg}")


def default_id_sink(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{ts}][{level}] {msg}")


def make_default_logger() -> Logger:
    return Logger(regular_sink=default_regular_sink, id_sink=default_id_sink)
