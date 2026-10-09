"""Objective storage: narrative + subgoals ``[{id, text, done}]``.

Two layers, on purpose:

* **Canonical**: records in the campaign memory (``semantic``/``objective``) —
  this is what feeds prompts.
* **Mirror**: ``campanhas/<slug>/objetivo.json`` — cheap reads for the UI panel
  and the only place that preserves subgoal *order*.

``PUT /api/campaigns/{id}/objective`` replaces the whole objective (contract);
subgoals dropped by the user are superseded in memory with a ``(dropped)``
marker so the memory fallback never resurrects them.

Text direction: the objective narrative is prompt material, so it is stored in
**EN** — ``put()`` pushes user text through ``Translator.to_english`` first
(identity by default until the DeepSeek translator piece is plugged in).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Iterable, Sequence

from .memory import (
    KEY_OBJECTIVE_INDEX,
    KEY_OBJECTIVE_NARRATIVE,
    TAG_OBJECTIVE,
    TAG_OBJECTIVE_NARRATIVE,
    TAG_SUBGOAL,
    MemoryStore,
    Translator,
    safe_translate,
)
from .paths import objective_path

log = logging.getLogger("system_one_dwarf.campaign.objective")

_SUBGOAL_RE = re.compile(r"^Subgoal (?P<id>\S+) \((?P<state>done|pending)\): (?P<text>.*)$")
_SUBGOAL_TAGS = f"{TAG_OBJECTIVE},{TAG_SUBGOAL}"


def _subgoal_line(subgoal: dict[str, Any]) -> str:
    state = "done" if subgoal.get("done") else "pending"
    return f"Subgoal {subgoal['id']} ({state}): {subgoal['text']}"


def normalize_subgoals(subgoals: Iterable[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Normalise user input into ``[{id, text, done}]`` with stable ids."""
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    counter = 1
    for raw in subgoals or []:
        if isinstance(raw, str):
            raw = {"text": raw}
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        subgoal_id = str(raw.get("id") or "").strip()
        if not subgoal_id or subgoal_id in used:
            while f"sg-{counter}" in used:
                counter += 1
            subgoal_id = f"sg-{counter}"
        used.add(subgoal_id)
        out.append({"id": subgoal_id, "text": text, "done": bool(raw.get("done"))})
    return out


class ObjectiveStore:
    """Objective of one campaign (mirror file + memory records)."""

    def __init__(
        self,
        campaign_dir: Path | str,
        *,
        memory: MemoryStore | None = None,
        translator: Translator | None = None,
        strict: bool = False,
    ) -> None:
        self.campaign_dir = Path(campaign_dir)
        self.memory = memory
        self.translator = translator
        self.strict = strict
        self.path = objective_path(self.campaign_dir)

    # --------------------------------------------------------------------- mirror

    def _read_mirror(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            log.warning("objective mirror unreadable at %s; falling back to memory", self.path)
            return None
        if not isinstance(data, dict):
            return None
        return {
            "narrative": str(data.get("narrative") or ""),
            "subgoals": normalize_subgoals(data.get("subgoals")),
        }

    def _write_mirror(self, objective: dict[str, Any]) -> None:
        self.campaign_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(objective, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------------- read/write

    def get(self) -> dict[str, Any]:
        """Current objective (mirror first, memory records as fallback)."""
        mirror = self._read_mirror()
        if mirror is not None:
            return mirror
        recovered = self._from_memory()
        self._write_mirror(recovered)
        return recovered

    def put(self, narrative: str, subgoals: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Replace the full objective and persist it (mirror + memory)."""
        previous = self.get()
        canonical = safe_translate(self.translator, str(narrative or ""), "en")
        objective = {
            "narrative": canonical,
            "subgoals": normalize_subgoals(subgoals),
        }
        # Subgoal text is prompt material too: store it in EN as well.
        for subgoal in objective["subgoals"]:
            subgoal["text"] = safe_translate(self.translator, subgoal["text"], "en")
        self._write_mirror(objective)
        self._sync_memory(previous, objective)
        return objective

    def seed(self, campaign_name: str | None = None) -> dict[str, Any]:
        """Fresh-game initialisation: empty objective + an index record in memory."""
        objective = self.get()
        self._write_mirror(objective)
        memory = self.memory
        if memory is not None:
            label = campaign_name or self.campaign_dir.name
            self._memory_call(
                lambda: memory.add_entry(
                    "semantic",
                    f"Objective initialized for campaign: {label}",
                    key=KEY_OBJECTIVE_INDEX,
                    tags=TAG_OBJECTIVE,
                    origin="system",
                )
            )
        return objective

    # ------------------------------------------------------------------- internals

    def _from_memory(self) -> dict[str, Any]:
        """Rebuild the objective from memory records (tolerant, order not guaranteed)."""
        memory = self.memory
        if memory is None:
            return {"narrative": "", "subgoals": []}
        narrative = ""
        subgoals: list[dict[str, Any]] = []
        try:
            items = memory.recall(
                None, budget=200_000, top=50, kind="semantic", tags=TAG_OBJECTIVE
            ).get("items") or []
        except Exception:  # pragma: no cover - defensive (CLI unavailable)
            log.warning("objective recovery from memory failed", exc_info=True)
            items = []
        for item in items:
            content = str(item.get("content") or "")
            if content.startswith("Objective initialized"):
                continue
            match = _SUBGOAL_RE.match(content)
            if match:
                subgoals.append(
                    {
                        "id": match.group("id"),
                        "text": match.group("text"),
                        "done": match.group("state") == "done",
                    }
                )
            elif content:
                narrative = content
        return {"narrative": narrative, "subgoals": subgoals}

    def _sync_memory(self, previous: dict[str, Any], objective: dict[str, Any]) -> None:
        memory = self.memory
        if memory is None:
            return
        narrative = objective["narrative"]
        if narrative:
            self._memory_call(
                lambda: memory.add_entry(
                    "semantic",
                    narrative,
                    key=KEY_OBJECTIVE_NARRATIVE,
                    tags=f"{TAG_OBJECTIVE},{TAG_OBJECTIVE_NARRATIVE}",
                    origin="owner",
                )
            )
        for subgoal in objective["subgoals"]:
            line = _subgoal_line(subgoal)
            self._memory_call(
                lambda line=line, subgoal=subgoal: memory.add_entry(
                    "semantic",
                    line,
                    key=f"objective:subgoal:{subgoal['id']}",
                    tags=_SUBGOAL_TAGS,
                    origin="owner",
                )
            )
        kept = {subgoal["id"] for subgoal in objective["subgoals"]}
        for stale in previous.get("subgoals") or []:
            if stale.get("id") in kept:
                continue
            dropped = f"Subgoal {stale['id']} (dropped): {stale.get('text', '')}"
            self._memory_call(
                lambda dropped=dropped, stale=stale: memory.add_entry(
                    "semantic",
                    dropped,
                    key=f"objective:subgoal:{stale['id']}",
                    tags=_SUBGOAL_TAGS,
                    origin="owner",
                )
            )

    def _memory_call(self, fn) -> Any:
        try:
            return fn()
        except Exception as exc:  # memory is canonical but never blocks the UI
            if self.strict:
                raise
            log.warning("objective memory sync failed (%s); mirror kept in sync", exc)
            return None
