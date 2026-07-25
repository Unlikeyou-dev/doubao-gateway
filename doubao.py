#!/usr/bin/env python3
"""Doubao Gateway CLI 启动脚本。"""

import argparse
import os
import sys
import uvicorn

from app.main import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Doubao Local Gateway")
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "127.0.0.1"),
        help="监听地址 (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8787")),
        help="监听端口 (default: 8787)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="启用热重载",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="工作进程数 (default: 1)",
    )
    args = parser.parse_args()

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=args.workers,
        log_level="info",
    )


if __name__ == "__main__":
    main()