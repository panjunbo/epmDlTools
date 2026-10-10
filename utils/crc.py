from pycrc.algorithms import Crc


class CrcCalculator:
    def __init__(self, width: int = 16, poly: int = 0x3d65,
                 ref_in: bool = False, init: int = 0x1234,
                 ref_out: bool = False, xor_out: int = 0):
        self._crc = Crc(width, poly, ref_in, init, ref_out, xor_out)

    def reconfig(self, width: int, poly: int, ref_in, init, ref_out, xor_out):
        self._crc = Crc(width, poly, ref_in, init, ref_out, xor_out)

    def calculate(self, data) -> int:
        if isinstance(data, (bytes, bytearray)):
            return self._crc.table_driven(bytes(data))
        return self._crc.table_driven(data)
