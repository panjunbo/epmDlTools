import configparser
import os
import sys
from typing import Any, Optional

from utils.app_paths import default_bin_dir, is_frozen_exe


class ConfigManager:
    def __init__(self, path: Optional[str] = None):
        if path is None:
            base = os.path.dirname(os.path.abspath(sys.argv[0]))
            path = os.path.join(base, "config", "config.ini")
        self.path = path
        self._parser = configparser.ConfigParser()
        self.reload()

    def reload(self):
        """重新从磁盘读取配置文件，使外部修改生效。

        加载后调 ensure_defaults() 把缺失的段/键补回默认值（idempotent——
        已存在的 key 不覆盖）。这样即使 ini 不存在或被外部删空，bin 识别
        和 id_log 仍能给出合理默认，UI 不会出现"一片空白"的情况。
        """
        self._parser = configparser.ConfigParser()
        if os.path.exists(self.path):
            self._parser.read(self.path, encoding="utf-8")
        self.ensure_defaults()
        if self.repair_known_config():
            self.save()

    def read(self) -> dict:
        out = {}
        for section in self._parser.sections():
            out[section] = dict(self._parser.items(section))
        return out

    def get(self, section: str, key: str, default: Any = None) -> Any:
        try:
            return self._parser.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    def get_int(self, section: str, key: str, default: int = 0) -> int:
        v = self.get(section, key, None)
        if v is None:
            return default
        try:
            return int(v)
        except ValueError:
            return default

    def get_hex(self, section: str, key: str, default: int = 0) -> int:
        v = self.get(section, key, None)
        if v is None:
            return default
        v = str(v).strip()
        try:
            return int(v, 0)
        except ValueError:
            return default

    def set(self, section: str, key: str, value: Any):
        if not self._parser.has_section(section):
            self._parser.add_section(section)
        self._parser.set(section, key, str(value))

    def save(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fp:
            self._parser.write(fp)

    def ensure_defaults(self):
        defaults = {
            "serial": {
                "log_port": "COM5",
                "log_baud": "500000",
                "download_port": "COM5",
                "download_baud": "3000000",
                "flush_timeout_ms": "100",
            },
            "flashload": {
                "comm_type": "uart",
                "flash_port_num": "0",
                "target_baud": "3000000",
                "sync_exit": "true",
                "xip_read": "false",
            },
            "bins": {
                "default_dir": default_bin_dir(),
            },
            "chip_9032": {
                "programmer": "programmer_9032, 0x08080000",
                "bootloader": "bootloader_packet_9032, 0x00",
                "mcu_a": "MCU_A, 0x20000",
                "mcu_b": "MCU_B, 0x100000",
                # 9032 无 C 核：不补 mcu_c 默认值，保持为空
            },
            "chip_9062": {
                "programmer": "programmer_9062, 0x50060000",
                "bootloader": "bootloader_packet_9062, 0x00",
                "mcu_a": "MCU_A, 0x20000",
                "mcu_b": "MCU_B, 0x100000",
                "mcu_c": "bt_audio_dsp, 0x180000",
            },
            "id_log": {
                "base_addr": "0x30000000",
                "mcu_a": "idlog_MCU_A.bin",
                "mcu_b": "idlog_MCU_B.bin",
                "mcu_c": "idlog_MCU_C.bin",
            },
            "ui": {
                "theme": "light",
                "auto_scroll": "true",
                "window_geometry": "1100x700",
                "find_history": "",
                "find_case_sensitive": "false",
            },
        }
        for sec, kv in defaults.items():
            for k, v in kv.items():
                if self.get(sec, k, None) is None:
                    self.set(sec, k, v)

    def repair_known_config(self) -> bool:
        """修正历史版本/错误默认中的已知配置（仅匹配固定错误值）。"""
        changed = False

        keyword, addr = self._parse_bin_entry(self.get("chip_9032", "programmer"))
        if keyword == "programmer_9032" and addr == 0x50060000:
            self.set("chip_9032", "programmer", "programmer_9032, 0x08080000")
            changed = True

        if is_frozen_exe():
            dd = (self.get("bins", "default_dir", "") or "").strip()
            if dd in ("", ".", "./bin", ".\\bin"):
                self.set("bins", "default_dir", default_bin_dir())
                changed = True

        return changed

    BINARY_KEYS = ("programmer", "bootloader", "mcu_a", "mcu_b", "mcu_c")

    @staticmethod
    def _parse_bin_entry(raw: Optional[str]):
        """解析 '匹配关键词, 下载地址' 格式的配置项。"""
        if not raw:
            return "", 0
        parts = [p.strip() for p in raw.split(",")]
        addr = 0
        if len(parts) > 1:
            try:
                addr = int(parts[1], 0)
            except ValueError:
                addr = 0
        return parts[0], addr

    def chip_bin_files(self, chip: str) -> dict:
        section = f"chip_{chip}"
        out = {}
        for key in self.BINARY_KEYS:
            keyword, addr = self._parse_bin_entry(self.get(section, key))
            id_log = None
            if key.startswith("mcu_"):
                default = f"idlog_MCU_{key[-1].upper()}.bin"
                id_log = self.get("id_log", key, default) or None
            out[key] = {
                "key": key,
                "label": {"programmer": "prog", "bootloader": "boot",
                          "mcu_a": "mcua", "mcu_b": "mcub", "mcu_c": "mcuc"}[key],
                "keyword": keyword,
                "addr": addr,
                "id_log": id_log,
                "mode": "programmer" if key == "programmer" else "flashload",
            }
        return out

    def list_presets(self) -> list:
        out = []
        i = 1
        while True:
            n = self.get("presets", f"name_{i}", None)
            if n is None:
                break
            out.append({
                "name": n,
                "mode": self.get("presets", f"mode_{i}", "ASCII"),
                "data": self.get("presets", f"data_{i}", ""),
            })
            i += 1
        return out

    def list_id_log_map(self) -> dict:
        """读取 [id_log_map] 段：log_id(int) -> 文本(str)。
        key 支持 0x300027 / 300027h 等写法；value 支持带引号或不带引号。"""
        out = {}
        if not self._parser.has_section("id_log_map"):
            return out
        for key, val in self._parser.items("id_log_map"):
            if not key or val is None:
                continue
            try:
                # key 是 log_id 数值
                k = str(key).strip()
                if k.lower().endswith("h"):
                    k = k[:-1]
                log_id = int(k, 0)
                # value 是文本（去掉外层引号）
                text = str(val).strip()
                if len(text) >= 2 and text[0] == text[-1] and text[0] in ('"', "'"):
                    text = text[1:-1]
                out[log_id] = text
            except (ValueError, TypeError):
                continue
        return out

    def save_presets(self, presets):
        if not self._parser.has_section("presets"):
            self._parser.add_section("presets")
        for k in list(self._parser.options("presets")):
            self._parser.remove_option("presets", k)
        for i, p in enumerate(presets, 1):
            self._parser.set("presets", f"name_{i}", p.get("name", ""))
            self._parser.set("presets", f"mode_{i}", p.get("mode", "ASCII"))
            self._parser.set("presets", f"data_{i}", p.get("data", ""))
