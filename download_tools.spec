# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for epmDlTool → download_tools.exe (onefile)
#
# Usage:
#   pyinstaller download_tools.spec
#   -> dist/download_tools.exe
#
# 与 G:\GitHub\PythonCode\9062_download_tools\download_tools.spec 的区别：
# - 不打包 Ginkgo_Driver.dll（iic/spi 路径未启用，bat 也只用 uart）
# - 多打一个 config/config.ini（运行时若 cwd 没有 ini，也能拿到默认值）
# - 把 tkinter 也打进去（支持 --ui 启动 GUI）

block_cipher = None

# 资源文件：打包 example 模板（首次 GUI 启动复制到 dlTools_cfg/config/）
datas = [('config/config.example.ini', 'config')]

# 显式列出 hiddenimports，避免 PyInstaller 漏抓
# - tkinter: --ui 需要；Python 3.13+ 默认不再隐式包含
# - serial (pyserial): UartComm 依赖
# - pkg_resources 等: 避免动态 import 失败
hiddenimports = [
    'tkinter',
    'tkinter.ttk',
    'tkinter.filedialog',
    'tkinter.messagebox',
    'serial',
    'utils',
    'utils.version',
    'commands',
    'protocol',
    'communication',
]

a = Analysis(
    ['epmDlTool.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 不打包无关重模块，减小体积
        'matplotlib', 'numpy', 'pandas', 'scipy',
        'pytest', 'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile 模式：单个 download_tools.exe，运行时解压到 %TEMP%\_MEIxxxxx
# 分发只需复制 exe；bat 多次调用时启动比 onedir 慢（每次解压）。
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='download_tools',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                # 不强制 UPX 压缩，避免触发杀毒误报
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,             # CLI 工具，需要 stdout
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
