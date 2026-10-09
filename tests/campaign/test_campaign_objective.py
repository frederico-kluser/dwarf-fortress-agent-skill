"""Objective storage: mirror file, memory records, id normalisation, fallback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fakes import FakeCoalaCli, MarkerTranslator
from system_one_dwarf.campaign import MemoryStore, ObjectiveStore, normalize_subgoals


@pytest.fixture
def campaign_dir(tmp_path: Path) -> Path:
    root = tmp_path / "campanhas" / "urist"
    root.mkdir(parents=True)
    return root


@pytest.fixture
def cli() -> FakeCoalaCli:
    return FakeCoalaCli()


@pytest.fixture
def store(campaign_dir: Path, cli: FakeCoalaCli) -> ObjectiveStore:
    memory = MemoryStore(campaign_dir, "urist", cli=cli)
    return ObjectiveStore(campaign_dir, memory=memory)


class TestNormalize:
    def test_generates_sequential_ids(self):
        subgoals = normalize_subgoals([{"text": "Find water"}, {"text": "Cross the river"}])
        assert subgoals == [
            {"id": "sg-1", "text": "Find water", "done": False},
            {"id": "sg-2", "text": "Cross the river", "done": False},
        ]

    def test_keeps_ids_supplied_by_the_client(self):
        assert normalize_subgoals([{"id": "sg-42", "text": "X", "done": True}])[0]["id"] == "sg-42"

    def test_deduplicates_and_drops_empty_text(self):
        subgoals = normalize_subgoals(
            [{"id": "sg-1", "text": "A"}, {"id": "sg-1", "text": "B"}, {"text": "   "}, "plain"]
        )
        assert [s["id"] for s in subgoals] == ["sg-1", "sg-2", "sg-3"]
        assert [s["text"] for s in subgoals] == ["A", "B", "plain"]


class TestRoundTrip:
    def test_get_on_a_fresh_campaign_is_empty(self, store):
        assert store.get() == {"narrative": "", "subgoals": []}

    def test_put_then_get(self, store, campaign_dir):
        saved = store.put(
            "Reach the fortress of Boatmurdered alive.",
            [{"text": "Find water", "done": True}, {"id": "sg-2", "text": "Cross the river"}],
        )

        assert saved == {
            "narrative": "Reach the fortress of Boatmurdered alive.",
            "subgoals": [
                {"id": "sg-1", "text": "Find water", "done": True},
                {"id": "sg-2", "text": "Cross the river", "done": False},
            ],
        }
        assert store.get() == saved
        assert json.loads((campaign_dir / "objetivo.json").read_text()) == saved

    def test_put_replaces_everything(self, store):
        store.put("First.", [{"text": "A"}, {"text": "B"}])
        replaced = store.put("Second.", [{"id": "sg-9", "text": "C"}])
        assert replaced["narrative"] == "Second."
        assert [s["id"] for s in replaced["subgoals"]] == ["sg-9"]
        assert store.get() == replaced

    def test_seed_writes_the_mirror_and_an_index_record(self, store, campaign_dir, cli):
        seeded = store.seed("Urist McAdventurer")

        assert seeded == {"narrative": "", "subgoals": []}
        assert (campaign_dir / "objetivo.json").exists()
        index = [e for e in cli.entries if e["key"] == "objective:index"]
        assert len(index) == 1
        assert "Urist McAdventurer" in index[0]["content"]
        assert index[0]["origin"] == "system"


class TestMemorySync:
    def test_narrative_and_subgoals_are_written_as_records(self, store, cli):
        store.put("Survive.", [{"id": "sg-1", "text": "Find water", "done": True}])

        by_key = {e["key"]: e for e in cli.entries}
        assert by_key["objective:narrative"]["content"] == "Survive."
        assert by_key["objective:narrative"]["tags"] == "objective,objective_narrative"
        assert by_key["objective:subgoal:sg-1"]["content"] == "Subgoal sg-1 (done): Find water"
        assert by_key["objective:subgoal:sg-1"]["tags"] == "objective,subgoal"

    def test_done_flag_flips_the_memory_record(self, store, cli):
        store.put("Survive.", [{"id": "sg-1", "text": "Find water"}])
        store.put("Survive.", [{"id": "sg-1", "text": "Find water", "done": True}])

        active = [e for e in cli.entries if e["key"] == "objective:subgoal:sg-1" and not e["superseded_by"]]
        assert active[-1]["content"] == "Subgoal sg-1 (done): Find water"

    def test_dropped_subgoals_are_marked(self, store, cli):
        store.put("Survive.", [{"id": "sg-1", "text": "Find water"}, {"id": "sg-2", "text": "Cross"}])
        store.put("Survive.", [{"id": "sg-2", "text": "Cross"}])

        dropped = [e for e in cli.entries if e["key"] == "objective:subgoal:sg-1" and not e["superseded_by"]]
        assert dropped[-1]["content"].startswith("Subgoal sg-1 (dropped):")

    def test_memory_failure_keeps_the_mirror(self, campaign_dir, cli):
        cli.fail_on.add("add")
        store = ObjectiveStore(campaign_dir, memory=MemoryStore(campaign_dir, "urist", cli=cli))

        saved = store.put("Still saved.", [{"text": "A"}])

        assert saved["narrative"] == "Still saved."
        assert store.get() == saved

    def test_strict_mode_propagates_memory_failures(self, campaign_dir, cli):
        cli.fail_on.add("add")
        store = ObjectiveStore(
            campaign_dir, memory=MemoryStore(campaign_dir, "urist", cli=cli), strict=True
        )
        with pytest.raises(Exception):
            store.put("Boom.", [])

    def test_translator_is_applied_before_storing(self, campaign_dir, cli):
        memory = MemoryStore(campaign_dir, "urist", cli=cli, translator=MarkerTranslator())
        store = ObjectiveStore(campaign_dir, memory=memory, translator=MarkerTranslator())

        saved = store.put("Chegar vivo à fortaleza.", [{"text": "Encontrar água"}])

        assert saved["narrative"] == "EN<Chegar vivo à fortaleza.>"
        assert saved["subgoals"][0]["text"] == "EN<Encontrar água>"
        stored = {e["key"]: e["content"] for e in cli.entries}
        assert stored["objective:narrative"] == "EN<Chegar vivo à fortaleza.>"
        assert stored["objective:subgoal:sg-1"] == "Subgoal sg-1 (pending): EN<Encontrar água>"


class TestFallback:
    def test_mirror_is_authoritative_when_present(self, store, campaign_dir):
        store.put("From memory.", [{"text": "A"}])
        (campaign_dir / "objetivo.json").write_text(
            json.dumps({"narrative": "Edited by hand.", "subgoals": []})
        )
        assert store.get()["narrative"] == "Edited by hand."

    def test_recovers_from_memory_when_the_mirror_is_gone(self, store, campaign_dir):
        store.put("Survive.", [{"id": "sg-1", "text": "Find water", "done": True}])
        (campaign_dir / "objetivo.json").unlink()

        recovered = store.get()

        assert recovered["narrative"] == "Survive."
        assert recovered["subgoals"][0]["id"] == "sg-1"
        assert recovered["subgoals"][0]["done"] is True
        assert (campaign_dir / "objetivo.json").exists()  # mirror rebuilt

    def test_corrupt_mirror_falls_back_to_memory(self, store, campaign_dir):
        store.put("Survive.", [{"text": "Find water"}])
        (campaign_dir / "objetivo.json").write_text("{broken")

        assert store.get()["narrative"] == "Survive."

    def test_store_without_memory_still_works(self, campaign_dir):
        store = ObjectiveStore(campaign_dir, memory=None)
        assert store.put("No memory.", [{"text": "A"}])["subgoals"][0]["id"] == "sg-1"
        assert store.get()["narrative"] == "No memory."


class TestNoTagCollision:
    """The objective must never shadow the rolling narrative in recall."""

    def test_objective_records_do_not_use_the_narrative_tag(self, campaign_dir, cli):
        memory = MemoryStore(campaign_dir, "urist", cli=cli)
        store = ObjectiveStore(campaign_dir, memory=memory)

        memory.set_narrative("The adventurer wakes in a clearing.")
        store.put("Reach the fortress alive.", [{"text": "Find water"}])

        assert memory.narrative() == "The adventurer wakes in a clearing."
        narrative_records = [e for e in cli.entries if "narrative" in str(e["tags"]).split(",")]
        assert [e["key"] for e in narrative_records] == ["narrative"]
        assert "narrative" not in str(
            [e for e in cli.entries if e["key"] == "objective:narrative"][0]["tags"]
        ).split(",")
