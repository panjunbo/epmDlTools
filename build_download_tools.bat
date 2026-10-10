@echo off

REM build_download_tools.bat

REM 用 PyInstaller 把 epmDlTool.py 打成 download_tools（onefile 模式）

REM

REM 产物（onefile）：

REM   dist\download_tools.exe        单文件可执行程序（~15MB）

REM

REM 与原 download_tools.exe 的对应关系：

REM   旧: ..\download_tools.exe

REM   新: ..\download_tools.exe（路径不变，直接替换）

REM

REM onefile 说明：启动时解压到 %%TEMP%%\_MEIxxxxx，bat 连续调用比 onedir 慢；

REM 优势是只需复制一个 exe 即可在其他电脑运行。



setlocal EnableDelayedExpansion

chcp 65001 >nul

REM 始终切到 bat 所在目录（项目根），避免在 dist 等子目录执行时找不到脚本
cd /d "%~dp0"



echo ====================================================

echo  Build download_tools (onefile) from epmDlTool.py

echo ====================================================



where pyinstaller >nul 2>nul

if errorlevel 1 (

    echo [ERROR] pyinstaller not found. Install first:

    echo   pip install pyinstaller

    exit /b 1

)



for /f "delims=" %%V in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PYVER=%%V

echo Using Python %PYVER%

if "%PYVER%"=="3.14" (

    echo [WARN] Python 3.14 打包产物仅支持 Windows 10/11 x64。

    echo        若其他电脑是 Win7/Win8 或缺少运行库，会报 python314.dll 加载失败。

    echo        建议在更多机器兼容时使用 Python 3.12 x64 重新打包。

)



REM 清理旧产物（含 onedir 遗留的 download_tools\ 子目录）
REM 若 dist\download_tools.exe 被占用，PyInstaller 会报 WinError 5 拒绝访问

taskkill /F /IM download_tools.exe >nul 2>&1

if exist build rmdir /s /q build

if exist dist\download_tools rmdir /s /q dist\download_tools

if exist dist\download_tools.exe (
    del /f /q dist\download_tools.exe >nul 2>&1
    if exist dist\download_tools.exe (
        echo [ERROR] 无法删除 dist\download_tools.exe — 文件被占用
        echo        请关闭：
        echo          1. Cursor/编辑器里打开的 download_tools.exe 预览
        echo          2. 正在运行的 download_tools 进程
        echo          3. 杀毒软件对该文件的扫描
        exit /b 1
    )
)

if exist dist\*.zip del /f /q dist\*.zip

if not exist dist mkdir dist



echo [0/4] Updating version build time ...

python write_build_time.py

if errorlevel 1 (

    echo [ERROR] write_build_time.py failed.

    exit /b 1

)



echo [1/4] PyInstaller running ...

pyinstaller --noconfirm --clean download_tools.spec

if errorlevel 1 (

    echo [ERROR] PyInstaller failed.

    exit /b 1

)



set EXE=dist\download_tools.exe

echo [2/4] Verifying ...

if not exist %EXE% (

    echo [ERROR] %EXE% not found.

    exit /b 1

)

for %%I in (%EXE%) do echo   exe  = %%~zI bytes



echo [3/4] Adding deploy helpers ...

copy /y deploy\部署说明.txt dist\ >nul

copy /y deploy\verify_download_tools.bat dist\ >nul



set ZIP=dist\download_tools_win64_py%PYVER%.zip

if exist "%ZIP%" del /f /q "%ZIP%"

powershell -NoProfile -Command "Compress-Archive -Path 'dist\download_tools.exe','dist\部署说明.txt','dist\verify_download_tools.bat' -DestinationPath '%ZIP%' -Force"

if exist "%ZIP%" (

    echo   zip  = %ZIP%

) else (

    echo [WARN] zip 创建失败，请手动复制 download_tools.exe

)



echo [4/4] Smoke check + startup benchmark ...



echo --- --help ---

call %EXE% --help >nul 2>&1

if errorlevel 1 (

    echo [WARN] --help exit code !errorlevel!

)



echo --- startup time (3 runs) ---

set BEST=9999

for /L %%i in (1,1,3) do (

    for /f "delims=" %%t in ('powershell -NoProfile -Command "$sw=[System.Diagnostics.Stopwatch]::StartNew(); & '%CD%\%EXE%' --help 2>$null; $sw.Stop(); [int]$sw.Elapsed.TotalMilliseconds"') do set T=%%t

    echo   run %%i: !T! ms

    if !T! LSS !BEST! set BEST=!T!

)

echo   best startup: !BEST! ms

echo   ^(onefile 首次启动通常 1500-3000ms，后续 run 会快一些^)



echo --- dry-run ---

call %EXE% -mode programmer --dry-run -flash_port_num 0 -programmer_bin_addr 0x50060000 -programmer_bin_path bin\programmer.bin -comm_type uart -uart_port COM20

if errorlevel 1 (

    echo [WARN] dry-run exit code !errorlevel!

)



echo.

echo ====================================================

echo  Built: %EXE%

echo ====================================================

echo.

echo 分发到其他电脑:

echo   1. 只复制 dist\download_tools.exe 即可（或发 zip）

echo   2. 可选: 同目录运行 verify_download_tools.bat 自检

echo.

echo Drop-in replacement:

echo   %%DOWNLOAD_EXE%% = "..\download_tools.exe"

echo.

echo (CLI argv unchanged: same -mode / -flashload_cmd / -uart_port etc.)



endlocal

