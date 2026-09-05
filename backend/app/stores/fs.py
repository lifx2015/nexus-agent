"""文件存储引擎：负责资产 Markdown 文件的物理读写与 frontmatter 序列化。

布局（相对 data_home/assets）：
  tools/  <name>.md
  memory/ <name>.md
  rules/  <name>.md
  skills/ <name>/SKILL.md      # 目录形式，可附带其他文件(脚本/参考)，对齐 Claude Skills
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from app.core.config import ASSET_DIR_NAMES, Settings
from app.domain import Asset, AssetKind


def dir_name(kind: AssetKind | str) -> str:
    """资产类型 -> 目录名（tools/memory/rules/skills）。"""
    k = kind.value if isinstance(kind, AssetKind) else kind
    return ASSET_DIR_NAMES[k]

_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_SKILL_MAIN = "SKILL.md"
_HEAD_SCAN_SIZE = 8192  # 定位 id 时只扫 frontmatter 头部
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class StoreError(Exception):
    pass


def sanitize_filename(name: str) -> str:
    """清洗文件名中的非法字符，保留中文与可读性。"""
    cleaned = _INVALID_FILENAME_CHARS.sub("-", name).strip().strip(".")
    if not cleaned:
        raise StoreError("名称不能为空或全为非法字符")
    return cleaned


def render_asset_text(asset: Asset) -> str:
    """将 Asset 序列化为 Markdown 文本（frontmatter + body）。"""
    meta = {
        "id": asset.id,
        "kind": asset.kind.value,
        "name": asset.name,
        "status": asset.status.value,
        "tags": [str(t) for t in asset.tags],
        "version": int(asset.version),
        "created_at": asset.created_at,
        "updated_at": asset.updated_at,
        "metadata": dict(asset.metadata),
    }
    fm = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    )
    return f"---\n{fm}---\n\n{asset.body}"


def parse_asset_text(text: str, kind: AssetKind, rel_path: str) -> Asset:
    """解析 Markdown 文本为 Asset。缺失元数据用默认值补齐。"""
    m = _FRONT_MATTER_RE.match(text)
    if m:
        raw = m.group(1)
        meta = yaml.safe_load(raw) if raw.strip() else {}
        if not isinstance(meta, dict):
            meta = {}
        body = text[m.end():]
    else:
        meta = {}
        body = text

    data = dict(meta)
    data.setdefault("kind", kind.value)
    data["body"] = body.lstrip("\n")
    data["rel_path"] = rel_path
    asset = Asset.from_dict(data, kind=kind)
    # 由文件位置决定 kind，避免歧义
    asset.kind = kind
    return asset


def rel_path_for(kind: AssetKind, name: str) -> str:
    """根据 kind 计算资产文件相对 assets_root 的路径。"""
    safe = sanitize_filename(name)
    d = dir_name(kind)
    if kind is AssetKind.SKILL:
        return f"{d}/{safe}/{_SKILL_MAIN}"
    return f"{d}/{safe}.md"


class FileStore:
    """对 assets 根目录下四类资产的物理文件访问。"""

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.ensure_layout()

    # ---- 路径 ---------------------------------------------------------
    def root(self) -> Path:
        return self.settings.assets_root

    def path_of(self, rel_path: str) -> Path:
        p = self.root() / rel_path
        # 防御：拒绝越出 assets 根
        try:
            p.resolve().relative_to(self.root().resolve())
        except ValueError:
            raise StoreError(f"非法路径: {rel_path}")
        return p

    # ---- 文件读写 ------------------------------------------------------
    def _iter_rel_paths(self, kind: AssetKind) -> list[str]:
        """列出某类资产的相对路径（不读内容）。"""
        base = self.settings.asset_dir(kind.value)
        prefix = base.name
        if kind is AssetKind.SKILL:
            return [
                f"{prefix}/{d.name}/{_SKILL_MAIN}"
                for d in sorted(p for p in base.iterdir() if p.is_dir())
                if (d / _SKILL_MAIN).exists()
            ]
        return [
            f"{prefix}/{f.name}"
            for f in sorted(p for p in base.iterdir() if p.is_file() and p.suffix == ".md")
        ]

    def list_assets(self, kind: AssetKind) -> list[Asset]:
        """列出某类全部资产（含 body）。"""
        return [self._read(rel, kind) for rel in self._iter_rel_paths(kind)]

    def _match_id(self, kind: AssetKind, asset_id: str) -> str | None:
        """定位 id 对应的文件：仅解析 frontmatter 头部，避免读取全部正文。"""
        for rel in self._iter_rel_paths(kind):
            with self.path_of(rel).open("r", encoding="utf-8") as f:
                head = f.read(_HEAD_SCAN_SIZE)
                if head.count("---") < 2:  # frontmatter 超长，回退读全文
                    f.seek(0)
                    head = f.read()
            m = _FRONT_MATTER_RE.match(head)
            if not m:
                continue
            meta = yaml.safe_load(m.group(1)) if m.group(1).strip() else None
            if isinstance(meta, dict) and str(meta.get("id")) == asset_id:
                return rel
        return None

    def read_asset(self, kind: AssetKind, asset_id: str) -> Asset | None:
        """按 id 读取资产；id 存于 frontmatter，故需定位文件后再读正文。"""
        rel = self._match_id(kind, asset_id)
        return self._read(rel, kind) if rel else None

    def read_skill_files(self, rel_path: str) -> dict[str, str]:
        """读取 skill 目录内所有文件内容（SKILL.md + 附属文件），供 UI 展示。"""
        base = self.path_of(rel_path).parent
        return {p.name: p.read_text(encoding="utf-8") for p in sorted(base.iterdir()) if p.is_file()}

    def write_asset(self, asset: Asset) -> Path:
        """写入资产文件（skill 自动建目录），返回目标路径。"""
        rel = rel_path_for(asset.kind, asset.name)
        p = self.path_of(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(render_asset_text(asset), encoding="utf-8")
        asset.rel_path = rel
        return p

    def rename_asset(self, kind: AssetKind, old_rel: str, new_name: str) -> str:
        """资产改名时同步移动文件/目录，返回新 rel_path。"""
        old = self.path_of(old_rel)
        if not old.exists():
            raise StoreError(f"源文件不存在: {old_rel}")

        target_name = sanitize_filename(new_name)
        if kind is AssetKind.SKILL:
            new_dir = old.parent.parent / target_name
            new_path = new_dir / _SKILL_MAIN
        else:
            new_path = old.parent / f"{target_name}.md"

        if new_path == old:
            return old_rel
        if new_path.exists():
            raise StoreError(f"目标文件已存在: {new_path.name}")

        if kind is AssetKind.SKILL:
            new_dir.mkdir(parents=True, exist_ok=True)
            for src in old.parent.iterdir():
                if src.is_file():
                    src.replace(new_dir / src.name)
            old.parent.rmdir()
            rel = f"{dir_name(kind)}/{target_name}/{_SKILL_MAIN}"
        else:
            old.replace(new_path)
            rel = f"{dir_name(kind)}/{target_name}.md"
        return rel

    def delete_asset(self, kind: AssetKind, rel_path: str) -> None:
        p = self.path_of(rel_path)
        if not p.exists():
            return
        if kind is AssetKind.SKILL:
            d = p.parent
            if d.exists():
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
        else:
            p.unlink()

    def _read(self, rel: str, kind: AssetKind) -> Asset:
        p = self.path_of(rel)
        text = p.read_text(encoding="utf-8")
        return parse_asset_text(text, kind, rel)
