# 审计：环境变量跨层污染（CODEAGENT_* / SEED_* 边界违规）

## 审计时间
2026-06-16

## 架构约定

```
用户设 CODEAGENT_XXX=value
        │
        ▼
CodeAgent 层 ─── 只认 CODEAGENT_*（产品层）
        │
        ▼
seed_bridge ─── CODEAGENT_XXX → SEED_XXX（自动映射，仅当 SEED_ 未设时）
        │
        ▼
Seed 层 ─── 只认 SEED_*（通过 seed/core/env_access.py 读取）
        │
        ▼
seed-tools ─── 也属于 Seed 生态，应通过 env_access 读 SEED_*
```

**违反规则**：Seed 生态（seed core + seed-tools）的代码中出现 `CODEAGENT_` 前缀 = 层间污染。
CodeAgent 层的代码中出现裸 key（无 `CODEAGENT_` 前缀）= 不符合产品层约定。

---

## 问题清单

### 🔴 P0 — seed-tools 直接读 CODEAGENT_*（11 处）

这些变量应该通过 `env_access` 读 `SEED_*`，由 bridge 自动映射 `CODEAGENT_* → SEED_*`。

| # | 文件 | 行 | 当前代码 | 应改为 |
|---|------|----|---------|-------|
| 1 | `seed_tools/vision.py` | 23 | `os.environ.get("CODEAGENT_VISION_ANALYZE_MAX_IMAGES", "4")` | `env_access.pick_int(4, ('SEED_VISION_ANALYZE_MAX_IMAGES',))` |
| 2 | `seed_tools/vision.py` | 96 | `os.environ.get("CODEAGENT_VISION_MAX_TOKENS", "4096")` | `env_access.pick_int(4096, ('SEED_VISION_MAX_TOKENS',))` |
| 3 | `seed_tools/vision.py` | 168 | `os.environ.get("CODEAGENT_VISION_RESULT_MAX_CHARS", "12000")` | `env_access.pick_int(12000, ('SEED_VISION_RESULT_MAX_CHARS',))` |
| 4 | `seed_tools/media.py` | 68 | `os.environ.get("CODEAGENT_AUDIO_TRANSCRIBE_TIMEOUT_SEC", "300")` | `env_access.pick_int(300, ('SEED_AUDIO_TRANSCRIBE_TIMEOUT_SEC',))` |
| 5 | `seed_tools/media.py` | 107 | `os.environ.get("CODEAGENT_VIDEO_MAX_FRAMES", "8")` | `env_access.pick_int(8, ('SEED_VIDEO_MAX_FRAMES',))` |
| 6 | `seed_tools/media.py` | 114 | `os.environ.get("CODEAGENT_VIDEO_FRAME_INTERVAL_SEC", "2")` | `env_access.pick_int(2, ('SEED_VIDEO_FRAME_INTERVAL_SEC',))`（注意 float，但当前代码用 int） |
| 7 | `seed_tools/media.py` | 191 | `os.environ.get("CODEAGENT_MEDIA_RESULT_MAX_CHARS", "12000")` | `env_access.pick_int(12000, ('SEED_MEDIA_RESULT_MAX_CHARS',))` |
| 8 | `seed_tools/image_gen.py` | 34 | `os.environ.get("CODEAGENT_IMAGE_GEN_MAX_COUNT", "4")` | `env_access.pick_int(4, ('SEED_IMAGE_GEN_MAX_COUNT',))` |
| 9 | `seed_tools/image_gen.py` | 40 | `os.environ.get("CODEAGENT_IMAGE_GEN_DEFAULT_SIZE", "1024x1024")` | `env_access.pick_nonempty(('SEED_IMAGE_GEN_DEFAULT_SIZE',)) or "1024x1024"` |
| 10 | `seed_tools/test_run.py` | 32 | `os.environ.get("CODEAGENT_BUNDLED_TOOLS", "")` | `env_access.env_truthy(('SEED_BUNDLED_TOOLS',))` |
| 11 | `seed_tools/video_gen.py` | 18 | `os.environ.get("CODEAGENT_PUBLIC_BASE_URL", "")` | `env_access.pick_nonempty(('SEED_PUBLIC_BASE_URL',))` |

**影响**：这些变量用户设 `CODEAGENT_*` 时 bridge 会映射到 `SEED_*`，所以暂不影响运行。但设 `SEED_*` 时当前代码读不到（因为硬写了 `CODEAGENT_`），**修复后行为更正确**：`SEED_*` 优先，`CODEAGENT_*` 通过 bridge 兜底。

---

### 🟡 P1 — seed core 读 CODEAGENT_* 作为 fallback（2 处）

`seed/seed/` 内核层，但当前代码已做了 `SEED_*` → `CODEAGENT_*` 的 fallback 链，属于合理的兼容代码。**保留不修**。

| # | 文件 | 行 | 模式 | 判定 |
|---|------|----|------|------|
| 1 | `integrations/webhook_auth.py` | 16 | `TASKAGENT_` → `CODEAGENT_` → `SEED_` 三链 fallback | ✅ 合理 |
| 2 | `integrations/agent_tools.py` | 137 | `SEED_` 优先（通过 `_env_truthy`），`CODEAGENT_` fallback | ✅ 合理 |

---

### 🔴 P2 — CodeAgent 层裸 key 无 CODEAGENT_ 前缀（1 文件 3 处）

`codeagent/` 产品层的变量应该带 `CODEAGENT_` 前缀，不应裸写。

| # | 文件 | 行 | 当前代码 | 应改为 |
|---|------|----|---------|-------|
| 1 | `server/self_healing.py` | 22 | `os.environ.get("SELF_HEALING_ENABLED", "1")` | `os.environ.get("CODEAGENT_SELF_HEALING_ENABLED", "1")` |
| 2 | `server/self_healing.py` | 23 | `os.environ.get("HEARTBEAT_TIMEOUT", "180")` | `os.environ.get("CODEAGENT_HEARTBEAT_TIMEOUT", "180")` |
| 3 | `server/self_healing.py` | 24 | `os.environ.get("WATCHDOG_INTERVAL", "10")` | `os.environ.get("CODEAGENT_WATCHDOG_INTERVAL", "10")` |

**影响**：如果有人设了这些裸 key 环境变量，修复后需要改为 `CODEAGENT_*` 前缀。破坏性**低**（这些通常在 dev 环境手动设，文档中也未公开过）。

---

## 修复优先级

| 优先级 | 问题 | 文件数 | 改动量 | 风险 |
|--------|------|--------|--------|------|
| **P0-1** | seed-tools: 注册 SEED_* tuple 到 env_access.py | 1 | 新增 ~11 个 tuple | 极低 |
| **P0-2** | seed-tools: 11 处读 CODEAGENT_* → env_access | 5 | 每处 1-2 行 | 低 |
| **P2** | self_healing.py: 裸 key 加 CODEAGENT_ 前缀 | 1 | 3 行 | 低（需知会使用者） |

## 验证方法

1. `cd seed-tools && python -m pytest tests/ -x -v` — 全部通过
2. `cd codeagent && python -m pytest tests/ -x -v` — 全部通过
3. 启动实例验证：`SEED_VISION_MAX_TOKENS=2048` 设后 vision 模块能读到 2048

## 审计结论

符合你设计的架构：产品层认 `CODEAGENT_*`，Seed 生态认 `SEED_*`，bridge 搭桥。
当前只有 seed-tools 11 处 + self_healing.py 3 处需要修，其他都已正确。
