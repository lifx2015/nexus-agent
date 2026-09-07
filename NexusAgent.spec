# -*- mode: python ; coding: utf-8 -*-
"""Nexus Agent Windows 打包配置（PyInstaller onedir）。

用法：仓库根目录执行
  backend/.venv/Scripts/pyinstaller --noconfirm NexusAgent.spec
产物：dist/NexusAgent/NexusAgent.exe（免安装绿色目录）

结构说明：
  - 入口 run_desktop.py：起 FastAPI（uvicorn 后台线程）+ pywebview 窗口
  - backend/app/static 随 datas 带入 _internal/app/static（main.py 冻结分支会指向 sys._MEIPASS）
  - uvicorn 的 loops/protocols 与 pywebview 的平台后端均为运行时动态导入，必须显式 hiddenimports
"""
import os
from PyInstaller.utils.hooks import collect_all

ROOT = os.path.abspath(SPECPATH)

datas, binaries, hiddenimports = [], [], []
for pkg in ("webview", "pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += [
    # 应用（uvicorn 以字符串 "app.main:app" 动态导入，静态分析不可见）
    "app",
    "app.main",
    # uvicorn 动态加载的组件
    "uvicorn.logging",
    "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl", "uvicorn.protocols.http.httptools_impl",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "uvicorn.middleware", "uvicorn.middleware.proxy_headers",
]

a = Analysis(
    ["run_desktop.py"],
    pathex=[os.path.join(ROOT, "backend"), os.path.join(ROOT, "desktop")],
    binaries=binaries,
    datas=datas + [(os.path.join(ROOT, "backend", "app", "static"), "app/static")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest"],
    noarchive=False,
)

# 系统自带 CRT（ucrtbase 等）不必携带；且本机依赖解析可能命中
# "Windows Performance Toolkit" 下的受限副本导致复制失败，按来源路径过滤
a.binaries = TOC(
    x for x in a.binaries if "windows kits" not in str(x[1]).lower()
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="NexusAgent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 桌面应用，无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "ico.ico"),   # exe 文件/任务栏/窗口标题栏图标
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="NexusAgent",
)
