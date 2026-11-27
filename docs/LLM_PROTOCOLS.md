# LLM 协议支持矩阵

> seed 支持的对话协议。每个协议对应 `seed_model_providers` 中的一个
> `chat_protocol` id，由 `resolve_chat_protocol(provider=, base_url=)` 解析。

## 一览

| 协议 id | 端点 | 说明 |
|---------|------|------|
| `openai` | `{base}/chat/completions` | OpenAI Chat Completions（默认协议） |
| `openai_responses` | `{base}/responses` | **OpenAI Responses API**（item 化输入输出） |
| `deepseek` | `{base}/chat/completions` | DeepSeek V4（`extra_body.thinking`） |
| `dashscope` | `{base}/chat/completions` | 阿里云百炼（`enable_thinking` + `preserve_thinking`） |
| `moonshot` | `{base}/chat/completions` | Kimi（`thinking` + `reasoning_content`） |
| `zhipu` | `{base}/chat/completions` | 智谱 GLM（`thinking.type`） |
| `minimax` | `{base}/chat/completions` | MiniMax（`thinking.type` + `reasoning_split`） |
| `minimax_anthropic` | `{base}/v1/messages` | MiniMax Anthropic Messages 兼容层（主动缓存） |
| `sglang` | `{base}/chat/completions` | 自建 SGLang（`separate_reasoning`） |

## 内部统一表示

seed 内部**一律使用 OpenAI Chat Completions 消息形状**：

```python
{"role": "assistant", "content": "...", "tool_calls": [...], "tool_call_id": "..."}
```

非 OpenAI 协议在**协议边界**做双向转换（与 `minimax_anthropic` 同一模式）：

```
内部 OpenAI 形状  ──_to_<protocol>_request──▶  协议请求体
协议响应          ──_parse_<protocol>_response──▶  内部 OpenAI 形状
```

这样做的好处：工具循环、会话历史、reasoning 提取、usage 统计、Web UI 全部
协议无关，新增协议不会引发全链路改动。

---

## OpenAI Responses 协议（`openai_responses`）

### 端点

`POST {base_url}/responses`（base_url 形如 `https://api.openai.com/v1`）

### 请求差异

| Chat Completions | Responses |
|---|---|
| `messages` | `input`（item 数组） |
| `system` 消息 | `instructions` |
| `max_tokens` / `max_completion_tokens` | `max_output_tokens` |
| `tools`（嵌套 `function`） | `tools`（扁平 `name`/`parameters`） |
| `reasoning_effort`（顶层） | `reasoning: {effort, summary}` |
| `stream_options` | **不支持**（usage 由 `response.completed` 事件携带） |

### 映射表

**请求方向（内部 → 协议）**

| 内部 Chat 形状 | Responses `input[]` |
|---|---|
| `{role:"system",content}` | 提取到 `instructions` |
| `{role:"user",content:"t"}` | `{type:"message",role:"user",content:[{type:"input_text",text:"t"}]}` |
| `{role:"assistant",content:"t"}` | `{type:"message",role:"assistant",content:[{type:"output_text",text:"t"}]}` |
| `{type:"image_url",image_url:{url}}` | `{type:"input_image",image_url:"..."}` |
| assistant `tool_calls[]` | `{type:"function_call",call_id,name,arguments}` |
| `{role:"tool",tool_call_id,content}` | `{type:"function_call_output",call_id,output}` |
| assistant `reasoning_content` | `{type:"reasoning",summary:[{type:"summary_text",text}]}` |

**响应方向（协议 → 内部）**

| Responses `output[]` | 内部 |
|---|---|
| `{type:"message",content:[{type:"output_text",text}]}` | `content` |
| `{type:"reasoning",summary:[...]}` | `reasoning_content` |
| `{type:"function_call",call_id,name,arguments}` | `tool_calls[].id=call_id` |
| `usage.input_tokens/output_tokens/total_tokens` | `prompt_tokens`/`completion_tokens`/`total_tokens` |

### 流式事件

| Responses 事件 | 内部事件 |
|---|---|
| `response.output_text.delta` | `{"type":"delta","text"}` |
| `response.reasoning_summary_text.delta` | `{"type":"reasoning_delta","text"}` |
| `response.reasoning_text.delta` | `{"type":"reasoning_delta","text"}` |
| `response.output_item.added`（function_call） | 建立 tool_call 槽位 |
| `response.function_call_arguments.delta` | 累积参数 |
| `response.completed` | `{"type":"done",...}`（usage 在此） |
| `response.failed` / `error` | 抛 `LLMError` |

### 推理档位

`reasoning.effort` 合法值（与官方 SDK 一致）：

```
none | minimal | low | medium | high | xhigh
```

前端「推理强度」下拉新增 `关闭(none)` / `极简(minimal)` 两档。

### 隐私与状态

- **无状态全量回放**：每轮把完整历史作为 `input[]` 重发，不使用
  `previous_response_id`（与 seed 的会话重放/分支语义冲突）
- **`store: false`**：服务端默认会留存响应内容，本项目显式关闭
- **reasoning 回放**：无状态模式下回放 reasoning item 的 summary 文本。
  OpenAI 要求「紧邻 function_call 之前的 reasoning item」必须存在，否则拒绝请求

### 配置

```bash
SEED_LLM_BASEURL=https://api.openai.com/v1
SEED_LLM_MODEL=gpt-5.1
SEED_LLM_API_KEY=sk-...
SEED_LLM_REASONING_EFFORT=medium        # 可选
SEED_LLM_REASONING_SUMMARY=auto          # 可选
```

或在 Web UI「环境与 LLM」中新增预设，provider 选 **OpenAI (Responses 协议)**。

### 代码位置

| 层 | 文件 | 内容 |
|---|---|---|
| provider | `seed-model-providers/seed_model_providers/model_providers.py` | catalog、`resolve_chat_protocol`、`apply_chat_thinking_extra_body`、`apply_chat_stream_options`、`normalize_responses_reasoning_effort` |
| 协议 | `seed/seed/core/llm_responses.py` | `build_responses_input`、`to_responses_request`、`parse_responses_response`、`iter_responses_stream` |
| 接线 | `seed/seed/core/llm_exec.py` | `_get_completion_url`、`_stream_openai_responses`、`generate()` 分支 |

### 测试

```bash
# seed-model-providers
pytest tests/test_responses_protocol.py
# seed
pytest tests/test_llm_responses_protocol.py tests/test_llm_responses_e2e.py
# codeagent
pytest tests/test_webui_responses_provider.py
```
