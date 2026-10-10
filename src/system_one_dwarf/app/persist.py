"""Persistência SQLite dos stores do runtime (ARQUITETURA §9 · PROBLEMAS H3).

Porquê: ``decisions``/``questions``/``approvals``/``inputs`` eram anéis em memória
do :class:`~system_one_dwarf.app.runtime.CampaignRuntime` — um restart apagava a
trilha de auditoria, as aprovações pendentes (e uma já *libertada* podia libertar
a ação outra vez) e os contadores de ids.  Aqui está a metade durável:
``campanhas/<slug>/runtime.sqlite``, **write-through** (cada mutação do runtime é
um upsert) e recarga dos registos recentes quando o runtime nasce.

Contrato
--------
* o runtime em memória **continua a ser a verdade** (caps, ordem, paginação e
  dedupe inalterados); o store só dá durabilidade, por isso **nunca levanta**:
  erro de disco/corrupção ⇒ log + *memory-only* (``degraded``) e nova tentativa
  passado ``retry_after_s``;
* um ficheiro por campanha, dir ``0700``/ficheiro ``0600`` (como
  ``campaign/spend.py``), WAL, **uma ligação** guardada por ``RLock`` (loop +
  threads dos pedidos); SQL parametrizado, tabelas de lista fechada;
* ``(id, ts, status, payload JSON, updated_at)`` + ``seq`` (ordem de inserção:
  reconstrói o anel); **retenção** de 50 000 decisões / 20 000 perguntas / 20 000
  aprovações / 5 000 inputs só apara linhas **não pendentes**; o que o runtime
  *larga* (duplicado/cap) fica como lápide (``status = 'dropped'``): auditável,
  nunca recarregado;
* **segredos**: as chaves vivem no ``KeyStore``, mas o JSON persistido passa por
  :func:`scrub_payload` — a máscara de :func:`~system_one_dwarf.campaign.keys.mask_key`
  — para padrões de chave, campos com nome de segredo e as chaves vivas da
  campanha; texto de jogo/NPC é **dado**: controlos removidos, tudo com teto;
* ficheiro **corrompido** ⇒ posto de lado (``runtime.sqlite.corrupt-N``) e
  recomeço num novo; se nem isso for possível, *memory-only*.

:class:`TrackedRecord` avisa um *hook* de cada edição in-place: há código
(``approval_flow.release_approved_action``) que edita por referência os registos
do runtime; sem aviso a marca ``released_at`` ficava só em memória e, após um
restart, a mesma aprovação libertava a ação **duas vezes**.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from ..campaign.clock import utc_now_iso
from ..campaign.keys import mask_key
from ..campaign.paths import PRIVATE_FILE_MODE, ensure_private_dir

log = logging.getLogger("system_one_dwarf.app.persist")

__all__ = [
    "APPROVALS_RETENTION", "DECISIONS_RETENTION", "DEFAULT_RETENTION", "INPUTS_RETENTION",
    "KINDS", "KIND_APPROVALS", "KIND_DECISIONS", "KIND_INPUTS", "KIND_QUESTIONS",
    "MAX_PAYLOAD_BYTES", "MAX_STRING_CHARS", "OP_DROP", "OP_SAVE", "QUESTIONS_RETENTION",
    "RUNTIME_DB_NAME", "SCHEMA_VERSION", "STATUS_DROPPED",
    "RuntimeSnapshot", "RuntimeStore", "TrackedRecord",
    "dump_payload", "runtime_db_path", "scrub_payload",
]

#: Nome do ficheiro, na raiz da campanha (ao lado de ``gastos.sqlite``).
RUNTIME_DB_NAME = "runtime.sqlite"
SCHEMA_VERSION = 1

KIND_DECISIONS, KIND_QUESTIONS = "decisions", "questions"
KIND_APPROVALS, KIND_INPUTS = "approvals", "inputs"
#: Lista **fechada** de tabelas: a única origem de nomes de tabela no SQL.
KINDS: tuple[str, ...] = (KIND_DECISIONS, KIND_QUESTIONS, KIND_APPROVALS, KIND_INPUTS)
OP_SAVE, OP_DROP = "save", "drop"
STATUS_PENDING, STATUS_DROPPED = "pending", "dropped"

# ----------------------------------------------------------- números afináveis
#: Retenção em disco: linhas não pendentes mais recentes que se mantêm.
DECISIONS_RETENTION = 50_000
QUESTIONS_RETENTION = 20_000
APPROVALS_RETENTION = 20_000
INPUTS_RETENTION = 5_000
DEFAULT_RETENTION: Mapping[str, int] = {
    KIND_DECISIONS: DECISIONS_RETENTION, KIND_QUESTIONS: QUESTIONS_RETENTION,
    KIND_APPROVALS: APPROVALS_RETENTION, KIND_INPUTS: INPUTS_RETENTION,
}
#: A retenção corre a cada tantas linhas escritas (e à abertura): O(1) amortizado.
PRUNE_EVERY_WRITES = 200
BUSY_TIMEOUT_MS = 2_000      #: espera por um lock de outro processo
RETRY_AFTER_S = 30.0         #: tempo em *memory-only* depois de uma falha
LOG_EVERY_FAILURES = 50      #: a 1.ª falha é sempre logada; depois 1 em cada N
SECRETS_REFRESH_S = 30.0     #: cache das chaves vivas (ler o KeyStore a cada escrita é caro)
MIN_SECRET_CHARS = 8         #: chaves mais curtas não são mascaradas literalmente (ruído)
#: Tetos do JSON persistido (o texto do jogo/NPC é dado não confiável).
MAX_STRING_CHARS = 20_000
MAX_COLLECTION_ITEMS = 500
MAX_DEPTH = 12
MAX_KEY_CHARS = 120
MAX_PAYLOAD_BYTES = 262_144
#: Folga de leitura antes de mascarar uma string (um segredo a cavalo do corte).
SECRET_SLACK_CHARS = 64
#: Tetos das colunas indexáveis e da mensagem de erro guardada.
MAX_TS_CHARS, MAX_STATUS_CHARS, MAX_ID_CHARS, MAX_ERROR_CHARS = 64, 32, 200, 300
#: Tetos do 2.º intento quando o payload ainda passa do limite de bytes.
SHRUNK_STRING_CHARS, SHRUNK_COLLECTION_ITEMS, SHRUNK_DEPTH = 1_000, 50, 6

_CORRUPTION_MARKERS = ("not a database", "malformed", "corrupt", "disk image")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_\-]{19,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)([A-Za-z0-9._~+/=\-]{16,})")
#: Nomes de campo cujo VALOR (string) é sempre mascarado (comparação exata).
_SECRET_FIELDS = frozenset({
    "api_key", "apikey", "x_api_key", "authorization", "proxy_authorization", "secret",
    "client_secret", "password", "passwd", "token", "access_token", "refresh_token",
    "id_token", "bearer", "private_key",
})

_COLUMNS = "ts TEXT NOT NULL DEFAULT '', status TEXT, payload TEXT NOT NULL, updated_at TEXT NOT NULL"
_SCHEMA = (
    f"CREATE TABLE IF NOT EXISTS decisions (seq INTEGER PRIMARY KEY, id TEXT NOT NULL, {_COLUMNS});"
    "CREATE INDEX IF NOT EXISTS decisions_id ON decisions (id);"
    + "".join(
        f"CREATE TABLE IF NOT EXISTS {kind} (seq INTEGER PRIMARY KEY AUTOINCREMENT, "
        f"id TEXT NOT NULL UNIQUE, {_COLUMNS});"
        f"CREATE INDEX IF NOT EXISTS {kind}_status ON {kind} (status, seq);"
        for kind in (KIND_QUESTIONS, KIND_APPROVALS, KIND_INPUTS)
    )
    + "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
)
_SET = "ts = excluded.ts, status = excluded.status, payload = excluded.payload, updated_at = excluded.updated_at"
_UPSERT_DECISION = (
    "INSERT INTO decisions (seq, id, ts, status, payload, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
    f"ON CONFLICT(seq) DO UPDATE SET id = excluded.id, {_SET}"
)
_UPSERT_KEYED = (
    "INSERT INTO {table} (id, ts, status, payload, updated_at) VALUES (?, ?, ?, ?, ?) "
    f"ON CONFLICT(id) DO UPDATE SET {_SET}"
)


def runtime_db_path(root: Path | str) -> Path:
    """``<raiz da campanha>/runtime.sqlite``."""
    return Path(root) / RUNTIME_DB_NAME


def _as_limit(value: Any, default: int) -> int:
    """Um ``limit`` seguro (``int >= 0``): um argumento mau não pode degradar o store."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _close_quietly(conn: sqlite3.Connection) -> None:
    try:
        conn.close()
    except sqlite3.Error:  # pragma: no cover - best effort
        pass


# ================================================================== registo


class TrackedRecord(dict):
    """``dict`` que avisa um *hook* de cada edição in-place (write-through).

    Os registos do runtime são devolvidos **por referência** (``get_approval``,
    ``list_approvals``…) e há quem os edite fora da classe.  Cada escrita
    (``rec[k] = v``, ``update``, ``pop``…) chama ``hook(record)``; o runtime usa-o
    para persistir de imediato.  Copiar (``copy``/``deepcopy``/``pickle``) devolve
    um ``dict`` simples — uma cópia nunca escreve no disco.  Uma edição que não
    muda nada (``setdefault`` de chave existente…) também avisa: o upsert é
    idempotente, e é mais barato do que comparar.
    """

    __slots__ = ("_hook",)

    def bind(self, hook: Callable[["TrackedRecord"], Any] | None) -> "TrackedRecord":
        """Liga (ou, com ``None``, desliga) o hook de edição; devolve ``self``."""
        self._hook = hook
        return self

    def _changed(self) -> None:
        hook = getattr(self, "_hook", None)  # o slot pode não estar definido (cópias)
        if hook is not None:
            try:
                hook(self)
            except Exception:  # persistir é cortesia: a edição em memória já foi feita
                log.debug("tracked-record hook failed", exc_info=True)

    def __copy__(self) -> dict[Any, Any]:
        return dict(self)

    def __deepcopy__(self, memo: dict[int, Any]) -> dict[Any, Any]:
        return copy.deepcopy(dict(self), memo)

    def __reduce__(self) -> tuple[Any, ...]:
        return (dict, (dict(self),))


def _notifying(name: str) -> Callable[..., Any]:
    base = getattr(dict, name)

    def method(self: TrackedRecord, *args: Any, **kwargs: Any) -> Any:
        result = base(self, *args, **kwargs)  # um KeyError aqui nem chega a avisar
        self._changed()
        return result

    method.__name__ = name
    return method


for _name in ("__setitem__", "__delitem__", "setdefault", "update", "pop", "popitem",
              "clear", "__ior__"):
    setattr(TrackedRecord, _name, _notifying(_name))


# ================================================================ scrub / dump


def _mask_text(text: str, literals: Sequence[str]) -> str:
    """Máscara de chaves (formato de ``mask_key``) em chaves vivas e padrões."""
    for literal in literals:  # mais longas primeiro
        if literal in text:
            text = text.replace(literal, mask_key(literal))
    for pattern in _SECRET_RES:
        text = pattern.sub(lambda match: mask_key(match.group(0)), text)
    return _BEARER_RE.sub(lambda match: match.group(1) + mask_key(match.group(2)), text)


def _clean(value: Any, literals: Sequence[str], depth: int, strings: int, items: int,
           max_depth: int) -> Any:
    """Cópia JSON-segura e limitada de ``value`` (nunca levanta, nunca muta)."""
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        # Mascara ANTES de truncar: um segredo a cavalo do corte não pode sobrar.
        text = _mask_text(_CONTROL_RE.sub("", value[: strings + SECRET_SLACK_CHARS]), literals)
        return text if len(text) <= strings else f"{text[:strings]}…[truncated]"
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if depth >= max_depth:
        return "…"

    def sub(item: Any) -> Any:
        return _clean(item, literals, depth + 1, strings, items, max_depth)

    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        pairs = list(value.items())
        for index, (key, item) in enumerate(pairs):
            if index >= items:
                out["…"] = f"+{len(pairs) - index} more"
                break
            name = _CONTROL_RE.sub("", str(key))[:MAX_KEY_CHARS]
            secret = name.strip().lower().replace("-", "_") in _SECRET_FIELDS
            out[name] = mask_key(item.strip()) if secret and isinstance(item, str) and item.strip() \
                else sub(item)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        seq = sorted(value, key=repr) if isinstance(value, (set, frozenset)) else list(value)
        out_list = [sub(item) for item in seq[:items]]
        return out_list + [f"…+{len(seq) - items} more"] if len(seq) > items else out_list
    try:
        return _clean(str(value), literals, depth, strings, items, max_depth)
    except Exception:  # um ``__str__`` hostil não rebenta a escrita
        return "<unprintable>"


def scrub_payload(record: Any, secrets: Iterable[str] = (), *, shrink: bool = False) -> Any:
    """Cópia limitada e **sem segredos** de ``record`` (a forma que vai a disco).

    Usa a máscara de :func:`~system_one_dwarf.campaign.keys.mask_key`
    (``sk-or-v1-••••••••abcd``) em (1) as chaves vivas dadas em ``secrets``, (2)
    padrões de chave (``sk-or-v1-…``, ``sk-…``, ``Bearer …``, GitHub/Slack/AWS) e
    (3) o valor de campos chamados ``api_key``/``authorization``/``token``….
    ``shrink`` usa os tetos reduzidos do 2.º intento de :func:`dump_payload`.
    """
    literals = tuple(sorted(
        {s.strip() for s in secrets if isinstance(s, str) and len(s.strip()) >= MIN_SECRET_CHARS},
        key=len, reverse=True,
    ))
    if shrink:
        return _clean(record, literals, 0, SHRUNK_STRING_CHARS, SHRUNK_COLLECTION_ITEMS,
                      SHRUNK_DEPTH)
    return _clean(record, literals, 0, MAX_STRING_CHARS, MAX_COLLECTION_ITEMS, MAX_DEPTH)


def dump_payload(record: Any, secrets: Iterable[str] = ()) -> str:
    """JSON compacto, limitado e sem segredos de ``record`` (nunca levanta).

    Se passar de :data:`MAX_PAYLOAD_BYTES` tenta de novo com tetos menores e, em
    último caso, guarda só os campos escalares de topo + ``_truncated``.
    """
    try:
        base = dict(record) if isinstance(record, Mapping) else {"value": record}
        for shrink in (False, True):
            text = json.dumps(scrub_payload(base, secrets, shrink=shrink),
                              ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            if len(text.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
                return text
        scalars = scrub_payload({
            str(k)[:MAX_KEY_CHARS]: v for k, v in base.items()
            if v is None or isinstance(v, (str, int, float, bool))
        }, secrets, shrink=True)
        scalars["_truncated"] = True
        return json.dumps(scalars, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except Exception:  # pragma: no cover - defesa: um payload estranho nunca rebenta
        log.debug("could not serialise a runtime record", exc_info=True)
        return json.dumps({"_unserialisable": True})


# ============================================================== snapshot / store


@dataclass(frozen=True)
class RuntimeSnapshot:
    """O que o runtime recarrega ao nascer (tudo já em ordem de inserção)."""

    #: ``(seq, payload)`` por decisão, a mais antiga primeiro.
    decisions: tuple[tuple[int, dict[str, Any]], ...] = ()
    questions: tuple[dict[str, Any], ...] = ()
    approvals: tuple[dict[str, Any], ...] = ()
    inputs: tuple[dict[str, Any], ...] = ()
    #: Maior número já emitido por tabela (``d-N``/``q-N``/``in-N`` e ``seq``): o
    #: runtime continua daqui, e um id novo nunca colide com um guardado.
    counters: Mapping[str, int] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not (self.decisions or self.questions or self.approvals or self.inputs)


class RuntimeStore:
    """``runtime.sqlite`` de uma campanha: upsert write-through + recarga.

    Args:
        path: ficheiro SQLite (:func:`runtime_db_path`).
        retention: ``{tabela: linhas não pendentes a manter}``.
        clock: ``() -> str`` ISO-8601 (``updated_at``); injetável em testes.
        monotonic: ``() -> float`` (janela de *retry* e cache de segredos).
        secrets: ``() -> Iterable[str]`` com as chaves vivas da campanha (ou uma
            lista fixa); mascaradas literalmente em todo o JSON persistido.
        retry_after_s: tempo em *memory-only* depois de uma falha.
        prune_every: escritas entre duas aparas de retenção.

    Os métodos públicos **nunca levantam**: devolvem 0/vazio/``False`` e marcam o
    store como :attr:`degraded`.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        retention: Mapping[str, int] | None = None,
        clock: Callable[[], str] = utc_now_iso,
        monotonic: Callable[[], float] = time.monotonic,
        secrets: Callable[[], Iterable[str]] | Iterable[str] | None = None,
        retry_after_s: float = RETRY_AFTER_S,
        prune_every: int = PRUNE_EVERY_WRITES,
        busy_timeout_ms: int = BUSY_TIMEOUT_MS,
    ) -> None:
        self.path = Path(path)
        self.retention: dict[str, int] = dict(DEFAULT_RETENTION)
        for kind, cap in (retention or {}).items():
            if kind in self.retention:
                self.retention[kind] = max(1, int(cap))
        self._clock, self._monotonic, self._secrets = clock, monotonic, secrets
        self.retry_after_s = max(0.0, float(retry_after_s))
        self.prune_every = max(1, int(prune_every))
        self.busy_timeout_ms = max(0, int(busy_timeout_ms))
        self._lock = threading.RLock()
        self._secrets_lock = threading.Lock()
        self._secret_cache: tuple[str, ...] = ()
        self._secret_until = float("-inf")
        self._conn: sqlite3.Connection | None = None
        #: Fecha a ligação quando o store é recolhido (ou o interpretador sai).  Um
        #: ``__del__`` não chega: num ciclo (runtime ↔ hooks) o GC corre o
        #: finalizador da ``Connection`` ANTES do nosso e ela avisa "unclosed".
        self._finalizer: weakref.finalize | None = None
        self._retry_at: float | None = None
        self._since_prune = 0
        self.failures = self.writes = self.quarantined = 0
        self.last_error: str | None = None

    # ----------------------------------------------------------- diagnóstico

    @property
    def degraded(self) -> bool:
        """``True`` enquanto o store está *memory-only* por causa de uma falha."""
        return self._conn is None and self._retry_at is not None

    def status(self) -> dict[str, Any]:
        """Resumo JSON-friendly (sem segredos) para logs/diagnóstico."""
        return {
            "path": str(self.path), "degraded": self.degraded, "failures": self.failures,
            "writes": self.writes, "quarantined": self.quarantined,
            "last_error": self.last_error, "retention": dict(self.retention),
        }

    # ------------------------------------------------------------- ligação

    @staticmethod
    def _is_corruption(exc: BaseException) -> bool:
        """``file is not a database``/``disk image is malformed`` — não ``locked``/``full``."""
        return isinstance(exc, sqlite3.DatabaseError) and any(
            marker in str(exc).lower() for marker in _CORRUPTION_MARKERS)

    def _connect(self) -> sqlite3.Connection:
        ensure_private_dir(self.path.parent)
        conn = sqlite3.connect(str(self.path), timeout=self.busy_timeout_ms / 1000.0,
                               isolation_level=None, check_same_thread=False)
        try:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:  # FS sem suporte a WAL: segue em DELETE
                pass
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if row is None:
                conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
                             (str(SCHEMA_VERSION),))
            elif str(row[0]).isdigit() and int(row[0]) > SCHEMA_VERSION:
                raise RuntimeError(f"runtime store schema {row[0]} is newer than "
                                   f"{SCHEMA_VERSION}; staying memory-only")
        except BaseException:
            conn.close()
            raise
        for suffix in ("", "-wal", "-shm"):
            try:
                os.chmod(f"{self.path}{suffix}", PRIVATE_FILE_MODE)
            except OSError:  # o -wal/-shm pode não existir (journal_mode DELETE)
                pass
        return conn

    def _quarantine(self) -> bool:
        """Põe o ficheiro corrompido (e o WAL órfão) de lado; ``False`` se falhar."""
        try:
            index = 1
            while (target := self.path.with_name(f"{self.path.name}.corrupt-{index}")).exists():
                index += 1
            os.replace(self.path, target)
            for suffix in ("-wal", "-shm"):  # um WAL órfão corromperia o ficheiro novo
                stale = Path(f"{self.path}{suffix}")
                try:
                    os.replace(stale, Path(f"{target}{suffix}"))
                except OSError:
                    stale.unlink(missing_ok=True)
            self.quarantined += 1
            log.warning("runtime store %s was corrupt: moved to %s", self.path, target.name)
            return True
        except OSError:
            log.warning("could not quarantine the corrupt runtime store %s", self.path,
                        exc_info=True)
            return False

    def _close_locked(self) -> None:
        conn, self._conn = self._conn, None
        finalizer, self._finalizer = self._finalizer, None
        if finalizer is not None:
            finalizer.detach()
        if conn is not None:
            _close_quietly(conn)

    def _fail_locked(self, what: str, exc: BaseException) -> None:
        self.failures += 1
        self.last_error = f"{what}: {type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
        self._close_locked()
        self._retry_at = self._monotonic() + self.retry_after_s
        if self.failures == 1 or self.failures % LOG_EVERY_FAILURES == 0:
            log.warning("runtime store %s degraded to memory-only (%s); retry in %.0fs",
                        self.path, self.last_error, self.retry_after_s)

    def _ready_locked(self) -> bool:
        if self._conn is not None:
            return True
        if self._retry_at is not None and self._monotonic() < self._retry_at:
            return False
        try:
            self._conn = self._connect()
        except Exception as exc:
            if self._is_corruption(exc) and self._quarantine():
                try:
                    self._conn = self._connect()
                except Exception as exc2:  # nem o ficheiro novo abre: memory-only
                    self._fail_locked("open", exc2)
                    return False
            else:
                self._fail_locked("open", exc)
                return False
        self._finalizer = weakref.finalize(self, _close_quietly, self._conn)
        self._retry_at = None
        self._since_prune = self.prune_every  # apara na 1.ª escrita depois de abrir
        return True

    def _run(self, what: str, work: Callable[[sqlite3.Connection], Any], default: Any) -> Any:
        """Corre ``work(conn)`` sob o lock; qualquer erro ⇒ degrada e devolve ``default``."""
        with self._lock:
            if not self._ready_locked():
                return default
            try:
                assert self._conn is not None
                return work(self._conn)
            except Exception as exc:
                corrupt = self._is_corruption(exc)
                self._fail_locked(what, exc)  # fecha a ligação (o ficheiro vai mudar)
                if corrupt and self._quarantine():
                    self._retry_at = self._monotonic()  # reabre já, num ficheiro novo
                return default

    def _in_transaction(self, conn: sqlite3.Connection, body: Callable[[], Any]) -> Any:
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = body()
            conn.execute("COMMIT")
            return result
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:  # pragma: no cover - já sem transação
                pass
            raise

    # ------------------------------------------------------------- segredos

    def _secret_values(self) -> tuple[str, ...]:
        source = self._secrets
        if source is None:
            return ()
        now = self._monotonic()
        with self._secrets_lock:
            if now < self._secret_until:
                return self._secret_cache
        try:
            values = {str(v).strip() for v in (source() if callable(source) else source)
                      if isinstance(v, str)}
            fetched = tuple(sorted((v for v in values if len(v) >= MIN_SECRET_CHARS),
                                   key=len, reverse=True))
        except Exception:
            log.debug("could not read the live secrets", exc_info=True)
            fetched = self._secret_cache
        with self._secrets_lock:
            self._secret_cache, self._secret_until = fetched, now + SECRETS_REFRESH_S
        return fetched

    # --------------------------------------------------------------- escrita

    @staticmethod
    def _prepare(kind: str, key: Any, record: Any, secrets: Sequence[str], stamp: str
                 ) -> tuple[Any, ...] | None:
        if not isinstance(record, Mapping):
            return None
        text = dump_payload(record, secrets)
        ts = _CONTROL_RE.sub("", str(record.get("ts") or ""))[:MAX_TS_CHARS]
        status = str(record["status"])[:MAX_STATUS_CHARS] if record.get("status") else None
        if kind == KIND_DECISIONS:
            try:
                seq = int(key)
            except (TypeError, ValueError):
                return None
            return (seq, str(record.get("id") or f"d-{seq}")[:MAX_ID_CHARS], ts, status, text, stamp)
        ident = str(key or record.get("id") or "")[:MAX_ID_CHARS]
        return (ident, ts, status, text, stamp) if ident else None

    def apply_ops(self, ops: Sequence[Sequence[Any]]) -> int:
        """Aplica ``(op, kind, key, record)`` numa transação; devolve quantos.

        ``op`` é :data:`OP_SAVE` (upsert do ``record`` sob ``key``) ou
        :data:`OP_DROP` (lápide de uma pergunta/aprovação).  Entradas inválidas
        são ignoradas.  Nunca levanta; devolve 0 com o store degradado.
        """
        if not ops or (self._conn is None and self._retry_at is not None
                       and self._monotonic() < self._retry_at):
            return 0  # janela de *retry* aberta: nem vale a pena preparar linhas
        stamp, literals = self._clock(), self._secret_values()
        rows: list[tuple[str, str, tuple[Any, ...]]] = []
        for op in ops:
            try:
                action, kind, key, record = op
            except (TypeError, ValueError):
                continue
            if kind not in KINDS:
                continue
            if action == OP_SAVE:
                row = self._prepare(kind, key, record, literals, stamp)
                if row is not None:
                    rows.append((OP_SAVE, kind, row))
            elif action == OP_DROP and kind in (KIND_QUESTIONS, KIND_APPROVALS):
                rows.append((OP_DROP, kind, (STATUS_DROPPED, stamp, str(key))))
        if not rows:
            return 0

        def work(conn: sqlite3.Connection) -> int:
            def body() -> int:
                for action, kind, params in rows:
                    if action == OP_DROP:
                        conn.execute(f"UPDATE {kind} SET status = ?, updated_at = ? WHERE id = ?",
                                     params)
                    elif kind == KIND_DECISIONS:
                        conn.execute(_UPSERT_DECISION, params)
                    else:
                        conn.execute(_UPSERT_KEYED.format(table=kind), params)
                return len(rows)

            return self._in_transaction(conn, body)

        written = int(self._run("write", work, 0))
        if written:
            self.writes += written
            self._since_prune += written
            if self._since_prune >= self.prune_every:
                self.prune()
        return written

    def save(self, kind: str, key: Any, record: Mapping[str, Any]) -> bool:
        """Upsert de um registo (atalho de :meth:`apply_ops`)."""
        return bool(self.apply_ops([(OP_SAVE, kind, key, record)]))

    def drop(self, kind: str, key: Any) -> bool:
        """Lápide de uma pergunta/aprovação (``status = 'dropped'``)."""
        return bool(self.apply_ops([(OP_DROP, kind, key, None)]))

    def prune(self) -> int:
        """Aplica a retenção; devolve as linhas apagadas.  Nunca apaga pendentes."""

        def work(conn: sqlite3.Connection) -> int:
            def body() -> int:
                deleted = conn.execute(
                    "DELETE FROM decisions WHERE seq IN "
                    "(SELECT seq FROM decisions ORDER BY seq DESC LIMIT -1 OFFSET ?)",
                    (self.retention[KIND_DECISIONS],),
                ).rowcount
                for kind in (KIND_QUESTIONS, KIND_APPROVALS, KIND_INPUTS):
                    deleted += conn.execute(
                        f"DELETE FROM {kind} WHERE seq IN (SELECT seq FROM {kind} "
                        "WHERE status IS NOT ? ORDER BY seq DESC LIMIT -1 OFFSET ?)",
                        (STATUS_PENDING, self.retention[kind]),
                    ).rowcount
                return deleted

            return self._in_transaction(conn, body)

        deleted = int(self._run("prune", work, 0))
        self._since_prune = 0
        return max(0, deleted)

    # --------------------------------------------------------------- leitura

    @staticmethod
    def _rows(conn: sqlite3.Connection, kind: str, limit: int, *, pending_first: bool = False,
              before: str | None = None) -> list[tuple[int, dict[str, Any]]]:
        """``(seq, payload)`` dos ``limit`` mais recentes (mais antigo primeiro)."""
        sql = f"SELECT seq, id, payload FROM {kind} WHERE status IS NOT ?"
        params: list[Any] = [STATUS_DROPPED]
        if before is not None:
            sql += " AND ts < ?"
            params.append(str(before))
        sql += " ORDER BY "
        if pending_first:
            sql += "(status IS ?) DESC, "
            params.append(STATUS_PENDING)
        sql += "seq DESC LIMIT ?"
        params.append(limit)
        out: list[tuple[int, dict[str, Any]]] = []
        for seq, ident, text in sorted(conn.execute(sql, params).fetchall(), key=lambda r: r[0]):
            try:
                payload = json.loads(text)
            except (TypeError, ValueError):
                continue  # linha ilegível: salta (a recarga não pode falhar por uma)
            if isinstance(payload, dict):
                payload.setdefault("id", ident)
                out.append((int(seq), payload))
        return out

    @staticmethod
    def _max_suffix(conn: sqlite3.Connection, kind: str, prefix: str) -> int:
        best = 0
        for (ident,) in conn.execute(f"SELECT id FROM {kind} WHERE id GLOB ?",
                                     (f"{prefix}-[0-9]*",)):
            match = re.fullmatch(rf"{re.escape(prefix)}-(\d+)", str(ident))
            best = max(best, int(match.group(1))) if match else best
        return best

    def load_snapshot(
        self,
        *,
        decisions: int = DECISIONS_RETENTION,
        questions: int = QUESTIONS_RETENTION,
        approvals: int = APPROVALS_RETENTION,
        inputs: int = INPUTS_RETENTION,
    ) -> RuntimeSnapshot:
        """Os registos mais recentes (+ **todas** as pendentes) e os contadores.

        ``decisions``/``inputs`` são os ``limit`` mais recentes; ``questions`` e
        ``approvals`` carregam primeiro as pendentes (a fila viva do humano) e
        completam com as decididas mais recentes até ``limit``.  Lápides e linhas
        ilegíveis ficam de fora.  Nunca levanta (falha ⇒ snapshot vazio).
        """

        decisions = _as_limit(decisions, DECISIONS_RETENTION)
        questions = _as_limit(questions, QUESTIONS_RETENTION)
        approvals = _as_limit(approvals, APPROVALS_RETENTION)
        inputs = _as_limit(inputs, INPUTS_RETENTION)

        def work(conn: sqlite3.Connection) -> RuntimeSnapshot:
            def keyed(kind: str, limit: int) -> tuple[dict[str, Any], ...]:
                rows = self._rows(conn, kind, limit, pending_first=True)
                return tuple(payload for _seq, payload in rows)

            top = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM decisions").fetchone()[0]
            return RuntimeSnapshot(
                decisions=tuple(self._rows(conn, KIND_DECISIONS, decisions)),
                questions=keyed(KIND_QUESTIONS, questions),
                approvals=keyed(KIND_APPROVALS, approvals),
                inputs=tuple(p for _s, p in self._rows(conn, KIND_INPUTS, inputs)),
                counters={
                    KIND_DECISIONS: max(int(top), self._max_suffix(conn, KIND_DECISIONS, "d")),
                    KIND_QUESTIONS: self._max_suffix(conn, KIND_QUESTIONS, "q"),
                    KIND_INPUTS: self._max_suffix(conn, KIND_INPUTS, "in"),
                },
            )

        return self._run("load", work, RuntimeSnapshot())

    def load_recent(self, limit: int = 100) -> RuntimeSnapshot:
        """Os ``limit`` registos mais recentes de **cada** tabela (+ as pendentes)."""
        return self.load_snapshot(decisions=limit, questions=limit, approvals=limit,
                                  inputs=limit)

    def read(self, kind: str, limit: int = 100, *, before: str | None = None
             ) -> list[dict[str, Any]]:
        """Os ``limit`` registos mais recentes de ``kind`` (mais antigo primeiro).

        ``before`` é um cursor ``ts`` exclusivo (comparação textual: ISO-8601 UTC
        com ``Z``) — leitura de histórico além do anel em memória.
        """
        if kind not in KINDS:
            return []
        limit = _as_limit(limit, 100)
        return self._run(
            "read", lambda conn: [p for _s, p in self._rows(conn, kind, limit, before=before)], [])

    def count(self, kind: str, *, status: str | None = None) -> int:
        """Nº de linhas de ``kind`` (lápides incluídas; ``status`` filtra)."""
        if kind not in KINDS:
            return 0
        where, params = ("", ()) if status is None else (" WHERE status IS ?", (status,))
        return int(self._run(
            "count",
            lambda conn: conn.execute(f"SELECT COUNT(*) FROM {kind}{where}", params).fetchone()[0],
            0,
        ))

    # ---------------------------------------------------------------- ciclo

    def close(self) -> None:
        """Faz checkpoint do WAL e liberta a ligação (reabre sozinho se preciso)."""
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:  # pragma: no cover - best effort
                pass
            self._close_locked()
