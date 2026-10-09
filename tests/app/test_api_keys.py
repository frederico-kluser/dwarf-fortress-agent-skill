"""``/api/campaigns/{id}/keys`` — masked keys only, never the full secret."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

SECRET = "TEST-KEY-NOT-REAL-0001"
DEEPSEEK_SECRET = "TEST-KEY-NOT-REAL-0002"


def test_post_key_returns_only_the_masked_form(client: TestClient, campaign):
    response = client.post(
        f"/api/campaigns/{campaign['id']}/keys",
        json={"provider": "openrouter", "key": SECRET},
    )

    assert response.status_code == 200
    assert response.json() == {
        "provider": "openrouter",
        "masked_key": "TEST-KEY-••••••••0001",
        "set": True,
    }
    assert SECRET not in response.text


def test_get_keys_lists_all_three_providers_masked(client: TestClient, campaign):
    client.post(
        f"/api/campaigns/{campaign['id']}/keys",
        json={"provider": "openrouter", "key": SECRET},
    )
    client.post(
        f"/api/campaigns/{campaign['id']}/keys",
        json={"provider": "deepseek", "key": DEEPSEEK_SECRET},
    )

    body = client.get(f"/api/campaigns/{campaign['id']}/keys").json()

    assert [item["provider"] for item in body] == ["openrouter", "typesafe", "deepseek"]
    assert body[0] == {"provider": "openrouter", "masked_key": "TEST-KEY-••••••••0001", "set": True}
    assert body[1] == {"provider": "typesafe", "masked_key": None, "set": False}
    assert body[2]["set"] is True and body[2]["masked_key"].endswith("0002")
    for item in body:
        assert set(item) == {"provider", "masked_key", "set"}


def test_no_response_ever_contains_a_full_key(client: TestClient, campaign):
    cid = campaign["id"]
    client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

    for path in (
        "/api/health",
        "/api/campaigns",
        f"/api/campaigns/{cid}",
        f"/api/campaigns/{cid}/keys",
        f"/api/campaigns/{cid}/memory",
        f"/api/campaigns/{cid}/objective",
        f"/api/campaigns/{cid}/metrics",
        f"/api/campaigns/{cid}/spend",
        f"/api/campaigns/{cid}/decisions",
        f"/api/campaigns/{cid}/questions",
        f"/api/campaigns/{cid}/screen",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert SECRET not in response.text, path
        assert SECRET[:15] not in response.text, path


def test_openapi_documents_no_key_material(client: TestClient, campaign):
    schema = json.dumps(client.get("/openapi.json").json())
    assert SECRET not in schema
    # the request schema takes the key, the response schema returns the masked form
    assert "masked_key" in schema


def test_unknown_provider_is_rejected(client: TestClient, campaign):
    response = client.post(
        f"/api/campaigns/{campaign['id']}/keys",
        json={"provider": "anthropic", "key": "sk-ant-1234567890"},
    )
    assert response.status_code == 422
    assert "detail" in response.json()


def test_empty_key_is_rejected(client: TestClient, campaign):
    response = client.post(
        f"/api/campaigns/{campaign['id']}/keys", json={"provider": "openrouter", "key": ""}
    )
    assert response.status_code == 422


def test_overwriting_a_key_keeps_one_mask(client: TestClient, campaign):
    client.post(f"/api/campaigns/{campaign['id']}/keys", json={"provider": "typesafe", "key": "first-key-1111"})
    client.post(f"/api/campaigns/{campaign['id']}/keys", json={"provider": "typesafe", "key": "second-key-2222"})

    body = client.get(f"/api/campaigns/{campaign['id']}/keys").json()
    typesafe = [item for item in body if item["provider"] == "typesafe"][0]
    assert typesafe["masked_key"].endswith("2222")
    assert "first" not in json.dumps(body)


def test_keys_are_per_campaign(client: TestClient, campaigns_root):
    first = client.post("/api/campaigns", json={"name": "Urist"}).json()
    second = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()
    client.post(f"/api/campaigns/{first['id']}/keys", json={"provider": "openrouter", "key": SECRET})

    other = client.get(f"/api/campaigns/{second['id']}/keys").json()

    assert all(item["set"] is False for item in other)
    assert (campaigns_root / "urist" / "chaves.sqlite").exists()
    assert not (campaigns_root / "boatmurdered" / "chaves.sqlite").exists()


def test_keys_on_unknown_campaign_is_404(client: TestClient):
    assert client.get("/api/campaigns/nope/keys").status_code == 404
    assert (
        client.post("/api/campaigns/nope/keys", json={"provider": "openrouter", "key": "x" * 20}).status_code
        == 404
    )
