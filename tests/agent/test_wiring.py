"""Wiring: the loop slice composes with the *real* neighbouring pieces.

One test walks the same steps the decision loop will run:

1. JEV-M answers (a real ``DecisionResult``) -> :class:`Narrator` -> real
   ``MemoryStore`` (CoALA CLI faked) and the real ``SpendLedger`` through
   ``usage_callback``;
2. JEV-D answers (a real ``DecisionResult``) -> :class:`QuestionFormatter` ->
   the real ``CampaignRuntime`` question queue, validated by the API's pydantic
   ``QuestionOut`` model.

Offline: fake transport, fake CoALA CLI, SQLite in ``tmp_path``, and the autouse
socket guard from ``conftest.py``.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

from system_one_dwarf.agent import (
    EPISODE,
    DeepSeekLLM,
    HumanQuestion,
    Narrator,
    QuestionFormatter,
    NarrationResult,
    spend_recorder,
)
from system_one_dwarf.app.runtime import CampaignRuntime
from system_one_dwarf.app.schemas import QuestionOut
from system_one_dwarf.campaign.memory import MemoryStore
from system_one_dwarf.campaign.spend import SpendLedger
from system_one_dwarf.jev.client import Answer, DecisionResult
from system_one_dwarf.jev.client import Usage as JevUsage

from .conftest import (
    FAKE_KEY,
    FakeTransport,
    narration_payload,
    question_payload,
)

NOW = "2026-10-09T18:30:00.000Z"
EVENTS = ["Climbed the ridge.", "A pack of wolves is howling to the north."]


def _app_fakes() -> Any:
    path = pathlib.Path(__file__).resolve().parents[1] / "app" / "fakes.py"
    spec = importlib.util.spec_from_file_location("wiring_test_app_fakes", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _answer(name: str, type_: str, value: Any, confidence: float | None = None) -> Answer:
    return Answer(
        name=name,
        type=type_,
        value=value,
        confidence=confidence,
        probabilities=None,
        legend=None,
        band="hitl",
        chosen_probability=confidence,
    )


def _result(**answers: Answer) -> DecisionResult:
    return DecisionResult(
        answers=answers,
        usage=JevUsage(input_tokens=476, output_tokens=70, cost_usd=0.00002),
        latency_ms=11,
        model="typesafe/jev-1.13",
        raw={},
    )


def test_narrator_and_questions_feed_the_real_memory_spend_and_queue(tmp_path):
    # -- the real neighbours, offline ------------------------------------- #
    ledger = SpendLedger(tmp_path / "spend.sqlite")
    store = MemoryStore(
        tmp_path,
        "camp",
        cli=_app_fakes().FakeCoalaCli(tmp_path / ".agents" / "camp" / "scripts" / "coala.py"),
    )
    runtime = CampaignRuntime("c-1")
    transport = FakeTransport(
        narration_payload(
            "The adventurer crossed the ridge and heard wolves to the north.",
            "Heard a wolf pack north of the ridge and moved south.",
        ),
        question_payload(
            "Seguir para sul, longe dos lobos, ou subir para um ponto mais alto?",
            ["Seguir para sul", "Subir para um ponto mais alto"],
            "A ameaça está próxima e o caminho seguro não é claro.",
        ),
    )
    llm = DeepSeekLLM(
        lambda: FAKE_KEY, transport=transport, usage_callback=spend_recorder(ledger)
    )
    narrator = Narrator(llm, store)
    formatter = QuestionFormatter(llm)

    # -- step 5 of the loop: JEV-M -> narrator -> memory ------------------- #
    jev_m = _result(
        update_memory=_answer("update_memory", "noul", 0.94),
        narrator_effort=_answer("narrator_effort", "choice", "high", 0.83),
        record_type=_answer("record_type", "choice", EPISODE, 0.79),
    )
    outcome = narrator.consider(jev_m.answers, EVENTS, store.narrative(), now=NOW)

    assert isinstance(outcome, NarrationResult)
    assert outcome.effort == "high"
    assert transport.bodies[0]["reasoning"] == {"effort": "high"}
    assert store.narrative() == outcome.narrative
    episode = store.episodes()[0]
    assert episode["content"] == f"{NOW} — Heard a wolf pack north of the ridge and moved south."

    # -- step 8 of the loop: JEV-D -> question formatter -> UI queue ------- #
    jev_d = _result(
        ask_human=_answer("ask_human", "noul", 0.91),
        question_format=_answer("question_format", "choice", "binary", 0.66),
    )
    question = formatter.format(
        jev_d.answers,
        context="Wolves are howling to the north; the adventurer is on an exposed ridge.",
        action_confidence=0.58,  # hitl band -> the human is asked
        candidate_actions=["move south", "climb higher"],
        now=NOW,
    )

    assert isinstance(question, HumanQuestion)
    assert question.format == "binary"
    assert len(question.options) == 2
    stored = runtime.add_question(question.to_queue_entry())
    assert QuestionOut(**stored).options[1].text == "Subir para um ponto mais alto"
    assert stored["status"] == "pending"

    # -- accounting: both calls reached the campaign spend ledger ---------- #
    totals = ledger.totals()
    assert ledger.calls() == 2
    assert totals["total_usd"] == 0.000046  # 2 x the fake provider cost
    assert totals["providers"][0]["provider"] == "openrouter"
    model = totals["providers"][0]["models"][0]
    assert model["model"] == "deepseek/deepseek-v4.1-flash"
    assert model["input_tokens"] == 240 and model["output_tokens"] == 80


def test_narrator_reuses_the_rolling_line_it_stored_between_turns(tmp_path):
    store = MemoryStore(
        tmp_path,
        "camp",
        cli=_app_fakes().FakeCoalaCli(tmp_path / ".agents" / "camp" / "scripts" / "coala.py"),
    )
    transport = FakeTransport(
        narration_payload("First line: left the clearing.", "Left the clearing."),
        narration_payload("Second line: reached the river.", "Reached the river."),
    )
    narrator = Narrator(DeepSeekLLM(lambda: FAKE_KEY, transport=transport), store)
    answer = {"update": 0.9, "effort": "medium", "record_type": EPISODE}

    narrator.consider(answer, ["Left the clearing."], store.narrative(), now=NOW)
    narrator.consider(answer, ["Reached the river."], store.narrative(), now=NOW)

    # the second prompt carried the stored line as the narrative tail
    assert "First line: left the clearing." in transport.bodies[1]["messages"][1]["content"]
    assert store.narrative() == "Second line: reached the river."
    assert len(store.episodes()) == 2