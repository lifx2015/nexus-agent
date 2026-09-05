"""SkillHub (https://skillhub.cn) 公共 API 客户端。

全部为公开只读接口（无需登录），带 TTL 内存缓存，线程安全。
关键事实（2026-09 实测）：
  - 列表接口是 /api/skills（无 /v1 前缀），响应为 {code, data:{skills,total}}
  - 批量详情 POST /api/v1/skills/batch {"slugs":[...]} → {items:[{skill,namespace,latestVersion}]}
  - 下载 GET /api/v1/download?slug=&version=&namespace= 返回 302 → COS zip（须跟随重定向）
  - 包内带 _meta.json（slug/version）；签名接口提供 package_md5 可做完整性校验
"""
from __future__ import annotations

import threading
import time
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.skillhub.cn"
USER_AGENT = "nexus-agent/1.0 (+local skill manager)"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
BATCH_CHUNK = 50


class SkillHubError(RuntimeError):
    """SkillHub 接口调用失败（网络/HTTP/格式）。"""


class _TTLCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._data: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple) -> Any | None:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < time.time():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: tuple, value: Any, ttl: float) -> None:
        with self._lock:
            self._data[key] = (time.time() + ttl, value)
            # 粗粒度清理，避免缓存无界增长
            if len(self._data) > 512:
                now = time.time()
                for k in [k for k, (e, _) in self._data.items() if e < now]:
                    self._data.pop(k, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


class SkillHubClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self._cache = _TTLCache()

    def close(self) -> None:
        try:
            self._http.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 基础 ----------------------------------------------------------
    def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = self._http.get(path, params=params)
        except httpx.HTTPError as e:
            raise SkillHubError(f"无法连接 SkillHub: {e}") from e
        if r.status_code >= 400:
            raise SkillHubError(f"SkillHub 接口错误 {r.status_code}: {path}")
        try:
            return r.json()
        except ValueError as e:
            raise SkillHubError(f"SkillHub 返回非 JSON: {path}") from e

    def ping(self) -> dict:
        """连通性探测 + 仓库规模（pageSize=1，5 分钟缓存）。"""
        key = ("ping",)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = self._get("/api/skills", {"pageSize": 1, "page": 1})
        out = {"online": True, "total": int(data.get("data", {}).get("total") or 0)}
        self._cache.set(key, out, 300)
        return out

    # ---- 浏览 ----------------------------------------------------------
    def categories(self) -> list[dict]:
        key = ("categories",)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = self._get("/api/v1/categories")
        items = data.get("items") or []
        self._cache.set(key, items, 24 * 3600)
        return items

    def list_skills(
        self,
        q: str = "",
        category: str = "",
        page: int = 1,
        page_size: int = 20,
        sort_by: str = "downloads",
        order: str = "desc",
    ) -> dict:
        params = {
            "page": page,
            "pageSize": page_size,
            "sortBy": sort_by,
            "order": order,
            "source": "all",
        }
        if q:
            params["keyword"] = q
        if category:
            params["category"] = category
        key = ("list", q, category, page, page_size, sort_by, order)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        data = self._get("/api/skills", params)
        if data.get("code", 0) != 0:
            raise SkillHubError(f"SkillHub 列表接口错误: {data.get('message')}")
        payload = data.get("data", {})
        out = {"skills": payload.get("skills") or [], "total": int(payload.get("total") or 0)}
        self._cache.set(key, out, 300)
        return out

    # ---- 详情 / 版本 ----------------------------------------------------
    def batch(self, slugs: list[str]) -> list[dict]:
        """批量取技能详情（含 latestVersion 与 changelog）。分块 + 15 分钟缓存。"""
        unique = list(dict.fromkeys(s for s in slugs if s))
        out: list[dict] = []
        for i in range(0, len(unique), BATCH_CHUNK):
            chunk = unique[i : i + BATCH_CHUNK]
            key = ("batch", tuple(chunk))
            cached = self._cache.get(key)
            if cached is None:
                try:
                    r = self._http.post("/api/v1/skills/batch", json={"slugs": chunk})
                except httpx.HTTPError as e:
                    raise SkillHubError(f"无法连接 SkillHub: {e}") from e
                if r.status_code >= 400:
                    raise SkillHubError(f"SkillHub 批量接口错误 {r.status_code}")
                try:
                    payload = r.json()
                except ValueError as e:
                    raise SkillHubError("SkillHub 批量接口返回非 JSON") from e
                cached = payload.get("items") or []
                self._cache.set(key, cached, 900)
            out.extend(cached)
        return out

    def skill(self, slug: str) -> dict:
        """单个技能详情（含 namespace）。404 返回 {}。"""
        key = ("skill", slug)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        try:
            data = self._get(f"/api/v1/skills/{slug}")
        except SkillHubError as e:
            if "404" in str(e):
                out = {}
                self._cache.set(key, out, 900)
                return out
            raise
        out = data if isinstance(data, dict) else {}
        self._cache.set(key, out, 900)
        return out

    def versions(self, slug: str, namespace: str = "") -> list[dict]:
        key = ("versions", slug, namespace)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        params = {"namespace": namespace} if namespace else None
        data = self._get(f"/api/v1/skills/{slug}/versions", params)
        items = data.get("versions") or []
        self._cache.set(key, items, 900)
        return items

    def signature(self, slug: str, version: str, namespace: str = "") -> dict | None:
        params = {"namespace": namespace} if namespace else None
        try:
            return self._get(
                f"/api/v1/open/skills/{slug}/versions/{version}/signature", params
            )
        except SkillHubError:
            return None  # 签名缺失不阻断更新，仅放弃校验

    # ---- 下载 ----------------------------------------------------------
    def download_bytes(self, slug: str, version: str = "", namespace: str = "") -> bytes:
        """下载技能包 zip（跟随重定向到 COS），限 200MB。"""
        params: dict = {"slug": slug}
        if version:
            params["version"] = version
        if namespace:
            params["namespace"] = namespace
        try:
            r = self._http.get("/api/v1/download", params=params)
        except httpx.HTTPError as e:
            raise SkillHubError(f"下载失败: {e}") from e
        if r.status_code >= 400:
            raise SkillHubError(f"下载失败 {r.status_code}: {slug}@{version or 'latest'}")
        if len(r.content) > MAX_DOWNLOAD_BYTES:
            raise SkillHubError("技能包超过 200MB，已拒绝下载")
        if len(r.content) < 64 or r.content[:4] != b"PK\x03\x04":
            raise SkillHubError("下载内容不是有效的 zip 包")
        return r.content
