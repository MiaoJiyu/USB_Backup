# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['usb_backup.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 配置文件
        ('config.json', '.', 'DATA'),
        # 图标文件 (如果有)
    ],
    hiddenimports=[
        # 核心依赖 (静态导入检测失败, 必须显式声明)
        'encodings.idna',
        'encodings.ascii',
        'encodings.base64',
        'encodings.gbk',
        'encodings.utf_8',
        # Windows API
        'win32api',
        'win32con',
        'win32file',
        'win32com',
        'win32com.client',
        'win32com.shell.shell',
        'win32api_struct',
        'pythoncom',
        'pywintypes',
        # 第三方库
        'wmi',
        'paramiko',
        'cryptography',
        'cryptography.x509',
        'cryptography.hazmat.primitives',
        'cryptography.hazmat.primitives.serialization',
        'nacl',
        'nacl.signing',
        'nacl.encoding',
        'nacl.bindings',
        'hmac',
        'hashlib',
        'json',
        'logging',
        'threading',
        'queue',
        'ctypes',
        'ctypes.wintypes',
        'shutil',
        'os',
        'sys',
        'time',
        'datetime',
        'subprocess',
        'platform',
        'pathlib',
        'uuid',
        'struct',
        'socket',
        'logging.handlers',
        'logging.handlers.RotatingFileHandler',
        'contextlib',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # 排除不需要的模块 (减小体积)
        'tkinter',
        'matplotlib',
        'numpy',
        'scipy',
        'pandas',
        'PIL',
        'Pillow',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'PyQt5',
        'PyQt6',
        'PySide2',
        'PySide6',
        'PyGTK',
        'gtk',
        'gi',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='USB_Backup',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # 关键: 隐藏控制台窗口 (静默运行)
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,          # 如有图标: icon='icon.ico'
    version=None,       # 如有版本信息: version='version_info.txt'
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='USB_Backup',
)

# ---- 辅助目标: 包含驱动加载器的独立 EXE ----
driver_exe = EXE(
    pyz,
    ['driver_loader.py'],
    [],
    exclude_binaries=True,
    name='DriverLoader',
    debug=False,
    strip=False,
    upx=True,
    console=False,
    icon=None,
)

driver_coll = COLLECT(
    driver_exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    name='DriverLoader',
)
