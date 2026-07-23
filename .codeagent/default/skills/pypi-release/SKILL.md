---
name: pypi-release
description: >
  完整 PyPI 发布工作流：预检 → 构建 → 发布 → 提交 → 验证 → 回滚。
  适用于 monorepo 多包场景（每个包独立版本、独立发布）。
agent: codeagent
version: 1.0.0
tags: [release, pypi, deploy]
trigger: "用户要求发版、发布 PyPI、打 tag 时"
---

# PyPI 发布工作流（项目级）

## 适用场景

每个包独立版本号的 monorepo 项目。以 seed-kernel（`seed/`）为例：每个轮次都需要预检、构建、上传、git 标签、验证。

## 流程总览

```
预检阶段 → 准备阶段 → 构建阶段 → 发布阶段 → 提交阶段 → 验证阶段 → ✅ 完成
（失败则回滚）
```

---

## 预检阶段（Preflight）

**必须全部通过，缺一不可。**

### 1. 版本确认
```bash
# 确认 pyproject.toml 中的版本号
grep '^version' pyproject.toml

# 确认 CHANGELOG.md 中该版本条目存在
grep -A3 "v$(grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')" CHANGELOG.md

# 该版本尚未在 PyPI 发布过
pip index versions seed-kernel 2>/dev/null | head -5
```

**检查项清单**：
- [ ] pyproject.toml 版本号已更新（不是旧版）
- [ ] CHANGELOG.md 有该版本的条目（日期、变更内容）
- [ ] CHANGELOG.md 的版本号与 pyproject.toml 一致
- [ ] 该版本号尚未在 PyPI 发布（`pip index versions` 未列出）

### 2. Tag 碰撞检查
```bash
git fetch --tags
git tag -l "v$(grep '^version' pyproject.toml | sed 's/version = "\(.*\)"/\1/')"
```
- 如果返回已有 tag → **先删除远端幽灵 tag**（`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`）后再继续
- 如果本地有同名 tag 指向错误 commit → 删除后重新打

### 3. 工作区干净检查
```bash
git status --short
```
- 必须干净（无未提交改动）
- 如有改动 → 先处理完毕再继续发版

### 4. 构建产物检查
```bash
ls -la dist/
```
- 确认没有旧版 `.tar.gz` / `.whl` 混淆
- 建议 `rm -rf dist/` 清空后再构建

---

## 准备阶段

**版本号和 CHANGELOG 如果尚未更新，在此阶段完成**：

```bash
# 修改 pyproject.toml 版本号
# 更新 CHANGELOG.md 追加版本条目
```

> **原则**：版本号改动的 commit 和后续构建发布的 commit 必须分开。
> 即：`commit A: bump version + changelog` → `构建 + 发布` → `commit B: 发布元信息`

---

## 构建阶段

```bash
# 清理旧构建产物
rm -rf dist/ *.egg-info

# 构建
python -m build

# 检查构建产物
ls -la dist/
```

**成功后检查**：
- [ ] `dist/` 中有 `.tar.gz`（源码包）和 `.whl`（wheel 包）
- [ ] 文件名版本号正确

---

## 发布阶段

```bash
# 发布到 PyPI（需要 TWINE_USERNAME 和 TWINE_PASSWORD 或 API token 已配置）
twine upload dist/*
```

**成功后**：
- 记录发布版本号
- 记录当前 commit hash

---

## 提交阶段（Git）

### 步骤
```bash
# 1. 暂存版本号和 CHANGELOG 的变更（如果还没提交）
git add pyproject.toml CHANGELOG.md

# 2. 提交（用项目级 scope 前缀）
git commit -m "release(seed-kernel): vX.Y.Z"

# 或如果构建后没有额外文件变更 — 直接用 --allow-empty 凑 commit？
# 实际上应该先提交版本变更，再构建发布，再提交 🏷️
```

**推荐流程（三步骤）**：

| 步骤 | 操作 | Commit/Tag |
|------|------|-----------|
| ① | `git add pyproject.toml CHANGELOG.md` → `git commit` | `release(seed-kernel): vX.Y.Z` |
| ② | 构建 + 发布（见上文） | — |
| ③ | `git tag vX.Y.Z` → `git push origin main --tags` | tag `vX.Y.Z` |

### Tag 规范
- 格式：`vX.Y.Z`（如 `v1.0.13`）
- **必须在步骤 ① commit 之后、push 之前打 tag**
- 先 `git fetch --tags` 确认无冲突
- push 时务必带 `--tags` 或单独 `git push origin vX.Y.Z`

---

## 验证阶段

```bash
# 方法一：从 PyPI 安装验证
pip install seed-kernel==X.Y.Z

# 方法二：从本地构建产物验证
pip install dist/*.whl
```

**验证清单**：
- [ ] `pip install` 成功
- [ ] `pip show seed-kernel` 版本正确
- [ ] 基本 import 正常：`python -c "import <包名>; print(<包名>.__version__)"`

---

## 回滚方案

需要回滚时：

```bash
# PyPI 不允许重新上传相同版本号
# 方案 A：走 PyPI 项目设置 → 删除该版本（仅允许有限时间内）
# 方案 B：yank 版本（标记为不推荐）
twine upload --repository-url https://upload.pypi.org/legacy/ dist/xxx.whl --skip-existing
# 或手动在 PyPI Web 界面 yank

# Git 回滚
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
```

**回滚原则**：
- PyPI 版本不可覆盖，发错了只能 yank
- 所以**预检阶段的版本冲突检查**是防呆的关键防线

---

## 常见失败场景对照表

| 失败场景 | 原因 | 处理方式 |
|---------|------|---------|
| Tag 已存在（ghost tag） | 之前打过同名 tag 但没发布成功 | 删除后重打 |
| 工作区脏 | 忘记先提交 | `git stash` 或先提交 |
| PyPI 版本已存在 | 版本号重复 | 必须升版本号 |
| Twine 认证失败 | token 过期/未配置 | 检查 `$TWINE_USERNAME` + `$TWINE_PASSWORD` |
| 构建产物版本不对 | 忘了改 pyproject.toml | 先改版本号，重新构建 |
