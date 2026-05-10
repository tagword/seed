# Seed：安装与快速开始

**`seed`** 是 Agent 内核 Python 包，内含 **`seed.core`**（主循环、LLM、会话、记忆、工具运行时）与 **`seed.integrations`**（浏览器、安全、定时任务、Webhook 等）。内置 LLM 可调工具在 **`seed-tools`** 发行版（导入前缀 **`seed_tools`**）。

架构与依赖方向见 [docs/PACKAGE_LAYOUT.md](docs/PACKAGE_LAYOUT.md)（**Seed 不依赖特定的宿主产品包；宿主只依赖 Seed**）；模块级公开 API 见仓库根的 [PUBLIC_API.md](../PUBLIC_API.md)。

宿主集成路径与 **`SEED_*` 环境变量**统一说明见 [docs/INTEGRATION.md](docs/INTEGRATION.md) 与 [docs/ENV_REFERENCE.md](docs/ENV_REFERENCE.md)。

---

## 安装

### PyPI（发布后）

```bash
pip install seed
```

若需要内置工具注册与实现，再安装：

```bash
pip install seed-tools
```

`seed-tools` 已声明依赖 `seed`，单独安装 `seed-tools` 时也会装上 `seed`。

### Monorepo 源码（可编辑安装）

在仓库根目录（与 `seed/`、`seed-tools/` 并列）执行：

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ./seed
pip install -e ./seed-tools
```

先装 `seed` 再装 `seed-tools` 即可。

### 打成 wheel（固定版本供下游安装）

在 monorepo 根目录执行：

```bash
./scripts/build_seed_wheels.sh
```

产物默认在 `dist/`，其中包含 `seed-*.whl`、`seed_tools-*.whl` 以及 `seed` 的传递依赖 wheel（便于离线或固定环境）。仅安装这两个包时：

```bash
pip install dist/seed-*.whl dist/seed_tools-*.whl
```

（若索引里没有传递依赖，请在同一目录用 `pip install dist/*.whl` 或保留 `build_seed_wheels.sh` 生成的完整 `dist/`。）

---

## 最小示例

### 只用内核与集成层

```python
from seed.core.config_plane import project_root
from seed.integrations import BROWSER  # 未启动浏览器时也可能仅为占位对象

root = project_root()
```

### 使用内置工具（需已安装 `seed-tools`）

工具**契约**（`ToolRegistry`、`ToolExecutor`、`ToolExecutionError`）在 `seed.core.tool_runtime`；**具体 builtin** 与 **`setup_builtin_tools()`** 在 `seed_tools`：

```python
from seed.core.tool_runtime import ToolRegistry
from seed_tools import setup_builtin_tools

registry = ToolRegistry()
setup_builtin_tools(registry)
```

---

## CLI

安装 `seed` 后：

```bash
seed info    # 打印 seed.core / seed.integrations 版本；若已安装则显示 seed_tools
seed check   # 逐项导入检查
```

`seed check` 会校验 **`seed_tools`**：若未安装 `seed-tools`，最后一行会显示失败且**退出码为非 0**。这只表示可选组件缺失；若你只做不依赖 builtin 工具的集成，可忽略或改用 `seed info` 查看核心包是否正常。

---

## 仅用源码路径（PYTHONPATH）

不落 editable 安装时，需把 **`seed`** 与 **`seed-tools`** 的安装根（各自包含 `seed/`、`seed_tools/` 包目录的那一个目录）加入 `PYTHONPATH`。

---

## 常见问题

**只装了 `seed`，`from seed import setup_builtin_tools` 报错？**

顶层 `setup_builtin_tools` 在**未安装 `seed-tools`** 时会占位并在调用时抛出 **`RuntimeError`**，提示安装 `seed-tools`。类型与执行器契约可直接使用 `seed.core.tool_runtime`。

**定时任务、仓库本地 env 文件从哪里导入？**

二者在 **`seed.integrations`**：`seed.integrations.cron_sched`、`seed.integrations.env_config`（读取 `config/seed.env`），不在 `seed.core`。

**更多信息**

- [docs/PACKAGE_LAYOUT.md](docs/PACKAGE_LAYOUT.md)
- [PUBLIC_API.md](../PUBLIC_API.md)
