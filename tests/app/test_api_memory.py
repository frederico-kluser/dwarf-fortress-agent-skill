"""``/api/campaigns/{id}/memory`` — contract shape, PT->EN on PATCH, 503 on a broken CLI."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from fakes import ExplodingTranslator, MarkerTranslator
from system_one_dwarf.campaign import CoalaCli

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

MEMORY_KEYS = {"narrative", "narrative_pt", "episodes", "working_memory", "updated_at"}
EPISODE_KEYS = {"id", "ts", "kind", "content", "content_pt"}


def test_memory_shape_on_a_fresh_campaign(client: TestClient, campaign):
    response = client.get(f"/api/campaigns/{campaign['id']}/memory")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == MEMORY_KEYS
    assert body["narrative"] == ""
    assert body["narrative_pt"] == ""
    assert body["episodes"] == []
    # the fresh-game seed record is the only thing in working memory
    assert isinstance(body["working_memory"], str)
    assert "Objective initialized" in body["working_memory"]
    assert ISO_Z.match(body["updated_at"])


def test_memory_reflects_episodes_written_by_the_agent_loop(client: TestClient, campaign, fake_cli):
    fake_cli.run(["add", "--type", "episodic", "--content", "Climbed the hill without incident.",
                  "--key", "ep-001", "--tags", "episode", "--origin", "agent"])

    body = client.get(f"/api/campaigns/{campaign['id']}/memory").json()

    assert len(body["episodes"]) == 1
    episode = body["episodes"][0]
    assert set(episode) == EPISODE_KEYS
    assert episode["kind"] == "episodic"
    assert episode["content"] == "Climbed the hill without incident."
    assert episode["content_pt"] == "Climbed the hill without incident."
    assert ISO_Z.match(episode["ts"])
    assert episode["id"].isdigit()
    assert "Climbed the hill" in body["working_memory"]


def test_patch_narrative_stores_it_canonically(client: TestClient, campaign, fake_cli):
    response = client.patch(
        f"/api/campaigns/{campaign['id']}/memory",
        json={"narrative": "The adventurer wakes in a clearing."},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["narrative"] == "The adventurer wakes in a clearing."
    assert body["narrative_pt"] == "The adventurer wakes in a clearing."
    assert set(body) == MEMORY_KEYS
    stored = [e for e in fake_cli.entries if e["key"] == "narrative"][0]
    assert stored["content"] == "The adventurer wakes in a clearing."
    assert stored["origin"] == "owner"


def test_patch_memory_translates_pt_to_en_before_storing(make_client, campaigns_root):
    client = make_client(translator=MarkerTranslator())
    created = client.post("/api/campaigns", json={"name": "Urist"}).json()

    body = client.patch(
        f"/api/campaigns/{created['id']}/memory",
        json={"narrative": "O aventureiro acorda numa clareira."},
    ).json()

    assert body["narrative"] == "EN<O aventureiro acorda numa clareira.>"
    assert body["narrative_pt"] == "PT<EN<O aventureiro acorda numa clareira.>>"


def test_broken_translator_does_not_break_the_editor(make_client):
    client = make_client(translator=ExplodingTranslator())
    created = client.post("/api/campaigns", json={"name": "Urist"}).json()

    body = client.patch(
        f"/api/campaigns/{created['id']}/memory", json={"narrative": "Keep me as is."}
    ).json()

    assert body["narrative"] == "Keep me as is."


def test_patch_appends_new_episodes_and_edits_existing_ones(client: TestClient, campaign, fake_cli):
    fake_cli.run(["add", "--type", "episodic", "--content", "Original text.", "--key", "ep-1",
                  "--tags", "episode"])

    created = client.patch(
        f"/api/campaigns/{campaign['id']}/memory",
        json={"episodes": [{"content": "A brand new episode."}]},
    ).json()
    assert {e["content"] for e in created["episodes"]} == {"Original text.", "A brand new episode."}

    original_id = [e["id"] for e in created["episodes"] if e["content"] == "Original text."][0]
    edited = client.patch(
        f"/api/campaigns/{campaign['id']}/memory",
        json={"episodes": [{"id": original_id, "content": "Rewritten episode."}]},
    ).json()

    assert {e["content"] for e in edited["episodes"]} == {"Rewritten episode.", "A brand new episode."}
    assert [c[0] for c in fake_cli.calls].count("supersede") == 1


def test_patch_ignores_blank_episode_content(client: TestClient, campaign):
    body = client.patch(
        f"/api/campaigns/{campaign['id']}/memory", json={"episodes": [{"content": "   "}]}
    ).json()
    assert body["episodes"] == []


def test_empty_patch_is_a_no_op(client: TestClient, campaign):
    body = client.patch(f"/api/campaigns/{campaign['id']}/memory", json={}).json()
    assert body["narrative"] == "" and body["episodes"] == []


def test_memory_on_unknown_campaign_is_404(client: TestClient):
    assert client.get("/api/campaigns/nope/memory").status_code == 404
    assert client.patch("/api/campaigns/nope/memory", json={"narrative": "x"}).status_code == 404


def test_missing_coala_cli_is_a_clear_503(make_client, tmp_path: Path):
    """A campaign whose CoALA install is broken fails loudly, not silently."""
    client = make_client(cli=CoalaCli(tmp_path / "gone" / "scripts" / "coala.py"))
    created = client.post("/api/campaigns", json={"name": "Urist"}).json()

    response = client.get(f"/api/campaigns/{created['id']}/memory")

    assert response.status_code == 503
    assert "gone" in response.json()["detail"]
