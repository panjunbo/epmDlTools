#!/usr/bin/env bash
# build_download_tools.sh
# Cross-platform build for epmDlTool → download_tools (exe on Windows, binary on *nix).
# Same PyInstaller spec as build_download_tools.bat.
#
# Output:
#   dist/download_tools[.exe]   single executable
#   dist/config/config.ini       default config (runtime fallback)

set -euo pipefail

cd "$(dirname "$0")"

echo "===================================================="
echo " Build download_tools from epmDlTool.py"
echo "===================================================="

# Find pyinstaller
if ! command -v pyinstaller >/dev/null 2>&1; then
    echo "[ERROR] pyinstaller not found. Install first:"
    echo "  pip install pyinstaller"
    exit 1
fi

# Clean
rm -rf build dist
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "[0/2] Updating version build time ..."
python write_build_time.py

echo "[1/2] PyInstaller running ..."
pyinstaller \
    --noconfirm \
    --clean \
    download_tools.spec

echo "[2/2] Verifying ..."
EXE="dist/download_tools"
if [[ "$OSTYPE" == "msys"* || "$OSTYPE" == "cygwin"* || "$OSTYPE" == "win32" ]]; then
    EXE="dist/download_tools.exe"
fi
if [[ ! -f "$EXE" ]]; then
    echo "[ERROR] $EXE not found."
    exit 1
fi
echo "   $EXE built ($(stat -c %s "$EXE" 2>/dev/null || stat -f %z "$EXE") bytes)"

echo ""
echo "===================================================="
echo " Built: $EXE"
echo "===================================================="
echo ""
echo "Smoke check:"
"$EXE" --help || true

echo ""
echo "Dry-run sample (works without hardware):"
"$EXE" -mode programmer --dry-run -flash_port_num 0 \
    -programmer_bin_addr 0x50060000 -programmer_bin_path bin/programmer.bin \
    -comm_type uart -uart_port COM20 || true

echo ""
echo "Drop-in replacement ready."