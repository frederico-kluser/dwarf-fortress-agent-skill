"""``POST /api/campaigns/{id}/inputs`` — free text, translated PT->EN and persisted."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from fakes import FakeTranslator

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_input_returns_201_and_the_contract_shape(client: TestClient, campaign):
    response = client.post(
        f"/api/campaigns/{campaign['id']}/inputs",
        json={"text": "There is a cave to the north with water."},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"id", "text", "ts"}
    assert body["id"] == "in-1"
    assert body["text"] == "There is a cave to the north with water."
    assert ISO_Z.match(body["ts"])


def test_input_ids_increment(client: TestClient, campaign):
    cid = campaign["id"]
    ids = [
        client.post(f"/api/campaigns/{cid}/inputs", json={"text": f"note {n}"}).json()["id"]
        for n in range(3)
    ]
    assert ids == ["in-1", "in-2", "in-3"]


def test_input_is_translated_before_entering_prompts(make_client):
    translator = FakeTranslator({"Há uma caverna a norte que dá água.": "There is a cave to the north with water."})
    client = make_client(translator=translator)
    cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]

    body = client.post(f"/api/campaigns/{cid}/inputs", json={"text": "Há uma caverna a norte que dá água."}).json()

    assert body["text"] == "There is a cave to the north with water."
    assert ("to_english", "Há uma caverna a norte que dá água.") in translator.calls


def test_input_is_written_to_the_campaign_memory(client: TestClient, campaign, fake_cli):
    client.post(f"/api/campaigns/{campaign['id']}/inputs", json={"text": "The bridge is east."})

    stored = [entry for entry in fake_cli.entries if entry["key"] == "input:in-1"]
    assert len(stored) == 1
    assert stored[0]["content"] == "The bridge is east."
    assert stored[0]["tags"] == "user_input"
    assert stored[0]["origin"] == "owner"
    assert stored[0]["type"] == "episodic"


def test_input_survives_a_memory_failure(client: TestClient, campaign, fake_cli):
    fake_cli.fail_on.add("add")
    response = client.post(f"/api/campaigns/{campaign['id']}/inputs", json={"text": "Still accepted."})
    assert response.status_code == 201
    assert response.json()["text"] == "Still accepted."


def test_blank_input_is_rejected(client: TestClient, campaign):
    assert client.post(f"/api/campaigns/{campaign['id']}/inputs", json={"text": ""}).status_code == 422
    assert client.post(f"/api/campaigns/{campaign['id']}/inputs", json={}).status_code == 422


def test_input_on_unknown_campaign_is_404(client: TestClient):
    assert client.post("/api/campaigns/nope/inputs", json={"text": "hello"}).status_code == 404
