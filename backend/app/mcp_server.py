"""MCP Server（stdio）：让 CodeBuddy / Claude Desktop / 任意 MCP 客户端直接读写本地资产。

运行：
    python -m app.mcp_server

客户端配置示例（mcpServers）：
    "nexus-agent": {
      "command": "<backend/.venv/Scripts/python.exe>",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<项目路径>/backend",
      "env": { "NEXUS_DATA_HOME": "C:/Users/you/.nexus-agent" }
    }
"""
from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from app.core.config import Settings
from app.domain import AssetKind, AssetStatus
from app.stores.service import AssetService

# 兼容 mcp SDK 2.x（FastMCP 已更名为 MCPServer）
mcp = MCPServer("nexus-agent")
_service = AssetService(Settings())


def _kind(kind: str) -> AssetKind:
    try:
        return AssetKind(kind)
    except ValueError:
        raise ValueError(f"未知类型: {kind}（可用 tool/memory/rule/skill）")


def _status(s: str) -> AssetStatus:
    try:
        return AssetStatus(s)
    except ValueError:
        raise ValueError(f"未知状态: {s}（可用 enabled/disabled/archived）")


def _fmt(items: list[dict], with_body: bool = False) -> str:
    if not items:
        return "（无结果）"
    lines = []
    for i in items:
        tags = ",".join(i.get("tags") or []) or "-"
        head = f"- [{i['kind']}] {i['name']} (id={i['id']}, status={i['status']}, tags={tags})"
        if with_body:
            head += f"\n  {(i.get('body') or '').strip()}"
        lines.append(head)
    return "\n".join(lines)


@mcp.tool()
def list_assets(kind: str = "", status: str = "enabled", tag: str = "", q: str = "") -> str:
    """列出资产。kind 为空表示全部类型，可选 tool/memory/rule/skill。"""
    items = _service.list(
        kind=_kind(kind) if kind else None,
        status=_status(status) if status else None,
        tag=tag or None,
        q=q or None,
    )
    return _fmt(items)


@mcp.tool()
def get_asset(kind: str, asset_id: str) -> str:
    """读取单条资产全文（frontmatter 元数据 + Markdown 正文）。"""
    a = _service.get(_kind(kind), asset_id)
    if a is None:
        return f"未找到: {kind}/{asset_id}"
    meta = a.to_dict(include_body=False)
    return (
        f"# {a.name}\n"
        f"kind={a.kind.value} id={a.id} status={a.status.value} version={a.version}\n"
        f"tags={a.tags} metadata={a.metadata}\n"
        f"path={a.rel_path}\n\n{a.body}"
    )


@mcp.tool()
def search_assets(q: str, kind: str = "") -> str:
    """按关键词检索名称/正文/标签。"""
    return _fmt(_service.search(q, _kind(kind) if kind else None), with_body=True)


@mcp.tool()
def context(kinds: str = "rule,memory,skill,tool", tag: str = "") -> str:
    """汇总启用中的资产为 Markdown，适合注入 system prompt。"""
    out = []
    for k in [x.strip() for x in kinds.split(",") if x.strip()]:
        items = _service.list(kind=_kind(k), status=_status("enabled"), tag=tag or None, include_body=True)
        out.append(f"## {_kind(k).label} ({len(items)})")
        for i in items:
            out.append(f"### {i['name']}\n{(i.get('body') or '').strip()}")
    return "\n\n".join(out) or "（无启用资产）"


@mcp.tool()
def create_asset(
    kind: str, name: str, body: str = "", tags: str = "", status: str = "enabled", metadata: str = "{}"
) -> str:
    """新建资产。tags 用逗号分隔，metadata 为 JSON 字符串。"""
    import json

    md = json.loads(metadata) if metadata.strip() else {}
    a = _service.create(
        kind=_kind(kind),
        name=name,
        body=body,
        status=_status(status),
        tags=[t.strip() for t in tags.split(",") if t.strip()],
        metadata=md,
    )
    return f"已创建: {a.kind.value}/{a.id} -> {a.rel_path}"


@mcp.tool()
def update_asset(
    kind: str, asset_id: str, name: str = "", body: str = "", status: str = "", tags: str = ""
) -> str:
    """更新资产（仅传入的字段生效）。"""
    patch: dict = {}
    if name:
        patch["name"] = name
    if body:
        patch["body"] = body
    if status:
        patch["status"] = _status(status)
    if tags:
        patch["tags"] = [t.strip() for t in tags.split(",") if t.strip()]
    if not patch:
        return "未提供任何更新字段"
    a = _service.update(_kind(kind), asset_id, patch)
    return f"已更新: {a.kind.value}/{a.id} v{a.version}"


@mcp.tool()
def delete_asset(kind: str, asset_id: str) -> str:
    """删除资产（同时移除数据目录中的文件）。"""
    ok = _service.delete(_kind(kind), asset_id)
    return "已删除" if ok else f"未找到: {kind}/{asset_id}"


@mcp.resource("nexus://stats")
def stats() -> str:
    """资产统计概览。"""
    s = _service.stats()
    return f"总计 {s['total']} 条\n按类型: {s['totals']}\n明细: {s['by_kind']}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
