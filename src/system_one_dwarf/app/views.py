"""Read-model builders shared by the REST routes and the WebSocket broadcasts.

Keeping them here (instead of inside one route module) means ``memory_update``
events and ``GET .../memory`` can never drift apart, and the ``*_pt`` display
fields of the objective are produced by exactly one place.
"""

from __future__ import annotations

from typing import Any

from ..campaign import Campaign, MemoryStore
from ..campaign.memory import safe_translate
from .state import AppServices

EPISODE_LIMIT = 50


def memory_payload(services: AppServices, campaign: Campaign) -> dict[str, Any]:
    """Exactly the ``GET /api/campaigns/{id}/memory`` object."""
    memory: MemoryStore = services.memory(campaign)
    narrative = memory.narrative()
    return {
        "narrative": narrative,
        "narrative_pt": memory.narrative_pt(),
        "episodes": memory.episodes(limit=EPISODE_LIMIT),
        "working_memory": memory.working_memory(),
        "updated_at": memory.updated_at() or campaign.updated_at,
    }


def display_pt(translator: Any, canonical: str) -> str | None:
    """The PT display of a canonical EN string, or ``None`` when there is none.

    ``None`` means "no display translation to show": the text is empty or the
    translator is the identity fallback (no key), in which case the PT rendering
    *is* the canonical EN and the field would carry no information.  Routes
    declare the ``*_pt`` fields optional and use
    ``response_model_exclude_none``, so a client written against the original
    contract sees the exact original payload while a keyed campaign gets the
    translated one.
    """
    if not canonical:
        return None
    translated = safe_translate(translator, canonical, "pt")
    return translated if translated and translated != canonical else None


def objective_payload(services: AppServices, campaign: Campaign) -> dict[str, Any]:
    """Exactly the ``GET|PUT /api/campaigns/{id}/objective`` object.

    The canonical subgoal/narrative text is **EN** (it is prompt material); the
    additive ``narrative_pt`` / ``subgoals[].text_pt`` fields are the PT display
    translation produced through the campaign's ``safe_translate`` seam — the
    same seam ``PATCH .../memory`` uses in the other direction, so a missing key
    simply has no ``_pt`` field to offer instead of failing.
    """
    objective = services.objective(campaign).get()
    translator = services.translator_for(campaign)
    narrative = str(objective.get("narrative") or "")
    subgoals: list[dict[str, Any]] = []
    for subgoal in objective.get("subgoals") or []:
        text = str(subgoal.get("text") or "")
        subgoals.append(
            {
                "id": str(subgoal.get("id") or ""),
                "text": text,
                "done": bool(subgoal.get("done")),
                "text_pt": display_pt(translator, text),
            }
        )
    return {
        "narrative": narrative,
        "narrative_pt": display_pt(translator, narrative),
        "subgoals": subgoals,
    }
