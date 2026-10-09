"""Superfície de checkpoints (item 4 / achado #4): fila de aprovações.

Em ``checkpoint`` o loop retém *todas* as ações e levanta uma pergunta de
aprovação (``source_data.answer_mode == "approval"``).  O app espelha essas
perguntas numa fila de aprovações com approve/reject; a decisão volta pelo
caminho de resposta de pergunta que já existia (memória + eventos).

Em ``autopilot`` não há ações retidas para aprovar, por isso a lista nasce vazia.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes_loop import approval_question, hitl_question
from test_api_telemetry import publish

APPROVAL_KEYS = {"id", "question_id", "ts", "action_id", "action_name_en", "question", "band", "status"}
#: Só presente quando o loop o publica (``source_data.description_en``).
OPTIONAL_KEYS = {"description_en"}


def question_store(client: TestClient, cid: str, qid: str) -> dict:
    """O registo interno da pergunta (o ``GET`` não expõe ``option_id`` por contrato)."""
    return client.app.state.services.runtime(cid).get_question(qid)


def checkpoint(client: TestClient, cid: str) -> None:
    response = client.post(f"/api/campaigns/{cid}/control", json={"mode": "checkpoint"})
    assert response.status_code == 200, response.text


def publish_approval(client: TestClient, cid: str, **kwargs) -> None:
    publish(client, cid, "question_new", approval_question(**kwargs))


class TestQueue:
    def test_autopilot_has_no_approvals(self, client: TestClient, campaign):
        publish_approval(client, campaign["id"])

        response = client.get(f"/api/campaigns/{campaign['id']}/approvals")

        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["x-source"] == "stub"
        # a pergunta continua na fila normal (o checkpoint colapsa nela)
        assert len(client.get(f"/api/campaigns/{campaign['id']}/questions").json()) == 1

    def test_checkpoint_lists_the_pending_approval(self, client: TestClient, campaign):
        checkpoint(client, campaign["id"])
        publish_approval(client, campaign["id"])

        response = client.get(f"/api/campaigns/{campaign['id']}/approvals")
        body = response.json()

        assert response.headers["x-source"] == "live"
        assert len(body) == 1
        # ``note``/``decided_at`` só aparecem depois de decidida
        assert set(body[0]) == APPROVAL_KEYS | OPTIONAL_KEYS
        assert body[0]["id"] == body[0]["question_id"] == "q-3-1"
        assert body[0]["action_id"] == "goto:river_bank"
        assert body[0]["action_name_en"] == "Go to the river bank"
        assert body[0]["question"] == "Posso executar «goto:river_bank»?"
        assert body[0]["band"] == "auto"
        assert body[0]["status"] == "pending"

    def test_a_hitl_question_that_is_not_an_approval_stays_out(self, client: TestClient, campaign):
        checkpoint(client, campaign["id"])
        publish(client, campaign["id"], "question_new", hitl_question())

        assert client.get(f"/api/campaigns/{campaign['id']}/approvals").json() == []

    def test_switching_back_to_autopilot_stops_creating_approvals(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid, qid="q-1-1")
        client.post(f"/api/campaigns/{cid}/control", json={"mode": "autopilot"})
        publish_approval(client, cid, qid="q-2-1")

        pending = client.get(f"/api/campaigns/{cid}/approvals").json()

        assert [item["id"] for item in pending] == ["q-1-1"]

    def test_status_filter_exposes_the_history(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")

        assert client.get(f"/api/campaigns/{cid}/approvals").json() == []
        history = client.get(f"/api/campaigns/{cid}/approvals?status=all").json()
        assert [item["status"] for item in history] == ["approved"]

    def test_approvals_are_per_campaign(self, client: TestClient, campaign):
        other = client.post("/api/campaigns", json={"name": "Boatmurdered"}).json()
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        assert len(client.get(f"/api/campaigns/{cid}/approvals").json()) == 1
        assert client.get(f"/api/campaigns/{other['id']}/approvals").json() == []

    def test_approvals_on_unknown_campaign_is_404(self, client: TestClient):
        assert client.get("/api/campaigns/nope/approvals").status_code == 404


class TestApprove:
    def test_approve_releases_and_records(self, client: TestClient, campaign, fake_cli):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        response = client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve", json={"note": "vai"})

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "approved"
        assert body["note"] == "vai"
        assert body["decided_at"]
        assert set(body) == APPROVAL_KEYS | OPTIONAL_KEYS | {"note", "decided_at"}

        # a pergunta subjacente foi respondida (opção "yes") e a resposta foi
        # para a memória — o mesmo registo de qualquer resposta humana.
        question = client.get(f"/api/campaigns/{cid}/questions").json()[0]
        assert question["status"] == "answered"
        assert question_store(client, cid, "q-3-1")["option_id"] == "yes"
        stored = [entry for entry in fake_cli.entries if entry["key"] == "answer:q-3-1"]
        assert len(stored) == 1
        assert "Aprovado pelo humano" in stored[0]["content"] and "vai" in stored[0]["content"]
        assert stored[0]["tags"] == "human_answer"

    def test_approve_without_a_body_is_allowed(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        body = client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve").json()

        assert body["status"] == "approved"
        assert "note" not in body  # sem nota, o campo (nulo) é omitido

    def test_reject_drops_and_records_the_note(self, client: TestClient, campaign, fake_cli):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        body = client.post(
            f"/api/campaigns/{cid}/approvals/q-3-1/reject", json={"note": "demasiado arriscado"}
        ).json()

        assert body["status"] == "rejected" and body["note"] == "demasiado arriscado"
        question = client.get(f"/api/campaigns/{cid}/questions").json()[0]
        assert question["status"] == "answered"
        assert question_store(client, cid, "q-3-1")["option_id"] == "no"
        stored = [entry for entry in fake_cli.entries if entry["key"] == "answer:q-3-1"]
        assert "Rejeitado pelo humano" in stored[0]["content"]
        assert "demasiado arriscado" in stored[0]["content"]

    def test_double_decision_is_a_conflict(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")

        again = client.post(f"/api/campaigns/{cid}/approvals/q-3-1/reject")

        assert again.status_code == 409
        assert again.json() == {"detail": "approval already approved"}

    def test_unknown_approval_is_404(self, client: TestClient, campaign):
        checkpoint(client, campaign["id"])

        response = client.post(f"/api/campaigns/{campaign['id']}/approvals/q-404/approve")

        assert response.status_code == 404
        assert response.json() == {"detail": "approval not found"}


class TestMirroring:
    def test_answering_the_question_directly_decides_the_approval(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        client.post(
            f"/api/campaigns/{cid}/questions/q-3-1/answer",
            json={"answer_text": "Não faças isso", "option_id": "no"},
        )

        history = client.get(f"/api/campaigns/{cid}/approvals?status=all").json()
        assert history[0]["status"] == "rejected"
        assert history[0]["decided_at"]

    def test_a_free_text_answer_approves_it(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)

        client.post(
            f"/api/campaigns/{cid}/questions/q-3-1/answer", json={"answer_text": "Vai com cuidado"}
        )

        history = client.get(f"/api/campaigns/{cid}/approvals?status=all").json()
        assert history[0]["status"] == "approved"


class TestWebSocket:
    def test_approval_new_and_updated_events(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)

        with client.websocket_connect(f"/ws/campaigns/{cid}") as ws:
            assert ws.receive_json()["type"] == "control_state"
            publish_approval(client, cid)
            first = ws.receive_json()
            second = ws.receive_json()
            client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")
            after = [ws.receive_json() for _ in range(3)]

        assert [first["type"], second["type"]] == ["question_new", "approval_new"]
        assert second["payload"]["status"] == "pending"
        assert second["payload"]["action_id"] == "goto:river_bank"
        # approve publica a resposta da pergunta, a memória e a aprovação
        assert [message["type"] for message in after] == [
            "question_answered",
            "memory_update",
            "approval_updated",
        ]
        assert after[-1]["payload"]["status"] == "approved"


class TestRelease:
    """GAP 2: uma aprovação **aprovada** liberta a ação retida — uma única vez.

    O loop consulta o checker no passo de encaminhamento; o checker do app
    (:class:`~system_one_dwarf.app.approval_flow.ApprovalChecker`) só responde
    *sim* a uma aprovação ``approved`` da **mesma** ação, e consome-a ao usá-la.
    """

    def checker(self, client, cid, **kwargs):
        from system_one_dwarf.app.approval_flow import ApprovalChecker

        return ApprovalChecker(client.app.state.services.runtime(cid), **kwargs)

    def runtime(self, client, cid):
        return client.app.state.services.runtime(cid)

    def test_an_approved_action_releases_once(self, client: TestClient, campaign):
        from system_one_dwarf.app.approval_flow import RELEASED_ACTION_ID, RELEASED_AT

        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        checker = self.checker(client, cid)

        # Pendente não liberta — e não é consumida por tentar.
        assert checker.was_approved("goto:river_bank") is False
        assert self.runtime(client, cid).get_approval("q-3-1").get(RELEASED_AT) is None

        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")

        assert checker.was_approved("goto:river_bank") is True
        assert checker.was_approved("goto:river_bank") is False   # consumida
        stored = self.runtime(client, cid).get_approval("q-3-1")
        assert stored[RELEASED_AT]
        assert stored[RELEASED_ACTION_ID] == "goto:river_bank"
        assert stored["status"] == "approved"      # o histórico fica auditável

    def test_a_rejected_action_never_releases(self, client: TestClient, campaign):
        from system_one_dwarf.app.approval_flow import RELEASED_AT

        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/reject")

        checker = self.checker(client, cid)
        assert checker.was_approved("goto:river_bank") is False
        assert checker.was_approved("goto:river_bank") is False
        assert self.runtime(client, cid).get_approval("q-3-1").get(RELEASED_AT) is None

    def test_only_the_matching_action_releases(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")

        checker = self.checker(client, cid)
        assert checker.was_approved("goto:nowhere") is False
        assert checker.was_approved("") is False
        assert checker.was_approved("goto:river_bank") is True   # intacta até à ação certa

    def test_on_release_receives_the_consumed_record(self, client: TestClient, campaign):
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(f"/api/campaigns/{cid}/approvals/q-3-1/approve")

        released: list[dict] = []
        checker = self.checker(client, cid, on_release=released.append)

        assert checker.was_approved("goto:river_bank") is True
        assert released and released[0]["id"] == "q-3-1"
        assert released[0]["released_action_id"] == "goto:river_bank"
        assert released[0]["status"] == "approved"

    def test_an_old_approval_of_another_question_still_releases_the_action(
        self, client: TestClient, campaign
    ):
        """Uma aprovação antiga (por id) liberta a ação; a mais recente fica pendente."""
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid, qid="q-1-1")
        publish_approval(client, cid, qid="q-2-1")
        client.post(f"/api/campaigns/{cid}/approvals/q-1-1/approve")

        checker = self.checker(client, cid)

        assert checker.was_approved("goto:river_bank") is True
        assert self.runtime(client, cid).get_approval("q-2-1").get("released_at") is None

    def test_approving_through_the_question_answer_path_also_releases(
        self, client: TestClient, campaign
    ):
        """Responder ``yes`` à pergunta espelha a aprovação — e também liberta."""
        cid = campaign["id"]
        checkpoint(client, cid)
        publish_approval(client, cid)
        client.post(
            f"/api/campaigns/{cid}/questions/q-3-1/answer",
            json={"answer_text": "Sim, executa", "option_id": "yes"},
        )

        assert self.runtime(client, cid).get_approval("q-3-1")["status"] == "approved"
        assert self.checker(client, cid).was_approved("goto:river_bank") is True


class TestJevAHoldMirroring:
    """Regressão (defeito B): uma pergunta JEV-A que retém uma ação **sem**
    proveniência de aprovação (sem ``answer_mode``, ``source_data`` vazio) tem de
    espelhar-se na fila de aprovações em ``checkpoint`` — é o único caminho que
    liberta execuções nas bandas ``hitl``/``abstain``.  Idempotente: uma pergunta
    = uma aprovação; o caminho explícito (``answer_mode == "approval"``) mantém o
    id documentado (o id da pergunta).
    """

    def hold(self, *, qid: str = "q-7-7", action_id: str = "explore:savanna_temperate") -> dict:
        """O payload ``question_new`` de uma retenção JEV-A sem proveniência."""
        return {
            "id": qid,
            "ts": "2026-10-08T12:00:05.000Z",
            "question": f"Posso executar «{action_id}»?",
            "options": [
                {"id": "yes", "text": "Sim, executar"},
                {"id": "no", "text": "Não, escolher outra ação"},
            ],
            "free_text_allowed": True,
            "status": "pending",
            "format": "binary",
            "why_pt": "o modo checkpoint exige aprovação humana para todas as ações (banda abstain)",
            "band": "abstain",
            "role": "JEV-A",
            "action_id": action_id,
            "source_data": {},
        }

    def test_the_hold_mirrors_approves_and_marks_the_released_decision(
        self, client: TestClient, campaign
    ):
        cid = campaign["id"]
        checkpoint(client, cid)
        hold = self.hold()
        publish(client, cid, "question_new", hold)
        publish(client, cid, "question_new", hold)  # idempotente: 1 pergunta = 1 aprovação

        approvals = client.get(f"/api/campaigns/{cid}/approvals?status=all").json()
        assert [item["id"] for item in approvals] == [f"appr-{hold['id']}"]
        assert approvals[0]["question_id"] == hold["id"]
        assert approvals[0]["action_id"] == "explore:savanna_temperate"
        assert approvals[0]["band"] == "abstain"
        assert approvals[0]["status"] == "pending"

        # responder à pergunta decide também a aprovação espelhada (align)
        client.post(f"/api/campaigns/{cid}/questions/{hold['id']}/answer", json={"option_id": "yes"})
        answered = client.get(f"/api/campaigns/{cid}/approvals?status=all").json()
        assert answered[0]["status"] == "approved"

        # o caminho explícito continua a espelhar com o id documentado (o da pergunta)
        publish_approval(client, cid, qid="q-9-9")
        ids = [item["id"] for item in client.get(f"/api/campaigns/{cid}/approvals?status=all").json()]
        assert "q-9-9" in ids

        # libertação (evento do loop): o registo do JEV-A publicado ANTES dela
        # ganha a marca documentada — e só ele (a decisão sem marca fica sem o
        # campo, "omitido quando vazio").
        other = {
            "id": "d-8", "ts": "2026-10-08T12:00:04.000Z", "agent": "JEV-D",
            "request_summary": "noul(ask_human)", "answers": {"ask_human": 0.9},
            "probabilities": None, "confidence": None, "band": "abstain",
            "latency_ms": 10, "cost_usd": 0.0,
        }
        released = {
            "id": "d-9", "ts": "2026-10-08T12:00:06.000Z", "agent": "JEV-A",
            "request_summary": "choice(action)", "answers": {"action": hold["action_id"]},
            "probabilities": {hold["action_id"]: 0.7}, "confidence": 0.48, "band": "abstain",
            "latency_ms": 20, "cost_usd": 0.0,
        }
        publish(client, cid, "decision", other)
        publish(client, cid, "decision", released)
        publish(client, cid, "approval_released", {
            "action_id": hold["action_id"],
            "ts": "2026-10-08T12:00:06.100Z",
            "decision": dict(released, issues=["released_by_approval"]),
        })

        log = client.get(f"/api/campaigns/{cid}/decisions").json()
        assert "issues" not in log[0]  # omitido quando vazio (docs/api.md)
        assert log[1]["issues"] == ["released_by_approval"]
