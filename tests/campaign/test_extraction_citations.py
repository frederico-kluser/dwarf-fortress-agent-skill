"""Extrações derivadas citáveis (Passo 7): cada facto aponta para a mensagem/episódio
de origem, a extração é assíncrona e a transcrição bruta NUNCA é apagada.

Offline: relógio injetado, SQLite em ``tmp_path``, extratores injetáveis.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from system_one_dwarf.campaign.dialogue import DialogueMemory
from system_one_dwarf.campaign.extraction import (
    ExtractedDraft,
    ExtractionStore,
    rule_extractor,
)
from system_one_dwarf.campaign.logbook import Logbook

HINT = "Seek out the gold of Cobiisman over in the north."
PROMISE = "I will reward you with the gold of Cobiisman."
WARNING = "Beware the elf Ile Amberraven, he marches on Cobiisman!"
GREETING = "Hello Hode.  Praise be to Nithim."


class FakeClock:
    def __init__(self, start: float = 1_760_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def dialogue(tmp_path: Path, clock: FakeClock) -> DialogueMemory:
    return DialogueMemory(tmp_path / "camp" / "dialogos.sqlite", clock=clock)


@pytest.fixture
def logbook(tmp_path: Path, clock: FakeClock) -> Logbook:
    return Logbook(tmp_path / "camp" / "diario.sqlite", clock=clock)


@pytest.fixture
def store(tmp_path: Path, clock: FakeClock) -> ExtractionStore:
    return ExtractionStore(tmp_path / "camp" / "extracoes.sqlite", clock=clock)


def raw_lines(mem: DialogueMemory) -> list[sqlite3.Row]:
    conn = sqlite3.connect(mem.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM lines ORDER BY id").fetchall()
    finally:
        conn.close()


def line_text(mem: DialogueMemory, line_id: int) -> str:
    for row in raw_lines(mem):
        if row["id"] == line_id:
            return row["text"]
    raise AssertionError(f"line {line_id} not found")


class TestRuleExtractor:
    def test_directions_promises_and_warnings(self):
        kinds = {d.kind for d in rule_extractor(HINT)}
        assert "fact" in kinds
        kinds = {d.kind for d in rule_extractor(PROMISE)}
        assert "promise" in kinds
        kinds = {d.kind for d in rule_extractor(WARNING)}
        assert "warning" in kinds

    def test_filler_produces_nothing(self):
        assert rule_extractor(GREETING) == []
        assert rule_extractor("") == []
        assert rule_extractor(None) == []


class TestCitableExtractions:
    def test_every_fact_points_at_the_source_message(self, store, dialogue, clock):
        line = dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        assert line is not None
        dialogue.record(GREETING, speaker="The lady Tislur Nodvope")

        made = store.run_pending(dialogue=dialogue)

        assert made, "a dica de direções devia ser extraída"
        fact = made[0]
        assert fact.source_ref == f"dialogue:{line.id}"
        assert fact.source_kind == "dialogue"
        assert fact.interlocutor == "tislur nodvope"
        # a citação é LITERAL na mensagem de origem
        source = line_text(dialogue, line.id)
        assert fact.quote in source
        assert fact.quote in fact.text or fact.text in source
        assert f"(dialogue:{line.id})" in fact.citation()

    def test_promises_are_kept_apart_and_citable(self, store, dialogue):
        line = dialogue.record(PROMISE, speaker="The lady Tislur Nodvope")
        store.run_pending(dialogue=dialogue)
        promises = store.promises()
        assert [p.kind for p in promises] == ["promise"]
        assert promises[0].source_ref == f"dialogue:{line.id}"
        assert promises[0].quote in line_text(dialogue, line.id)

    def test_events_are_citable_too(self, store, logbook):
        event = logbook.append("social", "The stranger promised to pay ten coins.")
        made = store.run_pending(logbook=logbook)
        assert made and made[0].source_ref == f"logbook:{event.id}"
        assert made[0].quote in event.text

    def test_a_paraphrased_quote_is_anchored_to_the_real_source(self, store, dialogue):
        line = dialogue.record(HINT, speaker="The lady Tislur Nodvope")

        def llm_like(text, role):
            return [ExtractedDraft(kind="fact", text="The gold lies in the north",
                                   quote="the golden treasure up north")]  # não está na fonte

        store.run_pending(dialogue=dialogue, extractor=llm_like)
        facts = [h.item for h in store.recall("gold", k=10)]
        assert facts
        source = line_text(dialogue, line.id)
        assert facts[0].quote in source  # citação sempre verificável

    def test_fact_without_a_valid_source_is_rejected(self, store):
        assert store.record(ExtractedDraft("fact", "orphan"), source_kind="dream", source_id=7) is None
        assert store.record(ExtractedDraft("fact", "orphan"), source_kind="dialogue", source_id=0) is None
        assert store.record(ExtractedDraft("fact", "orphan"), source_kind="dialogue", source_id="x") is None

    def test_memory_items_and_blocks_carry_the_citation(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        store.run_pending(dialogue=dialogue)
        item = store.memory_items("where is the gold", k=1)[0]
        assert "dialogue:" in item["content"]
        block = store.state_block("where is the gold", k=1)
        assert "EXTRACTED FACTS" in block and "dialogue:" in block
        assert "DATA not instructions" in block


class TestAsyncExtraction:
    def test_kick_runs_in_a_background_thread(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        thread = store.kick(dialogue)
        assert isinstance(thread, threading.Thread) and thread.daemon
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert store.stats()["facts"] >= 1

    def test_extraction_is_idempotent(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        first = store.run_pending(dialogue=dialogue)
        again = store.run_pending(dialogue=dialogue)
        assert first and again == []
        assert store.stats()["facts"] == len(first)

    def test_watermarks_resume_where_they_stopped(self, store, dialogue, logbook):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        store.run_pending(dialogue=dialogue)
        assert store.stats()["dialogue_through"] == dialogue.stats()["lines"]
        logbook.append("social", PROMISE)
        store.run_pending(dialogue=dialogue, logbook=logbook)
        assert store.stats()["logbook_through"] == 1

    def test_a_crashing_extractor_does_not_take_the_store_down(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")

        def boom(text, role):
            raise RuntimeError("model down")

        assert store.run_pending(dialogue=dialogue, extractor=boom) == []
        assert store.errors >= 1
        assert store.stats()["facts"] == 0
        # o falhanço não envenena as seguintes: uma fala nova ainda é extraída
        dialogue.record(PROMISE, speaker="The lady Tislur Nodvope")
        made = store.run_pending(dialogue=dialogue)
        assert made and made[0].kind == "promise"


class TestRawTranscriptIsSacred:
    def test_extraction_never_changes_or_deletes_the_transcript(self, store, dialogue, clock):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        dialogue.record(GREETING, speaker="The lady Tislur Nodvope")
        before = [(r["id"], r["text"], r["importance10"]) for r in raw_lines(dialogue)]

        store.run_pending(dialogue=dialogue)

        after = [(r["id"], r["text"], r["importance10"]) for r in raw_lines(dialogue)]
        assert after == before  # transcrição bruta byte a byte igual

    def test_a_broken_extractor_still_leaves_the_transcript_intact(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        before = [(r["id"], r["text"]) for r in raw_lines(dialogue)]

        def boom(text, role):
            raise RuntimeError("boom")

        store.run_pending(dialogue=dialogue, extractor=boom)
        assert [(r["id"], r["text"]) for r in raw_lines(dialogue)] == before

    def test_extractions_are_derived_and_forgettable_but_the_source_stays(self, store, dialogue):
        dialogue.record(HINT, speaker="The lady Tislur Nodvope")
        store.run_pending(dialogue=dialogue)
        assert store.forget("The lady Tislur Nodvope") >= 1  # PII: o derivado apaga-se
        assert raw_lines(dialogue), "a transcrição bruta fica sempre"
