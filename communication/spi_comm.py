from ctypes import c_ubyte, byref

from .interface import CommunicationInterface


class SpiComm(CommunicationInterface):
    def __init__(self, clock_speed: int = 500000):
        self.clock_speed = clock_speed
        self._write_buffer = (c_ubyte * 1024)()
        self._read_buffer = (c_ubyte * 1024)()
        self._opened = False
        self._lib = None

    def _load_lib(self):
        if self._lib is not None:
            return self._lib
        try:
            import ControlSPI
        except ImportError as e:
            raise ImportError(
                "缺少 ControlSPI 模块（厂商 USB-SPI 适配器），请确认 lib/ControlSPI.py 已加入路径"
            ) from e
        self._lib = ControlSPI
        return ControlSPI

    def open(self) -> bool:
        if self._opened:
            return True
        lib = self._load_lib()
        nRet = lib.VSI_ScanDevice(1)
        if nRet <= 0:
            print("[SpiComm] 未发现设备")
            return False
        lib.VSI_CloseDevice(lib.VSI_USBSPI, 0)

        nRet = lib.VSI_OpenDevice(lib.VSI_USBSPI, 0, 0)
        if nRet != lib.ERR_SUCCESS:
            print(f"[SpiComm] 打开失败: {nRet}")
            return False

        spi_init = lib.VSI_INIT_CONFIG()
        spi_init.ClockSpeed = self.clock_speed
        spi_init.ControlMode = 2
        spi_init.CPHA = 1
        spi_init.CPOL = 1
        spi_init.LSBFirst = 0
        spi_init.MasterMode = 1
        spi_init.SelPolarity = 0
        spi_init.TranBits = 8

        nRet = lib.VSI_InitSPI(lib.VSI_USBSPI, 0, byref(spi_init))
        if nRet != lib.ERR_SUCCESS:
            print(f"[SpiComm] 初始化失败: {nRet}")
            return False
        self._opened = True
        return True

    def close(self):
        if not self._opened:
            return
        lib = self._load_lib()
        nRet = lib.VSI_CloseDevice(lib.VSI_USBSPI, 0)
        if nRet != lib.ERR_SUCCESS:
            print(f"[SpiComm] 关闭失败: {nRet}")
        self._opened = False

    def send(self, data: bytes) -> int:
        if not self._opened:
            return 0
        lib = self._load_lib()
        for i, b in enumerate(data[:1024]):
            self._write_buffer[i] = b
        nRet = lib.VSI_WriteBytes(
            lib.VSI_USBSPI, 0, 0, self._write_buffer, len(data)
        )
        if nRet != lib.ERR_SUCCESS:
            print(f"[SpiComm] 写入失败: {nRet}")
            return 0
        return len(data)

    def recv(self, timeout_ms: int = 1000) -> bytes:
        if not self._opened:
            return b""
        lib = self._load_lib()
        nRet = lib.VSI_ReadBytes(
            lib.VSI_USBSPI, 0, 0, self._read_buffer, 16
        )
        if nRet != lib.ERR_SUCCESS:
            return b""
        out = bytearray()
        for i in range(16):
            out.append(self._read_buffer[i])
        return bytes(out)

    def flush(self):
        pass
