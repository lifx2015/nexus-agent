"""项目「打开方式」解析。

服务器进程（桌面壳/服务方式启动）的 PATH 往往比用户终端少目录，
且 Trae / OpenCode 等 GUI 编辑器未必把 CLI 注册进 PATH，
因此按三级策略把「命令名」解析为本机可执行文件：

1. PATH（shutil.which）
2. 常见安装位置 glob（npm 全局、~/.local/bin、%LOCALAPPDATA%/Programs、Program Files 等）
3. Windows 注册表卸载表：DisplayName 含关键字 → InstallLocation/bin/<cmd>.cmd；
   无 InstallLocation 时用 DisplayIcon 指向的主程序（OpenCode 桌面版即此形态）

结果按进程缓存（TTL 60s）——本机新装工具一分钟内自动被识别，无需重启。
"""
from __future__ import annotations

import glob
import os
import shutil
import time
from pathlib import Path

_TTL = 60.0
_CACHE: tuple[float, list[dict]] | None = None

# 数据驱动目录：key/label/kind 对外；bin 检测命令；keywords 注册表匹配；
# globs 常见安装位置；arg_mode=agent 启动时是否把项目路径作为参数传入
CATALOG: list[dict] = [
    {
        "key": "explorer", "label": "资源管理器", "kind": "system",
        "bin": "", "keywords": [], "globs": [], "arg_mode": "none",
    },
    {
        "key": "vscode", "label": "VS Code", "kind": "editor", "bin": "code",
        "keywords": ["visual studio code", "vs code", "vscode"],
        "globs": [
            "%LOCALAPPDATA%/Programs/Microsoft VS Code/bin/code*.cmd",
            "C:/Program Files/Microsoft VS Code/bin/code*.cmd",
            "C:/Program Files (x86)/Microsoft VS Code/bin/code*.cmd",
        ],
        "arg_mode": "path",
    },
    {
        "key": "cursor", "label": "Cursor", "kind": "editor", "bin": "cursor",
        "keywords": ["cursor"],
        "globs": [
            "%LOCALAPPDATA%/Programs/cursor*/bin/cursor*.cmd",
            "%LOCALAPPDATA%/Programs/cursor*/resources/app/bin/cursor*.cmd",
            "C:/Program Files/cursor*/bin/cursor*.cmd",
        ],
        "arg_mode": "path",
    },
    {
        "key": "trae", "label": "Trae", "kind": "editor", "bin": "trae",
        "keywords": ["trae"],
        "globs": [
            "%LOCALAPPDATA%/Programs/Trae*/bin/trae*.cmd",
            "%LOCALAPPDATA%/Trae*/bin/trae*.cmd",
            "C:/Program Files/Trae*/bin/trae*.cmd",
        ],
        "arg_mode": "path",
    },
    {
        "key": "windsurf", "label": "Windsurf", "kind": "editor", "bin": "windsurf",
        "keywords": ["windsurf"],
        "globs": [
            "%LOCALAPPDATA%/Programs/Windsurf*/bin/windsurf*.cmd",
            "C:/Program Files/Windsurf*/bin/windsurf*.cmd",
        ],
        "arg_mode": "path",
    },
    {
        "key": "qoder", "label": "Qoder", "kind": "editor", "bin": "qoder",
        "keywords": ["qoder"],
        "globs": [
            "%LOCALAPPDATA%/Programs/Qoder*/bin/qoder*.cmd",
            "C:/Program Files/Qoder*/bin/qoder*.cmd",
        ],
        "arg_mode": "path",
    },
    {
        "key": "opencode", "label": "OpenCode", "kind": "agent", "bin": "opencode",
        "keywords": ["opencode"],
        "globs": [
            "%APPDATA%/npm/opencode*.cmd",
            "~/.opencode/bin/opencode*.exe",
            "~/.local/bin/opencode*",
        ],
        "arg_mode": "path",  # opencode CLI / 桌面版均接受项目路径参数
    },
    {
        "key": "codex", "label": "Codex CLI", "kind": "agent", "bin": "codex",
        "keywords": ["codex"],
        "globs": ["%APPDATA%/npm/codex*.cmd", "~/.local/bin/codex*"],
        "arg_mode": "none",
    },
    {
        "key": "claude", "label": "Claude Code", "kind": "agent", "bin": "claude",
        "keywords": ["claude code"],  # 刻意不含 "claude"，避免误认 Claude 桌面版
        "globs": ["%APPDATA%/npm/claude*.cmd", "~/.local/bin/claude*"],
        "arg_mode": "none",
    },
    {
        "key": "gemini", "label": "Gemini CLI", "kind": "agent", "bin": "gemini",
        "keywords": ["gemini cli"],
        "globs": ["%APPDATA%/npm/gemini*.cmd", "~/.local/bin/gemini*"],
        "arg_mode": "none",
    },
]


def _registry_entries() -> list[dict]:
    """卸载表 [{name, install_location, display_icon}]；非 Windows 返回空。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:
        return []
    roots = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    )
    out: list[dict] = []
    for root, path in roots:
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            continue
        with key:
            try:
                count = winreg.QueryInfoKey(key)[0]
            except OSError:
                continue
            for i in range(count):
                name = loc = icon = ""
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                        try:
                            name = str(winreg.QueryValueEx(sub, "DisplayName")[0] or "")
                        except OSError:
                            pass
                        try:
                            loc = str(winreg.QueryValueEx(sub, "InstallLocation")[0] or "")
                        except OSError:
                            pass
                        try:
                            icon = str(winreg.QueryValueEx(sub, "DisplayIcon")[0] or "")
                        except OSError:
                            pass
                except OSError:
                    continue
                if name and (loc or icon):
                    out.append({"name": name, "install_location": loc, "display_icon": icon})
    return out


def _expand(raw: str) -> str:
    return os.path.expandvars(os.path.expanduser(raw))


def _from_registry_entry(item: dict, bin_: str, kind: str) -> str | None:
    """从单个卸载项提取可执行文件：InstallLocation/bin/<bin>* 优先，DisplayIcon 兜底。"""
    loc = item["install_location"]
    if loc:
        base = Path(_expand(loc))
        if base.is_dir():
            for pat in (f"bin/{bin_}*.cmd", f"bin/{bin_}*.exe"):
                hits = sorted(base.glob(pat))
                if hits:
                    return str(hits[0])
            if kind != "editor":
                # 桌面版（Electron 整包）无 bin/ 目录：取安装目录主程序
                hits = sorted(p for p in base.glob("*.exe") if "uninstall" not in p.name.lower())
                if hits:
                    return str(hits[0])
    icon = item["display_icon"].split(",")[0].strip()
    if icon and Path(icon).is_file():
        return icon
    return None


def resolve(entry: dict, *, registry: list[dict] | None = None) -> dict:
    """把目录项解析为 {**entry, resolved, available}。"""
    bin_ = entry["bin"]
    resolved = shutil.which(bin_) if bin_ else ""
    if not resolved and bin_:
        for pattern in entry.get("globs", ()):
            hits = sorted(glob.glob(_expand(pattern)))
            if hits:
                resolved = hits[0]
                break
    if not resolved and bin_:
        items = _registry_entries() if registry is None else registry
        for item in items:
            if any(k.lower() in item["name"].lower() for k in entry.get("keywords", ())):
                hit = _from_registry_entry(item, bin_, entry.get("kind", ""))
                if hit:
                    resolved = hit
                    break
    return {**entry, "resolved": resolved or "", "available": bool(resolved) or not bin_}


def state(*, refresh: bool = False) -> list[dict]:
    """全部打开方式的解析结果（带 TTL 缓存）。"""
    global _CACHE
    now = time.time()
    if not refresh and _CACHE is not None and now - _CACHE[0] < _TTL:
        return _CACHE[1]
    resolved = [resolve(e) for e in CATALOG]
    _CACHE = (now, resolved)
    return resolved
