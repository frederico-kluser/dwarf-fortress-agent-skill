"""Integration: the narrator writes through the *real* campaign memory wrapper.

The other test modules use a recording fake; this one wires
:class:`~system_one_dwarf.campaign.memory.MemoryStore` (the real wrapper, with
the CoALA CLI replaced by the in-memory fake from ``tests/app/fakes.py``) to the
narrator, so the method names and the record conventions actually line up:

* rolling narrative -> ``semantic`` / ``narrative`` key (``set_narrative``);
* episode           -> ``episodic`` / ``episode`` tag (``add_episode``);
* fact              -> ``semantic`` / ``fact`` tag (``add_fact``);
* procedure         -> ``procedural`` / ``procedure`` tag (``add_procedure``).

Still fully offline: no subprocess, no network, no real SQLite.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

from system_one_dwarf.agent import EPISODE, FACT, PROCEDURE, DeepSeekLLM, Narrator
from system_one_dwarf.campaign.memory import MemoryStore

from .conftest import FAKE_KEY, FakeTransport, narration_payload

NOW = "2026-10-09T12:00:00.000Z"
EVENTS = ["Woke in a clearing.", "A wolf pack is moving north."]


def _load_app_fakes() -> Any:
    """Load ``tests/app/fakes.py`` by path (it is not an importable package)."""
    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "fakes.py"
    spec = importlib.util.spec_from_file_location("agent_test_app_fakes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _store(tmp_path) -> tuple[MemoryStore, Any]:
    fakes = _load_app_fakes()
    cli = fakes.FakeCoalaCli(tmp_path / ".agents" / "camp" / "scripts" / "coala.py")
    return MemoryStore(tmp_path, "camp", cli=cli), cli


def _narrator(store: MemoryStore, narrative: str, record: str) -> Narrator:
    return Narrator(
        DeepSeekLLM(lambda: FAKE_KEY, transport=FakeTransport(narration_payload(narrative, record))),
        store,
    )


def test_episode_update_lands_in_the_campaign_memory(tmp_path):
    store, cli = _store(tmp_path)
    narrator = _narrator(store, "The adventurer reached the river bank.", "Crossed the ford at dusk.")

    result = narrator.consider(
        {"update": 0.92, "effort": "high", "record_type": EPISODE}, EVENTS, "old tail", now=NOW
    )

    assert result.narrative == "The adventurer reached the river bank."
    assert store.narrative() == result.narrative
    episodes = store.episodes()
    assert len(episodes) == 1
    assert episodes[0]["content"] == f"{NOW} — Crossed the ford at dusk."
    assert episodes[0]["kind"] == "episodic"
    assert episodes[0]["ts"].endswith("Z")
    # the calls that actually went through the CLI wrapper: one add per record,
    # then the reads made by the two assertions above
    commands = [call[0] for call in cli.calls]
    assert commands == ["add", "add", "recall", "recall"]


def test_rewriting_the_rolling_line_supersedes_the_previous_one(tmp_path):
    store, cli = _store(tmp_path)
    first = _narrator(store, "Line one about the clearing.", "Slept in the clearing.")
    second = _narrator(store, "Line two: the adventurer reached the river.", "Reached the river.")

    first.consider({"update": 0.9, "effort": "low", "record_type": EPISODE}, EVENTS, "", now=NOW)
    second.consider({"update": 0.9, "effort": "low", "record_type": EPISODE}, EVENTS, "Line one about the clearing.", now=NOW)

    active_narratives = cli.active(kind="semantic", tags="narrative")
    assert len(active_narratives) == 1  # the old line is superseded, not duplicated
    assert active_narratives[0]["content"] == "Line two: the adventurer reached the river."
    assert store.narrative() == "Line two: the adventurer reached the river."


def test_fact_and_procedure_land_in_their_coala_layers(tmp_path):
    store, _cli = _store(tmp_path)
    narrator = _narrator(store, "Chronicle.", "The river ford is shallow at dawn.")

    narrator.consider({"update": 0.9, "effort": "medium", "record_type": FACT}, EVENTS, "tail", now=NOW)
    facts = store.recall(None, kind="semantic", tags="fact")["items"]
    assert [item["content"] for item in facts] == ["The river ford is shallow at dawn."]

    narrator = _narrator(store, "Chronicle.", "Cross a river at dawn, when the ford is shallow.")
    narrator.consider({"update": 0.9, "effort": "medium", "record_type": PROCEDURE}, EVENTS, "tail", now=NOW)
    procedures = store.recall(None, kind="procedural", tags="procedure")["items"]
    assert [item["content"] for item in procedures] == [
        "Cross a river at dawn, when the ford is shallow."
    ]


def test_skip_path_touches_neither_the_model_nor_the_memory(tmp_path):
    store, cli = _store(tmp_path)
    narrator = _narrator(store, "unused", "unused")

    result = narrator.consider({"update": 0.1, "effort": "max", "record_type": EPISODE}, EVENTS, "tail", now=NOW)

    assert result.cost_usd == 0.0
    assert cli.calls == []
    assert store.narrative() == ""