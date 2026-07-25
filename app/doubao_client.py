from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

import httpx

from .config import Settings


@dataclass
class ChatSessionState:
    conversation_id: str = ""
    section_id: str = ""
    last_message_index: Optional[int] = None
    local_conversation_id: str = field(
        default_factory=lambda: f"local_{int(time.time() * 1000)}"
    )


class DoubaoError(Exception):
    def __init__(self, message: str, *, code: str = "doubao_error", detail: Any = None):
        super().__init__(message)
        self.code = code
        self.detail = detail


class AccountConfig:
    def __init__(
        self,
        cookie: str,
        device_id: str,
        web_id: str,
        tea_uuid: str,
        fp: str,
        ms_token: str = "",
        a_bogus: str = "",
        web_tab_id: str = "",
    ):
        self.cookie = cookie
        self.device_id = device_id
        self.web_id = web_id
        self.tea_uuid = tea_uuid
        self.fp = fp
        self.ms_token = ms_token
        self.a_bogus = a_bogus
        self.web_tab_id = web_tab_id
        self.last_use_time = 0.0
        self.fail_count = 0


class DoubaoClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._accounts: List[AccountConfig] = []
        self._current_account_idx = 0
        
        self._init_accounts()
        
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.request_timeout,
                connect=settings.connect_timeout,
                pool=settings.connect_timeout,
            ),
            headers={
                "user-agent": settings.user_agent,
                "origin": settings.base_url,
                "referer": f"{settings.base_url}/chat/",
                "accept-language": "zh-CN,zh;q=0.9",
            },
            follow_redirects=True,
            http2=False,
            limits=httpx.Limits(
                max_keepalive_connections=20,
                max_connections=40,
                keepalive_expiry=60.0,
            ),
        )

    def _init_accounts(self):
        if self.settings.doubao_cookie:
            self._accounts.append(AccountConfig(
                cookie=self.settings.doubao_cookie,
                device_id=self.settings.device_id,
                web_id=self.settings.web_id,
                tea_uuid=self.settings.tea_uuid,
                fp=self.settings.fp,
                ms_token=self.settings.ms_token or "",
                a_bogus=self.settings.a_bogus or "",
                web_tab_id=self.settings.web_tab_id or "",
            ))
        
        extra_accounts = self.settings.extra_accounts
        if extra_accounts:
            try:
                accounts_data = json.loads(extra_accounts)
                for acc in accounts_data:
                    if acc.get("cookie"):
                        self._accounts.append(AccountConfig(
                            cookie=acc.get("cookie", ""),
                            device_id=acc.get("device_id", self.settings.device_id),
                            web_id=acc.get("web_id", self.settings.web_id),
                            tea_uuid=acc.get("tea_uuid", self.settings.tea_uuid),
                            fp=acc.get("fp", self.settings.fp),
                            ms_token=acc.get("ms_token", ""),
                            a_bogus=acc.get("a_bogus", ""),
                            web_tab_id=acc.get("web_tab_id", ""),
                        ))
            except (json.JSONDecodeError, TypeError):
                pass

    def _get_current_account(self) -> AccountConfig:
        if not self._accounts:
            raise DoubaoError("No accounts configured", code="no_accounts")
        
        for _ in range(len(self._accounts)):
            account = self._accounts[self._current_account_idx]
            if account.fail_count >= 3:
                self._current_account_idx = (self._current_account_idx + 1) % len(self._accounts)
                continue
            return account
        
        for acc in self._accounts:
            acc.fail_count = 0
        return self._accounts[0]

    def _rotate_account(self):
        self._current_account_idx = (self._current_account_idx + 1) % len(self._accounts)

    def _mark_account_success(self, account: AccountConfig):
        account.last_use_time = time.time()
        account.fail_count = 0

    def _mark_account_fail(self, account: AccountConfig):
        account.fail_count += 1

    async def aclose(self) -> None:
        await self._client.aclose()

    def _common_params(self, account: AccountConfig) -> Dict[str, str]:
        params = {
            "aid": self.settings.aid,
            "real_aid": self.settings.aid,
            "device_id": account.device_id,
            "device_platform": "web",
            "doubao_device_platform": "web",
            "web_platform": "browser",
            "pc_version": self.settings.pc_version,
            "doubao_pc_version": self.settings.pc_version,
            "version_code": self.settings.version_code,
            "pkg_type": "release_version",
            "language": "zh",
            "region": "CN",
            "sys_region": "CN",
            "samantha_web": "1",
            "use-olympus-account": "1",
            "web_id": account.web_id,
            "tea_uuid": account.tea_uuid,
            "web_tab_id": account.web_tab_id or str(uuid.uuid4()),
            "tz_name": "Asia/Shanghai",
            "fp": account.fp,
        }
        if account.ms_token:
            params["msToken"] = account.ms_token
        if account.a_bogus:
            params["a_bogus"] = account.a_bogus
        return params

    def _build_body(self, text: str, state: ChatSessionState) -> Dict[str, Any]:
        now_ms = int(time.time() * 1000)
        need_create = not bool(state.conversation_id)
        body: Dict[str, Any] = {
            "client_meta": {
                "local_conversation_id": state.local_conversation_id,
                "conversation_id": state.conversation_id,
                "bot_id": self.settings.bot_id,
                "last_section_id": state.section_id,
                "last_message_index": state.last_message_index,
            },
            "messages": [
                {
                    "local_message_id": str(uuid.uuid4()),
                    "content_block": [
                        {
                            "block_type": 10000,
                            "content": {
                                "text_block": {
                                    "text": text,
                                    "icon_url": "",
                                    "icon_url_dark": "",
                                    "summary": "",
                                },
                                "pc_event_block": "",
                            },
                            "block_id": str(uuid.uuid4()),
                            "parent_id": "",
                            "meta_info": [],
                            "append_fields": [],
                        }
                    ],
                    "message_status": 0,
                }
            ],
            "option": {
                "send_message_scene": "",
                "create_time_ms": now_ms,
                "collect_id": "",
                "is_audio": False,
                "answer_with_suggest": False,
                "agent_mode": 2,
                "tts_switch": False,
                "need_deep_think": 0,
                "click_clear_context": False,
                "from_suggest": False,
                "is_regen": False,
                "is_replace": False,
                "is_from_click_option": False,
                "is_from_click_softlink": False,
                "disable_sse_cache": False,
                "select_text_action": "",
                "is_select_text": False,
                "resend_for_regen": False,
                "scene_type": 0,
                "unique_key": str(uuid.uuid4()),
                "start_seq": 0,
                "need_create_conversation": need_create,
                "conversation_init_option": {"need_ack_conversation": True},
                "regen_query_id": [],
                "edit_query_id": [],
                "regen_instruction": "",
                "no_replace_for_regen": False,
                "message_from": 0,
                "shared_app_name": "",
                "shared_app_id": "",
                "sse_recv_event_options": {"support_chunk_delta": True},
                "is_ai_playground": False,
                "is_old_user": True,
                "recovery_option": {
                    "is_recovery": False,
                    "req_create_time_sec": now_ms // 1000,
                    "append_sse_event_scene": 0,
                },
                "message_storage_type": 0,
            },
            "user_context": [],
            "ext": {
                "use_deep_think": "0",
                "fp": self.settings.fp,
                "commerce_credit_config_enable": "0",
            },
        }
        if need_create:
            body["ext"]["sub_conv_firstmet_type"] = "1"
            body["ext"]["conversation_init_option"] = json.dumps(
                {"need_ack_conversation": True}
            )
        return body

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: List[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text") or ""))
                elif isinstance(item, str):
                    parts.append(item)
            return "\n".join(p for p in parts if p).strip()
        return ""

    @classmethod
    def extract_user_text(cls, messages: List[Dict[str, Any]]) -> str:
        """只取最后一条 user 消息，避免 IDE 历史被整段拼进豆包。"""
        last = ""
        for msg in messages:
            if msg.get("role") != "user":
                continue
            text = cls._content_to_text(msg.get("content"))
            if text:
                last = text
        if not last:
            raise DoubaoError("messages 中没有可用的 user 文本", code="invalid_request")
        return last

    @classmethod
    def extract_system_text(cls, messages: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for msg in messages:
            if msg.get("role") != "system":
                continue
            text = cls._content_to_text(msg.get("content"))
            if text:
                parts.append(text)
        return "\n\n".join(parts[-2:]).strip()

    @classmethod
    def extract_prompt_with_history(
        cls,
        messages: List[Dict[str, Any]],
        *,
        max_turns: int = 8,
        assistant_max_chars: int = 500,
        include_history: bool = True,
    ) -> str:
        """无上游会话可复用时：把最近多轮整理成一段文本发给豆包。"""
        normalized: List[Dict[str, str]] = []
        for msg in messages:
            role = str(msg.get("role") or "")
            if role not in {"system", "user", "assistant"}:
                continue
            text = cls._content_to_text(msg.get("content"))
            if text:
                normalized.append({"role": role, "text": text})
        if not normalized:
            raise DoubaoError("messages 中没有可用文本", code="invalid_request")

        last_user = ""
        for item in reversed(normalized):
            if item["role"] == "user":
                last_user = item["text"]
                break
        if not last_user:
            raise DoubaoError("messages 中没有可用的 user 文本", code="invalid_request")

        has_assistant = any(i["role"] == "assistant" for i in normalized)
        if (not include_history) or (not has_assistant):
            return last_user

        recent = [i for i in normalized if i["role"] != "system"][-max_turns:]
        lines: List[str] = [
            "下面是同一会话的最近对话，请只回答最后一条用户消息，不要重复历史长文。",
            "",
        ]
        for item in recent:
            if item["role"] == "user":
                lines.append(f"用户：{item['text']}")
            else:
                t = item["text"]
                if len(t) > assistant_max_chars:
                    t = t[:assistant_max_chars] + "…(已截断)"
                lines.append(f"助手：{t}")
        lines.append("")
        lines.append(f"请回答最后这条用户消息：{last_user}")
        return "\n".join(lines)

    async def stream_chat(
        self, text: str, state: Optional[ChatSessionState] = None
    ) -> AsyncGenerator[Dict[str, Any], None]:
        state = state or ChatSessionState()
        body = self._build_body(text, state)
        
        url = f"{self.settings.base_url}/chat/completion"
        
        for attempt in range(len(self._accounts) + 1):
            account = self._get_current_account()
            
            headers = {
                "accept": "*/*",
                "content-type": "application/json",
                "agw-js-conv": "str, str",
                "last-event-id": "undefined",
                "cookie": account.cookie,
            }
            
            try:
                async with self._client.stream(
                    "POST",
                    url,
                    params=self._common_params(account),
                    headers=headers,
                    json=body,
                ) as resp:
                    new_ms = resp.headers.get("x-ms-token")
                    if new_ms:
                        account.ms_token = new_ms

                    if resp.status_code >= 400:
                        raw = (await resp.aread()).decode("utf-8", errors="ignore")
                        if resp.status_code == 429:
                            self._mark_account_fail(account)
                            self._rotate_account()
                            continue
                        raise DoubaoError(
                            f"上游 HTTP {resp.status_code}",
                            code="upstream_http_error",
                    detail=raw[:2000],
                )

                    event_name = "message"
                    data_lines: List[str] = []
                    async for line in resp.aiter_lines():
                        if line is None:
                            continue
                        if line.startswith("event:"):
                            event_name = line[6:].strip()
                            continue
                        if line.startswith("data:"):
                            data_lines.append(line[5:].lstrip())
                            continue
                        if line == "":
                            if not data_lines:
                                event_name = "message"
                                continue
                            raw_data = "\n".join(data_lines)
                            payload: Any = raw_data
                            try:
                                payload = json.loads(raw_data) if raw_data else {}
                            except Exception:
                                pass
                            item = {"event": event_name, "data": payload}
                            self._sync_state(item, state)
                            yield item
                            event_name = "message"
                            data_lines = []
                    self._mark_account_success(account)
                    return
            except DoubaoError:
                raise
            except Exception as e:
                self._mark_account_fail(account)
                self._rotate_account()
                if attempt >= len(self._accounts):
                    raise DoubaoError(f"All accounts failed: {e}", code="all_accounts_failed")

    def _sync_state(self, item: Dict[str, Any], state: ChatSessionState) -> None:
        if item.get("event") != "SSE_ACK":
            return
        data = item.get("data")
        if not isinstance(data, dict):
            return
        meta = data.get("ack_client_meta") or {}
        if meta.get("conversation_id"):
            state.conversation_id = str(meta["conversation_id"])
        if meta.get("section_id"):
            state.section_id = str(meta["section_id"])
        query_list = data.get("query_list") or []
        if query_list:
            idx = query_list[0].get("message_index")
            if idx is not None:
                state.last_message_index = idx

    async def chat(
        self, text: str, state: Optional[ChatSessionState] = None
    ) -> Dict[str, Any]:
        state = state or ChatSessionState()
        chunks: List[str] = []
        brief = ""
        error = None
        async for item in self.stream_chat(text, state):
            event = item.get("event")
            data = item.get("data")
            if not isinstance(data, dict):
                continue
            if event == "CHUNK_DELTA":
                t = data.get("text")
                if t:
                    chunks.append(str(t))
            elif event == "STREAM_MSG_NOTIFY":
                blocks = ((data.get("content") or {}).get("content_block")) or []
                for b in blocks:
                    t = (((b.get("content") or {}).get("text_block") or {}).get("text"))
                    if t:
                        chunks.append(str(t))
            elif event == "STREAM_CHUNK":
                for op in data.get("patch_op") or []:
                    pv = op.get("patch_value") or {}
                    for b in pv.get("content_block") or []:
                        t = (((b.get("content") or {}).get("text_block") or {}).get("text"))
                        if t:
                            chunks.append(str(t))
            elif event == "SSE_REPLY_END":
                attr = data.get("msg_finish_attr") or {}
                if attr.get("brief"):
                    brief = str(attr["brief"])
            elif event == "STREAM_ERROR":
                error = data

        if error:
            raise DoubaoError(
                error.get("error_msg") or "上游流错误",
                code=str(error.get("error_code") or "stream_error"),
                detail=error,
            )

        joined = "".join(chunks).strip()
        # brief 常常是摘要/截断，tool_call JSON 必须用完整流文本
        if joined:
            text_out = joined
        else:
            text_out = brief
        return {
            "text": text_out,
            "conversation_id": state.conversation_id,
            "section_id": state.section_id,
            "last_message_index": state.last_message_index,
            "brief": brief,
        }

    async def iter_text_deltas(
        self, text: str, state: Optional[ChatSessionState] = None
    ) -> AsyncGenerator[str, None]:
        state = state or ChatSessionState()
        async for item in self.stream_chat(text, state):
            event = item.get("event")
            data = item.get("data")
            if event == "STREAM_ERROR" and isinstance(data, dict):
                raise DoubaoError(
                    data.get("error_msg") or "上游流错误",
                    code=str(data.get("error_code") or "stream_error"),
                    detail=data,
                )
            if event == "CHUNK_DELTA" and isinstance(data, dict):
                t = data.get("text")
                if t:
                    yield str(t)
            elif event == "STREAM_MSG_NOTIFY" and isinstance(data, dict):
                blocks = ((data.get("content") or {}).get("content_block")) or []
                for b in blocks:
                    t = (((b.get("content") or {}).get("text_block") or {}).get("text"))
                    if t:
                        yield str(t)
            elif event == "STREAM_CHUNK" and isinstance(data, dict):
                for op in data.get("patch_op") or []:
                    pv = op.get("patch_value") or {}
                    for b in pv.get("content_block") or []:
                        t = (((b.get("content") or {}).get("text_block") or {}).get("text"))
                        if t:
                            yield str(t)