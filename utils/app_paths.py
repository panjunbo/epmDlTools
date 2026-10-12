"""UI 打包 exe 下的数据目录布局。

frozen exe + --ui 时启用::

    <exe_dir>/dlTools_cfg/
        config/config.ini
        logs/
        readback/

CLI 与开发环境（python --ui）保持原有路径不变。
"""
import os
import shutil
import sys

DLTOOLS_CFG = "dlTools_cfg"

_active = False


def get_exe_dir() -> str:
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def app_icon_path() -> str | None:
    """窗口图标。开发时用仓库 assets，打包后用解压目录里的同一文件。"""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
        "download_tools.ico",
    )
    if os.path.isfile(path):
        return path
    return None


def is_frozen_exe() -> bool:
    return getattr(sys, "frozen", False)


def bundled_example_ini() -> str | None:
    """打包进 exe 或开发目录中的 example/config 模板路径。"""
    if is_frozen_exe():
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            for name in ("config.example.ini", "config.ini"):
                path = os.path.join(meipass, "config", name)
                if os.path.isfile(path):
                    return path
    for name in ("config.example.ini", "config.ini"):
        path = os.path.join(get_exe_dir(), "config", name)
        if os.path.isfile(path):
            return path
    return None


def default_bin_dir() -> str:
    """无配置时的 bin 目录默认值：exe 模式用 exe 同目录，开发环境用 ./bin。"""
    if is_frozen_exe():
        return get_exe_dir()
    return "./bin"


def dialog_initial_dir(preferred: str | None = None) -> str:
    """文件/目录对话框起始路径：preferred 为有效目录时用其，否则用 exe 所在目录。"""
    if preferred and os.path.isdir(preferred):
        return preferred
    return get_exe_dir()


def init_ui_exe_layout() -> bool:
    """确保 dlTools_cfg 子目录存在，并在首次运行时写入 config 模板。"""
    global _active
    if not is_frozen_exe():
        return False
    root = os.path.join(get_exe_dir(), DLTOOLS_CFG)
    for sub in ("config", "logs", "readback"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)

    target = os.path.join(root, "config", "config.ini")
    if not os.path.isfile(target):
        src = bundled_example_ini()
        if src:
            shutil.copy2(src, target)

    _active = True
    return True


def config_ini_path() -> str:
    if _active:
        return os.path.join(get_exe_dir(), DLTOOLS_CFG, "config", "config.ini")
    return os.path.join(get_exe_dir(), "config", "config.ini")


def get_logs_dir() -> str:
    if _active:
        return os.path.join(get_exe_dir(), DLTOOLS_CFG, "logs")
    return "logs"


def get_readback_dir() -> str:
    if _active:
        return os.path.join(get_exe_dir(), DLTOOLS_CFG, "readback")
    return os.path.join(get_exe_dir(), "bin", "readback")
