"""``/api/campaigns`` — CRUD, error shapes and the control endpoint."""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
UUID4 = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


def test_create_returns_the_campaign_object(client: TestClient, fake_installer):
    response = client.post(
        "/api/campaigns", json={"name": "Urist McAdventurer", "is_new_game": True}
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "name", "is_new_game", "created_at", "updated_at", "control"}
    assert UUID4.match(body["id"])
    assert body["name"] == "Urist McAdventurer"
    assert body["is_new_game"] is True
    assert ISO_Z.match(body["created_at"]) and ISO_Z.match(body["updated_at"])
    assert body["control"] == {"mode": "autopilot", "kill_switch": False}
    assert fake_installer.calls[0]["name"] == "urist-mcadventurer"


def test_create_defaults_to_a_new_game(client: TestClient):
    assert client.post("/api/campaigns", json={"name": "Fresh"}).json()["is_new_game"] is True


def test_create_requires_a_name(client: TestClient):
    assert client.post("/api/campaigns", json={"name": ""}).status_code == 422
    assert client.post("/api/campaigns", json={}).status_code == 422


def test_create_rejects_unslugifiable_names(client: TestClient):
    response = client.post("/api/campaigns", json={"name": "!!!"})
    assert response.status_code == 422
    assert "slug" in response.json()["detail"]


def test_create_twice_is_the_same_campaign(client: TestClient):
    first = client.post("/api/campaigns", json={"name": "Urist", "is_new_game": True}).json()
    second = client.post("/api/campaigns", json={"name": "Urist", "is_new_game": True}).json()
    assert first["id"] == second["id"]
    assert len(client.get("/api/campaigns").json()) == 1


def test_attach_existing_memory(client: TestClient):
    created = client.post("/api/campaigns", json={"name": "Urist", "is_new_game": True}).json()
    attached = client.post("/api/campaigns", json={"name": "Urist", "is_new_game": False})
    assert attached.status_code == 201
    assert attached.json()["id"] == created["id"]


def test_attach_unknown_campaign_is_404(client: TestClient):
    response = client.post("/api/campaigns", json={"name": "Never Played", "is_new_game": False})
    assert response.status_code == 404
    assert response.json() == {"detail": "campaign not found"}


def test_list_reflects_created_campaigns(client: TestClient):
    assert client.get("/api/campaigns").json() == []
    client.post("/api/campaigns", json={"name": "Urist"})
    client.post("/api/campaigns", json={"name": "Boatmurdered"})

    listed = client.get("/api/campaigns").json()

    assert {item["name"] for item in listed} == {"Urist", "Boatmurdered"}
    assert all(set(item) == {"id", "name", "is_new_game", "created_at", "updated_at", "control"} for item in listed)


def test_get_by_id_and_by_slug(client: TestClient, campaign):
    assert client.get(f"/api/campaigns/{campaign['id']}").json() == campaign
    assert client.get("/api/campaigns/urist-mcadventurer").json() == campaign


def test_get_unknown_campaign_is_404(client: TestClient):
    response = client.get("/api/campaigns/does-not-exist")
    assert response.status_code == 404
    assert response.json() == {"detail": "campaign not found"}


def test_control_persists_and_returns_ts(client: TestClient, campaign):
    response = client.post(
        f"/api/campaigns/{campaign['id']}/control",
        json={"mode": "checkpoint", "kill_switch": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"mode", "kill_switch", "ts"}
    assert body["mode"] == "checkpoint" and body["kill_switch"] is False
    assert ISO_Z.match(body["ts"])
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["control"] == {
        "mode": "checkpoint",
        "kill_switch": False,
    }


def test_control_accepts_all_contract_modes(client: TestClient, campaign):
    for mode in ("autopilot", "checkpoint", "paused"):
        response = client.post(f"/api/campaigns/{campaign['id']}/control", json={"mode": mode})
        assert response.status_code == 200 and response.json()["mode"] == mode


def test_control_rejects_unknown_mode(client: TestClient, campaign):
    response = client.post(f"/api/campaigns/{campaign['id']}/control", json={"mode": "yolo"})
    assert response.status_code == 422


def test_kill_switch_round_trip(client: TestClient, campaign):
    body = client.post(
        f"/api/campaigns/{campaign['id']}/control", json={"mode": "autopilot", "kill_switch": True}
    ).json()
    assert body["kill_switch"] is True
    assert client.get(f"/api/campaigns/{campaign['id']}").json()["control"]["kill_switch"] is True


def test_control_on_unknown_campaign_is_404(client: TestClient):
    assert client.post("/api/campaigns/nope/control", json={"mode": "paused"}).status_code == 404


def test_campaign_state_survives_a_restart(campaigns_root, fake_cli, fake_installer):
    """A second app instance (same root) reloads campaigns from meta.json."""
    from system_one_dwarf.app.main import create_app

    def build():
        return create_app(
            campaigns_root=campaigns_root,
            installer=fake_installer,
            cli_factory=lambda _r, _s: fake_cli,
        )

    with TestClient(build()) as first:
        created = first.post("/api/campaigns", json={"name": "Urist"}).json()
        cid = created["id"]
        first.post(f"/api/campaigns/{cid}/control", json={"mode": "paused"})

    with TestClient(build()) as second:
        reloaded = second.get(f"/api/campaigns/{cid}").json()
        assert reloaded["id"] == cid
        assert reloaded["control"]["mode"] == "paused"
        assert len(second.get("/api/campaigns").json()) == 1

    meta = json.loads((campaigns_root / "urist" / "meta.json").read_text())
    assert meta["mode"] == "paused"
