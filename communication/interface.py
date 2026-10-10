from abc import ABC, abstractmethod
import threading


class CommunicationInterface(ABC):
    """串口通信抽象基类。新增 cancel/is_cancelled/reset_cancel 用于协议中断。"""
    pass

    @abstractmethod
    def open(self) -> bool:
        pass

    @abstractmethod
    def close(self):
        pass

    @abstractmethod
    def send(self, data: bytes) -> int:
        pass

    @abstractmethod
    def recv(self, timeout_ms: int = 1000) -> bytes:
        pass

    @abstractmethod
    def flush(self):
        pass

    @property
    def is_open(self) -> bool:
        return getattr(self, "_opened", False)

    def recv_buffered(self, max_len: int = 1024) -> bytes:
        return self.recv(timeout_ms=0)

    # ---- 取消/中断接口 ----
    def _get_cancel_event(self) -> threading.Event:
        # 懒初始化：兼容未调用 super().__init__() 的子类（如测试 Mock）
        ev = getattr(self, "_cancel_event", None)
        if ev is None:
            ev = threading.Event()
            self._cancel_event = ev
        return ev

    def cancel(self):
        """请求停止正在进行的协议循环。下次 tick() 会检测到并返回。"""
        self._get_cancel_event().set()

    def is_cancelled(self) -> bool:
        return self._get_cancel_event().is_set()

    def reset_cancel(self):
        """在新一轮下载开始前清除旧的取消状态。"""
        self._get_cancel_event().clear()

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False