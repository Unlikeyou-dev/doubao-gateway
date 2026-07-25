"""Doubao Local Gateway - OpenAI 兼容的豆包网页 API 网关。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .agent_prompt import load_agent_prompt
from .agent_runtime import AgentRuntime
from .config import Settings, get_settings
from .doubao_client import ChatSessionState, DoubaoClient, DoubaoError
from .local_tools import LocalToolRuntime
from .tool_protocol import build_tool_aware_prompt, parse_tool_calls


app = FastAPI(title="Doubao Local Gateway", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_client: Optional[DoubaoClient] = None
_sessions: Dict[str, ChatSessionState] = {}


class ChatMessage(BaseModel):
    role: str
    content: Optional[Union[str, List[Any]]] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


class ChatCompletionsRequest(BaseModel):
    model: Optional[str] = None
    messages: List[ChatMessage]
    stream: bool = False
    temperature: Optional[float] = None
    user: Optional[str] = None
    conversation_id: Optional[str] = None
    section_id: Optional[str] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    functions: Optional[List[Dict[str, Any]]] = None
    function_call: Optional[Union[str, Dict[str, Any]]] = None
    agent: Optional[bool] = None


class AgentRequest(BaseModel):
    goal: str
    workspace: Optional[str] = None
    max_rounds: Optional[int] = None
    user: Optional[str] = None
    model: Optional[str] = None
    mode: Optional[str] = None


def get_client() -> DoubaoClient:
    global _client
    settings = get_settings()
    settings.require_upstream()
    if _client is None:
        _client = DoubaoClient(settings)
    return _client


def require_api_key(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    settings: Settings = Depends(get_settings),
) -> None:
    token = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()
    if not token or token != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


@app.get("/health")
async def health(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "ok": True,
        "model": settings.default_model,
        "has_cookie": bool(settings.doubao_cookie.strip()),
        "device_id": bool(settings.device_id.strip()),
        "session_mode": settings.session_mode,
        "cached_sessions": len(_sessions),
        "agent_prompt": settings.agent_prompt_enabled,
        "tools_bridge": True,
        "agent_mode": settings.agent_mode,
        "agent_workspace": settings.agent_workspace
        or str(Path(__file__).resolve().parent.parent),
    }


@app.get("/v1/models")
async def list_models(
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
) -> Dict[str, Any]:
    mid = settings.default_model
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "doubao-local-gateway",
            }
        ],
    }


def _session_key(req: ChatCompletionsRequest) -> str:
    if req.user:
        return f"user:{req.user}"
    if req.conversation_id:
        return f"conv:{req.conversation_id}"
    return "default"


def _is_new_thread(req: ChatCompletionsRequest) -> bool:
    return not any(m.role == "assistant" for m in req.messages)


def _get_state(req: ChatCompletionsRequest, settings: Settings) -> ChatSessionState:
    mode = (settings.session_mode or "auto").strip().lower()
    key = _session_key(req)
    new_thread = _is_new_thread(req)

    if req.conversation_id:
        state = _sessions.get(key) or ChatSessionState()
        state.conversation_id = req.conversation_id
        if req.section_id:
            state.section_id = req.section_id
        _sessions[key] = state
        return state

    if mode == "fresh" or (mode in {"auto", "sticky"} and new_thread):
        state = ChatSessionState()
        if req.section_id:
            state.section_id = req.section_id
        if mode != "fresh":
            _sessions[key] = state
        return state

    state = _sessions.get(key)
    if state is None:
        state = ChatSessionState()
        _sessions[key] = state
    if req.section_id:
        state.section_id = req.section_id
    return state


def _normalize_tools(req: ChatCompletionsRequest) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    if req.tools:
        tools.extend([t for t in req.tools if isinstance(t, dict)])
    if req.functions:
        for fn in req.functions:
            if isinstance(fn, dict):
                tools.append({"type": "function", "function": fn})
    return tools


def _build_prompt(req: ChatCompletionsRequest, settings: Settings, state: ChatSessionState) -> str:
    msgs = [m.model_dump(exclude_none=True) for m in req.messages]
    tools = _normalize_tools(req)
    agent_rules = ""
    inject_agent = settings.agent_prompt_enabled and (
        not state.conversation_id or _is_new_thread(req) or bool(tools)
    )
    if inject_agent:
        agent_rules = load_agent_prompt(settings.agent_prompt_path)

    tool_choice = req.tool_choice
    if isinstance(tool_choice, str) and tool_choice.lower() == "none":
        tools = []

    return build_tool_aware_prompt(
        messages=msgs,
        tools=tools,
        agent_rules=agent_rules,
        last_user_only_if_no_tools=bool(state.conversation_id),
    )


def _openai_error(message: str, code: str = "server_error", status: int = 500) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "message": message,
                "type": code,
                "code": code,
            }
        },
    )


def _completion_response(
    *,
    model: str,
    content: str,
    tool_calls: List[Dict[str, Any]],
    state: ChatSessionState,
    result_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    created = int(time.time())
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": content if content else (None if tool_calls else ""),
    }
    finish = "stop"
    if tool_calls:
        message["tool_calls"] = tool_calls
        finish = "tool_calls"

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish,
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "doubao": {
            "conversation_id": (result_meta or {}).get("conversation_id") or state.conversation_id,
            "section_id": (result_meta or {}).get("section_id") or state.section_id,
            "last_message_index": state.last_message_index,
            "tool_calls_count": len(tool_calls),
        },
    }


def _workspace(settings: Settings, override: Optional[str] = None) -> str:
    if override and override.strip():
        return str(Path(override).expanduser().resolve())
    if settings.agent_workspace.strip():
        return str(Path(settings.agent_workspace).expanduser().resolve())
    return str(Path(__file__).resolve().parent.parent)


def _should_run_local_agent(req: ChatCompletionsRequest, settings: Settings) -> bool:
    if req.agent is True:
        return True
    if req.agent is False:
        return False
    mode = (settings.agent_mode or "auto").strip().lower()
    if mode in {"off", "bridge"}:
        return False
    if mode == "always":
        return True
    external = _normalize_tools(req)
    if external:
        return False
    try:
        last = ""
        for m in reversed(req.messages):
            if m.role == "user":
                c = m.content
                last = c if isinstance(c, str) else ""
                break
        s = last.strip().lower()
        if len(s) <= 8 and s in {"你好", "您好", "hi", "hello", "在吗", "嗨", "hey"}:
            return False
    except Exception:
        pass
    return True


def _extract_goal(req: ChatCompletionsRequest) -> str:
    for m in reversed(req.messages):
        if m.role == "user":
            c = m.content
            if isinstance(c, str) and c.strip():
                return c.strip()
            if isinstance(c, list):
                parts = []
                for p in c:
                    if isinstance(p, str):
                        parts.append(p)
                    elif isinstance(p, dict) and p.get("type") == "text":
                        parts.append(str(p.get("text") or ""))
                text = "\n".join(x for x in parts if x).strip()
                if text:
                    return text
    return ""


async def _run_local_agent(
    *,
    client: DoubaoClient,
    settings: Settings,
    state: ChatSessionState,
    goal: str,
    workspace: Optional[str] = None,
    max_rounds: Optional[int] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    mode: Optional[str] = None,
) -> Dict[str, Any]:
    ws = _workspace(settings, workspace)
    runtime_tools = LocalToolRuntime(
        ws,
        shell_timeout=settings.agent_shell_timeout,
        allow_shell=settings.agent_allow_shell,
    )
    agent = AgentRuntime(
        client,
        runtime_tools,
        max_rounds=max_rounds or settings.agent_max_rounds,
        agent_prompt_path=settings.agent_prompt_path,
        agent_prompt_enabled=settings.agent_prompt_enabled,
        mode=mode or AgentRuntime.MODE_AGENT,
        workspace=ws,
    )
    result = await agent.run(goal, state=state, history_messages=history)
    return {
        "answer": result.answer,
        "rounds": result.rounds,
        "tool_trace": result.tool_trace,
        "skills": result.skills,
        "conversation_id": result.conversation_id or state.conversation_id,
        "section_id": result.section_id or state.section_id,
        "steps": [
            {
                "round": s.round,
                "content": (s.content or "")[:500],
                "tools": [((t.get("function") or {}).get("name")) for t in s.tool_calls],
            }
            for s in result.steps
        ],
    }


@app.post("/v1/sessions/reset")
async def reset_sessions(
    _: None = Depends(require_api_key),
) -> Dict[str, Any]:
    n = len(_sessions)
    _sessions.clear()
    return {"ok": True, "cleared": n}


@app.post("/v1/agent")
async def run_agent(
    req: AgentRequest,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    client: DoubaoClient = Depends(get_client),
):
    state = ChatSessionState()
    try:
        data = await _run_local_agent(
            client=client,
            settings=settings,
            state=state,
            goal=req.goal,
            workspace=req.workspace,
            max_rounds=req.max_rounds,
            mode=req.mode,
        )
    except DoubaoError as e:
        status = 429 if "limit" in str(e).lower() or e.code in {"710022004"} else 502
        return _openai_error(str(e), code=str(e.code), status=status)
    except Exception as e:
        return _openai_error(f"agent failed: {e}", code="agent_error", status=502)

    return {
        "ok": True,
        "model": req.model or settings.default_model,
        "answer": data["answer"],
        "rounds": data["rounds"],
        "steps": data["steps"],
        "tool_trace": data["tool_trace"],
        "skills": data.get("skills") or [],
        "mode": req.mode or "agent",
        "doubao": {
            "conversation_id": data["conversation_id"],
            "section_id": data["section_id"],
        },
        "workspace": _workspace(settings, req.workspace),
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionsRequest,
    request: Request,
    _: None = Depends(require_api_key),
    settings: Settings = Depends(get_settings),
    client: DoubaoClient = Depends(get_client),
):
    model = req.model or settings.default_model
    state = _get_state(req, settings)

    if _should_run_local_agent(req, settings):
        goal = _extract_goal(req)
        if not goal:
            return _openai_error("messages 中没有 user 文本", code="invalid_request", status=400)
        history = [
            m.model_dump(exclude_none=True)
            for m in req.messages[:-1]
            if m.role in {"system", "user", "assistant", "tool"}
        ]
        try:
            data = await _run_local_agent(
                client=client,
                settings=settings,
                state=state,
                goal=goal,
                history=history or None,
            )
        except DoubaoError as e:
            status = 429 if "limit" in str(e).lower() or e.code in {"710022004"} else 502
            return _openai_error(str(e), code=str(e.code), status=status)
        except Exception as e:
            return _openai_error(f"agent failed: {e}", code="agent_error", status=502)

        payload = _completion_response(
            model=model,
            content=data["answer"],
            tool_calls=[],
            state=state,
            result_meta={
                "conversation_id": data["conversation_id"],
                "section_id": data["section_id"],
            },
        )
        payload["agent"] = {
            "mode": "local_runtime",
            "rounds": data["rounds"],
            "steps": data["steps"],
            "tool_trace": data["tool_trace"],
            "skills": data.get("skills") or [],
            "workspace": _workspace(settings),
        }
        if req.stream:

            async def _agent_stream() -> AsyncGenerator[str, None]:
                created = int(time.time())
                chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {"role": "assistant"},
                                    "finish_reason": None,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                text = data["answer"] or ""
                step = 200
                for i in range(0, len(text), step):
                    part = text[i : i + step]
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "id": chunk_id,
                                "object": "chat.completion.chunk",
                                "created": created,
                                "model": model,
                                "choices": [
                                    {
                                        "index": 0,
                                        "delta": {"content": part},
                                        "finish_reason": None,
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "id": chunk_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                            "agent": payload.get("agent"),
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                yield "data: [DONE]\n\n"

            return StreamingResponse(
                _agent_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
            )
        return payload

    try:
        text = _build_prompt(req, settings, state)
    except DoubaoError as e:
        return _openai_error(str(e), code=e.code, status=400)

    if req.stream:
        return StreamingResponse(
            _stream_openai(client, text, state, model, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        result = await client.chat(text, state)
    except DoubaoError as e:
        status = 429 if "limit" in str(e).lower() or e.code in {"710022004"} else 502
        return _openai_error(str(e), code=str(e.code), status=status)
    except Exception as e:
        return _openai_error(f"upstream failed: {e}", code="upstream_error", status=502)

    raw = result.get("text") or ""
    tools = _normalize_tools(req)
    content, tool_calls = parse_tool_calls(raw) if tools or "tool_call" in raw else (raw, [])
    if isinstance(req.tool_choice, str) and req.tool_choice.lower() == "none":
        tool_calls = []
        content = raw

    return _completion_response(
        model=model,
        content=content,
        tool_calls=tool_calls,
        state=state,
        result_meta=result,
    )


async def _stream_openai(
    client: DoubaoClient,
    text: str,
    state: ChatSessionState,
    model: str,
    req: ChatCompletionsRequest,
) -> AsyncGenerator[str, None]:
    created = int(time.time())
    chunk_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    tools = _normalize_tools(req)
    expect_tools = bool(tools)

    if expect_tools:
        try:
            result = await client.chat(text, state)
        except DoubaoError as e:
            err = {
                "error": {
                    "message": str(e),
                    "type": str(e.code),
                    "code": str(e.code),
                    "detail": e.detail,
                }
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        except Exception as e:
            err = {
                "error": {
                    "message": f"upstream failed: {e}",
                    "type": "upstream_error",
                    "code": "upstream_error",
                }
            }
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return

        raw = result.get("text") or ""
        content, tool_calls = parse_tool_calls(raw)
        if isinstance(req.tool_choice, str) and req.tool_choice.lower() == "none":
            tool_calls = []
            content = raw

        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"

        if content:
            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': content}, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"

        if tool_calls:
            for i, tc in enumerate(tool_calls):
                delta = {
                    "tool_calls": [
                        {
                            "index": i,
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name"),
                                "arguments": tc.get("function", {}).get("arguments") or "",
                            },
                        }
                    ]
                }
                yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}, ensure_ascii=False)}\n\n"
            finish = "tool_calls"
        else:
            finish = "stop"

        final = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "doubao": {
                "conversation_id": state.conversation_id,
                "section_id": state.section_id,
                "last_message_index": state.last_message_index,
                "tool_calls_count": len(tool_calls),
            },
        }
        yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    first = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant"},
                "finish_reason": None,
            }
        ],
    }
    yield f"data: {json.dumps(first, ensure_ascii=False)}\n\n"

    try:
        async for delta in client.iter_text_deltas(text, state):
            payload = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": delta},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    except DoubaoError as e:
        err = {
            "error": {
                "message": str(e),
                "type": str(e.code),
                "code": str(e.code),
                "detail": e.detail,
            }
        }
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return
    except Exception as e:
        err = {
            "error": {
                "message": f"upstream failed: {e}",
                "type": "upstream_error",
                "code": "upstream_error",
            }
        }
        yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    final = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
        "doubao": {
            "conversation_id": state.conversation_id,
            "section_id": state.section_id,
            "last_message_index": state.last_message_index,
        },
    }
    yield f"data: {json.dumps(final, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


@app.get("/")
async def root(settings: Settings = Depends(get_settings)) -> Dict[str, Any]:
    return {
        "name": "doubao-local-gateway",
        "openai_base_url": f"http://{settings.host}:{settings.port}/v1",
        "endpoints": [
            "GET /health",
            "GET /v1/models",
            "POST /v1/chat/completions",
            "POST /v1/agent",
            "POST /v1/sessions/reset",
        ],
        "auth": "Authorization: Bearer <API_KEY>",
        "session_mode": settings.session_mode,
        "agent_prompt": settings.agent_prompt_enabled,
        "tools_bridge": True,
        "agent_mode": settings.agent_mode,
        "note": "local agent runtime + OpenAI tools bridge",
    }
