from protocol import ProgrammerProtocol, parse_idle_timeout
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
        cfg["prog_timeout"] = self._resolve_timeout()
        self.logger.info(f"mode = programmer, bin = {cfg['programmer_bin_path']}")
        self.logger.info(f"bin_addr = 0x{cfg['programmer_bin_addr']:08x}")
        self.logger.info(f"idle timeout = {cfg['prog_timeout']}s")
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

    def _resolve_timeout(self) -> float:
        """Idle timeout resolution order:

        1. CLI argument ``-prog_timeout``
        2. ``prog_timeout`` already set in cfg (top-level key from the caller)
        3. ini ``[flashload].prog_timeout``
        4. default 10s

        The ini-backed cfg is a "section -> key/value" dict (ConfigManager.read),
        so the ini entry lives at cfg["flashload"]["prog_timeout"].
        """
        raw = getattr(self.args, "prog_timeout", None)
        if raw is None:
            raw = self.cfg.get("prog_timeout")
        if raw is None:
            section = self.cfg.get("flashload") if isinstance(self.cfg, dict) else None
            if isinstance(section, dict):
                raw = section.get("prog_timeout")
        return parse_idle_timeout(raw, ProgrammerProtocol.DEFAULT_IDLE_TIMEOUT)
