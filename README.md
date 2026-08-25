# Seed — Agent 内核

> **CodeAgent 的自托管 AI Agent 核心引擎**：主循环、LLM 路由、会话、记忆、工具运行时、定时任务、Webhook，开箱即用。

[![PyPI version](https://img.shields.io/pypi/v/seed-kernel.svg)](https://pypi.org/project/seed-kernel/)
[![PyPI downloads](https://img.shields.io/pypi/dm/seed-kernel.svg)](https://pypi.org/project/seed-kernel/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](pyproject.toml)

---

## 这是什么

**Seed** 是一个轻量、自托管的 Agent 内核（Python 包，PyPI 名为 `seed-kernel`）。它提供构建自主 Agent 所需的全部底层能力：

- 🧠 **主循环** — LLM 推理 + 工具调用的自主循环（`seed.core`）
- 🔌 **工具运行时** — `ToolRegistry` / `ToolExecutor` 契约，内置工具集在 `seed-toolbox`
- 💬 **会话与记忆** — 会话持久化、长期记忆沉淀
- ⏰ **定时任务** — 内置 cron 调度（线程安全 reload、标准 crontab 语义、启动失败可见化）
- 🔗 **集成层** — 浏览器、Webhook、MCP（Streamable HTTP 自动重连）、环境配置
- 🌍 **模型无关** — 通过 `seed-model-providers` 对接 DeepSeek / OpenAI / Anthropic / Ollama 等

**Seed 不依赖任何特定宿主产品；宿主（如 CodeAgent）只依赖 Seed**。架构与依赖方向见 [docs/PACKAGE_LAYOUT.md](docs/PACKAGE_LAYOUT.md)。

## 三件套

| PyPI 包 | 作用 | 说明 |
|---------|------|------|
| **`seed-kernel`** | Agent 内核 | 主循环 / LLM / 会话 / 记忆 / 工具运行时 / 集成层（本仓库） |
| **`seed-toolbox`** | 内置工具集 | `setup_builtin_tools()` 注册 45+ 内置工具 |
| **`seed-model-providers`** | 模型提供商目录 | DeepSeek / OpenAI / Anthropic / Ollama 等的 tokenizer 与路由 |

> ⚠️ 注意：PyPI 上的 **`seed`** 是另一个项目（Python 打包工具），**不是本包**。安装请使用 **`seed-kernel`**。

## 安装

### 📦 PyPI（推荐）

```bash
pip install seed-kernel        # 内核（主循环 / LLM / 会话 / 记忆 / 定时任务）

pip install seed-toolbox       # 可选：内置工具集（自动带上 seed-kernel）
```

### 🔧 源码安装

```bash
git clone https://github.com/tagword/seed.git
cd seed

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
```

## 快速上手

### 只用内核与集成层

```python
from seed.core.config_plane import project_root
from seed.integrations import BROWSER  # 未启动浏览器时也可能仅为占位对象

root = project_root()
```

### 使用内置工具（需已安装 `seed-toolbox`）

工具**契约**（`ToolRegistry`、`ToolExecutor`、`ToolExecutionError`）在 `seed.core.tool_runtime`；**具体 builtin** 与 **`setup_builtin_tools()`** 在 `seed_toolbox`：

```python
from seed.core.tool_runtime import ToolRegistry
from seed_toolbox import setup_builtin_tools

registry = ToolRegistry()
setup_builtin_tools(registry)
```

## CLI

安装后提供 `seed` 命令：

```bash
seed info    # 打印 seed.core / seed.integrations 版本；若已安装则显示 seed_toolbox
seed check   # 逐项导入检查
```

`seed check` 会校验 **`seed_toolbox`**：若未安装 `seed-toolbox`，最后一行会显示失败且**退出码为非 0**。这只表示可选组件缺失；若你只做不依赖内置工具的集成，可忽略或改用 `seed info` 查看核心包是否正常。

## 文档

| 文档 | 说明 |
|------|------|
| [docs/PACKAGE_LAYOUT.md](docs/PACKAGE_LAYOUT.md) | 包结构、模块边界与依赖方向 |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | 宿主集成路径与 `SEED_*` 环境变量 |
| [docs/ENV_REFERENCE.md](docs/ENV_REFERENCE.md) | `SEED_*` 环境变量完整手册 |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |

## 与 CodeAgent 的关系

[CodeAgent](https://github.com/tagword/codeagent)（`tagword-codeagent`）是基于 Seed 构建的**自主全栈开发 Agent**：

```
┌─────────────────────────────────────────────┐
│  CodeAgent                                   │
│  CLI · HTTP/WebSocket · Web UI · 认证 · Agent 层 │
├─────────────────────────────────────────────┤
│  Seed（本仓库） — 内核                        │
│  seed-toolbox    — 内置工具                  │
│  seed-model-providers — 模型提供商           │
└─────────────────────────────────────────────┘
```

使用 CodeAgent 时无需直接安装 Seed——`pip install tagword-codeagent` 会自动拉取全部依赖。

## 常见问题

**只装了 `seed-kernel`，`from seed import setup_builtin_tools` 报错？**

顶层 `setup_builtin_tools` 在**未安装 `seed-toolbox`** 时会占位并在调用时抛出 **`RuntimeError`**，提示安装 `seed-toolbox`。类型与执行器契约可直接使用 `seed.core.tool_runtime`。

**定时任务、仓库本地 env 文件从哪里导入？**

二者在 **`seed.integrations`**：`seed.integrations.cron_sched`、`seed.integrations.env_config`（读取 `config/seed.env`），不在 `seed.core`。

## 许可证

MIT License（见 [opensource.org/licenses/MIT](https://opensource.org/licenses/MIT)）© 2025-2026 tagword
