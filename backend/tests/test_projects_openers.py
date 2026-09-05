"""打开方式解析测试：PATH → globs → 注册表卸载表（全部用 tmp 桩，不依赖本机安装状态）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.projects import openers as openers_mod


def test_resolve_via_which(tmp_path, monkeypatch):
    monkeypatch.setattr(
        openers_mod.shutil, "which",
        lambda b: f"C:/fake/{b}.CMD" if b == "code" else None,
    )
    entry = {"key": "vscode", "kind": "editor", "bin": "code",
             "keywords": [], "globs": [str(tmp_path / "nope" / "*.cmd")], "arg_mode": "path"}
    out = openers_mod.resolve(entry)
    assert out["available"] is True
    assert out["resolved"] == "C:/fake/code.CMD"


def test_resolve_via_glob(tmp_path, monkeypatch):
    monkeypatch.setattr(openers_mod.shutil, "which", lambda b: None)
    shim = tmp_path / "npm"
    shim.mkdir()
    (shim / "opencode.cmd").write_text("@echo off", encoding="utf-8")
    entry = {"key": "opencode", "kind": "agent", "bin": "opencode",
             "keywords": ["opencode"], "globs": [str(shim / "opencode*.cmd")],
             "arg_mode": "path"}
    out = openers_mod.resolve(entry)
    assert out["available"] is True
    assert out["resolved"] == str(shim / "opencode.cmd")


def test_resolve_via_registry_install_location(tmp_path, monkeypatch):
    monkeypatch.setattr(openers_mod.shutil, "which", lambda b: None)
    install = tmp_path / "Trae CN"
    (install / "bin").mkdir(parents=True)
    (install / "bin" / "trae.cmd").write_text("@echo off", encoding="utf-8")
    (install / "bin" / "trae-cn.cmd").write_text("@echo off", encoding="utf-8")
    (install / "Trae CN.exe").write_text("M", encoding="utf-8")
    registry = [
        {"name": "TraeCode CN (User)", "install_location": str(install) + "\\", "display_icon": ""},
    ]
    entry = {"key": "trae", "kind": "editor", "bin": "trae",
             "keywords": ["trae"], "globs": [], "arg_mode": "path"}
    out = openers_mod.resolve(entry, registry=registry)
    assert out["available"] is True
    assert out["resolved"].startswith(str(install))
    assert out["resolved"].endswith(".cmd")  # CLI shim 优先于主程序


def test_resolve_via_registry_display_icon(tmp_path, monkeypatch):
    """OpenCode 桌面版没有 InstallLocation，DisplayIcon 指向主程序。"""
    monkeypatch.setattr(openers_mod.shutil, "which", lambda b: None)
    exe = tmp_path / "@opencode-aidesktop"
    exe.mkdir()
    main = exe / "OpenCode.exe"
    main.write_text("M", encoding="utf-8")
    (exe / "Uninstall OpenCode.exe").write_text("U", encoding="utf-8")
    registry = [
        {"name": "OpenCode 1.18.26", "install_location": "",
         "display_icon": str(main) + ",0"},
    ]
    entry = {"key": "opencode", "kind": "agent", "bin": "opencode",
             "keywords": ["opencode"], "globs": [], "arg_mode": "path"}
    out = openers_mod.resolve(entry, registry=registry)
    assert out["available"] is True
    assert out["resolved"] == str(main)  # 卸载器被排除


def test_resolve_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(openers_mod.shutil, "which", lambda b: None)
    entry = {"key": "trae", "kind": "editor", "bin": "trae",
             "keywords": ["trae"], "globs": [str(tmp_path / "none" / "*.cmd")],
             "arg_mode": "path"}
    out = openers_mod.resolve(entry, registry=[])
    assert out["available"] is False and out["resolved"] == ""


def test_state_contains_full_catalog():
    st = openers_mod.state(refresh=True)
    keys = {o["key"] for o in st}
    assert {"explorer", "vscode", "cursor", "trae", "windsurf", "qoder",
            "opencode", "codex", "claude", "gemini"} <= keys
    for o in st:
        assert "available" in o and "resolved" in o and "arg_mode" in o
    assert next(o for o in st if o["key"] == "explorer")["available"] is True


def test_real_machine_smoke():
    """真实机器冒烟：只验证结构，不假设具体装了什么。"""
    st = openers_mod.state(refresh=True)
    assert len(st) == len(openers_mod.CATALOG)
