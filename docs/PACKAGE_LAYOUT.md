# Seed 仓库布局（双发行包）

Monorepo 内有两个 **可独立安装** 的 Python 发行根：

| 目录 | PyPI 名 | Python 包 |
|------|---------|-----------|
| [`seed/`](../)（本目录上级） | `seed` | `seed` → `seed.core`, `seed.integrations` |
| [`seed-tools/`](../../seed-tools)（仓库并列） | `seed-tools` | `seed_tools` |

开发时常用：`pip install -e ./seed` 再 `pip install -e ./seed-tools`（后者依赖前者）。

## 依赖方向（保持单向）

```
seed_tools      → seed.core（及按需 seed.integrations）
seed.integrations → seed.core
seed.core       → 标准库 + httpx + requests + ddgs（以 seed/pyproject.toml 为准）
```

**禁止** `seed.core` / `seed.integrations` 在 import 时依赖 `seed_tools`。Builtin 工具的 **Registry / Executor 契约**在 `seed.core.tool_runtime`；具体 handler 在 `seed_tools`。

## `seed` 包内子包

| 子包 | 路径 | 职责 |
|------|------|------|
| **core** | `seed/seed/core/` | 主循环、LLM、会话与记忆、`config_plane`、`paths`、`proj_reg`、`proj_todos`、`execution`、`agent_runtime`、`sess_store`、`chat_events`、`tool_runtime` 等 |
| **integrations** | `seed/seed/integrations/` | 浏览器、safety 桥接、webhook、transcript、`message_api`、`session_title`、**cron**（`cron_sched`）、**`env_config`** |

## `seed-tools` 包

| 模块前缀 | 职责 |
|----------|------|
| `seed_tools.*` | 内置 Tool 实现、`setup_builtin_tools()`；使用 `seed.core.models.Tool` 与 `seed.core.tool_runtime` |

## CLI

- `seed` 入口：`seed.cli:main`（随 `seed` 包安装）
- `seed info` / `seed check`：校验 `seed.core`、`seed.integrations`；若已安装则校验 `seed_tools`

## 上层产品（如 CodeAgent）

同时依赖 **`seed`** 与 **`seed-tools`**（或把二者源码根加入 `PYTHONPATH`）。库代码推荐显式导入：`from seed.core…`、`from seed.integrations…`、`from seed_tools…`。
