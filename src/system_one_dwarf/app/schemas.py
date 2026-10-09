"""Pydantic models mirroring ``docs/api.md`` **exactly**.

Naming rule of the contract: field names in English; a translated field carries
the ``_pt`` suffix and is read-only for the client (except in ``PATCH .../memory``
where the server translates PT -> EN before storing).

Money is a ``float`` in USD with 6 decimals; timestamps are ISO-8601 UTC with a
literal ``Z``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Provider = Literal["openrouter", "typesafe", "deepseek"]
ControlMode = Literal["autopilot", "checkpoint", "paused"]
AgentName = Literal["JEV-R", "JEV-A", "JEV-M", "JEV-D"]
DecisionBand = Literal["auto", "hitl", "abstain"]
QuestionStatus = Literal["pending", "answered"]
ApprovalStatus = Literal["pending", "approved", "rejected"]

PROVIDERS: tuple[str, ...] = ("openrouter", "typesafe", "deepseek")
MODES: tuple[str, ...] = ("autopilot", "checkpoint", "paused")


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


# ----------------------------------------------------------------------- health


class HealthResponse(_Model):
    status: str
    version: str


# -------------------------------------------------------------------- campaigns


class ControlState(_Model):
    mode: ControlMode
    kill_switch: bool


class ControlRequest(_Model):
    mode: ControlMode
    kill_switch: bool = False


class ControlResponse(ControlState):
    ts: str


class CampaignCreate(_Model):
    name: str = Field(min_length=1, max_length=120)
    is_new_game: bool = True


class CampaignOut(_Model):
    id: str
    name: str
    is_new_game: bool
    created_at: str
    updated_at: str
    control: ControlState


# -------------------------------------------------------------------------- keys


class KeyUpsert(_Model):
    provider: Provider
    key: str = Field(min_length=1)


class KeyOut(_Model):
    provider: str
    masked_key: str | None = None
    set: bool


# ------------------------------------------------------------------------ memory


class EpisodeOut(_Model):
    id: str
    ts: str
    kind: str
    content: str
    content_pt: str


class MemoryOut(_Model):
    narrative: str
    narrative_pt: str
    episodes: list[EpisodeOut] = Field(default_factory=list)
    working_memory: str = ""
    updated_at: str


class EpisodePatch(_Model):
    id: str | None = None
    content: str = ""


class MemoryPatch(_Model):
    """Partial body: send only what changes (PT is translated to EN server-side)."""

    narrative: str | None = None
    episodes: list[EpisodePatch] | None = None


# --------------------------------------------------------------------- objective


class SubgoalIn(_Model):
    id: str | None = None
    text: str = ""
    done: bool = False


class SubgoalOut(_Model):
    id: str
    text: str
    done: bool
    #: Aditivo — exibição PT do ``text`` canónico (EN).
    text_pt: str | None = None


class ObjectiveIn(_Model):
    narrative: str = ""
    subgoals: list[SubgoalIn] = Field(default_factory=list)


class ObjectiveOut(_Model):
    narrative: str
    #: Aditivo — exibição PT do ``narrative`` canónico (EN).
    narrative_pt: str | None = None
    subgoals: list[SubgoalOut] = Field(default_factory=list)


# ------------------------------------------------------------------------ inputs


class InputCreate(_Model):
    text: str = Field(min_length=1)


class InputOut(_Model):
    id: str
    text: str
    ts: str


# ----------------------------------------------------------------------- metrics


class HealthMetrics(_Model):
    wounds: list[Any] = Field(default_factory=list)
    #: ``null`` = indisponível (contrato de nulls do snapshot — nunca ``0``/``""``).
    status: str | None = "healthy"


class NeedsMetrics(_Model):
    hunger: str | None = "none"
    thirst: str | None = "none"
    sleep: str | None = "rested"


class InventoryMetrics(_Model):
    #: Peso total do snapshot (``inventory.total_weight_kg``): ``null`` quando o
    #: jogo não expõe massa/capacidade — ``null`` explícito, nunca ``0.0``.
    weight_kg: float | None = 0.0
    items: list[Any] = Field(default_factory=list)


class ThreatsMetrics(_Model):
    level: str | None = "low"
    combat: bool = False
    hostiles: list[Any] = Field(default_factory=list)


class LocationMetrics(_Model):
    region: str | None = ""
    path: list[Any] = Field(default_factory=list)


class ObjectiveProgressMetrics(_Model):
    percent: float | None = 0.0
    active_subgoal_id: str | None = None


class AgentMetrics(_Model):
    decisions_per_min: float | None = 0.0
    confidence_avg: float | None = 0.0
    calls: int | None = 0
    cost_usd: float | None = 0.0
    latency_ms: int | None = 0


class MetricsOut(_Model):
    """The 8 metric groups (+ ``source`` = ``live`` | ``stub``)."""

    ts: str
    health: HealthMetrics = Field(default_factory=HealthMetrics)
    needs: NeedsMetrics = Field(default_factory=NeedsMetrics)
    inventory: InventoryMetrics = Field(default_factory=InventoryMetrics)
    threats: ThreatsMetrics = Field(default_factory=ThreatsMetrics)
    location: LocationMetrics = Field(default_factory=LocationMetrics)
    objective_progress: ObjectiveProgressMetrics = Field(
        default_factory=ObjectiveProgressMetrics
    )
    combat_history: list[Any] = Field(default_factory=list)
    agent: AgentMetrics = Field(default_factory=AgentMetrics)
    source: str | None = None


# ------------------------------------------------------------------------- spend


class ModelSpend(_Model):
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ProviderSpend(_Model):
    provider: str
    models: list[ModelSpend] = Field(default_factory=list)


class SpendOut(_Model):
    total_usd: float
    providers: list[ProviderSpend] = Field(default_factory=list)
    ts: str


# --------------------------------------------------------------------- decisions


class DecisionOut(_Model):
    id: str
    ts: str
    agent: AgentName | str
    request_summary: str = ""
    answers: dict[str, Any] = Field(default_factory=dict)
    #: ``null`` nas decisões sem distribuição (JEV-R/M/D só com ``noul``/``score``)
    #: — o loop publica ``None`` e o valor **não** pode virar ``{}`` (mentiria
    #: sobre uma distribuição que não existe).
    probabilities: dict[str, float] | None = None
    confidence: float | None = None
    band: DecisionBand | str = "auto"
    latency_ms: int = 0
    cost_usd: float = 0.0
    #: **Aditivo** (``docs/api.md``): marcas de auditoria (ex.
    #: ``released_by_approval`` quando a execução foi libertada por uma aprovação
    #: humana).  **Omitida quando vazia** — o contrato manda-a faltar, não vir
    #: ``[]``.
    issues: list[str] | None = Field(default=None, exclude_if=lambda v: v is None)


# --------------------------------------------------------------------- questions


class QuestionOption(_Model):
    id: str
    text: str


class QuestionOut(_Model):
    """The 6 frozen fields of the queue entry + the additive provenance fields.

    The provenance fields (finding #2 of ``docs/uxui-audit-ui.json``) are
    **optional**: they are present only when the loop's ``question_new`` payload
    carries them (``band``, ``action_id``, ``why_pt``, ``source_data``) or when
    they can be imputed from it (``role``).  A client written against the
    original 6-field entry keeps working unchanged.
    """

    id: str
    ts: str
    question: str
    options: list[QuestionOption] = Field(default_factory=list)
    free_text_allowed: bool = True
    status: QuestionStatus | str = "pending"
    #: Banda de ação que originou a pergunta.
    band: DecisionBand | str | None = None
    #: Papel JEV que originou a pergunta (``JEV-A`` ação retida, ``JEV-D`` dúvida).
    role: str | None = None
    #: Ação retida à espera da resposta (quando existe).
    action_id: str | None = None
    #: Explicação em PT do *porquê* da pergunta (human-facing, como ``question``).
    why_pt: str | None = None
    #: Formato da pergunta (aditivo, ex. ``binary``/``open``/``multiple``).
    format: str | None = None
    #: Dados de origem (aditivo, diagnóstico): razão, confiança, candidatos, …
    source_data: dict[str, Any] | None = None


class AnswerRequest(_Model):
    answer_text: str | None = None
    option_id: str | None = None


class AnswerOut(_Model):
    id: str
    status: str
    answer_text: str | None = None
    option_id: str | None = None


# -------------------------------------------------------------------- approvals


class ApprovalOut(_Model):
    """Uma aprovação de ação (modo ``checkpoint``) — ver ``docs/api.md``.

    O ``id`` é o id da pergunta de aprovação que a originou: aprovar/rejeitar é
    responder a essa pergunta, pelo caminho de resposta já existente.  Uma
    aprovação **aprovada** liberta a ação retida **uma única vez**: é consumida
    (``released_at``/``released_action_id``) e o registo de decisão do JEV-A
    fica marcado com ``released_by_approval``.  Recusas nunca libertam.
    """

    id: str
    question_id: str | None = None
    ts: str
    action_id: str
    action_name_en: str
    question: str | None = None
    band: DecisionBand | str = "hitl"
    status: ApprovalStatus | str = "pending"
    note: str | None = None
    decided_at: str | None = None
    description_en: str | None = None
    #: **Aditivo** — quando a aprovação foi consumida para libertar uma execução.
    released_at: str | None = None
    #: **Aditivo** — a ação exata que a aprovação libertou (id de macro-ação).
    released_action_id: str | None = None


class ApprovalDecision(_Model):
    """Corpo (opcional) de ``approve``/``reject``: só uma nota humana."""

    note: str | None = None


# ------------------------------------------------------------------------ screen


class ScreenOut(_Model):
    translated_pt: str
    ts: str
    source: str | None = None


# --------------------------------------------------------------------- websocket


class EventEnvelope(_Model):
    """Server -> client envelope (``WS /ws/campaigns/{id}``)."""

    type: str
    ts: str
    payload: dict[str, Any] = Field(default_factory=dict)


class WsAnswerQuestion(_Model):
    type: Literal["answer_question"]
    question_id: str
    answer_text: str | None = None
    option_id: str | None = None


class WsSetControl(_Model):
    type: Literal["set_control"]
    mode: ControlMode
    kill_switch: bool | None = None


class ErrorDetail(_Model):
    detail: str
