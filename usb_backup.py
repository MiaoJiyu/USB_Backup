"""
USB Backup - Windows USB 自动备份与内核进程保护系统

主程序入口，包含 10 个核心类：
  ConfigManager  Logger  USBDetector  BackupEngine  CloudSyncer
  SpaceManager  SelfHealer  GuardianProcess  DriverController  USBBackupApp

依赖: pywin32, paramiko, cryptography, wmi
"""

import os
import sys
import json
import time
import queue
import struct
import hashlib
import logging
import datetime
import subprocess
import threading
import shutil
import socket
import platform
import uuid
import ctypes
from pathlib import Path
from typing import Optional, List, Dict, Any
from functools import wraps

# ---- Windows 专用模块 ----
try:
    import win32api
    import win32con
    import win32file
    import win32com.client
    import wmi
    import pythoncom
    import pywintypes
    HAS_WINDOWS = True
except ImportError:
    HAS_WINDOWS = False

# ---- SFTP 模块 ----
try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


# ============================================================
# 辅助工具函数
# ============================================================

def get_machine_id() -> str:
    """获取本机唯一标识 (基于主板序列号或 MAC 地址的 MD5)"""
    try:
        if HAS_WINDOWS:
            cmd = subprocess.run(
                ['wmic', 'baseboard', 'get', 'serialnumber'],
                capture_output=True, text=True, timeout=10
            )
            serial = cmd.stdout.strip().split('\n')[-1].strip()
            if serial and serial != 'SerialNumber':
                return hashlib.md5(serial.encode()).hexdigest()[:16]
            # 备选：MAC 地址
            mac = ':'.join(['{:02x}'.format((uuid.getnode() >> i) & 0xff)
                             for i in range(0, 48, 8)][::-1])
            return hashlib.md5(mac.encode()).hexdigest()[:16]
    except Exception:
        pass
    return hashlib.md5(socket.gethostname().encode()).hexdigest()[:16]


def is_admin() -> bool:
    """检查当前是否以管理员权限运行"""
    if not HAS_WINDOWS:
        return False
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def require_admin(func):
    """装饰器: 确保函数以管理员权限执行"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not is_admin():
            raise PermissionError("此操作需要管理员权限")
        return func(*args, **kwargs)
    return wrapper


def format_size(size_bytes: int) -> str:
    """将字节数格式化为可读字符串"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size_bytes) < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} PB"


def get_file_md5(filepath: str, chunk_size: int = 8192) -> str:
    """计算文件 MD5 哈希 (流式读取避免大文件内存爆炸)"""
    md5 = hashlib.md5()
    try:
        with open(filepath, 'rb') as f:
            for chunk in iter(lambda: f.read(chunk_size), b''):
                md5.update(chunk)
        return md5.hexdigest()
    except Exception:
        return ''


def ensure_dir(path: str) -> None:
    """确保目录存在，不存在则创建"""
    os.makedirs(path, exist_ok=True)


# ============================================================
# ConfigManager 类 — 配置文件管理
# ============================================================

class ConfigManager:
    """
    管理 config.json 配置文件，支持默认值、JSON 序列化与路径解析。
    配置路径优先级: 参数指定 > 环境变量 > 程序同级目录 > %LOCALAPPDATA%
    """

    DEFAULT_CONFIG = {
        "backup_root": "D:\\USB_Backup",
        "sftp_host": "47.117.126.60",
        "sftp_port": 222,
        "sftp_username": "backup_user",
        "sftp_password": "",
        "sftp_remote_base": "/mnt/hdd/backup/USB_Backup",
        "max_backup_size_gb": 5,
        "enable_cloud_sync": True,
        "self_heal_enabled": True,
        "guardian_enabled": True,
        "log_level": "INFO",
        "protected_pids": [],
        "driver_auto_load": True,
        "watch_interval_seconds": 60,
        "self_check_interval_seconds": 60,
        "guardian_check_interval_seconds": 5,
        "protected_process_names": ["USB_Backup.exe", "DriverLoader.exe"],
        "large_file_threshold_mb": 200,
        "chunk_size_mb": 100,
    }

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = self._resolve_path(config_path)
        self._config: Dict[str, Any] = {}
        self.load()

    def _resolve_path(self, config_path: Optional[str]) -> str:
        """解析配置文件路径"""
        if config_path and os.path.exists(config_path):
            return config_path
        env_path = os.environ.get('USB_BACKUP_CONFIG')
        if env_path and os.path.exists(env_path):
            return env_path
        local_dir = os.environ.get('LOCALAPPDATA', 'C:\\Users\\Default')
        local_path = os.path.join(local_dir, 'USB_Backup', 'config.json')
        if os.path.exists(local_path):
            return local_path
        exe_dir = os.path.dirname(os.path.abspath(sys.argv[0])
                                   if hasattr(sys, 'argv') and sys.argv else __file__)
        exe_path = os.path.join(exe_dir, 'config.json')
        if os.path.exists(exe_path):
            return exe_path
        return exe_path  # 返回默认位置，加载时会应用默认值

    def load(self) -> None:
        """从文件加载配置，不存在则使用默认配置并写入磁盘"""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    self._config = {**self.DEFAULT_CONFIG, **loaded}
            except Exception as e:
                print(f"[ConfigManager] 配置文件读取失败: {e}，使用默认配置")
                self._config = self.DEFAULT_CONFIG.copy()
        else:
            self._config = self.DEFAULT_CONFIG.copy()
            ensure_dir(os.path.dirname(self.config_path))
            self.save()

    def save(self) -> None:
        """将当前配置写入磁盘"""
        try:
            ensure_dir(os.path.dirname(self.config_path))
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self._config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[ConfigManager] 配置文件保存失败: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项"""
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置配置项并持久化"""
        self._config[key] = value
        self.save()

    @property
    def backup_root(self) -> str:
        return self._config.get('backup_root', self.DEFAULT_CONFIG['backup_root'])

    @property
    def sftp_host(self) -> str:
        return self._config.get('sftp_host', '')

    @property
    def sftp_port(self) -> int:
        return self._config.get('sftp_port', 222)

    @property
    def sftp_username(self) -> str:
        return self._config.get('sftp_username', '')

    @property
    def sftp_password(self) -> str:
        return self._config.get('sftp_password', '')

    @property
    def sftp_remote_base(self) -> str:
        return self._config.get('sftp_remote_base', '/mnt/hdd/backup/USB_Backup')

    @property
    def max_backup_size_gb(self) -> float:
        return float(self._config.get('max_backup_size_gb', 5))

    @property
    def enable_cloud_sync(self) -> bool:
        return bool(self._config.get('enable_cloud_sync', True))

    @property
    def self_heal_enabled(self) -> bool:
        return bool(self._config.get('self_heal_enabled', True))

    @property
    def guardian_enabled(self) -> bool:
        return bool(self._config.get('guardian_enabled', True))

    @property
    def machine_id(self) -> str:
        return get_machine_id()

    @property
    def remote_path(self) -> str:
        """云端备份路径: sftp_remote_base/machine_id/"""
        return f"{self.sftp_remote_base}/{self.machine_id}"

    @property
    def system_remote_path(self) -> str:
        """自修复系统目录: sftp_remote_base/system/machine_id/"""
        return f"{self.sftp_remote_base}/system/{self.machine_id}"


# ============================================================
# Logger 类 — 日志管理
# ============================================================

class Logger:
    """
    统一日志管理，控制台 + 文件双输出，支持日志轮转。
    文件路径: %LOCALAPPDATA%\\USB_Backup\\logs\\usb_backup.log
    """

    def __init__(self, config: ConfigManager):
        self.config = config
        self._log_dir = os.path.join(
            os.environ.get('LOCALAPPDATA', 'C:\\Users\\Default'),
            'USB_Backup', 'logs'
        )
        ensure_dir(self._log_dir)
        self._log_file = os.path.join(self._log_dir, 'usb_backup.log')
        self._setup_logger()

    def _setup_logger(self) -> None:
        """配置 Python logging"""
        level_name = self.config.get('log_level', 'INFO')
        level = getattr(logging, level_name.upper(), logging.INFO)

        formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        root = logging.getLogger('USBBackup')
        root.setLevel(level)
        root.handlers.clear()

        # 文件处理器 (轮转, 10MB/文件, 保留 5 个)
        try:
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(
                self._log_file, maxBytes=10 * 1024 * 1024,
                backupCount=5, encoding='utf-8'
            )
        except Exception:
            fh = logging.FileHandler(self._log_file, encoding='utf-8')
        fh.setLevel(level)
        fh.setFormatter(formatter)
        root.addHandler(fh)

        # 控制台处理器
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(level)
        ch.setFormatter(formatter)
        root.addHandler(ch)

    def get_logger(self, name: str = 'USBBackup') -> logging.Logger:
        return logging.getLogger(name)

    def debug(self, msg: str) -> None:
        self.get_logger().debug(msg)

    def info(self, msg: str) -> None:
        self.get_logger().info(msg)

    def warning(self, msg: str) -> None:
        self.get_logger().warning(msg)

    def error(self, msg: str) -> None:
        self.get_logger().error(msg)

    def critical(self, msg: str) -> None:
        self.get_logger().critical(msg)


# ============================================================
# DriverController 类 — 内核驱动控制
# ============================================================

class DriverController:
    """
    通过 driver_loader 模块控制 ProcProtect.sys 驱动的安装与通信。
    在 backup_root/ 目录下查找 driver_loader.py 同级 ProcProtect.sys，
    或使用 config 中指定的路径。
    """

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self._loader = None
        self._client = None
        self._driver_path = None
        self._initialized = False

    def _find_driver(self) -> Optional[str]:
        """在多个可能路径中查找 ProcProtect.sys"""
        candidates = [
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'driver', 'ProcProtect.sys'),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ProcProtect.sys'),
            'C:\\Windows\\System32\\drivers\\ProcProtect.sys',
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return None

    def initialize(self) -> bool:
        """初始化驱动: 查找并尝试安装/启动"""
        if self._initialized:
            return True

        if not self.config.get('driver_auto_load', True):
            self.logger.info("[DriverController] 驱动自动加载已禁用")
            return False

        if not HAS_WINDOWS:
            self.logger.warning("[DriverController] 非 Windows 平台，跳过驱动初始化")
            return False

        if not is_admin():
            self.logger.warning("[DriverController] 非管理员权限，无法加载内核驱动")
            return False

        try:
            # 延迟导入 driver_loader 以支持非 Windows 平台静态检查
            from driver_loader import DriverLoader, DriverClient
        except ImportError:
            self.logger.error("[DriverController] 找不到 driver_loader.py")
            return False

        self._driver_path = self._find_driver()
        if not self._driver_path:
            self.logger.warning("[DriverController] ProcProtect.sys 未找到，跳过驱动加载")
            return False

        self._loader = DriverLoader(driver_path=self._driver_path)

        # 尝试安装（服务已存在则返回 True）
        if not self._loader.install(copy_driver=True):
            self.logger.error("[DriverController] 驱动安装失败")
            return False

        if not self._loader.start():
            self.logger.warning("[DriverController] 驱动启动失败（可能需要 TestSigning）")
            return False

        self._client = DriverClient()
        if not self._client.connect():
            self.logger.error("[DriverController] 无法连接到驱动设备 \\.\\ProcProtect")
            return False

        self._initialized = True
        self.logger.info("[DriverController] 内核驱动初始化完成")
        return True

    def protect_pid(self, pid: int) -> bool:
        """将指定 PID 注册到内核保护列表"""
        if not self._initialized or not self._client:
            return False
        try:
            return self._client.add_pid(pid)
        except Exception as e:
            self.logger.error(f"[DriverController] protect_pid({pid}) 失败: {e}")
            return False

    def protect_current_process(self) -> bool:
        """便捷方法: 保护当前 Python/EXE 进程"""
        return self.protect_pid(os.getpid())

    def list_protected_pids(self) -> Optional[List[int]]:
        """获取当前受保护的 PID 列表"""
        if not self._initialized or not self._client:
            return None
        try:
            return self._client.list_pids()
        except Exception as e:
            self.logger.error(f"[DriverController] list_pids 失败: {e}")
            return None

    def shutdown(self) -> None:
        """关闭驱动连接"""
        if self._client:
            try:
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._initialized = False


# ============================================================
# USBDetector 类 — U 盘热插拔监控
# ============================================================

class USBDetector:
    """
    使用 WMI Win32_VolumeChangeEvent 监控 USB 存储设备插入/拨出事件。
    检测到新 U 盘后，将卷标信息放入事件队列供 BackupEngine 消费。
    """

    def __init__(self, config: ConfigManager, logger: Logger,
                 event_queue: queue.Queue):
        self.config = config
        self.logger = logger
        self.event_queue = event_queue
        self._wmi_conn = None
        self._watcher = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._seen_volumes: set = set()  # 避免重复触发

    def start(self) -> None:
        """启动 WMI 监控线程"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop,
                                         name='USBDetector', daemon=True)
        self._thread.start()
        self.logger.info("[USBDetector] U 盘监控线程已启动")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self._watcher:
            try:
                self._watcher.Stop()
            except Exception:
                pass
        self.logger.info("[USBDetector] U 盘监控线程已停止")

    def _watch_loop(self) -> None:
        """WMI 监控主循环 (在独立线程中运行 COM)"""
        while self._running:
            try:
                self._do_watch()
            except Exception as e:
                self.logger.error(f"[USBDetector] 监控异常: {e}，3 秒后重试")
                time.sleep(3)

    def _do_watch(self) -> None:
        """执行一次 WMI 事件监控 (每次最多阻塞 watch_interval)"""
        if not HAS_WINDOWS:
            return

        interval = self.config.get('watch_interval_seconds', 60)
        pythoncom.CoInitialize()
        try:
            c = wmi.WMI()
            self._watcher = c.Win32_VolumeChangeEvent.watch_for(
                notification_type='creation',
                delay_interval=interval
            )
            self.logger.debug("[USBDetector] WMI 监听就绪，等待卷事件...")

            while self._running:
                try:
                    event = self._watcher(timeout_ms=interval * 1000)
                    if event:
                        self._handle_event(event)
                except Exception:
                    break
        finally:
            pythoncom.CoUninitialize()

    def _handle_event(self, event) -> None:
        """处理 WMI 事件，过滤 USB 存储卷"""
        try:
            drive_letter = getattr(event, 'DriveName', None)
            if not drive_letter:
                return

            drive_letter = drive_letter.rstrip('\\')
            self.logger.info(f"[USBDetector] 检测到卷事件: {drive_letter}")

            # 检查是否为 USB 可移动驱动器
            if not self._is_usb(drive_letter):
                self.logger.debug(f"[USBDetector] 非 USB 设备，跳过: {drive_letter}")
                return

            if drive_letter in self._seen_volumes:
                self.logger.debug(f"[USBDetector] 已见过，跳过: {drive_letter}")
                return

            self._seen_volumes.add(drive_letter)
            volume_info = self._get_volume_info(drive_letter)
            self.event_queue.put(('INSERT', volume_info))
            self.logger.info(f"[USBDetector] USB 设备已插入: {drive_letter} "
                             f"({volume_info.get('label', 'NO_LABEL')})")

        except Exception as e:
            self.logger.error(f"[USBDetector] 事件处理失败: {e}")

    def _is_usb(self, drive_letter: str) -> bool:
        """判断指定盘符是否为 USB 可移动存储"""
        if not HAS_WINDOWS:
            return False
        try:
            drive_path = drive_letter + '\\'
            drive_type = win32file.GetDriveType(drive_path)
            if drive_type != win32file.DRIVE_REMOVABLE:
                return False
            # 进一步验证: 检查设备描述
            pythoncom.CoInitialize()
            try:
                c = wmi.WMI()
                for disk in c.Win32_DiskDrive():
                    if 'USB' in (disk.InterfaceType or ''):
                        for part in disk.associators('Win32_DiskDriveToDiskPartition'):
                            for logical in part.associators('Win32_LogicalDiskToPartition'):
                                if logical.DeviceID == drive_letter.rstrip(':'):
                                    return True
            finally:
                pythoncom.CoUninitialize()
            return True
        except Exception as e:
            self.logger.debug(f"[_is_usb] {drive_letter} USB 检测异常: {e}")
            # DRIVE_REMOVABLE 本身已足够
            return drive_letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'

    def _get_volume_info(self, drive_letter: str) -> Dict[str, Any]:
        """获取卷详细信息"""
        info = {
            'drive_letter': drive_letter,
            'label': '',
            'serial': '',
            'file_system': '',
            'total_size': 0,
            'free_space': 0,
        }
        if not HAS_WINDOWS:
            return info
        try:
            drive_path = drive_letter + '\\'
            pythoncom.CoInitialize()
            try:
                c = wmi.WMI()
                for vol in c.Win32_Volume(DriveLetter=drive_letter.rstrip(':') + ':'):
                    info['label'] = getattr(vol, 'Label', '') or ''
                    info['serial'] = str(getattr(vol, 'SerialNumber', 0))
                    info['file_system'] = getattr(vol, 'FileSystem', '') or ''
                    info['total_size'] = int(getattr(vol, 'Capacity', 0) or 0)
                    info['free_space'] = int(getattr(vol, 'FreeSpace', 0) or 0)
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            self.logger.debug(f"[_get_volume_info] {drive_letter}: {e}")
        return info


# ============================================================
# BackupEngine 类 — 增量备份引擎
# ============================================================

class BackupEngine:
    """
    核心备份逻辑:
    - 三重校验 (文件大小 + 修改时间 + MD5) 确定文件是否变化
    - backup_meta.json 持久化备份元数据
    - 大文件 (>200MB) 流式分段处理
    - 支持 dry-run 模拟模式
    """

    META_FILENAME = 'backup_meta.json'

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self._dry_run = getattr(sys, '_dry_run', False)

    def backup_volume(self, volume_info: Dict[str, Any]) -> Optional[str]:
        """
        对指定卷执行增量备份。

        返回: 备份目录路径，失败返回 None
        """
        drive_letter = volume_info['drive_letter']
        label = volume_info.get('label', 'NO_LABEL')
        label = self._sanitize_label(label)
        now = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
        backup_name = f"{now}_{label}"
        backup_dir = os.path.join(self.config.backup_root, backup_name)
        meta_path = os.path.join(backup_dir, self.META_FILENAME)

        ensure_dir(backup_dir)
        self.logger.info(f"[BackupEngine] 开始备份 {drive_letter} -> {backup_dir}")

        # 收集所有待备份文件
        files_to_backup = self._scan_volume(drive_letter)
        if not files_to_backup:
            self.logger.info(f"[BackupEngine] {drive_letter} 中无文件需要备份")
            return backup_dir

        # 加载已有元数据 (用于增量判断)
        existing_meta = self._load_meta(drive_letter)

        # 执行备份
        backed_up_count = 0
        skipped_count = 0
        total_size = 0

        for rel_path, abs_path in files_to_backup:
            dest_path = os.path.join(backup_dir, rel_path)
            dest_dir = os.path.dirname(dest_path)
            ensure_dir(dest_dir)

            # 增量判断: 三重校验
            if self._is_file_changed(rel_path, abs_path, existing_meta):
                if self._dry_run:
                    self.logger.debug(f"[DRY-RUN] 备份: {rel_path}")
                else:
                    try:
                        self._copy_file(abs_path, dest_path)
                        total_size += os.path.getsize(abs_path)
                        backed_up_count += 1
                    except Exception as e:
                        self.logger.error(f"备份失败 {rel_path}: {e}")
                        skipped_count += 1
            else:
                skipped_count += 1

        # 写入备份元数据
        if not self._dry_run:
            meta = self._build_meta(drive_letter, files_to_backup)
            try:
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, indent=2, ensure_ascii=False)
            except Exception as e:
                self.logger.error(f"写入备份元数据失败: {e}")

        self.logger.info(
            f"[BackupEngine] 备份完成: {backed_up_count} 文件已备份，"
            f"{skipped_count} 文件已跳过，合计 {format_size(total_size)}"
        )
        return backup_dir if backed_up_count > 0 else None

    def _scan_volume(self, drive_letter: str) -> List[tuple]:
        """递归扫描卷下所有文件，返回 [(相对路径, 绝对路径)]"""
        files = []
        try:
            for root, dirs, filenames in os.walk(drive_letter):
                # 跳过系统隐藏目录
                dirs[:] = [d for d in dirs if not self._skip_dir(d)]
                for fname in filenames:
                    if self._skip_file(fname):
                        continue
                    abs_path = os.path.join(root, fname)
                    rel_path = os.path.relpath(abs_path, drive_letter)
                    files.append((rel_path, abs_path))
        except Exception as e:
            self.logger.error(f"扫描卷 {drive_letter} 失败: {e}")
        return files

    def _skip_dir(self, dirname: str) -> bool:
        """判断是否跳过目录"""
        skip = {'System Volume Information', '$RECYCLE.BIN', 'RECYCLER',
                'Windows', 'Program Files', 'Program Files (x86)',
                'ProgramData', '$Recycle.Bin'}
        return dirname in skip or dirname.startswith('.')

    def _skip_file(self, filename: str) -> bool:
        """判断是否跳过文件"""
        skip_ext = {'.tmp', '.temp', '.lock', '.bak~'}
        skip_name = {'thumbs.db', 'desktop.ini', '.ds_store'}
        return (Path(filename).suffix.lower() in skip_ext or
                filename.lower() in skip_name)

    def _load_meta(self, drive_letter: str) -> Dict[str, Any]:
        """加载已有备份元数据 (最近一次)"""
        try:
            backup_root = self.config.backup_root
            if not os.path.exists(backup_root):
                return {}
            dirs = sorted([d for d in os.listdir(backup_root)
                           if os.path.isdir(os.path.join(backup_root, d))],
                          reverse=True)
            for d in dirs:
                meta_file = os.path.join(backup_root, d, self.META_FILENAME)
                if os.path.exists(meta_file):
                    with open(meta_file, 'r', encoding='utf-8') as f:
                        return json.load(f)
        except Exception:
            pass
        return {}

    def _is_file_changed(self, rel_path: str, abs_path: str,
                         existing_meta: Dict) -> bool:
        """
        三重校验判断文件是否变化:
        1. 文件大小
        2. 修改时间 (mtime)
        3. 小文件 (<5MB) MD5 哈希
        """
        try:
            stat = os.stat(abs_path)
            file_size = stat.st_size
            mtime = int(stat.st_mtime)

            prev = existing_meta.get('files', {}).get(rel_path)
            if prev is None:
                return True

            # 大小不同 → 变化
            if prev.get('size') != file_size:
                return True

            # 时间不同 → 变化 (大文件跳过 MD5)
            if prev.get('mtime') != mtime:
                if file_size < 5 * 1024 * 1024:
                    # 小文件比较 MD5
                    new_md5 = get_file_md5(abs_path)
                    return new_md5 != prev.get('md5', '')
                return True

            return False
        except Exception:
            return True

    def _build_meta(self, drive_letter: str,
                    files: List[tuple]) -> Dict[str, Any]:
        """构建备份元数据"""
        meta = {
            'volume': drive_letter,
            'backup_time': datetime.datetime.now().isoformat(),
            'file_count': len(files),
            'files': {}
        }
        for rel_path, abs_path in files:
            try:
                stat = os.stat(abs_path)
                entry = {
                    'size': stat.st_size,
                    'mtime': int(stat.st_mtime),
                }
                if stat.st_size < 5 * 1024 * 1024:
                    entry['md5'] = get_file_md5(abs_path)
                meta['files'][rel_path] = entry
            except Exception:
                pass
        return meta

    def _copy_file(self, src: str, dst: str) -> None:
        """复制文件，自动处理大文件流式写入"""
        file_size = os.path.getsize(src)
        threshold = self.config.get('large_file_threshold_mb', 200) * 1024 * 1024
        chunk_size = self.config.get('chunk_size_mb', 100) * 1024 * 1024

        if file_size > threshold:
            # 大文件: 流式分段复制，避免内存占用
            with open(src, 'rb') as sf, open(dst, 'wb') as df:
                while True:
                    chunk = sf.read(chunk_size)
                    if not chunk:
                        break
                    df.write(chunk)
            self.logger.debug(f"[BackupEngine] 大文件流式复制: {src} -> {dst}")
        else:
            shutil.copy2(src, dst)

    def _sanitize_label(self, label: str) -> str:
        """清理卷标，移除非法文件名字符"""
        invalid = '<>:"/\\|?*'
        for ch in invalid:
            label = label.replace(ch, '_')
        label = label.strip('. ')
        return label or 'USBVOLUME'


# ============================================================
# CloudSyncer 类 — SFTP 云端同步
# ============================================================

class CloudSyncer:
    """
    SFTP 断点续传同步:
    - 检测远程文件大小，实现大文件续传
    - 按 machine_id 隔离存储目录
    - 云端路径: sftp_remote_base/machine_id/backup_name/
    """

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self._sftp: Optional[paramiko.SFTPClient] = None
        self._ssh: Optional[paramiko.SSHClient] = None
        self._connected = False

    def connect(self) -> bool:
        """建立 SFTP 连接"""
        if not HAS_PARAMIKO:
            self.logger.warning("[CloudSyncer] paramiko 未安装，跳过云同步")
            return False

        if not self.config.enable_cloud_sync:
            self.logger.info("[CloudSyncer] 云端同步已禁用")
            return False

        try:
            self._ssh = paramiko.SSHClient()
            self._ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            self._ssh.connect(
                hostname=self.config.sftp_host,
                port=self.config.sftp_port,
                username=self.config.sftp_username,
                password=self.config.sftp_password,
                timeout=30,
                banner_timeout=30,
            )

            self._sftp = self._ssh.open_sftp()
            self._ensure_remote_dir(self.config.remote_path)
            self._ensure_remote_dir(self.config.system_remote_path)
            self._connected = True
            self.logger.info(f"[CloudSyncer] 已连接到 {self.config.sftp_host}:{self.config.sftp_port}")
            return True

        except Exception as e:
            self.logger.error(f"[CloudSyncer] SFTP 连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """关闭 SFTP 连接"""
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
            self._sftp = None
        if self._ssh:
            try:
                self._ssh.close()
            except Exception:
                pass
            self._ssh = None
        self._connected = False

    def _ensure_remote_dir(self, path: str) -> None:
        """确保远程目录存在 (递归创建)"""
        if not self._sftp:
            return
        dirs = path.strip('/').split('/')
        current = ''
        for d in dirs:
            current += '/' + d
            try:
                self._sftp.stat(current)
            except FileNotFoundError:
                try:
                    self._sftp.mkdir(current)
                    self.logger.debug(f"[CloudSyncer] 创建远程目录: {current}")
                except Exception as e:
                    self.logger.debug(f"[CloudSyncer] mkdir {current}: {e}")

    def upload_backup_dir(self, local_backup_dir: str) -> bool:
        """
        上传本地备份目录到云端 (断点续传)
        """
        if not self._connected or not self._sftp:
            self.logger.warning("[CloudSyncer] 未连接，跳过上传")
            return False

        backup_name = os.path.basename(local_backup_dir)
        remote_dir = f"{self.config.remote_path}/{backup_name}"

        try:
            self._ensure_remote_dir(remote_dir)
            self.logger.info(f"[CloudSyncer] 开始上传 {local_backup_dir} -> {remote_dir}")

            total_uploaded = 0
            for root, dirs, files in os.walk(local_backup_dir):
                for fname in files:
                    if fname == BackupEngine.META_FILENAME:
                        # 元数据文件上传时在远程也生成
                        local_path = os.path.join(root, fname)
                        remote_path = self._remote_path(local_path, local_backup_dir, remote_dir)
                        self._upload_file_with_resume(local_path, remote_path)
                        continue

                    local_path = os.path.join(root, fname)
                    rel_dir = os.path.relpath(root, local_backup_dir)
                    if rel_dir == '.':
                        rel_dir = ''
                    remote_file = (remote_dir + '/' + rel_dir + '/' + fname).replace('\\', '/')
                    uploaded = self._upload_file_with_resume(local_path, remote_file)
                    total_uploaded += uploaded

            self.logger.info(f"[CloudSyncer] 上传完成，共上传 {format_size(total_uploaded)}")
            return True

        except Exception as e:
            self.logger.error(f"[CloudSyncer] 上传失败: {e}")
            return False

    def _upload_file_with_resume(self, local_path: str, remote_path: str) -> int:
        """带断点续传的单个文件上传，返回上传字节数"""
        try:
            local_size = os.path.getsize(local_path)
        except Exception:
            return 0

        chunk_size = self.config.get('chunk_size_mb', 100) * 1024 * 1024

        # 尝试获取远程文件大小 (断点续传)
        remote_size = 0
        try:
            remote_size = self._sftp.stat(remote_path).st_size
        except FileNotFoundError:
            remote_size = 0
        except Exception:
            remote_size = 0

        uploaded_bytes = remote_size

        try:
            with open(local_path, 'rb') as f:
                if remote_size > 0:
                    f.seek(remote_size)
                    mode = 'ab'  # 追加
                    self.logger.debug(f"[CloudSyncer] 续传 {remote_path} (已上传 {format_size(remote_size)})")
                else:
                    mode = 'wb'

                with self._sftp.open(remote_path, mode) as remote_f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        remote_f.write(chunk)
                        uploaded_bytes += len(chunk)

            return uploaded_bytes

        except Exception as e:
            self.logger.error(f"[_upload_file] {remote_path}: {e}")
            return 0

    def _remote_path(self, local_path: str,
                     local_base: str, remote_base: str) -> str:
        """将本地路径映射为远程路径"""
        rel = os.path.relpath(local_path, local_base)
        return (remote_base + '/' + rel).replace('\\', '/')

    def upload_self_restore(self, exe_path: str) -> bool:
        """将 EXE 文件上传到自修复目录供 SelfHealer 恢复使用"""
        if not self._connected:
            return False
        try:
            remote_exe = f"{self.config.system_remote_path}/usb_backup.exe"
            self._ensure_remote_dir(self.config.system_remote_path)
            self._upload_file_with_resume(exe_path, remote_exe)
            self.logger.info(f"[CloudSyncer] 自修复文件已上传: {remote_exe}")
            return True
        except Exception as e:
            self.logger.error(f"[CloudSyncer] 自修复文件上传失败: {e}")
            return False


# ============================================================
# SpaceManager 类 — 空间管理与清理
# ============================================================

class SpaceManager:
    """
    管理备份存储空间:
    - 备份前预检: 剩余空间不足 5GB 时拒绝备份
    - 备份后清理: 自动删除最旧的备份目录直到空间满足阈值
    - 磁盘监控: 监控备份盘可用空间，接近阈值时主动清理
    """

    def __init__(self, config: ConfigManager, logger: Logger):
        self.config = config
        self.logger = logger
        self._min_free_gb = 5.0  # 最低保留空间 GB

    def pre_check(self, required_bytes: int) -> bool:
        """
        备份前检查: 所需空间 + 最低保留空间 <= 可用空间
        返回: True=空间充足可以备份
        """
        try:
            backup_root = self.config.backup_root
            drive = os.path.splitdrive(backup_root)[0] + '\\'
            free_bytes = shutil.disk_usage(drive).free
            threshold_bytes = self._min_free_gb * 1024 * 1024 * 1024

            if free_bytes < required_bytes + threshold_bytes:
                self.logger.warning(
                    f"[SpaceManager] 磁盘空间不足: 需要 {format_size(required_bytes)} + "
                    f"{format_size(threshold_bytes)} (最低保留)，"
                    f"可用 {format_size(free_bytes)}"
                )
                return False
            return True
        except Exception as e:
            self.logger.error(f"[SpaceManager] 空间检查失败: {e}")
            return True  # 检查失败时放行，避免阻断备份

    def post_cleanup(self) -> int:
        """
        备份后清理: 删除最旧备份直到剩余空间满足阈值
        返回: 清理的目录数量
        """
        try:
            backup_root = self.config.backup_root
            if not os.path.exists(backup_root):
                return 0

            drive = os.path.splitdrive(backup_root)[0] + '\\'
            free_bytes = shutil.disk_usage(drive).free
            threshold_bytes = self._min_free_gb * 1024 * 1024 * 1024

            if free_bytes >= threshold_bytes:
                return 0  # 空间充足，无需清理

            # 获取所有备份目录，按修改时间排序 (最旧在前)
            dirs = []
            for d in os.listdir(backup_root):
                full = os.path.join(backup_root, d)
                if os.path.isdir(full):
                    dirs.append((os.path.getmtime(full), full, d))

            dirs.sort()
            cleaned = 0

            for _, full, name in dirs:
                if free_bytes >= threshold_bytes:
                    break
                try:
                    dir_size = self._calc_dir_size(full)
                    shutil.rmtree(full)
                    free_bytes += dir_size
                    cleaned += 1
                    self.logger.info(f"[SpaceManager] 清理旧备份: {name} ({format_size(dir_size)})")
                except Exception as e:
                    self.logger.error(f"[SpaceManager] 删除 {name} 失败: {e}")

            self.logger.info(f"[SpaceManager] 清理完成: 删除 {cleaned} 个旧备份")
            return cleaned

        except Exception as e:
            self.logger.error(f"[SpaceManager] 清理失败: {e}")
            return 0

    def _calc_dir_size(self, path: str) -> int:
        """计算目录总大小"""
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except Exception:
                    pass
        return total


# ============================================================
# SelfHealer 类 — 程序自修复
# ============================================================

class SelfHealer:
    """
    自修复机制:
    - 每 60 秒检查 EXE 文件是否存在
    - 检测到 EXE 缺失则从云端 system/{machine_id}/ 下载恢复
    - 重建计划任务 USB Backup Service
    """

    def __init__(self, config: ConfigManager, logger: Logger,
                 syncer: CloudSyncer):
        self.config = config
        self.logger = logger
        self.syncer = syncer
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._exe_path = sys.executable if hasattr(sys, 'executable') else ''

    def start(self) -> None:
        if not self.config.self_heal_enabled:
            self.logger.info("[SelfHealer] 自修复已禁用")
            return
        self._running = True
        self._thread = threading.Thread(target=self._heal_loop,
                                         name='SelfHealer', daemon=True)
        self._thread.start()
        self.logger.info("[SelfHealer] 自修复线程已启动")

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)

    def _heal_loop(self) -> None:
        """自修复主循环"""
        interval = self.config.get('self_check_interval_seconds', 60)
        while self._running:
            time.sleep(interval)
            if not self.config.self_heal_enabled:
                continue
            try:
                self._check_and_heal()
            except Exception as e:
                self.logger.error(f"[SelfHealer] 自修复检查异常: {e}")

    def _check_and_heal(self) -> None:
        """检查 EXE 是否存在，缺失则从云端恢复"""
        exe_path = self._get_exe_path()
        if exe_path and os.path.exists(exe_path):
            self.logger.debug(f"[SelfHealer] EXE 存在: {exe_path}")
            return

        self.logger.warning(f"[SelfHealer] EXE 缺失，尝试从云端恢复: {exe_path}")
        if self._restore_from_cloud():
            self.logger.info("[SelfHealer] EXE 恢复成功")
            self._recreate_scheduled_task()
        else:
            self.logger.error("[SelfHealer] EXE 恢复失败")

    def _get_exe_path(self) -> str:
        """获取当前 EXE 路径 (PyInstaller 打包后为 EXE 自身)"""
        if getattr(sys, 'frozen', False):
            return sys.executable
        return os.path.abspath(sys.argv[0])

    def _restore_from_cloud(self) -> bool:
        """从云端下载 EXE 恢复"""
        if not self.syncer._connected:
            if not self.syncer.connect():
                return False

        remote_exe = f"{self.config.system_remote_path}/usb_backup.exe"
        local_exe = self._get_exe_path()
        local_dir = os.path.dirname(local_exe)

        try:
            ensure_dir(local_dir)
            self.logger.info(f"[SelfHealer] 从 {remote_exe} 下载 EXE...")

            chunk_size = self.config.get('chunk_size_mb', 100) * 1024 * 1024
            with self.syncer._sftp.open(remote_exe, 'rb') as remote_f:
                with open(local_exe, 'wb') as local_f:
                    while True:
                        chunk = remote_f.read(chunk_size)
                        if not chunk:
                            break
                        local_f.write(chunk)

            self.logger.info(f"[SelfHealer] EXE 已恢复到: {local_exe}")
            return True

        except Exception as e:
            self.logger.error(f"[SelfHealer] 下载 EXE 失败: {e}")
            return False

    def _recreate_scheduled_task(self) -> None:
        """重建开机自启计划任务"""
        exe_path = self._get_exe_path()
        task_name = "USB Backup Service"
        try:
            subprocess.run(
                ['schtasks', '/create', '/tn', task_name,
                 '/tr', f'"{exe_path}"', '/sc', 'onlogon',
                 '/rl', 'highest', '/f'],
                capture_output=True, timeout=30
            )
            self.logger.info(f"[SelfHealer] 计划任务已重建: {task_name}")
        except Exception as e:
            self.logger.error(f"[SelfHealer] 重建计划任务失败: {e}")


# ============================================================
# GuardianProcess 类 — 双进程守护
# ============================================================

class GuardianProcess:
    """
    双进程守护机制:
    - 主进程正常参数启动，守护进程使用 --guardian 参数启动
    - 守护进程每 5 秒通过 Windows API 检测主进程是否存活
    - 主进程检测到守护进程消失则重启守护；反之亦然
    - 两者 PID 均注册到内核驱动保护
    """

    def __init__(self, config: ConfigManager, logger: Logger,
                 driver_ctrl: DriverController):
        self.config = config
        self.logger = logger
        self.driver_ctrl = driver_ctrl
        self._running = False
        self._guardian_pid: Optional[int] = None
        self._self_pid = os.getpid()
        self._lock = threading.Lock()

    def start(self, guardian_pid: Optional[int] = None) -> bool:
        """启动守护，参数 guardian_pid 为对方进程 PID"""
        if not self.config.guardian_enabled:
            self.logger.info("[Guardian] 守护进程已禁用")
            return True

        self._guardian_pid = guardian_pid
        self._running = True

        # 注册自身保护
        if self.driver_ctrl:
            self.driver_ctrl.protect_pid(self._self_pid)

        # 注册对方进程保护
        if guardian_pid:
            self.driver_ctrl.protect_pid(guardian_pid)

        # 启动守护线程
        t = threading.Thread(target=self._guardian_loop,
                             name=f'Guardian-{self._self_pid}',
                             daemon=True)
        t.start()
        self.logger.info(f"[Guardian] 守护线程已启动, my_pid={self._self_pid}, "
                          f"guardian_pid={guardian_pid}")
        return True

    def stop(self) -> None:
        self._running = False

    def _guardian_loop(self) -> None:
        """守护主循环: 互相检测对方进程"""
        interval = self.config.get('guardian_check_interval_seconds', 5)
        while self._running:
            time.sleep(interval)
            if not self.config.guardian_enabled:
                continue
            try:
                self._check_peer()
            except Exception as e:
                self.logger.error(f"[Guardian] 守护检查异常: {e}")

    def _check_peer(self) -> None:
        """检测对方进程是否存活，存活则更新内核保护列表"""
        if self._guardian_pid is None:
            return

        alive = self._is_process_alive(self._guardian_pid)
        if not alive:
            self.logger.warning(f"[Guardian] 对方守护进程 {self._guardian_pid} 消失")
            # 尝试重启对方
            self._restart_peer()

    def _is_process_alive(self, pid: int) -> bool:
        """通过 Windows API 检查进程是否存在"""
        if not HAS_WINDOWS:
            import signal
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        try:
            handle = ctypes.windll.kernel32.OpenProcess(
                0x1000,  # PROCESS_QUERY_LIMITED_INFORMATION
                False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return False

    def _restart_peer(self) -> None:
        """重启对方守护进程"""
        try:
            exe_path = self._get_exe_path()
            subprocess.Popen(
                [exe_path, '--guardian'],
                creationflags=subprocess.CREATE_NEW_CONSOLE
                if not getattr(sys, 'frozen', False) else 0,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            self.logger.info("[Guardian] 对方守护进程已重启")
        except Exception as e:
            self.logger.error(f"[Guardian] 重启对方失败: {e}")

    def _get_exe_path(self) -> str:
        if getattr(sys, 'frozen', False):
            return sys.executable
        return os.path.abspath(sys.argv[0])


# ============================================================
# USBBackupApp 类 — 应用总控
# ============================================================

class USBBackupApp:
    """
    应用总控类，整合所有子系统:
    - 初始化所有组件
    - 启动 USB 监控线程
    - 从事件队列消费 USB 插入事件并调度备份
    - 协调云端同步、空间管理、自修复、进程守护
    """

    def __init__(self, config_path: Optional[str] = None,
                 is_guardian: bool = False,
                 guardian_pid: Optional[int] = None):
        self._is_guardian = is_guardian
        self._guardian_pid = guardian_pid
        self._running = False
        self._threads: List[threading.Thread] = []
        self._stop_event = threading.Event()

        # 配置与日志
        self.config = ConfigManager(config_path)
        self.logger_wrapper = Logger(self.config)
        self.logger = self.logger_wrapper

        # 事件队列 (USB 插入事件)
        self.event_queue: queue.Queue = queue.Queue()

        # 组件
        self.driver_ctrl: Optional[DriverController] = None
        self.usb_detector: Optional[USBDetector] = None
        self.backup_engine: Optional[BackupEngine] = None
        self.cloud_syncer: Optional[CloudSyncer] = None
        self.space_manager: Optional[SpaceManager] = None
        self.self_healer: Optional[SelfHealer] = None
        self.guardian: Optional[GuardianProcess] = None

    def initialize(self) -> bool:
        """初始化所有子系统"""
        self.logger.info("=" * 60)
        self.logger.info(" USB Backup Service 启动")
        self.logger.info(f" 模式: {'守护进程' if self._is_guardian else '主进程'}")
        self.logger.info(f" 机器ID: {self.config.machine_id}")
        self.logger.info(f" 备份目录: {self.config.backup_root}")
        self.logger.info(f" 云端同步: {self.config.enable_cloud_sync}")
        self.logger.info("=" * 60)

        # 确保备份目录存在
        ensure_dir(self.config.backup_root)

        # 初始化驱动控制器
        self.driver_ctrl = DriverController(self.config, self.logger)
        if not self.driver_ctrl.initialize():
            self.logger.warning("内核驱动初始化失败，将以无驱动模式运行")

        # 初始化备份引擎
        self.backup_engine = BackupEngine(self.config, self.logger)

        # 初始化云端同步
        self.cloud_syncer = CloudSyncer(self.config, self.logger)
        if self.config.enable_cloud_sync:
            self.cloud_syncer.connect()

        # 初始化空间管理器
        self.space_manager = SpaceManager(self.config, self.logger)

        # 初始化自修复
        self.self_healer = SelfHealer(self.config, self.logger, self.cloud_syncer)

        # 初始化双进程守护
        self.guardian = GuardianProcess(self.config, self.logger, self.driver_ctrl)
        if self._is_guardian:
            # 守护进程模式: 保护自身并通知对方自己已启动
            pass
        else:
            # 主进程模式: 启动守护并传入对方 PID
            self.guardian.start(guardian_pid=self._guardian_pid)

        # 初始化 USB 探测器 (主进程模式才需要)
        if not self._is_guardian:
            self.usb_detector = USBDetector(self.config, self.logger, self.event_queue)
            self.usb_detector.start()
            # 立即进行一次初始扫描 (检测已插入的 USB)
            self._scan_existing_usb()

        self.logger.info("[App] 所有子系统初始化完成")
        return True

    def _scan_existing_usb(self) -> None:
        """程序启动时扫描已插入的 USB 设备并立即备份"""
        if not HAS_WINDOWS:
            return
        try:
            pythoncom.CoInitialize()
            try:
                c = wmi.WMI()
                for vol in c.Win32_Volume(DriveType=2):  # DRIVE_REMOVABLE
                    drive = getattr(vol, 'DriveLetter', None)
                    if drive:
                        drive = drive.rstrip(':') + ':'
                        volume_info = {
                            'drive_letter': drive,
                            'label': getattr(vol, 'Label', '') or '',
                            'serial': str(getattr(vol, 'SerialNumber', 0)),
                            'file_system': getattr(vol, 'FileSystem', '') or '',
                            'total_size': int(getattr(vol, 'Capacity', 0) or 0),
                            'free_space': int(getattr(vol, 'FreeSpace', 0) or 0),
                        }
                        self.event_queue.put(('INSERT', volume_info))
            finally:
                pythoncom.CoUninitialize()
        except Exception as e:
            self.logger.debug(f"[_scan_existing_usb] {e}")

    def run(self) -> None:
        """主循环: 消费事件队列，执行备份 + 云同步"""
        self._running = True
        self.logger.info("[App] 主循环开始运行")

        # 启动自修复线程
        if self.self_healer:
            self.self_healer.start()

        while self._running and not self._stop_event.is_set():
            try:
                # 非阻塞获取事件，超时则继续循环
                try:
                    event_type, volume_info = self.event_queue.get(timeout=1)
                except queue.Empty:
                    continue

                if event_type == 'INSERT':
                    self._handle_usb_insert(volume_info)

            except KeyboardInterrupt:
                self.logger.info("[App] 收到键盘中断，停止服务")
                break
            except Exception as e:
                self.logger.error(f"[App] 主循环异常: {e}")

        self.shutdown()

    def _handle_usb_insert(self, volume_info: Dict[str, Any]) -> None:
        """处理 USB 插入事件: 备份 + 云同步"""
        drive = volume_info.get('drive_letter', '?')
        self.logger.info(f"[App] 处理 USB 插入: {drive}")

        # 步骤 1: 备份
        backup_dir = self.backup_engine.backup_volume(volume_info)
        if not backup_dir:
            self.logger.info(f"[App] 无新数据备份: {drive}")
            return

        # 步骤 2: 空间清理
        if self.space_manager:
            self.space_manager.post_cleanup()

        # 步骤 3: 云端同步
        if self.cloud_syncer and self.cloud_syncer._connected:
            self.cloud_syncer.upload_backup_dir(backup_dir)

        self.logger.info(f"[App] USB {drive} 处理完成")

    def shutdown(self) -> None:
        """优雅关闭所有子系统"""
        self.logger.info("[App] 开始关闭服务...")
        self._running = False

        if self.usb_detector:
            self.usb_detector.stop()

        if self.self_healer:
            self.self_healer.stop()

        if self.guardian:
            self.guardian.stop()

        if self.cloud_syncer:
            self.cloud_syncer.disconnect()

        if self.driver_ctrl:
            self.driver_ctrl.shutdown()

        self.logger.info("[App] 服务已关闭")

    def run_as_guardian(self, peer_pid: Optional[int] = None) -> None:
        """以守护进程模式运行 (仅监控，不备份)"""
        self._is_guardian = True
        self._guardian_pid = peer_pid
        self.initialize()

        # 守护进程持续监控对方
        interval = self.config.get('guardian_check_interval_seconds', 5)
        while self._running and not self._stop_event.is_set():
            time.sleep(interval)

        self.shutdown()


# ============================================================
# 程序入口
# ============================================================

def parse_args():
    """解析命令行参数"""
    args = {
        'config': None,
        'guardian': False,
        'guardian_pid': None,
        'no_cloud': False,
        'dry_run': False,
        'log_level': None,
    }

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ('--config', '-c') and i + 1 < len(argv):
            args['config'] = argv[i + 1]
            i += 2
        elif arg == '--guardian':
            args['guardian'] = True
            i += 1
        elif arg in ('--guardian-pid', '-gp') and i + 1 < len(argv):
            try:
                args['guardian_pid'] = int(argv[i + 1])
            except ValueError:
                print(f"错误: 无效的 PID 值 '{argv[i + 1]}'")
            i += 2
        elif arg == '--no-cloud':
            args['no_cloud'] = True
            i += 1
        elif arg == '--dry-run':
            args['dry_run'] = True
            i += 1
        elif arg in ('--log-level', '-l') and i + 1 < len(argv):
            args['log_level'] = argv[i + 1]
            i += 2
        else:
            i += 1

    return args


def main():
    """主入口函数"""
    args = parse_args()

    # 加载配置 (早期加载以读取 log_level)
    config = ConfigManager(args['config'])

    # 覆盖配置项
    if args['no_cloud']:
        config.set('enable_cloud_sync', False)
    if args['log_level']:
        config.set('log_level', args['log_level'])
    if args['dry_run']:
        sys._dry_run = True

    # 日志
    logger_wrapper = Logger(config)
    logger = logger_wrapper.get_logger()

    # 检查管理员权限 (非必需，但提示)
    if HAS_WINDOWS and not is_admin():
        logger.warning("建议以管理员权限运行以获得最佳兼容性")

    # 启动应用
    if args['guardian']:
        # 守护进程模式
        app = USBBackupApp(config_path=args['config'],
                           is_guardian=True,
                           guardian_pid=args['guardian_pid'])
        app.initialize()
        logger.info("[Main] 守护进程模式运行")
        # 守护进程简单循环，等待信号
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            app.shutdown()
    else:
        # 主进程模式
        app = USBBackupApp(config_path=args['config'], is_guardian=False)
        if not app.initialize():
            logger.error("[Main] 初始化失败")
            return 1
        try:
            app.run()
        except KeyboardInterrupt:
            logger.info("[Main] 收到中断信号")
            app.shutdown()
        except Exception as e:
            logger.error(f"[Main] 未处理异常: {e}")
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
