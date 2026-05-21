"""Tests for run_agent_task (mocked LLM)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from seed.integrations.instruction_release import publish_release
from seed.integrations.task_runner import RunContext, run_agent_task_sync


@pytest.mark.asyncio
async def test_run_agent_task_ephemeral_deletes_session(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))
    publish_release("demo", "v1", "## Step\n\nDo echo.", base=tmp_path)

    async def _fake_loop(llm, executor, *, messages, registry, max_tool_rounds=16, **kwargs):
        messages.append({"role": "assistant", "content": "done"})
        return "done", {"usage": {"total_tokens": 10}}, ["echo"], [], {"rounds": 1}

    with patch("seed.integrations.task_runner.get_tools_for_agent") as mock_tools:
        mock_tools.return_value = (AsyncMock(), AsyncMock())
        with patch("seed.integrations.task_runner.run_llm_tool_loop", new=AsyncMock(side_effect=_fake_loop)):
            ctx = RunContext(
                agent_id="default",
                user_message="run demo",
                instruction_bundle="demo@v1",
                ephemeral=True,
            )
            from seed.integrations.task_runner import run_agent_task

            result = await run_agent_task(ctx)

    assert result.status == "ok"
    assert result.reply == "done"
    from seed.core.llm_sess import _find_session_file

    assert _find_session_file(result.session_id, "default", None) is None


def test_run_agent_task_sync_wrapper(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SEED_PROJECT_ROOT", str(tmp_path))

    async def _fake_loop(llm, executor, *, messages, registry, max_tool_rounds=16, **kwargs):
        return "ok", {}, [], [], {"rounds": 1}

    with patch("seed.integrations.task_runner.get_tools_for_agent") as mock_tools:
        mock_tools.return_value = (AsyncMock(), AsyncMock())
        with patch("seed.integrations.task_runner.run_llm_tool_loop", new=AsyncMock(side_effect=_fake_loop)):
            result = run_agent_task_sync(
                RunContext(agent_id="default", user_message="hi", ephemeral=True)
            )
    assert result.status == "ok"
