"""
墨参 MoShen · 后端服务启动器
用于 Electron 模式下启动 FastAPI 服务（无 PyWebView，接受端口参数）
"""
import sys
import os
import argparse
from pathlib import Path

# 确保可以导入同目录下的模块
sys.path.insert(0, str(Path(__file__).parent))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="墨参 MoShen 后端服务")
    parser.add_argument("--port", type=int, default=8765, help="服务端口")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="监听地址")
    args = parser.parse_args()

    print(f"墨参 MoShen 后端服务启动中...")
    print(f"  监听: {args.host}:{args.port}")

    # 延迟导入，确保 sys.path 已设置
    from main import app

    config = uvicorn.Config(
        app=app,
        host=args.host,
        port=args.port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    server.run()


if __name__ == "__main__":
    main()
