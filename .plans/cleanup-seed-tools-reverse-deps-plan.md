# 清理 seed-tools → codeagent 逆向依赖

## 状态: ✅ 已完成

## 目标

消除 `seed-tools/seed_tools/` 中所有 `from codeagent.core.xxx` 的惰性导入，改为使用 `seed.core.xxx` 或自实现。
让 seed-tools 真正只依赖 seed 内核，恢复设计隔离。

## 现有逆向依赖清单

| 文件 | 导入 | 替代方案 |
|------|------|---------|
| `vision_tools.py` (6处) | `codeagent.core.attachments.resolve_attachment_path` | `seed.core.media_store.resolve_session_media_path` |
| | `codeagent.core.vision_models.get_vision_executor` | `seed.core.llm_exec.get_llm_executor()` + `seed.core.llm_presets.resolve_preset()` |
| | `codeagent.core.usage_billing.merge_accumulated_usage` | 直接用 seed 的 `usage_accumulator` + 写入 session |
| | `codeagent.core.vision_models.resolve_preset_id` | `seed.core.llm_presets.load_presets()` |
| | `codeagent.core.attachments.scan_image_directory` | 自实现简单扫描 |
| | `codeagent.core.attachments.save_attachment` | `seed.core.media_store.save_session_media` |
| `music_gen_tools.py` (3处) | `codeagent.core.attachments.resolve_attachment_path` | `seed.core.media_store.resolve_session_media_path` |
| | `codeagent.core.music_models.resolve_music_preset` | seed-tools 自实现 preset 解析 |
| | `codeagent.core.attachments.save_attachment` | `seed.core.media_store.save_session_media` |
| `video_gen_tools.py` (2处) | `codeagent.core.video_models.resolve_video_gen_preset` | seed-tools 自实现 preset 解析 |
| | `codeagent.core.attachments.save_attachment` | `seed.core.media_store.save_session_media` |
| `image_gen_tools.py` (3处) | `codeagent.core.attachments.resolve_attachment_path` | `seed.core.media_store.resolve_session_media_path` |
| | `codeagent.core.image_gen_models.resolve_image_gen_preset` | seed-tools 自实现 preset 解析 |
| | `codeagent.core.attachments.save_attachment` | `seed.core.media_store.save_session_media` |
| `media_tools.py` (3处) | `codeagent.core.attachments.resolve_attachment_path` | `seed.core.media_store.resolve_session_media_path` |
| | `codeagent.core.audio_models.resolve_audio_preset` (x2) | seed-tools 自实现 preset 解析 |

## 执行步骤

### Step 1: 新建 `seed_tools/_preset_helpers.py` — 通用 preset 解析函数

所有 model resolve 函数（vision/music/video/image/audio）有相同模式：
1. 从 `agent_context.get_active_*_preset()` 或 env 获取 preset_id
2. 用 `load_presets()` 按 capability flag 过滤
3. 按 id 精确匹配或 fallback 取唯一匹配

抽象为 `_resolve_capability_preset(capability_key, env_var, active_fn, error_msg)`。

### Step 2: 逐文件替换

1. **vision_tools.py** — 改 6 处导入
2. **music_gen_tools.py** — 改 3 处导入
3. **video_gen_tools.py** — 改 2 处导入
4. **image_gen_tools.py** — 改 3 处导入
5. **media_tools.py** — 改 3 处导入

### Step 3: 验证

- `pytest tests/` 跑 seed-tools 测试
- `python -c "from seed_tools import *"` 确保 import 无报错
- 确认 `grep -r "codeagent" seed-tools/seed_tools/` 不再有导入语句

## 关键设计决策

- **preset 解析**：抽象到 `_preset_helpers.py` 中统一处理，避免每个文件重复逻辑
- **save_attachment**：seed 的 `save_session_media` 返回 `(aid, path)`，codeagent 的 `save_attachment` 返回 `AttachmentMeta`。seed-tools 中需要 `aid` 和原始 `filename`——直接构造 dict 代替。
- **usage billing**：`_accumulate_vision_usage` 简化为只记录原始 token 用量到 session metadata，不做 cost 计算（那是 codeagent 层的计费逻辑）
- **scan_image_directory**：浅层目录扫描，自实现即可
