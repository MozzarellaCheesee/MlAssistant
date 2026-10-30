import pytest

from app import config


def test_model_names_required(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "x")
    monkeypatch.setenv("GIGACHAT_MODEL_LITE", "l")
    monkeypatch.delenv("GIGACHAT_MODEL_PRO", raising=False)
    with pytest.raises(RuntimeError, match="GIGACHAT_MODEL_PRO"):
        config.load_settings()


def _base(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda: None)
    for k, v in dict(GIGACHAT_CREDENTIALS="x", GIGACHAT_MODEL_PRO="p", GIGACHAT_MODEL_LITE="l").items():
        monkeypatch.setenv(k, v)
    for k in ("YANDEX_CLIENT_ID", "AUTH_DISABLED"):
        monkeypatch.delenv(k, raising=False)


def test_yandex_id_required_unless_auth_disabled(monkeypatch):
    _base(monkeypatch)
    with pytest.raises(RuntimeError, match="YANDEX_CLIENT_ID"):
        config.load_settings()
    monkeypatch.setenv("AUTH_DISABLED", "1")
    assert config.load_settings().auth_disabled
