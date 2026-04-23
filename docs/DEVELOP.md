# 开发指南

> 面向开发者和贡献者，涵盖项目架构、代码规范、驱动编译、调试方法。

---

## 一、项目架构

```
USB_Backup/
├── usb_backup.py              # 主程序（用户态）
│   ├── ConfigManager           # 配置管理
│   ├── Logger                  # 日志系统
│   ├── USBDetector             # USB 热插拔监控（WMI）
│   ├── BackupEngine            # 增量备份引擎
│   ├── CloudSyncer             # SFTP 云端同步
│   ├── SpaceManager            # 磁盘空间管理
│   ├── SelfHealer              # EXE 自修复
│   ├── GuardianProcess         # 双进程守护
│   ├── DriverController        # 内核驱动控制
│   └── USBBackupApp            # 总控调度
│
├── driver_loader.py            # 驱动 SCM 管理 + IOCTL 封装
│
├── driver/
│   ├── ProcProtect.h           # 驱动头文件（IOCTL 码/数据结构）
│   ├── ProcProtect.c           # 驱动核心（WDM + ObRegisterCallbacks）
│   └── ProcProtect.inf         # WDM 安装信息
│
├── scripts/
│   ├── makecert.bat            # 生成自签名测试证书
│   ├── sign_driver.bat          # 驱动文件签名
│   └── install_driver.bat       # 驱动安装（创建 SCM 服务）
│
├── docs/
│   ├── QUICKSTART.md           # 快速运行指南
│   └── DEVELOP.md              # 本文档
│
├── usb_backup.spec             # PyInstaller 打包配置
├── config.json.example         # 配置模板
└── requirements.txt            # Python 依赖
```

---

## 二、依赖关系图

```
┌─────────────────────────────────────────┐
│              USBBackupApp                │
│  (总控: 初始化 + 事件循环 + 优雅关闭)     │
└──────┬──┬──┬──┬──┬──┬──┬──┬───────────┘
       │  │  │  │  │  │  │  │
   ┌───┘  │  │  │  │  │  │  └──────────┐
   │      │  │  │  │  │  │             │
   ▼      ▼  ▼  ▼  ▼  ▼  ▼             ▼
USBDetector BackupEngine CloudSyncer  DriverController
(WMI)     (文件IO)  (SFTP)      (IOCTL)
                 │
                 ▼
            SpaceManager
            SelfHealer
            GuardianProcess
```

---

## 三、核心设计决策

### 3.1 增量备份三重校验

```
步骤1: 比较文件大小     — O(1)，最快
步骤2: 比较 mtime      — O(1)
步骤3: 小文件(<5MB)比较MD5 — O(n)，最精确
```

### 3.2 大文件流式处理

- 阈值：200MB（可配置 `large_file_threshold_mb`）
- 块大小：100MB（可配置 `chunk_size_mb`）
- 不落盘：内存中分块读写，不产生临时文件

### 3.3 云端路径隔离

```
远程: /mnt/hdd/backup/USB_Backup/{machine_id}/{backup_name}/
     /mnt/hdd/backup/USB_Backup/system/{machine_id}/  ← EXE 自修复

machine_id = MD5(主板序列号 或 MAC地址)[:16]
```

### 3.4 双进程守护与内核驱动保护

```
主进程 (USB_Backup.exe)
  ├─ 注册 PID 到 ProcProtect.sys (内核驱动)
  ├─ 启动守护线程 (GuardianProcess)
  └─ 启动 USB 监控线程 (USBDetector)
       │
       ▼
守护进程 (USB_Backup.exe --guardian)
  ├─ 注册自己的 PID 到 ProcProtect.sys
  └─ 每 5 秒检测主进程是否存活
```

### 3.5 自旋锁保护 PID 列表

```
用户态: DriverClient.add_pid(pid)
          ↓ DeviceIoControl
内核驱动: AddProtectedPid(pid)
          ├─ KeAcquireSpinLock(&lock, &oldIrql)   ← 加锁
          ├─ 检查是否已存在
          ├─ 检查是否超过 MAX(64)
          └─ KeReleaseSpinLock(&lock, oldIrql)     ← 解锁
          ↓
ObRegisterCallbacks PreCallback:
          ├─ 检查目标 PID 是否受保护
          └─ DesiredAccess &= ~PROTECTED_ACCESS_MASK
```

---

## 四、编译内核驱动（WDK）

### 4.1 环境准备

1. 安装 **Visual Studio 2022**（包含 MSVC 工具链）
2. 安装 **Windows 11 WDK** 或 **WDK for Windows 10**
3. 安装 **Windows SDK**

### 4.2 编译步骤

1. 打开 **x64 Native Tools Command Prompt for VS 2026**（或 VS 2022）
2. 进入驱动目录：
   ```cmd
   cd C:\Users\admin\Documents\USB_Backup\driver
   ```
3. **使用 MSBuild 编译**：
   ```cmd
   msbuild ProcProtect.vcxproj /p:Configuration=Release /p:Platform=x64
   ```
   - 成功输出：`driver\x64\Release\ProcProtect.sys`

> **常见错误**：`'build' 不是内部或外部命令` → 这是 WDK 7.x 的旧命令，现代 WDK 必须用 MSBuild。如果 msbuild 找不到，请确保从 "x64 Native Tools Command Prompt for VS" 启动，并已安装 WDK VS 集成扩展（WDK 安装时勾选 "Enable driver development..."）。


### 4.3 签名驱动

```cmd
:: 方式 A: 使用项目脚本（开发/测试）
sign_driver.bat

:: 方式 B: 手动签名
makecert -r -h 0 -n "CN=ProcProtectTest" -e 12/31/2030 -sv ProcProtectTest.pvk ProcProtectTest.cer
pvk2pfx -pvk ProcProtectTest.pvk -spc ProcProtectTest.cer -pfx ProcProtectTest.pfx -f
signtool sign /f ProcProtectTest.pfx /p TestPass123 /fd SHA256 ProcProtect.sys
```

### 4.4 在真机上测试（TestSigning 模式）

```powershell
# 1. 启用测试签名（需重启）
bcdedit /set testsigning on

# 2. 安装驱动
install_driver.bat

# 3. 验证驱动加载
sc query ProcProtect

# 4. 查看 DbgPrint 输出（用 DebugView）
# https://learn.microsoft.com/en-us/sysinternals/downloads/debugview
```

### 4.5 检查驱动代码（CodeQL / Cppcheck）

```bash
# 安装 cppcheck（Linux 交叉编译时）
cppcheck --enable=all --std=c99 --platform=win64W driver/ProcProtect.c

# 或使用 Visual Studio静态分析（IDE 内置）
# Build → Configuration Properties → Code Analysis → Enable
```

---

## 五、调试方法

### 5.1 Python 调试

```bash
# 方法 A: 直接运行（显示控制台）
python usb_backup.py --log-level DEBUG

# 方法 B: 使用 IDE 断点调试
# VS Code: launch.json
{
  "name": "USB Backup (DEBUG)",
  "type": "python",
  "request": "launch",
  "module": "usb_backup",
  "console": "integratedTerminal",
  "args": ["--log-level", "DEBUG"]
}
```

### 5.2 内核驱动调试

**DbgPrint（实时日志）：**
```c
// 在驱动代码中添加 DbgPrint
DbgPrint("[ProcProtect] PID %lu protected\n", pid);
```
使用 **DebugView**（Sysinternals）捕获：`DebugView → Capture Kernel`

**WinDbg（双机调试）：**
```powershell
# 目标机
bcdedit /debug on
bcdedit /dbgsettings serial debugport:1 baudrate:115200

# 主机
WinDbg -k com:port=COM1,baud=115200
```

### 5.3 IOCTL 通信调试

```python
# driver_loader.py 内置调试模式
python driver_loader.py test

# 或手动测试
python -c "
from driver_loader import DriverClient
client = DriverClient()
client.connect()
client.add_pid(1234)
print(client.list_pids())
client.disconnect()
"
```

### 5.4 WMI 监控调试

```powershell
# 检查 WMI 是否正常
wmic volume get DriveLetter, Label, FileSystem, DriveType

# 查看 USB 设备
wmic diskdrive get model, mediaType, InterfaceType

# 监视 WMI 事件（实时）
wmic /NAMESPACE:\\root\CIMV2 PATH __InstanceCreationEvent WHERE "TargetInstance ISA 'Win32_Volume'" GET TargetInstance
```

---

## 六、代码规范

### 6.1 Python 代码规范

- **遵循**: PEP 8
- **类型注解**: 所有公共方法添加 `type hints`
- **异常处理**: 不 bare `except`，使用 `except Exception as e`
- **日志**: 使用 `self.logger` 而非 `print()`

### 6.2 C 驱动代码规范

- **遵循**: Microsoft DDI 规范
- **危险函数**: 禁止使用 `strcpy`, `sprintf`, `gets`, `scanf`
- **内存**: 使用 `ExAllocatePool` + `PoolTag`，禁止 `malloc/free`
- **锁**: 自旋锁用于线程同步，禁止在持有锁时调用可等待函数
- **Unicode**: 所有用户态字符串使用 `UNICODE_STRING`

### 6.3 Git 提交规范

```
feat: 新功能
fix:  错误修复
docs: 文档更新
refactor: 重构（无行为变化）
test: 测试
chore: 构建/工具变更

示例:
feat: 增加 SFTP 断点续传
fix(ProcProtect.c): 修复权限剥离条件判断逻辑
docs: 新增开发指南
```

---

## 七、添加新功能

### 7.1 添加新的 IOCTL 命令

**步骤 1**: 在 `ProcProtect.h` 中添加 IOCTL 宏：
```c
#define IOCTL_PROCPROTECT_XXX \
    CTL_CODE(FILE_DEVICE_UNKNOWN, 0x804, METHOD_BUFFERED, FILE_ANY_ACCESS)
```

**步骤 2**: 在 `ProcProtect.c` 的 `DeviceControl` 中添加 case：
```c
case IOCTL_PROCPROTECT_XXX:
    // 实现逻辑
    Irp->IoStatus.Information = ...;
    status = STATUS_SUCCESS;
    break;
```

**步骤 3**: 在 `driver_loader.py` 中添加 Python 封装：
```python
IOCTL_PROCPROTECT_XXX = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x804, METHOD_BUFFERED, FILE_ANY_ACCESS)

def xxx_operation(self, ...):
    return self._ioctl(IOCTL_PROCPROTECT_XXX, ...)
```

### 7.2 添加新的配置项

在 `ConfigManager.DEFAULT_CONFIG` 中添加默认值，在 `config.json.example` 中添加示例，在 README 中文档化。

---

## 八、测试

### 8.1 单元测试

```bash
pip install pytest

# 运行测试
pytest tests/ -v
```

### 8.2 模拟 USB 插入测试

```powershell
# 使用 WMI 模拟卷插入事件（开发调试用）
# 实际项目中无需手动触发，WMI 会自动检测
wmic volume get DriveLetter, FileSystem
```

### 8.3 签名验证测试

```powershell
# 检查驱动是否有签名
signtool verify /pa /v driver\objfre_win7_amd64\amd64\ProcProtect.sys

# 查看签名详情
signtool verify /pa /dw /v driver\objfre_win7_amd64\amd64\ProcProtect.sys
```

---

## 九、发布流程

1. **代码审查**: 确保所有 `TODO` 已处理，无硬编码密码
2. **安全检查**: 运行安全扫描，确认无新警告
3. **驱动签名**: 使用正式代码签名证书对 `ProcProtect.sys` 签名
4. **PyInstaller 打包**: `pyinstaller usb_backup.spec`
5. **功能测试**: 在干净 Windows 环境中测试完整流程
6. **更新文档**: 同步更新 README.md、CHANGELOG.md
7. **版本打标**: `git tag -a v1.0.0 -m "Release v1.0.0"`
