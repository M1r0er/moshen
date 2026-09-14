"""/api/config 密钥掩码 + 保存回填保留 的回归测试"""
import pytest
from fastapi.testclient import TestClient

import core.config as config_module
import main as main_module
from core.config import ConfigManager, MASKED_API_KEY

ROLE = "TEXT_MASTER"


@pytest.fixture
def client(tmp_path, monkeypatch):
    manager = ConfigManager(env_path=str(tmp_path / ".env"))
    monkeypatch.setattr(config_module, "_config_manager", manager)
    return TestClient(main_module.app)


def _base_payload(**overrides):
    payload = {
        "default_api": {},
        "independent_keys": True,
        "roles": {},
        "api_channels": [],
        "image_config": {
            "enabled": False, "base_url": "", "api_key": "",
            "model": "", "size": "1024x1024", "quality": "auto",
        },
    }
    payload.update(overrides)
    return payload


def test_get_config_never_returns_plaintext_role_key(client):
    payload = _base_payload(roles={
        ROLE: {"models": ["gpt-4"], "base_url": "https://api.example.com",
               "api_key": "sk-real-secret", "temperature": 0.7, "max_tokens": 8192},
    })
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200
    assert resp.json()["roles"][ROLE]["api_key"] == MASKED_API_KEY
    assert "sk-real-secret" not in resp.text

    resp2 = client.get("/api/config")
    assert resp2.json()["roles"][ROLE]["api_key"] == MASKED_API_KEY
    assert "sk-real-secret" not in resp2.text


def test_save_round_trip_preserves_role_key_when_blank(client):
    real_key = "sk-real-secret"
    payload = _base_payload(roles={
        ROLE: {"models": ["gpt-4"], "base_url": "https://api.example.com",
               "api_key": real_key, "temperature": 0.7, "max_tokens": 8192},
    })
    client.post("/api/config", json=payload)

    # 模拟前端把掩码后的 GET 响应原样回传（api_key 留空）
    payload["roles"][ROLE]["api_key"] = ""
    resp = client.post("/api/config", json=payload)
    assert resp.status_code == 200

    mgr = config_module.get_config_manager()
    assert mgr._models[ROLE].api_key == real_key


def test_save_round_trip_preserves_role_key_when_masked_sentinel_resent(client):
    real_key = "sk-real-secret"
    payload = _base_payload(roles={
        ROLE: {"models": ["gpt-4"], "base_url": "https://api.example.com",
               "api_key": real_key, "temperature": 0.7, "max_tokens": 8192},
    })
    client.post("/api/config", json=payload)

    payload["roles"][ROLE]["api_key"] = MASKED_API_KEY
    client.post("/api/config", json=payload)

    mgr = config_module.get_config_manager()
    assert mgr._models[ROLE].api_key == real_key


def test_save_with_new_key_overwrites_role_key(client):
    payload = _base_payload(roles={
        ROLE: {"models": ["gpt-4"], "base_url": "https://api.example.com",
               "api_key": "sk-old", "temperature": 0.7, "max_tokens": 8192},
    })
    client.post("/api/config", json=payload)

    payload["roles"][ROLE]["api_key"] = "sk-new"
    client.post("/api/config", json=payload)

    mgr = config_module.get_config_manager()
    assert mgr._models[ROLE].api_key == "sk-new"


def test_channel_key_masked_and_preserved_on_round_trip(client):
    channel = {
        "id": "ch_1", "name": "Test", "models": ["gpt-4"],
        "base_url": "https://api.example.com", "api_key": "sk-channel-secret",
        "temperature": 0.7, "max_tokens": 8192, "is_default": True,
    }
    resp = client.post("/api/config", json=_base_payload(api_channels=[channel]))
    assert resp.json()["api_channels"][0]["api_key"] == MASKED_API_KEY

    # 前端重新加载后该渠道的 api_key 字段为空，保存时应保留原密钥
    client.post("/api/config", json=_base_payload(api_channels=[{**channel, "api_key": ""}]))

    mgr = config_module.get_config_manager()
    assert mgr.api_channels[0]["api_key"] == "sk-channel-secret"


def test_image_config_key_masked_and_preserved_on_round_trip(client):
    image_config = {
        "enabled": True, "base_url": "https://api.example.com",
        "api_key": "sk-image-secret", "model": "dall-e-3",
        "size": "1024x1024", "quality": "auto",
    }
    resp = client.post("/api/config", json=_base_payload(image_config=image_config))
    assert resp.json()["image_config"]["api_key"] == MASKED_API_KEY

    client.post("/api/config", json=_base_payload(image_config={**image_config, "api_key": ""}))

    mgr = config_module.get_config_manager()
    assert mgr.image_config["api_key"] == "sk-image-secret"
