"""桌面壳入口：单进程内起 FastAPI 服务，再用 pywebview 打开本机窗口。

- 服务跑在后台线程（关闭 uvicorn 的信号注册，非主线程无 signal 权限）
- 未安装 pywebview 时自动降级为默认浏览器打开，功能一致
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

# PyInstaller 无控制台模式下（console=False，双击运行）stdout/stderr 为 None
# 或缺 isatty() 的空流，uvicorn 配置日志时会直接崩溃 —— 先替换为安全的空流
if getattr(sys, "frozen", False):
    if sys.stdout is None or not hasattr(sys.stdout, "isatty"):
        sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None or not hasattr(sys.stderr, "isatty"):
        sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import APP_NAME, APP_VERSION, settings  # noqa: E402

WINDOW_TITLE = f"{APP_NAME} {APP_VERSION}"


def wait_port(host: str, port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.3)
    return False


def start_server(host: str, port: int):
    import uvicorn

    config = uvicorn.Config("app.main:app", host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    # 非主线程运行，禁用 uvicorn 的信号处理
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    threading.Thread(target=server.run, daemon=True).start()
    return server


def main() -> int:
    host, port = settings.host, settings.port
    url = f"http://{host}:{port}"

    print(f"[{APP_NAME}] 数据目录: {settings.data_home}")
    server = start_server(host, port)
    if not wait_port(host, port):
        print(f"[{APP_NAME}] 服务启动超时: {url}")
        return 1
    print(f"[{APP_NAME}] 服务已就绪: {url}")

    try:
        import webview  # noqa: PLC0415
    except ImportError:
        print(f"[{APP_NAME}] 未安装 pywebview，改用默认浏览器打开 (pip install pywebview)")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    window = webview.create_window(
        WINDOW_TITLE,
        url,
        width=1440,
        height=900,
        min_size=(1100, 700),
        text_select=True,
    )
    window.events.closing += lambda: setattr(server, "should_exit", True)  # type: ignore[union-attr]
    webview.start(debug=False)
    server.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
