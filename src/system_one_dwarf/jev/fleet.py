"""Request builders for the four Jev fleet roles (JEV-R, JEV-A, JEV-M, JEV-D).

Every builder returns a ready-to-send request dict::

    {"state": <compact English state>, "questions": {name: Question},
     "meta": {...}, "ordering": {...}}

so the caller can do ``client.decide_request(build_action(...))``. States are
compact English by design (English is more accurate than Portuguese, and
*context rot* punishes volume), and every builder validates its own output with
:func:`system_one_dwarf.jev.validate.validate_request` before returning.

Roles
-----
``build_retrieval``  JEV-R — one ``noul`` per knowledge candidate (indices +
                     top-K only, never the full memory) plus an optional
                     ``choice`` for the dominant theme.
``build_action``     JEV-A — ``choice`` (which action, always with an exit
                     option) + ``score`` (urgency, concrete situations) +
                     ``noul`` (needs human help?).
``build_memory``     JEV-M — ``noul`` (update the narrative now?) + ``choice``
                     narrator effort + ``choice`` record type.
``build_doubt``      JEV-D — ``noul`` (ask the human?) + ``choice`` question
                     format.

Option-order bias
-----------------
The model leans towards the **first** option of a ``choice``. Builders therefore
shuffle option order by default (a fresh order per call) while keeping ordered
rulers — ``score`` levels and the narrator-effort scale — in their canonical
order, because their order *is* their meaning. ``shuffle_seed=<int>`` gives a
reproducible order (tests, audits), ``shuffle=False`` keeps the canonical order,
and the presented order is reported in ``request["ordering"]``.
"""

from __future__ import annotations

import hashlib
import random
import re
from typing import Any, Iterable, Mapping, Sequence

from .errors import JevRequestError
from .schema import Question, choice, is_exit_option, noul, score
from .validate import validate_request

__all__ = [
    "build_retrieval",
    "build_action",
    "build_memory",
    "build_doubt",
    "shuffle_options",
    "unshuffle",
    "MAX_RETRIEVAL_CANDIDATES",
    "NARRATOR_EFFORTS",
    "RECORD_TYPES",
    "QUESTION_FORMATS",
    "DEFAULT_URGENCY_LEVELS",
]

#: JEV-R works on top-K: never send the whole memory, and never more than this.
MAX_RETRIEVAL_CANDIDATES = 20

#: ``choice`` limit (255) minus room for the mandatory exit option.
MAX_ACTION_CANDIDATES = 254

#: Narrator effort levels accepted by the narrator (``reasoning.effort``).
NARRATOR_EFFORTS: tuple[str, ...] = ("max", "xhigh", "high", "medium", "low", "minimal", "none")

#: Memory record kinds; ``skip`` is the exit option.
RECORD_TYPES: tuple[str, ...] = ("episode", "fact", "procedure", "skip")

#: Human-question formats of JEV-D.
QUESTION_FORMATS: tuple[str, ...] = ("binary", "multiple", "open")

#: Urgency ruler: concrete situations, never numeric labels.
DEFAULT_URGENCY_LEVELS: tuple[str, ...] = (
    "Safe — no time pressure: the situation is stable and delay changes nothing "
    "(waiting for dawn, standing in a safe place)",
    "Pressing — act soon: the situation can still get worse if ignored "
    "(a wounded enemy recovering, supplies running low, daylight fading)",
    "Critical — act now: delay costs health, position or the objective "
    "(bleeding, an enemy closing, a trap about to trigger)",
)

_DEFAULT_EXIT_KEY = "other"
_NAME_JUNK_RE = re.compile(r"[^A-Za-z0-9_.:-]+")
_POSITIONAL_RE = re.compile(r"^(?:opt(?:ion)?[\s_-]*)?(\d+)$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def _require_text(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise JevRequestError(f"{what} must be a non-empty string")
    return value.strip()


def _truncate(value: Any, limit: int) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _sanitize_name(value: Any, *, fallback: str = "item", max_len: int = 96) -> str:
    """Turn an arbitrary id into a safe question-name fragment (unique-ish)."""
    text = _NAME_JUNK_RE.sub("_", str(value).strip()).strip("_")
    if not text:
        text = fallback
    if len(text) <= max_len:
        return text
    digest = hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{text[: max_len - 9]}_{digest}"


def _finalise(request: dict[str, Any]) -> dict[str, Any]:
    """Fail loudly if a builder ever produced an invalid request."""
    validate_request(request)
    return request


# --------------------------------------------------------------------------- #
# option-order bias helpers
# --------------------------------------------------------------------------- #
def shuffle_options(
    criteria: Mapping[str, str],
    seed: int | None = None,
    *,
    pin_last: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Shuffle the presentation order of ``choice`` options.

    Returns ``(shuffled_criteria, order)`` where ``order[i]`` is the original
    option key now presented at position ``i``. The option **keys** are
    unchanged, so the model still answers with a key; the order only moves the
    documented first-option bias around.

    Args:
        criteria: ``{option: description}`` to reorder.
        seed: ``None`` → a fresh random order per call (real bias mitigation);
            an int → deterministic order (tests/audits).
        pin_last: key kept at the final position (used to keep the exit option
            in a stable, auditable place while the real options shuffle).
    """
    if not isinstance(criteria, Mapping):
        raise JevRequestError("shuffle_options expects a mapping {option: description}")
    items = list(criteria.items())
    pinned: tuple[str, str] | None = None
    if pin_last is not None:
        for key in list(criteria):
            if key == pin_last:
                pinned = (key, criteria[key])
                items = [(k, v) for k, v in items if k != pin_last]
                break
    random.Random(seed).shuffle(items)
    if pinned is not None:
        items.append(pinned)
    return {key: value for key, value in items}, [key for key, _ in items]


def unshuffle(choice_value: Any, order: Sequence[str]) -> str:
    """Map a positional option label back to the original key of ``order``.

    ``choice`` answers are keys, so for the normal case this is the identity.
    The reverse mapping exists for callers that present positional labels
    (``"opt_2"``, ``"2"``, ``2``) to the model: positions are 1-based, and the
    integer ``0`` also names the first presented option.
    """
    if not order:
        raise JevRequestError("unshuffle needs the order returned by shuffle_options")
    keys = [str(key) for key in order]
    if isinstance(choice_value, str) and choice_value in keys:
        return choice_value

    index: int | None = None
    if isinstance(choice_value, bool):
        index = None
    elif isinstance(choice_value, int):
        index = choice_value if choice_value == 0 else choice_value - 1
    elif isinstance(choice_value, str):
        match = _POSITIONAL_RE.match(choice_value.strip())
        if match:
            position = int(match.group(1))
            index = position if position == 0 else position - 1
    if index is None or not 0 <= index < len(keys):
        raise JevRequestError(
            f"cannot unshuffle {choice_value!r}: it is neither an option key nor a position "
            f"within 1..{len(keys)}"
        )
    return keys[index]


# --------------------------------------------------------------------------- #
# candidate normalisation
# --------------------------------------------------------------------------- #
def _normalise_candidates(candidates: Any, max_candidates: int) -> list[dict[str, str]]:
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Iterable):
        raise JevRequestError("candidates must be a sequence of {id, title, summary}")
    items = list(candidates)
    if not items:
        raise JevRequestError("retrieval needs at least one candidate")
    if len(items) > max_candidates:
        raise JevRequestError(
            f"retrieval received {len(items)} candidates; send only the top-K "
            f"(maximum {max_candidates})"
        )
    normalised: list[dict[str, str]] = []
    seen: set[str] = set()
    for position, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            raise JevRequestError(f"candidate {position} must be a mapping with id/title/summary")
        item_id = _require_text(item.get("id"), f"candidate {position} id")
        if item_id in seen:
            raise JevRequestError(f"duplicate candidate id {item_id!r}")
        seen.add(item_id)
        title = _truncate(item.get("title"), 200)
        summary = _truncate(item.get("summary"), 2000)
        if not title and not summary:
            raise JevRequestError(f"candidate {item_id!r} needs a title or a summary")
        normalised.append({"id": item_id, "title": title, "summary": summary})
    return normalised


def _normalise_actions(actions: Any) -> list[dict[str, str]]:
    if isinstance(actions, (str, bytes)) or not isinstance(actions, Iterable):
        raise JevRequestError("action_candidates must be a sequence of {id, name, description}")
    items = list(actions)
    if not items:
        raise JevRequestError("action building needs at least one action candidate")
    if len(items) > MAX_ACTION_CANDIDATES:
        raise JevRequestError(
            f"action building received {len(items)} candidates; the choice maximum is "
            f"{MAX_ACTION_CANDIDATES} (255 options minus the exit option)"
        )
    normalised: list[dict[str, str]] = []
    seen: set[str] = set()
    for position, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            raise JevRequestError(f"action {position} must be a mapping with id/name/description")
        action_id = _require_text(item.get("id"), f"action {position} id")
        if action_id in seen:
            raise JevRequestError(f"duplicate action id {action_id!r}")
        seen.add(action_id)
        name = _truncate(item.get("name"), 160)
        description = _truncate(item.get("description"), 600)
        if not name and not description:
            raise JevRequestError(f"action {action_id!r} needs a name or a description")
        normalised.append(
            {"id": action_id, "name": name or action_id, "description": description}
        )
    return normalised


def _theme_options(
    raw_candidates: Any,
    themes: Sequence[str] | None,
) -> list[str]:
    """Theme options for JEV-R's dominant-theme choice.

    Explicit ``themes`` win; otherwise the candidates' ``theme`` fields are
    used. Options that would collide with the exit option are dropped.
    """
    if themes is not None:
        source: list[Any] = list(themes)
    else:
        source = []
        if isinstance(raw_candidates, Iterable) and not isinstance(raw_candidates, (str, bytes)):
            for item in raw_candidates:
                if isinstance(item, Mapping) and item.get("theme"):
                    source.append(item["theme"])
    options: list[str] = []
    for theme in source:
        text = _truncate(theme, 60)
        if text and text not in options and not is_exit_option(text):
            options.append(text)
    return options


# --------------------------------------------------------------------------- #
# JEV-R — retrieval
# --------------------------------------------------------------------------- #
def build_retrieval(
    candidates: list[Mapping[str, Any]],
    situation: str,
    *,
    themes: Sequence[str] | None = None,
    include_theme_choice: bool = True,
    shuffle: bool = True,
    shuffle_seed: int | None = None,
    max_candidates: int = MAX_RETRIEVAL_CANDIDATES,
    summary_chars: int = 400,
) -> dict[str, Any]:
    """JEV-R: should each candidate knowledge item enter the next action prompt?

    One ``noul`` per candidate (``p`` = probability that including it improves
    the next decision) — the caller keeps the items whose ``p`` crosses its own
    threshold. State carries **indices + top-K** (``[C1] title | summary``),
    never the full memory.

    Args:
        candidates: ``[{id, title, summary}]``, at most ``max_candidates`` (20).
            An optional ``theme`` field feeds the dominant-theme question.
        situation: the compact English situation the knowledge must help with.
        themes: explicit theme options for the optional ``choice``; when omitted,
            the candidates' ``theme`` values are used.
        include_theme_choice: set ``False`` to skip the theme question entirely.
        shuffle / shuffle_seed: option-order bias control (see module docstring).
        summary_chars: per-candidate summary budget in the state (context rot).
    """
    situation_text = _require_text(situation, "situation")
    items = _normalise_candidates(candidates, max_candidates)

    lines = [
        f"SITUATION: {situation_text}",
        "",
        f"KNOWLEDGE CANDIDATES (retrieved top-{len(items)}, indexed):",
    ]
    questions: dict[str, Question] = {}
    labels: dict[str, str] = {}
    for position, item in enumerate(items, 1):
        label = f"C{position}"
        labels[label] = item["id"]
        title = _truncate(item["title"], 200)
        summary = _truncate(item["summary"], summary_chars)
        detail = " | ".join(part for part in (title, summary) if part)
        lines.append(f"[{label}] {detail}")
        question = noul(
            f"include_{_sanitize_name(item['id'])}",
            f"[{label}] Would including this knowledge improve the next action decision "
            "given the situation?",
            "It would change how the next action is chosen: a relevant risk, constraint, "
            "location, resource or method.",
            "It would not change the next action: irrelevant, already known, or too generic.",
        )
        questions[question.name] = question

    request: dict[str, Any] = {
        "state": "\n".join(lines),
        "questions": questions,
        "meta": {"role": "JEV-R", "candidate_labels": labels},
        "ordering": {},
    }

    if include_theme_choice:
        options = _theme_options(candidates, themes)
        if len(options) >= 2:
            criteria = {
                theme: f"The knowledge that should shape the decision is mostly about {theme}."
                for theme in options
            }
            criteria[_DEFAULT_EXIT_KEY] = "No single one of these themes dominates the decision."
            if shuffle or shuffle_seed is not None:
                criteria, order = shuffle_options(
                    criteria, shuffle_seed, pin_last=_DEFAULT_EXIT_KEY
                )
                request["ordering"]["dominant_theme"] = order
            question = choice(
                "dominant_theme",
                "Which single theme dominates the knowledge that should inform the next action?",
                criteria,
            )
            questions[question.name] = question
        else:
            request["meta"]["theme_choice_skipped"] = "fewer than two theme options"

    return _finalise(request)


# --------------------------------------------------------------------------- #
# JEV-A — action
# --------------------------------------------------------------------------- #
def build_action(
    action_candidates: list[Mapping[str, Any]],
    state_summary: str,
    *,
    urgency_levels: Sequence[str] | None = None,
    exit_key: str = _DEFAULT_EXIT_KEY,
    shuffle: bool = True,
    shuffle_seed: int | None = None,
) -> dict[str, Any]:
    """JEV-A: which macro-action to take, how urgent it is, and whether to ask.

    One request with three parallel questions: ``action`` (``choice`` over the
    code-generated candidates, **always** with an exit option), ``urgency``
    (``score`` over concrete situations) and ``needs_human`` (``noul``).
    Macro-actions are never invented by the model: the caller generates them
    deterministically and passes them here.

    Args:
        action_candidates: ``[{id, name, description}]``; ``id`` is the exact
            macro-action identifier the caller will execute.
        state_summary: compact English summary of the critical situation.
        urgency_levels: 2–10 concrete-situation levels (default: safe /
            pressing / critical).
        exit_key: option key of the escape hatch (default ``other``); it is
            always appended last so its position is stable and auditable.
        shuffle / shuffle_seed: option-order bias control.
    """
    summary = _require_text(state_summary, "state_summary")
    actions = _normalise_actions(action_candidates)
    exit_name = _require_text(exit_key, "exit_key")
    if exit_name in {action["id"] for action in actions}:
        raise JevRequestError(f"exit_key {exit_name!r} collides with an action id")

    lines = [
        f"SITUATION SUMMARY: {summary}",
        "",
        "AVAILABLE ACTIONS (generated by code; ids are exact):",
    ]
    criteria: dict[str, str] = {}
    labels: dict[str, str] = {}
    for position, action in enumerate(actions, 1):
        label = f"A{position}"
        labels[label] = action["id"]
        detail = f"[{label}] {action['name']}"
        if action["description"]:
            detail = f"{detail}: {action['description']}"
        lines.append(f"{action['id']} — {detail}")
        criteria[action["id"]] = detail
    criteria[exit_name] = "None of these actions fits the situation (do nothing or reconsider)."

    request: dict[str, Any] = {
        "state": "\n".join(lines),
        "questions": {},
        "meta": {"role": "JEV-A", "action_labels": labels, "exit_key": exit_name},
        "ordering": {},
    }
    if shuffle or shuffle_seed is not None:
        criteria, order = shuffle_options(criteria, shuffle_seed, pin_last=exit_name)
        request["ordering"]["action"] = order

    levels = list(urgency_levels) if urgency_levels is not None else list(DEFAULT_URGENCY_LEVELS)
    request["questions"] = {
        "action": choice("action", "Which action should the agent take now?", criteria),
        "urgency": score("urgency", "How urgent is it to act right now?", levels),
        "needs_human": noul(
            "needs_human",
            "Does this situation require human help?",
            "A fact the agent cannot observe decides the outcome, or the remaining risk "
            "can kill the character and no available action reduces it: the agent cannot "
            "proceed safely alone.",
            "The visible facts already decide the outcome and an available action "
            "addresses the risk: the agent can decide and act on its own with the "
            "information it has.",
        ),
    }
    return _finalise(request)


# --------------------------------------------------------------------------- #
# JEV-M — memory / meta
# --------------------------------------------------------------------------- #
def build_memory(
    recent_events: Sequence[str],
    narrative_tail: str,
    *,
    max_events: int = 20,
    event_chars: int = 300,
    narrative_chars: int = 2000,
) -> dict[str, Any]:
    """JEV-M: update the narrative now? with how much effort? as what record?

    One request with ``update_memory`` (``noul``), ``narrator_effort``
    (``choice`` over exactly ``max``/``xhigh``/``high``/``medium``/``low``/
    ``minimal``/``none``) and ``record_type`` (``choice`` over ``episode``/
    ``fact``/``procedure``/``skip``). The effort order is the narrator's own
    scale and is **never** shuffled.

    Args:
        recent_events: event strings, oldest first; only the last
            ``max_events`` are sent, each truncated to ``event_chars``.
        narrative_tail: the rolling narrative (tail), truncated to
            ``narrative_chars``.
        max_events / event_chars / narrative_chars: context-rot budgets.
    """
    if isinstance(recent_events, (str, bytes)) or not isinstance(recent_events, Iterable):
        raise JevRequestError("recent_events must be a sequence of strings")
    events = [event for event in recent_events]
    for position, event in enumerate(events, 1):
        if not isinstance(event, str):
            raise JevRequestError(f"recent event {position} must be a string")
    narrative = narrative_tail if isinstance(narrative_tail, str) else ""
    if not events and not narrative.strip():
        raise JevRequestError(
            "memory building needs at least one recent event or a narrative tail"
        )

    kept = events[-max_events:] if max_events > 0 else []
    dropped = len(events) - len(kept)
    lines = ["NARRATIVE TAIL (rolling narrative, most recent last):", _truncate(narrative, narrative_chars), ""]
    if kept:
        header = "RECENT EVENTS (oldest first, newest last):"
        if dropped:
            header = f"RECENT EVENTS (last {len(kept)} of {len(events)}; older ones omitted):"
        lines.append(header)
        lines.extend(f"{index}. {_truncate(event, event_chars)}" for index, event in enumerate(kept, 1))

    effort_criteria = {
        "max": "Turning point: a death, near-death, goal change or permanent party change "
               "— rewrite the rolling narrative with maximum care.",
        "xhigh": "Major event: a dangerous fight won or lost, a serious injury, a betrayal "
                 "or a major discovery — extend and refine the rolling narrative.",
        "high": "Notable event: a first-time milestone (new companion, new region, a key "
                "item) — write a solid paragraph for the narrative.",
        "medium": "Ordinary progress with something real to record (arrived somewhere, met "
                  "someone in passing) — write one short accurate sentence.",
        "low": "Minor detail that still deserves a clause (weather turning, a small "
               "resupply) — append a brief clause to the narrative.",
        "minimal": "Trivial detail with marginal value — make the smallest possible edit.",
        "none": "Nothing here is worth recording — do not call the narrator; skip the update.",
    }
    record_criteria = {
        "episode": "A dated event of this campaign: a fight, a journey leg, a meeting, a "
                   "discovery. NOT for: stable truths (fact) or reusable methods (procedure).",
        "fact": "A stable truth about the world, the character or the objective: a persistent "
                "injury, the current objective, a companion's identity. NOT for: things that "
                "happened at one moment (episode).",
        "procedure": "A method worth reusing later: how to cross rivers safely, how to trade "
                     "with a caravan. NOT for: one-off events (episode) or stable truths (fact).",
        "skip": "Nothing here is worth storing: routine movement or already recorded elsewhere.",
    }

    request: dict[str, Any] = {
        "state": "\n".join(lines),
        "questions": {
            "update_memory": noul(
                "update_memory",
                "Should the narrative memory be updated now?",
                "A milestone, reversal, injury, death, new companion, objective change, "
                "location change or reusable lesson happened: the chronicle would be wrong "
                "or stale without this update.",
                "Nothing the chronicle must carry changed: routine movement, a repeated "
                "event already covered, or a detail too small to deserve a clause.",
            ),
            "narrator_effort": choice(
                "narrator_effort",
                "How much narrator effort should this memory update use?",
                effort_criteria,
            ),
            "record_type": choice(
                "record_type",
                "What kind of memory record should be stored?",
                record_criteria,
            ),
        },
        "meta": {
            "role": "JEV-M",
            "events_sent": len(kept),
            "events_omitted": dropped,
            "narrator_efforts": list(NARRATOR_EFFORTS),
            "record_types": list(RECORD_TYPES),
        },
        "ordering": {},
    }
    return _finalise(request)


# --------------------------------------------------------------------------- #
# JEV-D — doubt
# --------------------------------------------------------------------------- #
def build_doubt(
    context: str,
    action_confidence: float | None,
    *,
    shuffle: bool = True,
    shuffle_seed: int | None = None,
) -> dict[str, Any]:
    """JEV-D: should the agent ask the human, and in what format?

    ``action_confidence`` is **computed by code** (for example the band of the
    JEV-A answer) and only handed over as context — the model must not
    re-estimate arithmetic. ``None`` or ``0.0`` are rendered as *unavailable*
    (the API uses ``0`` for "no confidence reported").

    Args:
        context: compact English context of the doubt (what is unclear and why).
        action_confidence: confidence in ``[0, 1]``, or ``None``/``0.0`` when
            unavailable.
        shuffle / shuffle_seed: option-order bias control for the format options.
    """
    context_text = _require_text(context, "context")
    if action_confidence is None:
        confidence_text = "unavailable (the model reported no confidence)"
    else:
        if isinstance(action_confidence, bool) or not isinstance(
            action_confidence, (int, float)
        ):
            raise JevRequestError(
                f"action_confidence must be a number in [0, 1] or None, got {action_confidence!r}"
            )
        value = float(action_confidence)
        if not 0.0 <= value <= 1.0:
            raise JevRequestError(f"action_confidence must be in [0, 1], got {action_confidence!r}")
        confidence_text = (
            "unavailable (reported as 0.0, which the API uses for 'no confidence')"
            if value == 0.0
            else f"{value:.2f}"
        )

    criteria = {
        "binary": "A yes/no question with two mutually exclusive options, when the decision "
                  "is genuinely two-way (cross now or turn back). NOT for: situations with "
                  "three or more real options (use multiple) or answers that are names, "
                  "values or plans (use open).",
        "multiple": "A short list of 3-4 concrete options to choose from, when several "
                    "distinct actions are on the table. NOT for: genuine two-way decisions "
                    "(use binary) and answers no option list can cover (use open).",
        "open": "A free-text question, when the answer is a name, a value or a plan that no "
                "option list can cover. NOT for: decisions whose concrete options can be "
                "listed (use binary or multiple).",
    }
    request: dict[str, Any] = {
        "state": (
            f"CONTEXT:\n{context_text}\n\n"
            f"ACTION CONFIDENCE (computed by code; do not re-estimate): {confidence_text}"
        ),
        "questions": {
            "ask_human": noul(
                "ask_human",
                "Should the agent ask the human for help?",
                "A fact the agent cannot observe decides the outcome (what an unreadable "
                "dialog will do, whether a shape in the fog is lethal, which of two "
                "irreversible routes is survivable), or the remaining risk can kill the "
                "character and no available action reduces it.",
                "The visible facts already decide the outcome: the enemy, the terrain and "
                "the escape route are known, or the situation is routine and reversible. "
                "Uncertainty the agent can reduce by acting (scouting, waiting one round, "
                "testing) is NOT a reason to ask.",
            ),
            "question_format": choice(
                "question_format",
                "If the human is asked, what question format fits best?",
                criteria,
            ),
        },
        "meta": {"role": "JEV-D", "action_confidence": action_confidence},
        "ordering": {},
    }
    if shuffle or shuffle_seed is not None:
        shuffled, order = shuffle_options(criteria, shuffle_seed)
        request["questions"]["question_format"]["criteria"] = shuffled
        request["ordering"]["question_format"] = order
    return _finalise(request)
