"""Agent 接入层：把本地资产以「AI 可直接消费」的形态暴露。

这是「伺服」的核心——AI 通过 HTTP 拉取自己的工具/记忆/规范/技能：
  GET /api/v1/agent/context   汇总 Markdown，可直接注入 system prompt
  GET /api/v1/agent/tools     启用的工具清单（function-calling 风格 schema）
  GET /api/v1/agent/rules     启用的规范
  GET /api/v1/agent/memories  记忆（可按关键词过滤）
  GET /api/v1/agent/skills    技能包（含 SKILL.md 正文）
  GET /api/v1/agent/search    跨类型检索
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from app.deps import get_service, get_settings
from app.domain import AssetKind, AssetStatus

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _parse_kind(kind: str) -> AssetKind:
    try:
        return AssetKind(kind)
    except ValueError:
        raise HTTPException(404, f"未知资产类型: {kind}")


def _fetch(kind: AssetKind, tag: str | None, q: str | None, status: AssetStatus = AssetStatus.ENABLED):
    return get_service().list(kind=kind, status=status, tag=tag, q=q, include_body=True)


def _enabled_kinds(kinds: str) -> list[AssetKind]:
    return [_parse_kind(k.strip()) for k in kinds.split(",") if k.strip()]


@router.get("/context", summary="汇总上下文（可直接注入 system prompt）")
def context(
    kinds: str = "rule,memory,skill,tool",
    tag: str | None = None,
    q: str | None = None,
    max_chars: int = 24000,
):
    svc = get_service()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# Nexus Agent Context",
        f"> 数据目录: {get_settings().data_home}",
        f"> 生成时间: {now}",
        "",
    ]
    for kind in _enabled_kinds(kinds):
        items = _fetch(kind, tag, q)
        lines.append(f"## {kind.label} ({len(items)})")
        lines.append("")
        for it in items:
            tags = "、".join(it.get("tags") or []) or "—"
            lines.append(f"### {it['name']}")
            lines.append(f"- id: `{it['id']}` · 标签: {tags} · 更新: {it['updated_at']}")
            if it.get("metadata"):
                lines.append(f"- 元数据: `{it['metadata']}`")
            lines.append("")
            body = (it.get("body") or "").strip()
            if body:
                lines.append(body)
                lines.append("")
        lines.append("")

    text = "\n".join(lines)
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n...（已截断，请用 /agent/search 精确检索）"
        truncated = True
    return {
        "markdown": text,
        "truncated": truncated,
        "counts": {k.value: len(_fetch(k, tag, q)) for k in _enabled_kinds(kinds)},
        "total": svc.count(),
    }


@router.get("/tools", summary="启用的工具清单（function-calling 风格）")
def tools(tag: str | None = None):
    items = _fetch(AssetKind.TOOL, tag, None)
    schemas = []
    for it in items:
        meta = it.get("metadata") or {}
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": it["name"],
                    "description": meta.get("description", ""),
                    "parameters": meta.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
                "nexus": {
                    "id": it["id"],
                    "runner": meta.get("runner"),
                    "tags": it.get("tags", []),
                },
            }
        )
    return {"count": len(schemas), "tools": schemas}


@router.get("/rules", summary="启用的规范")
def rules(tag: str | None = None, q: str | None = None):
    items = _fetch(AssetKind.RULE, tag, q)
    return {
        "count": len(items),
        "markdown": "\n\n".join(
            f"### {i['name']}\n{(i.get('body') or '').strip()}" for i in items
        ),
        "items": items,
    }


@router.get("/memories", summary="记忆列表（可按关键词过滤）")
def memories(tag: str | None = None, q: str | None = None, limit: int = 50):
    items = _fetch(AssetKind.MEMORY, tag, q)[:limit]
    return {
        "count": len(items),
        "markdown": "\n\n".join(
            f"- {i['name']}: {(i.get('body') or '').strip()}" for i in items
        ),
        "items": items,
    }


@router.get("/skills", summary="启用的技能包")
def skills(tag: str | None = None):
    items = _fetch(AssetKind.SKILL, tag, None)
    return {
        "count": len(items),
        "items": [
            {
                "id": i["id"],
                "name": i["name"],
                "description": (i.get("metadata") or {}).get("description", ""),
                "tags": i.get("tags", []),
                "path": i.get("rel_path"),
                "body": i.get("body", ""),
            }
            for i in items
        ],
    }


@router.get("/search", summary="跨类型检索")
def search(q: str, kinds: str | None = None, limit: int = 20):
    if not q.strip():
        raise HTTPException(400, "检索关键词不能为空")
    if kinds:
        target = _enabled_kinds(kinds)
    else:
        target = list(AssetKind)
    out = []
    for kind in target:
        out.extend(get_service().list(kind=kind, q=q, include_body=True))
    out.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return {"query": q, "count": len(out[:limit]), "items": out[:limit]}
