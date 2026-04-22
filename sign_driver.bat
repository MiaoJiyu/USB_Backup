@echo off
REM ============================================================
REM sign_driver.bat - 内核驱动签名脚本
REM
REM 用途: 使用证书对 ProcProtect.sys 进行数字签名
REM 支持: 测试证书 (自签名) 或 EV 正式证书 (EV Code Sign)
REM
REM 前提条件:
REM   - 已编译 ProcProtect.sys 到 driver\ 目录
REM   - 已通过 makecert.bat 生成测试证书, 或拥有 EV 证书
REM   - 已安装 Windows SDK (signtool.exe)
REM ============================================================

setlocal EnableDelayedExpansion

echo ============================================
echo  ProcProtect 驱动签名工具
echo ============================================
echo.

REM ---- 管理员权限检查 ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 需要管理员权限运行此脚本!
    pause
    exit /b 1
)

REM ---- 参数配置 ----
set DRIVER_SRC=.\driver\ProcProtect.sys
set DRIVER_DST=.\driver\Release\ProcProtect.sys
set TIMESTAMP_SERVER=http://timestamp.digicert.com

REM ---- 检查驱动文件 ----
echo [1/5] 检查驱动文件...

if exist "%DRIVER_SRC%" (
    set DRIVER_PATH=%DRIVER_SRC%
    echo      找到: %DRIVER_SRC%
) else if exist "%DRIVER_DST%" (
    set DRIVER_PATH=%DRIVER_DST%
    echo      找到: %DRIVER_DST%
) else (
    echo [错误] 未找到已编译的驱动文件!
    echo        期望路径:
    echo          %DRIVER_SRC%
    echo          或
    echo          %DRIVER_DST%
    echo        请先使用 Visual Studio + WDK 编译驱动
    pause
    exit /b 1
)

REM ---- 查找 signtool ----
echo.
echo [2/5] 定位 signtool.exe...

set SIGNTOOL=
for %%i in (signtool.exe) do set SIGNTOOL=%%~$PATH:i

if "%SIGNTOOL%"=="" (
    REM 从 Windows Kits 查找
    for /f "tokens=2*" %%a in (
        'reg query "HKLM\SOFTWARE\Microsoft\Windows Kits\Installed Roots" /v KitsRoot10 2^>nul'
    ) do set KITS_ROOT=%%b

    if not "%KITS_ROOT%"=="" (
        if exist "%KITS_ROOT%\bin\x64\signtool.exe" (
            set SIGNTOOL=%KITS_ROOT%\bin\x64\signtool.exe
        ) else if exist "%KITS_ROOT%\bin\x86\signtool.exe" (
            set SIGNTOOL="%KITS_ROOT%\bin\x86\signtool.exe"
        )
    )
)

if "%SIGNTOOL%"=="" (
    echo [错误] 未找到 signtool.exe!
    echo        请安装 Windows SDK 10
    pause
    exit /b 1
)

echo      找到: %SIGNTOOL%

REM ---- 选择签名方式 ----
echo.
echo [3/5] 选择签名模式:
echo      1. 使用测试自签名证书 (需要先运行 makecert.bat)
echo      2. 使用 EV 代码签名证书 (生产环境)
echo      3. 仅添加时间戳 (已有签名的文件)
echo.
set /p SIGN_MODE="请输入选项 (1/2/3): "

if "%SIGN_MODE%"=="1" goto sign_test
if "%SIGN_MODE%"=="2" goto sign_ev
if "%SIGN_MODE%"=="3" goto timestamp_only

echo [错误] 无效选项!
pause
exit /b 1

REM ==================== 模式1: 测试证书签名 ====================
:sign_test
echo.
echo [4/5] 使用测试证书签名...

set CERT_FILE=ProcProtectTestCert.cer
set PVK_FILE=ProcProtectTestCert.pvk

if not exist "%CERT_FILE%" (
    echo [错误] 未找到测试证书: %CERT_FILE%
    echo        请先运行 makecert.bat 生成证书
    pause
    exit /b 1
)

if not exist "%PVK_FILE%" (
    echo [错误] 未找到私钥文件: %PVK_FILE%
    pause
    exit /b 1
)

"%SIGNTOOL%" sign ^
    /f "%CERT_FILE%" ^
    /pvk "%PVK_FILE%" ^
    /t "%TIMESTAMP_SERVER%" ^
    /v "%DRIVER_PATH%"

goto verify_result

REM ==================== 模式2: EV 代码签名 ====================
:sign_ev
echo.
echo [4/5] 使用 EV 证书签名...
echo      (EV 证书通常需要硬件令牌或云服务)
echo.
set /p PFX_PATH="请输入 PFX 证书文件路径: "

if not exist "%PFX_PATH%" (
    echo [错误] 证书文件不存在: %PFX_PATH%
    pause
    exit /b 1
)

"%SIGNTOOL%" sign ^
    /f "%PFX_PATH%" ^
    /fd SHA256 ^
    /tr "%TIMESTAMP_SERVER%" ^
    /td SHA256 ^
    /v "%DRIVER_PATH%"

goto verify_result

REM ==================== 模式3: 仅时间戳 ====================
:timestamp_only
echo.
echo [4/5] 仅添加时间戳...

"%SIGNTOOL%" timestamp ^
    /tr "%TIMESTAMP_SERVER%" ^
    /td SHA256 ^
    /v "%DRIVER_PATH%"

goto verify_result

REM ==================== 验证结果 ====================
:verify_result
echo.
echo [5/5] 验证签名结果...

"%SIGNTOOL%" verify /pa /v "%DRIVER_PATH%"
set VERIFY_RESULT=%errorlevel%

echo.
if %VERIFY_RESULT% equ 0 (
    echo [成功] 驱动签名验证通过!
) else (
    echo [警告] 签名验证返回非零值: %VERIFY_RESULT%
    echo         这可能是正常的 (如未启用测试模式)
)

echo.
echo ============================================
echo  签名完成!
echo ============================================
echo.
echo  下一步:
echo    运行 install_driver.bat 安装驱动
echo    若为测试证书, 确保已执行:
echo      bcdedit /set testsigning on ^&^& shutdown /r /t 5
echo.

pause
