from pathlib import Path

from app import config


def test_get_available_models_parses_csv(monkeypatch):
    monkeypatch.setenv("QGENIE_AVAILABLE_MODELS", "claude-sonnet-4, qwen3 , qgenie-pro")
    assert config.get_available_models() == ["claude-sonnet-4", "qwen3", "qgenie-pro"]


def test_get_default_model_auto(monkeypatch):
    monkeypatch.setenv("QGENIE_AVAILABLE_MODELS", "claude-sonnet-4,qwen3")
    monkeypatch.setenv("QGENIE_DEFAULT_MODEL", "auto")
    assert config.get_default_model() == "claude-sonnet-4"


def test_get_default_model_explicit(monkeypatch):
    monkeypatch.setenv("QGENIE_AVAILABLE_MODELS", "claude-sonnet-4,qwen3")
    monkeypatch.setenv("QGENIE_DEFAULT_MODEL", "qwen3")
    assert config.get_default_model() == "qwen3"


def test_is_first_run_true_when_env_file_missing(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setenv("QGENIE_API_KEY", "")
    assert config.is_first_run() is True


def test_is_first_run_false_when_key_present(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("QGENIE_API_KEY=test\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setenv("QGENIE_API_KEY", "test")
    assert config.is_first_run() is False
