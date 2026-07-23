# 项目级 Skill（seed 项目）

> 项目级 skill 放在 `$PROJECT_SKILLS` = `<project>/.codeagent/default/skills/` 下。
> 优先级高于 Agent 级同名 skill。

## 清单

| Skill | 用途 | 路径 |
|-------|------|------|
| `pypi-release` | 完整 PyPI 发布流程（预检→构建→发布→验证→回滚） | `skills/pypi-release/SKILL.md` |
| `git` | Monorepo 多包 Git 规范（scope / staging / tag / push） | `skills/git/SKILL.md` |

## 使用

```python
# 方式 1：项目级 scope
skill_discover(scope="project", project_path="/home/u2/agent/seed")

# 方式 2：全量（项目级优先覆盖 Agent 级同名 skill）
skill_discover(scope="all")
```
