"""OpenAI-compatible tool calling bridge for Doubao text model.

IDE (Trae/Cursor/Cline) 会发 tools + messages(role=tool)。
豆包网页 API 没有原生 function calling，因此：
1) 把 tools 编成提示
2) 要求模型输出 <tool_call> JSON
3) 解析后转成 OpenAI tool_calls
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple


TOOL_CALL_RE = re.compile(
    r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
    re.DOTALL | re.IGNORECASE,
)
# 兜底：模型直接吐 JSON 数组/对象
RAW_JSON_TOOL_RE = re.compile(
    r"\{\s*\"name\"\s*:\s*\"[^\"]+\"\s*,\s*\"arguments\"\s*:\s*[\{\[]",
    re.DOTALL,
)


def _safe_json_loads(s: str) -> Any:
    try:
        return json.loads(s)
    except Exception:
        return None


def format_tools_for_prompt(tools: List[Dict[str, Any]], *, max_tools: int = 40) -> str:
    if not tools:
        return ""
    lines = [
        "## 可用工具（必须通过结构化调用，不要假装已经执行）",
        "当你需要读文件/改文件/跑命令/搜索/调用 MCP 时，输出一个或多个：",
        "<tool_call>",
        '{"name":"工具名","arguments":{...}}',
        "</tool_call>",
        "规则：",
        "1. 需要真实外部信息或执行动作时，优先 tool_call，不要编造结果",
        "2. 可并行时一次输出多个 <tool_call>",
        "3. 不需要工具时直接正常回答用户，不要输出 tool_call",
        "4. arguments 必须是合法 JSON 对象",
        "5. 工具执行后，用户会把 role=tool 的结果发回，你再继续",
        "",
        "工具列表：",
    ]
    for i, tool in enumerate(tools[:max_tools]):
        if not isinstance(tool, dict):
            continue
        ttype = tool.get("type") or "function"
        fn = tool.get("function") if ttype == "function" else tool
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        desc = str(fn.get("description") or "").strip()
        params = fn.get("parameters") or fn.get("input_schema") or {}
        try:
            schema = json.dumps(params, ensure_ascii=False)
        except Exception:
            schema = "{}"
        if len(schema) > 1200:
            schema = schema[:1200] + "…(schema truncated)"
        lines.append(f"- name: `{name}`")
        if desc:
            lines.append(f"  description: {desc[:400]}")
        lines.append(f"  parameters: {schema}")
    if len(tools) > max_tools:
        lines.append(f"... 另有 {len(tools) - max_tools} 个工具未展开")
    return "\n".join(lines)


def format_messages_for_prompt(messages: List[Dict[str, Any]], *, max_tool_chars: int = 2500) -> str:
    """把含 tool 的消息整理成豆包可读文本。"""
    blocks: List[str] = []
    for msg in messages:
        role = str(msg.get("role") or "")
        content = msg.get("content")
        if isinstance(content, list):
            # multimodal / content parts
            texts = []
            for part in content:
                if isinstance(part, str):
                    texts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
            content = "\n".join(t for t in texts if t)
        content_s = "" if content is None else str(content)

        if role == "system":
            if content_s.strip():
                blocks.append(f"[system]\n{content_s.strip()}")
        elif role == "user":
            if content_s.strip():
                blocks.append(f"[user]\n{content_s.strip()}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                parts = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    args = fn.get("arguments") or "{}"
                    parts.append(f"<tool_call>\n{json.dumps({'name': name, 'arguments': _maybe_obj(args)}, ensure_ascii=False)}\n</tool_call>")
                body = "\n".join(parts)
                if content_s.strip():
                    body = content_s.strip() + "\n" + body
                blocks.append(f"[assistant]\n{body}")
            elif content_s.strip():
                blocks.append(f"[assistant]\n{content_s.strip()}")
        elif role == "tool":
            name = msg.get("name") or msg.get("tool_call_id") or "tool"
            text = content_s.strip()
            if len(text) > max_tool_chars:
                text = text[:max_tool_chars] + "\n…(tool result truncated)"
            blocks.append(f"[tool result name={name}]\n{text}")
        else:
            if content_s.strip():
                blocks.append(f"[{role}]\n{content_s.strip()}")
    return "\n\n".join(blocks).strip()


def _maybe_obj(args: Any) -> Any:
    if isinstance(args, (dict, list)):
        return args
    if isinstance(args, str):
        parsed = _safe_json_loads(args)
        return parsed if parsed is not None else args
    return args


def parse_tool_calls(text: str) -> Tuple[str, List[Dict[str, Any]]]:
    """从模型文本中提取 tool_calls，返回 (可见文本, tool_calls)。"""
    if not text:
        return "", []

    calls: List[Dict[str, Any]] = []
    cleaned = text

    for m in TOOL_CALL_RE.finditer(text):
        raw = m.group(1).strip()
        obj = _safe_json_loads(raw)
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name") or obj.get("tool") or "").strip()
        if not name:
            continue
        arguments = obj.get("arguments", obj.get("parameters", {}))
        if not isinstance(arguments, (dict, list, str, int, float, bool)) and arguments is not None:
            arguments = {}
        if not isinstance(arguments, str):
            try:
                arguments = json.dumps(arguments, ensure_ascii=False)
            except Exception:
                arguments = "{}"
        calls.append(
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments,
                },
            }
        )
        cleaned = cleaned.replace(m.group(0), "")

    # 兜底：整段就是一个 tool json
    if not calls:
        obj = _safe_json_loads(text.strip())
        if isinstance(obj, dict) and (obj.get("name") or obj.get("tool")):
            name = str(obj.get("name") or obj.get("tool") or "").strip()
            arguments = obj.get("arguments", obj.get("parameters", {}))
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
            calls.append(
                {
                    "id": f"call_{uuid.uuid4().hex[:24]}",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
            cleaned = ""
        elif isinstance(obj, list):
            for item in obj:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or item.get("tool") or "").strip()
                if not name:
                    continue
                arguments = item.get("arguments", item.get("parameters", {}))
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
                calls.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:24]}",
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                )
            if calls:
                cleaned = ""

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, calls


def build_tool_aware_prompt(
    *,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]],
    agent_rules: str = "",
    last_user_only_if_no_tools: bool = True,
) -> str:
    tools = tools or []
    has_tool_roles = any(str(m.get("role")) == "tool" for m in messages)
    has_tool_calls_hist = any(bool(m.get("tool_calls")) for m in messages if isinstance(m, dict))

    if not tools and not has_tool_roles and not has_tool_calls_hist:
        # 普通聊天：只保留最近 user
        if last_user_only_if_no_tools:
            for m in reversed(messages):
                if m.get("role") == "user":
                    c = m.get("content")
                    if isinstance(c, str) and c.strip():
                        base = c.strip()
                        if agent_rules:
                            return agent_rules + "\n\n---\n\n## 用户请求\n" + base
                        return base
        return format_messages_for_prompt(messages)

    parts: List[str] = []
    if agent_rules:
        parts.append(agent_rules)
    if tools:
        parts.append(format_tools_for_prompt(tools))
    parts.append("## 当前对话\n" + format_messages_for_prompt(messages))
    parts.append(
        "## 你的输出（强制）\n"
        "现在用户请求需要工具。请立刻输出完整 tool_call，例如：\n"
        "<tool_call>\n"
        '{"name":"工具名","arguments":{...}}\n'
        "</tool_call>\n"
        "不要只输出 JSON 片段，不要省略结束标签，不要编造执行结果。"
        if tools
        else "## 你的输出\n直接回答用户。"
    )
    return "\n\n".join(parts)
