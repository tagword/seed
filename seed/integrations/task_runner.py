"""Unified agent task runner for taskagent, cron, and scripts."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from seed.core.agent_context import (
    clear_active_instruction_bundle,
    clear_active_project_workspace,
    set_active_instruction_bundle,
    set_active_llm_session,
    set_active_project_episodic,
    set_active_project_workspace,
)
from seed.core.agent_runtime import (
    build_api_projection_messages,
    maybe_compact_context_messages,
    merge_llm_tail_into_full,
    persist_compact_summary,
    run_llm_tool_loop,
)
from seed.core.llm_presets import new_llm_executor_from_preset
from seed.core.llm_sess import (
    delete_stored_session,
    load_or_create_chat_session,
    merge_fresh_system,
    persist_chat_session,
)
from seed.core.paths import ensure_agent_dirs
from seed.core.proj_reg import get_project, resolve_project_path
from seed.integrations.agent_tools import get_tools_for_agent
from seed.integrations.hooks import dispatch_hooks
from seed.integrations.instruction_release import resolve_bootstrap
from seed.integrations.prompt_task import build_task_system_prompt
from seed.core import env_access as _ea

logger = logging.getLogger(__name__)

TaskStatus = Literal["ok", "failed", "timeout", "cancelled"]


@dataclass
class RunContext:
    agent_id: str
    user_message: str
    session_id: str | None = None
    llm_preset_id: str | None = None
    instruction_bundle: str | None = None
    instruction_mode: str = "bootstrap"
    instruction_sections: list[str] | None = None
    system_overlay: str = ""
    ephemeral: bool = True
    max_tool_rounds: int = 16
    timeout_sec: int | None = None
    project_id: str | None = None
    locked_bundle: str | None = field(default=None, repr=False)


@dataclass
class TaskResult:
    status: TaskStatus
    reply: str
    tools_used: list[str]
    error: str | None
    duration_ms: int
    usage: dict[str, int] | None
    session_id: str


def _new_task_session_id() -> str:
    return f"task-{uuid.uuid4().hex[:16]}"


def _build_system(ctx: RunContext) -> str:
    base = build_task_system_prompt(overlay=ctx.system_overlay)
    if ctx.instruction_bundle:
        try:
            base += resolve_bootstrap(
                ctx.instruction_bundle,
                mode=ctx.instruction_mode,
                sections=ctx.instruction_sections,
            )
        except Exception as e:
            logger.warning("instruction bootstrap failed: %s", e)
            base += f"\n\n[Instruction load error: {e}]\n"
    return base


async def run_agent_task(ctx: RunContext) -> TaskResult:
    """Execute one ephemeral agent task."""
    t0 = time.monotonic()
    aid = (ctx.agent_id or "default").strip() or "default"
    sid = (ctx.session_id or "").strip() or _new_task_session_id()
    ctx.session_id = sid
    if ctx.instruction_bundle:
        ctx.locked_bundle = ctx.instruction_bundle

    try:
        ensure_agent_dirs(aid)
    except Exception:
        pass

    chat_sess = load_or_create_chat_session(sid, aid, ctx.project_id or None)
    if not isinstance(chat_sess.metadata, dict):
        chat_sess.metadata = {}
    chat_sess.metadata["channel"] = "Task"
    chat_sess.metadata["source"] = "taskagent"
    chat_sess.metadata["ephemeral"] = bool(ctx.ephemeral)
    if ctx.instruction_bundle:
        chat_sess.metadata["instruction_bundle"] = ctx.instruction_bundle

    fresh = _build_system(ctx)
    if not chat_sess.messages:
        chat_sess.messages = [{"role": "system", "content": fresh}]
    else:
        chat_sess.messages[:] = merge_fresh_system(chat_sess.messages, fresh)

    chat_sess.messages.append({"role": "user", "content": ctx.user_message})

    llm = new_llm_executor_from_preset(ctx.llm_preset_id)
    reg, exe = get_tools_for_agent(aid)
    set_active_llm_session(sid)
    if ctx.instruction_bundle:
        set_active_instruction_bundle(ctx.instruction_bundle)

    if ctx.project_id:
        set_active_project_episodic(True, ctx.project_id)
        wd = resolve_project_path(aid, ctx.project_id)
        if wd:
            set_active_project_workspace(wd)
    else:
        set_active_project_episodic(False)
        clear_active_project_workspace()

    # ── 分块渐进压缩 ──
    # 把 run_llm_tool_loop 拆成多个小段（SEED_MAX_TOOL_ROUNDS_PER_CHUNK 轮），
    # 每段结束后合并回 full_messages → 压缩 → 重建 projection → 继续下一段。
    # 这样自主开发模式下（长时间无新用户消息），上下文窗口不会无限膨胀。
    MAX_TOOL_ROUNDS_PER_CHUNK = max(1, int(
        _ea.pick_default("4", *_ea.MAX_TOOL_ROUNDS_PER_CHUNK)
    ))
    total_max = max(1, int(ctx.max_tool_rounds))

    api_msgs = build_api_projection_messages(chat_sess.messages)
    compact_result = maybe_compact_context_messages(api_msgs, llm)
    persist_compact_summary(chat_sess.messages, compact_result)
    tools_used: list[str] = []
    reply = ""
    err: str | None = None
    status: TaskStatus = "ok"
    usage: dict[str, int] | None = None
    total_rounds_done = 0
    _chunk_n_before = len(api_msgs)

    async def _run_loop() -> None:
        nonlocal reply, tools_used, usage, status, err, api_msgs, total_rounds_done, _chunk_n_before
        try:
            while total_rounds_done < total_max:
                remaining = total_max - total_rounds_done
                chunk_rounds = min(MAX_TOOL_ROUNDS_PER_CHUNK, remaining)

                reply, meta, chunk_tools, _trace, loop_meta = await run_llm_tool_loop(
                    llm,
                    exe,
                    messages=api_msgs,
                    registry=reg,
                    max_tool_rounds=chunk_rounds,
                )
                total_rounds_done += chunk_rounds
                tools_used = chunk_tools  # last chunk wins

                # 合并新消息回 full_messages
                merge_llm_tail_into_full(chat_sess.messages, api_msgs, _chunk_n_before)

                # 累计 token 用量
                u = meta.get("usage") if isinstance(meta, dict) else None
                if isinstance(u, dict):
                    if usage is None:
                        usage = {}
                    for k, v in u.items():
                        if isinstance(v, (int, float)):
                            usage[k] = usage.get(k, 0) + int(v)

                stopped = loop_meta.get("stopped_reason")
                if stopped == "no_tool_calls":
                    break
                if stopped == "cancelled":
                    status = "cancelled"
                    break

                # 压缩 + 重建 projection，为下一段做准备
                api_msgs = build_api_projection_messages(chat_sess.messages)
                compact_result = maybe_compact_context_messages(api_msgs, llm)
                persist_compact_summary(chat_sess.messages, compact_result)

                # 注入 nudge 让 LLM 知道要继续（_auto_continue_nudge 标记，
                # 在 _user_round_indices 中计入轮次，在 LFU/merge 中过滤）
                nudge_msg = {
                    "role": "user",
                    "content": "continue",
                    "_auto_continue_nudge": True,
                }
                chat_sess.messages.append(nudge_msg)

                # 重建 projection（含 nudge），更新 n_before
                api_msgs = build_api_projection_messages(chat_sess.messages)
                _chunk_n_before = len(api_msgs)

        except Exception as e:
            status = "failed"
            err = str(e)
            logger.exception("run_agent_task chunk failed")

    try:
        if ctx.timeout_sec and ctx.timeout_sec > 0:
            await asyncio.wait_for(_run_loop(), timeout=float(ctx.timeout_sec))
        else:
            await _run_loop()
    except asyncio.TimeoutError:
        status = "timeout"
        err = f"timeout after {ctx.timeout_sec}s"

    if not ctx.ephemeral:
        try:
            persist_chat_session(chat_sess, aid)
        except Exception:
            logger.exception("persist_chat_session failed")

    dispatch_hooks(
        "turn_end",
        {
            "session_id": sid,
            "agent_id": aid,
            "stopped_reason": status,
            "ephemeral": ctx.ephemeral,
        },
    )

    if ctx.ephemeral:
        delete_stored_session(sid, aid, ctx.project_id or None)

    set_active_llm_session(None)
    set_active_project_episodic(False)
    clear_active_project_workspace()
    clear_active_instruction_bundle()

    duration_ms = int((time.monotonic() - t0) * 1000)
    return TaskResult(
        status=status,
        reply=reply or "",
        tools_used=tools_used,
        error=err,
        duration_ms=duration_ms,
        usage=usage,
        session_id=sid,
    )


def run_agent_task_sync(ctx: RunContext) -> TaskResult:
    """Sync wrapper for CLI/tests."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(run_agent_task(ctx))
    if loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, run_agent_task(ctx)).result()
    return loop.run_until_complete(run_agent_task(ctx))
