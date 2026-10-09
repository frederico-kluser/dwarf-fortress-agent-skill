"""``WS /ws/campaigns/{id}`` — envelope, bus fan-out and the client->server messages."""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from test_api_telemetry import DECISION, METRICS, QUESTION, publish

ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

EVENT_TYPES = {
    "metrics_update",
    "screen_translation",
    "decision",
    "question_new",
    "memory_update",
    "spend_update",
    "game_state",
    "control_state",
}


def test_connect_sends_the_control_state_snapshot(client: TestClient, campaign):
    with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
        message = ws.receive_json()

    assert message["type"] == "control_state"
    assert message["payload"] == {"mode": "autopilot", "kill_switch": False}
    assert set(message) == {"type", "ts", "payload"}
    assert ISO_Z.match(message["ts"])


def test_envelope_wraps_every_published_event(client: TestClient, campaign):
    with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
        assert ws.receive_json()["type"] == "control_state"

        publish(client, campaign["id"], "metrics_update", METRICS)
        publish(client, campaign["id"], "decision", DECISION)
        publish(client, campaign["id"], "screen_translation", {"translated_pt": "Na clareira."})
        publish(client, campaign["id"], "question_new", QUESTION)

        received = [ws.receive_json() for _ in range(4)]

    assert [m["type"] for m in received] == [
        "metrics_update",
        "decision",
        "screen_translation",
        "question_new",
    ]
    for message in received:
        assert set(message) == {"type", "ts", "payload"}
        assert ISO_Z.match(message["ts"])
    assert received[0]["payload"]["health"]["status"] == "wounded"
    assert received[1]["payload"]["id"] == "d-91"
    assert received[2]["payload"] == {"translated_pt": "Na clareira."}
    assert received[3]["payload"]["id"] == "q-4"


def test_every_contract_event_type_has_a_stable_envelope(client: TestClient, campaign):
    with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
        assert ws.receive_json()["type"] == "control_state"
        for event_type in sorted(EVENT_TYPES - {"control_state"}):
            publish(client, campaign["id"], event_type, {"probe": event_type})
        received = [ws.receive_json() for _ in range(len(EVENT_TYPES) - 1)]

    assert {m["type"] for m in received} == EVENT_TYPES - {"control_state"}
    for message in received:
        assert message["payload"]["probe"] == message["type"]


def test_events_only_reach_their_own_campaign(client: TestClient):
    first = client.post("/api/campaigns", json={"name": "Urist"}).json()
    second = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()

    with client.websocket_connect(f"/ws/campaigns/{first['id']}") as ws:
        assert ws.receive_json()["type"] == "control_state"
        publish(client, second["id"], "metrics_update", METRICS)
        publish(client, first["id"], "metrics_update", METRICS | {"ts": "2026-09-27T20:09:00.000Z"})
        message = ws.receive_json()

    assert message["type"] == "metrics_update"
    assert message["payload"]["ts"] == "2026-09-27T20:09:00.000Z"


def test_multiple_clients_receive_the_same_event(client: TestClient, campaign):
    with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as first:
        assert first.receive_json()["type"] == "control_state"
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as second:
            assert second.receive_json()["type"] == "control_state"
            publish(client, campaign["id"], "metrics_update", METRICS)
            assert first.receive_json()["type"] == "metrics_update"
            assert second.receive_json()["type"] == "metrics_update"


class TestAnswerQuestion:
    def test_ws_answer_updates_the_queue_and_the_memory(self, client: TestClient, campaign, fake_cli):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            publish(client, campaign["id"], "question_new", QUESTION)
            assert ws.receive_json()["type"] == "question_new"

            ws.send_json(
                {
                    "type": "answer_question",
                    "question_id": "q-4",
                    "answer_text": "Look for a bridge to the east",
                    "option_id": "opt-2",
                }
            )
            events = [ws.receive_json() for _ in range(2)]

        assert [e["type"] for e in events] == ["question_answered", "memory_update"]
        assert events[0]["payload"] == {
            "id": "q-4",
            "status": "answered",
            "answer_text": "Look for a bridge to the east",
            "option_id": "opt-2",
        }
        assert set(events[1]["payload"]) == {
            "narrative",
            "narrative_pt",
            "episodes",
            "working_memory",
            "updated_at",
        }
        queued = client.get(f"/api/campaigns/{campaign['id']}/questions").json()
        assert queued[0]["status"] == "answered"
        assert any(e["key"] == "answer:q-4" for e in fake_cli.entries)

    def test_unknown_question_id_is_a_non_fatal_error(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "answer_question", "question_id": "q-404", "answer_text": "hi"})
            error = ws.receive_json()
            ws.send_json({"type": "set_control", "mode": "paused", "kill_switch": False})
            alive = ws.receive_json()

        assert error["type"] == "error" and "not found" in error["payload"]["detail"]
        assert alive["type"] == "control_state" and alive["payload"]["mode"] == "paused"

    def test_missing_question_id_is_an_error(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "answer_question", "answer_text": "hi"})
            assert ws.receive_json()["type"] == "error"


class TestSetControl:
    def test_set_control_persists_and_echoes(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "set_control", "mode": "checkpoint", "kill_switch": True})
            echoed = ws.receive_json()

        assert echoed["type"] == "control_state"
        assert echoed["payload"] == {"mode": "checkpoint", "kill_switch": True}
        control = client.get(f"/api/campaigns/{campaign['id']}").json()["control"]
        assert control == {"mode": "checkpoint", "kill_switch": True}

    def test_rest_control_is_broadcast_to_ws_clients(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            client.post(f"/api/campaigns/{campaign['id']}/control", json={"mode": "paused"})
            broadcast = ws.receive_json()

        assert broadcast["type"] == "control_state"
        assert broadcast["payload"] == {"mode": "paused", "kill_switch": False}

    def test_invalid_mode_is_a_non_fatal_error(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "set_control", "mode": "warp-speed"})
            error = ws.receive_json()

        assert error["type"] == "error" and "invalid mode" in error["payload"]["detail"]


class TestRobustness:
    def test_unknown_message_types_are_ignored(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "totally-unknown", "payload": {}})
            ws.send_json({"type": "set_control", "mode": "autopilot", "kill_switch": False})
            message = ws.receive_json()

        assert message["type"] == "control_state"  # the socket survived the junk

    def test_invalid_json_is_an_error_event(self, client: TestClient, campaign):
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_text("{not json")
            error = ws.receive_json()

        assert error["type"] == "error" and "invalid JSON" in error["payload"]["detail"]

    def test_disconnect_cleans_up_the_subscription(self, client: TestClient, campaign):
        bus = client.app.state.services.bus
        with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            assert bus.subscriber_count(campaign["id"]) == 1
        assert bus.subscriber_count(campaign["id"]) == 0

    def test_unknown_campaign_closes_with_4404(self, client: TestClient):
        with client.websocket_connect("/ws/campaigns/does-not-exist") as ws:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_json()
        assert excinfo.value.code == 4404


def test_raw_envelope_is_plain_json(client: TestClient, campaign):
    """Clients parse JSON, not anything exotic — the frame must be a JSON object."""
    with client.websocket_connect(f"/ws/campaigns/{campaign['id']}") as ws:
        message = json.loads(ws.receive_text())

    assert set(message) == {"type", "ts", "payload"}
