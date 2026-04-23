# USB Backup — Windows USB 自动备份与内核进程保护系统

## 一、项目概述

本项目实现一个运行在 Windows 平台上的 **USB 自动备份服务**，配套一个 **Ring0 内核级进程保护驱动**，确保备份进程自身无法被恶意终止。

### 核心功能

| 模块 | 功能 |
|------|------|
| **USB 监控** | WMI 实时检测 U 盘插入/拨出，自动触发增量备份 |
| **增量备份** | 三重校验（大小 + 修改时间 + MD5），`backup_meta.json` 持久化元数据 |
| **云端同步** | SFTP 断点续传，按 `machineid` 隔离存储目录 |
| **空间管理** | 5GB 阈值预检；大文件流式分段传输（100MB/块）；自动清理最旧备份 |
| **自修复** | 60 秒自检 EXE 完整性；缺失则从云端恢复；重建计划任务 |
| **双进程守护** | 主进程 + 守护进程互相监控（`--guardian` 参数区分角色），5 秒保活 |
| **内核驱动** | `ObRegisterCallbacks` 挂钩进程创建/复制句柄，剥离危险权限（Terminate/VM_WRITE/Suspend 等） |

---

## 二、文件结构

```
USB_Backup/
├── usb_backup.py              # 主程序入口 (10 个核心类)
├── driver_loader.py           # Python 驱动加载器 (DriverLoader + DriverClient)
├── usb_backup.spec            # PyInstaller 打包配置
├── config.json.example        # 配置文件模板
│
├── driver/
│   ├── ProcProtect.h          # 驱动头文件 (IOCTL 宏/数据结构)
│   ├── ProcProtect.c          # 驱动核心源码 (WDM + ObRegisterCallbacks)
│   └── ProcProtect.inf        # WDM 安装信息文件
│
├── scripts/
│   ├── makecert.bat           # 生成自签名测试证书
│   ├── sign_driver.bat        # 对驱动文件进行签名
│   └── install_driver.bat     # 复制驱动 + 创建 SCM 服务
│
├── docs/
│   ├── QUICKSTART.md          # 快速运行指南（面向用户）
│   └── DEVELOP.md              # 开发指南（面向开发者）
│
├── README.md                  # 项目概览与索引
└── LICENSE                    # GPL v3
```

> 详细操作说明请参考：
> - [快速运行指南](docs/QUICKSTART.md) — 安装、配置、日常使用
> - [开发指南](docs/DEVELOP.md) — 编译驱动、调试、代码规范


---

## 三、快速部署

### 3.1 环境准备

- **操作系统**: Windows 10/11 x64
- **Python**: 3.8 ~ 3.11 (推荐 3.10)
- **WDK**: Windows Driver Kit (用于编译内核驱动，如不使用预编译驱动可跳过)

### 3.2 Python 依赖

```powershell
pip install pywin32 paramiko cryptography pyinstaller
```

或使用 requirements.txt:

```powershell
pip install -r requirements.txt
```

### 3.3 编译内核驱动 (可选)

如使用预编译的 `ProcProtect.sys` 文件可跳过此步骤。

1. 安装 WDK (Windows 11 WDK 或 WDK for Windows 10)
2. 打开 WDK Build Environment (x64 Free or Checked)
3. 编译驱动:

```powershell
cd driver
msbuild /p:Configuration=Release /p:Platform=x64
```

4. 签名驱动:

```powershell
# 启用测试签名模式 (需重启)
bcdedit /set testsigning on

# 运行签名脚本
sign_driver.bat
```

### 3.4 打包 EXE

```powershell
# 安装 PyInstaller (如未安装)
pip install pyinstaller

# 打包
pyinstaller usb_backup.spec

# 输出目录: dist\USB_Backup\
```

### 3.5 首次安装

#### 步骤 1: 配置 config.json

```json
{
  "backup_root": "D:\\USB_Backup",
  "sftp_host": "47.117.126.60",
  "sftp_port": 222,
  "sftp_username": "backup_user",
  "sftp_password": "your_password_here",
  "sftp_remote_base": "/mnt/hdd/backup/USB_Backup",
  "max_backup_size_gb": 5,
  "enable_cloud_sync": true,
  "self_heal_enabled": true,
  "guardian_enabled": true,
  "log_level": "INFO"
}
```

#### 步骤 2: 安装内核驱动

**方式 A: 图形化 (推荐普通用户)**

1. 右键 `install_driver.bat` → **以管理员身份运行**
2. 驱动将复制到 `C:\Windows\System32\drivers\ProcProtect.sys`
3. SCM 服务 `ProcProtect` 将被创建并启动

**方式 B: 命令行**

```powershell
cd /d %~dp0
install_driver.bat
```

#### 步骤 3: 创建计划任务 (开机自启)

```powershell
schtasks /create /tn "USB Backup Service" /tr "\"dist\USB_Backup\USB_Backup.exe\"" /sc onlogon /rl highest /f
```

#### 步骤 4: 启动服务

```powershell
# 直接启动
start "" "dist\USB_Backup\USB_Backup.exe"

# 或通过计划任务
schtasks /run /tn "USB Backup Service"
```

---

## 四、使用说明

### 4.1 正常运行

插入 USB 设备后，程序将：

1. 检测到新插入的卷
2. 扫描该卷根目录及子目录的文件变更
3. 与 `backup_root` 中的历史元数据 (`backup_meta.json`) 比对
4. 执行增量备份（仅备份有变化的文件）
5. 将备份数据同步到 SFTP 云端（如启用）

日志文件位于 `%LOCALAPPDATA%\USB_Backup\logs\`。

### 4.2 保护机制

- **双进程守护**: 主进程 PID 由内核驱动保护；守护进程每 5 秒检查主进程是否存活
- **内核保护**: `ObRegisterCallbacks` 阻止任何用户态代码对受保护进程执行 Terminate / VM_WRITE / Suspend 等危险操作
- **自修复**: 检测到 EXE 被删除后，自动从云端 `system/{MACHINE_ID}/` 恢复

### 4.3 命令行参数

| 参数 | 说明 |
|------|------|
| `--config <path>` | 指定配置文件路径 (默认: 程序目录下的 `config.json`) |
| `--guardian` | 以守护进程模式运行 |
| `--log-level DEBUG|INFO|WARNING` | 覆盖日志级别 |
| `--no-cloud` | 禁用云端同步 |
| `--dry-run` | 模拟备份 (不实际写入) |

### 4.4 云端目录结构

```
/mnt/hdd/backup/USB_Backup/
├── <machine_id_1>/
│   ├── usb_backup.exe            # 自修复恢复文件
│   ├── 2024-01-15_123456_USBVOLUME/
│   │   ├── file1.txt
│   │   ├── subdir/
│   │   └── backup_meta.json
│   └── 2024-01-16_654321_USBVOLUME/
│       └── ...
├── <machine_id_2>/
│   └── ...
└── system/
    └── <machine_id>/             # 自修复目录
        └── usb_backup.exe
```

---

## 五、卸载

### 5.1 停止并删除计划任务

```powershell
schtasks /delete /tn "USB Backup Service" /f
```

### 5.2 停止并卸载内核驱动

```powershell
sc stop ProcProtect
sc delete ProcProtect
del C:\Windows\System32\drivers\ProcProtect.sys
```

### 5.3 删除本地备份数据

```powershell
rd /s /q "D:\USB_Backup"
rd /s /q "%LOCALAPPDATA%\USB_Backup"
```

---

## 六、常见问题

### Q1: 驱动无法加载 (错误码 577)

**原因**: 驱动未正确签名。

**解决方案**:
1. 启用测试签名: `bcdedit /set testsigning on`（需重启）
2. 使用 `sign_driver.bat` 对驱动进行自签名
3. 或使用正式代码签名证书进行签名

### Q2: "DeviceIoControl failed" 错误

**原因**: 驱动未加载或服务未启动。

**解决方案**:
```powershell
sc query ProcProtect
sc start ProcProtect
```

### Q3: U 盘插入后没有触发备份

**排查步骤**:
1. 检查日志: `%LOCALAPPDATA%\USB_Backup\logs\usb_backup.log`
2. 确认 `enable_cloud_sync` 未设为 `false`
3. 检查备份目录是否存在且可写
4. 确认 SFTP 连接信息正确

### Q4: 备份卡在某个大文件

**原因**: 大文件传输超时或被中断。

**说明**: 程序使用 100MB 分块流式传输，理论上支持任意大小文件。卡住通常是网络问题，程序会在下次运行时自动续传。

### Q5: 进程无法被 Terminate 却也无法正常退出

**原因**: 内核保护阻止了 Terminate 操作。

**解决方案**: 通过驱动客户端先移除保护:
```powershell
python driver_loader.py remove <pid>
```

---

## 七、安全说明

1. **测试签名**: 内核驱动使用自签名证书，仅适用于开发测试环境
2. **正式部署**: 生产环境请使用受信任 CA 颁发的代码签名证书
3. **TestSigning**: `bcdedit /set testsigning on` 仅用于开发和测试
4. **云端凭证**: 请妥善保管 `config.json` 中的 SFTP 密码，建议使用 SSH 密钥认证

---

## 八、许可证

本项目基于 **GPL v3** 许可证开源。

---

## 九、技术支持

如遇问题请提交 Issue，并附上以下信息:
- Windows 版本
- 日志文件 (`%LOCALAPPDATA%\USB_Backup\logs\usb_backup.log`)
- 错误截图或完整错误信息
