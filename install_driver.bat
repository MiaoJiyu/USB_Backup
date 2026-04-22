@echo off
REM ============================================================
REM install_driver.bat - 内核驱动安装/卸载脚本
REM
REM 用途: 通过 SCM (Service Control Manager) 安装/卸载
REM       ProcProtect.sys Ring0 进程保护驱动
REM
REM 用法:
REM   install_driver.bat install   -- 安装并启动驱动
REM   install_driver.bat uninstall -- 停止并卸载驱动
REM   install_driver.bat status     -- 查看驱动状态
REM   install_driver.bat restart    -- 重启驱动
REM
REM 前提条件:
REM   - 已编译并签名 ProcProtect.sys
REM   - 以管理员身份运行
REM   - 测试环境需开启 testsigning 模式
REM ============================================================

setlocal EnableDelayedExpansion

echo ============================================
echo  ProcProtect 驱动安装管理工具
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

REM ---- 配置常量 ----
set DRIVER_SERVICE_NAME=ProcProtect
set DRIVER_DISPLAY_NAME=Process Protection Driver for USB Backup
set DRIVER_BINARY=ProcProtect.sys
set DRIVER_BINARY_PATH=%~dp0driver\Release\%DRIVER_BINARY%

REM ---- 自动检测驱动文件位置 ----
if not exist "%DRIVER_BINARY_PATH%" (
    set DRIVER_BINARY_PATH=%~dp0driver\%DRIVER_BINARY%
)
if not exist "%DRIVER_BINARY_PATH%" (
    REM 尝试当前目录
    set DRIVER_BINARY_PATH=%~dp0%DRIVER_BINARY%
)

REM ---- 解析命令参数 ----
set COMMAND=%1
if "%COMMAND%"=="" set COMMAND=status

if /i "%COMMAND%"=="install"     goto cmd_install
if /i "%COMMAND%"=="uninstall"   goto cmd_uninstall
if /i "%COMMAND%"=="remove"      goto cmd_uninstall
if /i "%COMMAND%"=="status"      goto cmd_status
if /i "%COMMAND%"=="start"       goto cmd_start
if /i "%COMMAND%"=="stop"        goto cmd_stop
if /i "%COMMAND%"=="restart"     goto cmd_restart
if /i "%COMMAND%"=="help"        goto cmd_help

echo [错误] 未知命令: %COMMAND%
echo.
goto cmd_help


REM ==================== 命令: 安装驱动 ====================
:cmd_install
echo [操作] 安装驱动服务...

REM 检查是否已安装
sc query "%DRIVER_SERVICE_NAME%" >nul 2>&1
if %errorlevel% equ 0 (
    echo [提示] 驱动服务已存在, 如需重装请先运行 uninstall
    echo        或直接使用 restart 命令
    pause
    exit /b 0
)

REM 检查驱动文件是否存在
if not exist "%DRIVER_BINARY_PATH%" (
    echo [错误] 未找到驱动文件!
    echo        期望路径: %DRIVER_BINARY_PATH%
    echo        请确保已编译驱动或调整路径
    pause
    exit /b 1
)

echo      驱动文件: %DRIVER_BINARY_PATH%

REM 复制驱动到 System32\drivers
echo      复制到 System32\drivers...
copy /y "%DRIVER_BINARY_PATH%" "%SystemRoot%\System32\drivers\%DRIVER_BINARY%" >nul
if %errorlevel% neq 0 (
    echo [错误] 复制驱动文件失败! 请确认写入权限
    pause
    exit /b 1
)

REM 创建 SCM 服务
echo      创建 SCM 服务...
sc create "%DRIVER_SERVICE_NAME%" ^
    type= kernel ^
    binPath= "%SystemRoot%\System32\drivers\%DRIVER_BINARY%" ^
    group= "System Bus Extender" ^
    start= demand ^
    displayname= "%DRIVER_DISPLAY_NAME%"

if %errorlevel% neq 0 (
    echo [错误] 服务创建失败! 错误码: %errorlevel%
    pause
    exit /b 1
)

echo.
echo [操作] 启动驱动服务...
sc start "%DRIVER_SERVICE_NAME%"
if %errorlevel% neq 0 (
    echo [警告] 服务启动失败 (错误码: %errorlevel%)
    echo         可能原因:
    echo           1. 未启用测试签名模式 (测试证书)
    echo           2. 驱动签名无效
    echo           3. 架构不匹配 (仅支持 x64)
    echo.
    echo         解决方案:
    echo           bcdedit /set testsigning on ^&^& shutdown /r /t 5
) else (
    echo      驱动启动成功!
)

echo.
goto show_status


REM ==================== 命令: 卸载驱动 ====================
:cmd_uninstall
echo [操作] 卸载驱动...

REM 先停止服务
echo      停止服务...
sc stop "%DRIVER_SERVICE_NAME%" >nul 2>&1
timeout /t 2 /nobreak >nul

REM 删除服务
echo      删除 SCM 服务...
sc delete "%DRIVER_SERVICE_NAME%"
if %errorlevel% equ 0 (
    echo      服务删除成功
) else (
    echo [提示] 服务可能不存在或已被删除
)

REM 删除驱动文件
echo      删除驱动文件...
del /f /q "%SystemRoot%\System32\drivers\%DRIVER_BINARY%" >nul 2>&1
if %errorlevel% equ 0 (
    echo      驱动文件已删除
) else (
    echo [提示] 驱动文件删除失败 (可能被占用或不存在), 重启后可手动清理
)

echo.
echo [完成] 驱动卸载完成 (建议重启以确保完全卸载)
echo.


REM ==================== 命令: 查看状态 ====================
:cmd_status
:show_status
echo --------------------------------------------
echo  驱动状态信息
echo --------------------------------------------

sc query "%DRIVER_SERVICE_NAME%"
echo.

REM 检查设备符号链接是否存在
if exist "\\.\ProcProtect" (
    echo      设备链接: \\.\ProcProtect [可用]
) else (
    echo      设备链接: \\.\ProcProtect [不可用]
      echo                 (驱动可能未加载或设备名不同)
)

echo --------------------------------------------
pause
exit /b 0


REM ==================== 命令: 启动驱动 ====================
:cmd_start
echo [操作] 启动驱动服务...
sc start "%DRIVER_SERVICE_NAME%"
if %errorlevel% equ 0 (
    echo      驱动启动成功!
) else (
    echo      启动失败 (错误码: %errorlevel%)
)
echo.
goto show_status


REM ==================== 命令: 停止驱动 ====================
:cmd_stop
echo [操作] 停止驱动服务...
sc stop "%DRIVER_SERVICE_NAME%"
if %errorlevel% equ 0 (
    echo      驱动停止成功!
) else (
    echo      停止失败 (错误码: %errorlevel%)
)
echo.
goto show_status


REM ==================== 命令: 重启驱动 ====================
:cmd_restart
echo [操作] 重启驱动服务...
sc stop "%DRIVER_SERVICE_NAME%" >nul 2>&1
timeout /t 2 /nobreak >nul
sc start "%DRIVER_SERVICE_NAME%"
if %errorlevel% equ 0 (
    echo      驱动重启成功!
) else (
    echo      重启失败 (错误码: %errorlevel%)
)
echo.
goto show_status


REM ==================== 帮助信息 ====================
:cmd_help
echo 用法: %0 ^<command^>
echo.
echo 命令:
echo   install     安装驱动服务并启动
echo   uninstall   停止并卸载驱动服务
echo   status      显示驱动当前状态 (默认)
echo   start       启动已安装的驱动
echo   stop        停止正在运行的驱动
echo   restart     重启驱动
echo   help        显示此帮助信息
echo.
echo 示例:
echo   %0 install      - 首次安装并启动
echo   %0 status       - 查看运行状态
echo   %0 uninstall    - 完全卸载
echo.
echo 注意事项:
echo   1. 必须以管理员身份运行
echo   2. 测试证书需先启用: bcdedit /set testsigning on
echo   3. 仅支持 x64 Windows 系统
echo   4. 卸载后建议重启系统
echo.

pause
