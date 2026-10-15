"""Extração derivada de factos e palavras dadas (ARQUITETURA.md §8, mudança 01).

Porquê: promessas feitas ("palavras dadas") e factos ditos ("o ouro está em X")
merecem recuperação própria, citada. :class:`ExtractionStore` guarda cada
**extração** com a sua citação — o ``source_ref`` (``dialogue:<id>`` /
``logbook:<id>``) mais o ``quote`` exato da fonte — para que todo o facto
derivado seja verificável contra a transcrição.

REGRA DURA: a transcrição bruta (``dialogos.sqlite``) e o diário (``diario.sqlite``)
**nunca** são apagados nem reescritos por aqui. A extração é uma camada
**derivada** — pode estar errada e pode ser refeita; a fonte é a verdade. Por
isso este armazém só acrescenta (``facts`` é append-only na prática: sem API de
update; ``forget`` existe só para PII) e marca o que já processou em
``processed`` (marcas d'água), para a extração assíncrona poder parar e retomar.

Assíncrono: :meth:`ExtractionStore.run_pending` faz o trabalho (determinístico,
sem LLM) e :meth:`ExtractionStore.kick` lança-o numa thread daemon — a conversa
nunca espera pela extração. O extrator é injetável (``extractor(text, role) ->
iterable de ExtractedDraft``), para o narrador/JEV-M fornecer factos melhores no
futuro; sem ele, as regras locais extraem direções, promessas e avisos.

Ficheiro: ``extracoes.sqlite`` na raiz da campanha (dir 0700, ficheiro 0600).
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .dialogue import interlocutor_key
from .recall import (
    DEFAULT_DECAY,
    DEFAULT_DIM,
    Candidate,
    ScoreWeights,
    Scored,
    clean_text,
    cosine,
    fit_lines,
    format_age,
    guarded,
    hash_embed,
    is_real,
    iso_from_epoch,
    meta_get,
    meta_set,
    neutralize,
    pack_vector,
    rank,
    unpack_vector,
)
from .recall import connect_private

log = logging.getLogger("system_one_dwarf.campaign.extraction")

__all__ = [
    "EXTRACTION_DB_NAME",
    "ExtractedDraft",
    "ExtractedFact",
    "ExtractionStore",
    "rule_extractor",
]

EXTRACTION_DB_NAME = "extracoes.sqlite"
SCHEMA_VERSION = 1

KIND_FACT, KIND_PROMISE, KIND_WARNING = "fact", "promise", "warning"
KINDS = (KIND_FACT, KIND_PROMISE, KIND_WARNING)
BASE_IMPORTANCE = {KIND_FACT: 5, KIND_PROMISE: 7, KIND_WARNING: 6}

SOURCE_DIALOGUE, SOURCE_LOGBOOK = "dialogue", "logbook"
MAX_QUOTE_CHARS = 240
MAX_FACT_CHARS = 280
BLOCK_LINE_CHARS = 150
DEFAULT_PENDING_BATCH = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, wall REAL NOT NULL,
    kind TEXT NOT NULL, text TEXT NOT NULL, quote TEXT NOT NULL,
    source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_ref TEXT NOT NULL,
    source_ts TEXT, interlocutor TEXT, importance INTEGER NOT NULL,
    extractor TEXT NOT NULL, vec BLOB NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS facts_by_source ON facts (source_ref, text);
CREATE INDEX IF NOT EXISTS facts_by_interlocutor ON facts (interlocutor, id);
CREATE INDEX IF NOT EXISTS facts_by_importance ON facts (importance, id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# ---------------------------------------------------------------- extração local

_PROMISE_RE = re.compile(
    r"\b(?:i (?:will|shall|'ll)|we (?:will|shall|'ll)|promise[sd]?|shall be rewarded|"
    r"reward you|pay you|give you)\b[^.!?]{2,120}",
    re.IGNORECASE,
)
_DIRECTION_RE = re.compile(
    r"\b(?:seek out|look for|go to|head to|travel to|over in|located in|lives? in|"
    r"lies? (?:in|to|north|south|east|west))\b[^.!?]{2,140}",
    re.IGNORECASE,
)
_WARNING_RE = re.compile(
    r"\b(?:beware|watch out|avoid|do not|don't|never|prepare to die|danger\w*)\b[^.!?]{2,120}",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ExtractedDraft:
    """Uma extração crua (antes de ganhar citação): o que, e de que tipo."""

    kind: str
    text: str
    quote: str = ""


def rule_extractor(text: Any, role: str = "") -> list[ExtractedDraft]:
    """Extrator determinístico local: direções, promessas ("palavras dadas") e avisos.

    Sem LLM de propósito — a extração assíncrona tem de ser barata e reproduzível;
    um extrator melhor entra pela costura ``extractor`` de
    :meth:`ExtractionStore.run_pending`.
    """
    body = text if isinstance(text, str) else ""
    if not body.strip():
        return []
    drafts: list[ExtractedDraft] = []
    for pattern, kind in ((_PROMISE_RE, KIND_PROMISE), (_DIRECTION_RE, KIND_FACT), (_WARNING_RE, KIND_WARNING)):
        for match in pattern.finditer(body):
            quote = match.group(0).strip(" ,;:")
            if len(quote) < 8:
                continue
            drafts.append(ExtractedDraft(kind=kind, text=clean_text(quote, MAX_FACT_CHARS),
                                         quote=clean_text(quote, MAX_QUOTE_CHARS)))
    return drafts


# ---------------------------------------------------------------- tipos públicos


@dataclass(frozen=True)
class ExtractedFact:
    """Uma extração **derivada** e citável.

    ``source_ref`` (``dialogue:<id>`` / ``logbook:<id>``) + ``quote`` (trecho exato
    da fonte) são a citação: qualquer facto extraído aponta para a mensagem ou
    episódio de origem.
    """

    id: int
    ts: str
    kind: str
    text: str
    quote: str
    source_kind: str
    source_id: str
    source_ref: str
    source_ts: str | None
    interlocutor: str | None
    importance: int
    extractor: str
    age_hours: float | None = None

    def citation(self) -> str:
        """``"«quote» (dialogue:12)"`` — como o facto se cita de volta à fonte."""
        quote = neutralize(self.quote, MAX_QUOTE_CHARS)
        return f'"{quote}" ({self.source_ref})'

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["citation"] = self.citation()
        return out


Extractor = Callable[[str, str], Iterable[ExtractedDraft | Mapping[str, Any] | tuple[str, ...]]]


class ExtractionStore:
    """Camada derivada de factos/palavras dadas, sobre ``extracoes.sqlite``.

    Append-only na prática: só :meth:`record` escreve factos; não há update.
    :meth:`forget` apaga o espaço de um interlocutor (PII) e nada mais.
    """

    def __init__(
        self,
        db_path: Path | str,
        *,
        clock: Callable[[], float] = time.time,
        weights: ScoreWeights | Mapping[str, Any] | None = None,
        decay: float = DEFAULT_DECAY,
        dim: int = DEFAULT_DIM,
    ) -> None:
        self.db_path = Path(db_path)
        self.clock = clock
        self.weights = ScoreWeights.from_any(weights)
        self.decay = decay
        self.dim = dim
        self.errors = 0
        self._lock = threading.RLock()

    @classmethod
    def from_campaign_root(cls, root: Path | str, **kwargs: Any) -> "ExtractionStore":
        """``<raiz da campanha>/extracoes.sqlite``."""
        return cls(Path(root) / EXTRACTION_DB_NAME, **kwargs)

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = connect_private(self.db_path)
            try:
                if conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    conn.executescript(_SCHEMA)
                    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                yield conn
                conn.commit()
            finally:
                conn.close()

    # --------------------------------------------------------------- write

    @guarded(lambda: None)
    def record(
        self,
        draft: ExtractedDraft | Mapping[str, Any] | Sequence[Any],
        *,
        source_kind: str,
        source_id: Any,
        source_ts: Any = None,
        interlocutor: Any = None,
        extractor: str = "rules",
        importance: int | None = None,
    ) -> ExtractedFact | None:
        """Guarda uma extração com a citação da origem. ``None`` se vazia/repetida.

        A citação é obrigatória: sem ``source_kind``/``source_id`` válidos não há
        facto — um facto sem origem não é verificável e não entra.
        """
        parsed = _as_draft(draft)
        if parsed is None:
            return None
        kind = parsed.kind if parsed.kind in KINDS else KIND_FACT
        if source_kind not in (SOURCE_DIALOGUE, SOURCE_LOGBOOK):
            return None
        try:
            sid = int(source_id)
        except (TypeError, ValueError):
            return None
        if isinstance(source_id, bool) or sid <= 0:
            return None
        quote = clean_text(parsed.quote or parsed.text, MAX_QUOTE_CHARS)
        level = min(10, max(1, int(round(importance)))) if is_real(importance) else BASE_IMPORTANCE[kind]
        wall = float(self.clock())
        ts = iso_from_epoch(wall)
        ref = f"{source_kind}:{sid}"
        with self._session() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO facts (ts, wall, kind, text, quote, source_kind, source_id, "
                "source_ref, source_ts, interlocutor, importance, extractor, vec) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, wall, kind, parsed.text, quote, source_kind, str(sid),
                 ref, clean_text(source_ts, 40) if isinstance(source_ts, str) else None,
                 interlocutor_key(interlocutor) if isinstance(interlocutor, str) and interlocutor.strip() else None,
                 level, clean_text(extractor, 40) or "rules",
                 pack_vector(hash_embed(f"{kind} {parsed.text}", self.dim))),
            )
            if cur.rowcount == 0:
                return None  # já extraído: nada a acrescentar
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (cur.lastrowid,)).fetchone()
        return self._fact(row)

    @staticmethod
    def _fact(row: sqlite3.Row, age: float | None = None) -> ExtractedFact:
        return ExtractedFact(
            id=int(row["id"]), ts=row["ts"], kind=row["kind"], text=row["text"], quote=row["quote"],
            source_kind=row["source_kind"], source_id=row["source_id"], source_ref=row["source_ref"],
            source_ts=row["source_ts"], interlocutor=row["interlocutor"],
            importance=int(row["importance"]), extractor=row["extractor"], age_hours=age,
        )

    @guarded(lambda: 0)
    def forget(self, interlocutor: Any) -> int:
        """Apaga as extrações de um interlocutor (PII). Devolve quantas apagou."""
        if not isinstance(interlocutor, str) or not interlocutor.strip():
            return 0
        with self._session() as conn:
            return int(conn.execute(
                "DELETE FROM facts WHERE interlocutor = ?", (interlocutor_key(interlocutor),)
            ).rowcount)

    # ------------------------------------------------- extração assíncrona

    @staticmethod
    def _watermark(conn: sqlite3.Connection, key: str) -> int:
        try:
            return max(0, int(meta_get(conn, f"processed_{key}", "0") or 0))
        except ValueError:
            return 0

    @guarded(lambda: 0)
    def pending(self, dialogue: Any = None, logbook: Any = None, *, limit: int = DEFAULT_PENDING_BATCH) -> int:
        """Quantas fontes (falas + eventos) faltam processar."""
        total = 0
        with self._session() as conn:
            dlg_from = self._watermark(conn, "dialogue")
            log_from = self._watermark(conn, "logbook")
        if dialogue is not None:
            total += len(dialogue.lines_after(dlg_from, limit))
        if logbook is not None:
            total += len(logbook.events_after(log_from, limit))
        return total

    @guarded(list)
    def run_pending(
        self,
        dialogue: Any = None,
        logbook: Any = None,
        *,
        extractor: Extractor | None = None,
        limit: int = DEFAULT_PENDING_BATCH,
    ) -> list[ExtractedFact]:
        """Extrai factos das fontes ainda não processadas e avança as marcas d'água.

        Só **lê** as fontes (``lines_after``/``events_after``) e só **acrescenta**
        factos — a transcrição bruta nunca é tocada. Um extrator que falhe numa
        fala/evento é saltado (``errors``+1, a fonte segue) e repetir a ronda é
        sempre seguro: ``INSERT OR IGNORE`` por citação não duplica factos.
        Devolve os factos novos.
        """
        extract = extractor or rule_extractor
        made: list[ExtractedFact] = []
        with self._session() as conn:
            dlg_from = self._watermark(conn, "dialogue")
            log_from = self._watermark(conn, "logbook")

        if dialogue is not None:
            lines = list(dialogue.lines_after(dlg_from, limit))
            for line in lines:
                made.extend(self._extract_one(
                    extract, line.text, SOURCE_DIALOGUE, line.id, line.ts, line.interlocutor, "rules"))
            if lines:
                with self._session() as conn:
                    meta_set(conn, "processed_dialogue", int(lines[-1].id))

        if logbook is not None:
            events = list(logbook.events_after(log_from, limit))
            for ev in events:
                made.extend(self._extract_one(
                    extract, ev.text, SOURCE_LOGBOOK, ev.id, ev.ts, None, "rules"))
            if events:
                with self._session() as conn:
                    meta_set(conn, "processed_logbook", int(events[-1].id))
        return made

    def _extract_one(
        self, extract: Extractor, text: Any, source_kind: str, source_id: Any,
        source_ts: Any, interlocutor: Any, extractor_name: str,
    ) -> list[ExtractedFact]:
        source = text if isinstance(text, str) else ""
        try:
            drafts = list(extract(source, source_kind))
        except Exception:
            log.warning("extractor failed on %s:%s — skipping (source untouched)", source_kind, source_id,
                        exc_info=True)
            self.errors += 1
            return []
        out: list[ExtractedFact] = []
        for draft in drafts:
            fixed = _anchor_quote(draft, source)
            fact = self.record(
                fixed, source_kind=source_kind, source_id=source_id, source_ts=source_ts,
                interlocutor=interlocutor, extractor=extractor_name,
            )
            if fact is not None:
                out.append(fact)
        return out

    def kick(self, *args: Any, **kwargs: Any) -> threading.Thread:
        """Lança :meth:`run_pending` numa thread daemon (extração assíncrona).

        Devolve a thread (joinável nos testes); nunca lança nem bloqueia a
        conversa. Falhas dentro da thread ficam nos logs e nas marcas d'água.
        """

        def worker() -> None:
            try:
                self.run_pending(*args, **kwargs)
            except Exception:  # pragma: no cover - defesa final da thread
                log.warning("background extraction crashed", exc_info=True)

        thread = threading.Thread(target=worker, name="sod-extraction", daemon=True)
        thread.start()
        return thread

    # ---------------------------------------------------------------- read

    @guarded(list)
    def recall(
        self, query: Any, k: int = 5, *, interlocutor: Any = None, kind: Any = None,
        weights: Any = None, now: Any = None,
    ) -> list[Scored[ExtractedFact]]:
        """Top-``k`` extrações por recência + importância + similaridade.

        ``now`` é epoch de parede (esta loja não usa relógio de jogo); ``kind`` e
        ``interlocutor`` restringem o conjunto.
        """
        clauses, args = [], ()
        if isinstance(interlocutor, str) and interlocutor.strip():
            clauses.append("interlocutor = ?")
            args += (interlocutor_key(interlocutor),)
        if kind in KINDS:
            clauses.append("kind = ?")
            args += (kind,)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._session() as conn:
            rows = conn.execute(f"SELECT * FROM facts{where} ORDER BY id DESC LIMIT 2000", args).fetchall()
        moment = float(self.clock()) if now is None or not is_real(now) else float(now)
        qvec = hash_embed(clean_text(query, 4000), self.dim)
        pool = [
            Candidate(
                item=self._fact(r, max(0.0, (moment - float(r["wall"])) / 3600.0)),
                age_hours=max(0.0, (moment - float(r["wall"])) / 3600.0),
                importance=float(r["importance"]),
                relevance=cosine(qvec, unpack_vector(r["vec"])),
            )
            for r in rows
        ]
        return rank(pool, weights=ScoreWeights.from_any(weights, self.weights), decay=self.decay, k=k)

    @guarded(list)
    def promises(self, interlocutor: Any = None, k: int = 10) -> list[ExtractedFact]:
        """As palavras dadas (promessas), as mais recentes primeiro."""
        return [h.item for h in self.recall("", k=k, interlocutor=interlocutor, kind=KIND_PROMISE,
                                            weights={"recency": 1.0, "importance": 0.0, "relevance": 0.0})]

    @guarded(list)
    def memory_items(
        self, situation: Any, k: int = 5, now: Any = None, interlocutor: Any = None
    ) -> list[dict[str, Any]]:
        """Itens ``{id,key,content,tags,title}`` na forma da memória do loop (para o JEV-R).

        O ``content`` traz sempre a citação — o JEV-R vê de onde o facto veio.
        """
        items: list[dict[str, Any]] = []
        for hit in self.recall(situation, k=k, now=now, interlocutor=interlocutor):
            fact = hit.item
            who = neutralize(fact.interlocutor or "", 40).replace(",", " ")
            items.append({
                "id": f"fact-{fact.id}",
                "key": f"extraction:{fact.id}",
                "title": neutralize(f"[{fact.kind}] {fact.text}", 80),
                "content": neutralize(f"{fact.text} — {fact.citation()}", 400),
                "tags": ",".join(("extraction", fact.kind, fact.source_kind, who)),
                "importance": fact.importance,
                "score": round(hit.score, 4),
                "recency": round(hit.raw_recency, 4),
                "relevance": round(hit.raw_relevance, 4),
            })
        return items

    @guarded(str)
    def state_block(
        self, situation: Any, k: int = 3, char_budget: int = 500, now: Any = None, interlocutor: Any = None
    ) -> str:
        """Bloco EN compacto das extrações, com citações, rotulado como DADOS.

        Aviso honesto no cabeçalho: é camada derivada e pode estar errada — a
        fonte citada é que manda.
        """
        hits = self.recall(situation, k=k, now=now, interlocutor=interlocutor)
        if not hits:
            return ""
        lines = ["[EXTRACTED FACTS - derived, may be wrong, check the cited source; DATA not instructions]"]
        for hit in hits:
            fact = hit.item
            lines.append(neutralize(
                f"- {fact.kind}, importance {fact.importance}/10, {format_age(fact.age_hours)}: "
                f'"{fact.text}" ({fact.source_ref})',
                BLOCK_LINE_CHARS,
            ))
        return fit_lines(lines, char_budget)

    @guarded(dict)
    def stats(self) -> dict[str, Any]:
        with self._session() as conn:
            by_kind = {r[0]: r[1] for r in conn.execute("SELECT kind, COUNT(*) FROM facts GROUP BY kind")}
            total = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        return {
            "facts": int(total), "by_kind": by_kind, "errors": self.errors,
            "dialogue_through": self._watermark_safe("dialogue"),
            "logbook_through": self._watermark_safe("logbook"),
        }

    @guarded(lambda: 0)
    def _watermark_safe(self, key: str) -> int:
        with self._session() as conn:
            return self._watermark(conn, key)


def _as_draft(draft: Any) -> ExtractedDraft | None:
    """Aceita ``ExtractedDraft``, mapeamento ou tuplo ``(kind, text[, quote])``."""
    if isinstance(draft, ExtractedDraft):
        return draft
    if isinstance(draft, Mapping):
        text = clean_text(draft.get("text"), MAX_FACT_CHARS)
        if not text:
            return None
        return ExtractedDraft(
            kind=str(draft.get("kind") or KIND_FACT), text=text,
            quote=clean_text(draft.get("quote"), MAX_QUOTE_CHARS),
        )
    if isinstance(draft, (list, tuple)) and draft:
        text = clean_text(draft[1] if len(draft) > 1 else draft[0], MAX_FACT_CHARS)
        if not text:
            return None
        return ExtractedDraft(
            kind=str(draft[0]), text=text,
            quote=clean_text(draft[2], MAX_QUOTE_CHARS) if len(draft) > 2 else "",
        )
    return None


def _anchor_quote(draft: Any, source: str) -> ExtractedDraft | Mapping[str, Any] | Sequence[Any] | None:
    """Garante que o ``quote`` é um trecho LITERAL da fonte (a citação é a âncora).

    Um extrator externo pode devolver um quote parafraseado — aí a citação deixa
    de ser verificável e usa-se antes um trecho real da fonte (o próprio texto
    extraído, se lá estiver; senão o início da fonte). Nunca altera a fonte.
    """
    parsed = _as_draft(draft)
    if parsed is None or not source:
        return draft if parsed is not None else None
    quote = parsed.quote or parsed.text
    if quote in source:
        return parsed
    match = re.search(re.escape(parsed.text[:60]), source, re.IGNORECASE) if parsed.text[:6] else None
    if match:
        return ExtractedDraft(kind=parsed.kind, text=parsed.text, quote=match.group(0))
    prefix = clean_text(source, MAX_QUOTE_CHARS)
    return ExtractedDraft(kind=parsed.kind, text=parsed.text, quote=prefix)
