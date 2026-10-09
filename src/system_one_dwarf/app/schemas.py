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


class ObjectiveIn(_Model):
    narrative: str = ""
    subgoals: list[SubgoalIn] = Field(default_factory=list)


class ObjectiveOut(_Model):
    narrative: str
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
    status: str = "healthy"


class NeedsMetrics(_Model):
    hunger: str = "none"
    thirst: str = "none"
    sleep: str = "rested"


class InventoryMetrics(_Model):
    weight_kg: float = 0.0
    items: list[Any] = Field(default_factory=list)


class ThreatsMetrics(_Model):
    level: str = "low"
    combat: bool = False
    hostiles: list[Any] = Field(default_factory=list)


class LocationMetrics(_Model):
    region: str = ""
    path: list[Any] = Field(default_factory=list)


class ObjectiveProgressMetrics(_Model):
    percent: float = 0.0
    active_subgoal_id: str | None = None


class AgentMetrics(_Model):
    decisions_per_min: float = 0.0
    confidence_avg: float = 0.0
    calls: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0


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
    probabilities: dict[str, float] = Field(default_factory=dict)
    confidence: float | None = None
    band: DecisionBand | str = "auto"
    latency_ms: int = 0
    cost_usd: float = 0.0


# --------------------------------------------------------------------- questions


class QuestionOption(_Model):
    id: str
    text: str


class QuestionOut(_Model):
    """Exactly the 6 fields of the contract's queue entry (answers live elsewhere)."""

    id: str
    ts: str
    question: str
    options: list[QuestionOption] = Field(default_factory=list)
    free_text_allowed: bool = True
    status: QuestionStatus | str = "pending"


class AnswerRequest(_Model):
    answer_text: str | None = None
    option_id: str | None = None


class AnswerOut(_Model):
    id: str
    status: str
    answer_text: str | None = None
    option_id: str | None = None


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
