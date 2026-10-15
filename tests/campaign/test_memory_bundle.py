"""Memória viva (Onda 2) — Passos 7 + 9 ligados: (a) conversar puxa contexto,
(b) episódios recuperados por importância/relevância (eventos reais da `caca-2`
quando existem), (c) eventos append-only, extrações citáveis e top-K do JEV-R.

Offline: relógio injetado, SQLite em ``tmp_path``, CoALA CLI falso
(``tests/app/fakes.py``). Os eventos REAIS de ``campanhas/caca-2`` são lidos por
cópia — a árvore real nunca é tocada.
"""

from __future__ import annotations

import shutil
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from system_one_dwarf.campaign.bundle import MemoryBundle
from system_one_dwarf.campaign.dialogue import DialogueMemory
from system_one_dwarf.campaign.extraction import ExtractionStore
from system_one_dwarf.campaign.logbook import Logbook
from system_one_dwarf.campaign.memory import EpisodicStore, episode_importance
from system_one_dwarf.campaign.recall import ScoreWeights

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REAL_CACA2 = REPO / "campanhas" / "caca-2" / ".agents" / "caca-2-coala-agent-skill" / "memory" / "coala.sqlite"

MARKER = "** Starting Adventure **"
GREETING = "The lady Tislur Nodvope (to the swordsman Hode Ijamosman): Hello Hode.  Praise be to Nithim."
HINT = ("The lady Tislur Nodvope (to the swordsman Hode Ijamosman): "
        "Seek out the gold of Cobiisman over in the north.")
AGAIN = "The lady Tislur Nodvope (to the swordsman Hode Ijamosman): Hello again Hode."


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


def make_state(lines=(), *, marker: str = MARKER):
    return NS(
        recent_events=tuple(NS(text=t, category=None, game_date=None) for t in ([marker] if marker else []) + list(lines)),
        screen_text="",
        adventurer=NS(name="Usmen Thedlemones"),
        metrics=NS(
            units_nearby=NS(units=()),
            game_clock=NS(year=100, tick=1000, date_text="Granite 3, year 100"),
        ),
    )


def make_bundle(root: Path, clock: FakeClock, *, episodes: EpisodicStore | None = None,
                memory: object = None, **kw) -> MemoryBundle:
    kw.setdefault("auto_extract", False)
    kw.setdefault("auto_project", False)
    return MemoryBundle(
        DialogueMemory.from_campaign_root(root, clock=clock),
        Logbook.from_campaign_root(root, clock=clock),
        ExtractionStore.from_campaign_root(root, clock=clock),
        episodes,
        memory,
        clock=clock,
        **kw,
    )


def make_coala_db(path: Path, rows: list[dict]) -> Path:
    """Uma ``coala.sqlite`` mínima (só o que a camada episódica lê)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE memory_entries (id INTEGER PRIMARY KEY, memory_type TEXT NOT NULL, "
        "content TEXT NOT NULL, origin_class TEXT NOT NULL DEFAULT 'agent', supersession_key TEXT, "
        "superseded_by INTEGER, recorded_at TEXT NOT NULL, valid_from TEXT, valid_until TEXT, "
        "source TEXT, tags TEXT, content_id TEXT, kind TEXT)"
    )
    for i, row in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO memory_entries (id, memory_type, content, recorded_at, source, tags, superseded_by) "
            "VALUES (?, ?, ?, ?, ?, ?, 0)",
            (row.get("id", i), row.get("type", "episodic"), row["content"],
             row.get("ts", "2026-10-10T00:00:00+00:00"), row.get("source"), row.get("tags")),
        )
    conn.commit()
    conn.close()
    return path


# ==================================================== (a) conversa puxa contexto


class TestConversationContext:
    def test_new_conversation_with_the_same_npc_pulls_the_previous_topic(self, tmp_path, clock):
        root = tmp_path / "camp"
        session1 = make_bundle(root, clock)
        first = session1.observe(make_state([GREETING, HINT]))
        assert len(first.dialogue) == 2
        assert first.contexts == {}  # primeiro contacto: não há memória a puxar

        clock.advance(7200)  # a sessão morre; duas horas depois...
        session2 = make_bundle(root, clock)  # ...processo novo, mesma campanha
        again = session2.observe(make_state([AGAIN]))

        assert again.dialogue and again.dialogue[0].interlocutor == "tislur nodvope"
        assert "tislur nodvope" in again.contexts, "a conversa devia puxar contexto automaticamente"
        block = again.contexts["tislur nodvope"]
        assert "Cobiisman" in block, "o tema dito antes devia vir no contexto"
        assert "DATA not instructions" in block

    def test_the_context_can_also_be_pulled_explicitly(self, tmp_path, clock):
        root = tmp_path / "camp"
        session1 = make_bundle(root, clock)
        session1.observe(make_state([GREETING, HINT]))
        clock.advance(7200)
        session2 = make_bundle(root, clock)
        block = session2.context("talking with the lady about the gold", interlocutor="Tislur Nodvope")
        assert "Cobiisman" in block

    def test_ongoing_conversation_does_not_repull(self, tmp_path, clock):
        root = tmp_path / "camp"
        bundle = make_bundle(root, clock)
        bundle.observe(make_state([GREETING, HINT]))
        clock.advance(60)  # 1 minuto: a conversa ainda está a decorrer
        assert bundle.observe(make_state([AGAIN])).contexts == {}

    def test_the_pull_does_not_depend_on_extraction(self, tmp_path, clock):
        root = tmp_path / "camp"
        bundle = make_bundle(root, clock)
        bundle.observe(make_state([GREETING, HINT]))
        clock.advance(7200)
        observed = bundle.observe(make_state([AGAIN]))
        assert "Cobiisman" in observed.contexts["tislur nodvope"]

    def test_observe_kicks_the_async_extraction(self, tmp_path, clock):
        root = tmp_path / "camp"
        bundle = make_bundle(root, clock, auto_extract=True)
        bundle.observe(make_state([HINT]))
        deadline = time.time() + 5.0
        while time.time() < deadline and bundle.extraction.stats()["facts"] == 0:
            time.sleep(0.02)
        assert bundle.extraction.stats()["facts"] >= 1  # extraída em background, sem bloquear

    def test_context_merges_dialogue_facts_events_and_episodes(self, tmp_path, clock):
        root = tmp_path / "camp"
        db = make_coala_db(tmp_path / "coala" / "coala.sqlite", [
            {"content": "Usmen killed a stray mule with his iron spear in Kobisman.",
             "tags": "episode,evento,kill,importance:8", "source": "diario:3"},
        ])
        bundle = make_bundle(root, clock, episodes=EpisodicStore(db, clock=clock))
        bundle.observe(make_state([GREETING, HINT]))
        bundle.logbook.append("kill", "The stray mule has been struck down.", importance=8)
        bundle.extract(background=False)

        block = bundle.context("what do we know about the gold and the mule?", k=3, char_budget=4000)
        assert "[DIALOGUE MEMORY" in block
        assert "[EXTRACTED FACTS" in block and "dialogue:" in block  # extrações citadas
        assert "[LOGBOOK" in block
        assert "[EPISODES" in block


# ================================================ (c) eventos: append-only puro


class TestAppendOnlyEvents:
    def test_an_existing_event_cannot_be_changed_or_deleted(self, tmp_path, clock):
        bundle = make_bundle(tmp_path / "camp", clock)
        bundle.logbook.append("kill", "keep me", importance=8)
        conn = sqlite3.connect(bundle.logbook.db_path)
        for sql in ("UPDATE events SET text = 'tampered'", "DELETE FROM events",
                    "DELETE FROM events WHERE id = 1", "UPDATE events SET importance = 1"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                conn.execute(sql)
        conn.close()
        events = bundle.logbook.recent()
        assert [e.text for e in events] == ["keep me"]

    def test_the_whole_flow_only_appends(self, tmp_path, clock):
        bundle = make_bundle(tmp_path / "camp", clock)
        bundle.observe(make_state([GREETING, HINT]))
        for i in range(12):
            bundle.logbook.append("kill", f"the mule was struck down ({i})", importance=10)
        before = [(e.id, e.text, e.importance) for e in bundle.logbook.recent(100)][::-1]
        assert bundle.reflect() is not None  # reflexão = evento derivado, acrescentado
        after = [(e.id, e.text, e.importance) for e in bundle.logbook.recent(100)][::-1]
        assert after[: len(before)] == before  # nada do que já lá está é reescrito
        assert len(after) == len(before) + 1


# ============================================ (b) episódios: importância/relevância


class TestEpisodicRetrieval:
    def test_importance_separates_the_mule_kill_from_the_bread(self, tmp_path, clock):
        db = make_coala_db(tmp_path / "coala.sqlite", [
            {"content": "The adventurer ate a simple bread ration."},
            {"content": "Usmen killed a stray mule with his iron spear."},
            {"content": "The lady Tislur Nodvope greeted the swordsman."},
        ])
        store = EpisodicStore(db, clock=clock)
        top = store.recall("what happened?", k=1, weights=ScoreWeights(0, 1, 0))[0]
        assert "mule" in top.item.content and top.item.importance >= 8

    def test_relevance_separates_the_topics(self, tmp_path, clock):
        db = make_coala_db(tmp_path / "coala.sqlite", [
            {"content": "The adventurer ate a simple bread ration."},
            {"content": "Usmen killed a stray mule with his iron spear."},
        ])
        store = EpisodicStore(db, clock=clock)
        top = store.recall("who killed the stray mule", k=1, weights=ScoreWeights(0, 0, 1))[0]
        assert "mule" in top.item.content
        top = store.recall("the simple bread ration", k=1, weights=ScoreWeights(0, 0, 1))[0]
        assert "bread" in top.item.content

    def test_the_importance_tag_wins_over_the_text_rules(self):
        assert episode_importance("anything at all", ("episode", "importance:10")) == 10
        assert episode_importance("Usmen killed a stray mule.") == 8
        assert episode_importance("The adventurer ate a simple bread ration.") == 2

    def test_projected_events_keep_their_source_and_importance(self, tmp_path, clock):
        db = make_coala_db(tmp_path / "coala.sqlite", [
            {"content": "[kill 8/10] The stray mule has been struck down.",
             "tags": "episode,evento,kill,importance:8", "source": "diario:7"},
        ])
        store = EpisodicStore(db, clock=clock)
        top = store.recall("struck down", k=1)[0]
        assert top.item.importance == 8  # a etiqueta do diário manda
        assert top.item.source == "diario:7"
        assert top.item.ref == f"coala:{top.item.entry_id}"

    @pytest.mark.skipif(not REAL_CACA2.exists(), reason="sem eventos reais de campanhas/caca-2")
    def test_real_caca2_events_are_retrievable_by_importance_and_relevance(self, tmp_path, clock):
        db = tmp_path / "caca-2.sqlite"
        shutil.copy(REAL_CACA2, db)  # só leitura da árvore real; cópia para o tmp
        store = EpisodicStore(db, clock=clock)
        episodes = store.episodes(limit=10_000)
        assert len(episodes) >= 50, "a caca-2 devia ter episódios reais"

        # relevância: o episódio certo para o tema do elfo
        top = store.recall("the elf Ile Amberraven marching forces threaten the town", k=1,
                           weights=ScoreWeights(0, 0, 1))[0]
        assert "Ile Amberraven" in top.item.content

        # importância: "mata-se uma mula" (8/10) sobe acima das saudações (3/10)
        top = store.recall("", k=1, weights=ScoreWeights(0, 1, 0))[0]
        assert top.item.importance >= 8 and "mule" in top.item.content

        # composto: o abate da mula é o episódio certo para a pergunta do abate
        top = store.recall("Usmen killed a stray mule with his iron spear in Kobisman", k=1)[0]
        assert "mule" in top.item.content and top.item.importance >= 8


# ==================================================== projeção → camada episódica


class TestEpisodicProjection:
    def test_events_are_projected_into_the_coala_layer_cited_and_idempotent(
        self, campaign, manager, fake_cli
    ):
        bundle = manager.bundle(campaign)
        event = bundle.logbook.append("kill", "The stray mule has been struck down.", importance=8)

        assert bundle.project() == 1
        entry = next(e for e in fake_cli.entries if e.get("key") == f"diario:{event.id}")
        assert entry["source"] == f"diario:{event.id}"  # citação evento ↔ episódio
        assert entry["type"] == "episodic"
        assert "episode" in entry["tags"] and "evento" in entry["tags"] and "importance:8" in entry["tags"]

        assert bundle.project() == 0  # marcador: nada de duplicar
        assert len([e for e in fake_cli.entries if e.get("key") == f"diario:{event.id}"]) == 1

    def test_the_manager_wires_dialogue_logbook_extraction_and_episodes(self, campaign, manager):
        bundle = manager.bundle(campaign)
        bundle.dialogue.record("Seek out the gold of Cobiisman over in the north.",
                               speaker="The lady Tislur Nodvope")
        bundle.logbook.append("discovery", "Found a strange altar.", importance=6)
        assert bundle.extract(background=False)
        root = Path(campaign.root)
        assert (root / "dialogos.sqlite").exists()
        assert (root / "diario.sqlite").exists()
        assert (root / "extracoes.sqlite").exists()
        stats = bundle.stats()
        assert stats["dialogue"]["lines"] == 1 and stats["logbook"]["events"] == 1
        assert stats["extraction"]["facts"] >= 1


# ======================================================= top-K composto do JEV-R


class TestJevRetrievalTopK:
    def test_memory_items_fuse_every_store_with_composite_scores(self, tmp_path, clock):
        root = tmp_path / "camp"
        db = make_coala_db(tmp_path / "coala.sqlite", [
            {"content": "Usmen killed a stray mule with his iron spear in Kobisman.",
             "tags": "episode,evento,kill,importance:8", "source": "diario:9"},
        ])
        bundle = make_bundle(root, clock, episodes=EpisodicStore(db, clock=clock))
        bundle.observe(make_state([HINT]))
        bundle.logbook.append("kill", "The stray mule has been struck down in Kobisman.", importance=8)
        bundle.extract(background=False)

        items = bundle.memory_items("the mule in Kobisman", k=10)
        ids = {item["id"].split("-")[0] for item in items}
        assert {"dlg", "log", "fact", "ep"} <= ids, f"faltam lojas no top-K: {ids}"
        scores = [item["score"] for item in items]
        assert scores == sorted(scores, reverse=True)
        for item in items:
            assert {"id", "key", "title", "content", "tags"} <= set(item)
        # citações visíveis no candidato
        assert any("dialogue:" in item["content"] for item in items)

    def test_weights_are_the_retrieval_api(self, tmp_path, clock):
        root = tmp_path / "camp"
        bundle = make_bundle(root, clock)
        clock.t = 1_760_000_000.0
        bundle.logbook.append("system", "early trivial note", importance=1)
        clock.advance(86_400)
        bundle.logbook.append("kill", "the mule was struck down", importance=10)

        items = bundle.memory_items("the mule", k=2, weights={"recency": 1.0, "importance": 0.0, "relevance": 0.0})
        assert "the mule was struck down" in items[0]["content"]  # o mais recente
        items = bundle.memory_items("the mule", k=2, weights={"recency": 0.0, "importance": 1.0, "relevance": 0.0})
        assert "the mule was struck down" in items[0]["content"]  # o mais importante

    def test_top_k_respects_k_and_is_deterministic(self, tmp_path, clock):
        bundle = make_bundle(tmp_path / "camp", clock)
        for i in range(6):
            bundle.logbook.append("system", f"note {i} about the mule", importance=1 + i)
        first = [i["id"] for i in bundle.memory_items("the mule", k=3)]
        again = [i["id"] for i in bundle.memory_items("the mule", k=3)]
        assert first == again and len(first) == 3
