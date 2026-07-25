from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 对外暴露给 IDE 的 API Key
    api_key: str = Field(default="sk-doubao-local-change-me", alias="API_KEY")
    host: str = Field(default="127.0.0.1", alias="HOST")
    port: int = Field(default=8787, alias="PORT")

    # 豆包上游会话
    doubao_cookie: str = Field(default="", alias="DOUBAO_COOKIE")
    device_id: str = Field(default="", alias="DOUBAO_DEVICE_ID")
    web_id: str = Field(default="", alias="DOUBAO_WEB_ID")
    tea_uuid: str = Field(default="", alias="DOUBAO_TEA_UUID")
    fp: str = Field(default="", alias="DOUBAO_FP")
    ms_token: str = Field(default="", alias="DOUBAO_MS_TOKEN")
    a_bogus: str = Field(default="", alias="DOUBAO_A_BOGUS")
    web_tab_id: str = Field(default="", alias="DOUBAO_WEB_TAB_ID")
    extra_accounts: str = Field(default="", alias="DOUBAO_EXTRA_ACCOUNTS")
    bot_id: str = Field(default="7338286299411103781", alias="DOUBAO_BOT_ID")
    aid: str = Field(default="497858", alias="DOUBAO_AID")
    pc_version: str = Field(default="3.28.8", alias="DOUBAO_PC_VERSION")
    version_code: str = Field(default="20800", alias="DOUBAO_VERSION_CODE")
    base_url: str = Field(default="https://www.doubao.com", alias="DOUBAO_BASE_URL")
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        alias="DOUBAO_UA",
    )
    request_timeout: float = Field(default=180.0, alias="REQUEST_TIMEOUT")
    connect_timeout: float = Field(default=15.0, alias="CONNECT_TIMEOUT")
    default_model: str = Field(default="doubao-chat", alias="DEFAULT_MODEL")
    # auto: 同线程粘会话(快)，新线程开新会话(不串台)  — 推荐
    # fresh: 每次都新开会话（最慢，最不串）
    # sticky: 同 user 一直粘（最快，但跨对话可能串）
    session_mode: str = Field(default="auto", alias="SESSION_MODE")
    # 注入编程 Agent 系统提示
    agent_prompt_enabled: bool = Field(default=True, alias="AGENT_PROMPT_ENABLED")
    agent_prompt_path: str = Field(default="prompts/coding-agent.md", alias="AGENT_PROMPT_PATH")
    history_max_turns: int = Field(default=8, alias="HISTORY_MAX_TURNS")
    history_assistant_max_chars: int = Field(default=500, alias="HISTORY_ASSISTANT_MAX_CHARS")

    # 真正 Agent 运行时
    agent_mode: str = Field(default="auto", alias="AGENT_MODE")
    # auto: 无外部tools时走本地Agent循环；有外部tools时只做bridge
    # always: 总是本地Agent循环（忽略外部tools执行，仍可参考）
    # bridge: 只桥接 tool_calls 给 IDE
    # off: 纯聊天
    agent_max_rounds: int = Field(default=8, alias="AGENT_MAX_ROUNDS")
    agent_workspace: str = Field(default="", alias="AGENT_WORKSPACE")
    agent_allow_shell: bool = Field(default=True, alias="AGENT_ALLOW_SHELL")
    agent_shell_timeout: float = Field(default=60.0, alias="AGENT_SHELL_TIMEOUT")

    def require_upstream(self) -> None:
        missing = []
        if not self.doubao_cookie.strip():
            missing.append("DOUBAO_COOKIE")
        if not self.device_id.strip():
            missing.append("DOUBAO_DEVICE_ID")
        if not self.web_id.strip():
            missing.append("DOUBAO_WEB_ID")
        if not self.tea_uuid.strip():
            missing.append("DOUBAO_TEA_UUID")
        if not self.fp.strip():
            missing.append("DOUBAO_FP")
        if missing:
            raise RuntimeError(
                "缺少上游配置: " + ", ".join(missing) + "。请先填写 .env"
            )


def get_settings() -> Settings:
    # 不缓存，确保改 .env 后无需重启即可生效（uvicorn --reload 时更稳）
    return Settings()