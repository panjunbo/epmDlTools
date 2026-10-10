from protocol import ProgrammerProtocol
from utils.bin_parse import BinParse

from .base import Command


class ProgrammerCommand(Command):
    name = "programmer"

    def execute(self) -> int:
        a = self.args
        cfg = dict(self.cfg)
        cfg["comm_type"] = getattr(a, "comm_type", "uart")
        cfg["programmer_bin_path"] = getattr(a, "programmer_bin_path", "./bin/programmer.bin")
        cfg["programmer_bin_addr"] = int(getattr(a, "programmer_bin_addr", "0x50540000"), 0)
        self.logger.info(f"mode = programmer, bin = {cfg['programmer_bin_path']}")
        self.logger.info(f"bin_addr = 0x{cfg['programmer_bin_addr']:08x}")
        if getattr(a, "dry_run", False):
            self.logger.info("[dry-run] 跳过实际烧录")
            return 0
        with self._enter_comm() as comm:
            bin_parse = BinParse(self.logger)
            if not bin_parse.parse(cfg["programmer_bin_path"]):
                return 1
            proto = ProgrammerProtocol(
                comm, bin_parse, cfg, self.logger,
                unlock=getattr(a, "unlock", False),
            )
            return proto.run()
