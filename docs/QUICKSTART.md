# 快速运行指南

> 面向普通用户，零基础也能上手。

---

## 前置要求

| 项目 | 版本 |
|------|------|
| 操作系统 | Windows 10/11 x64 |
| Python | 3.8 ~ 3.11（推荐 3.10） |
| 管理员权限 | 必须（右键 → 以管理员身份运行） |
| 网络 | 能访问 SFTP 服务器 `47.117.126.60:222` |

---

## 安装步骤

### 第一步：安装 Python 依赖

打开 **PowerShell（管理员）**：

```powershell
pip install pywin32 paramiko cryptography pyinstaller
```

> 如果 `pip` 找不到，先安装 Python：https://www.python.org/downloads/

### 第二步：配置程序

1. 复制配置文件模板：
   ```
   copy config.json.example config.json
   ```

2. 用记事本打开 `config.json`，修改以下必填项：
   ```json
   {
     "backup_root": "D:\\USB_Backup",       ← 修改为你的备份目标目录
     "sftp_password": "your_password",    ← 修改为你的 SFTP 密码
     ...
   }
   ```

### 第三步：安装内核驱动

> 驱动用于防止备份进程被恶意终止。**必须管理员权限**。

右键 `install_driver.bat` → **以管理员身份运行**

看到 `ProcProtect 服务已启动` 即成功。

> **如果报错 577**（驱动签名错误）：
> 1. 重启电脑
> 2. 按 `Shift + F8`（或 advanced startup）进入 **禁用强制签名模式**，或执行：
>    ```
>    bcdedit /set testsigning on
>    ```
>    重启后重新运行安装脚本。

### 第四步：打包 EXE（可选）

```powershell
pyinstaller usb_backup.spec
```

输出目录：`dist\USB_Backup\USB_Backup.exe`

### 第五步：设置开机自启

在 **PowerShell（管理员）** 中执行：

```powershell
schtasks /create /tn "USB Backup Service" /tr "dist\USB_Backup\USB_Backup.exe" /sc onlogon /rl highest /f
```

---

## 使用方法

### 正常运行

1. **插入 U 盘** → 程序自动检测并启动增量备份
2. 首次备份全部文件，后续仅备份变化文件
3. 备份完成后自动上传到云端
4. 所有操作静默完成，无界面弹窗

### 验证是否正常运行

1. 打开日志文件：
   ```
   %LOCALAPPDATA%\USB_Backup\logs\usb_backup.log
   ```
2. 看到 `USB Backup Service 启动` 和 `U 盘监控线程已启动` 即正常

### 查看备份结果

打开配置的 `backup_root` 目录（如 `D:\USB_Backup`）：

```
D:\USB_Backup\
├── 2026-04-22_143200_MYUSB/        ← 每次插入生成一个备份文件夹
│   ├── file1.txt
│   ├── photos/
│   │   └── img.jpg
│   └── backup_meta.json            ← 元数据（勿删除）
├── 2026-04-22_160500_MYUSB/
└── ...
```

---

## 常用命令

| 操作 | 命令 |
|------|------|
| 启动服务 | 直接运行 `USB_Backup.exe` |
| 查看日志 | `type %LOCALAPPDATA%\USB_Backup\logs\usb_backup.log` |
| 停止服务 | 任务管理器 → 结束 `USB_Backup.exe` |
| 查看驱动状态 | `sc query ProcProtect` |
| 卸载驱动 | `install_driver.bat uninstall` |
| 删除计划任务 | `schtasks /delete /tn "USB Backup Service" /f` |
| 强制重新备份 | 删除 `D:\USB_Backup` 下对应目录，下次插入自动重新备份 |

---

## 配置项说明

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `backup_root` | `D:\USB_Backup` | 本地备份存储路径 |
| `sftp_host` | `47.117.126.60` | 云端 SFTP 服务器地址 |
| `sftp_port` | `222` | SFTP 端口 |
| `sftp_username` | `backup_user` | SFTP 用户名 |
| `sftp_password` | （必填） | SFTP 密码 |
| `sftp_remote_base` | `/mnt/hdd/backup/USB_Backup` | 云端根目录 |
| `max_backup_size_gb` | `5` | 触发清理的最低保留空间（GB） |
| `enable_cloud_sync` | `true` | 是否启用云端同步 |
| `self_heal_enabled` | `true` | 是否启用 EXE 自修复 |
| `guardian_enabled` | `true` | 是否启用双进程守护 |
| `log_level` | `INFO` | 日志级别：DEBUG/INFO/WARNING |

---

## 故障排查

### 日志在哪？
```
%LOCALAPPDATA%\USB_Backup\logs\usb_backup.log
```

### 常见问题

**Q: U盘插入后没反应**
```
1. 检查日志是否有错误
2. 确认 config.json 中 sftp_password 已填写
3. 确认网络能连通 SFTP: telnet 47.117.126.60 222
```

**Q: 备份卡在某个文件不动**
```
大文件（>200MB）使用流式分段上传，这是正常行为。下次启动自动续传。
```

**Q: 提示"需要管理员权限"**
```
右键程序 → 更多 → 以管理员身份运行
```

**Q: 驱动无法加载（错误码 577）**
```
1. 执行: bcdedit /set testsigning on
2. 重启电脑
3. 再次运行 install_driver.bat
```

**Q: 如何完全卸载？**
```
1. 停止服务（任务管理器结束进程）
2. sc stop ProcProtect && sc delete ProcProtect
3. 删除 %LOCALAPPDATA%\USB_Backup
4. schtasks /delete /tn "USB Backup Service" /f
5. 删除备份目录 D:\USB_Backup（可选）
```
