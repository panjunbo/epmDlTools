"""打包前更新 utils/version.py 中的 __build_time__。"""
import re
import sys
from datetime import datetime
from pathlib import Path

VERSION_FILE = Path(__file__).resolve().parent / "utils" / "version.py"


def main() -> int:
    if not VERSION_FILE.is_file():
        print(f"[version] missing {VERSION_FILE}", file=sys.stderr)
        return 1
    text = VERSION_FILE.read_text(encoding="utf-8")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text, n = re.subn(
        r'(__build_time__\s*=\s*)["\'][^"\']*["\']',
        rf'\g<1>"{now}"',
        text,
        count=1,
    )
    if n != 1:
        print("[version] __build_time__ not found in utils/version.py", file=sys.stderr)
        return 1
    VERSION_FILE.write_text(text, encoding="utf-8")
    print(f"[version] build_time={now}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
