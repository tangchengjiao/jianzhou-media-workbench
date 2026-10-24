# -*- mode: python ; coding: utf-8 -*-
# 见舟工作台 · PyInstaller 打包配置（预留，首版不执行）
# 用法见 packaging/打包说明.md

import sys
from pathlib import Path

SPEC_LOCATION = Path(SPECPATH).resolve()
SPEC_DIR = SPEC_LOCATION.parent if SPEC_LOCATION.is_file() else SPEC_LOCATION
ROOT = SPEC_DIR.parent if SPEC_DIR.name == "packaging" else SPEC_DIR

if not (ROOT / "app.py").is_file() or not (ROOT / "frontend").is_dir():
    raise RuntimeError(f"无法解析项目根目录：{ROOT}")

a = Analysis(
    [str(ROOT / "app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 前端静态资源整体打入（免构建 Vue3，无 Node 依赖）
        (str(ROOT / "frontend"), "frontend"),
    ],
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tests", "scripts", "IPython", "jupyter", "notebook",
        "matplotlib", "numpy", "pandas", "scipy", "sklearn", "sympy",
        "torch", "tensorflow", "cv2", "tkinter",
        "PyQt5", "PyQt6", "PySide2", "PySide6",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="见舟工作台",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 关闭控制台即会终止本地服务；错误详情同时写入本地日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # 可后续替换为 packaging/jianzhou.ico
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="见舟工作台.app",
        icon=None,
        bundle_identifier="com.jianzhou.media-workbench",
        info_plist={
            "CFBundleName": "见舟工作台",
            "CFBundleDisplayName": "见舟工作台",
            "CFBundleShortVersionString": "2.1.0",
            "CFBundleVersion": "2.1.0",
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
        },
    )
