"""Thin wrapper over a campaign's CoALA CLI (``coala.py``, subprocess + JSON).

Layout decision: the CLI lives at
``<campaign>/.agents/<slug>-coala-agent-skill/scripts/coala.py`` and its database at
``<campaign>/.agents/<slug>-coala-agent-skill/memory/coala.sqlite`` (canonical
``coala-install.py`` v3+ naming; the legacy ``<slug>-agent-skill`` dir is still
resolved — see :func:`paths.skill_dir`).  Everything here
is a *subprocess call with ``--json``*; we never read the SQLite file directly.

Record conventions used by the rest of the system (kept in one place so the
agent loop, the narrator and the UI agree):

============================  =========================================  ======================
Concept                       CoALA type / tags                          supersession key
============================  =========================================  ======================
rolling narrative             ``semantic`` / ``narrative``               ``narrative``
episode (memory editor row)   ``episodic`` / ``episode``                 episode id (``ep-*``)
fact                          ``semantic`` / ``fact``                    caller supplied
procedure                     ``procedural`` / ``procedure``             caller supplied
objective narrative           ``semantic`` / ``objective,narrative``     ``objective:narrative``
objective subgoal             ``semantic`` / ``objective,subgoal``       ``objective:subgoal:<id>``
user input (free text)        ``episodic`` / ``user_input``              ``input:<n>``
human answer to a question    ``episodic`` / ``human_answer``            ``answer:<qid>``
============================  =========================================  ======================

Translator protocol
-------------------
``Translator`` is the pluggable PT<->EN seam.  The canonical memory is stored in
**EN**; the UI is PT.  A later piece (the DeepSeek translator) implements::

    class DeepSeekTranslator:
        def to_english(self, text: str) -> str: ...      # PT -> EN (before storing)
        def to_portuguese(self, text: str) -> str: ...   # EN -> PT (for ``*_pt`` fields)

and injects it into ``create_app(translator=...)`` / ``MemoryStore(translator=...)``.
Both methods are **best effort**: they must never raise — return the input text
unchanged when the model is unavailable.  Until then ``IdentityTranslator`` is
used and every ``*_pt`` field simply mirrors the canonical EN value.
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, Protocol, Sequence, runtime_checkable

from .clock import to_z
from .paths import (
    KNOWLEDGE_NAME,
    coala_python,
    knowledge_dir,
    knowledge_memory_db,
    memory_db,
    skill_script,
)

log = logging.getLogger("system_one_dwarf.campaign.memory")

KIND_EPISODIC = "episodic"
KIND_SEMANTIC = "semantic"
KIND_PROCEDURAL = "procedural"
KINDS = (KIND_EPISODIC, KIND_SEMANTIC, KIND_PROCEDURAL)

TAG_NARRATIVE = "narrative"
TAG_EPISODE = "episode"
TAG_FACT = "fact"
TAG_PROCEDURE = "procedure"
TAG_OBJECTIVE = "objective"
#: Tag of the objective narrative record — deliberately *not* ``narrative``, which
#: is reserved for the rolling narrative recalled by :meth:`MemoryStore.narrative`.
TAG_OBJECTIVE_NARRATIVE = "objective_narrative"
TAG_SUBGOAL = "subgoal"
TAG_USER_INPUT = "user_input"
TAG_HUMAN_ANSWER = "human_answer"

KEY_NARRATIVE = "narrative"
KEY_OBJECTIVE_NARRATIVE = "objective:narrative"
KEY_OBJECTIVE_INDEX = "objective:index"

#: Budget used for *display* recalls (narrative/episodes in the memory editor):
#: large on purpose so the editor round-trips content without truncation.
DISPLAY_BUDGET = 200_000

#: Default top-K for the shared knowledge base.  Its records are whole wiki
#: articles (several KB each) and the CoALA CLI gates stdout at 48 KB, so a large
#: ``--limit`` truncates the JSON; :func:`search_knowledge` retries with a smaller K.
KNOWLEDGE_LIMIT = 3

_DEFAULT_TIMEOUT = 120.0


# --------------------------------------------------------------------------- errors


class CoalaError(RuntimeError):
    """Base class for CoALA CLI problems."""


class CoalaMissing(CoalaError):
    """The skill/CLI was not found — message always names the missing path."""


class CoalaCallError(CoalaError):
    """The CLI exited non-zero (or returned unparsable JSON)."""

    def __init__(self, message: str, *, argv: Sequence[str] = (), code: int | None = None):
        super().__init__(message)
        self.argv = list(argv)
        self.code = code


# ----------------------------------------------------------------------- translator


@runtime_checkable
class Translator(Protocol):
    """PT<->EN seam.  Implementations must be best effort and never raise."""

    def to_english(self, text: str) -> str:  # pragma: no cover - protocol
        ...

    def to_portuguese(self, text: str) -> str:  # pragma: no cover - protocol
        ...


class IdentityTranslator:
    """Fallback translator: both directions are the identity (offline, no model)."""

    name = "identity"

    def to_english(self, text: str) -> str:
        return text

    def to_portuguese(self, text: str) -> str:
        return text


def safe_translate(translator: Translator | None, text: str, direction: str) -> str:
    """Call a translator without letting it break the caller (best effort)."""
    if translator is None or not text:
        return text
    method = "to_english" if direction == "en" else "to_portuguese"
    fn = getattr(translator, method, None)
    if fn is None:
        return text
    try:
        out = fn(text)
    except Exception:  # pragma: no cover - defensive: translators are external
        log.warning("translator.%s failed; keeping the original text", method, exc_info=True)
        return text
    return out if isinstance(out, str) and out else text


# ------------------------------------------------------------------------ CLI glue


class CoalaCli:
    """Runs ``python3 <skill>/scripts/coala.py <args> --json`` and parses stdout."""

    def __init__(
        self,
        script: Path | str,
        *,
        python: str | None = None,
        timeout: float = _DEFAULT_TIMEOUT,
        db: Path | str | None = None,
    ) -> None:
        self.script = Path(script)
        self.python = python or coala_python()
        self.timeout = timeout
        self.db = Path(db) if db else None

    def argv(self, args: Sequence[str]) -> list[str]:
        cmd = [self.python, str(self.script)]
        if self.db is not None:
            cmd += ["--db", str(self.db)]
        return cmd + [str(a) for a in args]

    def run(
        self,
        args: Sequence[str],
        *,
        json_output: bool = True,
        tolerate_non_json: bool = False,
    ) -> Any:
        """Run the CLI; return parsed JSON (``None`` for a tolerated non-JSON reply)."""
        import subprocess

        if not self.script.exists():
            raise CoalaMissing(
                f"CoALA CLI not found at {self.script} — run the installer "
                f"(coala-install.py install --project <project> --name <name>) first"
            )
        cmd = self.argv(list(args) + (["--json"] if json_output else []))
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:  # python3 itself missing
            raise CoalaMissing(f"interpreter not found: {self.python} ({exc})") from exc
        except subprocess.TimeoutExpired as exc:
            raise CoalaCallError(
                f"CoALA CLI timed out after {self.timeout}s: {' '.join(cmd[:3])} …",
                argv=cmd,
            ) from exc

        if proc.returncode != 0:
            detail = _tail(proc.stderr) or _tail(proc.stdout) or "no output"
            raise CoalaCallError(
                f"CoALA CLI failed (exit {proc.returncode}): {detail}",
                argv=cmd,
                code=proc.returncode,
            )
        if not json_output:
            return proc.stdout
        if tolerate_non_json:
            # ``recall`` prints a human sentence instead of JSON when nothing
            # matched ("(working memory vazia …)"), still with exit code 0.
            try:
                return _parse_json(proc.stdout, cmd)
            except CoalaCallError:
                log.debug("CLI printed no JSON (exit 0): %s", _tail(proc.stdout, 120))
                return None
        return _parse_json(proc.stdout, cmd)


def _tail(text: str | None, limit: int = 400) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return "…" + text[-limit:]


def _parse_json(stdout: str, argv: Sequence[str]) -> Any:
    """Parse CLI JSON tolerantly (the CLI may print a trailing human line)."""
    text = (stdout or "").strip()
    if not text:
        raise CoalaCallError("CoALA CLI returned no output", argv=argv)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith(("{", "[")):
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise CoalaCallError(f"CoALA CLI returned non-JSON output: {_tail(text, 200)}", argv=argv)


# --------------------------------------------------------------------- memory store


class MemoryStore:
    """Campaign memory operations (episodes, facts, procedures, recall, search)."""

    def __init__(
        self,
        project: Path | str,
        name: str,
        *,
        cli: Any | None = None,
        translator: Translator | None = None,
    ) -> None:
        self.project = Path(project)
        self.name = name
        self.cli = cli if cli is not None else CoalaCli(skill_script(self.project, self.name))
        self.translator = translator or IdentityTranslator()

    # ----------------------------------------------------------------- introspection

    @property
    def script(self) -> Path:
        return Path(getattr(self.cli, "script", skill_script(self.project, self.name)))

    @property
    def db_path(self) -> Path:
        return memory_db(self.project, self.name)

    def exists(self) -> bool:
        return self.script.exists() and self.db_path.exists()

    # ------------------------------------------------------------------------- write

    def add_entry(
        self,
        kind: str,
        content: str,
        *,
        key: str | None = None,
        tags: str | None = None,
        origin: str = "agent",
        source: str | None = None,
        entities: str | None = None,
    ) -> dict[str, Any]:
        if kind not in KINDS:
            raise ValueError(f"unknown memory type {kind!r}; expected one of {', '.join(KINDS)}")
        args: list[str] = ["add", "--type", kind, "--content", content, "--origin", origin]
        if key:
            args += ["--key", key]
        if tags:
            args += ["--tags", tags]
        if source:
            args += ["--source", source]
        if entities:
            args += ["--entities", entities]
        result = self.cli.run(args)
        return result if isinstance(result, dict) else {"ok": True, "raw": result}

    def add_episode(self, content: str, *, key: str | None = None, origin: str = "agent", **kw: Any) -> dict[str, Any]:
        return self.add_entry(
            KIND_EPISODIC, content, key=key, tags=kw.pop("tags", TAG_EPISODE), origin=origin, **kw
        )

    def add_fact(self, content: str, *, key: str | None = None, origin: str = "agent", **kw: Any) -> dict[str, Any]:
        return self.add_entry(
            KIND_SEMANTIC, content, key=key, tags=kw.pop("tags", TAG_FACT), origin=origin, **kw
        )

    def add_procedure(self, content: str, *, key: str | None = None, origin: str = "agent", **kw: Any) -> dict[str, Any]:
        return self.add_entry(
            KIND_PROCEDURAL, content, key=key, tags=kw.pop("tags", TAG_PROCEDURE), origin=origin, **kw
        )

    def supersede(
        self,
        entry_id: str | int,
        content: str,
        *,
        origin: str = "owner",
        tags: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Replace the *active* record ``entry_id`` with new content (keeps history)."""
        args: list[str] = [
            "supersede",
            str(entry_id),
            "--content",
            content,
            "--origin",
            origin,
        ]
        if tags:
            args += ["--tags", tags]
        if source:
            args += ["--source", source]
        result = self.cli.run(args)
        return result if isinstance(result, dict) else {"ok": True, "raw": result}

    def edit(self, entry_id: str | int, content: str, **kw: Any) -> dict[str, Any]:
        """Editor semantics: supersede when the record exists, otherwise add it.

        Unknown ids are *created* (upsert) so the UI can save rows it just made up;
        this mirrors "enviar só o que muda" from the memory editor.
        """
        try:
            return self.supersede(entry_id, content, **kw)
        except CoalaCallError:
            log.debug("supersede(%s) failed — creating a new record instead", entry_id)
            return self.add_episode(content)

    def save_episode(
        self,
        content: str,
        *,
        entry_id: str | None = None,
        origin: str = "owner",
    ) -> dict[str, Any]:
        """Memory-editor entry point: PT->EN then insert (no id) or supersede (id)."""
        canonical = safe_translate(self.translator, content, "en")
        if entry_id:
            return self.edit(entry_id, canonical, origin=origin, tags=TAG_EPISODE)
        return self.add_episode(canonical, origin=origin)

    # -------------------------------------------------------------------------- read

    def recall(
        self,
        query: str | None = None,
        *,
        budget: int = 2000,
        top: int = 12,
        kind: str | None = None,
        tags: str | None = None,
    ) -> dict[str, Any]:
        args: list[str] = ["recall", "--budget", str(int(budget)), "--top", str(int(top))]
        if kind:
            args += ["--type", kind]
        if tags:
            args += ["--tags", tags]
        if query:
            args.append(query)
        result = self.cli.run(args, tolerate_non_json=True)
        if isinstance(result, dict):
            return result
        # Empty recall: the CLI answers with a human sentence, not JSON.
        return {"budget_tokens": int(budget), "used_tokens": 0, "count": 0, "items": []}

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        kind: str | None = None,
        tags: str | None = None,
        include_superseded: bool = False,
    ) -> list[dict[str, Any]]:
        args: list[str] = ["search", query, "--limit", str(int(limit))]
        if kind:
            args += ["--type", kind]
        if tags:
            args += ["--tags", tags]
        if include_superseded:
            args.append("--include-superseded")
        result = self.cli.run(args, tolerate_non_json=True)
        if isinstance(result, dict):
            return list(result.get("results") or [])
        return list(result or [])

    def search_knowledge(self, query: str, *, limit: int = KNOWLEDGE_LIMIT) -> list[dict[str, Any]]:
        """Read-only search over the shared ``conhecimento/`` base (may be empty)."""
        return search_knowledge(query, limit=limit)

    def stats(self) -> dict[str, Any]:
        result = self.cli.run(["stats"])
        return result if isinstance(result, dict) else {}

    def export(self) -> dict[str, Any]:
        """Full dump (``export --format json --out <tmp>``), never the DB directly."""
        with tempfile.TemporaryDirectory(prefix="sod-coala-export-") as tmp:
            out = Path(tmp) / "export.json"
            self.cli.run(["export", "--format", "json", "--out", str(out)], json_output=False)
            if not out.exists():
                raise CoalaCallError("export produced no file", argv=["export"])
            return json.loads(out.read_text(encoding="utf-8"))

    # ------------------------------------------------------- higher level helpers

    def narrative(self) -> str:
        """The rolling narrative line (active ``narrative`` record), ``""`` if unset."""
        items = self.recall(
            None, budget=DISPLAY_BUDGET, top=1, kind=KIND_SEMANTIC, tags=TAG_NARRATIVE
        ).get("items") or []
        for item in items:
            content = item.get("content")
            if content:
                return str(content)
        return ""

    def set_narrative(self, text: str, *, origin: str = "owner") -> dict[str, Any]:
        """Store the rolling narrative (PT->EN through the translator first)."""
        canonical = safe_translate(self.translator, text, "en")
        return self.add_entry(
            KIND_SEMANTIC,
            canonical,
            key=KEY_NARRATIVE,
            tags=TAG_NARRATIVE,
            origin=origin,
        )

    def narrative_pt(self) -> str:
        return safe_translate(self.translator, self.narrative(), "pt")

    def episodes(self, limit: int = 50) -> list[dict[str, Any]]:
        """Active episodes, newest first, already in the API episode shape (``_pt`` filled)."""
        items = self.recall(
            None,
            budget=DISPLAY_BUDGET,
            top=int(limit),
            kind=KIND_EPISODIC,
            tags=TAG_EPISODE,
        ).get("items") or []
        episodes = [
            _as_episode(
                item,
                content_pt=safe_translate(
                    self.translator, str(item.get("content") or ""), "pt"
                ),
            )
            for item in items
        ]
        episodes.sort(key=lambda e: (e["ts"] or ""), reverse=True)
        return episodes

    def working_memory(self, *, budget: int = 1500, top: int = 12) -> str:
        """Budgeted working-memory excerpt, rendered for the UI/prompt."""
        data = self.recall(None, budget=budget, top=top)
        lines = []
        for item in data.get("items") or []:
            kind = item.get("type") or "?"
            content = (item.get("content") or "").strip().replace("\n", " ")
            if content:
                lines.append(f"[{kind}] {content}")
        return "\n".join(lines)

    def updated_at(self) -> str | None:
        """Newest ``recorded_at`` across the current display set (``…Z`` form)."""
        items = self.recall(None, budget=DISPLAY_BUDGET, top=20).get("items") or []
        stamps = [to_z(item.get("recorded_at")) for item in items]
        stamps = [s for s in stamps if s]
        return max(stamps) if stamps else None


def _as_episode(item: dict[str, Any], *, content_pt: str | None = None) -> dict[str, Any]:
    kind = item.get("type") or KIND_EPISODIC
    return {
        "id": str(item.get("id")),
        "ts": to_z(item.get("recorded_at")) or "",
        "kind": kind,
        "content": str(item.get("content") or ""),
        "content_pt": content_pt if content_pt is not None else str(item.get("content") or ""),
    }


# ------------------------------------------------------------------ knowledge base

def _knowledge_cli() -> CoalaCli | None:
    db = knowledge_memory_db()
    if not db.exists():
        log.debug("knowledge base not installed yet at %s", db)
        return None
    return CoalaCli(skill_script(knowledge_dir(), KNOWLEDGE_NAME), db=db)


def search_knowledge(query: str, *, limit: int = KNOWLEDGE_LIMIT) -> list[dict[str, Any]]:
    """Read-only search on the shared knowledge base (``conhecimento/``).

    Returns ``[]`` when the base is missing, empty or unreachable — a later piece
    owns it; we only reserve the path/config constant and the read-only helper.
    """
    cli = _knowledge_cli()
    if cli is None:
        return []

    attempts = [int(limit)]
    while attempts[-1] > 1:
        attempts.append(max(1, attempts[-1] // 2))

    for k in attempts:
        try:
            result = cli.run(["search", query, "--limit", str(k)])
        except CoalaError as exc:
            log.warning("knowledge search (limit=%s) failed: %s — retrying smaller", k, exc)
            continue
        if isinstance(result, dict):
            return list(result.get("results") or [])
        return list(result or [])
    return []
