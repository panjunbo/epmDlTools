from .interface import CommunicationInterface
from .uart_comm import UartComm
from .spi_comm import SpiComm
from .i2c_comm import I2cComm
from .log_serial import LogSerial


SUPPORTED_TYPES = ("uart", "spi", "i2c")


def create(comm_type: str, port: str, baudrate: int = 115200):
    ct = (comm_type or "").lower()
    if ct == "iic":
        ct = "i2c"
    if ct == "uart":
        return UartComm(port=port, baudrate=baudrate)
    if ct == "spi":
        return SpiComm(clock_speed=baudrate)
    if ct == "i2c":
        return I2cComm(clock_speed=baudrate)
    raise ValueError(f"不支持的通信类型: {comm_type}，可选: {SUPPORTED_TYPES}")


__all__ = [
    "CommunicationInterface",
    "UartComm",
    "SpiComm",
    "I2cComm",
    "LogSerial",
    "create",
    "SUPPORTED_TYPES",
]
