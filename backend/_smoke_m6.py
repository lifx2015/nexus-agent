"""临时冒烟脚本：验证 MCP server 工具与桌面壳服务启动（验证后删除）。"""
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
tmp = Path(tempfile.mkdtemp(prefix="nexus-m6-")) / "data"
os.environ["NEXUS_DATA_HOME"] = str(tmp)

from app.mcp_server import (  # noqa: E402
    context,
    create_asset,
    delete_asset,
    get_asset,
    list_assets,
    mcp,
    search_assets,
    stats,
    update_asset,
)


def check(cond, msg):
    if not cond:
        print("  FAIL:", msg)
        raise SystemExit(1)
    print("  ok:", msg)


try:
    # ---- MCP 工具注册 ----
    tools = asyncio.run(mcp.list_tools())  # 声明为 async def
    names = [t.name for t in tools]
    check(len(names) == 7, f"MCP 注册 7 个工具: {names}")
    resources = asyncio.run(mcp.list_resources())
    check(any("stats" in str(r.uri) for r in resources), f"MCP 资源: {[str(r.uri) for r in resources]}")

    # ---- MCP 工具业务 ----
    print(" ", create_asset("memory", "MCP 测试记忆", body="通过 MCP 创建的记忆", tags="测试,mcp"))
    text = context("memory")
    check("通过 MCP 创建的记忆" in text, "context 输出记忆正文")
    check("MCP 测试记忆" in list_assets("memory"), "list_assets 列出新资产")

    rows = [r for r in list_assets("").splitlines() if "MCP 测试记忆" in r]
    check(len(rows) == 1, "全类型列表包含新资产")
    asset_id = rows[0].split("id=")[1].split(",")[0]

    print(" ", update_asset("memory", asset_id, body="更新后的内容"))
    check("更新后的内容" in get_asset("memory", asset_id), "update + get 生效")
    check("更新后" in search_assets("更新后"), "search_assets 命中")
    print(" ", delete_asset("memory", asset_id))
    check(delete_asset("memory", asset_id).startswith("未找到"), "删除后不可再删")
    print("  stats:", stats().replace("\n", " | "))

    # ---- 桌面壳：服务启动与端口探测 ----
    sys.path.insert(0, str(repo / "desktop"))
    from main import start_server, wait_port  # noqa: E402

    server = start_server("127.0.0.1", 8899)
    check(wait_port("127.0.0.1", 8899, timeout=25), "桌面壳内嵌服务启动并可连接")
    server.should_exit = True

    print("\n[M6] MCP 接入 + 桌面壳服务验证通过。")
finally:
    shutil.rmtree(tmp.parent, ignore_errors=True)
