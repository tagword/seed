# Contributing

## 开发环境

```bash
# 克隆
git clone https://github.com/tagword/seed
cd seed

# 虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 可编辑安装
pip install -e ".[dev]"
```

### 配套包

seed 依赖 `seed-tools` 和 `seed-model-providers`（均为同一 GitHub org 下的独立仓库）。本地开发时推荐在 monorepo 布局下工作：

```
parent/
├── seed/                    # 本包
├── seed-tools/              # 工具包（git clone github.com/tagword/seed-tools）
└── seed-model-providers/    # 模型提供商目录（git clone github.com/tagword/seed-model-providers）
```

```bash
pip install -e ../seed-tools
pip install -e ../seed-model-providers
```

## 测试

```bash
# 运行全部测试
pytest

# 指定测试文件
pytest tests/test_config_plane.py -v
```

## 项目结构

```
seed/
├── seed/
│   ├── core/              # 内核：LLM 执行、工具运行时、会话管理、记忆
│   ├── integrations/      # 集成层：浏览器、定时任务、MCP、Webhook
│   ├── cli.py             # CLI 入口
│   └── models.py          # 公开类型定义
├── docs/                  # 设计文档与参考
├── tests/                 # 测试
└── pyproject.toml
```

## PR 规范

- 分支命名：`feat/xxx`、`fix/xxx`、`refactor/xxx`
- Commit message 遵循 Conventional Commits
- 提交前确保 `pytest` 全部通过
- 新增功能需附带测试覆盖

## 发布流程

```bash
# 1. 更新 pyproject.toml 版本号
# 2. 更新 CHANGELOG.md
# 3. 提交并打 tag
git commit -m "chore: bump version to v1.0.x"
git tag v1.0.x
git push origin v1.0.x
# 4. 构建并发布 PyPI
python -m build
twine upload dist/*
```
