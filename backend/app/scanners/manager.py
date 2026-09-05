"""扫描任务管理器：后台执行扫描并同步进 discovered 表。"""
from __future__ import annotations

import threading
import time
from datetime import datetime

from app.scanners.engine import ScanEngine


def _now() -> str:
    """系统时区当前时间，格式 yyyy-MM-dd HH:mm:ss。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class ScanManager:
    def __init__(self):
        self.engine = ScanEngine()
        self._lock = threading.Lock()
        self._job: dict | None = None

    def start(self, agent_keys: list[str] | None = None,
              project_roots: list[str] | None = None) -> dict:
        with self._lock:
            if self._job is not None and not self._job.get("done"):
                return self._job
            job: dict = {
                "state": "running",
                "agent_keys": agent_keys or [],
                "project_roots": project_roots or [],
                "started": _now(),
                "finished": "",
                "progress": {},
                "counts_by_agent": {},
                "counts_by_kind": {},
                "total": 0,
                "indexed": 0,
                "projects_found": 0,
                "projects_indexed": 0,
                "usage_backfilled": 0,
                "error": None,
                "done": False,
            }
            self._job = job
        threading.Thread(target=self._worker, args=(job,), daemon=True).start()
        return job

    def _worker(self, job: dict) -> None:
        from app.deps import get_discovered_store, get_project_store, get_usage_store
        from app.projects.discover import discover_projects

        store = get_discovered_store()
        try:
            def on_progress(agent: str, count: int) -> None:
                job["progress"][agent] = count

            res = self.engine.scan(
                agent_keys=job["agent_keys"],
                project_roots=job["project_roots"],
                on_progress=on_progress,
            )
            job["counts_by_agent"] = res["counts_by_agent"]
            job["counts_by_kind"] = res["counts_by_kind"]
            job["total"] = res["total"]
            job["indexed"] = store.sync(res["items"])

            # 同步发现「AI 开发/维护过的项目」（只读回溯会话日志，不搬运）
            pstore = get_project_store()
            pstats = pstore.sync_refs(discover_projects(job["project_roots"] or None))
            job["projects_found"] = pstats["projects"]
            job["projects_indexed"] = pstats["added"]
            # 按 会话→项目 映射回填历史用量的 project 列（幂等）
            job["usage_backfilled"] = get_usage_store().backfill_projects(
                pstore.session_mapping()
            )
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
        finally:
            job["done"] = True
            job["finished"] = _now()
            job["state"] = "error" if job["error"] else "done"

    def status(self) -> dict | None:
        with self._lock:
            return dict(self._job) if self._job is not None else None


scan_manager = ScanManager()
