@echo off
chcp 65001 >nul
setlocal

set "ROOT=%~dp0"
set "EXE=%ROOT%download_tools.exe"

echo ====================================================
echo  download_tools 部署自检 (onefile)
echo ====================================================
echo 目录: %ROOT%
echo.

if not exist "%EXE%" (
    echo [FAIL] 缺少 download_tools.exe
    goto :fail
)
echo [OK]   download_tools.exe

for %%F in ("%EXE%") do (
    echo        大小: %%~zF bytes
    if %%~zF LSS 5000000 (
        echo [WARN] 文件偏小，可能拷贝不完整
    )
)

echo.
echo 尝试启动 --help ...
"%EXE%" --help >nul 2>&1
if errorlevel 1 (
    echo [FAIL] 程序无法启动，请安装 VC++ 2015-2022 x64 运行库
    echo        https://aka.ms/vs/17/release/vc_redist.x64.exe
    goto :fail
)
echo [OK]   程序可正常启动

echo.
echo ====================================================
echo  自检通过
echo ====================================================
endlocal
exit /b 0

:fail
echo.
echo ====================================================
echo  自检失败 — 请参考 部署说明.txt
echo ====================================================
endlocal
exit /b 1
