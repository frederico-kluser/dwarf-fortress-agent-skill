"""Opt-in integration test against the REAL CoALA installer + CLI.

Offline by default: it is skipped unless ``SOD_RUN_INTEGRATION=1`` **and** the
installer exists, so ``pytest tests/app tests/campaign -q`` never shells out.

Run it explicitly with::

    SOD_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q

Everything happens inside ``tmp_path`` — the real ``campanhas/`` tree is never
touched.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from system_one_dwarf.campaign import CampaignManager, CoalaInstaller
from system_one_dwarf.campaign.paths import coala_installer_path, memory_db

INSTALLER = coala_installer_path()
ENABLED = os.environ.get("SOD_RUN_INTEGRATION") == "1"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not ENABLED, reason="set SOD_RUN_INTEGRATION=1 to run the real installer"),
    pytest.mark.skipif(not INSTALLER.exists(), reason=f"CoALA installer missing at {INSTALLER}"),
]


def test_real_installer_creates_a_working_campaign_memory(tmp_path: Path):
    manager = CampaignManager(root=tmp_path / "campanhas", installer=CoalaInstaller(INSTALLER))

    campaign, created = manager.create("Integration Urist", True)

    assert created is True
    root = tmp_path / "campanhas" / "integration-urist"
    assert root.is_dir()
    db = memory_db(root, "integration-urist")
    assert db.exists() and db.stat().st_size > 0
    assert (root / "meta.json").exists()

    memory = manager.memory(campaign)
    memory.add_episode("Climbed the hill without incident.", key="ep-it-1")
    memory.add_fact("The river is to the north.", key="fact-it-river")

    episodes = memory.episodes()
    assert any("Climbed the hill" in e["content"] for e in episodes)
    assert memory.narrative() == ""

    memory.set_narrative("The adventurer wakes in a clearing.")
    assert memory.narrative() == "The adventurer wakes in a clearing."

    hits = memory.search("river", limit=3)
    assert hits and any("river" in hit["content"] for hit in hits)

    objective = manager.objective(campaign)
    saved = objective.put("Reach the fortress alive.", [{"text": "Find water", "done": True}])
    assert saved["subgoals"][0]["id"] == "sg-1"
    assert objective.get() == saved

    # attach to the same campaign without wiping anything
    attached, created_again = manager.create("Integration Urist", False)
    assert created_again is False and attached.id == campaign.id
    assert manager.memory(attached).narrative() == "The adventurer wakes in a clearing."
