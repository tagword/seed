"""
Optional scheduled agent turns (cron).

Config: ``<project_root>/config/seed.cron.json`` (legacy ``codeagent.cron.json`` is still read if present).

Disable entirely: ``SEED_CRON=0`` (alias ``CODEAGENT_CRON``; or ``false`` / ``no`` / ``off``).
Requires APScheduler: ``pip install apscheduler``.
"""
from __future__ import annotations


import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from seed.core import env_access as _ea
from seed.core._session_cache import SESSIONS, _memkey

logger = logging.getLogger(__name__)

# Track currently running cron jobs to prevent overlapping triggers
_active_jobs: set = set()


def _normalize_cron_outcome(text: str) -> str:
    return " ".join((text or "").strip().split())


def _experience_session_value(text: str) -> str:
    """First non-empty line after ``## Session``."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower() == "## session":
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if s:
                    return s
            return ""
    return ""


def _extract_outcome_section(text: str) -> str:
    """Body under ``## Outcome`` until the next ``## `` heading."""
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        if lines[i].startswith("## "):
            title = lines[i][3:].strip().lower()
            if title == "outcome":
                i += 1
                parts: List[str] = []
                while i < len(lines):
                    if lines[i].startswith("## "):
                        break
                    parts.append(lines[i])
                    i += 1
                return "\n".join(parts).strip()
        i += 1
    return ""


def _cron_outcome_matches_latest(
    mem: Any,
    *,
    job_id: str,
    session_id: str,
    new_outcome: str,
) -> bool:
    """
    True if the newest experience for this cron job id + session has the same outcome text.
    Used to skip writing duplicate episodic rows for periodic checks.
    """
    exp_dir = mem.memory_path / "experiences"
    if not exp_dir.is_dir():
        return False
    needle = f"cron-{job_id}-"
    new_norm = _normalize_cron_outcome(new_outcome)
    if not new_norm:
        return False
    want_sid = (session_id or "").strip()
    files = sorted(exp_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[:120]:
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if needle not in body:
            continue
        if _experience_session_value(body) != want_sid:
            continue
        prev = _extract_outcome_section(body)
        return _normalize_cron_outcome(prev) == new_norm
    return False


CRON_JSON = "seed.cron.json"
LEGACY_CRON_JSON = "codeagent.cron.json"


def cron_config_canonical_path() -> Path:
    """Path used for new writes (always ``seed.cron.json``)."""
    from seed.core.config_plane import project_root

    return project_root() / "config" / CRON_JSON


def cron_config_resolved_path() -> Path:
    """Active config path: prefer ``seed.cron.json``, else legacy ``codeagent.cron.json``."""
    from seed.core.config_plane import project_root

    cfg = project_root() / "config"
    seed_p = cfg / CRON_JSON
    leg = cfg / LEGACY_CRON_JSON
    if seed_p.is_file():
        return seed_p
    if leg.is_file():
        return leg
    return seed_p


def cron_config_path() -> Path:
    """Alias for :func:`cron_config_canonical_path` (writes go here)."""
    return cron_config_canonical_path()


def load_cron_config() -> Dict[str, Any]:
    p = cron_config_resolved_path()
    if not p.is_file():
        return {"enabled": False, "jobs": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("cron: cannot read %s: %s", p, e)
        return {"enabled": False, "jobs": []}
    if not isinstance(data, dict):
        return {"enabled": False, "jobs": []}
    jobs = data.get("jobs")
    if not isinstance(jobs, list):
        data["jobs"] = []
    return data


def cron_job_id_is_active(jid: str) -> bool:
    """Whether this job id is listed in the UI and registered with APScheduler.

    Empty ids are inactive. Ids that are *only* underscores (e.g. placeholder
    slugs from pure non-ASCII names) are inactive. An id like ``_backup`` stays
    active because it contains alphanumeric characters.
    """

    s = (jid or "").strip()
    if not s:
        return False
    if s.startswith("_") and not any(c.isalnum() for c in s):
        return False
    return True


# APScheduler 3.x 的 ``CronTrigger.from_crontab`` 会把 day-of-week 字段原样透传给
# ``DayOfWeekField``，而后者遵循 Python weekday 约定（0=Monday … 6=Sunday）。
# 这与标准 crontab 语义（0/7=Sunday … 6=Saturday）不一致：配置 ``0 9 * * 0`` 会在
# 周一而不是周日触发。这里把第 5 个字段从标准 crontab 语义转换为 APScheduler 语义。
# 文本名（sun/mon/…）两种语义一致，保留原样；数字一律展开为显式列表，
# 避免范围回绕（如 crontab ``0-5`` = Sun-Fri 不能直接写成 APS 的 ``6-4``）。
_CRONTAB_DOW_TO_APS: Dict[int, int] = {
    0: 6,  # Sunday
    1: 0,  # Monday
    2: 1,  # Tuesday
    3: 2,  # Wednesday
    4: 3,  # Thursday
    5: 4,  # Friday
    6: 5,  # Saturday
    7: 6,  # Sunday（部分 crontab 实现支持 7）
}


def _dow_token_to_aps(token: str) -> str:
    """Convert a single crontab day-of-week token to APScheduler semantics."""
    body, _, step = token.partition("/")
    if body == "*":
        if not step:
            return token
        # */n: expand over crontab day range (0..7) then map
        nums = list(range(0, 8, int(step)))
        mapped = [str(_CRONTAB_DOW_TO_APS[n]) for n in nums if n in _CRONTAB_DOW_TO_APS]
        return ",".join(mapped) or token
    if body.isdigit():
        if not step:
            # 单个数字：直接映射（0→6 周日, 1→0 周一, …）
            n = int(body)
            return str(_CRONTAB_DOW_TO_APS[n]) if n in _CRONTAB_DOW_TO_APS else token
        # "N/step": 从 N 起步进到 crontab 域末尾（0..7）再映射
        nums = list(range(int(body), 8, int(step)))
        mapped = [str(_CRONTAB_DOW_TO_APS[n]) for n in nums if n in _CRONTAB_DOW_TO_APS]
        return ",".join(mapped) or token
    if "-" in body:
        lo, _, hi = body.partition("-")
        lo, hi = lo.strip(), hi.strip()
        if lo.isdigit() and hi.isdigit():
            nums = list(range(int(lo), int(hi) + 1, int(step) if step else 1))
            mapped = [str(_CRONTAB_DOW_TO_APS[n]) for n in nums if n in _CRONTAB_DOW_TO_APS]
            return ",".join(mapped) or token
        # 文本范围（mon-fri）或混合：APScheduler 与 crontab 文本语义一致，原样保留
        return token
    return token


def _convert_crontab_dow(expr: str) -> str:
    """Map standard crontab day-of-week (0/7 = Sunday) to APScheduler (0 = Monday)."""
    fields = expr.split()
    if len(fields) != 5:
        return expr
    fields[4] = ",".join(
        _dow_token_to_aps(tok) for tok in fields[4].split(",") if tok.strip()
    )
    return " ".join(fields)


def _cron_disabled_by_env() -> bool:
    return _ea.pick_default("1", *_ea.CRON).lower() in (
        "0",
        "false",
        "no",
        "off",
    )


def _tools_for_agent(_aid: str):
    from seed_tools import setup_builtin_tools

    return setup_builtin_tools()


_scheduler: Optional[Any] = None


async def _run_cron_job_async(job: Dict[str, Any]) -> None:
    """Execute one cron job fully async — shares the main event loop (no asyncio.run)."""
    import asyncio
    from seed.core._session_cache import SESSIONS, _memkey

    if not job.get("enabled", True):
        return
    jid = (str(job.get("id") or "").strip() or "cron-job")
    agent_id = (str(job.get("agent_id") or "").strip() or "default")
    sid = (str(job.get("session_id") or "").strip() or f"cron-{jid}")
    prompt = (str(job.get("prompt") or "")).strip()
    if not prompt:
        logger.warning("cron job %s: empty prompt, skip", jid)
        return
    # Skip if previous run is still in progress
    if jid in _active_jobs:
        logger.info("cron job %s: previous run still active, skip this trigger", jid)
        return
    _active_jobs.add(jid)

    try:
        from seed.core.paths import ensure_agent_dirs
        await asyncio.to_thread(ensure_agent_dirs, agent_id)
    except Exception:
        pass

    project_id = (str(job.get("project_id") or "")).strip() or ""
    mkey = _memkey(agent_id, sid)
    from seed.core.llm_sess import (
        load_or_create_chat_session,
        merge_fresh_system,
        persist_chat_session,
    )
    from seed.core.agent_runtime import (
        build_api_projection_messages,
        default_system_prompt,
        maybe_compact_context_messages,
        merge_llm_tail_into_full,
        persist_compact_summary,
        run_llm_tool_loop,
        strip_ephemeral_message_fields,
    )
    from seed.core.agent_context import clear_active_project_episodic, set_active_llm_session
    from seed.core.llm_exec import LLMError
    from seed.core.mem_bridge import finalize_episodic_for_llm
    from seed.core.mem_sys import MemorySystem
    from seed.core.llm_presets import llm_executor_from_resolved, resolve_preset
    if mkey in SESSIONS:
        chat_sess = SESSIONS[mkey]
    else:
        chat_sess = await asyncio.to_thread(
            load_or_create_chat_session, sid, agent_id, project_id=project_id
        )

    # 将 cron 会话关联到项目（如果有），并标记频道
    if not isinstance(chat_sess.metadata, dict):
        chat_sess.metadata = {}
    if project_id:
        chat_sess.metadata["project_id"] = project_id
    chat_sess.metadata["channel"] = "Cron"
    chat_sess.metadata["source"] = f"cron:{jid}"

    fresh = default_system_prompt()
    import hashlib

    cur_hash = hashlib.sha256((fresh or "").encode("utf-8")).hexdigest()
    if not isinstance(chat_sess.metadata, dict):
        chat_sess.metadata = {}
    prev_hash = str(chat_sess.metadata.get("system_hash") or "").strip()
    if not chat_sess.messages:
        chat_sess.messages = [{"role": "system", "content": fresh}]
    else:
        if (not prev_hash) or (prev_hash != cur_hash):
            chat_sess.messages[:] = merge_fresh_system(chat_sess.messages, fresh)
        else:
            try:
                keep = str(chat_sess.messages[0].get("content") or "")
            except Exception:
                keep = ""
            chat_sess.messages[:] = merge_fresh_system(chat_sess.messages, keep)
    chat_sess.metadata["system_hash"] = cur_hash

    cron_line = f"[cron:{jid}] {prompt}"
    chat_sess.messages.append(
        {
            "role": "user",
            "content": cron_line,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Resolve LLM config from presets / env
    llm = llm_executor_from_resolved(resolve_preset(None))
    # run_in_executor for the env reads (resolve_preset may read files)
    set_active_llm_session(mkey)
    tools_used: List[str] = []
    tool_trace: List[Dict[str, str]] = []
    try:
        api_msgs = build_api_projection_messages(
            chat_sess.messages,
            skills_suffix=None,
        )
        compact_result = maybe_compact_context_messages(api_msgs, llm)
        persist_compact_summary(chat_sess.messages, compact_result)
        strip_ephemeral_message_fields(api_msgs)
        await asyncio.to_thread(
            finalize_episodic_for_llm,
            api_msgs,
            chat_sess.metadata,
            agent_id=agent_id,
            session_id=sid,
            project_id=project_id or None,
            compact_happened=compact_result is not None,
        )
        reg, exe = _tools_for_agent(agent_id)
        n_before = len(api_msgs)
        # ── single run (no continuation loop — cron interval handles retriggering) ──
        cron_max_rounds = max(1, int(job.get("max_tool_rounds") or 16))
        reply, __, tools_used, tool_trace, _loop_meta = await run_llm_tool_loop(
            llm, exe,
            messages=api_msgs,
            registry=reg,
            max_tool_rounds=cron_max_rounds,
        )
        merge_llm_tail_into_full(chat_sess.messages, api_msgs, n_before)
        try:
            await asyncio.to_thread(persist_chat_session, chat_sess, agent_id)
        except Exception:
            logger.exception("cron persist failed job=%s", jid)
        SESSIONS[mkey] = chat_sess
        if _ea.pick_default("1", *_ea.MEMORY_LOG).lower() not in ("0", "false", "no"):
            try:
                from seed.core.paths import agent_memory_dir

                def _log_cron_experience():
                    mem = MemorySystem(base_path=agent_memory_dir(agent_id))
                    outcome = (reply or "")[:2000]
                    skip_dup = _ea.pick_default(
                        "", *_ea.CRON_EXPERIENCE_SKIP_DUPLICATE
                    ).lower() in ("1", "true", "yes", "on")
                    if skip_dup and _cron_outcome_matches_latest(
                        mem, job_id=jid, session_id=sid, new_outcome=outcome
                    ):
                        logger.info(
                            "cron job id=%s: skip experience log (outcome unchanged vs latest for session=%s)",
                            jid,
                            sid,
                        )
                        return
                    ttl_raw = _ea.pick_nonempty(*_ea.CRON_EXPERIENCE_TTL_SECONDS)
                    ttl_val = int(ttl_raw) if ttl_raw.isdigit() else None
                    mem.log_experience(
                        task_id=f"cron-{jid}-{datetime.now(timezone.utc).isoformat()}",
                        outcome=outcome,
                        tools_used=tools_used,
                        session_id=sid,
                        ttl_seconds=ttl_val,
                    )

                await asyncio.to_thread(_log_cron_experience)
            except Exception:
                pass
        logger.info(
            "cron job done id=%s agent=%s session=%s tools=%s trace_len=%s",
            jid,
            agent_id,
            sid,
            ",".join(tools_used) if tools_used else "(none)",
            len(tool_trace),
        )
    except LLMError as e:
        logger.warning("cron job LLM error id=%s: %s", jid, e)
        try:
            chat_sess.messages.pop()
        except Exception:
            pass
    except Exception:
        logger.exception("cron job crashed id=%s", jid)
        try:
            chat_sess.messages.pop()
        except Exception:
            pass
    finally:
        _active_jobs.discard(jid)
        clear_active_project_episodic()
        set_active_llm_session(None)


def start_cron_scheduler() -> None:
    """Start APScheduler from disk config (no-op if disabled or apscheduler missing)."""
    global _scheduler
    if _scheduler is not None:
        return
    if _cron_disabled_by_env():
        logger.info("cron: disabled (SEED_CRON / CODEAGENT_CRON)")
        return

    cfg = load_cron_config()
    if not cfg.get("enabled"):
        logger.info("cron: config disabled or missing (see config/%s)", CRON_JSON)
        return

    jobs: List[Dict[str, Any]] = [j for j in cfg.get("jobs") or [] if isinstance(j, dict)]
    if not jobs:
        logger.info("cron: no jobs defined")
        return

    actionable = False
    for job in jobs:
        if not job.get("enabled", True):
            continue
        jid = (str(job.get("id") or "").strip() or None)
        if not jid or not cron_job_id_is_active(jid):
            continue
        if (str(job.get("cron") or "")).strip():
            actionable = True
            break

    if not actionable:
        logger.info("cron: no enabled jobs with a cron expression")
        return

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning(
            "cron: APScheduler unexpectedly unavailable; scheduled jobs will not run."
        )
        return

    sched = AsyncIOScheduler()
    default_tz = _ea.pick_default("UTC", *_ea.CRON_TZ).strip() or "UTC"

    for job in jobs:
        if not job.get("enabled", True):
            continue
        jid = (str(job.get("id") or "").strip() or None)
        if not jid or not cron_job_id_is_active(jid):
            continue
        expr = (str(job.get("cron") or "")).strip()
        if not expr:
            logger.warning("cron: skip job (missing id or cron): %s", job)
            continue
        tz_name = (str(job.get("timezone") or "")).strip() or default_tz
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(tz_name)
        except Exception:
            logger.warning("cron job %s: bad timezone %r, use UTC", jid, tz_name)
            from zoneinfo import ZoneInfo

            tz = ZoneInfo("UTC")
        try:
            trigger = CronTrigger.from_crontab(_convert_crontab_dow(expr), timezone=tz)
        except Exception as e:
            logger.warning("cron job %s: invalid cron %r: %s", jid, expr, e)
            continue
        sched.add_job(
            _run_cron_job_async,
            trigger,
            args=[job],
            id=f"oa-cron-{jid}",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        logger.info("cron: registered job id=%s cron=%r tz=%s", jid, expr, tz_name)

    sched.start()
    _scheduler = sched
    logger.info("cron: scheduler started (%s job(s))", len(sched.get_jobs()))


def shutdown_cron_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        logger.exception("cron: scheduler shutdown")
    _scheduler = None


# 进程主事件循环：由服务入口（如 codeagent app_factory 的 lifespan）注册。
# 用于在无 running loop 的 worker 线程（agent 工具执行环境）安全触发 reload。
_main_loop: Optional[Any] = None


def register_main_loop(loop: Optional[Any] = None) -> None:
    """Record the process main event loop (must be called from inside that loop)."""
    global _main_loop
    if loop is not None:
        _main_loop = loop
        return
    try:
        _main_loop = asyncio.get_running_loop()
    except RuntimeError:
        _main_loop = None


def _reload_now() -> None:
    shutdown_cron_scheduler()
    start_cron_scheduler()


async def _reload_async() -> None:
    _reload_now()


def reload_cron_scheduler() -> str:
    """Re-read cron JSON and rebuild APScheduler. Thread-safe.

    优先在当前线程的 running loop 上执行（服务内调用）；否则调度到进程
    主事件循环（``register_main_loop`` 注册）上执行，保证 agent 工具在
    worker 线程也能真正热更新；两者都不可用时**安全失败**——绝不先
    shutdown 再失败，避免 ``AsyncIOScheduler.start()`` 因 ``no running
    event loop`` 抛错把现有调度器打成停摆。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        _reload_now()
        return "reloaded"

    loop = _main_loop
    if loop is not None and not loop.is_closed():
        if not loop.is_running():
            logger.error("cron: reload skipped — main loop not running")
            return "error: main loop not running"
        try:
            fut = asyncio.run_coroutine_threadsafe(_reload_async(), loop)
            fut.result(timeout=10)
            return "reloaded (via main loop)"
        except Exception as e:
            logger.exception("cron: reload via main loop failed")
            return f"error: {e}"

    logger.error(
        "cron: reload skipped — no running event loop in this thread and no active "
        "main loop; scheduler left untouched"
    )
    return "error: no running event loop available"


def write_cron_config(data: Dict[str, Any]) -> None:
    """Write the full cron config dict to disk."""
    p = cron_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def save_cron_job(job: Dict[str, Any]) -> None:
    """Add or update a single job in the cron config, then reload scheduler."""
    cfg = load_cron_config()
    jobs = cfg.get("jobs") or []
    jid = str(job.get("id") or "").strip()
    prev: Optional[Dict[str, Any]] = None
    for j in jobs:
        if isinstance(j, dict) and str(j.get("id") or "").strip() == jid:
            prev = j
            break
    # ensure required fields
    entry: Dict[str, Any] = {
        "id": jid,
        "enabled": bool(job.get("enabled", True)),
        "cron": str(job.get("cron") or "0 * * * *").strip(),
        "agent_id": str(job.get("agent_id") or "default").strip() or "default",
        "session_id": str(job.get("session_id") or "").strip() or ("cron-" + jid),
        "prompt": str(job.get("prompt") or "").strip(),
    }
    if job.get("timezone"):
        entry["timezone"] = str(job.get("timezone") or "").strip()
    mtr = job.get("max_tool_rounds")
    if mtr is not None:
        try:
            entry["max_tool_rounds"] = int(mtr)
        except (TypeError, ValueError):
            pass
    pid = str(job.get("project_id") or "").strip()
    if pid:
        entry["project_id"] = pid
    if "title" in job:
        t = str(job.get("title") or "").strip()
        if t:
            entry["title"] = t
    elif isinstance(prev, dict):
        ot = str(prev.get("title") or "").strip()
        if ot:
            entry["title"] = ot
    # update existing or append
    found = False
    for i, j in enumerate(jobs):
        if isinstance(j, dict) and str(j.get("id") or "").strip() == jid:
            jobs[i] = entry
            found = True
            break
    if not found:
        jobs.append(entry)
    cfg["jobs"] = jobs
    write_cron_config(cfg)
    reload_cron_scheduler()


def delete_cron_job(job_id: str) -> None:
    """Remove a job by id, then reload scheduler."""
    cfg = load_cron_config()
    jobs = cfg.get("jobs") or []
    jid = job_id.strip()
    cfg["jobs"] = [j for j in jobs if not (isinstance(j, dict) and str(j.get("id") or "").strip() == jid)]
    write_cron_config(cfg)
    reload_cron_scheduler()


def apscheduler_available() -> bool:
    try:
        import apscheduler  # noqa: F401

        return True
    except ImportError:
        return False


def cron_status_for_ui() -> Dict[str, Any]:
    """Lightweight status for ``/api/ui/flags`` (no secrets, no full prompts)."""
    cfg = load_cron_config()
    jobs_config: List[Dict[str, Any]] = []
    for j in cfg.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        jid = str(j.get("id") or "").strip()
        if not cron_job_id_is_active(jid):
            continue
        row = {
            "id": jid,
            "enabled": bool(j.get("enabled", True)),
            "cron": str(j.get("cron") or "").strip(),
            "timezone": str(j.get("timezone") or "").strip(),
            "agent_id": str(j.get("agent_id") or "default").strip() or "default",
            "session_id": str(j.get("session_id") or "").strip(),
            "prompt": str(j.get("prompt") or "").strip(),
            "project_id": str(j.get("project_id") or "").strip(),
        }
        title = str(j.get("title") or "").strip()
        if title:
            row["title"] = title
        jobs_config.append(row)
    out: Dict[str, Any] = {
        "apscheduler": apscheduler_available(),
        "env_disabled": _cron_disabled_by_env(),
        "config_file": str(cron_config_path()),
        "config_enabled": bool(cfg.get("enabled")),
        "job_defs": len([x for x in jobs_config if x.get("enabled")]),
        "jobs_config": jobs_config,
        "scheduler_running": _scheduler is not None,
        "scheduled_jobs": [],
    }
    if _scheduler is not None:
        for j in _scheduler.get_jobs():
            nr = getattr(j, "next_run_time", None)
            out["scheduled_jobs"].append(
                {
                    "id": getattr(j, "id", "") or "",
                    "next_run": nr.isoformat() if nr is not None else None,
                }
            )
    return out


def run_cron_job_sync(job: Dict[str, Any]) -> None:
    """Execute one cron job: one LLM+tools turn, persist session, sync in-memory SESSIONS."""
    if not job.get("enabled", True):
        return
    jid = (str(job.get("id") or "").strip() or "cron-job")
    agent_id = (str(job.get("agent_id") or "").strip() or "default")
    sid = (str(job.get("session_id") or "").strip() or f"cron-{jid}")
    prompt = (str(job.get("prompt") or "")).strip()
    if not prompt:
        logger.warning("cron job %s: empty prompt, skip", jid)
        return
    # Skip if previous run is still in progress
    if jid in _active_jobs:
        logger.info("cron job %s: previous run still active, skip this trigger", jid)
        return
    _active_jobs.add(jid)

    try:
        from seed.core.paths import ensure_agent_dirs

        ensure_agent_dirs(agent_id)
    except Exception:
        pass

    project_id = (str(job.get("project_id") or "")).strip() or ""
    mkey = _memkey(agent_id, sid)
    from seed.core.llm_sess import (
        load_or_create_chat_session,
        merge_fresh_system,
        persist_chat_session,
    )
    from seed.core.agent_runtime import (
        build_api_projection_messages,
        default_system_prompt,
        maybe_compact_context_messages,
        merge_llm_tail_into_full,
        persist_compact_summary,
        run_llm_tool_loop,
        strip_ephemeral_message_fields,
    )
    from seed.core.agent_context import clear_active_project_episodic, set_active_llm_session
    from seed.core.llm_exec import LLMError
    from seed.core.mem_bridge import finalize_episodic_for_llm
    from seed.core.mem_sys import MemorySystem
    from seed.core.llm_presets import llm_executor_from_resolved, resolve_preset

    if mkey in SESSIONS:
        chat_sess = SESSIONS[mkey]
    else:
        chat_sess = load_or_create_chat_session(sid, agent_id, project_id=project_id)

    # 将 cron 会话关联到项目（如果有），并标记频道
    if not isinstance(chat_sess.metadata, dict):
        chat_sess.metadata = {}
    if project_id:
        chat_sess.metadata["project_id"] = project_id
    chat_sess.metadata["channel"] = "Cron"
    chat_sess.metadata["source"] = f"cron:{jid}"

    fresh = default_system_prompt()
    import hashlib
    cur_hash = hashlib.sha256((fresh or "").encode("utf-8")).hexdigest()
    if not isinstance(chat_sess.metadata, dict):
        chat_sess.metadata = {}
    prev_hash = str(chat_sess.metadata.get("system_hash") or "").strip()
    if not chat_sess.messages:
        chat_sess.messages = [{"role": "system", "content": fresh}]
    else:
        if (not prev_hash) or (prev_hash != cur_hash):
            chat_sess.messages[:] = merge_fresh_system(chat_sess.messages, fresh)
        else:
            try:
                keep = str(chat_sess.messages[0].get("content") or "")
            except Exception:
                keep = ""
            chat_sess.messages[:] = merge_fresh_system(chat_sess.messages, keep)
    chat_sess.metadata["system_hash"] = cur_hash

    cron_line = f"[cron:{jid}] {prompt}"
    chat_sess.messages.append(
        {
            "role": "user",
            "content": cron_line,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Resolve LLM config from presets / env (see resolve_preset)
    llm = llm_executor_from_resolved(resolve_preset(None))
    set_active_llm_session(mkey)
    tools_used: List[str] = []
    tool_trace: List[Dict[str, str]] = []
    try:
        api_msgs = build_api_projection_messages(
            chat_sess.messages,
            skills_suffix=None,
        )
        compact_result = maybe_compact_context_messages(api_msgs, llm)
        persist_compact_summary(chat_sess.messages, compact_result)
        strip_ephemeral_message_fields(api_msgs)
        finalize_episodic_for_llm(
            api_msgs,
            chat_sess.metadata,
            agent_id=agent_id,
            session_id=sid,
            project_id=project_id or None,
            compact_happened=compact_result is not None,
        )
        reg, exe = _tools_for_agent(agent_id)
        n_before = len(api_msgs)
        # ── single run (no continuation loop — cron interval handles retriggering) ──
        cron_max_rounds = max(1, int(job.get("max_tool_rounds") or 16))
        reply, __, tools_used, tool_trace, _loop_meta = asyncio.run(
            run_llm_tool_loop(
                llm, exe,
                messages=api_msgs,
                registry=reg,
                max_tool_rounds=cron_max_rounds,
            )
        )
        merge_llm_tail_into_full(chat_sess.messages, api_msgs, n_before)
        try:
            persist_chat_session(chat_sess, agent_id)
        except Exception:
            logger.exception("cron persist failed job=%s", jid)
        SESSIONS[mkey] = chat_sess
        if _ea.pick_default("1", *_ea.MEMORY_LOG).lower() not in ("0", "false", "no"):
            try:
                from seed.core.paths import agent_memory_dir

                mem = MemorySystem(base_path=agent_memory_dir(agent_id))
                outcome = (reply or "")[:2000]
                skip_dup = _ea.pick_default("", *_ea.CRON_EXPERIENCE_SKIP_DUPLICATE).lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
                if skip_dup and _cron_outcome_matches_latest(
                    mem, job_id=jid, session_id=sid, new_outcome=outcome
                ):
                    logger.info(
                        "cron job id=%s: skip experience log (outcome unchanged vs latest for session=%s)",
                        jid,
                        sid,
                    )
                else:
                    ttl_raw = _ea.pick_nonempty(*_ea.CRON_EXPERIENCE_TTL_SECONDS)
                    ttl_val = int(ttl_raw) if ttl_raw.isdigit() else None
                    mem.log_experience(
                        task_id=f"cron-{jid}-{datetime.now(timezone.utc).isoformat()}",
                        outcome=outcome,
                        tools_used=tools_used,
                        session_id=sid,
                        ttl_seconds=ttl_val,
                    )
            except Exception:
                pass
        logger.info(
            "cron job done id=%s agent=%s session=%s tools=%s trace_len=%s",
            jid,
            agent_id,
            sid,
            ",".join(tools_used) if tools_used else "(none)",
            len(tool_trace),
        )
    except LLMError as e:
        logger.warning("cron job LLM error id=%s: %s", jid, e)
        try:
            chat_sess.messages.pop()
        except Exception:
            pass
    except Exception:
        logger.exception("cron job crashed id=%s", jid)
        try:
            chat_sess.messages.pop()
        except Exception:
            pass
    finally:
        _active_jobs.discard(jid)
        clear_active_project_episodic()
        set_active_llm_session(None)


