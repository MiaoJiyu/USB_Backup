@echo off
REM ============================================================
REM makecert.bat - 生成测试用自签名证书
REM
REM 用途: 为 ProcProtect.sys 内核驱动生成测试签名证书
REM 注意: 仅适用于测试环境 (需开启 testsigning 模式)
REM       生产环境必须使用 EV 代码签名证书!
REM ============================================================

setlocal EnableDelayedExpansion

echo ============================================
echo  ProcProtect 测试证书生成工具
echo ============================================
echo.

REM ---- 管理员权限检查 ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [错误] 需要管理员权限运行此脚本!
    echo        请右键点击 "以管理员身份运行"
    pause
    exit /b 1
)

REM ---- 配置参数 ----
set CERT_NAME=MiaoJiyu Test Signing Certificate
set CERT_SUBJECT=CN=MiaoJiyu Test Signing, O=MiaoJiyu Tech, L=Beijing, C=CN
set CERT_FILE=ProcProtectTestCert.cer
set PVK_FILE=ProcProtectTestCert.pvk
set PFX_FILE=ProcProtectTestCert.pfx
set CERT_STORE_NAME=Root

REM ---- 获取 Windows SDK 工具路径 ----
echo [1/4] 检查 Windows SDK 工具...

REM 尝试从注册表查找 SDK 路径
for /f "tokens=2*" %%a in (
    'reg query "HKLM\SOFTWARE\Microsoft\Windows Kits\Installed Roots" /v KitsRoot10 2^>nul'
) do set KITS_ROOT=%%b

if "%KITS_ROOT%"=="" (
    for /f "tokens=2*" %%a in (
        'reg query "HKLM\SOFTWARE\Microsoft\Windows Kits\Installed Roots" /v KitsRoot81 2^>nul'
    ) do set KITS_ROOT=%%b
)

if "%KITS_ROOT%"=="" (
    echo [警告] 未找到 Windows Kits 注册表条目, 使用默认路径...
    set KITS_ROOT=C:\Program Files (x86)\Windows Kits\10
)

REM 查找 bin\x64 目录中的工具
set MAKECERT_PATH=%KITS_ROOT%\bin\x64\makecert.exe
set PVK2PFX_PATH=%KITS_ROOT%\bin\x64\pvk2pfx.exe

if not exist "%MAKECERT_PATH%" (
    REM 尝试其他可能的位置
    if exist "%KITS_ROOT%\bin\x86\makecert.exe" set MAKECERT_PATH=%KITS_ROOT%\bin\x86\makecert.exe
    if exist "%KITS_ROOT%\bin\makecert.exe" set MAKECERT_PATH=%KITS_ROOT%\bin\makecert.exe
)

if "%KITS_ROOT%"=="" (
    echo [错误] 未找到 makecert.exe, 请安装 Windows SDK!
    pause
    exit /b 1
)

echo      找到 SDK: %KITS_ROOT%
echo.

REM ---- 步骤 1: 创建自签名证书 ----
echo [2/4] 创建自签名测试证书...
echo      证书主题: %CERT_SUBJECT%

"%MAKECERT_PATH%" ^
    -r -pe -n "%CERT_SUBJECT%" ^
    -ss %CERT_STORE_NAME% ^
    -sr LocalMachine ^
    -a sha256 ^
    -cy authority ^
    -sky signature ^
    -sv "%PVK_FILE%" "%CERT_FILE%"

if %errorlevel% neq 0 (
    echo [错误] 证书创建失败! 错误码: %errorlevel%
    pause
    exit /b 1
)

echo.
echo [3/4] 生成 PFX 文件 (合并公钥+私钥)...

"%PVK2PFX_PATH%" ^
    -pvk "%PVK_FILE%" ^
    -spc "%CERT_FILE%" ^
    -pfx "%PFX_FILE%"

if %errorlevel% neq 0 (
    echo [警告] PFX 生成失败 (非致命, 可继续使用 .cer + .pvk)
) else (
    echo      已生成: %PFX_FILE%
)

REM ---- 步骤 2: 验证证书安装 ----
echo.
echo [4/4] 验证证书安装...

certutil -verifystore %CERT_STORE_NAME% | findstr /i "MiaoJiyu" >nul 2>&1
if %errorlevel% equ 0 (
    echo      证书已成功安装到受信任根证书颁发机构存储
) else (
    echo [警告] 证书验证失败, 请手动检查
)

echo.
echo ============================================
echo  证书生成完成!
echo ============================================
echo.
echo  生成的文件:
echo    公钥证书:   %CERT_FILE%
echo    私钥文件:   %PVK_FILE%
echo    PFX 包含私钥: %PFX_FILE%
echo.
echo  下一步:
echo    1. 运行 sign_driver.bat 对驱动进行签名
echo    2. 启用测试签名模式: bcdedit /set testsigning on
echo    3. 重启系统使签名生效
echo.

pause
