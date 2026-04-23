"""
driver_loader.py - 内核驱动加载与控制模块

功能:
  - DriverLoader 类: 通过 Windows SCM (Service Control Manager) 管理驱动服务的
    安装、启动、停止、卸载操作
  - DriverClient 类: 封装 Win32 API (CreateFile / DeviceIoControl) 与内核驱动通信,
    提供 ADD_PID / REMOVE_PID / CLEAR_ALL / LIST_PIDS 四个高层接口

依赖:
  - ctypes (标准库, 用于 Win32 API 调用)
  - os / shutil (文件操作)

兼容性:
  - 仅 Windows 平台
  - 需要管理员权限执行 SCM 操作
"""

import os
import sys
import ctypes
import ctypes.wintypes as wintypes
from typing import List, Optional, Tuple

# ============================================================
# Win32 常量定义
# ============================================================

# 服务控制管理器访问权限
SC_MANAGER_ALL_ACCESS = 0xF003F
SC_MANAGER_CONNECT    = 0x0001
SC_MANAGER_CREATE_SERVICE = 0x0002

# 服务类型
SERVICE_KERNEL_DRIVER = 0x00000001

# 服务启动类型
SERVICE_DEMAND_START  = 0x00000003   # 手动启动
SERVICE_AUTO_START    = 0x00000002   # 系统自动启动

# 服务状态
SERVICE_STOPPED       = 0x00000001
SERVICE_RUNNING       = 0x00000004

# 服务控制码
SERVICE_CONTROL_STOP  = 0x00000020

# 设备创建标志
FILE_ATTRIBUTE_NORMAL = 0x80
GENERIC_READ          = 0x80000000
GENERIC_WRITE         = 0x40000000
OPEN_EXISTING         = 3

# IOCTL 控制码 (必须与内核驱动 ProcProtect.h 中定义完全一致)
FILE_DEVICE_UNKNOWN = 0x00000022
METHOD_BUFFERED     = 0
FILE_ANY_ACCESS     = 0

def CTL_CODE(DeviceType, Function, Method, Access):
    return ((DeviceType) << 16) | ((Access) << 14) | ((Function) << 2) | (Method)

IOCTL_PROCPROTECT_ADD_PID     = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x800, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_PROCPROTECT_REMOVE_PID  = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x801, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_PROCPROTECT_CLEAR_ALL   = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x802, METHOD_BUFFERED, FILE_ANY_ACCESS)
IOCTL_PROCPROTECT_LIST_PIDS   = CTL_CODE(FILE_DEVICE_UNKNOWN, 0x803, METHOD_BUFFERED, FILE_ANY_ACCESS)

# 错误代码
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED  = 5
ERROR_ALREADY_EXISTS = 183
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


# ============================================================
# Win32 结构体定义
# ============================================================

class PROTECT_PID_INFO(ctypes.Structure):
    """IOCTL ADD_PID / REMOVE_PID 的输入输出缓冲区结构"""
    _fields_ = [
        ("Pid", wintypes.ULONG),
    ]


class PID_LIST_HEADER(ctypes.Structure):
    """IOCTL LIST_PIDS 的输出缓冲区结构"""
    _fields_ = [
        ("Count", wintypes.ULONG),
        ("Pids", wintypes.ULONG * 64),
    ]


# ============================================================
# DriverLoader 类 - SCM 驱动服务管理
# ============================================================

class DriverLoader:
    """
    通过 Windows Service Control Manager 管理内核驱动的安装与运行。

    用法示例:
        loader = DriverLoader(
            service_name="ProcProtect",
            driver_path="C:\\Windows\\System32\\drivers\\ProcProtect.sys",
            display_name="Process Protection Driver"
        )

        # 安装并启动
        loader.install()
        loader.start()

        # 停止并卸载
        loader.stop()
        loader.uninstall()
    """

    def __init__(
        self,
        service_name: str = "ProcProtect",
        driver_path: Optional[str] = None,
        display_name: str = "Process Protection Driver for USB Backup",
        group_name: str = "System Bus Extender"
    ):
        self.service_name = service_name
        self.display_name = display_name
        self.group_name = group_name

        # 默认驱动路径: System32\drivers\
        if driver_path is None:
            self.driver_path = os.path.join(
                os.environ.get('SystemRoot', 'C:\\Windows'),
                'System32',
                'drivers',
                f'{service_name}.sys'
            )
        else:
            self.driver_path = driver_path

        # 加载 Win32 库
        self._advapi32 = ctypes.windll.advapi32
        self._kernel32 = ctypes.windll.kernel32

        # SCM 句柄
        self._scm_handle = None
        self._svc_handle = None

    def _open_scm(self) -> bool:
        """打开 SCM 控制管理器"""
        try:
            self._scm_handle = self._advapi32.OpenSCManagerW(
                None,
                None,
                SC_MANAGER_ALL_ACCESS
            )
            return self._scm_handle is not None and self._scm_handle != INVALID_HANDLE_VALUE
        except Exception as e:
            print(f"[DriverLoader] OpenSCManager 失败: {e}")
            return False

    def _close_scm(self):
        """关闭 SCM 和服务句柄"""
        if self._svc_handle and self._svc_handle != INVALID_HANDLE_VALUE:
            self._advapi32.CloseServiceHandle(self._svc_handle)
            self._svc_handle = None
        if self._scm_handle and self._scm_handle != INVALID_HANDLE_VALUE:
            self._advapi32.CloseServiceHandle(self._scm_handle)
            self._scm_handle = None

    def _open_service(self):
        """打开已存在的驱动服务"""
        if not self._open_scm():
            return False
        self._svc_handle = self._advapi32.OpenServiceW(
            self._scm_handle,
            self.service_name,
            SC_MANAGER_ALL_ACCESS
        )
        return self._svc_handle is not None and self._svc_handle != INVALID_HANDLE_VALUE

    def is_installed(self) -> bool:
        """检查驱动服务是否已安装"""
        if not self._open_service():
            return False
        self._close_scm()
        return True

    def is_running(self) -> bool:
        """检查驱动是否正在运行"""
        if not self._open_service():
            return False

        status = wintypes.DWORD()
        buf_size = wintypes.DWORD()

        result = self._advapi32.QueryServiceStatusEx(
            self._svc_handle,
            0,  # SC_STATUS_PROCESS_INFO
            ctypes.byref(status),
            ctypes.sizeof(status),
            ctypes.byref(buf_size)
        )
        self._close_scm()

        return result and status.value == SERVICE_RUNNING

    def get_status(self) -> Tuple[int, int]:
        """
        返回当前状态 (current_state, exit_code)
        current_state: SERVICE_STOPPED / SERVICE_RUNNING 等
        """
        if not self._open_service():
            return (0, 0)

        class SERVICE_STATUS_PROCESS(ctypes.Structure):
            _fields_ = [
                ("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD),
                ("dwProcessId", wintypes.DWORD),
                ("dwServiceFlags", wintypes.DWORD),
            ]

        status_buf = SERVICE_STATUS_PROCESS()
        bytes_needed = wintypes.DWORD()

        result = self._advapi32.QueryServiceStatusEx(
            self._svc_handle,
            0,
            ctypes.byref(status_buf),
            ctypes.sizeof(status_buf),
            ctypes.byref(bytes_needed)
        )

        self._close_scm()

        if result:
            return (status_buf.dwCurrentState, status_buf.dwWin32ExitCode)
        return (0, 0)

    def install(self, copy_driver: bool = True) -> bool:
        """
        安装驱动服务

            copy_driver: 是否将驱动文件复制到 System32/drivers 目录

        返回:
            True=成功 False=失败
        """
        import shutil

        # 检查驱动源文件是否存在
        src_path = self.driver_path
        target_path = os.path.join(
            os.environ.get('SystemRoot', 'C:\\Windows'),
            'System32',
            'drivers',
            f'{self.service_name}.sys'
        )

        if copy_driver and not os.path.exists(target_path):
            if not os.path.exists(src_path):
                print(f"[DriverLoader] 驱动文件不存在: {src_path}")
                return False
            try:
                shutil.copy2(src_path, target_path)
                print(f"[DriverLoader] 已复制驱动到 {target_path}")
            except Exception as e:
                print(f"[DriverLoader] 复制驱动失败: {e}")
                return False

        # 打开 SCM
        if not self._open_scm():
            return False

        # 创建服务
        svc_handle = self._advapi32.CreateServiceW(
            self._scm_handle,
            self.service_name,
            self.display_name,
            SC_MANAGER_ALL_ACCESS,
            SERVICE_KERNEL_DRIVER,
            SERVICE_DEMAND_START,
            1,  # SERVICE_ERROR_NORMAL
            target_path,
            self.group_name,  # LoadOrderGroup
            None,             # TagId
            None,             # Dependencies
            None,             # ServiceStartName (使用 LocalSystem)
            None              # Password
        )

        error_code = ctypes.get_last_error()

        if svc_handle is None or svc_handle == INVALID_HANDLE_VALUE:
            self._close_scm()
            if error_code == ERROR_ALREADY_EXISTS:
                print(f"[DriverLoader] 服务 '{self.service_name}' 已存在")
                return True
            print(f"[DriverLoader] 创建服务失败, 错误码: {error_code}")
            return False

        self._advapi32.CloseServiceHandle(svc_handle)
        self._close_scm()

        print(f"[DriverLoader] 驱动服务 '{self.service_name}' 已安装")
        return True

    def start(self) -> bool:
        """启动驱动服务"""
        if not self._open_service():
            print("[DriverLoader] 无法打开服务 (可能未安装)")
            return False

        result = self._advapi32.StartServiceW(self._svc_handle, 0, None)

        if result:
            print(f"[DriverLoader] 驱动 '{self.service_name}' 启动命令已发送")
        else:
            error_code = ctypes.get_last_error()
            if error_code == ERROR_ALREADY_EXISTS or error_code == 1056:
                print(f"[DriverLoader] 驱动已在运行中")
                result = True
            else:
                print(f"[DriverLoader] 启动失败, 错误码: {error_code}")

        self._close_scm()
        return bool(result)

    def stop(self) -> bool:
        """停止驱动服务"""
        if not self._open_service():
            print("[DriverLoader] 无法打开服务")
            return False

        # 构建 SERVICE_STATUS 结构并停止
        class SERVICE_STATUS(ctypes.Structure):
            _fields_ = [
                ("dwServiceType", wintypes.DWORD),
                ("dwCurrentState", wintypes.DWORD),
                ("dwControlsAccepted", wintypes.DWORD),
                ("dwWin32ExitCode", wintypes.DWORD),
                ("dwServiceSpecificExitCode", wintypes.DWORD),
                ("dwCheckPoint", wintypes.DWORD),
                ("dwWaitHint", wintypes.DWORD),
            ]

        svc_status = SERVICE_STATUS()
        result = self._advapi32.ControlService(
            self._svc_handle,
            SERVICE_CONTROL_STOP,
            ctypes.byref(svc_status)
        )

        if result:
            print(f"[DriverLoader] 驱动 '{self.service_name}' 停止命令已发送")
        else:
            error_code = ctypes.get_last_error()
            print(f"[DriverLoader] 停止失败, 错误码: {error_code}")

        self._close_scm()
        return bool(result)

    def uninstall(self, stop_first: bool = True) -> bool:
        """
        卸载驱动服务

        参数:
            stop_first: 卸载前是否先尝试停止驱动
        """
        if stop_first:
            self.stop()
        import time
        time.sleep(1)  # 等待停止完成

        if not self._open_service():
            # 可能已经不存在了
            print(f"[DriverLoader] 服务可能已被删除或从未安装")
            return True

        result = self._advapi32.DeleteService(self._svc_handle)

        if result:
            print(f"[DriverLoader] 驱动服务 '{self.service_name}' 已卸载")
        else:
            error_code = ctypes.get_last_error()
            print(f"[DriverLoader] 删除服务失败, 错误码: {error_code}")

        self._close_scm()
        return bool(result)


# ============================================================
# DriverClient 类 - 设备 IO 控制 (DeviceIoControl 封装)
# ============================================================

class DriverClient:
    """
    通过 DeviceIoControl 与内核驱动 ProcProtect.sys 通信的客户端。

    用户态程序通过此类向驱动发送 IOCTL 命令来管理受保护进程列表。

    用法示例:
        client = DriverClient()

        if client.connect():
            client.add_pid(os.getpid())      # 保护自身进程
            pids = client.list_pids()        # 获取保护列表
            client.disconnect()
    """

    DEVICE_PATH = r"\\.\ProcProtect"

    def __init__(self, device_path: Optional[str] = None):
        """
        初始化驱动客户端

        参数:
            device_path: 驱动设备路径, 默认 \\Device\\ProcProtect
        """
        self.device_path = device_path or self.DEVICE_PATH
        self._device_handle = None
        self._kernel32 = ctypes.windll.kernel32
        self._connected = False

    def connect(self) -> bool:
        """
        连接到驱动设备

        返回: True=连接成功 False=失败
        """
        try:
            self._device_handle = self._kernel32.CreateFileW(
                self.device_path,
                GENERIC_READ | GENERIC_WRITE,
                0,               # 共享模式: 不共享
                None,            # 安全属性
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None             # 模板文件
            )

            if (self._device_handle is None or
                    self._device_handle == INVALID_HANDLE_VALUE):
                error_code = ctypes.get_last_error()
                print(f"[DriverClient] 连接设备失败 ({self.device_path}), "
                      f"错误码: {error_code}")
                print("           请确认驱动已加载且运行正常")
                return False

            self._connected = True
            print(f"[DriverClient] 已连接到驱动: {self.device_path}")
            return True

        except Exception as e:
            print(f"[DriverClient] 连接异常: {e}")
            return False

    def disconnect(self):
        """断开设备连接"""
        if self._device_handle and self._device_handle != INVALID_HANDLE_VALUE:
            self._kernel32.CloseHandle(self._device_handle)
            self._device_handle = None
            self._connected = False
            print("[DriverClient] 设备连接已断开")

    def _ioctl(self, control_code: int, input_data=None,
               output_type=None) -> Tuple[bool, any]:
        """
        发送 IOCTL 命令到底层驱动

        参数:
            control_code: IOCTL 控制码
            input_data:   输入数据 (PROTECT_PID_INFO 实例或 None)
            output_type:  输出结构体类型 (如 PID_LIST_HEADER)

        返回: (成功标志, 输出数据或None)
        """
        if not self._connected:
            print("[DriverClient] 未连接到设备")
            return (False, None)

        # 准备输入缓冲区
        input_buffer = None
        input_size = 0
        if input_data is not None:
            input_buffer = ctypes.byref(input_data)
            input_size = ctypes.sizeof(input_data)

        # 准备输出缓冲区
        output_data = output_type() if output_type else None
        output_buffer = ctypes.byref(output_data) if output_data else None
        output_size = ctypes.sizeof(output_type) if output_type else 0

        bytes_returned = wintypes.DWORD()

        result = self._kernel32.DeviceIoControl(
            self._device_handle,
            control_code,
            input_buffer,
            input_size,
            output_buffer,
            output_size,
            ctypes.byref(bytes_returned),
            None  # Overlapped (同步模式)
        )

        if result:
            return (True, output_data if output_data else None)
        else:
            error_code = ctypes.get_last_error()
            print(f"[DriverClient] IOCTL 失败 (码: 0x{control_code:X}), "
                  f"Win32 错误: {error_code}")
            return (False, None)

    @property
    def is_connected(self) -> bool:
        """返回当前连接状态"""
        return self._connected

    def add_pid(self, pid: int) -> bool:
        """
        将指定 PID 加入受保护列表

        参数:
            pid: 要保护的进程 ID

        返回: True=成功 False=失败
        """
        info = PROTECT_PID_INFO()
        info.Pid = pid

        success, _ = self._ioctl(IOCTL_PROCPROTECT_ADD_PID, info)

        if success:
            print(f"[DriverClient] PID {pid} 已加入受保护列表")
        return success

    def remove_pid(self, pid: int) -> bool:
        """
        从受保护列表移除指定 PID

        返回: True=成功 False=失败 (包括 PID 未找到的情况)
        """
        info = PROTECT_PID_INFO()
        info.Pid = pid

        success, _ = self._ioctl(IOCTL_PROCPROTECT_REMOVE_PID, info)

        if success:
            print(f"[DriverClient] PID {pid} 已从受保护列表移除")
        return success

    def clear_all(self) -> bool:
        """
        清空所有受保护 PID

        返回: True=成功 False=失败
        """
        success, _ = self._ioctl(IOCTL_PROCPROTECT_CLEAR_ALL)

        if success:
            print("[DriverClient] 所有受保护 PID 已清空")
        return success

    def list_pids(self) -> Optional[List[int]]:
        """
        获取当前所有受保护 PID 列表

        返回: PID 列表, 失败返回 None
        """
        success, data = self._ioctl(
            IOCTL_PROCPROTECT_LIST_PIDS,
            output_type=PID_LIST_HEADER
        )

        if success and data:
            pids = list(data.Pids[:data.Count])
            print(f"[DriverClient] 当前受保护 PIDs ({data.Count}): {pids}")
            return pids

        return None

    def protect_self(self) -> bool:
        """便捷方法: 保护当前进程自身"""
        import os
        return self.add_pid(os.getpid())

    def __enter__(self):
        """支持上下文管理器 with 语法"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文时自动断开连接"""
        self.disconnect()
        return False

    def __del__(self):
        """析构时确保释放句柄"""
        if self._connected:
            try:
                self.disconnect()
            except Exception:
                pass


# ============================================================
# 便捷函数: 一键安装+启动+保护
# ============================================================

def install_and_protect(driver_path: Optional[str] = None,
                        pids_to_protect: Optional[List[int]] = None) -> bool:
    """
    一键式函数: 安装驱动 → 启动 → 注册保护 PID

    参数:
        driver_path: 驱动 .sys 文件路径 (可选)
        pids_to_protect: 要保护的 PID 列表, 默认为 [当前进程PID]

    返回: 全部步骤成功返回 True
    """
    if pids_to_protect is None:
        pids_to_protect = [os.getpid()]

    print("=" * 50)
    print("  ProcProtect 驱动一键安装与保护")
    print("=" * 50)

    # 步骤 1: 安装驱动服务
    print("\n[1/3] 安装驱动服务...")
    loader = DriverLoader(driver_path=driver_path)
    if not loader.install(copy_driver=True):
        print("  驱动服务安装失败!")
        return False

    # 步骤 2: 启动驱动
    print("\n[2/3] 启动驱动...")
    if not loader.start():
        print("  驱动启动失败! 可能原因:")
        print("    - 测试签名未启用 (运行: bcdedit /set testsigning on)")
        print("    - 驱动签名无效")
        print("    - 非 x64 系统")
        return False

    # 步骤 3: 连接并注册保护 PID
    print("\n[3/3] 注册受保护进程...")
    with DriverClient() as client:
        for pid in pids_to_protect:
            if not client.add_pid(pid):
                print(f"  注册 PID {pid} 失败!")
                return False

    print("\n" + "=" * 50)
    print("  完成! 进程保护已激活")
    print("=" * 50)
    return True


if __name__ == "__main__":
    # 命令行测试模式
    print("ProcProtect 驱动加载器测试")
    print("-" * 40)

    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd in ("install", "i"):
            loader = DriverLoader()
            loader.install()
            loader.start()
        elif cmd in ("uninstall", "u", "remove"):
            loader = DriverLoader()
            loader.uninstall()
        elif cmd in ("start", "s"):
            loader = DriverLoader()
            loader.start()
        elif cmd in ("stop"):
            loader = DriverLoader()
            loader.stop()
        elif cmd in ("status", "st"):
            loader = DriverLoader()
            state, code = loader.get_status()
            state_map = {
                1: "STOPPED", 4: "RUNNING",
                2: "START_PENDING", 3: "STOP_PENDING"
            }
            print(f"状态: {state_map.get(state, f'UNKNOWN({state})')}, 退出码: {code}")
        elif cmd in ("test", "t"):
            # 测试连接和基本操作
            install_and_protect(pids_to_protect=[os.getpid()])
        else:
            print(f"未知命令: {cmd}")
            print("用法: python driver_loader.py [install|uninstall|start|stop|status|test]")
    else:
        # 无参数时进入交互测试
        print("可用命令:")
        print("  install   - 安装并启动驱动")
        print("  uninstall - 卸载驱动")
        print("  start     - 启动驱动")
        print("  stop      - 停止驱动")
        print("  status    - 查看驱动状态")
        print("  test      - 安装+启动+保护当前进程")
