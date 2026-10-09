"""``/api/campaigns/{id}/objective`` — PUT/GET round trip and subgoal handling."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from fakes import MarkerTranslator

OBJECTIVE_KEYS = {"narrative", "subgoals"}
SUBGOAL_KEYS = {"id", "text", "done"}


def test_fresh_campaign_has_an_empty_objective(client: TestClient, campaign):
    response = client.get(f"/api/campaigns/{campaign['id']}/objective")

    assert response.status_code == 200
    assert response.json() == {"narrative": "", "subgoals": []}


def test_put_then_get_round_trip(client: TestClient, campaign):
    payload = {
        "narrative": "Reach the fortress of Boatmurdered alive.",
        "subgoals": [
            {"id": "sg-1", "text": "Find water", "done": True},
            {"id": "sg-2", "text": "Cross the river", "done": False},
        ],
    }

    put = client.put(f"/api/campaigns/{campaign['id']}/objective", json=payload)

    assert put.status_code == 200
    assert put.json() == payload
    assert client.get(f"/api/campaigns/{campaign['id']}/objective").json() == payload


def test_subgoal_ids_are_generated_when_missing(client: TestClient, campaign):
    body = client.put(
        f"/api/campaigns/{campaign['id']}/objective",
        json={"narrative": "Survive.", "subgoals": [{"text": "Find water"}, {"text": "Sleep"}]},
    ).json()

    assert [s["id"] for s in body["subgoals"]] == ["sg-1", "sg-2"]
    assert all(set(s) == SUBGOAL_KEYS for s in body["subgoals"])
    assert set(body) == OBJECTIVE_KEYS


def test_put_replaces_the_whole_objective(client: TestClient, campaign):
    cid = campaign["id"]
    client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "First.", "subgoals": [{"text": "A"}, {"text": "B"}]},
    )

    body = client.put(
        f"/api/campaigns/{cid}/objective", json={"narrative": "Second.", "subgoals": []}
    ).json()

    assert body == {"narrative": "Second.", "subgoals": []}
    assert client.get(f"/api/campaigns/{cid}/objective").json() == body


def test_objective_is_persisted_outside_the_memory_client(client: TestClient, campaign, campaigns_root):
    cid = campaign["id"]
    client.put(f"/api/campaigns/{cid}/objective", json={"narrative": "Persisted.", "subgoals": []})

    mirror = campaigns_root / "urist-mcadventurer" / "objetivo.json"
    assert json.loads(mirror.read_text()) == {"narrative": "Persisted.", "subgoals": []}


def test_objective_is_written_into_the_campaign_memory(client: TestClient, campaign, fake_cli):
    client.put(
        f"/api/campaigns/{campaign['id']}/objective",
        json={"narrative": "Reach the fortress alive.", "subgoals": [{"text": "Find water", "done": True}]},
    )

    by_key = {entry["key"]: entry["content"] for entry in fake_cli.entries}
    assert by_key["objective:narrative"] == "Reach the fortress alive."
    assert by_key["objective:subgoal:sg-1"] == "Subgoal sg-1 (done): Find water"


def test_objective_requires_a_body(client: TestClient, campaign):
    assert client.put(f"/api/campaigns/{campaign['id']}/objective", json={}).status_code == 200
    assert (
        client.put(f"/api/campaigns/{campaign['id']}/objective", json={"narrative": "x", "subgoals": "nope"})
        .status_code
        == 422
    )


def test_objective_translates_pt_to_en(make_client):
    client = make_client(translator=MarkerTranslator())
    cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]

    body = client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "Chegar vivo.", "subgoals": [{"text": "Encontrar água"}]},
    ).json()

    assert body["narrative"] == "EN<Chegar vivo.>"
    assert body["subgoals"][0]["text"] == "EN<Encontrar água>"


def test_objective_on_unknown_campaign_is_404(client: TestClient):
    assert client.get("/api/campaigns/nope/objective").status_code == 404
    assert client.put("/api/campaigns/nope/objective", json={"narrative": "x"}).status_code == 404
