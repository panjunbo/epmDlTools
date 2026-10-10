from protocol import FlashloadProtocol
from utils.bin_parse import BinParse

from .base import Command


class FlashloadCommand(Command):
    name = "flashload"

    def execute(self) -> int:
        cfg = self._build_cfg()
        self._log_preview(cfg)
        if getattr(self.args, "dry_run", False):
            self.logger.info("[dry-run] 跳过实际下载")
            return 0
        with self._enter_comm() as comm:
            bin_parse = BinParse(self.logger)
            proto = FlashloadProtocol(comm, bin_parse, cfg, self.logger)
            return proto.run()

    def _build_cfg(self) -> dict:
        a = self.args
        cfg = dict(self.cfg)
        cfg["comm_type"] = getattr(a, "comm_type", "uart")
        cfg["cmd"] = getattr(a, "cmd", "erase")
        cfg["flash_port_num"] = int(getattr(a, "flash_port_num", "0"))
        cfg["target_baud"] = int(getattr(a, "target_baud", "0"))
        cfg["flashload_bin_path"] = getattr(a, "flashload_bin_path", "./bin/flash.bin")
        cfg["erase_addr"] = int(getattr(a, "erase_addr", "0x100000"), 0)
        cfg["erase_size"] = int(getattr(a, "erase_size", "0x100000"), 0)
        cfg["erase_load_addr"] = int(getattr(a, "erase_load_addr", "0x100000"), 0)
        cfg["erase_load_size"] = int(getattr(a, "erase_load_size", "0x100000"), 0)
        cfg["flash_read_addr"] = int(getattr(a, "flash_read_addr", "0x0"), 0)
        cfg["flash_read_size"] = int(getattr(a, "flash_read_size", "0x100000"), 0)
        cfg["sync_exit"] = getattr(a, "sync_exit", "false")
        cfg["rdelay"] = int(getattr(a, "rdelay", "0"))
        for k in ("src_crc_init", "src_crc_poly", "src_crc_len",
                  "src_crc_ref_in", "src_crc_ref_out", "src_crc_xor_out",
                  "dst_crc_init", "dst_crc_poly"):
            v = getattr(a, k, "0")
            cfg[k] = int(v, 0) if v else 0
        return cfg

    def _log_preview(self, cfg: dict):
        self.logger.info(f"mode = flashload, cmd = {cfg['cmd']}")
        self.logger.info(f"comm_type = {cfg['comm_type']}")
        if cfg["cmd"] == "erase":
            self.logger.info(f"erase_addr=0x{cfg['erase_addr']:x}, erase_size=0x{cfg['erase_size']:x}")
        elif cfg["cmd"] in ("erase_load", "erase_load_crypt"):
            self.logger.info(f"erase_load_addr=0x{cfg['erase_load_addr']:x}, size=0x{cfg['erase_load_size']:x}")
            self.logger.info(f"bin = {cfg['flashload_bin_path']}")
        elif cfg["cmd"] in ("flash_read", "flash_xip_read"):
            self.logger.info(f"read_addr=0x{cfg['flash_read_addr']:x}, read_size=0x{cfg['flash_read_size']:x}")
