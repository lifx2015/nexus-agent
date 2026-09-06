"""受支持的 Agent 清单（数据驱动）。

新增一家 Agent：在 SPECS 里加一条 AgentSpec 即可，扫描引擎无需改动。
路径支持 `~` 与 `%APPDATA%` 占位，运行时展开；不存在的目录会被自动跳过。
"""
from __future__ import annotations

from app.scanners.models import AgentSpec


def vscode_global_storage(ext_id: str) -> list[str]:
    """VS Code 扩展的 globalStorage 目录（Windows / macOS / Linux）。"""
    return [
        f"%APPDATA%/Code/User/globalStorage/{ext_id}",
        f"%APPDATA%/Code - Insiders/User/globalStorage/{ext_id}",
        f"~/Library/Application Support/Code/User/globalStorage/{ext_id}",
        f"~/.config/Code/User/globalStorage/{ext_id}",
    ]


SPECS: list[AgentSpec] = [
    AgentSpec(
        key="claude-code",
        name="Claude Code",
        vendor="Anthropic",
        global_dirs=["~/.claude"],
        project_dirs=[".claude"],
        project_files=["CLAUDE.md"],
    ),
    AgentSpec(
        key="cursor",
        name="Cursor",
        vendor="Anysphere",
        global_dirs=["~/.cursor"],
        project_dirs=[".cursor"],
        project_files=[".cursorrules"],
    ),
    AgentSpec(
        key="codebuddy",
        name="CodeBuddy",
        vendor="Tencent",
        global_dirs=["~/.codebuddy"],
        project_dirs=[".codebuddy"],
        project_files=["CODEBUDDY.md"],
    ),
    AgentSpec(
        key="gemini-cli",
        name="Gemini CLI",
        vendor="Google",
        global_dirs=["~/.gemini"],
        project_dirs=[".gemini"],
        project_files=["GEMINI.md"],
    ),
    AgentSpec(
        key="codex",
        name="Codex CLI",
        vendor="OpenAI",
        global_dirs=["~/.codex"],
        project_dirs=[".codex"],
        project_files=["AGENTS.md", "CODEX.md"],
    ),
    AgentSpec(
        key="windsurf",
        name="Windsurf",
        vendor="Codeium",
        global_dirs=["~/.windsurf", "~/.codeium/windsurf"],
        project_dirs=[".windsurf"],
        project_files=[".windsurfrules"],
    ),
    AgentSpec(
        key="cline",
        name="Cline",
        vendor="Cline Bot",
        global_dirs=vscode_global_storage("saoudrizwan.claude-dev"),
        project_dirs=[".clinerules", ".cline"],
        project_files=[".clinerules"],
    ),
    AgentSpec(
        key="roo",
        name="Roo Code",
        vendor="Roo Veterinary",
        global_dirs=vscode_global_storage("rooveterinaryinc.roo-cline"),
        project_dirs=[".roo"],
        project_files=[".roorules"],
    ),
    AgentSpec(
        key="continue",
        name="Continue",
        vendor="Continue Dev",
        global_dirs=["~/.continue"],
        project_dirs=[".continue"],
        project_files=[".continuerules"],
    ),
    AgentSpec(
        key="copilot",
        name="GitHub Copilot",
        vendor="GitHub",
        global_dirs=[],
        project_dirs=[".github/instructions", ".github/prompts"],
        project_files=[".github/copilot-instructions.md", "copilot-instructions.md"],
    ),
    AgentSpec(
        key="trae",
        name="Trae",
        vendor="ByteDance",
        global_dirs=["~/.trae"],
        project_dirs=[".trae"],
        project_files=[".trae/rules"],
    ),
    AgentSpec(
        key="kiro",
        name="Kiro",
        vendor="AWS",
        global_dirs=["~/.kiro"],
        project_dirs=[".kiro"],
        project_files=[".kiro/steering"],
    ),
    AgentSpec(
        key="qoder",
        name="Qoder",
        vendor="Alibaba",
        global_dirs=["~/.qoder"],
        project_dirs=[".qoder"],
        project_files=["AGENTS.md"],
    ),
    AgentSpec(
        key="workbuddy",
        name="WorkBuddy",
        vendor="Tencent",
        global_dirs=["~/.workbuddy"],
        project_dirs=[".workbuddy"],
        project_files=[],
    ),
    AgentSpec(
        key="opencode",
        name="OpenCode",
        vendor="SST",
        global_dirs=["~/.opencode", "~/.config/opencode"],
        project_dirs=[".opencode"],
        project_files=["AGENTS.md", "OPENCODE.md"],
    ),
    AgentSpec(
        key="amp",
        name="Amp",
        vendor="Sourcegraph",
        global_dirs=["~/.amp"],
        project_dirs=[".amp"],
        project_files=["AGENTS.md"],
    ),
    AgentSpec(
        key="droid",
        name="Droid",
        vendor="Factory",
        global_dirs=["~/.factory"],
        project_dirs=[".factory"],
        project_files=["AGENTS.md"],
    ),
    AgentSpec(
        key="zed",
        name="Zed",
        vendor="Zed Industries",
        global_dirs=["~/.config/zed"],
        project_dirs=[".zed"],
        project_files=[],
    ),
    AgentSpec(
        key="zcode",
        name="ZCode",
        vendor="",
        global_dirs=["~/.zcode"],
        project_dirs=[".zcode"],
        project_files=[],
    ),
    AgentSpec(
        key="openclaw",
        name="OpenClaw",
        vendor="Steipete",
        global_dirs=["~/.openclaw"],
        project_dirs=[".openclaw"],
        project_files=["AGENTS.md", "SOUL.md"],
    ),
    AgentSpec(
        key="hermes",
        name="Hermes",
        vendor="NousResearch",
        global_dirs=["~/.hermes", "%LOCALAPPDATA%/hermes"],
        project_dirs=[".hermes"],
        project_files=["AGENTS.md", "SOUL.md"],
    ),
    AgentSpec(
        key="pi",
        name="Pi Coding Agent",
        vendor="Mario Zechner",
        global_dirs=["~/.pi"],
        project_dirs=[".pi"],
        project_files=["AGENTS.md"],
    ),
    # 跨工具共享的 Agent Skills 目录（agentskills 约定：~/.agents/skills/<name>/SKILL.md）
    AgentSpec(
        key="agent-skills",
        name="Agent Skills",
        vendor="跨工具共享",
        global_dirs=["~/.agents"],
        project_dirs=[".agents"],
        project_files=[],
    ),
    # 兜底：未被上述清单覆盖的规则文件（如小众/自研 Agent）
    AgentSpec(
        key="generic",
        name="通用 AI 规则文件",
        vendor="—",
        global_dirs=[],
        project_dirs=[".ai/rules", ".agent"],
        project_files=[
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
            "ai-rules.md",
            ".cursorrules",
            ".windsurfrules",
        ],
    ),
]

SPECS_BY_KEY: dict[str, AgentSpec] = {s.key: s for s in SPECS}
