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

    api_msgs = build_api_projection_messages(chat_sess.messages)
    compact_result = maybe_compact_context_messages(api_msgs, llm)
    persist_compact_summary(chat_sess.messages, compact_result)
    n_before = len(api_msgs)
    tools_used: list[str] = []
    reply = ""
    err: str | None = None
    status: TaskStatus = "ok"
    usage: dict[str, int] | None = None

    async def _run_loop() -> None:
        nonlocal reply, tools_used, usage, status, err
        try:
            reply, meta, tools_used, _trace, loop_meta = await run_llm_tool_loop(
                llm,
                exe,
                messages=api_msgs,
                registry=reg,
                max_tool_rounds=max(1, int(ctx.max_tool_rounds)),
            )
            if loop_meta.get("stopped_reason") == "cancelled":
                status = "cancelled"
            u = meta.get("usage") if isinstance(meta, dict) else None
            if isinstance(u, dict):
                usage = {str(k): int(v) for k, v in u.items() if isinstance(v, (int, float))}
        except Exception as e:
            status = "failed"
            err = str(e)
            logger.exception("run_agent_task failed")

    try:
        if ctx.timeout_sec and ctx.timeout_sec > 0:
            await asyncio.wait_for(_run_loop(), timeout=float(ctx.timeout_sec))
        else:
            await _run_loop()
    except asyncio.TimeoutError:
        status = "timeout"
        err = f"timeout after {ctx.timeout_sec}s"

    tail = merge_llm_tail_into_full(chat_sess.messages, api_msgs, n_before)
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
