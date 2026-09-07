"""CodeBuddy 适配器测试：notifyStepEnd 解析、语义归一、模型归属、增量幂等。"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.core.config import Settings
from app.usage.adapters.codebuddy import CodeBuddyAdapter
from app.usage.store import UsageStore

# ---- 真实日志行模板（脱敏自本机 CodeBuddy CN 日志） ----------------------
PROVIDER = (
    "{ts} [info] [BaseAgent:craft] DeepSeek ModelProvider initialized, "
    "modelId: {model}, modelName: {display}"
)
STEP = (
    "{ts} [info] [BaseAgent:craft] [{rid}] notifyStepEnd, step: {step}, "
    "requestId: {rid}, messageId: {mid}, usage: {usage}, isMaxTokenLimit: false, "
    "isMaxStepLimit: false, hasReactiveTool:false, isContentFilter: false"
)
METRICS = (  # 同一 step 的重复口径行（无 messageId），不应被计数
    "{ts} [info] [AgentReporter] [{rid}] Step execution metrics: step={step}, "
    "duration=9000ms, usage={usage}"
)
NOISE = "{ts} [info] [chatProvider] request sent to copilot.tencent.com/v2/chat/completions"

# inputTokens=167522(含缓存167104) + outputTokens=171 = totalTokens=167693
U1 = ('{"inputTokens":167522,"outputTokens":171,"totalTokens":167693,'
      '"cacheTokens":167104,"cachedWriteTokens":0,"cachedMissTokens":418,'
      '"lastTokens":167693,"credit":0,"thinkingTokens":0}')
U2 = ('{"inputTokens":171548,"outputTokens":244,"totalTokens":171792,'
      '"cacheTokens":168576,"cachedWriteTokens":0,"cachedMissTokens":2972,'
      '"lastTokens":171792,"credit":0,"thinkingTokens":124}')


def _make_logs(root, content: str) -> None:
    d = root / "20260829T104857" / "window1" / "exthost" / "Tencent-Cloud.coding-copilot"
    d.mkdir(parents=True, exist_ok=True)
    (d / "腾讯云代码助手.log").write_text(content, encoding="utf-8")


def _adapter(monkeypatch, tmp_path) -> CodeBuddyAdapter:
    ad = CodeBuddyAdapter()
    monkeypatch.setattr(ad, "roots", lambda: [str(tmp_path)])
    return ad


def _by_id(store) -> dict:
    rows, _ = store.records(limit=100)
    return {r["request_id"]: r for r in rows}


@pytest.fixture()
def store(tmp_path):
    s = UsageStore(Settings(data_home=tmp_path))
    yield s
    s.close()


def test_codebuddy_parse_and_semantics(store, tmp_path, monkeypatch):
    """逐 step 解析 + 语义归一 + metrics 行去重 + 模型归属。"""
    _make_logs(
        tmp_path,
        "\n".join([
            NOISE.format(ts="2026-08-29 14:21:30.025"),
            PROVIDER.format(ts="2026-08-29 14:21:30.029", model="hy4-preview",
                            display="Hy4 preview"),
            STEP.format(ts="2026-08-29 14:21:38.224", rid="6a38dbc4f5d84396",
                        step=119, mid="aaa1", usage=U1),
            METRICS.format(ts="2026-08-29 14:21:38.225", rid="6a38dbc4f5d84396",
                           step=119, usage=U1),  # 重复口径，必须忽略
            STEP.format(ts="2026-08-29 14:22:03.446", rid="6a38dbc4f5d84396",
                        step=122, mid="bbb2", usage=U2),
        ]) + "\n",
    )
    stats = _adapter(monkeypatch, tmp_path).collect(store)
    assert stats.imported == 2 and stats.skipped == 0

    rec = _by_id(store)
    r1, r2 = rec["codebuddy:aaa1"], rec["codebuddy:bbb2"]
    # 语义归一：inputTokens 含缓存 → input=cachedMissTokens，缓存单列
    assert r1["input_tokens"] == 418 and r1["cache_read_tokens"] == 167104
    assert r1["output_tokens"] == 171 and r1["reasoning_tokens"] == 0
    # 总量守恒：新鲜输入 + 缓存读 + 输出 == totalTokens
    assert r1["input_tokens"] + r1["cache_read_tokens"] + r1["output_tokens"] == 167693
    assert r1["model"] == "hy4-preview" and r1["session_id"] == "6a38dbc4f5d84396"
    assert r1["agent"] == "codebuddy"
    # 行首本地时间戳
    assert r1["ts"] == int(datetime.fromisoformat("2026-08-29 14:21:38.224").timestamp())
    assert r2["input_tokens"] == 2972 and r2["reasoning_tokens"] == 124
    # 三列之和 = input(171548) - cache(168576) + 2972 校验：171548 = 168576 + 2972 ✓
    assert r2["input_tokens"] + r2["cache_read_tokens"] == 171548


def test_codebuddy_model_switch(store, tmp_path, monkeypatch):
    """中途切换模型：按行序归因，切换后的 step 记到新模型。"""
    _make_logs(
        tmp_path,
        "\n".join([
            PROVIDER.format(ts="2026-08-29 15:00:00.000", model="hy4-preview",
                            display="Hy4 preview"),
            STEP.format(ts="2026-08-29 15:00:05.000", rid="r1", step=1, mid="m1", usage=U1),
            PROVIDER.format(ts="2026-08-29 15:00:30.000", model="deepseek-v4-flash",
                            display="Deepseek V4 Flash"),
            STEP.format(ts="2026-08-29 15:00:35.000", rid="r1", step=2, mid="m2", usage=U2),
        ]) + "\n",
    )
    _adapter(monkeypatch, tmp_path).collect(store)
    rec = _by_id(store)
    assert rec["codebuddy:m1"]["model"] == "hy4-preview"
    assert rec["codebuddy:m2"]["model"] == "deepseek-v4-flash"


def test_codebuddy_incremental_and_noise(store, tmp_path, monkeypatch):
    """mtime 门控跳过 + 追加后整文件重扫去重增量 + 非目标扩展日志不扫描。"""
    noise_dir = tmp_path / "20260829T104857" / "window1" / "exthost" / "vscode.git"
    noise_dir.mkdir(parents=True, exist_ok=True)
    (noise_dir / "Git.log").write_text("git spam\n", encoding="utf-8")

    ad = _adapter(monkeypatch, tmp_path)
    part1 = "\n".join([
        PROVIDER.format(ts="2026-08-29 16:00:00.000", model="hy4-preview",
                        display="Hy4 preview"),
        STEP.format(ts="2026-08-29 16:00:05.000", rid="r9", step=1, mid="x1", usage=U1),
    ]) + "\n"
    _make_logs(tmp_path, part1)

    s1 = ad.collect(store)
    assert s1.imported == 1 and s1.files_scanned == 1  # Git.log 未纳入

    s2 = ad.collect(store)
    assert s2.imported == 0 and s2.skipped == 1        # 文件未变 → 门控跳过

    step2 = STEP.format(ts="2026-08-29 16:00:40.000", rid="r9", step=2, mid="x2", usage=U2)
    f = tmp_path / "20260829T104857" / "window1" / "exthost" / "Tencent-Cloud.coding-copilot" / "腾讯云代码助手.log"
    f.write_text(part1 + step2 + "\n", encoding="utf-8")

    s3 = ad.collect(store)
    assert s3.imported == 1 and s3.skipped == 1        # 重扫去重 x1，新增 x2
    assert set(_by_id(store)) == {"codebuddy:x1", "codebuddy:x2"}
