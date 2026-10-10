"""Fakes do loop de decisão — offline, sem rede, sem jogo, sem CoALA.

Este helper é **meu** (``tests/agent/fakes_loop.py``); não toca no
``tests/agent/conftest.py`` da outra peça. Os fakes usam, sempre que possível, os
**tipos reais** dos vizinhos (``Answer``/``DecisionResult`` do cliente JEV,
``GameState.from_dict`` do estado do jogo, ``NarrationResult``/``Skip`` do
narrador, ``HumanQuestion`` do formatador) — assim o teste verifica o contrato a
sério, e não uma maqueta. Se uma peça vizinha estiver a meio de uma escrita, há
um fallback local mínimo e o teste continua a correr.

O fake da ponte devolve relatórios **no formato do ``act batch``** e o executor
usado nos testes é o ``MacroExecutor`` real (com runner falso): o caminho
loop → decisão → plano DSL → comando da ponte é exercido de ponta a ponta.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from system_one_dwarf.agent.types import ExecutionResult, TimeAdvanceResult
from system_one_dwarf.game.models import GameState
from system_one_dwarf.jev.client import Answer, DecisionResult, Usage

try:  # peças vizinhas (podem estar a meio de uma escrita)
    from system_one_dwarf.agent.narrator import NarrationDraft, NarrationResult, Skip
except Exception:  # pragma: no cover - fallback local
    NarrationDraft = NarrationResult = Skip = None  # type: ignore[assignment]

try:
    from system_one_dwarf.agent.llm import LLMUsage
except Exception:  # pragma: no cover - fallback local
    @dataclass(frozen=True)
    class LLMUsage:  # type: ignore[no-redef]
        input_tokens: int = 0
        output_tokens: int = 0
        cost_usd: float = 0.0

        def as_dict(self) -> dict[str, Any]:
            return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                    "cost_usd": self.cost_usd}

try:
    from system_one_dwarf.agent.questions import HumanQuestion, NoQuestion, QuestionOption
except Exception:  # pragma: no cover - fallback local
    HumanQuestion = NoQuestion = QuestionOption = None  # type: ignore[assignment]

__all__ = [
    "FakeBridgeRunner",
    "FakeGame",
    "FakeJev",
    "FakeMemory",
    "FakeNarrator",
    "FakeProc",
    "FakeQuestions",
    "FakeSpend",
    "FakeTranslator",
    "RecordingExecutor",
    "RecordingSink",
    "human_question",
    "include_question_name",
    "no_question",
    "state_payload",
    "game_state",
]


# --------------------------------------------------------------- estado do jogo


def state_payload(
    *,
    hostiles: Sequence[Mapping[str, Any]] = (),
    units: Sequence[Mapping[str, Any]] = (),
    items: Sequence[Mapping[str, Any]] = (),
    ground: Sequence[Any] = (),
    landmarks: Sequence[Mapping[str, Any]] = (),
    objective_target: Mapping[str, Any] | None = None,
    hunger: str | None = None,
    thirst: str | None = None,
    sleep: str | None = None,
    stress: float | None = None,
    status: str = "healthy",
    wounds: Sequence[Mapping[str, Any]] = (),
    blood_current: float | None = None,
    blood_max: float | None = None,
    weapon: str | None = None,
    hour: float | None = None,
    date_text: str | None = None,
    season: str | None = None,
    region: str | None = None,
    site: str | None = None,
    surroundings: str | None = None,
    travel_destination: str | None = None,
    x: int | None = None,
    y: int | None = None,
    z: int | None = None,
    focus: str | None = None,
    screen_text: str = "",
    events: Sequence[Any] = (),
    warnings: Sequence[str] = (),
    adventurer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Payload cru do ``snapshot`` (o contrato do ``df_bridge``), para os testes."""
    payload: dict[str, Any] = {
        "schema_version": 1,
        "ts": "2026-10-08T12:00:00.000Z",
        "mode": "adventure",
        "focus": focus,
        "screen_text": screen_text,
        "warnings": list(warnings),
        "gamelog_events": [event if isinstance(event, dict) else {"text": str(event)}
                           for event in events],
        "metrics": {
            "health_injuries": {
                "status": status,
                "status_conditions": [],
                "wounds": [dict(w) for w in wounds],
                "wound_count": len(wounds),
                "pain": None,
                "blood_current": blood_current,
                "blood_max": blood_max,
            },
            "needs_state": {
                "hunger": hunger, "thirst": thirst, "sleep": sleep,
                "hunger_timer": None, "thirst_timer": None, "sleepiness_timer": None,
                "stress_level": stress,
            },
            "inventory": {
                "items": [dict(item) for item in items],
                "item_count": len(items),
                "total_weight_kg": sum(float(i.get("weight_kg") or 0.0) for i in items),
                "capacity_kg": None,
                "equipped_weapon": weapon,
                "equipped_armor": [],
            },
            "threats": {
                "count": len(hostiles),
                "level": "high" if hostiles else "none",
                "hostiles": [dict(h) for h in hostiles],
            },
            "location": {
                "x": x, "y": y, "z": z, "world": "Testworld", "region": region,
                "site": site, "site_type": None, "surroundings": surroundings,
                "tile": None, "travel_destination": travel_destination,
            },
            "game_clock": {
                "date_text": date_text, "year": 125, "month": "Granite", "day": 15,
                "season": season, "tick": None, "hour": hour,
            },
            "units_nearby": {
                "count": len(units), "units": [dict(u) for u in units],
            },
            "events": [],
        },
    }
    if adventurer is not None:
        payload["adventurer"] = dict(adventurer)
    if ground:
        payload["items_on_ground"] = list(ground)
    if landmarks:
        payload["landmarks"] = [dict(entry) for entry in landmarks]
    if objective_target is not None:
        payload["objective_target"] = dict(objective_target)
    return payload


def game_state(**kwargs: Any) -> GameState:
    """``GameState`` tipado (passa pelo parser real do snapshot)."""
    return GameState.from_dict(state_payload(**kwargs))


def hostile(name: str = "goblin skulker", distance: float = 6.0, direction: str = "NE",
            great_danger: bool = False, visible: bool = True, hostile_flag: bool = True,
            **extra: Any) -> dict[str, Any]:
    out = {"name": name, "distance": distance, "direction": direction, "hostile": hostile_flag,
           "visible": visible, "great_danger": great_danger}
    out.update(extra)
    return out


def friendly(name: str = "hunter", distance: float = 3.0, direction: str = "S",
             **extra: Any) -> dict[str, Any]:
    """Unidade não-hostil **sapiente** (pessoa: alvo de conversa, nunca de caça)."""
    out = {"name": name, "distance": distance, "direction": direction, "hostile": False,
           "visible": True, "great_danger": False, "kind": "sapient"}
    out.update(extra)
    return out


def animal(name: str = "donkey", distance: float = 2.0, direction: str = "N",
           *, x: int | None = 88, y: int | None = 70, z: int | None = 0,
           **extra: Any) -> dict[str, Any]:
    """Criatura não-sapiente visível (presa de caça), com posição para o ``goto``."""
    out = {"name": name, "distance": distance, "direction": direction, "hostile": False,
           "visible": True, "great_danger": False, "kind": "animal",
           "pos": {"x": x, "y": y, "z": z}}
    out.update(extra)
    return out


# --------------------------------------------------------------------- JEV


def _sanitize(value: Any) -> str:
    """Mesma regra de ``jev/fleet._sanitize_name`` (o nome da pergunta ``include_*``)."""
    import re

    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value).strip()).strip("_")
    return text or "item"


def include_question_name(item_id: Any) -> str:
    """Nome exato que o JEV-R usa para o candidato ``item_id``."""
    return f"include_{_sanitize(item_id)}"


@dataclass
class FakeJev:
    """Cliente JEV falso: responde por **papel** (``meta.role``), com tipos reais.

    Grava cada pedido em :attr:`requests` (``(role, request)``) e devolve um
    :class:`DecisionResult` construído com :class:`Answer` verdadeiros, para que
    o loop seja exercido contra o contrato do cliente.
    """

    action_id: str | None = None          # None → primeira ação não-saída
    action_confidence: float | None = 0.95
    urgency: float = 0.66
    needs_human: float = 0.02
    update_memory: float = 0.9
    effort: str = "high"
    record_type: str = "episode"
    ask_human: float = 0.5
    question_format: str = "binary"
    selected_ids: Sequence[str] = ()      # ids de memória que o JEV-R deixa entrar
    #: Respostas extra por papel (``{role: {nome_da_pergunta: Answer}}``) — serve
    #: para injetar respostas que o pedido não continha (drift do JEV-R).
    extra_answers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Perguntas que o fake **não** responde (ex.: ``("action",)``): simula um
    #: decider que omite a pergunta primária e devolve só as outras.
    drop_answers: Sequence[str] = ()
    role_responses: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    fail_roles: Sequence[str] = ()
    costs_usd: float = 0.0001
    model: str = "typesafe/jev-1.13"
    provider: str = "typesafe"
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    requests: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: Perguntas ``include_*`` cujo nome não bateu com o id do candidato (deve ficar vazio).
    name_mismatches: list[tuple[str, str]] = field(default_factory=list)

    # -- API do cliente ----------------------------------------------------
    def decide(self, state: Any, questions: Any, session_id: str | None = None, **kw: Any) -> Any:
        request = {"state": state, "questions": questions, "meta": {"role": "JEV-X"}}
        return self.decide_request(request, session_id=session_id)

    def decide_request(self, request: Mapping[str, Any], session_id: str | None = None,
                       **kw: Any) -> DecisionResult:
        role = str((request.get("meta") or {}).get("role") or "JEV-X")
        with self._lock:
            self.requests.append((role, dict(request)))
            if role in tuple(self.fail_roles):
                raise RuntimeError(f"fake JEV failure for {role}")
            answers = self._answers(role, request)
        return DecisionResult(
            answers=answers,
            usage=Usage(input_tokens=800, output_tokens=40, cost_usd=self.costs_usd),
            latency_ms=120,
            model=self.model,
            raw={"role": role},
            provider=self.provider,
        )

    # -- respostas ---------------------------------------------------------
    def _answers(self, role: str, request: Mapping[str, Any]) -> dict[str, Answer]:
        override = dict(self.role_responses.get(role) or {})
        questions = request.get("questions") or {}
        extra = dict(self.extra_answers.get(role) or {})
        dropped = tuple(str(name) for name in self.drop_answers)
        answers: dict[str, Answer]
        if role == "JEV-A":
            question = questions.get("action") or {}
            criteria = list((question.get("criteria") or {}).keys())
            chosen = override.get("action", self.action_id)
            if chosen is None:
                chosen = next((key for key in criteria if key != "other"), "other")
            probabilities = {key: 0.01 for key in criteria}
            probabilities[chosen] = override.get("probability", self.action_confidence or 0.0)
            answers = {
                "action": self._choice("action", chosen, override.get("confidence", self.action_confidence),
                                       probabilities),
                "urgency": self._score("urgency", override.get("urgency", self.urgency)),
                "needs_human": self._noul("needs_human", override.get("needs_human", self.needs_human)),
            }
        elif role == "JEV-M":
            answers = {
                "update_memory": self._noul("update_memory",
                                            override.get("update_memory", self.update_memory)),
                "narrator_effort": self._choice("narrator_effort",
                                                override.get("effort", self.effort), 0.8),
                "record_type": self._choice("record_type",
                                            override.get("record_type", self.record_type), 0.8),
            }
        elif role == "JEV-D":
            answers = {
                "ask_human": self._noul("ask_human", override.get("ask_human", self.ask_human)),
                "question_format": self._choice("question_format",
                                                override.get("question_format", self.question_format),
                                                0.7),
            }
        elif role == "JEV-R":
            selected = set(str(item) for item in override.get("selected_ids", self.selected_ids))
            labels = ((request.get("meta") or {}).get("candidate_labels") or {})
            answers = {}
            for name in questions:
                if not name.startswith("include_"):
                    continue
                # qual é o candidato desta pergunta? (ordem dos labels C1..Cn)
                index = len(answers)
                item_id = list(labels.values())[index] if index < len(labels) else ""
                probability = 0.9 if str(item_id) in selected else 0.05
                if name != include_question_name(item_id):  # contrato de nomes
                    self.name_mismatches.append((name, str(item_id)))
                    probability = 0.05
                answers[name] = self._noul(name, probability)
            if "dominant_theme" in questions:
                criteria = list((questions["dominant_theme"].get("criteria") or {}).keys())
                answers["dominant_theme"] = self._choice(
                    "dominant_theme", criteria[0] if criteria else "other", 0.6)
        else:
            # papel desconhecido: responde tudo com o valor do override, se houver
            answers = {name: self._noul(name, 0.5) for name in questions} if override else {}
        answers.update(extra)
        # um decider pode omitir perguntas (``drop_answers``): o loop tem de
        # sobreviver a uma resposta primária ausente
        return {name: answer for name, answer in answers.items() if name not in dropped}

    @staticmethod
    def _noul(name: str, value: float | None) -> Answer:
        from system_one_dwarf.jev.bands import band_for_noul

        return Answer(name=name, type="noul", value=value, confidence=None, probabilities=None,
                      legend=None, band=band_for_noul(value), chosen_probability=None)

    @staticmethod
    def _choice(name: str, value: Any, confidence: float | None,
                probabilities: Mapping[str, float] | None = None) -> Answer:
        from system_one_dwarf.jev.bands import band_for_choice_or_score

        # Um decider que NÃO seja o JevClient real pode entregar lixo (0.0, NaN,
        # fora de [0,1]) e o cálculo da banda levantaria: o fake imita esse
        # decider (banda hitl) para o loop ser testado com o pior caso.
        try:
            band = band_for_choice_or_score(confidence)
        except Exception:
            band = "hitl"
        return Answer(name=name, type="choice", value=value, confidence=confidence,
                      probabilities=dict(probabilities) if probabilities else None, legend=None,
                      band=band,
                      chosen_probability=(probabilities or {}).get(value))

    @staticmethod
    def _score(name: str, value: float) -> Answer:
        from system_one_dwarf.jev.bands import band_for_choice_or_score

        return Answer(name=name, type="score", value=value, confidence=0.8, probabilities=None,
                      legend=None, band=band_for_choice_or_score(0.8), chosen_probability=0.8)

    # -- conveniências -----------------------------------------------------
    @property
    def roles(self) -> list[str]:
        return [role for role, _request in self.requests]

    def request_of(self, role: str) -> Mapping[str, Any] | None:
        for recorded_role, request in self.requests:
            if recorded_role == role:
                return request
        return None

    def requests_of(self, role: str) -> list[Mapping[str, Any]]:
        return [request for recorded_role, request in self.requests if recorded_role == role]


# --------------------------------------------------------------------- jogo


class FakeGame:
    """Cliente do jogo falso: devolve snapshots em fila (o último repete-se)."""

    def __init__(self, *states: GameState, bridge_path: str = "/repo/dfhack/df_bridge.py",
                 python: str = "python3", fail_on: Sequence[int] = ()) -> None:
        self.states = list(states) or [game_state()]
        self.bridge_path = bridge_path
        self.python = python
        self.df_path = None
        self.port = None
        self.via = "auto"
        self.fail_on = tuple(fail_on)
        self.calls = 0
        self._lock = threading.Lock()

    def snapshot(self) -> GameState:
        with self._lock:
            self.calls += 1
            index = self.calls - 1
        if self.calls in self.fail_on:
            raise RuntimeError(f"fake game failure on snapshot #{self.calls}")
        if index >= len(self.states):
            index = len(self.states) - 1
        return self.states[index]


# ------------------------------------------------------------------ memória


@dataclass
class FakeMemory:
    """Memória CoALA falsa: a forma que o loop lê **e** a que publica em ``memory_update``."""

    items: Sequence[Mapping[str, Any]] = ()
    narrative_text: str = "Urist left the village and walked north."
    narrative_pt_text: str = "Urist saiu da aldeia e caminhou para norte."
    episodes_list: Sequence[Mapping[str, Any]] = ()
    working_memory_text: str = "[semantic] A bridge crosses the river two days north."
    updated_at_text: str = "2026-10-08T12:00:00.000Z"
    search_error: str | None = None
    searches: list[tuple[str, int]] = field(default_factory=list)

    def search(self, query: str, *, limit: int = 10, **kw: Any) -> list[Mapping[str, Any]]:
        self.searches.append((query, int(limit)))
        if self.search_error:
            raise RuntimeError(self.search_error)
        return list(self.items)[: int(limit)]

    def narrative(self) -> str:
        return self.narrative_text

    def narrative_pt(self) -> str:
        return self.narrative_pt_text

    def episodes(self, limit: int = 50) -> list[Mapping[str, Any]]:
        return [dict(item) for item in self.episodes_list[: int(limit)]]

    def working_memory(self, *, budget: int = 1500, top: int = 12) -> str:
        return self.working_memory_text

    def updated_at(self) -> str | None:
        return self.updated_at_text

    @staticmethod
    def item(item_id: str, content: str, *, tags: str = "df/skills/combat") -> dict[str, Any]:
        return {"id": item_id, "type": "semantic", "content": content, "tags": tags,
                "score": 0.9, "ts": "2026-10-01T00:00:00.000Z"}


# ------------------------------------------------------------------ narrador


@dataclass
class FakeNarrator:
    """Narrador falso: conta chamadas e devolve o resultado real do tipo do narrador."""

    narrative: str = "The skulker closed in and Urist drew his sword."
    record: str = "Urist fought a goblin skulker on the road."
    record_type: str = "episode"
    input_tokens: int = 1500
    output_tokens: int = 220
    cost_usd: float = 0.00042
    latency_ms: int = 900
    model: str = "deepseek/deepseek-v4.1-flash"
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def consider(self, jev_m_answer: Any, recent_events: Sequence[str] = (),
                 narrative_tail: str = "", **kw: Any) -> Any:
        self.calls.append({"answer": jev_m_answer, "events": list(recent_events),
                           "narrative_tail": narrative_tail, "kwargs": dict(kw)})
        if self.fail:
            raise RuntimeError("fake narrator failure")
        effort = _read_answer(jev_m_answer, "narrator_effort") or "medium"
        if NarrationResult is not None and NarrationDraft is not None:
            draft = NarrationDraft(
                narrative=self.narrative, record=self.record, record_type=self.record_type,
                usage=LLMUsage(input_tokens=self.input_tokens, output_tokens=self.output_tokens,
                               cost_usd=self.cost_usd),
                latency_ms=self.latency_ms, model=self.model, effort=str(effort),
                ts="2026-10-08T12:00:01.000Z",
            )
            return NarrationResult(draft=draft, memory={"narrative": {"ok": True}}, errors=())
        return _LocalNarration(narrative=self.narrative, record=self.record,
                               record_type=self.record_type, effort=str(effort),
                               usage=LLMUsage(input_tokens=self.input_tokens,
                                              output_tokens=self.output_tokens,
                                              cost_usd=self.cost_usd),
                               latency_ms=self.latency_ms, model=self.model)

    def narrate(self, *args: Any, **kw: Any) -> Any:  # paridade com a interface documentada
        return self.consider(*args, **kw)

    def skip(self, reason: str = "update_false") -> Any:
        """Um ``Skip`` (sem custo) — usado para simular o JEV-M a dizer não."""
        if Skip is not None:
            return Skip(reason=reason)
        return _LocalSkip(reason=reason)


@dataclass(frozen=True)
class _LocalNarration:
    narrative: str
    record: str
    record_type: str = "episode"
    effort: str = "medium"
    usage: Any = None
    latency_ms: int = 0
    model: str = ""

    @property
    def cost_usd(self) -> float:
        return float(getattr(self.usage, "cost_usd", 0.0) or 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {"narrative": self.narrative, "record": self.record,
                "record_type": self.record_type, "effort": self.effort,
                "usage": getattr(self.usage, "as_dict", lambda: {})(),
                "latency_ms": self.latency_ms, "model": self.model}


@dataclass(frozen=True)
class _LocalSkip:
    reason: str = "update_false"

    @property
    def calls(self) -> int:
        return 0

    @property
    def cost_usd(self) -> float:
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"skipped": True, "reason": self.reason, "cost_usd": 0.0}


def _read_answer(jev_m_answer: Any, name: str) -> Any:
    answers = getattr(jev_m_answer, "answers", None)
    if isinstance(answers, Mapping):
        answer = answers.get(name)
        return getattr(answer, "value", answer)
    if isinstance(jev_m_answer, Mapping):
        return jev_m_answer.get(name)
    return None


# ---------------------------------------------------------------- perguntas


def human_question(question_pt: str = "Atravessar o rio a nadar ou procurar uma ponte?",
                   options: Sequence[tuple[str, str]] = (("opt-1", "Nadar"), ("opt-2", "Procurar ponte")),
                   format: str = "binary", why_pt: str = "as opções estavam quase empatadas") -> Any:
    """``HumanQuestion`` real quando disponível (senão um equivalente local)."""
    if HumanQuestion is not None and QuestionOption is not None:
        return HumanQuestion(
            question_pt=question_pt,
            options=tuple(QuestionOption(id=oid, label_pt=label) for oid, label in options),
            format=format, why_pt=why_pt,
            source_data={"reason": "jev_d_asked"},
            ts="2026-10-08T12:00:02.000Z",
        )
    return _LocalQuestion(question_pt=question_pt, options=tuple(options), format=format,
                          why_pt=why_pt)


def no_question(reason: str = "auto_band_no_doubt") -> Any:
    """``NoQuestion`` real quando disponível (senão um equivalente local)."""
    if NoQuestion is not None:
        return NoQuestion(reason=reason, confidence=0.95, band="auto")
    return _LocalNoQuestion(reason=reason)


@dataclass(frozen=True)
class _LocalQuestion:
    question_pt: str
    options: tuple[tuple[str, str], ...] = ()
    format: str = "binary"
    why_pt: str = ""

    def to_queue_entry(self) -> dict[str, Any]:
        return {
            "question": self.question_pt,
            "options": [{"id": oid, "text": text} for oid, text in self.options],
            "free_text_allowed": True,
        }

    def as_dict(self) -> dict[str, Any]:
        payload = self.to_queue_entry()
        payload.update({"question_pt": self.question_pt, "format": self.format,
                        "why_pt": self.why_pt, "source_data": {}})
        return payload


@dataclass(frozen=True)
class _LocalNoQuestion:
    reason: str = "auto_band_no_doubt"

    @property
    def asks(self) -> bool:
        return False

    @property
    def cost_usd(self) -> float:
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"asked": False, "reason": self.reason}


@dataclass
class FakeQuestions:
    """Formatador de perguntas falso (interface documentada do ``QuestionFormatter``)."""

    result: Any = None
    fail: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def format(self, jev_d_answer: Any, context: str = "", action_confidence: float | None = None,
               candidate_actions: Sequence[str] = (), **kw: Any) -> Any:
        self.calls.append({"answer": jev_d_answer, "context": context,
                           "action_confidence": action_confidence,
                           "candidate_actions": list(candidate_actions), "kwargs": dict(kw)})
        if self.fail:
            raise RuntimeError("fake question formatter failure")
        return self.result if self.result is not None else human_question()


# ------------------------------------------------------------------ tradutor


@dataclass
class FakeTranslator:
    """Tradutor de ecrã falso (fire-and-forget no loop)."""

    prefix: str = "[PT] "
    calls: list[tuple[str, Any]] = field(default_factory=list)
    usage: Any = None

    def translate_screen(self, screen_text: str, previous_translation: Any = None) -> Any:
        self.calls.append((screen_text, previous_translation))
        return _LocalScreenTranslation(text_pt=self.prefix + " ".join((screen_text or "").split()))

    def to_pt(self, text: str) -> str:
        return self.prefix + text

    def to_en(self, text: str) -> str:
        return text


@dataclass(frozen=True)
class _LocalScreenTranslation:
    text_pt: str
    usage: Any = None
    latency_ms: int = 0
    model: str = ""
    llm_calls: int = 0

    @property
    def translated_pt(self) -> str:
        return self.text_pt


# --------------------------------------------------------------------- gastos


@dataclass
class FakeSpend:
    """``SpendLedger`` falso: grava as linhas e devolve o payload de ``/spend``."""

    rows: list[dict[str, Any]] = field(default_factory=list)

    def record(self, provider: str, model: str, *, cost_usd: float = 0.0,
               input_tokens: int = 0, output_tokens: int = 0,
               reason: str | None = None, ts: str | None = None) -> dict[str, Any]:
        row = {"provider": provider, "model": model, "cost_usd": round(float(cost_usd), 6),
               "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
               "reason": reason, "ts": ts or "2026-10-08T12:00:00.000Z"}
        self.rows.append(row)
        return row

    def totals(self) -> dict[str, Any]:
        grouped: dict[str, dict[str, dict[str, Any]]] = {}
        for row in self.rows:
            bucket = grouped.setdefault(row["provider"], {}).setdefault(
                row["model"], {"model": row["model"], "calls": 0, "input_tokens": 0,
                               "output_tokens": 0, "cost_usd": 0.0})
            bucket["calls"] += 1
            bucket["input_tokens"] += row["input_tokens"]
            bucket["output_tokens"] += row["output_tokens"]
            bucket["cost_usd"] = round(bucket["cost_usd"] + row["cost_usd"], 6)
        providers = [{"provider": provider, "models": list(models.values())}
                     for provider, models in grouped.items()]
        return {
            "total_usd": round(sum(row["cost_usd"] for row in self.rows), 6),
            "providers": providers,
            "ts": "2026-10-08T12:00:03.000Z",
        }

    def reasons(self) -> list[str | None]:
        return [row["reason"] for row in self.rows]


# ---------------------------------------------------------------------- sink


@dataclass
class RecordingSink:
    """Sink de eventos em memória (thread-safe: o loop publica de várias threads)."""

    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def publish(self, event_type: str, payload: Mapping[str, Any]) -> None:
        with self._lock:
            self.events.append((event_type, dict(payload)))

    def types(self) -> list[str]:
        with self._lock:
            return [event_type for event_type, _payload in self.events]

    def of(self, event_type: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(payload) for kind, payload in self.events if kind == event_type]

    def last(self, event_type: str) -> dict[str, Any] | None:
        found = self.of(event_type)
        return found[-1] if found else None

    def count(self, event_type: str) -> int:
        return len(self.of(event_type))


# ------------------------------------------------------------ ponte / execução


@dataclass
class FakeProc:
    """``subprocess.CompletedProcess`` mínimo."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


BATCH_OK = {
    "ok": True, "via": "tcp", "dry_run": False,
    "steps": [{"i": 0, "op": "move", "ok": True, "threats": {"count": 0}}],
    "summary": {"total": 1, "executed": 1, "ok": 1, "failed": 0, "move_steps": 1},
    "aborted_at": None, "reason": None, "journal": None,
}


@dataclass
class FakeBridgeRunner:
    """Runner da ponte: grava os ``argv`` e responde no formato do ``act batch``.

    ``batch_returncode``/``batch_report`` permitem simular o abort da ponte
    (``--stop-on-threat`` → exit 13 + ``reason: "threat"``) sem tocar no jogo.
    """

    batch_report: Mapping[str, Any] | None = None
    batch_returncode: int = 0
    calls: list[tuple[str, ...]] = field(default_factory=list)

    def __call__(self, argv: Sequence[str], timeout: float | None = None) -> FakeProc:
        argv = tuple(str(part) for part in argv)
        self.calls.append(argv)
        if "--dry-run" in argv:
            return FakeProc(0, json.dumps({"ok": True, "dry_run": True, "steps": [],
                                           "summary": {"total": 0, "validated": 0},
                                           "aborted_at": None, "reason": None}))
        if "act" in argv and "batch" in argv:
            report = dict(self.batch_report if self.batch_report is not None else BATCH_OK)
            return FakeProc(self.batch_returncode, json.dumps(report), "")
        return FakeProc(0, json.dumps({"ok": True, "via": "tcp"}), "")

    # -- consultas ---------------------------------------------------------
    def batch_calls(self) -> list[tuple[str, ...]]:
        return [argv for argv in self.calls if "batch" in argv]

    def subcommands(self) -> list[str]:
        out = []
        for argv in self.calls:
            for part in argv[2:]:
                if part in ("act", "unpause", "pause", "snapshot", "state", "run"):
                    out.append(part)
                    break
        return out


@dataclass
class RecordingExecutor:
    """``MacroExecutor`` **real** com runner falso + memória do que executou.

    O plano DSL, os gates de segurança e o *guard* são os verdadeiros; só o
    processo da ponte é falso. ``execute()`` pode ser forçado a abortar com
    :attr:`abort_reason` para exercer o caminho "a situação mudou".
    """

    inner: Any = None
    abort_reason: str | None = None
    executions: list[tuple[Any, Any]] = field(default_factory=list)
    advances: list[float] = field(default_factory=list)

    @classmethod
    def build(cls, game: Any, config: Any = None, runner: Any = None,
              *, abort_reason: str | None = None, guard: bool = True,
              sleeper: Any = None, dry_run: bool = False) -> "RecordingExecutor":
        from system_one_dwarf.agent.executor import MacroExecutor

        inner = MacroExecutor(game, config, runner=runner or FakeBridgeRunner(), guard=guard,
                              sleeper=sleeper, dry_run=dry_run)
        return cls(inner=inner, abort_reason=abort_reason)

    # -- delegação ---------------------------------------------------------
    def __getattr__(self, name: str) -> Any:  # plan/plan_dsl/executable_dsl/commands/…
        if name == "inner":  # antes do __init__ do dataclass: não recursar
            raise AttributeError(name)
        return getattr(self.inner, name)

    def execute(self, action: Any, state: Any) -> ExecutionResult:
        self.executions.append((action, state))
        if self.abort_reason:
            return ExecutionResult(action_id=action.id, status="aborted", ok=False,
                                   reason=self.abort_reason, dsl=self.inner.plan_dsl(action, state),
                                   steps_executed=0)
        return self.inner.execute(action, state)

    def advance_time(self, seconds: float | None = None) -> TimeAdvanceResult:
        self.advances.append(float(seconds or 0.0))
        return self.inner.advance_time(seconds)

    # -- consultas ---------------------------------------------------------
    @property
    def actions(self) -> list[Any]:
        return [action for action, _state in self.executions]

    def action_ids(self) -> list[str]:
        return [action.id for action in self.actions]

    def runner(self) -> Any:
        return self.inner.runner

    def dsl_of(self, index: int = 0) -> str:
        action, state = self.executions[index]
        return self.inner.plan_dsl(action, state)


def contains_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)
