import os

from seed.integrations.env_config import apply_seed_env_from_config


def test_loads_seed_and_codeagent_env_files(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "env").write_text("SEED_LLM_MODEL=from-seed\n", encoding="utf-8")
    (cfg / "codeagent.env").write_text(
        "CODEAGENT_SKILLS_AUTO=0\nSEED_LLM_MODEL=from-product\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("SEED_LLM_MODEL", raising=False)
    monkeypatch.delenv("CODEAGENT_SKILLS_AUTO", raising=False)

    apply_seed_env_from_config(tmp_path)

    assert os.environ["SEED_LLM_MODEL"] == "from-seed"
    assert os.environ["CODEAGENT_SKILLS_AUTO"] == "0"


def test_legacy_seed_env_fallback(tmp_path, monkeypatch):
    """Old seed.env is still loaded when env doesn't exist."""
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "seed.env").write_text("SEED_LEGACY_VAR=works\n", encoding="utf-8")
    monkeypatch.delenv("SEED_LEGACY_VAR", raising=False)

    apply_seed_env_from_config(tmp_path)

    assert os.environ["SEED_LEGACY_VAR"] == "works"


def test_legacy_only_codeagent_env(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "codeagent.env").write_text("CODEAGENT_AGENT_ID=legacy\n", encoding="utf-8")
    monkeypatch.delenv("CODEAGENT_AGENT_ID", raising=False)

    apply_seed_env_from_config(tmp_path)

    assert os.environ["CODEAGENT_AGENT_ID"] == "legacy"
