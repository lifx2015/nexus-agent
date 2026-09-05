"""流量统计同步管理器：编排适配器增量同步 + 后台定时任务。

仿 ScanManager 模式（锁 + job dict + daemon 线程）。与扫描中心不同点：
- 自动同步常驻：start_auto() 起定时线程，每 interval 秒触发一轮增量；
- 手动/自动共用同一 job 与互斥锁，同一时刻至多一轮在跑。

job 生命周期：start()/run_once() 先持锁把 job 置为 running 再执行，
保证并发调用者看到的状态一致（不会把上一轮 done 误当本轮结果）。
"""
from __future__ import annotations

import threading
from datetime import datetime

from app.usage.adapters import ADAPTERS
from app.usage.common import SyncStats


def _now() -> str:
    """系统时区当前时间，格式 yyyy-MM-dd HH:mm:ss。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_job() -> dict:
    return {
        "state": "running", "done": False, "started": _now(),
        "finished": "", "sources": {}, "total": {}, "backfilled": 0, "error": None,
    }


class UsageSyncManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._timer: threading.Timer | None = None
        self._auto_enabled = False
        self._interval = 120

    # ---- 执行体 --------------------------------------------------------
    def _execute(self) -> None:
        """跑一轮全适配器同步，结果写回 self._job。异常只记不抛。"""
        total = SyncStats("total")
        sources: dict[str, dict] = {}
        error: str | None = None
        backfilled = 0
        try:
            from app.deps import get_usage_store
            store = get_usage_store()
            for adapter in ADAPTERS:
                try:
                    s = adapter.collect(store)
                except Exception as e:  # noqa: BLE001  单适配器故障不拖垮整轮
                    s = SyncStats(adapter.source)
                    s.errors.append(f"适配器故障: {e}")
                sources[adapter.source] = {
                    "name": adapter.name, "agent": adapter.agent, **s.to_dict(),
                }
                total.merge(s)
            # 按 会话→项目 映射回填本轮新增(及历史遗留)用量的项目归属（幂等、低成本）
            try:
                from app.deps import get_project_store
                mapping = get_project_store().session_mapping()
                if mapping:
                    backfilled = store.backfill_projects(mapping)
            except Exception:  # noqa: BLE001  回填失败不影响用量同步本身
                backfilled = 0
        except Exception as e:  # noqa: BLE001
            error = str(e)
        with self._lock:
            if self._job is not None:
                self._job["sources"] = sources
                self._job["total"] = total.to_dict()
                self._job["backfilled"] = backfilled
                self._job["error"] = error
                self._job["done"] = True
                self._job["finished"] = _now()
                self._job["state"] = "error" if error else "done"

    def run_once(self) -> dict:
        """同步执行（定时线程/测试用）；已有任务在跑则直接回显不重复起。"""
        with self._lock:
            if self._job is not None and not self._job.get("done"):
                return dict(self._job)
            self._job = _new_job()
        self._execute()
        with self._lock:
            return dict(self._job) if self._job else {}

    def start(self) -> dict:
        """后台线程触发一轮；立即返回（状态由前端轮询 status 获取）。"""
        with self._lock:
            if self._job is not None and not self._job.get("done"):
                return dict(self._job)
            self._job = _new_job()
        threading.Thread(target=self._execute, daemon=True).start()
        return dict(self._job)

    def status(self) -> dict | None:
        with self._lock:
            return dict(self._job) if self._job is not None else None

    # ---- 定时自动同步 --------------------------------------------------
    def start_auto(self, interval: int) -> None:
        """开启/调整自动同步。interval<=0 关闭。"""
        if interval <= 0:
            self.stop_auto()
            return
        with self._lock:
            self._auto_enabled = True
            self._interval = interval
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        self._schedule_next(5)  # 启动 5s 后首轮，之后按 interval 循环

    def stop_auto(self) -> None:
        with self._lock:
            self._auto_enabled = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def _schedule_next(self, delay: float) -> None:
        with self._lock:
            if not self._auto_enabled:
                return
            self._timer = threading.Timer(delay, self._auto_tick)
            self._timer.daemon = True
            self._timer.start()

    def _auto_tick(self) -> None:
        try:
            self.run_once()
        finally:
            self._schedule_next(self._interval)


usage_sync_manager = UsageSyncManager()
