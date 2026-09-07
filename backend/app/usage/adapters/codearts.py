"""华为码道（CodeArts Agent）会话用量适配器。

两个数据源（并存时一并收集，游标天然隔离）：

1. **Space 内核会话库** `~/.codeartswork/kernel/sessions/<项目>/<会话ID>/`：
   - `history.jsonl` 逐行追加（每行一条消息），Assistant 行自带逐调用
     `token_usage: {prompt_tokens(含缓存), completion_tokens, total_tokens,
     max_tokens, reasoning_tokens, cached_tokens}` —— OpenAI 口径，
     写入侧扣减归一为 fresh input + 缓存读单列；
   - `meta.json` 提供会话元数据（session_id / working_directory /
     current_model.model_id），用于 model 兜底与项目归因。
   增量：history.jsonl 字节 offset 游标（同 Claude/ZCode 适配器）。

2. **IDE/CLI 模式库** `~/.codeartsdoer/{codearts-data,vscode-data}/opencode.db`：
   OpenCode 同构库（session/message/part 三表），token 语义为 Anthropic
   （input 不含缓存），复用 `_OpenCodeFamilyAdapter` 基类解析。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from app.usage.adapters.base import MAX_FILES_PER_SYNC, register
from app.usage.adapters.opencode import _OpenCodeFamilyAdapter
from app.usage.common import SyncStats, expand_path, parse_ts
from app.usage.store import UsageRecord, UsageStore


def _space_root() -> Path:
    """Space 内核会话库根目录（CODEARTS_WORK_HOME 可重定向，测试用）。"""
    env = os.environ.get("CODEARTS_WORK_HOME")
    base = expand_path(env) if env else Path.home() / ".codeartswork"
    return base / "kernel" / "sessions"


def _space_session_dirs() -> list[Path]:
    """枚举 Space 会话目录（含 meta.json 的），两层/一层布局都兜住。"""
    root = _space_root()
    if not root.is_dir():
        return []
    metas = list(root.glob("*/*/meta.json"))
    metas.extend(p for p in root.glob("*/meta.json") if p.parent not in {m.parent for m in metas})
    return sorted({m.parent for m in metas})


def _codearts_db_paths() -> list[Path]:
    home = os.environ.get("CODEARTS_DOER_HOME")
    base = Path(home).expanduser() if home else Path.home() / ".codeartsdoer"
    if not base.is_dir():
        return []
    out = [base / "codearts-data" / "opencode.db", base / "vscode-data" / "opencode.db"]
    out.extend(p for p in base.glob("*/opencode.db") if p not in out)
    return [p for p in out if p.exists()]


def _read_meta(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _u(obj: dict, key: str) -> int:
    try:
        return int(obj.get(key) or 0)
    except (TypeError, ValueError):
        return 0


@register
class CodeArtsAdapter(_OpenCodeFamilyAdapter):
    source = "codearts"
    agent = "codearts"
    name = "码道"

    def db_paths(self) -> list[Path]:
        return _codearts_db_paths()

    def roots(self) -> list[Path]:
        out = [p.parent for p in self.db_paths()]
        out.append(_space_root())
        return out

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        self._collect_space(store, stats)
        for db_path in self.db_paths():
            self._collect_db(store, db_path, stats)
        return stats

    # ---- 数据源 1：Space 内核 history.jsonl -----------------------------
    def _collect_space(self, store: UsageStore, stats: SyncStats) -> None:
        root = _space_root()
        if not root.is_dir():
            return
        files = list(root.glob("*/*/history.jsonl"))
        files.extend(p for p in root.glob("*/history.jsonl") if p not in files)
        stats.files_scanned += len(files)
        for path in sorted(files)[:MAX_FILES_PER_SYNC]:
            try:
                self._sync_space_session(store, path, stats)
            except OSError as e:
                stats.errors.append(f"{path}: {e}")

    def _sync_space_session(self, store: UsageStore, path: Path, stats: SyncStats) -> None:
        st = path.stat()
        key = str(path)
        cur = store.get_state(self.source, key)
        offset = int(cur["offset"]) if cur else 0
        if cur and int(cur["mtime_ns"]) == st.st_mtime_ns and int(cur["size"]) == st.st_size:
            stats.skipped += 1
            return
        if st.st_size < offset:
            offset = 0  # 文件被截断/重写，从头重读

        meta = _read_meta(path.parent / "meta.json")
        sid = str(meta.get("session_id") or path.parent.name)
        wd = meta.get("working_directory")
        project = str(Path(wd)) if isinstance(wd, str) and wd else ""
        cur_model = meta.get("current_model")
        default_model = (
            str(cur_model.get("model_id")) if isinstance(cur_model, dict) else ""
        )

        consumed = offset
        with path.open("rb") as f:
            f.seek(offset)
            while True:
                line = f.readline()
                if not line or not line.endswith(b"\n"):
                    break  # 半截尾行不提交，等下轮补全
                consumed += len(line)
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(value, dict) or value.get("role") != "Assistant":
                    continue
                usage = value.get("token_usage")
                if not isinstance(usage, dict):
                    continue  # User/Tool 行或未回填用量的消息
                model_ref = value.get("model_ref")
                model = (
                    model_ref.get("model_id")
                    if isinstance(model_ref, dict) and model_ref.get("model_id")
                    else default_model or "unknown"
                )
                req_id = str(value.get("id") or f"{key}:{consumed}")
                prompt = _u(usage, "prompt_tokens")
                cached = _u(usage, "cached_tokens")
                rec = UsageRecord(
                    request_id=f"codearts_session:{sid}:{req_id}",
                    agent=self.agent,
                    model=model,
                    ts=parse_ts(value.get("timestamp")) or int(st.st_mtime),
                    input_tokens=max(0, prompt - cached),  # OpenAI 口径 → fresh
                    output_tokens=_u(usage, "completion_tokens"),
                    cache_read_tokens=cached,
                    reasoning_tokens=_u(usage, "reasoning_tokens"),
                    session_id=sid,
                    source_path=key,
                    project=project,
                )
                if store.add_record(rec):
                    stats.imported += 1
                else:
                    stats.skipped += 1

        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=consumed, seq=0,
        )
