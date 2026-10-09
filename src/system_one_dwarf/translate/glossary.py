"""Deterministic PT↔EN term substitution driven by ``dfhack/glossary-pt-en.tsv``.

The glossary is a **read-only** project artefact (275 lines, 258 data rows,
`termo_pt <TAB> termo_en [termo_en …]`).  It exists so that Dwarf Fortress jargon
stays consistent no matter what the language model decides to output: term
substitution is deterministic, costs zero tokens and can be applied both *before*
a call (PT→EN, so the model receives canonical English jargon) and *after* it
(EN→PT, so screen text shown to the user uses the project's Portuguese words).

Both directions are supported::

    g = Glossary.load()                    # tolerates a missing file
    g.to_en("Preciso de aço e ferro")      # -> "Preciso de steel e iron"
    g.to_pt("Steel battle axe")            # -> "aço battle axe"  (axe is jargon too)

Substitution rules (deterministic, in this order):

1. Longest term wins — ``aço doce`` is matched before ``aço``.
2. Word-boundary anchored (``(?<!\\w)…(?!\\w)``), so ``aço`` never matches inside
   ``açougue``.
3. First-seen synonym wins when a term has several translations (file order).
4. Case is preserved for a leading capital (``Aço`` → ``Steel``).
5. Applying the same direction twice is idempotent.

Nothing here raises because of the glossary: a missing, empty or malformed file
degrades to an empty glossary (use :meth:`Glossary.load` with ``required=True`` to
be strict on purpose).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "Glossary",
    "GlossaryEntry",
    "DEFAULT_GLOSSARY_RELPATH",
    "default_glossary_path",
    "DIRECTIONS",
]

#: Path of the shared glossary, relative to the repository root.
DEFAULT_GLOSSARY_RELPATH = Path("dfhack") / "glossary-pt-en.tsv"

#: Accepted direction names for :meth:`Glossary.translate`.
DIRECTIONS: tuple[str, ...] = ("pt->en", "en->pt")

#: Words are runs of letters (unicode), never digits or underscores.
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_MULTISPACE_RE = re.compile(r"[ \t]+")


def default_glossary_path(start: Path | None = None) -> Path | None:
    """First existing ``dfhack/glossary-pt-en.tsv`` walking up from this file."""
    here = Path(start) if start is not None else Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in here.parents:
        candidates.append(parent / DEFAULT_GLOSSARY_RELPATH)
    candidates.append(Path.cwd() / DEFAULT_GLOSSARY_RELPATH)
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


class GlossaryEntry:
    """One glossary row: a Portuguese term and its English synonym(s)."""

    __slots__ = ("pt", "en")

    def __init__(self, pt: str, en: Sequence[str]) -> None:
        self.pt = pt
        self.en: tuple[str, ...] = tuple(en)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, GlossaryEntry):
            return NotImplemented
        return self.pt == other.pt and self.en == other.en

    def __hash__(self) -> int:  # pragma: no cover - convenience
        return hash((self.pt, self.en))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"GlossaryEntry({self.pt!r}, {list(self.en)!r})"


class Glossary:
    """Immutable PT↔EN term table with word-boundary substitution."""

    def __init__(self, entries: Iterable[GlossaryEntry] = ()) -> None:
        self._entries: list[GlossaryEntry] = list(entries)
        #: pt term (casefolded) -> canonical EN synonym.
        self._pt_to_en: dict[str, str] = {}
        #: EN synonym (casefolded) -> canonical PT term.
        self._en_to_pt: dict[str, str] = {}
        for entry in self._entries:
            if entry.pt and entry.en:
                self._pt_to_en.setdefault(entry.pt.casefold(), entry.en[0])
            for synonym in entry.en:
                if not synonym or not entry.pt:
                    continue
                key = synonym.casefold()
                current = self._en_to_pt.get(key)
                if current is None or _prefer_pt(entry.pt, current):
                    self._en_to_pt[key] = entry.pt
        self._pt_pattern = _build_pattern(self._pt_to_en)
        self._en_pattern = _build_pattern(self._en_to_pt)
        self._signature = _signature(self._entries)

    # ------------------------------------------------------------------ loading

    @classmethod
    def empty(cls) -> "Glossary":
        """An empty glossary: every substitution is the identity."""
        return cls(())

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        required: bool = False,
    ) -> "Glossary":
        """Load ``path`` (default: the repository glossary), tolerating absence.

        With ``required=False`` (the default) a missing or unreadable file yields
        an empty glossary; with ``required=True`` it raises ``FileNotFoundError``.
        """
        if path is None:
            found = default_glossary_path()
            if found is None:
                if required:
                    raise FileNotFoundError(
                        f"glossary not found: looked for {DEFAULT_GLOSSARY_RELPATH}"
                    )
                return cls.empty()
            path = found
        target = Path(path)
        if not target.is_file():
            if required:
                raise FileNotFoundError(f"glossary not found: {target}")
            return cls.empty()
        try:
            text = target.read_text(encoding="utf-8")
        except OSError:
            if required:
                raise
            return cls.empty()
        return cls.from_text(text)

    @classmethod
    def from_text(cls, text: str) -> "Glossary":
        """Parse glossary text; malformed lines are skipped, never fatal."""
        entries: list[GlossaryEntry] = []
        for raw in (text or "").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "\t" not in line:
                continue
            left, right = line.split("\t", 1)
            pt = _MULTISPACE_RE.sub(" ", left.strip())
            synonyms = [s for s in _MULTISPACE_RE.sub(" ", right.strip()).split(" ") if s]
            if not pt or not synonyms:
                continue
            entries.append(GlossaryEntry(pt, synonyms))
        return cls(entries)

    @classmethod
    def from_file(cls, path: str | Path) -> "Glossary":
        """Strict :meth:`load` (raises when the file is missing)."""
        return cls.load(path, required=True)

    # -------------------------------------------------------------- inspection

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)

    def __iter__(self) -> Iterator[GlossaryEntry]:
        return iter(self._entries)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Glossary(entries={len(self._entries)})"

    @property
    def entries(self) -> tuple[GlossaryEntry, ...]:
        return tuple(self._entries)

    @property
    def pt_terms(self) -> tuple[str, ...]:
        return tuple(self._pt_to_en)

    @property
    def en_terms(self) -> tuple[str, ...]:
        return tuple(self._en_to_pt)

    @property
    def signature(self) -> str:
        """Stable content hash — part of translation cache keys."""
        return self._signature

    def mapping(self, direction: str) -> Mapping[str, str]:
        """Read-only view of the substitution table for ``direction``."""
        return dict(self._table(direction))

    # ------------------------------------------------------------ substitution

    def to_en(self, text: str) -> str:
        """Replace known Portuguese terms by their canonical English term."""
        return self.translate(text, "pt->en")

    def to_pt(self, text: str) -> str:
        """Replace known English terms by their canonical Portuguese term."""
        return self.translate(text, "en->pt")

    def translate(self, text: str, direction: str) -> str:
        """Apply the term table of ``direction`` (``"pt->en"``/``"en->pt"``)."""
        if not text:
            return text
        table, pattern = self._table_and_pattern(direction)
        if not table or pattern is None:
            return text
        return pattern.sub(lambda m: _replace(m, table), text)

    def matched_terms(self, text: str, direction: str) -> list[str]:
        """Source terms matched in ``text`` (diagnostics/tests)."""
        if not text:
            return []
        table, pattern = self._table_and_pattern(direction)
        if not table or pattern is None:
            return []
        return [m.group(0) for m in pattern.finditer(text)]

    def translate_complete(
        self,
        text: str,
        direction: str,
        *,
        max_tokens: int = 3,
    ) -> str | None:
        """Fully-substituted text, or ``None`` when a word is unknown.

        Used for the zero-cost fast path: a short text whose *every* word is
        glossary jargon can be translated deterministically without any LLM call.
        """
        if not text:
            return text
        tokens = _WORD_RE.findall(text)
        if not tokens or len(tokens) > int(max_tokens):
            return None
        table, pattern = self._table_and_pattern(direction)
        if not table or pattern is None:
            return None
        covered = 0

        def _sub(match: re.Match[str]) -> str:
            nonlocal covered
            replacement = table.get(match.group(0).casefold())
            if replacement is None:
                return match.group(0)
            covered += 1
            return _match_case(match.group(0), replacement)

        translated = pattern.sub(_sub, text)
        if covered == 0:
            return None
        if _WORD_RE.search(pattern.sub("", text)):
            return None
        return translated

    def _table(self, direction: str) -> Mapping[str, str]:
        if direction == "pt->en":
            return self._pt_to_en
        if direction == "en->pt":
            return self._en_to_pt
        raise ValueError(f"unknown direction {direction!r}; expected one of {DIRECTIONS}")

    def _table_and_pattern(self, direction: str) -> tuple[Mapping[str, str], re.Pattern[str] | None]:
        if direction == "pt->en":
            return self._pt_to_en, self._pt_pattern
        if direction == "en->pt":
            return self._en_to_pt, self._en_pattern
        raise ValueError(f"unknown direction {direction!r}; expected one of {DIRECTIONS}")


# --------------------------------------------------------------------- helpers


def _fold(text: str) -> str:
    """ASCII fold (drop combining accents) — ``aço`` and ``aco`` fold together."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _accented(text: str) -> bool:
    return _fold(text) != text


def _prefer_pt(candidate: str, current: str) -> bool:
    """Pick between two Portuguese spellings of the same English synonym.

    The glossary carries ASCII twins of accented terms (``aco``/``aço``,
    ``anao``/``anão``).  Deterministic preference, in order: the accented
    spelling (correct Portuguese for display), then a hyphen-free term.  With no
    reason to switch, the first-seen entry keeps winning.
    """
    if candidate == current:
        return False
    if _fold(candidate).casefold() == _fold(current).casefold():
        return _accented(candidate) and not _accented(current)
    if ("-" in current) != ("-" in candidate):
        return "-" not in candidate
    return False


def _build_pattern(table: Mapping[str, str]) -> re.Pattern[str] | None:
    terms = sorted(table, key=lambda term: (-len(term), term))
    if not terms:
        return None
    alternation = "|".join(re.escape(term) for term in terms)
    return re.compile(
        rf"(?<!\w)(?:{alternation})(?!\w)",
        re.IGNORECASE | re.UNICODE,
    )


def _replace(match: re.Match[str], table: Mapping[str, str]) -> str:
    replacement = table.get(match.group(0).casefold())
    if replacement is None:  # pragma: no cover - pattern only matches table terms
        return match.group(0)
    return _match_case(match.group(0), replacement)


def _match_case(source: str, replacement: str) -> str:
    """Mirror a leading capital of ``source`` onto ``replacement``."""
    if not source or not replacement:
        return replacement
    if source[0].isupper() and replacement[0].islower():
        return replacement[0].upper() + replacement[1:]
    if source.isupper() and len(source) > 1 and replacement.islower():
        return replacement.upper()
    return replacement


def _signature(entries: Sequence[GlossaryEntry]) -> str:
    digest = hashlib.sha256()
    for entry in entries:
        digest.update(entry.pt.encode("utf-8"))
        digest.update(b"\t")
        digest.update(" ".join(entry.en).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()[:16]
