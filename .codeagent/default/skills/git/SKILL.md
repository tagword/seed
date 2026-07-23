---
name: git
description: >
  项目级 Git 工作流：monorepo 多包场景的 commit/scope/tag/push/changelog 规范。
  覆盖预检、提交信息、scoping、staging、tagging、推送全流程。
agent: codeagent
version: 1.0.0
tags: [git, commit, version-control]
trigger: "用户要求提交代码、打 tag、推送、查看变更时"
---

# Git 工作流（项目级 — Monorepo 多包场景）

## 适用项目

单仓库包含多个独立 Python 包（如 `seed` + `seed-tools` + `seed-invest` 等），每个包独立版本号、独立发布周期。

---

## 一、提交前预检

每次 `git commit` 之前强制检查：

```bash
# 1. 查看变更范围
git status --short

# 2. 确认改了哪些包（适用于 monorepo）
# 查看子目录的变更
git diff --name-only | cut -d/ -f1 | sort -u
```

**预检判定**：

| 情况 | 处理方式 |
|------|---------|
| 只改了 1 个包 | `scope` 用包名 |
| 改了多个包（关联变更） | 用一个 commit 提交，scope 用项目名 |
| 改了多个包（不相关） | **拆成多个 commit**，每个包一个 |
| 有文档/配置变更 | 可以并入相关包 commit，或单独 `docs:` / `chore:` |

---

## 二、提交信息规范

### 格式

```
<type>(<scope>): <中文描述>

<可选：为什么这么改，不是改了什么>
```

### type（前缀）

| 前缀 | 场景 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修 Bug |
| `refactor` | 重构（不改功能） |
| `chore` | 杂务（依赖、构建、配置） |
| `docs` | 文档 |
| `style` | 代码格式 |
| `test` | 测试 |
| `release` | 发版/版本号更新/CHANGELOG |
| `revert` | 回滚 |

### scope（范围）

Monorepo 项目 scope 用**包名或模块名**：

| scope 值 | 适用场景 |
|----------|---------|
| `seed` | 仅改 `seed/` 目录下的代码 |
| `seed-tools` | 仅改 `seed-tools/` 或 `seed_tools/` |
| `seed-invest` | 仅改投资模块 |
| `docs` | 只改了文档 |
| `dep` | 只改了依赖/配置 |
| *跨包* | 用 `所有涉及的包名` 或不用 scope |

### 示例

```
feat(seed): 添加大模型调用熔断机制
fix(seed-tools): 修复 web_fetch 超时截断阈值
refactor(seed): 拆分 llm_worker.py 为大模块
chore(dep): 升级 httpx 到 0.28
docs: 更新 API 文档和架构说明
release(seed-kernel): v1.0.13
```

---

## 三、Staging 策略

### 单包变更（推荐）
```bash
git add <包目录>/  pyproject.toml  # 视情况加入配置文件
git commit -m "feat(<scope>): ..."
```

### 多包不相关变更（必须拆分）
```bash
# 先 staging 包 A
git add <包A>/
git commit -m "feat(<包A>): ..."

# 再 staging 包 B
git add <包B>/
git commit -m "fix(<包B>): ..."
```

### 批量 staging 后再拆分
```bash
# 用 git add -p 交互式分段暂存
git add -p
```

---

## 四、Tag 管理

### Tag 命名规范

| 场景 | Tag 格式 |
|------|---------|
| 某包发版 | `vX.Y.Z`（如 `v1.0.13`） |
| 多包同时发版 | 各包独立 tag，如 `seed-v1.0.13` + `seed-tools-v1.0.3` |

### Tag 操作流程

```bash
# 打 tag（必须在 commit 之后）
git tag v1.0.13

# 查看已有 tag
git tag -l "v*"

# 删除本地 tag（打错了时）
git tag -d v1.0.13

# 删除远端 tag（ghost tag 修复）
git push origin :refs/tags/v1.0.13
```

### Tag 安全原则
- **先 fetch 再打 tag**：`git fetch --tags` 检查远端已存在
- **commit 之后、push 之前打 tag**：确保 tag 指向对的那个 commit
- **不要漏 push tag**：`git push origin main --tags`

---

## 五、Push 流程

```bash
# 常规推送
git push origin <分支名>

# 带 tag 推送
git push origin <分支名> --tags

# 或只推特定 tag（推荐，避免推出去无关 tag）
git push origin v1.0.13
```

### Push 前检查清单
- [ ] 本地 commit 信息格式正确
- [ ] scope 与改动范围一致
- [ ] CHANGELOG.md 有同步更新（如果有用户可见变更）
- [ ] tag 没有碰撞
- [ ] 工作区干净（无未 staging 的改动）

---

## 六、Monorepo 特有场景

### 场景 A：改了一个包 + 改了根目录配置文件

```bash
git add pyproject.toml  <包目录>/   # 将 pyproject.toml 归入该包的 commit
# 或单独 chore(dep): 更新依赖
```

### 场景 B：改了多个包但变更关联（如改 API 两边都要改）

```bash
git add -A
git commit -m "refactor: 重构消息队列接口兼顾 seed 和 seed-tools"
```

### 场景 C：发版

走 `pypi-release` skill 全流程。Git 部分直接：
```bash
git add pyproject.toml CHANGELOG.md
git commit -m "release(<包名>): vX.Y.Z"
git tag vX.Y.Z
git push origin main --tags
```

---

## 七、Commitizen / 自动化（可选）

项目可配置 commitizen（cz） 或 husky 做自动校验，但手写提交信息时以本规范为准。
