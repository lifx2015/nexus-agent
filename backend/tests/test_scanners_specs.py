"""扫描器冒烟测试：AgentSpec 注册与资产分类规则。

数据驱动架构下，新增 Agent 的契约就是 SPECS_BY_KEY 里有一条正确配置；
分类逻辑只测与新增 Agent 相关的目录名规则（如码道的 rule/ 单数目录）。
"""
from __future__ import annotations

from pathlib import Path

from app.scanners.agents import SPECS, SPECS_BY_KEY
from app.scanners.engine import classify
from app.scanners.models import ItemKind


# ---- SPECS 注册 ----------------------------------------------------------
def test_codearts_spec_registered():
    spec = SPECS_BY_KEY["codearts"]
    assert spec.name == "码道 (CodeArts)"
    assert spec.vendor == "Huawei"
    # 全局目录：家目录 + VSCode 式 APPDATA 会话目录
    assert "~/.codeartsdoer" in spec.global_dirs
    assert "%APPDATA%/codearts-agent/User/chat_sessions" in spec.global_dirs
    assert spec.project_dirs == [".codeartsdoer"]
    assert not spec.project_files


def test_specs_keys_unique():
    keys = [s.key for s in SPECS]
    assert len(keys) == len(set(keys))
    assert set(SPECS_BY_KEY) == set(keys)


# ---- 分类规则 ------------------------------------------------------------
def test_classify_codearts_rule_dir():
    # 码道的规则目录是单数 rule/（区别于其他 Agent 的 rules/）
    assert classify(Path.home() / ".codeartsdoer" / "rule" / "frontend.md") == ItemKind.RULE
    # 复数 rules/ 与 steering/ 等原有规则不受影响
    assert classify(Path("x/rules/a.md")) == ItemKind.RULE
    assert classify(Path("x/steering/b.md")) == ItemKind.RULE


def test_classify_codearts_skill_and_config():
    assert classify(Path.home() / ".codeartsdoer" / "skills" / "deploy" / "SKILL.md") == ItemKind.SKILL
    assert classify(Path.home() / ".codeartsdoer" / "sandbox.json") == ItemKind.CONFIG
