"""Tests for per-job `log_experience` opt-out in cron episodic-memory logging.

Covers:
  - `_cron_job_experience_disabled`: default (field absent) => enabled;
    explicit false => disabled; explicit true => enabled; unknown job => enabled.
  - `save_cron_job` persists the `log_experience` field through the whitelist.
  - `cron_status_for_ui` surfaces `log_experience` for the frontend.
"""
import pytest

from seed.integrations import cron_sched


def _write_tmp_config(monkeypatch, tmp_path, jobs):
    """Point cron config resolution AND writes at a temp file with the given jobs."""
    import json

    p = tmp_path / "seed.cron.json"
    p.write_text(json.dumps({"enabled": True, "jobs": jobs}), encoding="utf-8")
    # Writes go through cron_config_path -> cron_config_canonical_path;
    # reads go through cron_config_resolved_path. Point all three at tmp_path.
    monkeypatch.setattr(cron_sched, "cron_config_canonical_path", lambda: p)
    monkeypatch.setattr(cron_sched, "cron_config_resolved_path", lambda: p)
    monkeypatch.setattr(cron_sched, "cron_config_path", lambda: p)
    return p


def test_experience_disabled_default_enabled(monkeypatch, tmp_path):
    """Field absent => logging enabled (False returned, i.e. not disabled)."""
    _write_tmp_config(monkeypatch, tmp_path, [{"id": "job-a", "prompt": "x"}])
    assert cron_sched._cron_job_experience_disabled("job-a") is False


def test_experience_disabled_explicit_false(monkeypatch, tmp_path):
    """log_experience=false => disabled (True)."""
    _write_tmp_config(monkeypatch, tmp_path,
                      [{"id": "job-a", "log_experience": False, "prompt": "x"}])
    assert cron_sched._cron_job_experience_disabled("job-a") is True


def test_experience_disabled_explicit_true(monkeypatch, tmp_path):
    """log_experience=true => enabled (False)."""
    _write_tmp_config(monkeypatch, tmp_path,
                      [{"id": "job-a", "log_experience": True, "prompt": "x"}])
    assert cron_sched._cron_job_experience_disabled("job-a") is False


def test_experience_disabled_unknown_job(monkeypatch, tmp_path):
    """Unknown job id => enabled (False)."""
    _write_tmp_config(monkeypatch, tmp_path, [{"id": "job-a", "prompt": "x"}])
    assert cron_sched._cron_job_experience_disabled("job-zzz") is False


def test_save_cron_job_persists_log_experience(monkeypatch, tmp_path):
    """save_cron_job must keep the log_experience flag through its whitelist."""
    p = _write_tmp_config(monkeypatch, tmp_path, [])
    # avoid actually reloading the scheduler in this unit test
    monkeypatch.setattr(cron_sched, "reload_cron_scheduler", lambda: None)

    cron_sched.save_cron_job({
        "id": "job-meta",
        "prompt": "meta task",
        "cron": "0 9 * * sun",
        "log_experience": False,
    })
    data = cron_sched.load_cron_config()
    job = next(j for j in data["jobs"] if j["id"] == "job-meta")
    assert job.get("log_experience") is False


def test_save_cron_job_default_log_experience_true(monkeypatch, tmp_path):
    """save_cron_job without log_experience => field absent (defaults to enabled)."""
    p = _write_tmp_config(monkeypatch, tmp_path, [])
    monkeypatch.setattr(cron_sched, "reload_cron_scheduler", lambda: None)

    cron_sched.save_cron_job({"id": "job-plain", "prompt": "x", "cron": "0 8 * * *"})
    data = cron_sched.load_cron_config()
    job = next(j for j in data["jobs"] if j["id"] == "job-plain")
    assert "log_experience" not in job  # absent => legacy default (enabled)


def test_status_for_ui_surfaces_log_experience(monkeypatch, tmp_path):
    """cron_status_for_ui must include log_experience for the frontend."""
    _write_tmp_config(monkeypatch, tmp_path, [
        {"id": "job-a", "prompt": "x", "log_experience": False, "enabled": True},
    ])
    # active-job probe returns True for the id so it shows up in the UI list
    monkeypatch.setattr(cron_sched, "cron_job_id_is_active", lambda jid: True)
    out = cron_sched.cron_status_for_ui()
    job = next(j for j in out["jobs_config"] if j["id"] == "job-a")
    assert job.get("log_experience") is False
