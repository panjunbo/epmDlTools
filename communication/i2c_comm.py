from ctypes import c_ubyte, byref

from .interface import CommunicationInterface


class I2cComm(CommunicationInterface):
    def __init__(self, slave_addr: int = 0x50, clock_speed: int = 100000):
        self.slave_addr = slave_addr
        self.clock_speed = clock_speed
        self._write_buffer = (c_ubyte * 1024)()
        self._read_buffer = (c_ubyte * 1024)()
        self._opened = False
        self._lib = None

    def _load_lib(self):
        if self._lib is not None:
            return self._lib
        try:
            import ControlI2C
        except ImportError as e:
            raise ImportError(
                "缺少 ControlI2C 模块（厂商 USB-I2C 适配器），请确认 lib/ControlI2C.py 已加入路径"
            ) from e
        self._lib = ControlI2C
        return ControlI2C

    def open(self) -> bool:
        if self._opened:
            return True
        lib = self._load_lib()
        nRet = lib.VII_ScanDevice(1)
        if nRet <= 0:
            print("[I2cComm] 未发现设备")
            return False

        nRet = lib.VII_OpenDevice(lib.VII_USBI2C, 0, 0)
        if nRet != lib.ERR_SUCCESS:
            print(f"[I2cComm] 打开失败: {nRet}")
            return False

        cfg = lib.VII_INIT_CONFIG()
        cfg.MasterMode = lib.VII_MASTER
        cfg.ControlMode = lib.VII_HCTL_MODE
        cfg.AddrType = lib.VII_ADDR_7BIT
        cfg.SubAddrWidth = lib.VII_SUB_ADDR_1BYTE
        cfg.ClockSpeed = self.clock_speed

        nRet = lib.VII_InitI2C(lib.VII_USBI2C, 0, 0, byref(cfg))
        if nRet != lib.ERR_SUCCESS:
            print(f"[I2cComm] 初始化失败: {nRet}")
            return False
        self._opened = True
        return True

    def close(self):
        if not self._opened:
            return
        lib = self._load_lib()
        nRet = lib.VII_CloseDevice(lib.VII_USBI2C, 0)
        if nRet != lib.ERR_SUCCESS:
            print(f"[I2cComm] 关闭失败: {nRet}")
        self._opened = False

    def send(self, data: bytes) -> int:
        if not self._opened:
            return 0
        lib = self._load_lib()
        for i, b in enumerate(data[:1024]):
            self._write_buffer[i] = b
        nRet = lib.VII_WriteBytes(
            lib.VII_USBI2C, 0, 0, self.slave_addr, 0x00,
            byref(self._write_buffer), len(data),
        )
        if nRet != lib.ERR_SUCCESS:
            print(f"[I2cComm] 写入失败: {nRet}")
            return 0
        return len(data)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        if not self._opened:
            return b""
        lib = self._load_lib()
        receive_len = 32
        nRet = lib.VII_ReadBytes(
            lib.VII_USBI2C, 0, 0, self.slave_addr, 0x00,
            byref(self._read_buffer), receive_len,
        )
        if nRet != lib.ERR_SUCCESS:
            return b""
        out = bytearray()
        for i in range(receive_len):
            out.append(self._read_buffer[i])
        return bytes(out)

    def flush(self):
        pass
