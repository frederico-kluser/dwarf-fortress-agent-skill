"""Stub-backed reads: metrics, decisions, questions, screen, spend.

The agent loop is a separate piece; until it publishes, these endpoints must
answer with contract-correct zeroed payloads and a ``stub`` marker — never 501,
never a broken shape.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

METRIC_GROUPS = {
    "health",
    "needs",
    "inventory",
    "threats",
    "location",
    "objective_progress",
    "combat_history",
    "agent",
}

QUESTION = {
    "id": "q-4",
    "ts": "2026-09-27T20:04:59.000Z",
    "question": "Atravessar o rio a nadar ou procurar uma ponte?",
    "options": [{"id": "opt-1", "text": "Nadar"}, {"id": "opt-2", "text": "Procurar ponte"}],
    "free_text_allowed": True,
    "status": "pending",
}

DECISION = {
    "id": "d-91",
    "ts": "2026-09-27T20:04:58.000Z",
    "agent": "JEV-A",
    "request_summary": "choice(6 action candidates) + score(urgency) + noul(needs_human)",
    "answers": {"choice": "goto:river_bank", "score": "minor risk", "noul": 0.12},
    "probabilities": {"goto:river_bank": 0.94, "move:north": 0.04, "other": 0.02},
    "confidence": 0.94,
    "band": "auto",
    "latency_ms": 410,
    "cost_usd": 0.000133,
}

METRICS = {
    "ts": "2026-09-27T20:05:00.000Z",
    "health": {"wounds": [{"part": "left arm", "severity": "minor"}], "status": "wounded"},
    "needs": {"hunger": "none", "thirst": "mild", "sleep": "rested"},
    "inventory": {"weight_kg": 42.5, "items": [{"name": "waterskin", "count": 1}]},
    "threats": {"level": "low", "combat": False, "hostiles": []},
    "location": {"region": "The Hills", "path": [{"x": 1, "y": 2}]},
    "objective_progress": {"percent": 0.35, "active_subgoal_id": "sg-2"},
    "combat_history": [{"ts": "2026-09-27T20:00:00.000Z", "outcome": "fled"}],
    "agent": {
        "decisions_per_min": 12.4,
        "confidence_avg": 0.87,
        "calls": 318,
        "cost_usd": 0.042318,
        "latency_ms": 410,
    },
}


def publish(client: TestClient, campaign_id: str, event_type: str, payload: dict) -> None:
    """Exactly what another piece calls to feed the live views."""
    client.app.state.services.publish_event_threadsafe(campaign_id, event_type, payload)


#: O que o loop publica quando o snapshot do jogo traz os nulls do contrato
#: (``dfhack/df_bridge.py``: "nulls, nunca ausentes") — campos numéricos como
#: ``weight_kg``/``capacity_kg``/``hp_current``/``hp_max``/``travel_destination``
#: são ``None`` porque o Lua não os expõe.
METRICS_NULLS = {
    "ts": "2026-09-27T20:05:00.000Z",
    "health": {
        "wounds": [],
        "status": None,
        "pain": None,
        "hp_current": None,
        "hp_max": None,
    },
    "needs": {"hunger": None, "thirst": None, "sleep": None, "stress_level": None},
    "inventory": {
        "weight_kg": None,
        "items": [],
        "total_weight_kg": None,
        "capacity_kg": None,
    },
    "threats": {"level": None, "combat": False, "hostiles": []},
    "location": {"region": None, "path": [], "travel_destination": None},
    "objective_progress": {"percent": None, "active_subgoal_id": None},
    "combat_history": [],
    "agent": {
        "decisions_per_min": None,
        "confidence_avg": None,
        "calls": 0,
        "cost_usd": 0.0,
        "latency_ms": 0,
    },
}


class TestMetrics:
    def test_stub_payload_is_zeroed_and_marked(self, client, campaign):
        response = client.get(f"/api/campaigns/{campaign['id']}/metrics")

        assert response.status_code == 200
        body = response.json()
        assert METRIC_GROUPS <= set(body)
        assert body["source"] == "stub"
        assert response.headers["x-source"] == "stub"
        assert body["health"] == {"wounds": [], "status": "healthy"}
        assert body["needs"] == {"hunger": "none", "thirst": "none", "sleep": "rested"}
        assert body["inventory"] == {"weight_kg": 0.0, "items": []}
        assert body["threats"] == {"level": "low", "combat": False, "hostiles": []}
        assert body["location"] == {"region": "", "path": []}
        assert body["objective_progress"] == {"percent": 0.0, "active_subgoal_id": None}
        assert body["combat_history"] == []
        assert body["agent"] == {
            "decisions_per_min": 0.0,
            "confidence_avg": 0.0,
            "calls": 0,
            "cost_usd": 0.0,
            "latency_ms": 0,
        }
        assert ISO_Z.match(body["ts"])

    def test_published_metrics_become_live(self, client, campaign):
        publish(client, campaign["id"], "metrics_update", METRICS)

        response = client.get(f"/api/campaigns/{campaign['id']}/metrics")

        assert response.headers["x-source"] == "live"
        assert response.json() == METRICS | {"source": "live"}

    def test_unknown_metric_keys_are_dropped(self, client, campaign):
        publish(client, campaign["id"], "metrics_update", METRICS | {"injected": "nope"})
        body = client.get(f"/api/campaigns/{campaign['id']}/metrics").json()
        assert "injected" not in body

    def test_metrics_are_per_campaign(self, client):
        first = client.post("/api/campaigns", json={"name": "Urist"}).json()
        second = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()
        publish(client, first["id"], "metrics_update", METRICS)

        assert client.get(f"/api/campaigns/{first['id']}/metrics").json()["source"] == "live"
        assert client.get(f"/api/campaigns/{second['id']}/metrics").json()["source"] == "stub"

    def test_metrics_on_unknown_campaign_is_404(self, client):
        assert client.get("/api/campaigns/nope/metrics").status_code == 404

    def test_snapshot_nulls_never_break_the_metrics_response(self, client, campaign):
        """Regressão (defeito A): o snapshot emite ``null`` legítimo
        (``weight_kg`` e afins) e o modelo de resposta não pode rebentar com um
        ``500`` de validação — o ``null`` explícito do contrato continua ``null``.

        Antes do fix: ``ResponseValidationError: float_type … inventory.weight_kg``
        em ``routes/metrics.py`` logo que o jogo devolvesse peso desconhecido.
        """
        publish(client, campaign["id"], "metrics_update", METRICS_NULLS)

        response = client.get(f"/api/campaigns/{campaign['id']}/metrics")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["inventory"]["weight_kg"] is None
        assert body["health"]["status"] is None
        assert body["needs"] == {"hunger": None, "thirst": None, "sleep": None}
        assert body["threats"]["level"] is None
        assert body["location"]["region"] is None
        assert body["objective_progress"]["percent"] is None


class TestDecisions:
    def test_stub_is_an_empty_array(self, client, campaign):
        response = client.get(f"/api/campaigns/{campaign['id']}/decisions")
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["x-source"] == "stub"

    def test_published_decisions_are_listed(self, client, campaign):
        publish(client, campaign["id"], "decision", DECISION)

        response = client.get(f"/api/campaigns/{campaign['id']}/decisions")

        assert response.headers["x-source"] == "live"
        assert response.json() == [DECISION]
        assert set(response.json()[0]) == {
            "id",
            "ts",
            "agent",
            "request_summary",
            "answers",
            "probabilities",
            "confidence",
            "band",
            "latency_ms",
            "cost_usd",
        }

    def test_decisions_keep_publish_order(self, client, campaign):
        first = DECISION | {"id": "d-1"}
        second = DECISION | {"id": "d-2"}
        publish(client, campaign["id"], "decision", first)
        publish(client, campaign["id"], "decision", second)

        assert [d["id"] for d in client.get(f"/api/campaigns/{campaign['id']}/decisions").json()] == [
            "d-1",
            "d-2",
        ]

    def test_confidence_may_be_missing(self, client, campaign):
        publish(client, campaign["id"], "decision", {k: v for k, v in DECISION.items() if k != "confidence"})
        body = client.get(f"/api/campaigns/{campaign['id']}/decisions").json()[0]
        assert body["confidence"] is None
        assert body["band"] == "auto"

    def test_decisions_on_unknown_campaign_is_404(self, client):
        assert client.get("/api/campaigns/nope/decisions").status_code == 404


class TestQuestions:
    def test_stub_is_an_empty_array(self, client, campaign):
        response = client.get(f"/api/campaigns/{campaign['id']}/questions")
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["x-source"] == "stub"

    def test_question_from_the_agent_loop_shows_up(self, client, campaign):
        publish(client, campaign["id"], "question_new", QUESTION)

        response = client.get(f"/api/campaigns/{campaign['id']}/questions")

        assert response.headers["x-source"] == "live"
        assert response.json() == [QUESTION]
        assert set(response.json()[0]) == {
            "id",
            "ts",
            "question",
            "options",
            "free_text_allowed",
            "status",
        }

    def test_answer_is_recorded_and_persisted(self, client, campaign, fake_cli):
        publish(client, campaign["id"], "question_new", QUESTION)

        response = client.post(
            f"/api/campaigns/{campaign['id']}/questions/q-4/answer",
            json={"answer_text": "Procurar uma ponte a leste", "option_id": "opt-2"},
        )

        assert response.status_code == 200
        assert response.json() == {
            "id": "q-4",
            "status": "answered",
            "answer_text": "Procurar uma ponte a leste",
            "option_id": "opt-2",
        }
        queued = client.get(f"/api/campaigns/{campaign['id']}/questions").json()
        assert queued[0]["status"] == "answered"
        stored = [entry for entry in fake_cli.entries if entry["key"] == "answer:q-4"]
        assert len(stored) == 1 and stored[0]["tags"] == "human_answer"

    def test_answer_is_translated_to_english(self, make_client):
        from fakes import FakeTranslator

        translator = FakeTranslator({"Procurar uma ponte a leste": "Look for a bridge to the east."})
        client = make_client(translator=translator)
        cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
        publish(client, cid, "question_new", QUESTION)

        body = client.post(
            f"/api/campaigns/{cid}/questions/q-4/answer",
            json={"answer_text": "Procurar uma ponte a leste", "option_id": "opt-2"},
        ).json()

        assert body["answer_text"] == "Look for a bridge to the east."

    def test_answer_with_only_an_option(self, client, campaign):
        publish(client, campaign["id"], "question_new", QUESTION)
        body = client.post(
            f"/api/campaigns/{campaign['id']}/questions/q-4/answer", json={"option_id": "opt-1"}
        ).json()
        assert body["status"] == "answered" and body["option_id"] == "opt-1"
        assert body["answer_text"] is None

    def test_unknown_question_is_404(self, client, campaign):
        response = client.post(
            f"/api/campaigns/{campaign['id']}/questions/q-999/answer", json={"answer_text": "hello"}
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "question not found"}


class TestScreen:
    def test_stub_is_empty_pt_only(self, client, campaign):
        response = client.get(f"/api/campaigns/{campaign['id']}/screen")

        assert response.status_code == 200
        assert response.json()["translated_pt"] == ""
        assert response.json()["source"] == "stub"
        assert response.headers["x-source"] == "stub"
        assert ISO_Z.match(response.json()["ts"])

    def test_published_translation_becomes_live(self, client, campaign):
        publish(
            client,
            campaign["id"],
            "screen_translation",
            {"translated_pt": "Encontra-se numa clareira rodeada de árvores."},
        )

        body = client.get(f"/api/campaigns/{campaign['id']}/screen").json()

        assert body["translated_pt"] == "Encontra-se numa clareira rodeada de árvores."
        assert body["source"] == "live"
        assert "original" not in body and "text" not in body

    def test_screen_on_unknown_campaign_is_404(self, client):
        assert client.get("/api/campaigns/nope/screen").status_code == 404


class TestSpend:
    def test_empty_ledger(self, client, campaign):
        body = client.get(f"/api/campaigns/{campaign['id']}/spend").json()
        assert set(body) == {"total_usd", "providers", "ts"}
        assert body["total_usd"] == 0.0 and body["providers"] == []

    def test_ledger_entries_are_aggregated(self, client, campaign):
        ledger = client.app.state.services.spend(client.app.state.services.resolve(campaign["id"]))
        ledger.record("typesafe", "typesafe/jev-1.13", cost_usd=0.042318, input_tokens=1_007_714)
        ledger.record("typesafe", "typesafe/jev-1.13", cost_usd=0.0000004)
        ledger.record("openrouter", "deepseek/deepseek-v4.1-flash", cost_usd=0.0001234,
                      input_tokens=8000, output_tokens=2100)

        body = client.get(f"/api/campaigns/{campaign['id']}/spend").json()

        assert body["total_usd"] == 0.042441
        providers = {p["provider"]: p for p in body["providers"]}
        assert providers["typesafe"]["models"][0] == {
            "model": "typesafe/jev-1.13",
            "calls": 2,
            "input_tokens": 1_007_714,
            "output_tokens": 0,
            "cost_usd": 0.042318,
        }
        assert providers["openrouter"]["models"][0]["output_tokens"] == 2100

    def test_spend_on_unknown_campaign_is_404(self, client):
        assert client.get("/api/campaigns/nope/spend").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["metrics", "spend", "decisions", "questions", "screen"],
)
def test_every_stub_endpoint_answers_200(client, campaign, path):
    assert client.get(f"/api/campaigns/{campaign['id']}/{path}").status_code == 200
