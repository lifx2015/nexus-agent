"""项目管理 API 测试：列表(含用量归因)、手动登记、排除/恢复、扫描集成回填。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.deps as deps
import app.api.v1.routes.projects as projects_routes_mod
import app.projects.discover as discover_mod
from app.api.v1.routes import projects as projects_routes
from app.core.config import Settings
from app.projects.models import ProjectRef
from app.usage.store import UsageRecord


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(deps, "_settings", Settings(data_home=tmp_path))
    deps._usage_stores.clear()
    deps._project_stores.clear()
    deps._discovered_stores.clear()
    app_ = FastAPI()
    app_.include_router(projects_routes.router)
    with TestClient(app_) as c:
        yield c
    deps._usage_stores.clear()
    deps._project_stores.clear()
    deps._discovered_stores.clear()


def _seed_project(store, path="E:/demo", agent="claude-code", sid="s1"):
    store.sync_refs([ProjectRef(path=path, agent=agent, session_id=sid, ts=100)])
    return store.get_by_path(path)


def test_list_with_attributed_usage(client):
    pstore = deps.get_project_store()
    row = _seed_project(pstore)
    ustore = deps.get_usage_store()
    ustore.add_record(UsageRecord(
        request_id="r1", agent="claude-code", model="m", ts=1,
        input_tokens=10, output_tokens=5, session_id="s1", project=row["path"],
    ))

    res = client.get("/api/v1/projects").json()
    assert res["stats"]["total"] == 1
    item = res["items"][0]
    assert item["requests"] == 1 and item["tokens"] == 15
    assert res["stats"]["attributed_tokens"] == 15
    assert res["stats"]["attributed_requests"] == 1


def test_backfill_empty_project_then_aggregate(client):
    pstore = deps.get_project_store()
    row = _seed_project(pstore)
    ustore = deps.get_usage_store()
    ustore.add_record(UsageRecord(
        request_id="r1", agent="claude-code", model="m", ts=1,
        input_tokens=10, output_tokens=5, session_id="s1",  # project 留空
    ))
    assert ustore.usage_by_project() == {}

    n = ustore.backfill_projects(pstore.session_mapping())
    assert n == 1
    assert ustore.usage_by_project()[row["path"]]["tokens"] == 15
    # 幂等：重复回填为 0
    assert ustore.backfill_projects(pstore.session_mapping()) == 0


def test_add_exclude_restore_flow(client, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    rec = client.post("/api/v1/projects", json={"path": str(d)}).json()
    assert rec["source"] == "manual"
    assert rec["status"] == "active"

    assert client.post("/api/v1/projects", json={"path": str(tmp_path / "nope")}).status_code == 404

    rid = rec["id"]
    assert client.post(f"/api/v1/projects/{rid}/exclude").json()["status"] == "excluded"
    assert client.get("/api/v1/projects").json()["items"] == []  # 缺省隐藏排除项
    assert client.get("/api/v1/projects", params={"status": "excluded"}).json()["items"]
    assert client.post(f"/api/v1/projects/{rid}/restore").json()["status"] == "active"

    patched = client.patch(f"/api/v1/projects/{rid}", json={"starred": True, "note": "n"}).json()
    assert patched["starred"] == 1 and patched["note"] == "n"
    assert client.get("/api/v1/projects", params={"starred": True}).json()["items"]


def test_list_agent_filter(client):
    pstore = deps.get_project_store()
    _seed_project(pstore, path="E:/a", agent="claude-code", sid="a1")
    _seed_project(pstore, path="E:/b", agent="codex", sid="b1")
    res = client.get("/api/v1/projects", params={"agent": "codex"}).json()
    assert len(res["items"]) == 1 and res["items"][0]["agents"] == ["codex"]


def test_openers_catalog_and_launch(client, tmp_path, monkeypatch):
    """打开方式目录 + 启动命令构造（打桩 openers 状态，不真启动）。"""
    d = tmp_path / "proj"
    d.mkdir()
    rec = client.post("/api/v1/projects", json={"path": str(d)}).json()

    monkeypatch.setattr(projects_routes_mod, "_openers_state", lambda: [
        {"key": "explorer", "label": "资源管理器", "kind": "system", "bin": "",
         "arg_mode": "none", "resolved": "", "available": True},
        {"key": "vscode", "label": "VS Code", "kind": "editor", "bin": "code",
         "arg_mode": "path", "resolved": r"C:/fake/code.CMD", "available": True},
        {"key": "trae", "label": "Trae", "kind": "editor", "bin": "trae",
         "arg_mode": "path", "resolved": "", "available": False},
        {"key": "opencode", "label": "OpenCode", "kind": "agent", "bin": "opencode",
         "arg_mode": "path", "resolved": r"C:/fake/opencode.CMD", "available": True},
        {"key": "claude", "label": "Claude Code", "kind": "agent", "bin": "claude",
         "arg_mode": "none", "resolved": r"C:/fake/claude.EXE", "available": True},
    ])
    launched: list[str] = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append(cmd)
            self.pid = 0

    monkeypatch.setattr(projects_routes_mod.subprocess, "Popen", FakePopen)

    ops = client.get("/api/v1/projects/openers").json()
    by_key = {o["key"]: o for o in ops}
    assert {"explorer", "vscode", "trae", "opencode", "claude"} <= set(by_key)
    assert by_key["explorer"]["available"] is True      # 永远可用
    assert by_key["vscode"]["available"] is True
    assert by_key["trae"]["available"] is False          # 未检测到

    # 编辑器：解析到的 exe + 项目路径
    r = client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "vscode"})
    assert r.status_code == 200
    assert str(d) in launched[-1] and "code.CMD" in launched[-1]

    # CLI 智能体：新终端窗口 + 项目目录作为工作目录 + 路径参数(arg_mode=path)
    r = client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "opencode"})
    assert r.status_code == 200
    assert "start" in launched[-1] and "/D" in launched[-1] and "opencode.CMD" in launched[-1]
    assert launched[-1].count(str(d)) == 2  # 工作目录 + 参数

    # arg_mode=none 的智能体不传路径参数
    client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "claude"})
    assert launched[-1].count(str(d)) == 1

    # 未检测到的打开方式 → 410；未知 key → 404
    assert client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "trae"}).status_code == 410
    assert client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "nope"}).status_code == 404

    # 目录已删除 → 410（explorer 亦然，且不会真正启动）
    import shutil as _shutil

    _shutil.rmtree(d)
    r = client.post("/api/v1/projects/open", json={"id": rec["id"], "app": "explorer"})
    assert r.status_code == 410
    assert len(launched) == 3


def test_scan_manager_integrates_projects(tmp_path, monkeypatch):
    """扫描闭环：资产扫描(桩) → 项目发现(桩) → 入库 → 用量回填。"""
    monkeypatch.setattr(deps, "_settings", Settings(data_home=tmp_path))
    deps._usage_stores.clear()
    deps._project_stores.clear()
    deps._discovered_stores.clear()
    try:
        ustore = deps.get_usage_store()
        ustore.add_record(UsageRecord(
            request_id="r1", agent="claude-code", model="m", ts=1,
            input_tokens=7, output_tokens=3, session_id="sX",
        ))

        proj_path = str(tmp_path / "p")
        monkeypatch.setattr(
            discover_mod, "discover_projects",
            lambda roots=None: [ProjectRef(path=proj_path, agent="claude-code",
                                           session_id="sX", ts=9)],
        )

        from app.scanners.manager import ScanManager

        mgr = ScanManager()

        def fake_scan(**kw):
            return {
                "items": [], "counts_by_agent": {}, "counts_by_kind": {},
                "scanned_roots": [], "agents": [], "total": 0, "elapsed": 0,
            }

        monkeypatch.setattr(mgr.engine, "scan", fake_scan)
        job = mgr.start()
        for _ in range(200):
            if job.get("done"):
                break
            time.sleep(0.02)
        assert job["state"] == "done"
        assert job["projects_found"] == 1
        assert job["projects_indexed"] == 1
        assert job["usage_backfilled"] == 1

        pstore = deps.get_project_store()
        assert pstore.get_by_path(proj_path)["sessions"] == 1
        assert ustore.usage_by_project()[proj_path]["requests"] == 1
    finally:
        deps._usage_stores.clear()
        deps._project_stores.clear()
        deps._discovered_stores.clear()
