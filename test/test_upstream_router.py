from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def router(monkeypatch):
    """装一个假的 config，让 upstream_router 读到可控的 provider 列表。"""
    from services import config as config_module

    mod = importlib.import_module("services.upstream_router")

    class _FakeConfig:
        def __init__(self, providers):
            self.data = {"upstream_providers": providers}

    fake = _FakeConfig([
        {
            "id": "grok",
            "name": "Grok2API",
            "type": "openai",
            "base_url": "http://127.0.0.1:8002",
            "api_key": "g2a_test",
            "models": ["grok-4.7", "grok-chat-fast"],
            "enabled": True,
        },
        {
            "id": "mm",
            "name": "MiniMax2API",
            "type": "openai",
            "base_url": "http://127.0.0.1:8096",
            "api_key": "sk-mm-test",
            "models": ["minimax-m3"],
            "enabled": True,
        },
        {
            "id": "off",
            "name": "Disabled",
            "type": "openai",
            "base_url": "http://127.0.0.1:1",
            "api_key": "",
            "models": ["disabled-model"],
            "enabled": False,
        },
    ])
    monkeypatch.setattr(mod, "config", fake, raising=False)
    return mod


def test_find_provider_by_exact_model(router):
    p = router.find_provider("grok-4.7")
    assert p is not None
    assert p["name"] == "Grok2API"


def test_find_provider_unknown_model_returns_none(router):
    assert router.find_provider("gpt-6-mini") is None
    assert router.find_provider("") is None


def test_find_provider_skips_disabled(router):
    assert router.find_provider("disabled-model") is None


def test_find_provider_wildcard(router, monkeypatch):
    class _FakeConfig:
        data = {"upstream_providers": [
            {"id": "all", "name": "Any", "type": "openai",
             "base_url": "http://127.0.0.1:9999", "api_key": "k",
             "models": ["*"], "enabled": True},
        ]}

    monkeypatch.setattr(router, "config", _FakeConfig(), raising=False)
    assert router.find_provider("whatever-model") is not None


def test_provider_models_merges(router):
    ids = [m["id"] for m in router.provider_models()]
    assert "grok-4.7" in ids
    assert "minimax-m3" in ids
    assert "disabled-model" not in ids


def test_list_providers_hides_api_key(router):
    for p in router.list_providers():
        assert "api_key" not in p


def test_clean_strips_internal_fields(router):
    cleaned = router._clean({"model": "grok-4.7", "base_url": "x", "_account_email": "y"})
    assert cleaned == {"model": "grok-4.7"}


def test_endpoint_and_headers(router):
    p = router.find_provider("grok-4.7")
    assert router._endpoint(p, "/v1/chat/completions") == "http://127.0.0.1:8002/v1/chat/completions"
    headers = router._headers(p)
    assert headers["Authorization"] == "Bearer g2a_test"


def test_missing_base_url_raises(router):
    with pytest.raises(RuntimeError):
        router._endpoint({"name": "X"}, "/v1/chat/completions")
