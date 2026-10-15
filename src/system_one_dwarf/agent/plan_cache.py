"""Cache of validated LLM plans — conservative by construction (Passo 19).

A wrong (stale) plan is **worse** than re-generating, so this cache errs on the
side of dropping:

* **key** = ``value_key`` over the *normalised state* (the planning digest, minus
  the volatile game ``clock``) + the objective — "estado normalizado + objetivo";
* **invalidation signature** = the four conservative axes of the step:
  ``zone`` (position + location), ``goal``, ``threats`` and ``health`` (HP).
  ANY change of one of them invalidates the entry: :meth:`PlanCache.get_plan`
  only serves an entry whose stored signature matches the request exactly (and
  drops it otherwise), and :meth:`PlanCache.sweep` / :meth:`PlanCache.invalidate_*`
  actively evict entries that no longer match the current context;
* only plans that passed the deterministic critics are ever stored, and the
  consumer re-runs those critics on every hit before serving.

The cache stores plan **dicts** (``Plan.to_dict()``) so this module has no
import cycle with :mod:`system_one_dwarf.agent.planning`, which owns the
``dict`` ↔ ``Plan`` conversion.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ..cache import JsonEntryCache, value_key

__all__ = [
    "PLAN_CACHE_NAME",
    "PlanCache",
    "normalised_state",
    "plan_key",
    "plan_signature",
    "signature_parts",
]

#: Metric name reported by ``CacheSession``/``stats()``.
PLAN_CACHE_NAME = "hunt_plan"

#: The conservative invalidation axes (mudança de zona/objetivo/ameaça/HP).
SIGNATURE_AXES: tuple[str, ...] = ("zone", "goal", "threats", "health")


def _canon(value: Any) -> str:
    """Canonical JSON text for a signature part (stable across key order)."""
    import json

    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _get(data: Any, *path: str, default: Any = None) -> Any:
    node = data
    for name in path:
        if isinstance(node, Mapping):
            node = node.get(name, default)
        else:
            node = getattr(node, name, default)
        if node is default and name != path[-1]:
            return default
    return node


def normalised_state(digest: Mapping[str, Any]) -> dict[str, Any]:
    """The digest minus volatile noise (the game ``clock`` ticks on its own).

    Two snapshots with the same normalised state are the *same situation* — the
    same reasoning ``stuck.state_fingerprint`` uses (clock/counters change
    without real progress).
    """
    out = {k: v for k, v in dict(digest or {}).items() if k != "clock"}
    return out


def signature_parts(digest: Mapping[str, Any], goal: str = "") -> dict[str, str]:
    """The four conservative axes as canonical strings."""
    pos = _get(digest, "adventurer", "pos")
    location = _get(digest, "location", default={})
    return {
        "zone": _canon({"pos": pos, "location": location}),
        "goal": str(goal or ""),
        "threats": _canon(_get(digest, "threats")),
        "health": _canon(_get(digest, "adventurer", "health")),
    }


def plan_signature(digest: Mapping[str, Any], goal: str = "") -> dict[str, str]:
    """Signature that invalidation checks compare (zone/goal/threats/health)."""
    return signature_parts(digest, goal)


def plan_key(digest: Mapping[str, Any], goal: str = "") -> str:
    """Cache key: hash(normalised state) + objective."""
    return value_key(PLAN_CACHE_NAME, _canon(normalised_state(digest)), str(goal or ""))


class PlanCache(JsonEntryCache):
    """LRU (optionally disk-mirrored) cache of validated plan dicts.

    Entries hold ``{"plan": <plan dict>, "signature": {...}, "cost_usd": float}``;
    ``cost_usd`` is the generation cost the hit saves (session metric).
    """

    def __init__(
        self,
        *,
        maxsize: int = 256,
        path: str | Path | None = None,
        autosave: bool = True,
    ) -> None:
        super().__init__(maxsize=maxsize, path=path, autosave=autosave, name=PLAN_CACHE_NAME)

    # ---------------------------------------------------------------- read

    def get_plan(self, digest: Mapping[str, Any], goal: str = "") -> dict[str, Any] | None:
        """Cached plan dict, or ``None`` (miss).  NEVER serves a stale entry.

        A hit whose stored signature no longer matches the request (zone, goal,
        threats or HP changed) is dropped and reported as a miss — zero stale
        plans by construction.
        """
        key = plan_key(digest, goal)
        entry = self.peek(key)
        if entry is not None:
            want = plan_signature(digest, goal)
            stored = entry.get("signature")
            if not isinstance(stored, Mapping) or any(
                stored.get(axis) != want[axis] for axis in SIGNATURE_AXES
            ):
                # Defensive: the key already hashes these axes, so this only fires
                # on a corrupt/hand-edited mirror.  Conservative: drop and miss.
                self.drop(key)
                self.count_miss()
                return None
        entry = self._lookup(key)
        if entry is None:
            return None
        plan = entry.get("plan")
        return dict(plan) if isinstance(plan, Mapping) else None

    # --------------------------------------------------------------- write

    def put_plan(
        self,
        digest: Mapping[str, Any],
        goal: str = "",
        plan: Mapping[str, Any] | None = None,
        *,
        cost_usd: float = 0.0,
    ) -> None:
        """Store one validated plan (callers store critic-passing plans only)."""
        if plan is None or self.maxsize == 0:
            return
        self._store(plan_key(digest, goal), {
            "plan": dict(plan),
            "signature": plan_signature(digest, goal),
            "cost_usd": float(cost_usd or 0.0),
        })

    def drop_plan(self, digest: Mapping[str, Any], goal: str = "") -> bool:
        """Drop the entry for this exact state+objective (revalidation failed)."""
        return self.drop(plan_key(digest, goal))

    # ------------------------------------------------------- invalidation

    def _invalidate_axis(self, axis: str, current: str) -> int:
        """Drop every entry whose ``axis`` differs from ``current`` (conservative)."""
        dropped = 0
        for key, entry in self.items():
            stored = entry.get("signature")
            if not isinstance(stored, Mapping) or stored.get(axis) != current:
                if self.drop(key):
                    dropped += 1
        return dropped

    def invalidate_zone(self, digest: Mapping[str, Any]) -> int:
        """Zone changed: drop every entry of another zone/position."""
        return self._invalidate_axis("zone", plan_signature(digest)["zone"])

    def invalidate_objective(self, goal: str) -> int:
        """Objective changed: drop every entry planned for another objective."""
        return self._invalidate_axis("goal", str(goal or ""))

    def invalidate_threats(self, digest: Mapping[str, Any]) -> int:
        """Threats changed: drop every entry seen under another threat set."""
        return self._invalidate_axis("threats", plan_signature(digest)["threats"])

    def invalidate_health(self, digest: Mapping[str, Any]) -> int:
        """HP/health changed: drop every entry seen under another health."""
        return self._invalidate_axis("health", plan_signature(digest)["health"])

    def sweep(self, digest: Mapping[str, Any], goal: str = "") -> int:
        """Invalidation on ALL four axes at once: keep only the exact context."""
        want = plan_signature(digest, goal)
        dropped = 0
        for key, entry in self.items():
            stored = entry.get("signature")
            if not isinstance(stored, Mapping) or any(
                stored.get(axis) != want[axis] for axis in SIGNATURE_AXES
            ):
                if self.drop(key):
                    dropped += 1
        return dropped

    def stale_entries(self, digest: Mapping[str, Any], goal: str = "") -> list[str]:
        """Audit helper: keys that would be stale for this context (must be 0)."""
        want = plan_signature(digest, goal)
        out = []
        for key, entry in self.items():
            stored = entry.get("signature")
            if not isinstance(stored, Mapping) or any(
                stored.get(axis) != want[axis] for axis in SIGNATURE_AXES
            ):
                out.append(key)
        return out
