"""项目路径工具：会话日志目录名 ↔ 项目路径 的互转。

Claude / Qoder / WorkBuddy 等用「把项目路径里的非字母数字全部替换为 -」
来命名会话日志目录（Windows 下盘符后紧跟一个 -）。该编码有损
（路径里本带的 - 与分隔符不可区分），解码结果只作候选 / 兜底；
当日志行内带显式 cwd 时，以 cwd 为准。
"""
from __future__ import annotations

import re

_DRIVE_RE = re.compile(r"^[A-Za-z]-")


def decode_project_dir(name: str) -> str:
    """把会话日志目录名解码为候选项目路径（有损，仅作兜底）。

    Windows: E--workspace-code-pal → E:\\workspace\\code-pal
             C--Users-li           → C:\\Users\\li
    POSIX:   -Users-li-proj        → /Users/li/proj
    """
    if not name:
        return ""
    import os

    if os.name == "nt":
        if _DRIVE_RE.match(name):
            drive = name[0].upper()
            rest = name[2:].lstrip("-")
            return f"{drive}:\\" + rest.replace("-", "\\")
        return "\\" + name.replace("-", "\\")
    body = name[1:] if name.startswith("-") else name
    return "/" + body.replace("-", "/")
