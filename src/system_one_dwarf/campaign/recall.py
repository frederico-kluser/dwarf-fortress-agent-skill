"""Motor de recuperação partilhado pelas memórias de diálogo e de diário.

Porquê (ARQUITETURA.md §8, mudanças 01 e 08): cada sessão começava sem saber o
que foi dito nem o que mudou. As duas memórias novas (``dialogos.sqlite`` e
``diario.sqlite``) recuperam pelo critério dos *Generative Agents*
(arXiv 2304.03442): **recência** (decaimento exponencial por hora de JOGO) +
**importância** (escala 1-10) + **relevância** (cosseno), cada componente
normalizado min-max e somado com pesos iguais por omissão.

Contrato
--------
* Tudo é puro e **determinístico entre processos**: nunca se usa ``hash()`` do
  Python (muda com ``PYTHONHASHSEED``); o hashing usa ``zlib.crc32``.
* "Vetor local": hashing trick (unigramas + trigramas de caracteres), L2
  normalizado. Sem modelo de embeddings, sem rede, sem custo por chamada.
* Nada aqui levanta por entrada má: texto que não é ``str`` vira vazio, números
  inválidos viram valor neutro. Só :class:`ScoreWeights` valida no construtor
  (erro de configuração, nunca de runtime do loop).
* Texto do jogo e de NPCs é DADO: :func:`clean_text` tira controlos/bidi e limita
  o tamanho; :func:`neutralize` impede que um texto forje marcadores de bloco.

Relógio de recência (decisão): conta **horas de jogo**
(``ano * 8064 + tick / 50``, 1 hora = 50 ticks, 1 ano = 403 200 ticks — ver
``dfhack/dfb-state.lua``). Sem tempo de jogo cai para **horas de relógio de
parede**. Uma recuperação usa UM só relógio para o conjunto inteiro: misturar as
duas unidades falsearia a ordem (1 hora de parede ≈ 7 200 horas de jogo). Linhas
sem tempo de jogo, num conjunto que o tem, contam como as mais antigas.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import math
import os
import re
import sqlite3
import struct
import unicodedata
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from operator import mul
from pathlib import Path
from typing import Any, Callable, Generic, Iterable, Sequence, TypeVar

from .clock import to_z
from .paths import PRIVATE_FILE_MODE, ensure_private_dir

__all__ = [
    "Candidate",
    "DEFAULT_DECAY",
    "DEFAULT_DIM",
    "HOURS_PER_YEAR",
    "MAX_STORED_TEXT",
    "MAX_TOKENS",
    "STOPWORDS",
    "ScoreWeights",
    "Scored",
    "TimePoint",
    "after_last_marker",
    "ages_hours",
    "clean_text",
    "clock_game_hours",
    "coerce_now",
    "connect_private",
    "cosine",
    "fit_lines",
    "format_age",
    "game_hours",
    "guarded",
    "hash_embed",
    "is_real",
    "iso_from_epoch",
    "line_fingerprint",
    "meta_get",
    "meta_set",
    "minmax",
    "neutralize",
    "new_tail_start",
    "norm_key",
    "pack_vector",
    "rank",
    "recency_score",
    "state_game_hours",
    "strip_controls",
    "tokenize",
    "unpack_vector",
]

T = TypeVar("T")

log = logging.getLogger("system_one_dwarf.campaign.recall")

# ---------------------------------------------------------------- parâmetros
# Todos os números afináveis são constantes nomeadas (o orquestrador aplica a
# investigação externa por aqui, sem tocar na lógica).

#: Dimensão do vetor local (a base CoALA usa hashing-256 — mesma ordem de grandeza).
DEFAULT_DIM = 256
MIN_DIM = 8
MAX_DIM = 4096
#: Teto de tokens por texto (limita o custo e o peso de um parágrafo gigante).
MAX_TOKENS = 400
#: Teto de caracteres considerados por texto antes de qualquer processamento.
MAX_INPUT_CHARS = 20_000
MAX_TOKEN_CHARS = 48
UNIGRAM_WEIGHT = 1.0
#: Trigramas valem menos que palavras inteiras (dão robustez a flexões/gralhas).
TRIGRAM_WEIGHT = 0.35
#: Decaimento por hora de jogo (Generative Agents: 0.995).
DEFAULT_DECAY = 0.995
#: Limite de um texto guardado (fala ou evento).
MAX_STORED_TEXT = 600

TICKS_PER_HOUR = 50
TICKS_PER_YEAR = 403_200
HOURS_PER_YEAR = TICKS_PER_YEAR // TICKS_PER_HOUR  # 8064
SECONDS_PER_HOUR = 3600.0

#: Alinhamento mínimo (linhas) entre o rabo anterior e o atual para contar como
#: "já visto". 2 e não 1: uma linha só (``stands up``…) alinha por acaso; 2 e não
#: mais: num combate chegam muitas linhas por passo e sobram poucas antigas.
MIN_TAIL_OVERLAP = 2
#: Quantas impressões digitais do fim do rabo se persistem como âncora.
ANCHOR_SIZE = 8

SQLITE_TIMEOUT_S = 10.0

# ------------------------------------------------------------------- texto

_STOPWORDS_EN = (
    "a an the and or but nor of to in on at by for with from as into onto about than then "
    "so if is are was were be been being am do does did have has had it its this that these "
    "those i me my mine you your yours he him his she her hers we us our ours they them "
    "their theirs there here what which who whom will would shall should can could may might "
    "must oh ah um uh hmm"
)
_STOPWORDS_PT = (
    "a o as os um uma uns umas de do da dos das em no na nos nas num numa por pelo pela "
    "pelos pelas para pra com sem sob sobre entre e ou mas que se como mais menos já ainda "
    "também muito muita é são foi ser estar está estão era eu tu você vocês ele ela eles "
    "elas nós me te lhe lhes meu minha meus minhas teu tua seu sua seus suas este esta estes "
    "estas isto esse essa esses essas isso aquele aquela aquilo ao aos à às"
)

_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _fold(text: str) -> str:
    """Minúsculas + sem diacríticos (``Ñorlemhuh`` → ``norlemhuh``, ``não`` → ``nao``)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


#: Stopwords EN+PT, já dobradas (a comparação é feita sobre texto dobrado).
STOPWORDS: frozenset[str] = frozenset(_fold(w) for w in (_STOPWORDS_EN + " " + _STOPWORDS_PT).split())


def tokenize(text: Any, *, max_tokens: int = MAX_TOKENS) -> list[str]:
    """Texto → tokens: minúsculas, sem acentos, sem stopwords EN+PT, dígitos mantidos.

    Divide em tudo o que não é alfanumérico; devolve no máximo ``max_tokens``
    (por omissão 400). ``None``/não-``str``/vazio → ``[]``.
    """
    if not isinstance(text, str) or not text:
        return []
    folded = _fold(text[:MAX_INPUT_CHARS])
    try:
        limit = max(0, int(max_tokens))
    except (TypeError, ValueError):
        limit = MAX_TOKENS
    tokens: list[str] = []
    for match in _WORD_RE.finditer(folded):
        if len(tokens) >= limit:
            break
        token = match.group(0)[:MAX_TOKEN_CHARS]
        if token not in STOPWORDS:
            tokens.append(token)
    return tokens


def norm_key(text: Any) -> str:
    """Chave de comparação de um texto (dedupe): dobrado, só alfanuméricos, espaço simples."""
    if not isinstance(text, str) or not text:
        return ""
    return " ".join(_WORD_RE.findall(_fold(text[:MAX_INPUT_CHARS])))


# ---------------------------------------------------------- higiene de texto

_WHITESPACE_CONTROLS = frozenset("\t\n\r\v\f\x85\u2028\u2029")
# Cc controlos · Cf formato (bidi/zero-width: úteis a esconder injeção) · Cs
# surrogates soltos (o sqlite3 não os codifica) · Co uso privado · Cn por atribuir
_DROP_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})
_NEUTRALIZE = str.maketrans(
    {"[": "(", "]": ")", "{": "(", "}": ")", "<": "(", ">": ")", "`": "'", '"': "'"}
)


def strip_controls(text: Any) -> str:
    """Remove controlos/bidi/zero-width; quebras de linha viram espaço. Nunca levanta."""
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    text = text[:MAX_INPUT_CHARS]
    if text.isascii() and text.isprintable():
        return text
    out: list[str] = []
    for ch in text:
        if ch in _WHITESPACE_CONTROLS:
            out.append(" ")
        elif unicodedata.category(ch) not in _DROP_CATEGORIES:
            out.append(ch)
    return "".join(out)


def _ellipsize(text: str, limit: int) -> str:
    limit = max(1, int(limit))
    if len(text) <= limit:
        return text
    if limit == 1:
        return "…"
    return text[: limit - 1].rstrip() + "…"


def clean_text(text: Any, limit: int = MAX_STORED_TEXT) -> str:
    """Texto seguro para guardar: sem controlos, NFC, espaços colapsados, ≤ ``limit``."""
    stripped = strip_controls(text)
    if not stripped:
        return ""
    collapsed = " ".join(unicodedata.normalize("NFC", stripped).split())
    return _ellipsize(collapsed, limit)


def neutralize(text: Any, limit: int = MAX_STORED_TEXT) -> str:
    """:func:`clean_text` + troca de ``[]{}<>`"`` — o texto não forja marcadores nem sai das aspas."""
    return clean_text(text, limit).translate(_NEUTRALIZE)


def fit_lines(lines: Sequence[str], budget: int) -> str:
    """Junta linhas inteiras, por ordem, enquanto couberem em ``budget`` caracteres.

    A 1.ª linha é truncada se sozinha estoirar o orçamento; a primeira linha que
    já não cabe termina o bloco (preserva a ordem de relevância).
    """
    budget = max(1, int(budget))
    out: list[str] = []
    used = 0
    for line in lines:
        extra = len(line) + (1 if out else 0)
        if used + extra > budget:
            if not out:
                out.append(_ellipsize(line, budget))
            break
        out.append(line)
        used += extra
    return "\n".join(out)


def format_age(hours: Any) -> str:
    """Idade legível para os blocos de estado: ``just now``/``3h ago``/``5d ago``/``2mo ago``."""
    if not is_real(hours):
        return "earlier"
    value = float(hours)
    if value < 1:
        return "just now"
    if value < 48:
        return f"{int(value)}h ago"
    if value < 24 * 60:
        return f"{int(value // 24)}d ago"
    return f"{int(value // (24 * 30))}mo ago"


# ------------------------------------------------------------------- vetores

_dot = getattr(math, "sumprod", None) or (lambda a, b: sum(map(mul, a, b)))


def _clamp_dim(dim: Any) -> int:
    if isinstance(dim, bool) or not isinstance(dim, int):
        return DEFAULT_DIM
    return min(MAX_DIM, max(MIN_DIM, dim))


def hash_embed(text: Any, dim: int = DEFAULT_DIM) -> tuple[float, ...]:
    """Vetor local do texto (hashing trick com sinal), L2 normalizado.

    Características: unigramas (``w:<token>``) e trigramas de caracteres de
    ``#token#`` (``c:<tri>``), com peso sublinear ``1 + ln(contagem)``. O índice
    vem de ``crc32`` e o sinal de um segundo ``crc32`` com semente — estável
    entre processos. Texto vazio (ou só stopwords) → vetor nulo.
    """
    size = _clamp_dim(dim)
    tokens = tokenize(text)
    vec = [0.0] * size
    if not tokens:
        return tuple(vec)
    counts: dict[str, int] = {}
    for token in tokens:
        key = "w:" + token
        counts[key] = counts.get(key, 0) + 1
        padded = f"#{token}#"
        for i in range(len(padded) - 2):
            tri = "c:" + padded[i : i + 3]
            counts[tri] = counts.get(tri, 0) + 1
    for feature, count in counts.items():
        data = feature.encode("utf-8")
        index = zlib.crc32(data) % size
        sign = 1.0 if zlib.crc32(data, 0x5F3759DF) & 1 else -1.0
        weight = UNIGRAM_WEIGHT if feature[0] == "w" else TRIGRAM_WEIGHT
        vec[index] += sign * weight * (1.0 + math.log(count))
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return tuple(vec)
    return tuple(v / norm for v in vec)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosseno limitado a ``[0, 1]`` (anti-correlação conta 0). Dimensões ≠ → 0."""
    try:
        if not a or not b or len(a) != len(b):
            return 0.0
        na = math.sqrt(_dot(a, a))
        nb = math.sqrt(_dot(b, b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        value = _dot(a, b) / (na * nb)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if value != value:  # NaN
        return 0.0
    return min(1.0, max(0.0, value))


def pack_vector(vec: Iterable[float]) -> bytes:
    """Vetor → ``float32`` little-endian (valores não finitos viram 0)."""
    values: list[float] = []
    for item in vec:
        try:
            number = float(item)
        except (TypeError, ValueError):
            number = 0.0
        values.append(number if math.isfinite(number) and abs(number) < 3e38 else 0.0)
    return struct.pack(f"<{len(values)}f", *values)


def unpack_vector(blob: Any) -> tuple[float, ...]:
    """Inverso de :func:`pack_vector`; blob inválido → ``()``."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        return ()
    raw = bytes(blob)
    count, rest = divmod(len(raw), 4)
    if rest or count == 0 or count > MAX_DIM:
        return ()
    return struct.unpack(f"<{count}f", raw)


# ------------------------------------------------------------------ pontuação


def recency_score(age_hours: Any, decay: float = DEFAULT_DECAY) -> float:
    """``decay ** max(idade, 0)``; idade inválida → 0; ``decay`` inválido → 0.995."""
    if isinstance(age_hours, bool) or not isinstance(age_hours, (int, float)):
        return 0.0
    age = float(age_hours)
    if math.isnan(age):
        return 0.0
    if isinstance(decay, bool) or not isinstance(decay, (int, float)) or not 0.0 < decay <= 1.0:
        decay = DEFAULT_DECAY
    try:
        return float(decay) ** max(age, 0.0)
    except OverflowError:  # pragma: no cover - expoente enorme
        return 0.0


def minmax(values: Iterable[float]) -> list[float]:
    """Normalização min-max para ``[0, 1]``; constante → tudo 0.5; vazio → ``[]``."""
    items: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        items.append(number if math.isfinite(number) else 0.0)
    if not items:
        return []
    low, high = min(items), max(items)
    if high - low <= 1e-12:
        return [0.5] * len(items)
    span = high - low
    return [(v - low) / span for v in items]


@dataclass(frozen=True)
class ScoreWeights:
    """Pesos dos três componentes (≥ 0, não todos zero). Iguais por omissão."""

    recency: float = 1.0
    importance: float = 1.0
    relevance: float = 1.0

    def __post_init__(self) -> None:
        values = (self.recency, self.importance, self.relevance)
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"score weights must be numbers, got {value!r}")
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"score weights must be finite and >= 0, got {value!r}")
        if sum(values) <= 0:
            raise ValueError("score weights must not all be zero")

    @property
    def total(self) -> float:
        return float(self.recency + self.importance + self.relevance)

    @classmethod
    def from_any(cls, value: Any, default: "ScoreWeights | None" = None) -> "ScoreWeights":
        """Aceita ``ScoreWeights``, mapeamento ``{recency,importance,relevance}`` ou ``None``.

        Inválido → ``default`` (nunca levanta: é chamado dentro do loop).
        """
        fallback = default or cls()
        if value is None:
            return fallback
        if isinstance(value, cls):
            return value
        try:
            if isinstance(value, dict):
                return cls(
                    recency=value.get("recency", 1.0),
                    importance=value.get("importance", 1.0),
                    relevance=value.get("relevance", 1.0),
                )
        except ValueError:
            return fallback
        return fallback


@dataclass(frozen=True)
class Candidate(Generic[T]):
    """Entrada de :func:`rank`: o item + os três valores BRUTOS."""

    item: T
    age_hours: float
    importance: float
    relevance: float


@dataclass(frozen=True)
class Scored(Generic[T]):
    """Item classificado com os componentes expostos para auditoria.

    ``recency``/``importance``/``relevance`` são os normalizados (min-max sobre o
    conjunto); ``raw_*`` os valores originais. ``score`` ∈ ``[0, 1]``.
    """

    item: T
    score: float
    recency: float
    importance: float
    relevance: float
    raw_recency: float
    raw_importance: float
    raw_relevance: float

    def as_dict(self) -> dict[str, float]:
        return {
            "score": round(self.score, 6),
            "recency": round(self.recency, 6),
            "importance": round(self.importance, 6),
            "relevance": round(self.relevance, 6),
            "raw_recency": round(self.raw_recency, 6),
            "raw_importance": round(self.raw_importance, 6),
            "raw_relevance": round(self.raw_relevance, 6),
        }


def rank(
    candidates: Iterable[Candidate[T]],
    *,
    weights: ScoreWeights | dict[str, float] | None = None,
    decay: float = DEFAULT_DECAY,
    k: int = 5,
) -> list[Scored[T]]:
    """Top-``k`` por ``recência + importância + relevância`` (min-max, soma ponderada).

    Determinístico: empates resolvem-se por maior importância bruta, maior
    recência bruta e, por fim, ordem de entrada.
    """
    pool = list(candidates)
    try:
        limit = max(0, int(k))
    except (TypeError, ValueError):
        limit = 0
    if not pool or limit == 0:
        return []
    w = ScoreWeights.from_any(weights)
    raw_rec = [recency_score(c.age_hours, decay) for c in pool]
    raw_imp = [_finite(c.importance) for c in pool]
    raw_rel = [min(1.0, max(0.0, _finite(c.relevance))) for c in pool]
    rec, imp, rel = minmax(raw_rec), minmax(raw_imp), minmax(raw_rel)
    scores = [
        (w.recency * rec[i] + w.importance * imp[i] + w.relevance * rel[i]) / w.total
        for i in range(len(pool))
    ]
    order = sorted(range(len(pool)), key=lambda i: (-scores[i], -raw_imp[i], -raw_rec[i], i))
    return [
        Scored(
            item=pool[i].item,
            score=scores[i],
            recency=rec[i],
            importance=imp[i],
            relevance=rel[i],
            raw_recency=raw_rec[i],
            raw_importance=raw_imp[i],
            raw_relevance=raw_rel[i],
        )
        for i in order[:limit]
    ]


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


# --------------------------------------------------------------------- relógio


@dataclass(frozen=True)
class TimePoint:
    """"Agora" de uma recuperação: parede (epoch s) + horas de jogo (se conhecidas)."""

    wall: float
    game_hours: float | None = None


def is_real(value: Any) -> bool:
    """``int``/``float`` finito (``bool`` não conta)."""
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def game_hours(year: Any, tick: Any) -> float | None:
    """Horas de jogo absolutas (``ano * 8064 + tick / 50``); falta ano ou tick → ``None``."""
    if not is_real(year) or not is_real(tick):
        return None
    return float(year) * HOURS_PER_YEAR + float(tick) / TICKS_PER_HOUR


def clock_game_hours(game_clock: Any) -> float | None:
    """Horas de jogo de um ``GameClock`` (duck-typed: ``.year`` e ``.tick``)."""
    return game_hours(getattr(game_clock, "year", None), getattr(game_clock, "tick", None))


def state_game_hours(state: Any) -> float | None:
    """Horas de jogo de um ``GameState`` (``.metrics.game_clock``); ausente → ``None``."""
    metrics = getattr(state, "metrics", None)
    return clock_game_hours(getattr(metrics, "game_clock", None))


def coerce_now(value: Any, *, wall: float, game_hours_hint: float | None = None) -> TimePoint:
    """Normaliza o parâmetro ``now`` das recuperações.

    * ``None`` → parede atual + a última hora de jogo conhecida (``game_hours_hint``);
    * número → **horas de jogo**; ``datetime`` → relógio de parede;
    * ``GameClock``/``GameState`` (duck-typed) → as suas horas de jogo;
    * ``TimePoint`` → tal e qual; qualquer outra coisa → como ``None``.
    """
    if isinstance(value, TimePoint):
        return value
    if is_real(value):
        return TimePoint(wall=wall, game_hours=float(value))
    if isinstance(value, datetime):
        stamp = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return TimePoint(wall=stamp.timestamp(), game_hours=game_hours_hint)
    if value is not None:
        found = clock_game_hours(value)
        if found is None:
            found = state_game_hours(value)
        if found is not None:
            return TimePoint(wall=wall, game_hours=found)
    return TimePoint(wall=wall, game_hours=game_hours_hint)


def ages_hours(
    rows_game_hours: Sequence[float | None],
    rows_wall: Sequence[float],
    now: TimePoint,
) -> list[float]:
    """Idade (em horas) de cada linha, com UM relógio para o conjunto todo.

    Relógio de jogo se ``now`` o tem e alguma linha também; senão parede. Linhas
    sem hora de jogo, num conjunto em relógio de jogo, ficam com a maior idade.
    """
    now_game = now.game_hours
    if now_game is None or not any(g is not None for g in rows_game_hours):
        return [max(0.0, (now.wall - _finite(w)) / SECONDS_PER_HOUR) for w in rows_wall]
    ages: list[float | None] = [
        None if g is None else max(0.0, now_game - g) for g in rows_game_hours
    ]
    oldest = max((a for a in ages if a is not None), default=0.0)
    return [oldest if a is None else a for a in ages]


def iso_from_epoch(seconds: float) -> str:
    """Epoch (s) → ISO-8601 UTC com ``Z`` (convenção do contrato)."""
    try:
        return to_z(datetime.fromtimestamp(float(seconds), timezone.utc)) or ""
    except (OverflowError, OSError, ValueError):
        return to_z(datetime.fromtimestamp(0, timezone.utc)) or ""


# ------------------------------------------------- rabo do gamelog (idempotência)

_SESSION_MARKER_RE = re.compile(
    r"^\s*(?:\*{2,}.*\*{2,}|Generating world using parameter set\b.*"
    r"|(?:History |Name |Creature )?Seed:\s*\S+)\s*$",
    re.IGNORECASE,
)


def line_fingerprint(text: Any) -> str:
    """Impressão digital curta e estável de uma linha (``sha1`` do texto normalizado)."""
    base = " ".join(strip_controls(text).split()).casefold()
    return hashlib.sha1(base.encode("utf-8", "replace")).hexdigest()[:12]


def after_last_marker(lines: Sequence[str]) -> int:
    """Índice da 1.ª linha DEPOIS do último marcador de sessão do gamelog.

    Marcadores reais do 53.16: ``*** STARTING NEW GAME ***``,
    ``** Starting Adventure **``, ``Generating world using parameter set …`` e as
    linhas ``Seed:``. O que vem antes pertence a outro jogo/sessão e nunca é
    memória desta campanha. Sem marcador → ``0``.
    """
    for index in range(len(lines) - 1, -1, -1):
        if _SESSION_MARKER_RE.match(lines[index] or ""):
            return index + 1
    return 0


def new_tail_start(
    previous: Sequence[str],
    current: Sequence[str],
    *,
    min_overlap: int = MIN_TAIL_OVERLAP,
) -> int:
    """Onde começam as linhas NOVAS de ``current`` face ao rabo anterior.

    Dois snapshots seguidos repetem a maior parte do rabo do gamelog, e linhas
    legítimas repetem-se (``The stray mule stands up.``), por isso não se deduplica
    por texto: alinha-se o fim de ``previous`` com o início de ``current``
    (maior sobreposição, menor posição). ``len(current)`` = nada de novo;
    ``0`` = tudo novo (sem referência ou sem sobreposição suficiente). Aceita
    quaisquer sequências comparáveis (textos ou impressões digitais).
    """
    prev, cur = list(previous), list(current)
    if not prev or not cur:
        return 0
    need = max(1, min(int(min_overlap), len(prev)))
    for k in range(min(len(prev), len(cur)), need - 1, -1):
        tail = prev[-k:]
        first = tail[0]
        for i in range(0, len(cur) - k + 1):
            if cur[i] == first and cur[i : i + k] == tail:
                return i + k
    return 0


# ------------------------------------------------------------------- SQLite


def connect_private(path: Path | str, *, timeout: float = SQLITE_TIMEOUT_S) -> sqlite3.Connection:
    """Abre (criando) um SQLite privado: diretório 0700, ficheiro 0600 desde o início.

    O ficheiro é criado com 0600 *antes* de o SQLite lhe tocar (sem janela com o
    umask por omissão) e o modo é reforçado em cada abertura. O diário do SQLite
    herda o modo do ficheiro de base.
    """
    target = Path(path)
    ensure_private_dir(target.parent)
    if not target.exists():
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT, PRIVATE_FILE_MODE))
    conn = sqlite3.connect(target, timeout=timeout)
    conn.row_factory = sqlite3.Row
    os.chmod(target, PRIVATE_FILE_MODE)
    return conn


def meta_get(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    """Valor da tabela ``meta`` (``key``/``value`` TEXT), ou ``default``."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row is not None else default


def meta_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    """Grava na tabela ``meta`` (substitui)."""
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


# ------------------------------------------------------- API que nunca levanta


def guarded(default: Callable[[], Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador dos métodos públicos das memórias usadas dentro do loop.

    Uma falha (disco, SQLite, entrada inesperada) nunca sobe ao loop: regista-se,
    incrementa ``self.errors`` e devolve ``default()`` (``None``/``[]``/``""``).
    ``KeyboardInterrupt``/``SystemExit`` não são apanhados.
    """

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            try:
                return fn(self, *args, **kwargs)
            except Exception:
                self.errors = getattr(self, "errors", 0) + 1
                log.warning("%s.%s failed", type(self).__name__, fn.__name__, exc_info=True)
                return default()

        return wrapper

    return decorate
