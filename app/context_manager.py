"""上下文窗口管理：滑动窗口 + 历史压缩 + 长度感知 + 智能文件选择。

核心策略：
1. Agent 运行时维护完整推理链（goal/plan/steps/observations）
2. 每轮调用 Doubao 时只注入必要的上下文窗口
3. 保留最近 N 轮完整内容，更早轮次压缩为摘要
4. 基于文件重要性自动选择相关代码文件

借鉴 Aider 的 Repo Map 和 OpenHands 的 Memory Condenser 设计。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class CompressedRound:
    round: int
    summary: str
    tools: List[str] = field(default_factory=list)


@dataclass
class FileSignature:
    filename: str
    symbols: List[str]
    size: int


@dataclass
class ContextWindow:
    max_chars: int = 12000
    keep_recent_rounds: int = 3
    min_compress_ratio: float = 0.7
    max_repo_map_chars: int = 2000

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def extract_file_signatures(self, workspace: str, max_files: int = 20) -> List[FileSignature]:
        """提取工作区文件的符号信息（借鉴 Aider 的 Repo Map）"""
        sigs: List[FileSignature] = []
        ws = Path(workspace)
        if not ws.exists():
            return sigs

        python_files = sorted(ws.rglob("*.py"), key=lambda p: -p.stat().st_size)[:max_files]
        for fp in python_files:
            try:
                content = fp.read_text(encoding="utf-8", errors="ignore")
            except:
                continue
            
            symbols = []
            for match in re.finditer(r"(?:class|def)\s+(\w+)", content):
                symbols.append(match.group(1))
            
            rel_path = fp.relative_to(ws)
            sigs.append(FileSignature(
                filename=str(rel_path),
                symbols=symbols[:10],
                size=len(content),
            ))
        
        return sigs

    def build_repo_map(self, workspace: str) -> str:
        """构建仓库地图（借鉴 Aider 的 Repo Map）"""
        sigs = self.extract_file_signatures(workspace)
        if not sigs:
            return ""
        
        lines = ["## 项目结构"]
        for sig in sigs:
            lines.append(f"- `{sig.filename}` ({sig.size} chars)")
            if sig.symbols:
                lines.append(f"  符号: {', '.join(sig.symbols)}")
        
        repo_map = "\n".join(lines)
        if len(repo_map) > self.max_repo_map_chars:
            repo_map = repo_map[:self.max_repo_map_chars] + "\n\n[项目结构已截断]"
        
        return repo_map

    def compress_history(
        self,
        steps: List[Any],
        recent_messages: List[Dict[str, Any]],
    ) -> Tuple[List[CompressedRound], List[Dict[str, Any]]]:
        if len(steps) <= self.keep_recent_rounds:
            return [], recent_messages

        compressed: List[CompressedRound] = []
        for step in steps[:-self.keep_recent_rounds]:
            tools_used = []
            for tc in step.tool_calls:
                fn = tc.get("function") or {}
                tools_used.append(fn.get("name") or "")

            result_previews = []
            for tr in step.tool_results:
                preview = (tr.get("content") or "")[:200]
                result_previews.append(f"{tr.get('name')}: {preview}")

            summary = "\n".join(result_previews)
            if not summary:
                summary = f"第 {step.round} 轮：调用了 {', '.join(tools_used)}"

            compressed.append(
                CompressedRound(
                    round=step.round,
                    summary=summary,
                    tools=tools_used,
                )
            )

        recent_messages = recent_messages[-self._count_recent_messages(steps):]
        return compressed, recent_messages

    def _count_recent_messages(self, steps: List[Any]) -> int:
        count = 0
        for step in steps[-self.keep_recent_rounds:]:
            count += 1
            count += len(step.tool_results)
            count += 1
        return count

    def build_compressed_prompt(
        self,
        compressed_rounds: List[CompressedRound],
        recent_messages: List[Dict[str, Any]],
        agent_rules: str,
        tools_str: str,
        goal: str,
        workspace: str = "",
    ) -> str:
        parts: List[str] = []

        if agent_rules:
            parts.append(agent_rules)

        if workspace:
            repo_map = self.build_repo_map(workspace)
            if repo_map:
                parts.append(repo_map)

        if compressed_rounds:
            summary_lines = ["## 历史摘要（已压缩）"]
            for cr in compressed_rounds:
                summary_lines.append(f"### 第 {cr.round} 轮")
                summary_lines.append(f"- 工具：{', '.join(cr.tools)}")
                summary_lines.append(f"- 结果：{cr.summary}")
            parts.append("\n".join(summary_lines))

        if tools_str:
            parts.append(tools_str)

        parts.append("## 当前任务")
        parts.append(f"目标：{goal}")

        if recent_messages:
            parts.append("## 当前对话")
            for msg in recent_messages:
                role = msg.get("role")
                content = msg.get("content")
                if role == "user":
                    parts.append(f"[用户]\n{content}")
                elif role == "assistant":
                    tc = msg.get("tool_calls")
                    if tc:
                        calls = []
                        for t in tc:
                            fn = t.get("function") or {}
                            calls.append(f"{fn.get('name')}({fn.get('arguments', {})})")
                        parts.append(f"[助手]\n工具调用：{', '.join(calls)}")
                    elif content:
                        parts.append(f"[助手]\n{content}")
                elif role == "tool":
                    name = msg.get("name") or "tool"
                    parts.append(f"[工具结果 {name}]\n{content}")

        parts.append(
            "## 输出要求\n"
            "需要工具就输出 <tool_call>；完成就调用 finish。"
        )

        return "\n\n".join(parts)

    def check_needs_compression(self, text: str) -> bool:
        return len(text) > self.max_chars * self.min_compress_ratio


def truncate_tool_result(content: str, max_chars: int = 3000) -> str:
    if len(content) <= max_chars:
        return content
    return content[:max_chars] + "\n\n[结果过长已截断]"


def build_summary_from_steps(steps: List[Any]) -> str:
    lines = ["## 执行摘要"]
    for step in steps:
        tools = []
        for tc in step.tool_calls:
            fn = tc.get("function") or {}
            tools.append(fn.get("name") or "")
        if tools:
            lines.append(f"- 第 {step.round} 轮：调用 {', '.join(tools)}")
            for tr in step.tool_results:
                preview = (tr.get("content") or "")[:150]
                lines.append(f"  - {tr.get('name')}: {preview}")
        elif step.content:
            lines.append(f"- 第 {step.round} 轮：{step.content[:200]}")
    return "\n".join(lines)
