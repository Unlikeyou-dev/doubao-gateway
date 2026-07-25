"""本地工具运行时：让网关在无 IDE 工具时也能真正执行动作。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ToolResult:
    ok: bool
    name: str
    content: str
    meta: Optional[Dict[str, Any]] = None


class LocalToolRuntime:
    def __init__(
        self,
        workspace: str,
        *,
        shell_timeout: float = 60.0,
        max_read_chars: int = 120_000,
        max_grep_hits: int = 80,
        allow_shell: bool = True,
    ):
        self.workspace = Path(workspace).resolve()
        self.shell_timeout = shell_timeout
        self.max_read_chars = max_read_chars
        self.max_grep_hits = max_grep_hits
        self.allow_shell = allow_shell

    def openai_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "读取工作区内文件。可指定 offset/limit 行号。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "相对或绝对路径"},
                            "offset": {"type": "integer", "description": "起始行，从1开始"},
                            "limit": {"type": "integer", "description": "读取行数"},
                        },
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "写入/覆盖工作区内文件。优先用于新建；改已有文件也可用。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "replace_in_file",
                    "description": "在文件中精确替换一段文本（old→new）。old 必须唯一匹配。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "old_str": {"type": "string"},
                            "new_str": {"type": "string"},
                        },
                        "required": ["path", "old_str", "new_str"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "list_dir",
                    "description": "列出目录内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "目录路径，默认工作区根"},
                        },
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "glob",
                    "description": "按 glob 模式找文件，例如 **/*.py",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "path": {"type": "string", "description": "搜索根目录"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "grep",
                    "description": "在工作区用正则搜索文件内容。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "pattern": {"type": "string"},
                            "path": {"type": "string"},
                            "glob": {"type": "string", "description": "文件过滤如 *.py"},
                            "case_insensitive": {"type": "boolean"},
                        },
                        "required": ["pattern"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "run_shell",
                    "description": "在工作区执行 PowerShell/命令。用于测试、安装、查看状态。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "command": {"type": "string"},
                            "timeout": {"type": "number"},
                        },
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "finish",
                    "description": "任务完成时调用。arguments.answer 为最终给用户的答复。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "answer": {"type": "string"},
                        },
                        "required": ["answer"],
                    },
                },
            },
        ]

    def _resolve(self, path: str) -> Path:
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace / p
        p = p.resolve()
        try:
            p.relative_to(self.workspace)
        except ValueError as e:
            raise PermissionError(f"路径越出工作区: {p} (workspace={self.workspace})") from e
        return p

    def execute(self, name: str, arguments: Any) -> ToolResult:
        try:
            args = arguments
            if isinstance(arguments, str):
                try:
                    args = json.loads(arguments) if arguments.strip() else {}
                except Exception:
                    args = {"raw": arguments}
            if not isinstance(args, dict):
                args = {}

            # 兼容 IDE 常见命名
            aliases = {
                "Read": "read_file",
                "read": "read_file",
                "Write": "write_file",
                "write": "write_file",
                "SearchReplace": "replace_in_file",
                "str_replace": "replace_in_file",
                "LS": "list_dir",
                "Glob": "glob",
                "Grep": "grep",
                "RunCommand": "run_shell",
                "Shell": "run_shell",
                "Bash": "run_shell",
            }
            name = aliases.get(name, name)

            if name == "read_file":
                return self._read_file(args)
            if name == "write_file":
                return self._write_file(args)
            if name == "replace_in_file":
                return self._replace_in_file(args)
            if name == "list_dir":
                return self._list_dir(args)
            if name == "glob":
                return self._glob(args)
            if name == "grep":
                return self._grep(args)
            if name == "run_shell":
                return self._run_shell(args)
            if name == "finish":
                answer = str(args.get("answer") or "")
                return ToolResult(True, "finish", answer, {"final": True})
            return ToolResult(False, name, f"未知工具: {name}")
        except Exception as e:
            return ToolResult(False, name, f"工具执行失败: {type(e).__name__}: {e}")

    def _read_file(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path") or args.get("file_path") or args.get("filePath")
        if not path:
            return ToolResult(False, "read_file", "缺少 path")
        p = self._resolve(str(path))
        if not p.exists():
            return ToolResult(False, "read_file", f"文件不存在: {p}")
        if p.is_dir():
            return ToolResult(False, "read_file", f"是目录不是文件: {p}")
        text = p.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        offset = int(args.get("offset") or 1)
        limit = args.get("limit")
        if offset < 1:
            offset = 1
        start = offset - 1
        end = len(lines) if not limit else min(len(lines), start + int(limit))
        chunk = lines[start:end]
        numbered = [f"{i + start + 1:>6}|{line}" for i, line in enumerate(chunk)]
        out = "\n".join(numbered)
        if len(out) > self.max_read_chars:
            out = out[: self.max_read_chars] + "\n…(truncated)"
        meta = {"path": str(p), "total_lines": len(lines), "shown": f"{offset}-{end}"}
        return ToolResult(True, "read_file", out or "(空文件)", meta)

    def _write_file(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path") or args.get("file_path")
        content = args.get("content")
        if path is None or content is None:
            return ToolResult(False, "write_file", "缺少 path 或 content")
        p = self._resolve(str(path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
        return ToolResult(True, "write_file", f"已写入 {p} ({len(str(content))} chars)")

    def _replace_in_file(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path") or args.get("file_path")
        old = args.get("old_str") or args.get("old") or args.get("old_string")
        new = args.get("new_str") if "new_str" in args else args.get("new")
        if new is None:
            new = args.get("new_string")
        if not path or old is None or new is None:
            return ToolResult(False, "replace_in_file", "缺少 path/old_str/new_str")
        p = self._resolve(str(path))
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(str(old))
        if count == 0:
            return ToolResult(False, "replace_in_file", "old_str 未匹配到")
        if count > 1:
            return ToolResult(False, "replace_in_file", f"old_str 匹配 {count} 次，需唯一")
        p.write_text(text.replace(str(old), str(new), 1), encoding="utf-8")
        return ToolResult(True, "replace_in_file", f"已替换 {p}")

    def _list_dir(self, args: Dict[str, Any]) -> ToolResult:
        path = args.get("path") or "."
        p = self._resolve(str(path))
        if not p.exists():
            return ToolResult(False, "list_dir", f"不存在: {p}")
        if not p.is_dir():
            return ToolResult(False, "list_dir", f"不是目录: {p}")
        items = []
        for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            kind = "dir" if child.is_dir() else "file"
            items.append(f"{kind}\t{child.name}")
        return ToolResult(True, "list_dir", "\n".join(items[:500]) or "(空目录)", {"path": str(p)})

    def _glob(self, args: Dict[str, Any]) -> ToolResult:
        pattern = args.get("pattern") or args.get("glob")
        if not pattern:
            return ToolResult(False, "glob", "缺少 pattern")
        root = self._resolve(str(args.get("path") or "."))
        hits = []
        for p in root.glob(str(pattern)):
            try:
                rel = p.resolve().relative_to(self.workspace)
            except Exception:
                continue
            hits.append(str(rel).replace("\\", "/"))
            if len(hits) >= 200:
                break
        return ToolResult(True, "glob", "\n".join(hits) or "(无匹配)", {"count": len(hits)})

    def _grep(self, args: Dict[str, Any]) -> ToolResult:
        pattern = args.get("pattern") or args.get("query")
        if not pattern:
            return ToolResult(False, "grep", "缺少 pattern")
        flags = re.IGNORECASE if args.get("case_insensitive") else 0
        try:
            rx = re.compile(str(pattern), flags)
        except re.error as e:
            return ToolResult(False, "grep", f"正则错误: {e}")
        root = self._resolve(str(args.get("path") or "."))
        file_glob = args.get("glob") or "**/*"
        hits: List[str] = []
        files = root.glob(str(file_glob)) if root.is_dir() else [root]
        skip_ext = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".zip", ".exe", ".dll", ".pyd"}
        for fp in files:
            if not fp.is_file():
                continue
            if fp.suffix.lower() in skip_ext:
                continue
            if any(part in {".venv", "node_modules", "__pycache__", ".git"} for part in fp.parts):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    try:
                        rel = fp.resolve().relative_to(self.workspace)
                    except Exception:
                        rel = fp
                    hits.append(f"{rel}:{i}:{line[:300]}")
                    if len(hits) >= self.max_grep_hits:
                        return ToolResult(True, "grep", "\n".join(hits), {"truncated": True})
        return ToolResult(True, "grep", "\n".join(hits) or "(无匹配)", {"count": len(hits)})

    def _run_shell(self, args: Dict[str, Any]) -> ToolResult:
        if not self.allow_shell:
            return ToolResult(False, "run_shell", "shell 已禁用")
        command = args.get("command") or args.get("cmd")
        if not command:
            return ToolResult(False, "run_shell", "缺少 command")
        # 粗略拦截极危险命令
        lowered = str(command).lower()
        blocked = ["format ", "rm -rf /", "remove-item -recurse -force c:\\", "shutdown", "del /s /q c:\\"]
        if any(b in lowered for b in blocked):
            return ToolResult(False, "run_shell", "拒绝执行高危命令")
        timeout = float(args.get("timeout") or self.shell_timeout)
        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", str(command)],
                cwd=str(self.workspace),
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            out = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
            out = out.strip() or "(no output)"
            if len(out) > 20000:
                out = out[:20000] + "\n…(truncated)"
            ok = completed.returncode == 0
            return ToolResult(
                ok,
                "run_shell",
                f"exit={completed.returncode}\n{out}",
                {"returncode": completed.returncode},
            )
        except subprocess.TimeoutExpired:
            return ToolResult(False, "run_shell", f"超时 ({timeout}s)")
