"""CodeBuddy（腾讯）IDE 会话用量适配器（日志级逐 step 口径）。

来源: %APPDATA%/CodeBuddy CN/logs/<启动目录>/window*/exthost/
      Tencent-Cloud.coding-copilot/腾讯云代码助手*.log
CodeBuddy IDE 的 Craft 智能体每个推理 step 结束都打一条 notifyStepEnd
日志，携带逐 step 的 usage JSON：
  {"inputTokens"(含缓存), "outputTokens", "cacheTokens"(缓存读),
   "cachedWriteTokens"(缓存写), "cachedMissTokens"(新鲜输入),
   "thinkingTokens", "totalTokens", ...}
同一 step 的 [AgentReporter] "Step execution metrics" 行是同一份 usage
（无 messageId），只解析 notifyStepEnd 避免重复计数。
模型归属：按行序扫描，`ModelProvider initialized, modelId: X` 行更新
当前模型（每 step 的请求都会打印一次，天然形成 step→模型对应关系）。

语义归一（写入侧，实测验证）：CodeBuddy 的 inputTokens 含 cacheTokens
（totalTokens = inputTokens + outputTokens；inputTokens = cacheTokens
+ cachedMissTokens），故归一为 input_tokens=cachedMissTokens(新鲜)、
cache_read=cacheTokens、cache_creation=cachedWriteTokens，总和不变。

增量: 文件 mtime+size 门控，未变即整体跳过；变更时整文件重扫，
request_id=codebuddy:<messageId> + INSERT OR IGNORE 保证幂等。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.usage.adapters.base import MAX_FILES_PER_SYNC, BaseUsageAdapter, register
from app.usage.common import SyncStats, parse_ts
from app.usage.store import UsageRecord, UsageStore

# notifyStepEnd 行：requestId/messageId 均为十六进制 id，usage 为扁平 JSON
_STEP_RE = re.compile(
    r"notifyStepEnd,\s*step:\s*\d+,\s*requestId:\s*(\w+),"
    r"\s*messageId:\s*(\w+),\s*usage:\s*(\{[^{}]*\})"
)
_MODEL_RE = re.compile(r"ModelProvider initialized,\s*modelId:\s*([^,]+),\s*modelName")
_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s")


@register
class CodeBuddyAdapter(BaseUsageAdapter):
    source = "codebuddy"
    agent = "codebuddy"
    name = "CodeBuddy"

    def roots(self) -> list[str]:
        # CodeBuddy IDE（VSCode fork）扩展日志根目录
        return ["%APPDATA%/CodeBuddy CN/logs"]

    def collect(self, store: UsageStore) -> SyncStats:
        stats = SyncStats(self.source)
        files: list[Path] = []
        for root in self.detected_roots():
            for launch in sorted(root.iterdir()):
                if not launch.is_dir():
                    continue
                # window*/exthost/<扩展目录>/*.log，扩展目录名大小写不敏感匹配
                for p in launch.glob("window*/exthost/*/*.log"):
                    if p.parent.name.lower() == "tencent-cloud.coding-copilot":
                        files.append(p)
                        if len(files) > MAX_FILES_PER_SYNC:
                            break
        stats.files_scanned = len(files)
        for path in sorted(files)[:MAX_FILES_PER_SYNC]:
            try:
                self._sync_file(store, path, stats)
            except (OSError, ValueError) as e:
                stats.errors.append(f"{path}: {e}")
        return stats

    def _sync_file(self, store: UsageStore, path: Path, stats: SyncStats) -> None:
        st = path.stat()
        key = str(path)
        cur = store.get_state(self.source, key)
        if cur and int(cur["mtime_ns"]) == st.st_mtime_ns and int(cur["size"]) == st.st_size:
            stats.skipped += 1
            return

        model = "unknown"
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            m = _MODEL_RE.search(line)
            if m:
                model = m.group(1).strip()
                continue
            s = _STEP_RE.search(line)
            if not s:
                continue
            req_id, msg_id, usage_raw = s.group(1), s.group(2), s.group(3)
            try:
                u = json.loads(usage_raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(u, dict):
                continue
            cache_read = int(u.get("cacheTokens") or 0)
            miss = u.get("cachedMissTokens")
            fresh = (
                int(miss) if miss is not None
                else max(0, int(u.get("inputTokens") or 0) - cache_read)
            )
            rec = UsageRecord(
                request_id=f"codebuddy:{msg_id}",
                agent=self.agent,
                model=model,
                ts=parse_ts(self._line_ts(line)) or int(st.st_mtime),
                input_tokens=fresh,
                output_tokens=int(u.get("outputTokens") or 0),
                cache_read_tokens=cache_read,
                cache_creation_tokens=int(u.get("cachedWriteTokens") or 0),
                reasoning_tokens=int(u.get("thinkingTokens") or 0),
                session_id=req_id,  # requestId = 一次 Agent 运行（多 step）
                source_path=key,
            )
            if store.add_record(rec):
                stats.imported += 1
            else:
                stats.skipped += 1

        store.set_state(
            self.source, key,
            mtime_ns=st.st_mtime_ns, size=st.st_size, offset=0, seq=0,
        )

    @staticmethod
    def _line_ts(line: str) -> str | None:
        """日志行首本地时间戳 `2026-08-29 14:21:30.025`（parse_ts 兼容）。"""
        m = _TS_RE.match(line)
        return m.group(1) if m else None
