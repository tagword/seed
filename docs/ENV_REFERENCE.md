# 环境变量参考（Seed canonical）

规范前缀为 **`SEED_*`**（通用 Agent 内核，不绑定 Code Agent 产品）。

**Code Agent 产品**使用 **`CODEAGENT_*`**，见 [`codeagent/docs/ENV_REFERENCE.md`](../../codeagent/docs/ENV_REFERENCE.md)。

**`seed.core.env_access` 仅识别 `SEED_*`**（Phase 6.7）。Code Agent 在调用 Seed 前通过 `codeagent.core.seed_bridge` 将 `CODEAGENT_<后缀>` 同步为 `SEED_<后缀>`（仅当后者未设置）。

下列表格与 `env_access` 中的元组常量一致；实现细节以源码为准。

## 路径与身份

| Seed canonical | 别名（仍识别） | 含义（摘要） |
|----------------|----------------|--------------|
| `SEED_PROJECT_ROOT` | `CODEAGENT_PROJECT_ROOT` | 数据与配置根目录；`project_root()` |
| `SEED_AGENT_ID` | `CODEAGENT_AGENT_ID` | 默认 logical agent id |
| `SEED_SESSION_DIR` | `CODEAGENT_SESSION_DIR` | 会话相关目录覆盖（若模块使用） |

## LLM 上行与占位

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_LLM_API_KEY` | `CODEAGENT_LLM_API_KEY` | API 密钥 |
| `SEED_LLM_AUTH_SCHEME` | `CODEAGENT_LLM_AUTH_SCHEME` | Authorization scheme |
| `SEED_LLM_BASEURL` | `CODEAGENT_LLM_BASEURL` | OpenAI 兼容网关 Base URL |
| `SEED_LLM_MODEL` | `CODEAGENT_LLM_MODEL` | 默认模型 id |
| `SEED_LLM_MAX_TOKENS` | `CODEAGENT_LLM_MAX_TOKENS` | max_tokens 等 |
| `SEED_LLM_NO_TOPK` | `CODEAGENT_LLM_NO_TOPK` | 禁用 top-k 类参数 |
| `SEED_LLM_ENABLE_THINKING` | `CODEAGENT_LLM_ENABLE_THINKING` | thinking 开关；按 provider 写入不同 extra_body（DeepSeek / DashScope / Kimi / 智谱等，见 [PROVIDER_PROTOCOLS.md](../../seed-model-providers/docs/PROVIDER_PROTOCOLS.md)） |
| `SEED_LLM_REASONING_EFFORT` | `CODEAGENT_LLM_REASONING_EFFORT` | **主要 DeepSeek**：思考强度 `high` / `max`；Moonshot/智谱 协议会忽略顶层 `reasoning_effort` |
| `SEED_LLM_SEPARATE_REASONING` | `CODEAGENT_LLM_SEPARATE_REASONING` | separate_reasoning |
| `SEED_LLM_CHAT_TEMPLATE_KWARGS` | `CODEAGENT_LLM_CHAT_TEMPLATE_KWARGS` | chat_template_kwargs |
| `SEED_LLM_EXTRA_BODY` | `CODEAGENT_LLM_EXTRA_BODY` | 额外 JSON body |
| `SEED_LLM_CONTEXT_SIZE` | `CODEAGENT_LLM_CONTEXT_SIZE` | 上下文预算提示 |
| `SEED_LLM_INPUT_TOKEN_EST_DIVISOR` | `CODEAGENT_LLM_INPUT_TOKEN_EST_DIVISOR` | 输入 token 估算除数 |
| `SEED_LLM_CONTEXT_MARGIN` | `CODEAGENT_LLM_CONTEXT_MARGIN` | 上下文余量 |
| `SEED_LLM_SEND_REASONING_CONTENT` | `CODEAGENT_LLM_SEND_REASONING_CONTENT` | 是否转发 reasoning |
| `SEED_LLM_HTTP_REFERER` | `CODEAGENT_LLM_HTTP_REFERER` | OpenRouter 等 `HTTP-Referer`（见 [`seed-model-providers/docs/PROVIDER_PROTOCOLS.md`](../../seed-model-providers/docs/PROVIDER_PROTOCOLS.md)） |
| `SEED_LLM_APP_TITLE` | `CODEAGENT_LLM_APP_TITLE` | OpenRouter `X-OpenRouter-Title` / `X-Title` |
| `SEED_LLM_MAX_REQUEST_BODY_BYTES` | `CODEAGENT_LLM_MAX_REQUEST_BODY_BYTES` | 请求体上限 |
| `SEED_ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE` | `CODEAGENT_ASSISTANT_TOOLCALL_PLACEHOLDER_DISABLE` | 关闭工具轮占位正文 |
| `SEED_ASSISTANT_TOOLCALL_PLACEHOLDER` | `CODEAGENT_ASSISTANT_TOOLCALL_PLACEHOLDER` | 自定义占位文案 |

## 工具消息与解析

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_TOOL_OUTPUT_MAX_CHARS` | `CODEAGENT_TOOL_OUTPUT_MAX_CHARS` | 单条工具输出截断长度 |
| `SEED_INLINE_TOOL_PARSE` | `CODEAGENT_INLINE_TOOL_PARSE` | 内联伪工具 XML 解析 |

## System 与上下文压缩

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_SYSTEM_PROMPT` | `CODEAGENT_SYSTEM_PROMPT` | 显式覆盖默认 system |
| `SEED_PERSONA_MEMORY_MAX_CHARS` | `CODEAGENT_PERSONA_MEMORY_MAX_CHARS` | persona 注入上限 |
| `SEED_CONTEXT_COMPACT` | `CODEAGENT_CONTEXT_COMPACT` | 启用上下文摘要压缩 |
| `SEED_CONTEXT_COMPACT_SUMMARIZER_BASEURL` | `CODEAGENT_CONTEXT_COMPACT_SUMMARIZER_BASEURL` | 摘要专用网关 |
| `SEED_CONTEXT_COMPACT_SUMMARIZER_MODEL` | `CODEAGENT_CONTEXT_COMPACT_SUMMARIZER_MODEL` | 摘要专用模型 |
| `SEED_CONTEXT_COMPACT_MIN_TOKENS` | `CODEAGENT_CONTEXT_COMPACT_MIN_TOKENS` | 触发压缩的上下文 token 阈值 |
| `SEED_CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS` | `CODEAGENT_CONTEXT_COMPACT_SUMMARIZER_MAX_TOKENS` | 摘要模型 max_tokens |
| `SEED_CONTEXT_COMPACT_MIN_ROUNDS` | `CODEAGENT_CONTEXT_COMPACT_MIN_ROUNDS` | 用户轮次触发阈值 |
| `SEED_CONTEXT_COMPACT_KEEP_USER_ROUNDS` | `CODEAGENT_CONTEXT_COMPACT_KEEP_USER_ROUNDS` | 保留最近完整用户轮数 |
| `SEED_CONTEXT_SUMMARIZER_MAX_INPUT` | `CODEAGENT_CONTEXT_SUMMARIZER_MAX_INPUT` | 摘要输入字符上限 |
| `SEED_CONTEXT_COMPACT_WARN_RATIO` | `CODEAGENT_CONTEXT_COMPACT_WARN_RATIO` | 压缩告警比例 |

## Safety

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_SAFETY_BASH_BLOCKED` | `CODEAGENT_SAFETY_BASH_BLOCKED` | 额外 bash 拦截模式 |
| `SEED_SAFETY_BASH_ALLOWED_DIRS` | `CODEAGENT_SAFETY_BASH_ALLOWED_DIRS` | bash 允许 cwd |
| `SEED_SAFETY_BASH_TIMEOUT_MAX` | `CODEAGENT_SAFETY_BASH_TIMEOUT_MAX` | bash 超时硬上限 |
| `SEED_SAFETY_AUDIT_LOG` | `CODEAGENT_SAFETY_AUDIT_LOG` | 安全事件审计日志 |
| `SEED_LLM_PROJECTION_AUDIT` | `CODEAGENT_LLM_PROJECTION_AUDIT` | 每轮 LLM 请求全文快照（`sessions/_audit/<session>/`） |
| `SEED_LLM_PROJECTION_AUDIT_DIR` | `CODEAGENT_LLM_PROJECTION_AUDIT_DIR` | 可选覆盖审计目录根 |
| `SEED_SAFETY_REDACT_SECRETS` | `CODEAGENT_SAFETY_REDACT_SECRETS` | 密钥脱敏 |
| `SEED_SAFETY_REDACT_PII` | `CODEAGENT_SAFETY_REDACT_PII` | PII 脱敏 |

## Cron 与记忆

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_CRON` | `CODEAGENT_CRON` | 关闭调度 |
| `SEED_CRON_TZ` | `CODEAGENT_CRON_TZ` | 默认 Cron 时区 |
| `SEED_CHAT_USER_ROUNDS` | `CODEAGENT_CHAT_USER_ROUNDS` | API 投影保留用户轮数 |
| `SEED_MEMORY_LOG` | `CODEAGENT_MEMORY_LOG` | episodic 写入开关 |
| `SEED_CRON_EXPERIENCE_SKIP_DUPLICATE` | `CODEAGENT_CRON_EXPERIENCE_SKIP_DUPLICATE` | cron 经验去重 |
| `SEED_CRON_EXPERIENCE_TTL_SECONDS` | `CODEAGENT_CRON_EXPERIENCE_TTL_SECONDS` | cron 经验 TTL |
| `SEED_MEMORY_INJECT` | `CODEAGENT_MEMORY_INJECT` | episodic 注入 system（进模型前贴到投影 system，非每轮扫盘） |
| `SEED_MEMORY_INJECT_MAX_CHARS` | `CODEAGENT_MEMORY_INJECT_MAX_CHARS` | 注入块最大字符 |
| `SEED_MEMORY_INJECT_SESSION_ONLY` | `CODEAGENT_MEMORY_INJECT_SESSION_ONLY` | 仅本会话经验 |

Episodic 快照写入 Session `metadata`（`episodic_block` 等）：**新会话首轮**扫一次（agent 或项目 `memory/experiences`）；之后在 **context compact 成功** 时刷新；其余轮次只贴快照。绑定项目切换且已有快照时会再刷新。epoch 内新写的 experience 要到下次 compact 才进入模型。
## Webhook

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_WEBHOOK_DEDUP` | `CODEAGENT_WEBHOOK_DEDUP` | 是否启用去重 |
| `SEED_WEBHOOK_DEDUP_TTL_SEC` | `CODEAGENT_WEBHOOK_DEDUP_TTL_SEC` | 去重 TTL（秒） |
| `SEED_WEBHOOK_DEDUP_MAX_KEYS` | `CODEAGENT_WEBHOOK_DEDUP_MAX_KEYS` | 去重表最大条目 |

## 会话标题

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_SESSION_TITLE_MAX_CHARS` | `CODEAGENT_SESSION_TITLE_MAX_CHARS` | 标题最大字符 |
| `SEED_SESSION_TITLE_MAX_TOKENS` | `CODEAGENT_SESSION_TITLE_MAX_TOKENS` | 标题 LLM max_tokens |
| `SEED_SESSION_TITLE_LLM` | `CODEAGENT_SESSION_TITLE_LLM` | 是否用 LLM 生成标题 |
| `SEED_SESSION_TITLE_MODE` | `CODEAGENT_SESSION_TITLE_MODE` | `first` / `every` |

## 浏览器集成

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_BROWSER_UNHEALTHY_THRESHOLD` | `CODEAGENT_BROWSER_UNHEALTHY_THRESHOLD` | 目标不健康阈值 |
| `SEED_BROWSER_CDP_UNHEALTHY_THRESHOLD` | `CODEAGENT_BROWSER_CDP_UNHEALTHY_THRESHOLD` | CDP 不健康阈值（次选） |
| `SEED_BROWSER_ALLOW_REMOTE_DEBUG` | `CODEAGENT_BROWSER_ALLOW_REMOTE_DEBUG` | 允许非本机调试端口 |
| `SEED_BROWSER_ALLOW_PRIVATE_URLS` | `CODEAGENT_BROWSER_ALLOW_PRIVATE_URLS` | 导航忽略 SSRF 私有地址拦截 |

## Phase 2–5 集成（exec / MCP / LSP / hooks）

| Seed canonical | 别名 | 含义（摘要） |
|----------------|------|--------------|
| `SEED_EXEC_BACKEND` | `CODEAGENT_EXEC_BACKEND` | `local` / `docker` / `auto` |
| `SEED_EXEC_DOCKER_IMAGE` | `CODEAGENT_EXEC_DOCKER_IMAGE` | Docker 镜像 |
| `SEED_EXEC_DOCKER_WORKDIR` | `CODEAGENT_EXEC_DOCKER_WORKDIR` | 容器工作目录 |
| `SEED_EXEC_DOCKER_NETWORK` | `CODEAGENT_EXEC_DOCKER_NETWORK` | Docker network |
| `SEED_MCP_ENABLED` | `CODEAGENT_MCP_ENABLED` | MCP 工具总开关 |
| `SEED_MCP_CALL_TIMEOUT` | `CODEAGENT_MCP_CALL_TIMEOUT` | `mcp_call` 超时（秒） |
| `SEED_MCP_REGISTER_TOOLS` | `CODEAGENT_MCP_REGISTER_TOOLS` | 动态注册 MCP 工具 |
| `SEED_LSP_ENABLED` | `CODEAGENT_LSP_ENABLED` | LSP 工具开关 |
| `SEED_HOOKS_ENABLED` | `CODEAGENT_HOOKS_ENABLED` | Hooks 开关 |
| `SEED_ORCHESTRATOR_AUTO_SPLIT` | `CODEAGENT_ORCHESTRATOR_AUTO_SPLIT` | Orchestrator 自动拆任务 |

## 说明

- **未列入上表**、仍以 `CODEAGENT_*` 或 `SEED_*` 出现在 `config/seed.env.example` 中的变量，可能仅由上层应用或尚未迁入 `env_access` 的模块读取；以各自模块文档为准。
- 读取辅助函数：`pick_nonempty`、`pick_default`、`env_truthy` 等定义在 **`seed.core.env_access`**。
