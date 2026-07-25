# Doubao Gateway

把豆包网页版转成 OpenAI 兼容 API，并赋予真正的 Agent 能力。

## ✨ 特性

- **OpenAI 兼容 API**: `/v1/chat/completions` 标准接口，支持流式响应
- **真正的 Agent**: 内置本地工具运行时，支持文件操作、命令执行、搜索
- **技能路由**: 根据用户意图自动激活专业技能（代码审查、调试、计划、安全审查等）
- **多模式切换**: Agent/Plan/Debug 三种运行模式
- **并行工具调用**: 批量执行文件读取等操作，提高效率
- **会话隔离**: 支持 fresh/sticky/auto 三种会话模式
- **跨会话记忆**: 项目上下文、用户偏好持久化
- **多账号轮询**: 自动切换备用账号应对限流

## 🚀 快速开始

### 环境要求

- Python 3.12+

### 安装依赖

```bash
cd doubao-gateway
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 配置

复制 `.env.example` 为 `.env`，填写豆包 Cookie 和设备信息：

```bash
cp .env.example .env
```

从浏览器开发者工具（F12 → Network）获取以下信息：

1. 访问豆包官网并登录
2. 刷新页面，找到 `/chat/completion` 请求
3. 复制完整 Cookie 和 query 参数

### 启动服务

**方式一：CLI（推荐）**

```bash
python doubao.py --reload
```

**方式二：uvicorn**

```bash
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8787 --reload
```

**方式三：PowerShell 脚本**

```bash
.\start.ps1
```

**CLI 参数**

```bash
python doubao.py --help
# usage: doubao.py [-h] [--host HOST] [--port PORT] [--reload] [--workers WORKERS]
# 
# Doubao Local Gateway
# 
# optional arguments:
#   -h, --help         show this help message and exit
#   --host HOST        监听地址 (default: 127.0.0.1)
#   --port PORT        监听端口 (default: 8787)
#   --reload           启用热重载
#   --workers WORKERS  工作进程数 (default: 1)
```

服务启动后会输出 IDE 配置信息：

```
============================================================
  Doubao Local Gateway 已启动
============================================================
  API 地址: http://127.0.0.1:8787/v1
  API Key:  YOUR_API_KEY
  模型名称: doubao-chat
------------------------------------------------------------
  IDE 配置步骤:
  1. 在 IDE 的 AI 模型配置中选择 '自定义配置'
  2. API 格式: OpenAI Chat Completions
  3. 自定义请求地址: http://127.0.0.1:8787/v1
  4. 模型 ID: doubao-chat
  5. API 密钥: YOUR_API_KEY
============================================================
```

### 使用 API

```bash
curl -X POST http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"doubao-chat","messages":[{"role":"user","content":"你好"}]}'
```

## 📡 API 端点

### `/v1/chat/completions`

标准 OpenAI 兼容接口。

### `/v1/agent`

专用 Agent 接口，支持多轮工具循环。

```bash
curl -X POST http://localhost:8787/v1/agent \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "goal": "帮我审查 app/main.py 的代码",
    "mode": "agent",
    "workspace": "/path/to/workspace",
    "max_rounds": 8
  }'
```

### `/v1/models`

获取可用模型列表。

```bash
curl -X GET http://localhost:8787/v1/models \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### `/health`

健康检查接口。

```bash
curl http://localhost:8787/health
```

## 🎯 Agent 模式

| 模式 | 参数值 | 说明 |
|---|---|---|
| Agent | `agent` | 默认模式，完整的 plan-act-observe 循环 |
| Plan | `plan` | 只输出计划，不执行代码 |
| Debug | `debug` | 专注错误追踪和根因分析 |

## 🛠️ 技能系统

根据用户意图自动激活相应技能：

| 技能 | 触发关键词 | 用途 |
|---|---|---|
| plan | 先计划、做个方案、不要改代码 | 任务规划 |
| code-review | 代码审查、审查、review | 代码审查 |
| security-review | 安全审查、安全漏洞、SQL注入 | 安全审计 |
| debug | 调试、报错、traceback | 错误排查 |
| research | 研究、调研、深入分析 | 深度研究 |
| simplify | 简化、优化代码、重构 | 代码优化 |
| verify | 验证、确认有效、回归 | 验证修复 |
| implement | 实现、开发、修复 | 功能开发 |

## ⚙️ 配置说明

```env
# 服务配置
API_KEY=YOUR_SECRET_KEY
HOST=127.0.0.1
PORT=8787
DEFAULT_MODEL=doubao-chat

# 豆包会话配置
DOUBAO_COOKIE=your_cookie_here
DOUBAO_DEVICE_ID=your_device_id
DOUBAO_WEB_ID=your_web_id
DOUBAO_TEA_UUID=your_tea_uuid
DOUBAO_FP=your_fingerprint

# Agent 配置
AGENT_MODE=auto
AGENT_MAX_ROUNDS=8
AGENT_WORKSPACE=/path/to/workspace
AGENT_ALLOW_SHELL=true
AGENT_SHELL_TIMEOUT=60

# 会话模式
SESSION_MODE=auto

# 多账号轮询（可选）
# DOUBAO_EXTRA_ACCOUNTS=[{"cookie":"...","device_id":"...","web_id":"...","tea_uuid":"...","fp":"..."}]
```

## 🏗️ 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    外部客户端 (IDE/Curl/SDK)                 │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI Gateway                          │
│  ┌─────────────────────────────────────────────────────┐    │
│  │  /v1/chat/completions (OpenAI 兼容)                 │    │
│  │  /v1/agent (专用 Agent)                             │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────┬───────────────────────────────────┘
                          │
         ┌────────────────┼────────────────┐
         ▼                ▼                ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ Agent       │   │ Skill       │   │ Memory      │
│ Runtime     │   │ Router      │   │ System      │
│             │   │             │   │             │
│ plan-act-   │   │ 意图识别    │   │ 项目上下文   │
│ observe     │   │ 技能加载    │   │ 用户偏好    │
└──────┬──────┘   └─────────────┘   └─────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│                    Local Tool Runtime                       │
│  read_file | write_file | replace_in_file | list_dir |      │
│  glob | grep | run_shell | finish                           │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                    Doubao Client                            │
│           (豆包网页 API 逆向封装)                            │
└─────────────────────────────────────────────────────────────┘
```

## 📁 项目结构

```
doubao-gateway/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 主入口
│   ├── config.py            # 配置管理
│   ├── doubao_client.py     # 豆包 API 客户端
│   ├── agent_runtime.py     # Agent 运行时
│   ├── agent_prompt.py      # Agent 提示管理
│   ├── skill_router.py      # 技能路由
│   ├── local_tools.py       # 本地工具运行时
│   ├── tool_protocol.py     # 工具协议定义
│   ├── tool_parallel.py     # 并行工具调用
│   ├── memory.py            # 记忆系统
│   └── context_manager.py   # 上下文窗口管理
├── prompts/
│   ├── coding-agent.md      # 主 Agent 提示
│   ├── SOURCES.md           # 提示来源说明
│   └── skills/
│       ├── implement.md     # 实现技能
│       ├── code-review.md   # 代码审查技能
│       ├── debug.md         # 调试技能
│       ├── plan.md          # 计划技能
│       ├── verify.md        # 验证技能
│       ├── security-review.md # 安全审查技能
│       ├── research.md      # 研究技能
│       └── simplify.md      # 简化技能
├── scripts/
│   └── fill_cookie_from_clipboard.ps1
├── doubao.py                # CLI 启动脚本
├── start.ps1                # PowerShell 启动脚本
├── requirements.txt         # 依赖列表
├── .env.example             # 环境变量模板
├── .gitignore               # Git 忽略配置
├── LICENSE                  # MIT 许可证
├── CONTRIBUTING.md          # 贡献指南
├── CODE_OF_CONDUCT.md       # 行为准则
└── README.md                # 项目说明
```

## 🧪 测试

```bash
# 健康检查
curl http://localhost:8787/health

# 测试 API
curl -X POST http://localhost:8787/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"doubao-chat","messages":[{"role":"user","content":"你好"}]}'
```

## 🛡️ 安全注意事项

1. **API Key 保密**: 不要将 API Key 提交到代码仓库
2. **工作区限制**: Agent 只能访问配置的工作区目录
3. **高危命令**: 默认禁止 `rm -rf`, `format`, `chmod` 等高危命令
4. **Shell 超时**: 命令执行有时间限制，防止长时间挂起

## 📝 许可证

MIT License

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 📧 联系方式

如有问题或建议，欢迎在 GitHub Issues 中反馈。