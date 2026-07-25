#!/usr/bin/env python3
"""Doubao Gateway CLI 启动脚本。"""

import argparse
import os
import sys
import uvicorn

from app.config import Settings  # noqa: F401


def _print_banner(host: str, port: int) -> None:
    settings = Settings()
    print("", flush=True)
    print("=" * 60, flush=True)
    print("  Doubao Local Gateway 已启动", flush=True)
    print("=" * 60, flush=True)
    print(f"  API 地址: http://{host}:{port}/v1", flush=True)
    print(f"  API Key:  {settings.api_key}", flush=True)
    print(f"  模型名称: {settings.default_model}", flush=True)
    print("-" * 60, flush=True)
    print("  IDE 配置步骤:", flush=True)
    print("  1. 在 IDE 的 AI 模型配置中选择 '自定义配置'", flush=True)
    print("  2. API 格式: OpenAI Chat Completions", flush=True)
    print(f"  3. 自定义请求地址: http://{host}:{port}/v1", flush=True)
    print(f"  4. 模型 ID: {settings.default_model}", flush=True)
    print(f"  5. API 密钥: {settings.api_key}", flush=True)
    print("=" * 60, flush=True)


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

    _print_banner(args.host, args.port)

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