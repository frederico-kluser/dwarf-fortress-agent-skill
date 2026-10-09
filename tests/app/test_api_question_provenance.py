"""Proveniência das perguntas (item 3 / achado #2 do ``uxui-audit-ui.json``).

A superfície de human-in-the-loop tem de dizer **porque** o agente pergunta.  A
fila (``GET .../questions``) e os eventos WS ``question_new`` passam a levar os
campos **aditivos** ``band``, ``role``, ``action_id``, ``why_pt`` e
``source_data``, persistidos a partir do payload que o loop publica.

Regra de compatibilidade: um ``question_new`` sem proveniência (o payload de 6
campos do contrato original) continua a produzir exatamente a mesma entrada —
os campos existem só quando são conhecidos.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes_loop import hitl_question
from test_api_telemetry import QUESTION, publish

PROVENANCE = {"band", "role", "action_id", "why_pt", "source_data"}
FROZEN = {"id", "ts", "question", "options", "free_text_allowed", "status"}


class TestGetQuestions:
    def test_question_with_provenance_carries_it(self, client: TestClient, campaign):
        publish(client, campaign["id"], "question_new", hitl_question())

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]

        assert set(entry) == FROZEN | PROVENANCE
        assert entry["band"] == "hitl"
        assert entry["role"] == "JEV-A"  # há uma ação retida → papel JEV-A
        assert entry["action_id"] == "goto:river_bank"
        assert "hitl" in entry["why_pt"]
        assert entry["source_data"]["reason"] == "band_hitl"
        assert entry["source_data"]["action_confidence"] == 0.62

    def test_question_without_provenance_keeps_the_frozen_six_fields(self, client, campaign):
        publish(client, campaign["id"], "question_new", QUESTION)

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]

        assert set(entry) == FROZEN
        assert entry["id"] == "q-4"

    def test_the_band_can_be_read_from_source_data(self, client, campaign):
        payload = hitl_question()
        payload.pop("band")
        payload.pop("action_id")
        payload["source_data"] = {"reason": "ask_unavailable", "band": "abstain"}

        publish(client, campaign["id"], "question_new", payload)

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]
        assert entry["band"] == "abstain"
        # sem ação retida, a pergunta vem da escalada do JEV-D
        assert entry["role"] == "JEV-D"
        assert "action_id" not in entry

    def test_an_unknown_band_is_dropped_instead_of_leaking_junk(self, client, campaign):
        payload = hitl_question()
        payload["band"] = "yolo"  # lixo do evento: cai no band válido do source_data

        publish(client, campaign["id"], "question_new", payload)

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]
        assert entry["band"] == "hitl"

    def test_a_question_without_any_valid_band_omits_the_field(self, client, campaign):
        payload = hitl_question()
        payload["band"] = "yolo"
        payload["source_data"].pop("band")

        publish(client, campaign["id"], "question_new", payload)

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]
        assert "band" not in entry
        assert entry["role"] == "JEV-A"  # a ação retida continua a identificar o papel

    def test_answered_entries_keep_the_provenance(self, client, campaign):
        publish(client, campaign["id"], "question_new", hitl_question())
        client.post(
            f"/api/campaigns/{campaign['id']}/questions/q-1-1/answer",
            json={"answer_text": "Procurar uma ponte", "option_id": "opt-2"},
        )

        entry = client.get(f"/api/campaigns/{campaign['id']}/questions").json()[0]

        assert entry["status"] == "answered"
        assert entry["band"] == "hitl" and entry["action_id"] == "goto:river_bank"


class TestWebSocket:
    def test_question_new_event_carries_the_provenance(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            publish(client, campaign["id"], "question_new", hitl_question())
            message = ws.receive_json()

        assert message["type"] == "question_new"
        payload = message["payload"]
        assert set(payload) >= FROZEN | PROVENANCE
        assert payload["band"] == "hitl"
        assert payload["role"] == "JEV-A"
        assert payload["action_id"] == "goto:river_bank"

    def test_events_without_provenance_are_unchanged(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            publish(client, campaign["id"], "question_new", QUESTION)
            message = ws.receive_json()

        assert set(message["payload"]) == FROZEN