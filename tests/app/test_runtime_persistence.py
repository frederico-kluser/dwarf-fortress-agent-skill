"""Persistência dos stores do runtime (ARQUITETURA §9 · PROBLEMAS H3).

``decisions``/``questions``/``approvals``/``inputs`` eram anéis em memória: um
restart do backend apagava a trilha de auditoria, as aprovações pendentes (e uma
já *libertada* podia libertar a ação outra vez) e os contadores de ids.  O
``RuntimeStore`` (``runtime.sqlite``) guarda-os com write-through e o
``CampaignRuntime`` recarrega-os ao nascer — sem mudar caps, ordem, paginação,
dedupe nem a API pública.

Três camadas, todas offline (``tmp_path``, sem sockets, sem esperas reais):

* ``RuntimeStore`` isolado — formato, retenção, segredos, corrupção, threads;
* ``CampaignRuntime`` + store — write-through, "restart", contadores, dedupe;
* ``AppServices``/API — dois apps sobre a mesma raiz de campanhas.
"""

from __future__ import annotations

import json
import sqlite3
import stat
import threading
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest

import system_one_dwarf.app.persist as persist_module
import system_one_dwarf.app.runtime as runtime_module
from fakes_loop import approval_question, hitl_question
from system_one_dwarf.app.approval_flow import ApprovalChecker
from system_one_dwarf.app.persist import (
    MAX_PAYLOAD_BYTES,
    MAX_STRING_CHARS,
    OP_DROP,
    OP_SAVE,
    RUNTIME_DB_NAME,
    RuntimeSnapshot,
    RuntimeStore,
    TrackedRecord,
    dump_payload,
    runtime_db_path,
    scrub_payload,
)
from system_one_dwarf.app.runtime import CampaignRuntime
from system_one_dwarf.campaign.keys import mask_key

LIVE_KEY = "sk-or-v1-" + "a1b2c3d4" * 4
T0 = "2026-10-10T10:00:00.000Z"


def ts(n: int) -> str:
    return f"2026-10-10T10:{n // 60:02d}:{n % 60:02d}.000Z"


def db_path(tmp_path: Path) -> Path:
    return runtime_db_path(tmp_path / "campanha")


def new_store(tmp_path: Path, **kwargs) -> RuntimeStore:
    return RuntimeStore(db_path(tmp_path), **kwargs)


def new_runtime(tmp_path: Path, **kwargs) -> tuple[CampaignRuntime, RuntimeStore]:
    """Um runtime ligado a um store novo (= um processo novo sobre o mesmo ficheiro)."""
    store = new_store(tmp_path, **kwargs)
    return CampaignRuntime("c-1", store=store), store


def raw_bytes(tmp_path: Path) -> bytes:
    """Todo o conteúdo em disco do store (ficheiro principal + WAL + shm)."""
    base = db_path(tmp_path)
    return b"".join(
        Path(f"{base}{suffix}").read_bytes()
        for suffix in ("", "-wal", "-shm") if Path(f"{base}{suffix}").exists()
    )


def view(runtime: CampaignRuntime) -> dict:
    """O que a API mostra do runtime, normalizado para JSON (comparável entre runs)."""
    return json.loads(json.dumps({
        "decisions": runtime.list_decisions(),
        "questions": runtime.list_questions(),
        "approvals": runtime.list_approvals(status=None),
        "inputs": runtime.list_inputs(),
    }))


def decision(n: int, agent: str = "JEV-A", **extra) -> dict:
    return {"ts": ts(n), "agent": agent, "request_summary": f"step {n}",
            "answers": {"action": f"goto:spot_{n}"}, "probabilities": None,
            "confidence": 0.7, "band": "hitl", "latency_ms": 10, "cost_usd": 0.0001, **extra}


# ===================================================================== o store


class TestRuntimeStore:
    def test_creates_a_private_file_and_the_schema(self, tmp_path):
        store = new_store(tmp_path)

        assert store.save("decisions", 1, {"id": "d-1", "ts": T0, "agent": "JEV-A"}) is True

        path = store.path
        assert path.name == RUNTIME_DB_NAME
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        for candidate in path.parent.glob(f"{RUNTIME_DB_NAME}*"):
            assert stat.S_IMODE(candidate.stat().st_mode) == 0o600, candidate.name
        with closing(sqlite3.connect(path)) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'")}
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        assert {"decisions", "questions", "approvals", "inputs", "meta"} <= tables
        assert mode == "wal"
        assert version[0] == "1"

    def test_records_round_trip_through_a_second_connection(self, tmp_path):
        writer = new_store(tmp_path)
        writer.save("questions", "q-b", {"id": "q-b", "ts": ts(2), "status": "pending", "question": "b?"})
        writer.save("questions", "q-a", {"id": "q-a", "ts": ts(1), "status": "answered", "question": "a?"})
        writer.save("approvals", "q-b", {"id": "q-b", "ts": ts(2), "status": "pending"})
        writer.save("inputs", "in-1", {"id": "in-1", "ts": ts(3), "text": "hello"})

        snap = new_store(tmp_path).load_snapshot()  # outra ligação = outro processo

        assert isinstance(snap, RuntimeSnapshot)
        # ordem de inserção (seq), não de ts: é ela que reconstrói o anel
        assert [q["id"] for q in snap.questions] == ["q-b", "q-a"]
        assert [a["id"] for a in snap.approvals] == ["q-b"]
        assert snap.inputs[0]["text"] == "hello"
        assert snap.empty is False

    def test_load_recent_returns_the_newest_rows_of_every_table(self, tmp_path):
        store = new_store(tmp_path)
        store.save("questions", "q-old", {"id": "q-old", "ts": ts(0), "status": "pending"})
        for n in range(1, 8):
            store.save("decisions", n, {"id": f"d-{n}", "ts": ts(n)})
            store.save("inputs", f"in-{n}", {"id": f"in-{n}", "ts": ts(n), "text": "x"})
            store.save("questions", f"q-{n}", {"id": f"q-{n}", "ts": ts(n), "status": "answered"})

        recent = store.load_recent(3)

        assert [seq for seq, _ in recent.decisions] == [5, 6, 7]
        assert [i["id"] for i in recent.inputs] == ["in-5", "in-6", "in-7"]
        # a fila viva (pendente) entra sempre, mesmo sendo a mais antiga
        assert [q["id"] for q in recent.questions] == ["q-old", "q-6", "q-7"]
        assert [q["id"] for q in store.read("questions", 2)] == ["q-6", "q-7"]
        assert [q["id"] for q in store.read("questions", 10, before=ts(3))] == ["q-old", "q-1", "q-2"]

    def test_an_upsert_updates_in_place_and_keeps_the_ring_order(self, tmp_path):
        store = new_store(tmp_path)
        store.save("questions", "q-1", {"id": "q-1", "ts": ts(1), "status": "pending"})
        store.save("questions", "q-2", {"id": "q-2", "ts": ts(2), "status": "pending"})

        store.save("questions", "q-1", {"id": "q-1", "ts": ts(1), "status": "answered", "answer_text": "sim"})

        loaded = store.read("questions")
        assert [(q["id"], q["status"]) for q in loaded] == [("q-1", "answered"), ("q-2", "pending")]
        assert store.count("questions") == 2

    def test_decisions_are_keyed_by_seq_so_duplicate_ids_are_all_kept(self, tmp_path):
        store = new_store(tmp_path)
        for seq in (1, 2, 3):
            store.save("decisions", seq, {"id": "d-same", "ts": ts(seq), "agent": "JEV-A"})

        snap = store.load_snapshot()

        assert [seq for seq, _ in snap.decisions] == [1, 2, 3]
        assert snap.counters["decisions"] == 3

    def test_the_snapshot_loads_every_pending_entry_then_fills_with_recent_history(self, tmp_path):
        store = new_store(tmp_path)
        for n in range(1, 11):  # q-1..q-10; q-2, q-5, q-9 ficam pendentes
            status = "pending" if n in (2, 5, 9) else "answered"
            store.save("questions", f"q-{n}", {"id": f"q-{n}", "ts": ts(n), "status": status})

        snap = store.load_snapshot(questions=5)

        # as 3 pendentes (a fila viva do humano) + as 2 respondidas mais recentes
        assert [q["id"] for q in snap.questions] == ["q-2", "q-5", "q-8", "q-9", "q-10"]

    def test_id_counters_come_back_as_the_highest_issued_number(self, tmp_path):
        store = new_store(tmp_path)
        store.save("questions", "q-7", {"id": "q-7", "ts": T0, "status": "pending"})
        store.save("questions", "q-3-1", {"id": "q-3-1", "ts": T0, "status": "pending"})  # do loop
        store.save("inputs", "in-12", {"id": "in-12", "ts": T0, "text": "x"})
        store.save("decisions", 40, {"id": "d-9", "ts": T0})

        counters = store.load_snapshot().counters

        assert counters == {"decisions": 40, "questions": 7, "inputs": 12}

    # ------------------------------------------------------------- retenção

    def test_retention_keeps_the_newest_rows_and_never_prunes_pending_ones(self, tmp_path):
        store = new_store(
            tmp_path, prune_every=1,
            retention={"decisions": 5, "questions": 3, "approvals": 2, "inputs": 4},
        )
        for n in range(1, 13):
            store.save("decisions", n, {"id": f"d-{n}", "ts": ts(n)})
        for n in range(1, 10):  # q-1, q-4, q-7 pendentes; as outras respondidas
            status = "pending" if n % 3 == 1 else "answered"
            store.save("questions", f"q-{n}", {"id": f"q-{n}", "ts": ts(n), "status": status})
        for n in range(1, 6):
            store.save("approvals", f"a-{n}", {"id": f"a-{n}", "ts": ts(n), "status": "approved"})
        store.save("approvals", "a-pending", {"id": "a-pending", "ts": ts(0), "status": "pending"})
        for n in range(1, 8):
            store.save("inputs", f"in-{n}", {"id": f"in-{n}", "ts": ts(n), "text": "x"})

        snap = store.load_snapshot()

        assert [seq for seq, _ in snap.decisions] == [8, 9, 10, 11, 12]
        pending = [q["id"] for q in snap.questions if q["status"] == "pending"]
        answered = [q["id"] for q in snap.questions if q["status"] == "answered"]
        assert pending == ["q-1", "q-4", "q-7"]          # as pendentes sobrevivem sempre
        assert answered == ["q-6", "q-8", "q-9"]         # só as 3 respondidas mais recentes
        assert "a-pending" in [a["id"] for a in snap.approvals]
        assert [a["id"] for a in snap.approvals if a["status"] == "approved"] == ["a-4", "a-5"]
        assert [i["id"] for i in snap.inputs] == ["in-4", "in-5", "in-6", "in-7"]

    def test_the_default_retention_is_fifty_thousand_decisions_and_twenty_thousand_questions(
        self, tmp_path
    ):
        store = new_store(tmp_path)

        assert store.retention["decisions"] == 50_000
        assert store.retention["questions"] == 20_000
        assert store.retention["approvals"] >= 20_000

    def test_dropped_records_are_tombstoned_audited_and_never_reloaded(self, tmp_path):
        store = new_store(tmp_path)
        store.save("approvals", "a-1", {"id": "a-1", "ts": T0, "status": "pending"})
        store.save("approvals", "a-2", {"id": "a-2", "ts": T0, "status": "pending"})

        assert store.drop("approvals", "a-1") is True

        assert [a["id"] for a in store.load_snapshot().approvals] == ["a-2"]
        assert store.read("approvals") == [
            {"id": "a-2", "ts": T0, "status": "pending"}]
        assert store.count("approvals") == 2                       # a linha continua (auditoria)
        assert store.count("approvals", status="dropped") == 1

    def test_a_tombstone_does_not_reach_decisions_or_inputs(self, tmp_path):
        store = new_store(tmp_path)
        store.save("decisions", 1, {"id": "d-1", "ts": T0})

        assert store.drop("decisions", 1) is False
        assert store.count("decisions") == 1

    # ------------------------------------------------------------- segredos

    def test_secrets_never_reach_the_file(self, tmp_path):
        literal = "plain-live-key-without-a-known-prefix-0001"
        store = new_store(tmp_path, secrets=[literal])
        payload = {
            "id": "q-1", "ts": T0, "status": "pending",
            "question": f"my key is {LIVE_KEY}",
            "options": [{"id": "a", "text": f"use {LIVE_KEY} now"}],
            "source_data": {
                "api_key": "field-secret-value-123456",
                "headers": {"Authorization": f"Bearer {LIVE_KEY}"},
                "context": f"the campaign key {literal} must not leak",
                "values": [LIVE_KEY, 5, None],
                "input_tokens": 1234,  # contagem, não segredo: fica
            },
        }

        store.save("questions", "q-1", payload)
        store.close()

        blob = raw_bytes(tmp_path)
        for leaked in (LIVE_KEY, literal, "field-secret-value-123456"):
            assert leaked.encode() not in blob, leaked
        loaded = new_store(tmp_path).read("questions")[0]
        assert loaded["question"] == f"my key is {mask_key(LIVE_KEY)}"
        assert loaded["source_data"]["api_key"] == mask_key("field-secret-value-123456")
        # campo com nome de segredo: o valor inteiro é mascarado (nunca fica parte dele)
        assert loaded["source_data"]["headers"]["Authorization"] == mask_key(f"Bearer {LIVE_KEY}")
        assert loaded["source_data"]["context"] == f"the campaign key {mask_key(literal)} must not leak"
        assert loaded["source_data"]["input_tokens"] == 1234
        assert loaded["source_data"]["values"] == [mask_key(LIVE_KEY), 5, None]

    def test_scrub_uses_the_same_mask_as_the_key_store(self):
        masked = scrub_payload({"text": f"k={LIVE_KEY}"})

        assert masked == {"text": f"k={mask_key(LIVE_KEY)}"}
        assert scrub_payload({"api_key": mask_key(LIVE_KEY)}) == {"api_key": mask_key(LIVE_KEY)}

    def test_scrub_does_not_touch_ordinary_game_text(self):
        text = "The desk-clerk-of-the-task-force says: sk-ill is a skill, not a key."

        assert scrub_payload({"t": text}) == {"t": text}

    def test_the_live_secrets_callable_is_cached_and_failures_are_survived(self, tmp_path):
        calls = []

        def secrets():
            calls.append(1)
            if len(calls) == 1:
                return ["first-live-secret-0001"]
            raise RuntimeError("key store exploded")

        clock = [0.0]
        store = new_store(tmp_path, secrets=secrets, monotonic=lambda: clock[0])
        store.save("inputs", "in-1", {"id": "in-1", "ts": T0, "text": "a first-live-secret-0001 b"})
        store.save("inputs", "in-2", {"id": "in-2", "ts": T0, "text": "c first-live-secret-0001 d"})
        assert len(calls) == 1  # lida uma vez dentro da janela de cache

        clock[0] += persist_module.SECRETS_REFRESH_S + 1
        store.save("inputs", "in-3", {"id": "in-3", "ts": T0, "text": "e first-live-secret-0001 f"})

        assert len(calls) == 2  # releu e falhou: manteve as chaves anteriores
        store.close()
        assert b"first-live-secret-0001" not in raw_bytes(tmp_path)

    # ------------------------------------------------------- texto não confiável

    def test_game_text_is_bounded_and_stripped_of_control_characters(self, tmp_path):
        store = new_store(tmp_path)
        huge = "dwarf " * 50_000

        store.save("questions", "q-1", {
            "id": "q-1", "ts": T0, "status": "pending",
            "question": "a\x00b\x1b[31mc\x7fd\ne",
            "why_pt": huge,
        })

        loaded = store.read("questions")[0]
        assert loaded["question"] == "ab[31mcd\ne"          # \n fica; controlos saem
        assert len(loaded["why_pt"]) <= MAX_STRING_CHARS + len("…[truncated]")
        assert loaded["why_pt"].endswith("…[truncated]")

    def test_unserialisable_values_never_raise(self, tmp_path):
        class Hostile:
            def __str__(self) -> str:
                raise RuntimeError("no printing")

        store = new_store(tmp_path)
        record = {
            "id": "d-1", "ts": T0, "set": {3, 1, 2}, "bytes": b"\xff\xfe", "path": Path("/x"),
            "nan": float("nan"), "inf": float("inf"), "obj": Hostile(), "tuple": (1, 2),
            1: "int key",
        }

        assert store.save("decisions", 1, record) is True

        loaded = store.read("decisions")[0]
        assert loaded["set"] == [1, 2, 3]
        assert loaded["bytes"] == "<2 bytes>"
        assert loaded["nan"] is None and loaded["inf"] is None
        assert loaded["obj"] == "<unprintable>"
        assert loaded["tuple"] == [1, 2]
        assert loaded["1"] == "int key"

    def test_nesting_and_collection_size_are_capped(self, tmp_path):
        store = new_store(tmp_path)
        deep: dict = {}
        node = deep
        for _ in range(60):
            node["n"] = {}
            node = node["n"]
        wide = {f"k{i}": i for i in range(5000)}

        assert store.save("decisions", 1, {"id": "d-1", "ts": T0, "deep": deep,
                                           "wide": wide, "many": list(range(5000))})

        loaded = store.read("decisions")[0]
        depth, cursor = 0, loaded["deep"]
        while isinstance(cursor, dict) and "n" in cursor:
            depth, cursor = depth + 1, cursor["n"]
        assert depth <= persist_module.MAX_DEPTH
        assert len(loaded["wide"]) <= persist_module.MAX_COLLECTION_ITEMS + 1
        assert len(loaded["many"]) <= persist_module.MAX_COLLECTION_ITEMS + 1

    def test_an_oversized_payload_degrades_to_scalars_instead_of_filling_the_disk(self):
        record = {"id": "d-1", "ts": T0, "agent": "JEV-A",
                  **{f"k{i}": "x" * 15_000 for i in range(200)}}

        text = dump_payload(record)

        assert len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES
        loaded = json.loads(text)
        assert loaded["id"] == "d-1" and loaded["agent"] == "JEV-A"

    def test_unknown_kinds_and_malformed_ops_are_ignored(self, tmp_path):
        store = new_store(tmp_path)

        written = store.apply_ops([
            (OP_SAVE, "decisions; DROP TABLE questions;--", "k", {"id": "x"}),
            (OP_SAVE, "meta", "k", {"id": "x"}),
            (OP_DROP, "decisions", 1, None),          # só perguntas/aprovações têm lápide
            ("explode", "questions", "q-1", {"id": "q-1"}),
            (OP_SAVE,), None, 5, "abc",
            (OP_SAVE, "decisions", "not-a-number", {"id": "d-x"}),
            (OP_SAVE, "questions", "q-ok", {"id": "q-ok", "ts": T0, "status": "pending"}),
            (OP_SAVE, "questions", "q-bad", "not a mapping"),
        ])

        assert written == 1
        assert store.count("questions") == 1
        assert store.count("DROP TABLE questions") == 0
        with closing(sqlite3.connect(store.path)) as conn:
            assert conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0] == 1

    def test_a_bad_limit_argument_neither_raises_nor_degrades_the_store(self, tmp_path):
        store = new_store(tmp_path)
        store.save("decisions", 1, {"id": "d-1", "ts": T0})

        assert store.read("decisions", "abc") != []          # cai para o default
        assert store.read("decisions", None) != []
        assert store.read("decisions", -5) == []             # negativo = nada
        assert [seq for seq, _ in store.load_snapshot(decisions="x").decisions] == [1]
        assert store.degraded is False and store.failures == 0

    def test_parameters_are_bound_not_interpolated(self, tmp_path):
        store = new_store(tmp_path)
        nasty = "q-1'); DROP TABLE questions;--"

        assert store.save("questions", nasty, {"id": nasty, "ts": T0, "status": "pending"})

        assert [q["id"] for q in store.read("questions")] == [nasty]
        assert store.drop("questions", nasty) is True

    # --------------------------------------------------- corrupção e degradação

    def test_a_corrupt_file_is_quarantined_and_the_store_starts_fresh(self, tmp_path):
        path = db_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"this is definitely not a sqlite database " * 50)
        store = RuntimeStore(path)

        snap = store.load_snapshot()           # nunca levanta

        assert snap.empty
        assert store.quarantined == 1
        assert (path.parent / f"{RUNTIME_DB_NAME}.corrupt-1").exists()
        assert store.save("decisions", 1, {"id": "d-1", "ts": T0}) is True   # recomeçou
        assert [seq for seq, _ in RuntimeStore(path).load_snapshot().decisions] == [1]

    def test_corruption_discovered_after_the_store_was_open_is_handled_too(self, tmp_path):
        store = new_store(tmp_path)
        store.save("decisions", 1, {"id": "d-1", "ts": T0})
        store.close()
        path = db_path(tmp_path)
        for suffix in ("-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)
        path.write_bytes(b"\x00garbage" * 4096)

        assert store.save("decisions", 2, {"id": "d-2", "ts": T0}) is True

        assert store.quarantined == 1
        assert [seq for seq, _ in store.load_snapshot().decisions] == [2]

    def test_an_unquarantinable_corrupt_file_degrades_to_memory_only(self, tmp_path, monkeypatch):
        path = db_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_bytes(b"not a database" * 100)

        def refuse(*_args, **_kwargs):
            raise PermissionError("read-only filesystem")

        monkeypatch.setattr(persist_module.os, "replace", refuse)
        store = RuntimeStore(path)

        assert store.load_snapshot().empty
        assert store.save("decisions", 1, {"id": "d-1", "ts": T0}) is False
        assert store.degraded is True
        assert store.failures >= 1 and store.last_error
        assert store.count("decisions") == 0
        assert path.read_bytes().startswith(b"not a database")   # nada foi destruído

    def test_an_unwritable_location_degrades_without_raising(self, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file, not a directory")
        store = RuntimeStore(blocker / "campanha" / RUNTIME_DB_NAME)

        assert store.save("questions", "q-1", {"id": "q-1", "ts": T0}) is False
        assert store.load_snapshot().empty
        assert store.read("questions") == []
        assert store.drop("questions", "q-1") is False
        store.close()  # idem

        assert store.degraded is True
        assert store.status()["failures"] >= 1

    def test_a_degraded_store_retries_after_the_window_and_recovers(self, tmp_path):
        clock = [100.0]
        blocker = tmp_path / "campanha"
        blocker.write_text("in the way")                 # o diretório não pode ser criado
        store = RuntimeStore(blocker / RUNTIME_DB_NAME, monotonic=lambda: clock[0],
                             retry_after_s=30.0)

        assert store.save("inputs", "in-1", {"id": "in-1", "ts": T0, "text": "a"}) is False
        failures = store.failures
        clock[0] += 5.0
        assert store.save("inputs", "in-2", {"id": "in-2", "ts": T0, "text": "b"}) is False
        assert store.failures == failures                 # dentro da janela: nem tenta

        blocker.unlink()                                  # o disco "voltou"
        clock[0] += 60.0
        assert store.save("inputs", "in-3", {"id": "in-3", "ts": T0, "text": "c"}) is True
        assert store.degraded is False
        assert [i["id"] for i in store.read("inputs")] == ["in-3"]

    def test_a_schema_from_the_future_keeps_the_store_memory_only(self, tmp_path):
        store = new_store(tmp_path)
        store.save("decisions", 1, {"id": "d-1", "ts": T0})
        store.close()
        with closing(sqlite3.connect(store.path)) as conn:
            conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
            conn.commit()

        newer = new_store(tmp_path)

        assert newer.load_snapshot().empty
        assert newer.save("decisions", 2, {"id": "d-2", "ts": T0}) is False
        assert newer.degraded is True

    def test_close_checkpoints_and_the_store_reopens_on_demand(self, tmp_path):
        store = new_store(tmp_path)
        store.save("decisions", 1, {"id": "d-1", "ts": T0})

        store.close()
        store.close()  # idempotente

        assert store.save("decisions", 2, {"id": "d-2", "ts": T0}) is True
        assert [seq for seq, _ in store.load_snapshot().decisions] == [1, 2]

    # ----------------------------------------------------------- concorrência

    def test_concurrent_writes_from_many_threads_lose_nothing(self, tmp_path):
        store = new_store(tmp_path, prune_every=10_000)
        workers, per_worker = 8, 60
        barrier = threading.Barrier(workers)

        def work(worker: int) -> None:
            barrier.wait()
            for index in range(per_worker):
                key = f"q-{worker}-{index}"
                store.save("questions", key, {"id": key, "ts": ts(index), "status": "pending"})
                store.save("decisions", worker * 1000 + index,
                           {"id": f"d-{worker}-{index}", "ts": ts(index)})
                store.apply_ops([(OP_SAVE, "inputs", f"in-{worker}-{index}",
                                  {"id": f"in-{worker}-{index}", "ts": T0, "text": "x"})])

        threads = [threading.Thread(target=work, args=(w,)) for w in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert store.failures == 0 and store.degraded is False
        for kind in ("questions", "decisions", "inputs"):
            assert store.count(kind) == workers * per_worker, kind
        snap = new_store(tmp_path).load_snapshot()
        assert len(snap.decisions) == workers * per_worker


# ====================================================================== registo


class TestTrackedRecord:
    def test_every_in_place_edit_reports_to_the_hook(self):
        def edits():
            return [
                lambda r: r.__setitem__("b", 2),
                lambda r: r.update(c=3),
                lambda r: r.setdefault("d", 4),
                lambda r: r.pop("a"),
                lambda r: r.popitem(),
                lambda r: r.__delitem__("c"),
                lambda r: r.__ior__({"e": 5}),
                lambda r: r.clear(),
            ]

        for index, edit in enumerate(edits()):
            seen = []
            record = TrackedRecord({"a": 1, "c": 3, "d": 4}).bind(seen.append)

            edit(record)

            assert seen and all(item is record for item in seen), index

    def test_a_failed_edit_does_not_report(self):
        seen = []
        record = TrackedRecord({"a": 1}).bind(seen.append)

        with pytest.raises(KeyError):
            record.pop("missing")
        with pytest.raises(KeyError):
            del record["missing"]

        assert seen == [] and record == {"a": 1}

    def test_unbound_and_unbound_again_records_are_plain_dicts(self):
        seen = []
        record = TrackedRecord({"a": 1}).bind(seen.append)
        record.bind(None)

        record["a"] = 2

        assert seen == [] and record == {"a": 2}

    def test_a_failing_hook_never_breaks_the_edit(self):
        def boom(_record):
            raise RuntimeError("disk on fire")

        record = TrackedRecord().bind(boom)

        record["a"] = 1

        assert record == {"a": 1}

    def test_copies_are_plain_dicts_that_never_write(self):
        import copy
        import pickle

        seen = []
        record = TrackedRecord({"a": [1, 2]}).bind(seen.append)

        duplicate = copy.deepcopy(record)
        shallow = copy.copy(record)
        revived = pickle.loads(pickle.dumps(record))
        duplicate["a"].append(3)
        shallow["b"] = 1
        revived["c"] = 1

        assert type(duplicate) is dict and type(shallow) is dict and type(revived) is dict
        assert seen == [] and record == {"a": [1, 2]}


# ============================================================ runtime + store


class TestWriteThrough:
    def test_every_mutation_reaches_the_disk_without_any_flush(self, tmp_path):
        runtime, _store = new_runtime(tmp_path)
        reader = new_store(tmp_path)          # outra ligação: só vê o que está commitado

        runtime.add_decision(decision(1))
        assert reader.count("decisions") == 1

        question = runtime.add_question(approval_question(qid="q-1-1", action_id="goto:river"))
        assert [q["status"] for q in reader.read("questions")] == ["pending"]

        runtime.answer_question(question["id"], answer_text="sim", option_id="yes")
        assert [q["status"] for q in reader.read("questions")] == ["answered"]
        assert reader.read("questions")[0]["option_id"] == "yes"

        approval = runtime.add_approval(question)
        assert [a["status"] for a in reader.read("approvals")] == ["pending"]
        runtime.decide_approval(approval["id"], status="approved", note="vai")
        stored = reader.read("approvals")[0]
        assert (stored["status"], stored["note"]) == ("approved", "vai")

        runtime.add_input("hello")
        assert reader.read("inputs")[0]["text"] == "hello"

    def test_the_status_marks_of_a_decision_are_persisted(self, tmp_path):
        runtime, _ = new_runtime(tmp_path)
        runtime.add_decision(decision(1, answers={"action": "goto:river"}))
        runtime.add_decision(decision(2, answers={"action": "goto:hill"}))

        runtime.mark_decision_question(hitl_question(action_id="goto:river"))
        released = runtime.list_decisions()[1]
        runtime.mark_decision_released({"decision": {"ts": released["ts"], "agent": "JEV-A"}})

        again, _ = new_runtime(tmp_path)
        by_ts = {d["ts"]: d for d in again.list_decisions()}
        assert by_ts[ts(1)]["status"] == "question"
        assert by_ts[ts(2)]["status"] == "executed"
        assert by_ts[ts(2)]["issues"] == ["released_by_approval"]

    def test_a_missing_store_changes_nothing(self):
        runtime = CampaignRuntime("c-1")

        runtime.add_decision(decision(1))
        question = runtime.add_question(approval_question())
        runtime.add_approval(question)

        assert runtime.persistent is False
        assert runtime._ops == [] and runtime.restored == {}
        assert runtime.list_approvals()[0]["id"] == "q-3-1"
        runtime.close()  # no-op

    def test_a_failing_store_never_reaches_the_request_path(self):
        class Boom:
            def apply_ops(self, *_a, **_k):
                raise OSError("disk exploded")

            def load_snapshot(self, **_k):
                raise OSError("disk exploded")

            def close(self):
                raise OSError("disk exploded")

        runtime = CampaignRuntime("c-1", store=Boom())

        runtime.add_decision(decision(1))
        question = runtime.add_question(approval_question())
        runtime.add_approval(question)
        runtime.answer_question(question["id"], answer_text="x")
        runtime.add_input("hi")

        assert len(runtime.list_decisions()) == 1
        assert runtime.list_questions()[0]["status"] == "answered"
        runtime.close()

    def test_a_store_that_hands_back_junk_never_stops_the_runtime_from_starting(self):
        class Junk:
            def load_snapshot(self, **_k):
                return RuntimeSnapshot(
                    decisions=((1, {"id": "d-1", "ts": T0}), ("x", {"id": "d-2"})),
                    questions=(None,),                       # type: ignore[arg-type]
                    counters={"questions": "not-a-number"},
                )

            def apply_ops(self, ops):
                return len(ops)

        runtime = CampaignRuntime("c-1", store=Junk())       # não levanta

        assert runtime.persistent is True
        runtime.add_decision(decision(1))                    # e continua a funcionar
        assert runtime.list_decisions()

    def test_a_degraded_store_keeps_the_runtime_working_in_memory(self, tmp_path):
        blocker = tmp_path / "campanha"
        blocker.write_text("in the way")
        store = RuntimeStore(blocker / RUNTIME_DB_NAME)
        runtime = CampaignRuntime("c-1", store=store)

        runtime.add_decision(decision(1))
        question = runtime.add_question(approval_question())
        runtime.add_approval(question)

        assert store.degraded is True
        assert len(runtime.list_decisions()) == 1 and len(runtime.list_approvals()) == 1


class TestRestart:
    def build_history(self, runtime: CampaignRuntime) -> None:
        for n in range(1, 6):
            runtime.add_decision(decision(n, agent="JEV-A" if n % 2 else "JEV-D"))
        held = runtime.add_question(approval_question(qid="q-3-1", action_id="goto:river", ts=ts(10)))
        runtime.add_approval(held)
        asked = runtime.add_question(hitl_question(qid="q-4-1", action_id="goto:hill", ts=ts(11)))
        runtime.answer_question(asked["id"], answer_text="nadar", option_id="opt-1")
        other = runtime.add_question(approval_question(qid="q-5-1", action_id="eat:bread", ts=ts(12)))
        approval = runtime.add_approval(other)
        runtime.decide_approval(approval["id"], status="rejected", note="não")
        runtime.add_input("o rio tem uma ponte a norte", ts=ts(13))
        runtime.add_input("cuidado com o goblin", ts=ts(14))
        runtime.mark_decision_executed({"decision": {"ts": ts(2), "agent": "JEV-D"}})

    def test_a_restart_keeps_everything_the_api_shows(self, tmp_path):
        before_runtime, _ = new_runtime(tmp_path)
        self.build_history(before_runtime)
        before = view(before_runtime)

        after_runtime, _ = new_runtime(tmp_path)   # "restart": objetos novos, mesmo ficheiro

        assert view(after_runtime) == before
        assert after_runtime.restored == {"decisions": 5, "questions": 3, "approvals": 2, "inputs": 2}
        assert after_runtime.source_of("decisions") == "live"

    def test_pagination_is_identical_after_a_restart(self, tmp_path):
        runtime, _ = new_runtime(tmp_path)
        for n in range(1, 9):
            runtime.add_decision(decision(n))
        expect = {
            "all": runtime.list_decisions(),
            "limit": runtime.list_decisions(limit=3),
            "before": runtime.list_decisions(before=ts(5)),
            "both": runtime.list_decisions(limit=2, before=ts(5)),
        }

        again, _ = new_runtime(tmp_path)

        assert again.list_decisions() == expect["all"]
        assert again.list_decisions(limit=3) == expect["limit"]
        assert again.list_decisions(before=ts(5)) == expect["before"]
        assert again.list_decisions(limit=2, before=ts(5)) == expect["both"]
        assert [d["ts"] for d in expect["both"]] == [ts(3), ts(4)]
        with pytest.raises(ValueError):
            again.list_decisions(before="not-a-timestamp")  # mesma regra de cursor

    def test_id_counters_continue_instead_of_restarting_at_one(self, tmp_path):
        runtime, _ = new_runtime(tmp_path)
        for _ in range(4):
            runtime.add_decision(decision(1))
        for _ in range(3):
            runtime.add_question({"question": "?"})        # ids q-1..q-3 (do runtime)
        runtime.add_input("a")
        runtime.add_input("b")

        again, _ = new_runtime(tmp_path)

        assert again.next_question_id() == "q-4"
        assert again.add_input("c")["id"] == "in-3"
        assert again.add_decision(decision(9))["id"] == "d-5"
        assert [d["id"] for d in again.list_decisions()][-2:] == ["d-4", "d-5"]

    def test_a_full_ring_no_longer_repeats_one_decision_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_module, "DECISIONS_LIMIT", 5)
        runtime, store = new_runtime(tmp_path)

        for n in range(1, 13):
            runtime.add_decision(decision(n))

        ids = [d["id"] for d in runtime.decisions]
        assert ids == ["d-8", "d-9", "d-10", "d-11", "d-12"]    # antes: todos "d-6"
        assert store.count("decisions") == 12                    # o disco guarda o histórico todo
        again, _ = new_runtime(tmp_path)
        assert [d["id"] for d in again.decisions] == ids         # recarga respeita o cap
        assert again.add_decision(decision(13))["id"] == "d-13"

    def test_the_reload_honours_the_ring_caps(self, tmp_path, monkeypatch):
        runtime, _ = new_runtime(tmp_path)
        for n in range(1, 11):
            runtime.add_decision(decision(n))
            runtime.add_input(f"input {n}")
            q = runtime.add_question({"id": f"q-{n}", "ts": ts(n), "question": "?"})
            runtime.answer_question(q["id"], answer_text="ok")

        monkeypatch.setattr(runtime_module, "DECISIONS_LIMIT", 4)
        monkeypatch.setattr(runtime_module, "QUESTIONS_LIMIT", 3)
        monkeypatch.setattr(runtime_module, "INPUTS_LIMIT", 2)
        again, _ = new_runtime(tmp_path)

        assert [d["id"] for d in again.decisions] == ["d-7", "d-8", "d-9", "d-10"]
        assert [q["id"] for q in again.list_questions()] == ["q-8", "q-9", "q-10"]
        assert [i["text"] for i in again.list_inputs()] == ["input 9", "input 10"]

    # ------------------------------------------------- aprovações e libertação

    def test_a_pending_approval_survives_and_is_released_exactly_once(self, tmp_path):
        first, _ = new_runtime(tmp_path)
        held = first.add_question(approval_question(qid="q-1-1", action_id="goto:river"))
        first.add_approval(held)

        second, _ = new_runtime(tmp_path)                       # backend reiniciou
        assert [a["id"] for a in second.list_approvals()] == ["q-1-1"]
        assert second.list_approvals()[0]["status"] == "pending"
        assert ApprovalChecker(second).was_approved("goto:river") is False   # ainda pendente

        second.decide_approval("q-1-1", status="approved")
        assert ApprovalChecker(second).was_approved("goto:river") is True    # liberta UMA vez
        assert ApprovalChecker(second).was_approved("goto:river") is False

        third, _ = new_runtime(tmp_path)                        # e outra vez reiniciou
        released = third.get_approval("q-1-1")
        assert released["released_at"] and released["released_action_id"] == "goto:river"
        # sem a marca persistida, a mesma aprovação libertaria a ação uma 2.ª vez
        assert ApprovalChecker(third).was_approved("goto:river") is False

    def test_an_edit_by_reference_outside_the_runtime_is_persisted_at_once(self, tmp_path):
        runtime, _ = new_runtime(tmp_path)
        runtime.add_approval(approval_question(qid="q-1-1", action_id="goto:river"))
        runtime.decide_approval("q-1-1", status="approved")
        record = runtime.get_approval("q-1-1")            # a referência viva (como o release)

        record["released_at"] = "2026-10-10T10:05:00.000Z"   # nenhum método do runtime

        again, _ = new_runtime(tmp_path)
        assert again.get_approval("q-1-1")["released_at"] == "2026-10-10T10:05:00.000Z"

    def test_dedupe_by_action_still_collapses_a_re_ask_after_a_restart(self, tmp_path):
        first, _ = new_runtime(tmp_path)
        q = first.add_question(approval_question(qid="q-1-1", action_id="goto:river", ts=ts(1)))
        first.add_approval(q)

        second, _ = new_runtime(tmp_path)
        again = second.add_question(approval_question(qid="q-2-1", action_id="goto:river", ts=ts(9)))
        second.add_approval(again)

        assert [x["id"] for x in second.list_questions()] == ["q-1-1"]
        assert second.get_question("q-1-1")["attempts"] == 2
        approvals = second.list_approvals()
        assert [a["id"] for a in approvals] == ["q-1-1"] and approvals[0]["attempts"] == 2

    def test_the_pending_cap_is_enforced_on_reload_and_the_overflow_is_tombstoned(self, tmp_path):
        store = new_store(tmp_path)
        for n in range(25):  # um store escrito sob um cap maior do que o atual
            store.save("approvals", f"a-{n:02d}", {
                "id": f"a-{n:02d}", "ts": ts(n), "action_id": f"goto:{n:02d}",
                "question_id": f"a-{n:02d}", "status": "pending"})

        runtime = CampaignRuntime("c-1", store=store)

        pending = runtime.list_approvals()
        assert len(pending) == 20 and pending[0]["id"] == "a-05"
        assert store.count("approvals", status="dropped") == 5
        again, _ = new_runtime(tmp_path)
        assert len(again.list_approvals()) == 20

    def test_a_dropped_approval_is_not_resurrected_by_a_late_edit_or_a_restart(self, tmp_path):
        runtime, store = new_runtime(tmp_path)
        runtime.add_approval(approval_question(qid="q-1-1", action_id="goto:river"))
        record = runtime.get_approval("q-1-1")

        runtime.drop_approval("q-1-1")
        record["note"] = "late edit by someone holding the reference"

        assert store.count("approvals", status="dropped") == 1
        again, _ = new_runtime(tmp_path)
        assert again.list_approvals(status=None) == []

    def test_a_decided_approval_that_is_dropped_stays_as_history(self, tmp_path):
        runtime, _ = new_runtime(tmp_path)
        runtime.add_approval(approval_question(qid="q-1-1", action_id="goto:river"))
        runtime.decide_approval("q-1-1", status="rejected")

        runtime.drop_approval("q-1-1")

        again, _ = new_runtime(tmp_path)
        assert [a["status"] for a in again.list_approvals(status=None)] == ["rejected"]

    # ------------------------------------------------------- perguntas e ids

    def test_a_pending_question_evicted_from_the_ring_is_not_resurrected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(runtime_module, "QUESTIONS_LIMIT", 2)
        runtime, store = new_runtime(tmp_path)
        for n in range(1, 4):  # nenhuma respondida: a pendente mais antiga cai
            runtime.add_question({"id": f"q-{n}", "ts": ts(n), "question": "?",
                                  "action_id": f"goto:{n}"})

        assert sorted(runtime.questions) == ["q-2", "q-3"]
        assert store.count("questions", status="dropped") == 1
        again, _ = new_runtime(tmp_path)
        assert sorted(again.questions) == ["q-2", "q-3"]

    def test_an_answered_question_evicted_from_the_ring_stays_in_the_audit_trail(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(runtime_module, "QUESTIONS_LIMIT", 2)
        runtime, store = new_runtime(tmp_path)
        runtime.add_question({"id": "q-1", "ts": ts(1), "question": "?"})
        runtime.answer_question("q-1", answer_text="sim")
        runtime.add_question({"id": "q-2", "ts": ts(2), "question": "?"})
        runtime.add_question({"id": "q-3", "ts": ts(3), "question": "?"})

        assert sorted(runtime.questions) == ["q-2", "q-3"]     # a respondida saiu do anel…
        history = {q["id"]: q["status"] for q in store.read("questions", 10)}
        assert history == {"q-1": "answered", "q-2": "pending", "q-3": "pending"}  # …não do disco

    def test_a_loop_issued_id_that_collides_after_a_restart_keeps_both_records(self, tmp_path):
        first, _ = new_runtime(tmp_path)
        old = first.add_question(approval_question(qid="q-1-1", action_id="goto:river", ts=ts(1)))
        first.add_approval(old)
        first.decide_approval("q-1-1", status="rejected", note="não")
        first.answer_question("q-1-1", answer_text="não", option_id="no")

        # o backend reinicia: o AgentLoop novo volta a emitir q-1-1 para OUTRA ação
        second, _ = new_runtime(tmp_path)
        new = second.add_question(approval_question(qid="q-1-1", action_id="eat:bread", ts=ts(50)))
        approval = second.add_approval(new)

        assert new["id"] == "q-1-1.r1"
        assert approval is not None and approval["id"] == "q-1-1.r1"
        assert approval["status"] == "pending"
        assert second.get_question("q-1-1")["status"] == "answered"     # intacta
        assert second.get_approval("q-1-1")["status"] == "rejected"     # nunca "bumpada"
        assert "attempts" not in second.get_approval("q-1-1")

        third, _ = new_runtime(tmp_path)
        assert sorted(third.questions) == ["q-1-1", "q-1-1.r1"]
        assert [a["id"] for a in third.list_approvals()] == ["q-1-1.r1"]

    def test_the_collision_guard_does_not_depend_on_the_store(self):
        runtime = CampaignRuntime("c-1")
        old = runtime.add_question(approval_question(qid="q-1-1", action_id="goto:a", ts=ts(1)))
        runtime.answer_question(old["id"], answer_text="ok")

        new = runtime.add_question(approval_question(qid="q-1-1", action_id="goto:b", ts=ts(9)))

        assert new["id"] == "q-1-1.r1"
        assert [q["id"] for q in runtime.list_questions()] == ["q-1-1", "q-1-1.r1"]

    def test_the_same_event_published_again_keeps_the_replace_semantics(self):
        runtime = CampaignRuntime("c-1")
        event = approval_question(qid="q-1-1", action_id="goto:a", ts=ts(1))
        runtime.add_question(event)
        runtime.answer_question("q-1-1", answer_text="ok")

        runtime.add_question(dict(event))   # mesmo id **e** mesmo ts: o mesmo evento

        assert [q["id"] for q in runtime.list_questions()] == ["q-1-1"]
        assert runtime.get_question("q-1-1")["status"] == "pending"

    # --------------------------------------------------------- concorrência

    def test_concurrent_loop_and_request_threads_keep_memory_and_disk_consistent(self, tmp_path):
        runtime, store = new_runtime(tmp_path)
        workers, per_worker = 6, 40
        barrier = threading.Barrier(workers)
        errors: list[BaseException] = []

        def work(worker: int) -> None:
            try:
                barrier.wait()
                for index in range(per_worker):
                    runtime.add_decision(decision(index, answers={"action": f"goto:{worker}"}))
                    runtime.add_input(f"w{worker} i{index}")
                    q = runtime.add_question({"id": f"q-{worker}-{index}", "ts": ts(index),
                                              "question": "?", "action_id": f"goto:{worker}:{index}"})
                    if index % 2:
                        runtime.answer_question(q["id"], answer_text="ok")
            except BaseException as exc:  # pragma: no cover - reportado abaixo
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(w,)) for w in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        total = workers * per_worker
        assert len(runtime.decisions) == total
        assert len({d["id"] for d in runtime.decisions}) == total      # ids únicos
        assert len({i["id"] for i in runtime.inputs}) == total
        assert store.failures == 0 and store.count("decisions") == total
        again, _ = new_runtime(tmp_path)
        assert view(again) == view(runtime)

    # ------------------------------ migração de arranque (anéis em memória → base)

    def test_the_in_memory_rings_migrate_into_the_base_at_startup(self, tmp_path):
        """Passo 16: anéis já povoados no arranque caem na base e sobrevivem."""
        seed = CampaignRuntime("c-1")                    # sessão anterior: só memória
        self.build_history(seed)
        before = view(seed)

        migrated = CampaignRuntime(                      # arranque com store: migra tudo
            "c-1", decisions=list(seed.decisions), questions=dict(seed.questions),
            approvals=dict(seed.approvals), inputs=list(seed.inputs),
            store=new_store(tmp_path))

        assert view(migrated) == before                  # a memória não perde nada
        assert migrated.migrated == {"decisions": 5, "questions": 3,
                                     "approvals": 2, "inputs": 2}
        assert view(seed) == before                      # os anéis do chamador ficam intactos

        again, _ = new_runtime(tmp_path)                 # "restart": a base já tem tudo
        assert view(again) == before
        assert again.restored == {"decisions": 5, "questions": 3, "approvals": 2, "inputs": 2}
        assert again.add_decision(decision(9))["id"] == "d-6"    # ids continuam

    def test_the_migration_is_a_fallback_the_base_is_never_overwritten(self, tmp_path):
        """A base é a verdade onde tem dados: nada reescrito, nenhuma lápide revivida."""
        runtime, _ = new_runtime(tmp_path)
        runtime.add_question({"id": "q-1", "ts": ts(1), "question": "da base", "status": "answered"})
        approval = runtime.add_approval(approval_question(qid="q-2", action_id="goto:x", ts=ts(2)))
        runtime.drop_approval(approval["id"])            # largada: lápide na base
        runtime.close()

        migrated = CampaignRuntime(
            "c-1",
            questions={"q-1": {"id": "q-1", "ts": ts(1), "question": "da memória"},
                       "q-3": {"id": "q-3", "ts": ts(3), "question": "nova?"}},
            approvals={"q-2": {"id": "q-2", "ts": ts(2), "action_id": "goto:x",
                               "question_id": "q-2", "status": "pending"}},
            store=new_store(tmp_path))

        assert migrated.questions["q-1"]["question"] == "da base"     # nunca reescrita
        assert migrated.questions["q-3"]["question"] == "nova?"       # o desconhecido migra
        assert "q-2" not in migrated.approvals                        # não ressuscita
        assert migrated.migrated == {"decisions": 0, "questions": 1, "approvals": 0, "inputs": 0}
        store = new_store(tmp_path)
        assert store.count("approvals") == 1                          # a lápide fica auditada
        assert store.read("approvals") == []                          # e nunca recarregada

    def test_the_migration_never_writes_secrets_into_the_base(self, tmp_path):
        """A migração passa pelo mesmo scrub: chaves não chegam ao ficheiro."""
        seed = CampaignRuntime("c-1")
        seed.add_decision({**decision(1), "why_pt": f"a chave {LIVE_KEY} ficou no texto"})

        store = new_store(tmp_path)
        CampaignRuntime("c-1", decisions=list(seed.decisions), store=store)
        store.close()

        assert LIVE_KEY.encode() not in raw_bytes(tmp_path)
        stored = new_store(tmp_path).read("decisions")
        assert mask_key(LIVE_KEY) in stored[0]["why_pt"]


# ============================================================ app / API (restart)


def publish(client, campaign_id: str, event_type: str, payload: dict) -> None:
    client.app.state.services.publish_event_threadsafe(campaign_id, event_type, payload)


class TestAppServicesWiring:
    def test_a_backend_restart_keeps_the_audit_trail_and_the_pending_approvals(self, make_client):
        first = make_client()
        cid = first.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        first.post(f"/api/campaigns/{cid}/control", json={"mode": "checkpoint"})
        publish(first, cid, "decision", {
            "id": "d-1", "ts": ts(1), "agent": "JEV-A", "request_summary": "choice(action)",
            "answers": {"action": "goto:river_bank"}, "probabilities": None, "confidence": 0.7,
            "band": "hitl", "latency_ms": 10, "cost_usd": 0.0001})
        publish(first, cid, "question_new", approval_question(qid="q-3-1", ts=ts(2)))
        publish(first, cid, "question_new", hitl_question(qid="q-4-1", action_id="goto:hill",
                                                          ts=ts(3)))
        first.app.state.services.shutdown_loops()   # o que o lifespan faz ao desligar

        second = make_client()                      # processo novo, mesma raiz de campanhas

        decisions = second.get(f"/api/campaigns/{cid}/decisions")
        assert decisions.headers["x-source"] == "live"
        assert [d["id"] for d in decisions.json()] == ["d-1"]
        assert decisions.json()[0]["status"] == "question"
        questions = second.get(f"/api/campaigns/{cid}/questions").json()
        assert sorted(q["id"] for q in questions) == ["q-3-1", "q-4-1"]
        pending = second.get(f"/api/campaigns/{cid}/approvals").json()
        assert [a["id"] for a in pending] == ["q-3-1"]

        approved = second.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve", json={"note": "vai"})
        assert approved.status_code == 200 and approved.json()["status"] == "approved"
        assert second.get(f"/api/campaigns/{cid}/approvals").json() == []

    def test_the_file_lives_in_the_campaign_root_with_private_permissions(self, make_client):
        client = make_client()
        created = client.post("/api/campaigns", json={"name": "Urist"}).json()
        publish(client, created["id"], "decision", {"id": "d-1", "ts": ts(1), "agent": "JEV-A"})

        root = client.app.state.services.resolve(created["id"]).root
        path = root / RUNTIME_DB_NAME

        assert path.exists()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_persistence_can_be_switched_off(self, make_client):
        client = make_client()
        services = client.app.state.services
        services.persist_runtime = False
        created = client.post("/api/campaigns", json={"name": "Urist"}).json()

        publish(client, created["id"], "decision", {"id": "d-1", "ts": ts(1), "agent": "JEV-A"})

        assert services.runtime(created["id"]).persistent is False
        assert not (services.resolve(created["id"]).root / RUNTIME_DB_NAME).exists()
        assert len(client.get(f"/api/campaigns/{created['id']}/decisions").json()) == 1

    def test_an_unknown_campaign_stays_in_memory_and_creates_no_directory(self, make_client):
        client = make_client()
        services = client.app.state.services

        runtime = services.runtime("not-a-campaign")
        runtime.add_decision({"ts": ts(1), "agent": "JEV-A"})

        assert runtime.persistent is False
        assert not (services.manager.root / "not-a-campaign").exists()

    def test_the_store_factory_seam_replaces_the_real_store(self, make_client):
        client = make_client()
        services = client.app.state.services
        built = []

        class Fake:
            def load_snapshot(self, **_kwargs):
                return RuntimeSnapshot()

            def apply_ops(self, ops):
                built.append(list(ops))
                return len(ops)

            def close(self):
                built.append("closed")

        services.runtime_store_factory = lambda campaign: Fake()
        created = client.post("/api/campaigns", json={"name": "Urist"}).json()

        publish(client, created["id"], "decision", {"id": "d-1", "ts": ts(1), "agent": "JEV-A"})
        services.close_runtimes()

        kinds = [op[1] for batch in built if batch != "closed" for op in batch]
        assert kinds == ["decisions"] and built[-1] == "closed"

    def test_live_api_keys_are_masked_in_the_file_but_not_in_the_live_view(self, make_client):
        client = make_client()
        created = client.post("/api/campaigns", json={"name": "Urist"}).json()
        cid = created["id"]
        secret = "TEST-KEY-NOT-REAL-0001"
        assert client.post(f"/api/campaigns/{cid}/keys",
                           json={"provider": "openrouter", "key": secret}).status_code in (200, 201)

        publish(client, cid, "question_new", {
            **hitl_question(qid="q-9-1", ts=ts(5)),
            "source_data": {"reason": "band_hitl", "context": f"the page printed {secret}"},
        })
        services = client.app.state.services
        services.close_runtimes()

        blob = b"".join(p.read_bytes() for p in
                        services.resolve(cid).root.glob(f"{RUNTIME_DB_NAME}*"))
        assert secret.encode() not in blob
        stored = RuntimeStore(services.resolve(cid).root / RUNTIME_DB_NAME).read("questions")
        assert mask_key(secret) in stored[0]["source_data"]["context"]

    def test_shutdown_flushes_and_the_runtime_stays_usable(self, make_client):
        client = make_client()
        services = client.app.state.services
        created = client.post("/api/campaigns", json={"name": "Urist"}).json()
        cid = created["id"]
        publish(client, cid, "decision", {"id": "d-1", "ts": ts(1), "agent": "JEV-A"})

        services.shutdown_loops()
        publish(client, cid, "decision", {"id": "d-2", "ts": ts(2), "agent": "JEV-A"})  # reabre
        services.close_runtimes()

        store = RuntimeStore(services.resolve(cid).root / RUNTIME_DB_NAME)
        assert [seq for seq, _ in store.load_snapshot().decisions] == [1, 2]

    def test_two_campaigns_never_share_a_store(self, make_client):
        client = make_client()
        one = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        two = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()["id"]

        publish(client, one, "decision", {"id": "d-1", "ts": ts(1), "agent": "JEV-A"})
        client.app.state.services.close_runtimes()

        again = make_client()
        assert len(again.get(f"/api/campaigns/{one}/decisions").json()) == 1
        assert again.get(f"/api/campaigns/{two}/decisions").json() == []

    # --------------------------------------------- humano presente → watchdog

    def test_the_answer_listener_tells_the_session_watchdog_the_human_is_there(self, make_client):
        from system_one_dwarf.agent.watchdog import SessionWatchdog

        client = make_client()
        services = client.app.state.services
        cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        watchdog = SessionWatchdog(no_progress_steps=10, no_progress_s=0, wall_clock=lambda: "T")
        services.set_answer_listener(cid, watchdog.note_human_answer)
        state = SimpleNamespace(is_alive=True, metrics=None, adventurer=None)  # sem sinais
        watchdog.observe(state)
        watchdog.observe(state)
        assert watchdog.steps_since_progress == 2

        # a pergunta ao humano NÃO é progresso; só a resposta dele é
        publish(client, cid, "question_new", hitl_question(qid="q-1-1", ts=ts(1)))
        watchdog.observe(state)
        assert watchdog.steps_since_progress == 3
        answered = client.post(f"/api/campaigns/{cid}/questions/q-1-1/answer",
                               json={"option_id": "opt-1"})

        assert answered.status_code == 200
        assert watchdog.observe(state) is None
        assert watchdog.steps_since_progress == 0

    def test_the_answer_listener_is_per_campaign_replaceable_and_failure_proof(self, make_client):
        client = make_client()
        services = client.app.state.services
        one = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        two = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()["id"]
        fired: list[str] = []

        def explode() -> None:
            raise RuntimeError("listener bug")

        services.set_answer_listener(one, explode)
        services.set_answer_listener(two, lambda: fired.append("two"))
        publish(client, one, "question_new", hitl_question(qid="q-1-1", ts=ts(1)))
        publish(client, two, "question_new", hitl_question(qid="q-1-1", ts=ts(1)))

        ok = client.post(f"/api/campaigns/{one}/questions/q-1-1/answer", json={"option_id": "opt-1"})
        assert ok.status_code == 200 and fired == []   # um listener que falha não parte a resposta

        services.set_answer_listener(one, lambda: fired.append("one"))   # substitui
        services.set_answer_listener(two, None)                           # limpa
        client.post(f"/api/campaigns/{two}/questions/q-1-1/answer", json={"option_id": "opt-1"})
        assert fired == []

    # ------------------------------------------------- pause_and_ask → pausar

    def test_pause_campaign_is_the_control_endpoint_without_the_http(self, make_client):
        from functools import partial

        from fakes_loop import LoopFactory
        from system_one_dwarf.agent.loop import CallbackEventSink

        client = make_client()
        services = client.app.state.services
        cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        factory = LoopFactory()
        sink = CallbackEventSink(partial(services.publish_event_threadsafe, cid))
        services.loop_builder = lambda: factory(sink)
        client.post(f"/api/campaigns/{cid}/control", json={"mode": "autopilot"})
        assert services.loop_status(cid)["running"] is True

        with client.websocket_connect(f"/ws/campaigns/{cid}") as ws:
            assert ws.receive_json()["type"] == "control_state"      # estado inicial
            payload = services.pause_campaign(cid, reason="session watchdog: pause_and_ask")
            event = ws.receive_json()

        assert payload["mode"] == "paused" and payload["kill_switch"] is False
        assert event["type"] == "control_state" and event["payload"]["mode"] == "paused"
        assert client.get(f"/api/campaigns/{cid}").json()["control"]["mode"] == "paused"
        status = services.loop_status(cid)
        assert status["running"] is False and status["reason"] == "paused"
        assert factory.last.closed is True

    def test_pause_campaign_never_raises_and_ignores_unknown_campaigns(self, make_client):
        client = make_client()
        services = client.app.state.services

        assert services.pause_campaign("not-a-campaign") is None
        assert services.pause_campaign("") is None
