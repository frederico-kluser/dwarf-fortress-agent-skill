"""``GET /api/health`` and the OpenAPI surface of the frozen contract."""

from __future__ import annotations

from system_one_dwarf import __version__

#: Every REST path in docs/api.md (WebSocket is not part of the OpenAPI schema).
CONTRACT_PATHS = {
    "/api/health",
    "/api/campaigns",
    "/api/campaigns/{campaign_id}",
    "/api/campaigns/{campaign_id}/keys",
    "/api/campaigns/{campaign_id}/memory",
    "/api/campaigns/{campaign_id}/objective",
    "/api/campaigns/{campaign_id}/inputs",
    "/api/campaigns/{campaign_id}/metrics",
    "/api/campaigns/{campaign_id}/spend",
    "/api/campaigns/{campaign_id}/decisions",
    "/api/campaigns/{campaign_id}/questions",
    "/api/campaigns/{campaign_id}/questions/{question_id}/answer",
    "/api/campaigns/{campaign_id}/control",
    "/api/campaigns/{campaign_id}/screen",
}


def test_health_shape(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
    assert response.headers["content-type"].startswith("application/json")


def test_health_is_reachable_without_any_campaign(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_openapi_contains_every_contract_path(client):
    schema = client.get("/openapi.json").json()
    paths = set(schema["paths"])

    assert CONTRACT_PATHS <= paths, f"missing: {sorted(CONTRACT_PATHS - paths)}"


def test_openapi_methods_match_the_contract(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/api/campaigns": {"get", "post"},
        "/api/campaigns/{campaign_id}": {"get"},
        "/api/campaigns/{campaign_id}/keys": {"get", "post"},
        "/api/campaigns/{campaign_id}/memory": {"get", "patch"},
        "/api/campaigns/{campaign_id}/objective": {"get", "put"},
        "/api/campaigns/{campaign_id}/inputs": {"post"},
        "/api/campaigns/{campaign_id}/metrics": {"get"},
        "/api/campaigns/{campaign_id}/spend": {"get"},
        "/api/campaigns/{campaign_id}/decisions": {"get"},
        "/api/campaigns/{campaign_id}/questions": {"get"},
        "/api/campaigns/{campaign_id}/questions/{question_id}/answer": {"post"},
        "/api/campaigns/{campaign_id}/control": {"post"},
        "/api/campaigns/{campaign_id}/screen": {"get"},
    }
    for path, methods in expected.items():
        assert methods <= set(paths[path]), path
