"""LOOP ↔ RUNTIME (item 5): arranque/paragem pelo modo, chaves e eventos reais.

Cobre o que o backend promete:

* sem chaves o loop **fica parado** (``reason == "no_api_keys"``) e o app continua
  a servir os stubs documentados;
* ``autopilot``/``checkpoint`` arrancam o loop, ``paused``/kill-switch param-no;
  arrancar/parar pode repetir-se (cada arranque constrói um loop novo, porque
  ``AgentLoop.stop()`` é terminal por desenho);
* os eventos que o loop publica chegam à fila REST **e** ao WebSocket (fumo de
  wiring com um loop falso);
* com as peças reais (``build_loop`` + ``AgentLoop`` verdadeiro) e fábricas falsas
  para jogo/JEV/LLM/executor, o loop corre um passo a sério e a pergunta chega à
  fila com proveniência — tudo offline.
"""

from __future__ import annotations

import time
from functools import partial

import pytest
from fastapi.testclient import TestClient

from fakes_loop import (
    FakeExecutor,
    FakeGame,
    FakeJev,
    FakeLLM,
    LoopFactory,
    game_state,
    hitl_question,
    hostile,
)
from system_one_dwarf.agent.loop import CallbackEventSink
from system_one_dwarf.app.loop_runner import LoopUnavailable, build_loop
from test_api_telemetry import publish

SECRET = "TEST-KEY-NOT-REAL-0001"


def wait_until(predicate, *, timeout: float = 3.0, interval: float = 0.01):
    """Poll ``predicate`` until true (the loop runs in its own thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def create_campaign(client: TestClient, name: str = "Urist") -> str:
    response = client.post("/api/campaigns", json={"name": name, "is_new_game": True})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def set_control(client: TestClient, cid: str, **body) -> dict:
    response = client.post(f"/api/campaigns/{cid}/control", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def attach_fake_loops(client: TestClient, cid: str, factory: LoopFactory, services=None) -> None:
    """Point the campaign's loop builder at ``factory`` (with a real sink)."""
    services = services or client.app.state.services
    sink = CallbackEventSink(partial(services.publish_event_threadsafe, cid))
    services.loop_builder = lambda: factory(sink)


def status(client: TestClient, cid: str) -> dict:
    return client.app.state.services.loop_status(cid)


class TestWithoutKeys:
    def test_no_key_keeps_the_loop_stopped_and_the_api_on_stubs(self, client: TestClient, campaign):
        cid = campaign["id"]

        body = set_control(client, cid, mode="autopilot")
        assert set(body) == {"mode", "kill_switch", "ts"}  # a forma do contrato não muda

        state = status(client, cid)
        assert state["running"] is False
        assert state["reason"] == "no_api_keys"
        assert "typesafe" in state["detail"] and "openrouter" in state["detail"]
        assert state["starts"] == 0

        # Endpoints continuam com os stubs documentados.
        assert client.get(f"/api/campaigns/{cid}/metrics").json()["source"] == "stub"
        assert client.get(f"/api/campaigns/{cid}/questions").json() == []
        assert client.get(f"/api/campaigns/{cid}/decisions").json() == []

    def test_one_key_is_not_enough(self, client: TestClient, campaign):
        cid = campaign["id"]
        client.post(f"/api/campaigns/{cid}/keys", json={"provider": "typesafe", "key": SECRET})

        set_control(client, cid, mode="autopilot")

        state = status(client, cid)
        assert state["running"] is False and state["reason"] == "no_api_keys"
        assert "openrouter" in state["detail"]

    def test_the_real_builder_reports_the_missing_providers(self, client: TestClient, campaign):
        services = client.app.state.services
        with_missing = services.resolve(campaign["id"])
        try:
            build_loop(services, with_missing)
        except LoopUnavailable as exc:
            assert exc.reason == "no_api_keys"
            assert exc.missing == ("typesafe", "openrouter")
        else:  # pragma: no cover - the loop must not be buildable without keys
            raise AssertionError("build_loop must refuse to build without keys")

    def test_a_kill_switch_never_starts_anything(self, client: TestClient, campaign):
        cid = campaign["id"]
        set_control(client, cid, mode="autopilot", kill_switch=True)

        state = status(client, cid)
        assert state["running"] is False and state["reason"] == "kill_switch"


class TestStartStop:
    def test_control_starts_and_stops_the_loop(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory()
        attach_fake_loops(client, cid, factory)

        set_control(client, cid, mode="autopilot")
        assert wait_until(lambda: status(client, cid)["running"]) is True
        assert factory.calls == 1 and factory.last.run_calls == 1
        assert status(client, cid)["reason"] == "running"

        # checkpoint corre o mesmo loop (o modo não o reinicia)
        set_control(client, cid, mode="checkpoint")
        assert status(client, cid)["running"] is True
        assert factory.calls == 1

        set_control(client, cid, mode="paused")
        first = factory.loops[0]
        assert status(client, cid)["running"] is False
        assert status(client, cid)["reason"] == "paused"
        assert first.stop_calls == 1 and first.closed is True

        # arrancar outra vez constrói um loop novo (o antigo não é reiniciável)
        set_control(client, cid, mode="autopilot")
        assert wait_until(lambda: status(client, cid)["running"]) is True
        assert factory.calls == 2 and factory.loops[1] is not first

        set_control(client, cid, mode="autopilot", kill_switch=True)
        assert status(client, cid)["running"] is False
        assert status(client, cid)["reason"] == "kill_switch"
        assert factory.loops[1].stop_calls == 1

    def test_repeated_toggles_are_stable(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory()
        attach_fake_loops(client, cid, factory)

        for _ in range(3):
            set_control(client, cid, mode="autopilot")
            assert wait_until(lambda: status(client, cid)["running"]) is True
            set_control(client, cid, mode="paused")
            assert status(client, cid)["running"] is False

        state = status(client, cid)
        assert state["starts"] == 3 and state["stops"] == 3
        assert all(loop.stop_calls == 1 and loop.closed for loop in factory.loops)

    def test_a_loop_that_ends_on_its_own_can_be_started_again(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory(exit_after_events=True)
        attach_fake_loops(client, cid, factory)

        set_control(client, cid, mode="autopilot")
        assert wait_until(lambda: status(client, cid)["running"] is False) is True
        assert status(client, cid)["last_report"] == {"steps": 1, "stopped_reason": "scripted"}

        set_control(client, cid, mode="checkpoint")  # reinicia
        assert wait_until(lambda: factory.calls == 2) is True

    def test_a_broken_builder_never_breaks_the_api(self, client: TestClient, campaign):
        cid = campaign["id"]
        client.app.state.services.loop_builder = lambda: (_ for _ in ()).throw(
            RuntimeError("no collaborator")
        )

        assert set_control(client, cid, mode="autopilot")["mode"] == "autopilot"

        state = status(client, cid)
        assert state["running"] is False and state["reason"] == "error"
        assert "no collaborator" in state["last_error"]
        assert client.get(f"/api/health").status_code == 200

    def test_ws_set_control_also_drives_the_loop(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory()
        attach_fake_loops(client, cid, factory)

        with client.websocket_connect(f"/ws/campaigns/{cid}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            ws.send_json({"type": "set_control", "mode": "autopilot", "kill_switch": False})
            assert wait_until(lambda: status(client, cid)["running"]) is True
            echoed = ws.receive_json()

        assert echoed["type"] == "control_state" and echoed["payload"]["mode"] == "autopilot"
        assert factory.calls == 1


class TestEventFlow:
    def test_fake_loop_question_reaches_the_queue_and_the_websocket(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory(events=[("question_new", hitl_question(qid="q-1-1"))])
        attach_fake_loops(client, cid, factory)

        with client.websocket_connect(f"/ws/campaigns/{cid}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            set_control(client, cid, mode="autopilot")
            received = [ws.receive_json() for _ in range(2)]

        assert {message["type"] for message in received} == {"control_state", "question_new"}
        question = wait_until(
            lambda: client.get(f"/api/campaigns/{cid}/questions").json() or None
        )
        assert question[0]["id"] == "q-1-1"
        assert question[0]["band"] == "hitl" and question[0]["role"] == "JEV-A"

    def test_decisions_and_metrics_from_the_loop_become_live(self, client: TestClient, campaign):
        cid = campaign["id"]
        factory = LoopFactory(
            events=[
                ("decision", {"id": "d-1", "ts": "2026-10-08T12:00:00.000Z", "agent": "JEV-A",
                              "band": "auto", "confidence": 0.95, "cost_usd": 0.0001}),
                ("metrics_update", {"ts": "2026-10-08T12:00:00.000Z", "health": {"status": "wounded"},
                                    "combat_history": []}),
            ]
        )
        attach_fake_loops(client, cid, factory)

        set_control(client, cid, mode="autopilot")

        assert wait_until(lambda: client.get(f"/api/campaigns/{cid}/decisions").json() or None)
        assert client.get(f"/api/campaigns/{cid}/decisions").headers["x-source"] == "live"
        assert client.get(f"/api/campaigns/{cid}/metrics").json()["source"] == "live"


class TestDecisionsContract:
    def test_a_decision_without_a_distribution_is_null_never_empty(self, client: TestClient, campaign):
        """Achado do wiring: o loop publica ``probabilities: null`` (JEV-R/M/D).

        O modelo de resposta antigo (``dict``) rebentava com um ``500`` de
        validação assim que um loop real corresse — ``null`` nunca pode virar
        ``{}``, que mentiria sobre uma distribuição que não existe.
        """
        cid = campaign["id"]
        publish(
            client,
            cid,
            "decision",
            {
                "id": "d-9",
                "ts": "2026-10-08T12:00:00.000Z",
                "agent": "JEV-M",
                "request_summary": "noul(update_memory) + choice(narrator_effort)",
                "answers": {"update_memory": 0.1},
                "probabilities": None,
                "confidence": None,
                "band": "hitl",
                "latency_ms": 5,
                "cost_usd": 0.0,
            },
        )

        response = client.get(f"/api/campaigns/{cid}/decisions")

        assert response.status_code == 200
        assert response.json()[0]["probabilities"] is None
        assert response.json()[0]["confidence"] is None


class TestRealBuilder:
    def test_the_real_agent_loop_wires_end_to_end(self, client: TestClient, campaign):
        """``build_loop`` + ``AgentLoop`` reais, com fábricas falsas de jogo/JEV/LLM."""
        services = client.app.state.services
        cid = campaign["id"]
        client.post(f"/api/campaigns/{cid}/keys", json={"provider": "typesafe", "key": SECRET})
        client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

        jev = FakeJev(action_confidence=0.62)
        llm = FakeLLM()
        executor = FakeExecutor()
        state = game_state(hostiles=[hostile()], screen_text="You see a goblin skulker here.")

        def builder():
            return build_loop(
                services,
                services.resolve(cid),
                game_factory=lambda: FakeGame(state),
                jev_factory=lambda key, **kwargs: jev,
                llm_factory=lambda get_key, **kwargs: llm,
                executor_factory=lambda game, config: executor,
            )

        services.loop_builder = builder
        set_control(client, cid, mode="autopilot")

        assert wait_until(lambda: client.get(f"/api/campaigns/{cid}/questions").json() or None)

        # JEV-A ∥ JEV-D correram a sério (o loop é o verdadeiro).
        roles = set(jev.roles)
        assert {"JEV-A", "JEV-D"} <= roles
        # A ação ficou na banda hitl: nada foi executado, a pergunta foi publicada.
        assert executor.action_ids == []

        question = client.get(f"/api/campaigns/{cid}/questions").json()[0]
        assert question["band"] == "hitl"
        assert question["role"] == "JEV-A"
        assert question["action_id"]  # a ação retida vem identificada
        assert "hitl" in question["why_pt"]
        # A proveniência do formatador chega ao cartão da UI: o ``source_data``
        # cru (confiança, candidatos, dúvida do JEV-D…) já não vem vazio.
        source = question["source_data"]
        assert source["band"] == "hitl"
        assert source["reason"] and source["route_reason"] == "band_hitl"
        assert source["action_confidence"] == pytest.approx(0.62)
        assert source["candidate_actions"]  # os candidatos que o JEV-A pesou
        # ``format`` é publicado na entrada da fila (o modelo de resposta atual
        # ainda não o expõe; a UI tolera a ausência).
        stored = client.app.state.services.runtime(cid).get_question(question["id"])
        assert stored["format"] == "binary"
        assert question["question"] == "Atravessar o rio a nadar ou procurar uma ponte?"

        # O log de decisões e as métricas vêm do loop real.
        wait_until(lambda: len(client.get(f"/api/campaigns/{cid}/decisions").json()) >= 2)
        agents = {item["agent"] for item in client.get(f"/api/campaigns/{cid}/decisions").json()}
        assert {"JEV-A", "JEV-D"} <= agents
        assert client.get(f"/api/campaigns/{cid}/metrics").json()["source"] == "live"

        # Parar o loop a partir do controlo fecha-o: nada mais é publicado.
        set_control(client, cid, mode="paused")
        state_after = status(client, cid)
        assert state_after["running"] is False and state_after["reason"] == "paused"

    def test_an_approved_action_is_released_by_the_next_step(self, client: TestClient, campaign):
        """GAP 2 ponta-a-ponta: aprovar em ``checkpoint`` liberta a ação retida.

        Com banda ``auto`` e sem dúvida do JEV-D, o único gate é o checkpoint: o
        loop pergunta, o humano aprova na fila de aprovações e o passo seguinte
        executa a **mesma** ação (a aprovação é consumida — uma só execução).
        """
        services = client.app.state.services
        cid = campaign["id"]
        client.post(f"/api/campaigns/{cid}/keys", json={"provider": "typesafe", "key": SECRET})
        client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

        # ``ask_human`` baixo + confiança auto ⇒ o formatador não vê dúvida e o loop
        # publica a pergunta determinística de aprovação do checkpoint.
        jev = FakeJev(action_id="attack:goblin_skulker", action_confidence=0.97, ask_human=0.05)
        executor = FakeExecutor()
        state = game_state(hostiles=[hostile()])

        def builder():
            return build_loop(
                services,
                services.resolve(cid),
                game_factory=lambda: FakeGame(state),
                jev_factory=lambda key, **kwargs: jev,
                llm_factory=lambda get_key, **kwargs: FakeLLM(),
                executor_factory=lambda game, config: executor,
            )

        services.loop_builder = builder
        set_control(client, cid, mode="checkpoint")

        approval = wait_until(
            lambda: (client.get(f"/api/campaigns/{cid}/approvals").json() or [None])[0]
        )
        assert approval["action_id"] == "attack:goblin_skulker"
        assert approval["status"] == "pending"
        assert executor.action_ids == []      # retida: o checkpoint exige o humano

        body = client.post(
            f"/api/campaigns/{cid}/approvals/{approval['id']}/approve"
        ).json()
        assert body["status"] == "approved"

        # O passo seguinte do loop libertou a ação (executou como se fosse auto).
        assert wait_until(lambda: "attack:goblin_skulker" in executor.action_ids)

        # A aprovação foi consumida: fica registada a libertação e não volta a usar
        # — nenhuma outra aprovação foi aprovada, logo só houve uma execução.
        stored = client.app.state.services.runtime(cid).get_approval(approval["id"])
        assert stored["released_at"]
        assert stored["released_action_id"] == "attack:goblin_skulker"
        assert executor.action_ids == ["attack:goblin_skulker"]

        set_control(client, cid, mode="paused")