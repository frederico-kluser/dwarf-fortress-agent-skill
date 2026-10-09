"""Agent authoring: the DeepSeek narrator and the human question formatter.

This package is the "write" half of the loop — JEV decides, DeepSeek writes,
code executes:

* :mod:`~system_one_dwarf.agent.llm` — :class:`DeepSeekLLM`, the chat client for
  ``deepseek/deepseek-v4.1-flash`` with the **dynamic reasoning ladder** (the
  JEV-M effort drives ``reasoning``), typed errors, bounded 429 retries and the
  ``usage_callback`` spend-ledger seam;
* :mod:`~system_one_dwarf.agent.narrator` — :class:`Narrator`, which turns the
  JEV-M answer into the English rolling chronicle plus one episode/fact/procedure
  record, storing both through the campaign memory
  (``set_narrative`` / ``add_episode`` / ``add_fact`` / ``add_procedure``);
* :mod:`~system_one_dwarf.agent.questions` — :class:`QuestionFormatter`, which
  turns the JEV-D indecision into ONE short Portuguese question with 2–4 quick
  options, or :class:`NoQuestion` when the ``auto`` band needs no human.

Wiring (production shapes, the key injected at runtime)::

    from system_one_dwarf.agent import DeepSeekLLM, Narrator, QuestionFormatter, spend_recorder

    llm = DeepSeekLLM(get_key, usage_callback=spend_recorder(ledger))
    narrator = Narrator(llm, memory)                 # memory = campaign MemoryStore
    questions = QuestionFormatter(llm)

    outcome = narrator.consider(jev_m.answers, recent_events, memory.narrative())
    asked = questions.format(jev_d.answers, context, confidence, candidates)

Everything the models write into memory is **English**; only human-facing
questions are Portuguese.  No API key is ever hardcoded: it arrives through the
``get_key`` callable.
"""

from __future__ import annotations

from .llm import (
    DEFAULT_ENDPOINT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    MODEL_EFFORTS,
    REASONING_LEVELS,
    REASONING_OFF,
    RETRYABLE_STATUSES,
    DeepSeekLLM,
    LLMAuthError,
    LLMClient,
    LLMError,
    LLMQuotaError,
    LLMRequestError,
    LLMResponseError,
    LLMResult,
    LLMTransportError,
    LLMUsage,
    LLMUsageEvent,
    UsageCallback,
    estimate_cost,
    money,
    parse_usage,
    reasoning_ladder,
    reasoning_mode_name,
    spend_recorder,
)
from .narrator import (
    DEFAULT_MAX_EVENTS,
    DEFAULT_MAX_WORDS,
    DEFAULT_MIN_WORDS,
    DEFAULT_UPDATE_THRESHOLD,
    EPISODE,
    FACT,
    NARRATOR_SYSTEM_PROMPT,
    PROCEDURE,
    SKIP,
    NarrationDraft,
    NarrationResult,
    Narrator,
    NarratorMemory,
    Skip,
    build_prompt_state,
    condense_to_budget,
    parse_narration,
    read_jev_answer,
    word_count,
)
from .questions import (
    ASK_ANSWER_KEYS,
    BINARY,
    FORMAT_ANSWER_KEYS,
    MULTIPLE,
    OPEN,
    QUESTION_FORMATS,
    QUESTION_FORMATTER_SYSTEM_PROMPT,
    HumanQuestion,
    NoQuestion,
    QuestionFormatter,
    QuestionOption,
    parse_question,
    question_cost,
    queue_entry,
    strip_number_leaks,
)

__all__ = [
    # clients & protocols
    "DeepSeekLLM",
    "LLMClient",
    "NarratorMemory",
    "UsageCallback",
    # narrator
    "Narrator",
    "NarrationResult",
    "NarrationDraft",
    "Skip",
    "build_prompt_state",
    "condense_to_budget",
    "parse_narration",
    "read_jev_answer",
    "word_count",
    "NARRATOR_SYSTEM_PROMPT",
    "DEFAULT_MIN_WORDS",
    "DEFAULT_MAX_WORDS",
    "DEFAULT_UPDATE_THRESHOLD",
    "DEFAULT_MAX_EVENTS",
    "EPISODE",
    "FACT",
    "PROCEDURE",
    "SKIP",
    # questions
    "QuestionFormatter",
    "HumanQuestion",
    "QuestionOption",
    "NoQuestion",
    "parse_question",
    "queue_entry",
    "question_cost",
    "strip_number_leaks",
    "QUESTION_FORMATS",
    "QUESTION_FORMATTER_SYSTEM_PROMPT",
    "ASK_ANSWER_KEYS",
    "FORMAT_ANSWER_KEYS",
    "BINARY",
    "MULTIPLE",
    "OPEN",
    # results, usage & errors
    "LLMResult",
    "LLMUsage",
    "LLMUsageEvent",
    "LLMError",
    "LLMAuthError",
    "LLMQuotaError",
    "LLMRequestError",
    "LLMResponseError",
    "LLMTransportError",
    "reasoning_ladder",
    "reasoning_mode_name",
    "estimate_cost",
    "money",
    "parse_usage",
    "spend_recorder",
    "DEFAULT_MODEL",
    "DEFAULT_ENDPOINT",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "DEFAULT_MAX_ATTEMPTS",
    "REASONING_LEVELS",
    "MODEL_EFFORTS",
    "REASONING_OFF",
    "RETRYABLE_STATUSES",
]