"""Tipos do LOOP DE DECISÃO (a peça que liga JEV + memória + narrador + jogo).

Este módulo é a linguagem comum das três partes do loop:

* :mod:`system_one_dwarf.agent.candidates` produz :class:`MacroAction`;
* :mod:`system_one_dwarf.agent.executor` traduz uma ``MacroAction`` em DSL do
  ``df_bridge`` e devolve :class:`ExecutionResult`;
* :mod:`system_one_dwarf.agent.loop` orquestra os 10 passos e devolve
  :class:`StepResult` (e :class:`RunReport` para uma corrida inteira).

Tipos **frozen** e sem dependências de rede: nada aqui chama o JEV, o jogo ou a
memória. Valores monetários são USD ``float`` (a UI formata com 6 casas, ver
``docs/api.md``); timestamps são ISO-8601 UTC com sufixo ``Z``.

Sobre a *banda* registada: ``noul`` não tem ``confidence`` — a banda de uma
resposta ``noul`` vem da certainty ``|2p − 1|`` (:func:`..jev.bands.band_for_noul`),
e a de ``choice``/``score`` vem da ``confidence`` (indisponível ⇒ no máximo
``hitl``).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = [
    "ACTION_CATEGORIES",
    "MIN_MAX_CANDIDATES",
    "MODE_AUTOPILOT",
    "MODE_CHECKPOINT",
    "MODE_PAUSED",
    "MODES",
    "STATUS_ABORTED",
    "STATUS_ERROR",
    "STATUS_EXECUTED",
    "STATUS_HELD",
    "STATUS_KILLED",
    "STATUS_PAUSED",
    "STATUS_QUESTION",
    "ControlState",
    "DecisionRecord",
    "ExecutionResult",
    "LoopConfig",
    "MacroAction",
    "PlanStep",
    "RunReport",
    "StepResult",
    "TimeAdvanceResult",
    "UsageTotals",
    "utc_now_iso",
]

# ---------------------------------------------------------------- modos / estados

MODE_AUTOPILOT = "autopilot"
MODE_CHECKPOINT = "checkpoint"
MODE_PAUSED = "paused"
MODES: tuple[str, str, str] = (MODE_AUTOPILOT, MODE_CHECKPOINT, MODE_PAUSED)

STATUS_EXECUTED = "executed"
STATUS_ABORTED = "aborted"
STATUS_QUESTION = "question"
STATUS_HELD = "held"
STATUS_PAUSED = "paused"
STATUS_KILLED = "killed"
STATUS_ERROR = "error"

#: Mínimo legal de ``LoopConfig.max_candidates``: uma ação real + ``wait`` +
#: opção de saída. Abaixo disto só caberiam reservas — o teto dos candidatos é
#: exato e o config recusa em vez de devolver mais opções do que o pedido.
MIN_MAX_CANDIDATES = 3

#: Categorias de macro-ação, da mais para a menos prioritária (triagem dos
#: candidatos): sobrevivência > resposta a ameaça > objetivo > conveniência.
ACTION_CATEGORIES: tuple[str, str, str, str] = (
    "survival",
    "threat",
    "objective",
    "convenience",
)

_CATEGORY_RANK = {name: rank for rank, name in enumerate(ACTION_CATEGORIES)}


def utc_now_iso() -> str:
    """ISO-8601 UTC com sufixo ``Z`` (formato do contrato, ``docs/api.md``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ------------------------------------------------------------------- macro-ação


@dataclass(frozen=True)
class MacroAction:
    """Uma macro-ação **gerada por código** (o modelo nunca inventa ações).

    O ``id`` é o valor exato que o JEV-A devolve na pergunta ``choice`` e a
    chave que o executor usa para escolher o plano. A forma canónica é
    ``<verbo>:<alvo>`` (``attack:goblin_skulker``, ``wait:one_turn``), alinhada
    com a vocabulary dos evals (``evals/jev-a-action.json``); ``other`` é a
    opção de saída, sempre presente e sempre por último.

    ``verb`` é o nome semântico da vocabulary do loop (``attack``, ``flee``,
    ``drink_potion``, ``eat``, ``drink``, ``rest``, ``pick_up``, ``talk``,
    ``navigate_to``, ``explore``, ``wait``, ``other``) e ``category`` é o nível
    de triagem. ``params`` carrega o que o executor precisa para montar o DSL
    (direção, distância, coordenadas, nome do alvo/item), sem que o executor
    tenha de reinterpretar o estado do jogo.
    """

    id: str
    name_en: str
    description_en: str
    target: str | None = None
    verb: str = ""
    category: str = "convenience"
    priority: int = 100
    params: Mapping[str, Any] = field(default_factory=dict)
    #: True quando o caminho até ao jogo ainda não está mapeado ao vivo.
    needs_manual_mapping: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("MacroAction.id must be a non-empty string")
        if self.category not in _CATEGORY_RANK:
            raise ValueError(
                f"unknown action category {self.category!r}; expected one of "
                f"{', '.join(ACTION_CATEGORIES)}"
            )

    @property
    def category_rank(self) -> int:
        """0 = sobrevivência … 3 = conveniência (ordem de triagem)."""
        return _CATEGORY_RANK[self.category]

    @property
    def is_exit(self) -> bool:
        """True para a opção de saída (``other``/``none``)."""
        return self.id.strip().lower() in ("other", "none", "none_of_the_above")

    @property
    def sort_key(self) -> tuple[int, int, str]:
        return (self.category_rank, int(self.priority), self.id)

    def as_jev_candidate(self) -> dict[str, str]:
        """Forma aceite por :func:`..jev.fleet.build_action` (``id``/``name``/``description``)."""
        return {
            "id": self.id,
            "name": self.name_en,
            "description": self.description_en,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "verb": self.verb,
            "name_en": self.name_en,
            "description_en": self.description_en,
            "target": self.target,
            "category": self.category,
            "priority": int(self.priority),
            "params": dict(self.params),
            "needs_manual_mapping": bool(self.needs_manual_mapping),
        }

    def with_params(self, **extra: Any) -> "MacroAction":
        return replace(self, params={**dict(self.params), **extra})


# --------------------------------------------------------------------- config


@dataclass(frozen=True)
class LoopConfig:
    """Configuração do loop. Defaults alinhados com ``docs/arquitetura.md``.

    Args:
        auto_threshold / hitl_threshold: bandas de ação (``auto`` ≥ 0.90,
            ``hitl`` 0.50–0.89, ``abstain`` < 0.50). Confidence indisponível
            nunca chega a ``auto`` (regra das bandas).
        retrieval_k: quantos itens de memória entram no prompt (top-K do JEV-R).
        max_candidates: teto **exato** de macro-ações devolvidas por
            :func:`..candidates.generate_actions` (o ``choice`` aceita 255 opções
            mas o prompt degrada com volume). Mínimo ``MIN_MAX_CANDIDATES`` (3):
            uma ação real + ``wait`` + a opção de saída ``other``.
        step_timeout_s: orçamento de tempo de um passo (aplicado aos comandos da
            ponte e verificado nos pontos de controlo do passo).
        advance_seconds: quanto tempo de jogo despausar após executar ("o JEV
            controla o tempo").
        mode: ``autopilot`` | ``checkpoint`` | ``paused`` (valor de arranque; o
            valor vivo vem do callback ``control``).
        kill_switch: paragem imediata, zero chamadas a API.

    Extras do loop (documentados por serem decisões, não acidentes):
        retrieval_threshold: ``p`` mínimo de um ``noul`` do JEV-R para o item de
            conhecimento entrar no prompt (o JEV-R é um gate, não um ranking).
        needs_human_threshold: ``p`` do ``noul`` ``needs_human`` a partir do qual
            o loop escala para o humano (``0.5`` = "mais provável que não").
        memory_update_threshold: ``p`` do ``noul`` ``update_memory`` a partir do
            qual o narrador é chamado (abaixo disso, **zero** chamadas de LLM).
        search_limit: quantos candidatos a memória pede à busca CoALA antes do
            top-K (``retrieval_k``) enviado ao JEV-R.
        parallel_decisions: corre ``JEV-R ∥ JEV-M`` e ``JEV-A ∥ JEV-D`` em duas
            threads (é o desenho do doc); ``False`` serializa (debug).
        translate_screen / screen_translation_async: passo 2 do loop
            (fire-and-forget; a tradução nunca bloqueia a decisão).
        shuffle_options: mitiga o viés para a 1ª opção (o builder baralha).
        advance_on_execute: passo 9 do loop.
        step_interval_s: pausa entre passos (0 = o mais rápido possível).
        objective: objetivo em EN usado no estado crítico e em
            ``objective_progress`` (o loop também aceita um callback/store).
        max_state_chars: orçamento do estado EN (≈2K tokens — context rot).
        soar_*: identificação nas chamadas (``session_id``/provider/model para o
            ledger de gastos quando a resposta não traz o modelo).
    """

    auto_threshold: float = 0.90
    hitl_threshold: float = 0.50
    retrieval_k: int = 8
    max_candidates: int = 12
    step_timeout_s: float = 120.0
    advance_seconds: float = 5.0
    mode: str = MODE_AUTOPILOT
    kill_switch: bool = False

    retrieval_threshold: float = 0.5
    needs_human_threshold: float = 0.5
    memory_update_threshold: float = 0.5
    search_limit: int = 16
    parallel_decisions: bool = True
    translate_screen: bool = True
    screen_translation_async: bool = True
    shuffle_options: bool = True
    advance_on_execute: bool = True
    step_interval_s: float = 0.0
    objective: str = ""
    max_state_chars: int = 8000
    max_events: int = 20
    flee_steps: int = 5
    talk_radius: float = 8.0
    max_items: int = 6
    session_id: str = "system-one-dwarf"
    jev_provider: str = "typesafe"
    jev_model: str = "typesafe/jev-1.13"
    llm_provider: str = "openrouter"
    narrator_model: str = "deepseek/deepseek-v4.1-flash"
    screen_model: str = "deepseek/deepseek-v4.1-flash"
    question_model: str = "deepseek/deepseek-v4.1-flash"
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}; expected one of {', '.join(MODES)}")
        if not 0.0 <= float(self.hitl_threshold) <= float(self.auto_threshold) <= 1.0:
            raise ValueError(
                "thresholds must satisfy 0 <= hitl_threshold <= auto_threshold <= 1, "
                f"got {self.hitl_threshold!r}/{self.auto_threshold!r}"
            )
        if int(self.retrieval_k) < 1:
            raise ValueError("retrieval_k must be >= 1")
        # Mínimo legal: uma ação real + ``wait`` + a opção de saída. Abaixo disto o
        # ``choice`` do JEV-A ficaria só com reservas (o teto dos candidatos é
        # exato, não há "estouro" silencioso): o config recusa, e o carregamento
        # por env/meta reporta o problema e mantém o default.
        if int(self.max_candidates) < MIN_MAX_CANDIDATES:
            raise ValueError(
                f"max_candidates must be >= {MIN_MAX_CANDIDATES} "
                "(one real action plus the reserved wait and exit options), "
                f"got {self.max_candidates!r}"
            )
        if float(self.step_timeout_s) <= 0:
            raise ValueError("step_timeout_s must be > 0")

    def with_control(self, control: "ControlState") -> "LoopConfig":
        """Cópia com o modo/kill-switch vivos do callback ``control``."""
        return replace(self, mode=control.mode, kill_switch=bool(control.kill_switch))

    def as_dict(self) -> dict[str, Any]:
        return {
            "auto_threshold": float(self.auto_threshold),
            "hitl_threshold": float(self.hitl_threshold),
            "retrieval_k": int(self.retrieval_k),
            "max_candidates": int(self.max_candidates),
            "step_timeout_s": float(self.step_timeout_s),
            "advance_seconds": float(self.advance_seconds),
            "mode": self.mode,
            "kill_switch": bool(self.kill_switch),
        }


@dataclass(frozen=True)
class ControlState:
    """Modo de autonomia + kill-switch (``{mode, kill_switch}`` de ``docs/api.md``)."""

    mode: str = MODE_AUTOPILOT
    kill_switch: bool = False

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}; expected one of {', '.join(MODES)}")

    @classmethod
    def from_any(cls, value: Any, default: "ControlState | None" = None) -> "ControlState":
        """Coage o retorno do callback ``control`` para :class:`ControlState`.

        Aceita ``ControlState``, um mapeamento ``{mode, kill_switch}``, um objeto
        com esses atributos (ex.: ``Campaign.control``) ou ``None`` (usa o
        default). Um modo inválido **não** derruba o loop: cai no default e o
        kill-switch continua a ser respeitado.
        """
        fallback = default or cls()
        if value is None:
            return fallback
        if isinstance(value, ControlState):
            return value
        if isinstance(value, Mapping):
            mode, kill = value.get("mode"), value.get("kill_switch")
        else:
            control = getattr(value, "control", None)
            source = control if control is not None else value
            if isinstance(source, Mapping):
                mode, kill = source.get("mode"), source.get("kill_switch")
            else:
                mode = getattr(source, "mode", None)
                kill = getattr(source, "kill_switch", None)
        mode_text = mode if isinstance(mode, str) and mode in MODES else fallback.mode
        return cls(mode=mode_text, kill_switch=bool(kill) if kill is not None else fallback.kill_switch)

    @property
    def paused(self) -> bool:
        return self.mode == MODE_PAUSED

    @property
    def autopilot(self) -> bool:
        return self.mode == MODE_AUTOPILOT

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "kill_switch": bool(self.kill_switch)}


# -------------------------------------------------------------------- registos


@dataclass(frozen=True)
class UsageTotals:
    """Soma de uso/custo de um passo ou de uma corrida inteira."""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0

    def __add__(self, other: "UsageTotals") -> "UsageTotals":
        if not isinstance(other, UsageTotals):
            return NotImplemented
        return UsageTotals(
            calls=int(self.calls) + int(other.calls),
            input_tokens=int(self.input_tokens) + int(other.input_tokens),
            output_tokens=int(self.output_tokens) + int(other.output_tokens),
            cost_usd=round(float(self.cost_usd) + float(other.cost_usd), 6),
            latency_ms=int(self.latency_ms) + int(other.latency_ms),
        )

    @classmethod
    def from_llm_usage(cls, usage: Any, *, calls: int = 1, latency_ms: int = 0) -> "UsageTotals":
        """De um ``LLMUsage``/``Usage`` (``input_tokens``/``output_tokens``/``cost_usd``)."""
        if usage is None:
            return cls(calls=int(calls), latency_ms=int(latency_ms))
        return cls(
            calls=int(calls),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cost_usd=round(float(getattr(usage, "cost_usd", 0.0) or 0.0), 6),
            latency_ms=int(latency_ms),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": int(self.calls),
            "input_tokens": int(self.input_tokens),
            "output_tokens": int(self.output_tokens),
            "cost_usd": round(float(self.cost_usd), 6),
            "latency_ms": int(self.latency_ms),
        }


@dataclass(frozen=True)
class DecisionRecord:
    """Uma decisão do JEV, na forma do log de ``docs/api.md``.

    ``answers`` é indexado pelo **nome da pergunta** (``action``, ``urgency``,
    ``needs_human``, ``update_memory``, …) — é o que o código lê e o que a UI
    mostra. ``confidence`` é ``float`` **ou ``None``**: ``None`` = *UNAVAILABLE*
    (nunca desenhar como ``0``). ``band`` é a banda de encaminhamento do loop
    (calculada com os limiares do :class:`LoopConfig`, não com os do cliente).
    """

    agent: str
    answers: Mapping[str, Any] = field(default_factory=dict)
    probabilities: Mapping[str, float] | None = None
    confidence: float | None = None
    band: str = "hitl"
    latency_ms: int = 0
    cost_usd: float = 0.0
    request_summary: str = ""
    ts: str = ""
    model: str | None = None
    provider: str | None = None
    usage: UsageTotals = field(default_factory=UsageTotals)
    issues: tuple[str, ...] = ()

    @property
    def role(self) -> str:
        """Alias de ``agent`` (``JEV-R`` | ``JEV-A`` | ``JEV-M`` | ``JEV-D``)."""
        return self.agent

    def to_payload(self) -> dict[str, Any]:
        """Entrada do log de decisões (``GET .../decisions`` / evento ``decision``)."""
        payload: dict[str, Any] = {
            "ts": self.ts or utc_now_iso(),
            "agent": self.agent,
            "request_summary": self.request_summary,
            "answers": dict(self.answers),
            "probabilities": dict(self.probabilities) if self.probabilities else None,
            "confidence": self.confidence,
            "band": self.band,
            "latency_ms": int(self.latency_ms),
            "cost_usd": round(float(self.cost_usd), 6),
        }
        if self.model:
            payload["model"] = self.model
        if self.provider:
            payload["provider"] = self.provider
        payload["usage"] = self.usage.as_dict()
        if self.issues:
            payload["issues"] = list(self.issues)
        return payload


# -------------------------------------------------------------------- executor


@dataclass(frozen=True)
class PlanStep:
    """Um passo do plano DSL.

    ``dsl`` é a linha executável (``move n times=3``, ``wait short``, …) quando o
    passo está **verificado**; ``manual`` marca um passo cujo caminho ao vivo
    ainda não está mapeado — nesse caso ``dsl`` pode estar vazio e ``note``
    explica o que falta mapear. Passos manuais **nunca** são executados: o
    executor executa só a parte segura e reporta ``needs_manual_mapping``.
    """

    dsl: str = ""
    manual: bool = False
    note: str = ""


@dataclass(frozen=True)
class ExecutionResult:
    """Resultado de :meth:`..agent.executor.MacroExecutor.execute`."""

    action_id: str
    status: str  # executed | aborted | refused | dry_run | error
    ok: bool = False
    reason: str | None = None
    dsl: str = ""
    executed_dsl: str = ""
    steps_planned: int = 0
    steps_executed: int = 0
    needs_manual_mapping: bool = False
    manual_notes: tuple[str, ...] = ()
    commands: tuple[tuple[str, ...], ...] = ()
    report: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()
    duration_ms: int = 0

    @property
    def aborted(self) -> bool:
        return self.status == "aborted"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "status": self.status,
            "ok": bool(self.ok),
            "reason": self.reason,
            "dsl": self.dsl,
            "executed_dsl": self.executed_dsl,
            "steps_planned": int(self.steps_planned),
            "steps_executed": int(self.steps_executed),
            "needs_manual_mapping": bool(self.needs_manual_mapping),
            "manual_notes": list(self.manual_notes),
            "errors": list(self.errors),
            "duration_ms": int(self.duration_ms),
        }


@dataclass(frozen=True)
class TimeAdvanceResult:
    """Passo 9 do loop: despausar, deixar o jogo correr, voltar a pausar.

    "O JEV controla o tempo": o tempo do jogo só avança depois de uma execução
    bem-sucedida, e sempre com o jogo **pausado** no fim (o loop lê estado
    estável, não um jogo a correr sozinho).
    """

    seconds: float = 0.0
    ok: bool = False
    unpaused: bool = False
    paused: bool = False
    commands: tuple[tuple[str, ...], ...] = ()
    errors: tuple[str, ...] = ()
    duration_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "seconds": float(self.seconds),
            "ok": bool(self.ok),
            "unpaused": bool(self.unpaused),
            "paused": bool(self.paused),
            "errors": list(self.errors),
            "duration_ms": int(self.duration_ms),
        }


# ------------------------------------------------------------------ step / run


@dataclass(frozen=True)
class StepResult:
    """Tudo o que aconteceu num passo do loop (10 passos de ``docs/arquitetura.md``)."""

    index: int = 0
    status: str = STATUS_HELD
    mode: str = MODE_AUTOPILOT
    decisions: tuple[DecisionRecord, ...] = ()
    action: MacroAction | None = None
    executed: ExecutionResult | None = None
    question: Mapping[str, Any] | None = None
    errors: tuple[str, ...] = ()
    usage: UsageTotals = field(default_factory=UsageTotals)
    state_summary: str = ""
    knowledge_ids: tuple[str, ...] = ()
    narration: Mapping[str, Any] | None = None
    metrics: Mapping[str, Any] | None = None
    ts: str = ""
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status != STATUS_ERROR

    @property
    def used_calls(self) -> int:
        return int(self.usage.calls)

    @property
    def action_id(self) -> str | None:
        return self.action.id if self.action is not None else None

    def decision(self, agent: str) -> DecisionRecord | None:
        for record in self.decisions:
            if record.agent == agent:
                return record
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": int(self.index),
            "status": self.status,
            "mode": self.mode,
            "ts": self.ts,
            "duration_ms": int(self.duration_ms),
            "action": self.action.as_dict() if self.action else None,
            "executed": self.executed.as_dict() if self.executed else None,
            "question": dict(self.question) if self.question else None,
            "decisions": [record.to_payload() for record in self.decisions],
            "errors": list(self.errors),
            "usage": self.usage.as_dict(),
            "knowledge_ids": list(self.knowledge_ids),
            "narration": dict(self.narration) if self.narration else None,
            "state_summary": self.state_summary,
        }


@dataclass(frozen=True)
class RunReport:
    """Resultado de :meth:`..agent.loop.AgentLoop.run`."""

    steps: int = 0
    results: tuple[StepResult, ...] = ()
    stopped_reason: str = "max_steps"
    errors: tuple[str, ...] = ()
    usage: UsageTotals = field(default_factory=UsageTotals)
    duration_ms: int = 0

    @property
    def statuses(self) -> tuple[str, ...]:
        return tuple(result.status for result in self.results)

    @property
    def executed(self) -> int:
        return sum(1 for result in self.results if result.status == STATUS_EXECUTED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": int(self.steps),
            "stopped_reason": self.stopped_reason,
            "statuses": list(self.statuses),
            "executed": self.executed,
            "errors": list(self.errors),
            "usage": self.usage.as_dict(),
            "duration_ms": int(self.duration_ms),
        }
