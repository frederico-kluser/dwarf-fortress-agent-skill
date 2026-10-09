"""Offline fakes shared by ``tests/campaign`` and ``tests/app``.

Nothing here touches the network, the real CoALA installer or the real
``coala.py``: the "CLI" is an in-memory store that mimics the JSON the real CLI
prints (see the module docstring of ``campaign/memory.py`` for the shapes), and
the "installer" just creates the expected directory layout.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from system_one_dwarf.campaign.memory import CoalaCallError

#: Deterministic, strictly increasing timestamps for fake records.
_EPOCH = "2026-10-01T00:00:"


def _ts(seq: int) -> str:
    return f"{_EPOCH}{seq % 60:02d}.000+00:00"


class FakeCoalaCli:
    """Stand-in for ``<campaign>/.agents/<slug>-agent-skill/scripts/coala.py``."""

    def __init__(self, script: Path | str | None = None, *, seed: Iterable[dict] | None = None) -> None:
        self.script = Path(script) if script else Path("/fake/proj/.agents/fake-agent-skill/scripts/coala.py")
        self.calls: list[list[str]] = []
        self.entries: list[dict[str, Any]] = list(seed or [])
        self.fail_on: set[str] = set()
        self._seq = len(self.entries)
        self._next_id = max((int(e["id"]) for e in self.entries), default=0) + 1

    # ------------------------------------------------------------------- helpers

    def commands(self) -> list[str]:
        return [call[0] for call in self.calls]

    def args_of(self, command: str) -> list[list[str]]:
        return [call for call in self.calls if call and call[0] == command]

    def active(self, *, kind: str | None = None, tags: str | None = None) -> list[dict[str, Any]]:
        wanted = {t for t in (tags or "").split(",") if t}
        out = []
        for entry in self.entries:
            if entry.get("superseded_by"):
                continue
            if kind and entry["type"] != kind:
                continue
            entry_tags = {t for t in str(entry.get("tags") or "").split(",") if t}
            if wanted and not wanted <= entry_tags:
                continue
            out.append(entry)
        return out

    @staticmethod
    def _opt(args: list[str], name: str, default: Any = None) -> Any:
        return args[args.index(name) + 1] if name in args else default

    def _public(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": entry["id"],
            "type": entry["type"],
            "origin": entry["origin"],
            "content": entry["content"],
            "source": entry.get("source"),
            "tags": entry.get("tags"),
            "valid_from": entry.get("valid_from"),
            "valid_until": entry.get("valid_until"),
            "superseded_by": entry.get("superseded_by"),
            "recorded_at": entry["recorded_at"],
            "score": {"rrf": 0.03, "fts_rank": 1, "vec_rank": 1, "combined": 1.0},
        }

    def _insert(self, *, kind: str, content: str, key: str | None, tags: str | None,
                origin: str, source: str | None) -> dict[str, Any]:
        self._seq += 1
        entry = {
            "id": self._next_id,
            "type": kind,
            "origin": origin,
            "content": content,
            "tags": tags,
            "source": source,
            "key": key,
            "recorded_at": _ts(self._seq),
            "valid_from": None,
            "valid_until": None,
            "superseded_by": None,
        }
        self._next_id += 1
        for other in self.entries:  # supersession by key, like the real CLI
            if key and other.get("key") == key and not other.get("superseded_by"):
                other["superseded_by"] = entry["id"]
                other["valid_until"] = entry["recorded_at"]
        self.entries.append(entry)
        return entry

    # ---------------------------------------------------------------- "subprocess"

    def run(self, args: list[str], *, json_output: bool = True, tolerate_non_json: bool = False) -> Any:
        call = list(args) + (["--json"] if json_output else [])
        self.calls.append(call)
        command = call[0] if call else ""
        if command in self.fail_on:
            raise CoalaCallError(f"fake failure on {command}", argv=call, code=1)
        handler = getattr(self, f"_cmd_{command}", None)
        return handler(call) if handler else {}

    def _cmd_add(self, args: list[str]) -> dict[str, Any]:
        entry = self._insert(
            kind=self._opt(args, "--type"),
            content=self._opt(args, "--content"),
            key=self._opt(args, "--key"),
            tags=self._opt(args, "--tags"),
            origin=self._opt(args, "--origin", "agent"),
            source=self._opt(args, "--source"),
        )
        return {
            "ok": True,
            "id": entry["id"],
            "type": entry["type"],
            "origin": entry["origin"],
            "supersession_key": entry["key"],
        }

    def _cmd_recall(self, args: list[str]) -> dict[str, Any]:
        kind = self._opt(args, "--type")
        tags = self._opt(args, "--tags")
        top = int(self._opt(args, "--top", 12))
        items = self.active(kind=kind, tags=tags)[::-1][:top]
        return {
            "budget_tokens": int(self._opt(args, "--budget", 2000)),
            "used_tokens": sum(max(1, len(e["content"]) // 4) for e in items),
            "count": len(items),
            "items": [self._public(e) for e in items],
        }

    def _cmd_search(self, args: list[str]) -> dict[str, Any]:
        query = args[1] if len(args) > 1 else ""
        kind = self._opt(args, "--type")
        tags = self._opt(args, "--tags")
        limit = int(self._opt(args, "--limit", 10))
        candidates = self.active(kind=kind, tags=tags)
        matches = [e for e in candidates if query.lower() in str(e["content"]).lower()]
        if not matches and tags:  # the real CLI still returns tag-filtered hits
            matches = candidates
        return {"query": query, "backend": "fake", "results": [self._public(e) for e in matches[:limit]]}

    def _cmd_supersede(self, args: list[str]) -> dict[str, Any]:
        target_id = int(args[1])
        entry = next((e for e in self.entries if int(e["id"]) == target_id), None)
        if entry is None:
            raise CoalaCallError(f"record {target_id} not found", argv=list(args), code=2)
        new = self._insert(
            kind=entry["type"],
            content=self._opt(args, "--content"),
            key=entry.get("key"),
            tags=self._opt(args, "--tags", entry.get("tags")),
            origin=self._opt(args, "--origin", "owner"),
            source=self._opt(args, "--source"),
        )
        return {"ok": True, "old_id": target_id, "new_id": new["id"], "supersession_key": new["key"]}

    def _cmd_stats(self, args: list[str]) -> dict[str, Any]:
        return {"entries": {"total": len(self.entries), "active": len(self.active())}}

    def _cmd_where(self, args: list[str]) -> dict[str, Any]:
        return {"db": str(self.script), "exists": True}

    def _cmd_export(self, args: list[str]) -> str:
        out = self._opt(args, "--out")
        payload = {"entries": [self._public(e) for e in self.entries]}
        if out:
            Path(out).write_text(json.dumps(payload), encoding="utf-8")
        return json.dumps(payload)


class FakeInstaller:
    """Stand-in for ``coala-install.py install`` (never runs the real installer)."""

    def __init__(self, *, create_layout: bool = True, fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.create_layout = create_layout
        self.fail = fail

    def ensure_available(self) -> Path:
        return Path("/fake/coala-install.py")

    def install(self, project: Path | str, name: str) -> dict[str, Any]:
        self.calls.append({"project": str(project), "name": name})
        if self.fail:
            from system_one_dwarf.campaign import CampaignError

            raise CampaignError("fake installer failure")
        if self.create_layout:
            skill = Path(project) / ".agents" / f"{name}-agent-skill"
            (skill / "scripts").mkdir(parents=True, exist_ok=True)
            (skill / "memory").mkdir(parents=True, exist_ok=True)
            (skill / "scripts" / "coala.py").write_text("# fake coala cli\n", encoding="utf-8")
            (skill / "memory" / "coala.sqlite").write_bytes(b"fake")
        return {"ok": True, "argv": ["python3", "coala-install.py", "install", str(project), "--name", name]}


class FakeTranslator:
    """Mapping translator (defaults to identity) implementing the protocol."""

    name = "fake"

    def __init__(self, pt_to_en: dict[str, str] | None = None, en_to_pt: dict[str, str] | None = None) -> None:
        self.pt_to_en = dict(pt_to_en or {})
        self.en_to_pt = dict(en_to_pt or {})
        self.calls: list[tuple[str, str]] = []

    def to_english(self, text: str) -> str:
        self.calls.append(("to_english", text))
        return self.pt_to_en.get(text, text)

    def to_portuguese(self, text: str) -> str:
        self.calls.append(("to_portuguese", text))
        return self.en_to_pt.get(text, text)


class MarkerTranslator:
    """Marks the direction, so tests can prove PT->EN happened before storing."""

    name = "marker"

    def to_english(self, text: str) -> str:
        return f"EN<{text}>"

    def to_portuguese(self, text: str) -> str:
        return f"PT<{text}>"


class ExplodingTranslator:
    """A misbehaving translator: the contract is best effort, never fatal."""

    name = "exploding"

    def to_english(self, text: str) -> str:
        raise RuntimeError("translator down")

    def to_portuguese(self, text: str) -> str:
        raise RuntimeError("translator down")
