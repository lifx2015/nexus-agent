"""项目发现测试：造临时会话数据验证各提取器、转义解码与标记扫描。

所有用例把数据源重定向到 tmp_path（环境变量 / Path.home 打桩），
绝不触碰真实 ~/.claude 等目录。
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from app.projects.discover import (
    ClaudeCodeProjects,
    CodeArtsProjects,
    CodexProjects,
    OpenCodeProjects,
    QoderProjects,
    WorkBuddyProjects,
    discover_markers,
    discover_projects,
)
from app.projects.paths import decode_project_dir


# ---- 转义目录名解码 ------------------------------------------------------
@pytest.mark.skipif(os.name != "nt", reason="Windows 路径形态")
def test_decode_windows():
    # 有损解码：路径里的 - 与分隔符不可区分，一律还原为 \
    assert decode_project_dir("E--workspace-code-pal") == "E:\\workspace\\code\\pal"
    assert decode_project_dir("C--Users-li") == "C:\\Users\\li"
    assert decode_project_dir("c-Users-li-Demo") == "C:\\Users\\li\\Demo"
    assert decode_project_dir("plain") == "\\plain"
    assert decode_project_dir("") == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX 路径形态")
def test_decode_posix():
    assert decode_project_dir("-Users-li-proj") == "/Users/li/proj"
    assert decode_project_dir("proj") == "/proj"


# ---- Claude Code 提取器 --------------------------------------------------
def test_claude_cwd_authoritative(tmp_path, monkeypatch):
    proj_dir = tmp_path / "projects" / "E--test-proj"
    proj_dir.mkdir(parents=True)
    real = tmp_path / "test-proj"
    (proj_dir / "s1.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "s1", "cwd": str(real),
                    "timestamp": "2026-01-01T00:00:00Z"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    refs = ClaudeCodeProjects().extract()
    assert len(refs) == 1
    assert refs[0].project == str(real)  # cwd 权威，优先于目录名解码
    assert refs[0].session_id == "s1"
    assert refs[0].ts > 0


def test_claude_fallback_to_dirname(tmp_path, monkeypatch):
    proj_dir = tmp_path / "projects" / "F--workspace-demo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "s.jsonl").write_text(json.dumps({"type": "user"}) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    refs = ClaudeCodeProjects().extract()
    assert len(refs) == 1
    expected = "F:\\workspace\\demo" if os.name == "nt" else "/F/workspace/demo"
    assert refs[0].project == expected


# ---- Codex 提取器 --------------------------------------------------------
def test_codex_session_meta_cwd(tmp_path, monkeypatch):
    sess = tmp_path / "sessions" / "2026" / "01" / "01"
    sess.mkdir(parents=True)
    real = tmp_path / "demo"
    (sess / "rollout-1.jsonl").write_text(
        json.dumps({"timestamp": "2026-01-01T00:00:00Z", "type": "session_meta",
                    "payload": {"session_id": "c1", "cwd": str(real)}}) + "\n"
        + json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    refs = CodexProjects().extract()
    assert len(refs) == 1
    assert refs[0].project == str(real) and refs[0].session_id == "c1"


# ---- OpenCode 提取器 -----------------------------------------------------
def test_opencode_db_directory(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE session(id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER);"
    )
    conn.execute("INSERT INTO session VALUES('os1', 'F:/workspace/demo', 1700000000000)")
    conn.commit()
    conn.close()
    monkeypatch.setenv("OPENCODE_DB", str(db))
    refs = OpenCodeProjects().extract()
    assert len(refs) == 1
    assert refs[0].session_id == "os1"
    assert refs[0].project == str(Path("F:/workspace/demo"))  # 分隔符归一
    assert refs[0].ts == 1700000000  # 毫秒 → 秒


# ---- WorkBuddy 提取器 ----------------------------------------------------
def test_workbuddy_session_cwd(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "c-Users-li-Demo"
    proj.mkdir(parents=True)
    real = tmp_path / "Demo"
    (proj / "ws1.jsonl").write_text(
        json.dumps({"type": "message", "cwd": str(real), "sessionId": "ws1"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path))
    refs = WorkBuddyProjects().extract()
    assert len(refs) == 1
    assert refs[0].project == str(real)
    assert refs[0].session_id == "ws1"  # 行内 sessionId 优先于文件名


def test_workbuddy_filename_fallback(tmp_path, monkeypatch):
    proj = tmp_path / "projects" / "c-Users-li-Demo"
    proj.mkdir(parents=True)
    (proj / "ws9.jsonl").write_text(
        json.dumps({"type": "message", "cwd": "D:/w"}) + "\n", encoding="utf-8",
    )
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path))
    refs = WorkBuddyProjects().extract()
    assert len(refs) == 1 and refs[0].session_id == "ws9"  # 无 sessionId → 文件名兜底


# ---- Qoder 提取器 --------------------------------------------------------
def test_qoder_dirname_only(tmp_path, monkeypatch):
    (tmp_path / ".qoder" / "projects" / "D-dev-Qoder").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    refs = QoderProjects().extract()
    assert len(refs) == 1
    expected = "D:\\dev\\Qoder" if os.name == "nt" else "/D/dev/Qoder"
    assert refs[0].project == expected
    assert refs[0].session_id is None  # 无行级数据，只有项目


# ---- 华为码道（CodeArts Agent）提取器 --------------------------------------
def test_codearts_two_dbs_merged(tmp_path, monkeypatch):
    doer = tmp_path / ".codeartsdoer"
    for sub in ("codearts-data", "vscode-data"):
        db = doer / sub / "opencode.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE session(id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER);"
        )
        conn.execute(
            "INSERT INTO session VALUES(?, 'F:/workspace/demo', 1700000000000)",
            (f"cs-{sub}",),
        )
        conn.commit()
        conn.close()
    monkeypatch.setenv("CODEARTS_DOER_HOME", str(doer))
    refs = CodeArtsProjects().extract()
    assert len(refs) == 2
    assert {r.session_id for r in refs} == {"cs-codearts-data", "cs-vscode-data"}
    assert all(r.project == str(Path("F:/workspace/demo")) for r in refs)
    assert all(r.ts == 1700000000 for r in refs)  # 毫秒 → 秒


def test_codearts_empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEARTS_DOER_HOME", str(tmp_path / "no-doer"))
    assert CodeArtsProjects().extract() == []


# ---- 华为码道（CodeArts Agent）提取器 --------------------------------------
def test_codearts_two_dbs_merged(tmp_path, monkeypatch):
    doer = tmp_path / ".codeartsdoer"
    for sub in ("codearts-data", "vscode-data"):
        db = doer / sub / "opencode.db"
        db.parent.mkdir(parents=True)
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE session(id TEXT PRIMARY KEY, directory TEXT, time_updated INTEGER);"
        )
        conn.execute(
            "INSERT INTO session VALUES(?, 'F:/workspace/demo', 1700000000000)",
            (f"cs-{sub}",),
        )
        conn.commit()
        conn.close()
    monkeypatch.setenv("CODEARTS_DOER_HOME", str(doer))
    refs = CodeArtsProjects().extract()
    assert len(refs) == 2
    assert {r.session_id for r in refs} == {"cs-codearts-data", "cs-vscode-data"}
    assert all(r.project == str(Path("F:/workspace/demo")) for r in refs)
    assert all(r.ts == 1700000000 for r in refs)  # 毫秒 → 秒


def test_codearts_empty_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEARTS_DOER_HOME", str(tmp_path / "no-doer"))
    assert CodeArtsProjects().extract() == []


# ---- 标记文件扫描 --------------------------------------------------------
def test_discover_markers(tmp_path):
    proj = tmp_path / "repo"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# rules", encoding="utf-8")
    other = tmp_path / "lib" / "x"
    other.mkdir(parents=True)
    (other / "AGENTS.md").write_text("x", encoding="utf-8")

    refs = discover_markers([str(tmp_path)])
    by_path = {str(Path(r.path)): r for r in refs}
    assert str(proj) in by_path
    assert by_path[str(proj)].agent == "claude-code"
    assert by_path[str(proj)].source == "marker"
    assert by_path[str(other)].agent == "codex"  # AGENTS.md 归首个认领的具体 Agent
    assert discover_markers([str(tmp_path / "none")]) == []


# ---- 汇总：隔离环境下 discover_projects ----------------------------------
def test_discover_projects_isolated(tmp_path, monkeypatch):
    # 全部数据源指向 tmp，只留 claude 一家；roots 不存在 → 无标记结果
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "no-codex"))
    monkeypatch.setenv("WORKBUDDY_HOME", str(tmp_path / "no-wb"))
    monkeypatch.setenv("OPENCODE_DB", str(tmp_path / "no.db"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    proj_dir = tmp_path / "projects" / "E--test-proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "s.jsonl").write_text(
        json.dumps({"type": "user", "sessionId": "s9", "cwd": str(tmp_path / "tp")}) + "\n",
        encoding="utf-8",
    )
    refs = discover_projects([str(tmp_path / "no-roots")])
    assert len(refs) == 1
    assert refs[0].agent == "claude-code"
    assert refs[0].session_id == "s9"
    assert refs[0].source == "log"
