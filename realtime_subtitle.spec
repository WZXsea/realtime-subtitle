# -*- mode: python ; coding: utf-8 -*-

import os

APP_VERSION = "3.0.0"
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect any data files needed by dependencies
# MLX components need their data (like libraries/resources) correctly bundled.
datas = [
]

# Core MLX/Whisper data collection
datas += collect_data_files('mlx')
datas += collect_data_files('mlx_whisper')
datas += collect_data_files('tokenizers')

# Ensure we include the models directory if it exists locally
if os.path.exists('models'):
    datas.append(('models', 'models'))

binaries = []

# Collect hidden imports for all components
hidden_mlx = collect_submodules('mlx')
hidden_mlx_whisper = collect_submodules('mlx_whisper')

hiddenimports = [
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtWidgets',
    'sounddevice',
    'numpy',
    'openai',
    'watchdog',
    'requests',
    'httpx',
    'httpcore',
    'objc',
    'AppKit',
] + hidden_mlx + hidden_mlx_whisper

a = Analysis(
    ['dashboard.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PySide2', 'PySide6',
        'tensorflow', 'torch', 'tensorboard',
        'matplotlib', 'pandas', 'plotly', 'bokeh',
        'notebook', 'jupyter', 'IPython', 'jedi',
        'cv2', 'PIL', 'tkinter',
        'distutils'
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
    name='译世界',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False, # DISABLED: UPX often corrupts Metal-linked binaries on ARM64
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False, # DISABLED: Consistency with EXE
    upx_exclude=[],
    name='译世界',
)

app = BUNDLE(
    coll,
    name='译世界.app',
    icon='assets/transworld.icns',
    bundle_identifier='com.weizixun.realtime.subtitle',
    info_plist={
        'NSMicrophoneUsageDescription': '需要访问音频输入（如 BlackHole）来捕捉系统声音进行实时翻译。',
        'NSHighResolutionCapable': 'True',
        'LSMinimumSystemVersion': '14.0', # mlx-whisper requires macOS 14+
        'LSUIElement': 'False', # Set to True if it should be a "background" app, but we have an overlay
        'CFBundleShortVersionString': APP_VERSION,
        'CFBundleVersion': APP_VERSION,
    },
)
