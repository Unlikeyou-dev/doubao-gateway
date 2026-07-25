"""真正的 Agent 循环：plan → tool_call → observe → 再决策，直到 finish。

关键改进：
1. 上下文窗口管理：滑动窗口 + 历史压缩，避免无限增长
2. Agent会话与Doubao会话解耦：每轮fresh，Agent维护完整推理链
3. 增强tool_call解析健壮性：处理格式错误、添加重试提示
4. 上下文长度感知：自动触发压缩
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from .agent_prompt import load_prompt_with_skills, wrap_user_text
from .context_manager import ContextWindow, build_summary_from_steps, truncate_tool_result
from .doubao_client import ChatSessionState, DoubaoClient, DoubaoError
from .local_tools import LocalToolRuntime, ToolResult
from .memory import ProjectMemory
from .tool_parallel import ParallelToolExecutor, build_parallel_tool_calls, format_parallel_results
from .tool_protocol import build_tool_aware_prompt, format_tools_for_prompt, parse_tool_calls


@dataclass
class AgentStep:
    round: int
    assistant_raw: str
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    tool_results: List[Dict[str, Any]] = field(default_factory=list)
    content: str = ""


@dataclass
class AgentRunResult:
    answer: str
    steps: List[AgentStep]
    rounds: int
    conversation_id: str = ""
    section_id: str = ""
    finish_reason: str = "stop"
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    skills: List[str] = field(default_factory=list)


class AgentRuntime:
    MODE_AGENT = "agent"
    MODE_PLAN = "plan"
    MODE_DEBUG = "debug"

    def __init__(
        self,
        client: DoubaoClient,
        tools: LocalToolRuntime,
        *,
        max_rounds: int = 8,
        agent_prompt_path: str = "",
        agent_prompt_enabled: bool = True,
        mode: str = MODE_AGENT,
        workspace: str = "",
    ):
        self.client = client
        self.tools = tools
        self.max_rounds = max_rounds
        self.agent_prompt_path = agent_prompt_path
        self.agent_prompt_enabled = agent_prompt_enabled
        self.mode = mode
        self.workspace = workspace
        self.memory = ProjectMemory(workspace) if workspace else None
        self.parallel_executor = ParallelToolExecutor(tools)

    def _rules(self, goal: str) -> Tuple[str, List[str]]:
        if not self.agent_prompt_enabled:
            return "", []
        base, skills = load_prompt_with_skills(
            goal,
            prompt_path=self.agent_prompt_path,
            enabled=True,
        )
        
        mode_rules = ""
        if self.mode == self.MODE_PLAN:
            mode_rules = """
## 计划模式（强制）
你只输出计划，不执行任何代码或工具调用。
计划格式：
1. 目标分析
2. 关键步骤
3. 风险评估
4. 验证标准
输出后直接调用 finish，arguments.answer 为计划内容。
"""
        elif self.mode == self.MODE_DEBUG:
            mode_rules = """
## 调试模式（强制）
重点关注错误追踪和根因分析：
1. 收集错误信息（日志、堆栈、配置）
2. 重现问题
3. 定位根因
4. 验证修复
输出详细的调试报告和建议。
"""
        else:
            mode_rules = """
## Agent 运行模式（强制）
你现在处于自主 Agent 循环中：
1. 先调查（list_dir/glob/grep/read_file），再修改
2. 需要动作就输出 tool_call；需要结束就调用 finish
3. 不要编造工具结果；每次只能基于真实 tool result
4. 优先并行多个只读工具
5. 改完用 run_shell 验证（若合适）
6. 最终必须调用 finish，arguments.answer 写给用户的最终答案
7. 工具名只用：read_file, write_file, replace_in_file, list_dir, glob, grep, run_shell, finish

输出格式示例：
<tool_call>
{"name":"glob","arguments":{"pattern":"**/*.py"}}
</tool_call>
"""
        
        memory_summary = ""
        if self.memory:
            memory_summary = self.memory.build_context_summary()
            if memory_summary:
                base = base + "\n\n## 记忆上下文\n" + memory_summary
        
        return (base + "\n" + mode_rules).strip(), skills

    async def run(
        self,
        user_goal: str,
        *,
        state: Optional[ChatSessionState] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentRunResult:
        result = await self._run_internal(user_goal, state=state, history_messages=history_messages)
        return result

    async def run_streaming(
        self,
        user_goal: str,
        *,
        state: Optional[ChatSessionState] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        async for event in self._run_streaming_internal(user_goal, state=state, history_messages=history_messages):
            yield event

    async def _run_internal(
        self,
        user_goal: str,
        *,
        state: Optional[ChatSessionState] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> AgentRunResult:
        state = state or ChatSessionState()
        context_window = ContextWindow()
        openai_tools = self.tools.openai_tools()
        tools_str = format_tools_for_prompt(openai_tools)

        steps: List[AgentStep] = []
        tool_trace: List[Dict[str, Any]] = []
        final_answer = ""
        activated_skills: List[str] = []
        all_messages: List[Dict[str, Any]] = []

        for rnd in range(1, self.max_rounds + 1):
            if rnd == 1:
                agent_rules, activated_skills = self._rules(user_goal)
                all_messages.append({"role": "user", "content": user_goal})
            else:
                agent_rules = "继续完成任务。需要工具就 tool_call；完成就 finish。"

            compressed_rounds, recent_messages = context_window.compress_history(
                steps, all_messages
            )

            prompt = context_window.build_compressed_prompt(
                compressed_rounds=compressed_rounds,
                recent_messages=recent_messages,
                agent_rules=agent_rules,
                tools_str=tools_str,
                goal=user_goal,
                workspace=self.workspace or "",
            )

            prompt += (
                f"\n\n## 当前回合 {rnd}/{self.max_rounds}\n"
                "若信息不足：先调查。若已可交付：调用 finish。"
            )

            if context_window.check_needs_compression(prompt):
                prompt = prompt[: context_window.max_chars] + "\n\n[上下文已压缩]"

            try:
                result = await self.client.chat(prompt, state)
            except DoubaoError:
                raise

            raw = (result.get("text") or "").strip()
            
            parse_attempts = 0
            max_parse_attempts = 2
            content, tool_calls = parse_tool_calls(raw)
            
            while not tool_calls and parse_attempts < max_parse_attempts:
                parse_attempts += 1
                retry_prompt = (
                    f"你的上一次输出未能被解析为有效的工具调用。\n"
                    f"原始输出：\n{raw[:500]}\n\n"
                    f"请重新输出，必须使用以下格式：\n"
                    f"<tool_call>\n{{\"name\":\"工具名\",\"arguments\":{{...}}}}\n</tool_call>\n"
                    f"确保 JSON 格式正确，包含完整的开始和结束标签。"
                )
                try:
                    result = await self.client.chat(retry_prompt, ChatSessionState())
                    raw = (result.get("text") or "").strip()
                    content, tool_calls = parse_tool_calls(raw)
                except DoubaoError:
                    break

            step = AgentStep(round=rnd, assistant_raw=raw, tool_calls=tool_calls, content=content or "")
            all_messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls or None,
                }
            )

            if not tool_calls:
                if content and rnd >= 2:
                    final_answer = content
                    steps.append(step)
                    break
                all_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你没有调用工具。请用 tool_call 开始调查，"
                            "或若已完成请调用 finish。"
                        ),
                    }
                )
                steps.append(step)
                continue

            results_for_msg: List[Dict[str, Any]] = []
            finished = False
            
            if len(tool_calls) > 1:
                parallel_calls = build_parallel_tool_calls(tool_calls)
                parallel_results = self.parallel_executor.execute_parallel(parallel_calls)
                for pr in parallel_results:
                    name = pr.tool_name
                    tr = ToolResult(ok=pr.error is None, content=str(pr.result or ""), meta={})
                    truncated_content = truncate_tool_result(tr.content, 3000)
                    step.tool_results.append(
                        {
                            "name": name,
                            "ok": tr.ok,
                            "content": truncated_content[:2000],
                        }
                    )
                    tool_trace.append(
                        {
                            "round": rnd,
                            "tool": name,
                            "ok": tr.ok,
                            "meta": tr.meta,
                            "preview": truncated_content[:300],
                            "duration": pr.duration,
                        }
                    )
                    if name == "finish" or (tr.meta or {}).get("final"):
                        final_answer = truncated_content or content or ""
                        finished = True
                    results_for_msg.append(
                        {
                            "role": "tool",
                            "tool_call_id": pr.call_id,
                            "name": name,
                            "content": truncated_content if tr.ok else f"ERROR: {truncated_content}",
                        }
                    )
                    all_messages.append(results_for_msg[-1])
            else:
                for tc in tool_calls:
                    fn = (tc.get("function") or {})
                    name = str(fn.get("name") or "")
                    arguments = fn.get("arguments") or "{}"
                    tr = self.tools.execute(name, arguments)
                    truncated_content = truncate_tool_result(tr.content, 3000)
                    step.tool_results.append(
                        {
                            "name": name,
                            "ok": tr.ok,
                            "content": truncated_content[:2000],
                        }
                    )
                    tool_trace.append(
                        {
                            "round": rnd,
                            "tool": name,
                            "ok": tr.ok,
                            "meta": tr.meta,
                            "preview": truncated_content[:300],
                        }
                    )
                    if name == "finish" or (tr.meta or {}).get("final"):
                        final_answer = truncated_content or content or ""
                        finished = True
                    results_for_msg.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "name": name,
                            "content": truncated_content if tr.ok else f"ERROR: {truncated_content}",
                        }
                    )
                    all_messages.append(results_for_msg[-1])

            steps.append(step)
            if finished:
                break

            summary_lines = [f"[观察 r{rnd}]"]
            for item in results_for_msg:
                preview = str(item.get("content") or "")[:500]
                summary_lines.append(f"- {item.get('name')}: {preview}")
            all_messages.append({"role": "user", "content": "\n".join(summary_lines) + "\n请继续。完成则 finish。"})

        if not final_answer:
            if steps and steps[-1].content:
                final_answer = steps[-1].content
            else:
                final_answer = (
                    "Agent 在步数上限内未显式 finish。\n"
                    + "轨迹：\n"
                    + "\n".join(
                        f"r{s.round}: tools={[t.get('function', {}).get('name') for t in s.tool_calls]}"
                        for s in steps
                    )
                )

        if self.memory and final_answer:
            self.memory.record_file_change(user_goal, "agent_run", final_answer[:200])

        return AgentRunResult(
            answer=final_answer,
            steps=steps,
            rounds=len(steps),
            conversation_id=state.conversation_id,
            section_id=state.section_id,
            finish_reason="stop",
            tool_trace=tool_trace,
            skills=activated_skills,
        )

    async def _run_streaming_internal(
        self,
        user_goal: str,
        *,
        state: Optional[ChatSessionState] = None,
        history_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        state = state or ChatSessionState()
        context_window = ContextWindow()
        openai_tools = self.tools.openai_tools()
        tools_str = format_tools_for_prompt(openai_tools)

        steps: List[AgentStep] = []
        tool_trace: List[Dict[str, Any]] = []
        final_answer = ""
        activated_skills: List[str] = []
        all_messages: List[Dict[str, Any]] = []

        yield {"event": "start", "goal": user_goal, "max_rounds": self.max_rounds}

        for rnd in range(1, self.max_rounds + 1):
            if rnd == 1:
                agent_rules, activated_skills = self._rules(user_goal)
                all_messages.append({"role": "user", "content": user_goal})
            else:
                agent_rules = "继续完成任务。需要工具就 tool_call；完成就 finish。"

            compressed_rounds, recent_messages = context_window.compress_history(
                steps, all_messages
            )

            prompt = context_window.build_compressed_prompt(
                compressed_rounds=compressed_rounds,
                recent_messages=recent_messages,
                agent_rules=agent_rules,
                tools_str=tools_str,
                goal=user_goal,
                workspace=self.workspace or "",
            )

            prompt += (
                f"\n\n## 当前回合 {rnd}/{self.max_rounds}\n"
                "若信息不足：先调查。若已可交付：调用 finish。"
            )

            if context_window.check_needs_compression(prompt):
                prompt = prompt[: context_window.max_chars] + "\n\n[上下文已压缩]"

            yield {"event": "round_start", "round": rnd, "prompt_chars": len(prompt)}

            try:
                result = await self.client.chat(prompt, state)
            except DoubaoError as e:
                yield {"event": "error", "error": str(e), "code": e.code}
                raise

            raw = (result.get("text") or "").strip()
            
            parse_attempts = 0
            max_parse_attempts = 2
            content, tool_calls = parse_tool_calls(raw)
            
            while not tool_calls and parse_attempts < max_parse_attempts:
                parse_attempts += 1
                yield {"event": "retry", "round": rnd, "attempt": parse_attempts}
                retry_prompt = (
                    f"你的上一次输出未能被解析为有效的工具调用。\n"
                    f"原始输出：\n{raw[:500]}\n\n"
                    f"请重新输出，必须使用以下格式：\n"
                    f"<tool_call>\n{{\"name\":\"工具名\",\"arguments\":{{...}}}}\n</tool_call>\n"
                    f"确保 JSON 格式正确，包含完整的开始和结束标签。"
                )
                try:
                    result = await self.client.chat(retry_prompt, ChatSessionState())
                    raw = (result.get("text") or "").strip()
                    content, tool_calls = parse_tool_calls(raw)
                except DoubaoError:
                    break

            step = AgentStep(round=rnd, assistant_raw=raw, tool_calls=tool_calls, content=content or "")
            all_messages.append(
                {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": tool_calls or None,
                }
            )

            tool_names = [tc.get("function", {}).get("name") for tc in tool_calls] if tool_calls else []
            yield {"event": "thinking", "round": rnd, "tool_calls": tool_names, "content_preview": content[:200] if content else ""}

            if not tool_calls:
                if content and rnd >= 2:
                    final_answer = content
                    steps.append(step)
                    break
                all_messages.append(
                    {
                        "role": "user",
                        "content": (
                            "你没有调用工具。请用 tool_call 开始调查，"
                            "或若已完成请调用 finish。"
                        ),
                    }
                )
                steps.append(step)
                continue

            results_for_msg: List[Dict[str, Any]] = []
            finished = False
            
            if len(tool_calls) > 1:
                parallel_calls = build_parallel_tool_calls(tool_calls)
                yield {"event": "parallel_exec", "round": rnd, "tools": [pc.name for pc in parallel_calls]}
                parallel_results = self.parallel_executor.execute_parallel(parallel_calls)
                for pr in parallel_results:
                    name = pr.tool_name
                    tr = ToolResult(ok=pr.error is None, content=str(pr.result or ""), meta={})
                    truncated_content = truncate_tool_result(tr.content, 3000)
                    step.tool_results.append(
                        {
                            "name": name,
                            "ok": tr.ok,
                            "content": truncated_content[:2000],
                        }
                    )
                    tool_trace.append(
                        {
                            "round": rnd,
                            "tool": name,
                            "ok": tr.ok,
                            "meta": tr.meta,
                            "preview": truncated_content[:300],
                            "duration": pr.duration,
                        }
                    )
                    yield {"event": "tool_result", "round": rnd, "tool": name, "ok": tr.ok, "preview": truncated_content[:300], "duration": pr.duration}
                    if name == "finish" or (tr.meta or {}).get("final"):
                        final_answer = truncated_content or content or ""
                        finished = True
                    results_for_msg.append(
                        {
                            "role": "tool",
                            "tool_call_id": pr.call_id,
                            "name": name,
                            "content": truncated_content if tr.ok else f"ERROR: {truncated_content}",
                        }
                    )
                    all_messages.append(results_for_msg[-1])
            else:
                for tc in tool_calls:
                    fn = (tc.get("function") or {})
                    name = str(fn.get("name") or "")
                    arguments = fn.get("arguments") or "{}"
                    yield {"event": "tool_exec", "round": rnd, "tool": name, "arguments": arguments[:200]}
                    tr = self.tools.execute(name, arguments)
                    truncated_content = truncate_tool_result(tr.content, 3000)
                    step.tool_results.append(
                        {
                            "name": name,
                            "ok": tr.ok,
                            "content": truncated_content[:2000],
                        }
                    )
                    tool_trace.append(
                        {
                            "round": rnd,
                            "tool": name,
                            "ok": tr.ok,
                            "meta": tr.meta,
                            "preview": truncated_content[:300],
                        }
                    )
                    yield {"event": "tool_result", "round": rnd, "tool": name, "ok": tr.ok, "preview": truncated_content[:300]}
                    if name == "finish" or (tr.meta or {}).get("final"):
                        final_answer = truncated_content or content or ""
                        finished = True
                    results_for_msg.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id"),
                            "name": name,
                            "content": truncated_content if tr.ok else f"ERROR: {truncated_content}",
                        }
                    )
                    all_messages.append(results_for_msg[-1])

            steps.append(step)
            if finished:
                break

            summary_lines = [f"[观察 r{rnd}]"]
            for item in results_for_msg:
                preview = str(item.get("content") or "")[:500]
                summary_lines.append(f"- {item.get('name')}: {preview}")
            all_messages.append({"role": "user", "content": "\n".join(summary_lines) + "\n请继续。完成则 finish。"})

        if not final_answer:
            if steps and steps[-1].content:
                final_answer = steps[-1].content
            else:
                final_answer = (
                    "Agent 在步数上限内未显式 finish。\n"
                    + "轨迹：\n"
                    + "\n".join(
                        f"r{s.round}: tools={[t.get('function', {}).get('name') for t in s.tool_calls]}"
                        for s in steps
                    )
                )

        if self.memory and final_answer:
            self.memory.record_file_change(user_goal, "agent_run", final_answer[:200])

        yield {
            "event": "finish",
            "answer": final_answer,
            "rounds": len(steps),
            "steps": [
                {
                    "round": s.round,
                    "tools": [t.get("function", {}).get("name") for t in s.tool_calls],
                    "content_preview": s.content[:200] if s.content else "",
                }
                for s in steps
            ],
            "skills": activated_skills,
            "conversation_id": state.conversation_id,
            "section_id": state.section_id,
        }
