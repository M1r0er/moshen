"""
墨参 MoShen · 桌面应用入口
使用 PyWebView 将 Web 应用打包为桌面应用，无需浏览器
"""
import sys
import os
import time
import threading
import socket
from pathlib import Path

# 确保可以导入同目录下的模块
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn


def find_free_port(start: int = 8765, end: int = 8780) -> int:
    """查找可用端口"""
    for port in range(start, end):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    return start


def wait_for_server(host: str, port: int, timeout: float = 15.0) -> bool:
    """等待服务器启动就绪"""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect((host, port))
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.3)
    return False


def start_server(port: int, ready_event: threading.Event):
    """在后台线程中启动 FastAPI 服务器"""
    # 延迟导入，确保 sys.path 已设置
    from main import app

    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_level="warning",  # 减少控制台输出
        access_log=False,
    )
    server = uvicorn.Server(config)

    # 标记服务器已就绪
    ready_event.set()
    server.run()


def main():
    """桌面应用主入口"""
    print("=" * 50)
    print("  墨参 MoShen · 小说写作助手 (桌面版)")
    print("=" * 50)
    print()

    # 查找可用端口
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    print(f"  正在启动服务...")

    # 在后台线程启动服务器
    ready_event = threading.Event()
    server_thread = threading.Thread(
        target=start_server, args=(port, ready_event), daemon=True
    )
    server_thread.start()

    # 等待服务器就绪
    if not wait_for_server("127.0.0.1", port):
        print("  错误：服务器启动失败")
        input("按 Enter 键退出...")
        sys.exit(1)

    print(f"  服务已就绪")
    print(f"  正在打开桌面窗口...")

    # 导入 pywebview
    try:
        import webview
    except ImportError:
        print("  错误：未安装 pywebview，正在安装...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pywebview", "-q"])
        import webview

    # 创建桌面窗口
    window = webview.create_window(
        title="墨参 MoShen · 小说写作助手",
        url=url,
        width=1400,
        height=900,
        min_size=(1024, 700),
        text_select=True,
    )

    # 启动窗口（阻塞，关闭窗口后继续）
    webview.start(debug=False)

    # 窗口关闭后退出
    print("  窗口已关闭，正在停止服务...")
    sys.exit(0)


if __name__ == "__main__":
    main()
