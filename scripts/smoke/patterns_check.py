#!/usr/bin/env python3
"""Validação da biblioteca de padrões de navegação + alerta precoce de deriva de UI.

Porquê (ARQUITETURA §9 "Navegação", PROBLEMAS-E-SOLUCOES H7): os padrões de
``docs/padroes-navegacao.json`` (foco + marcadores de texto → ação) foram
provados num build concreto do DF 53.16. Um update do DF/DFHack que mude um
rótulo de menu ou um nome de foco quebra-os **em silêncio** (a classe de bug de
D18/D20: o personagem fica parado e nada rebenta). Este script é o alarme:

1. **Validação estrutural** (offline, determinística, corre em todo o PR): schema,
   ids únicos, cada padrão com ``focus`` ou ``text_markers`` (senão nunca casa) e
   com ``action`` ou ``outcome_markers``, regexes que compilam, proveniência
   (``proven``) e ``stats`` coerente com o conteúdo (``patterns`` e
   ``stuck_events_logged``, respeitando o teto do anel de 200 eventos).
2. **Deteção de deriva** (``--screen FICHEIRO`` ou ``--live``): cruza um ecrã real
   com a biblioteca e diz que padrões ainda casam. ``focus_only`` / ``text_only``
   são os suspeitos (o foco casa mas o texto já não, ou vice-versa). Com
   ``--expect ID[,ID…]`` a verificação passa a ser um **gate**: num ecrã conhecido
   (ex.: o título logo a seguir ao arranque) o padrão esperado TEM de casar.

Contrato:

* A semântica de correspondência é a de ``scripts/e2e/nav_jev.py::matches`` —
  marcadores são *regex* (``re.search``) sobre ``focus`` (marcadores de foco) e
  sobre ``focus + "\\n" + texto`` (marcadores de texto); sem marcadores nenhuns o
  padrão nunca casa. Um teste prova a paridade com o navegador real.
* **Só leitura**: nunca escreve na biblioteca (o ``StuckDetector`` do loop é o
  único escritor e acrescenta eventos sem atualizar ``stats``; ver ``stats``).
* Texto de jogo é DADO: é saneado (controlos removidos, tamanho limitado) e só
  alimenta ``re.search`` com regexes da biblioteca; nunca é executado nem eco.
* Códigos de saída: ``0`` ok · ``1`` biblioteca inválida ou ``--expect`` falhou ·
  ``2`` uso inválido · ``3`` ecrã indisponível (ficheiro ilegível / jogo em baixo).

Decisões: a regra de ``stuck_events`` aceita ``logged == len`` ou — com o anel
cheio (200) — ``logged >= 200`` (o anel descarta os mais antigos mas o contador é
histórico). Avisos (vocabulário desconhecido, estilo de id) só falham com
``--strict``.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

__all__ = [
    "LibraryReport",
    "PatternMatch",
    "Problem",
    "ScreenUnavailable",
    "analyze_screen",
    "check_library",
    "classify_pattern",
    "fetch_live_screen",
    "load_library",
    "main",
    "parse_screen_text",
    "sanitize_text",
    "screen_from_payload",
    "validate_library",
]

ROOT = Path(__file__).resolve().parents[2]
LIBRARY_PATH = ROOT / "docs" / "padroes-navegacao.json"
BRIDGE_PATH = ROOT / "dfhack" / "df_bridge.py"
DEFAULT_DF_PATH = "~/Games/dwarf-fortress-53.16"

# ------------------------------------------------------------------ limites
MAX_LIBRARY_BYTES = 5 * 1024 * 1024   # a biblioteca real tem ~100 KB
STUCK_EVENTS_CAP = 200                # espelha stuck.append_pattern_event(max_events=200)
MAX_SCREEN_CHARS = 200_000            # janela DF ~ 72x182 = 13 KB; folga ampla
MAX_SCREEN_LINES = 500
MAX_SCREEN_FILE_BYTES = 2 * 1024 * 1024  # um ecrã real tem ~13 KB
MAX_PROBLEMS_PER_CODE = 5             # o resto vira "(+N semelhantes)"
LIBRARY_READ_RETRIES = 2              # o escritor do StuckDetector não é atómico
LIBRARY_RETRY_SLEEP_S = 0.25
BRIDGE_TIMEOUT_S = 60.0               # o mesmo orçamento do session_live

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_NO_SCREEN = 0, 1, 2, 3

REQUIRED_TOP_LEVEL = ("version", "patterns", "stats", "stuck_events")
PATTERN_FIELDS = frozenset(
    {"id", "focus", "text_markers", "action", "outcome_markers", "proven", "notes"})
ID_STYLE = re.compile(r"^p-[a-z0-9]+(?:-[a-z0-9]+)*$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KEY_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")             # = df_bridge._KEY_SHAPE
ACTION_SPLIT = re.compile(r"\s*;\s*(?:depois\s+)?|\s+depois\s+")  # = nav_jev.split_actions
STUCK_KINDS = frozenset({"stuck", "action_loop", "damage", "death"})
STUCK_ESCALATIONS = frozenset({"force_explore", "replan", "break_loop", "retreat", "stop"})
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")  # fica \t (0x09) e \n (0x0a)

STATUS_MATCH, STATUS_FOCUS_ONLY, STATUS_TEXT_ONLY, STATUS_MISS = (
    "match", "focus_only", "text_only", "miss")


# ------------------------------------------------------------------- tipos
class Problem(NamedTuple):
    """Um achado da validação. ``code`` é estável (os testes e os alertas usam-no)."""

    level: str      # "error" | "warning"
    code: str
    where: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"level": self.level, "code": self.code, "where": self.where,
                "message": self.message}


class LibraryReport(NamedTuple):
    path: str
    problems: list[Problem]
    patterns: int
    stuck_events: int
    version: Any
    updated: Any
    data: Any   # biblioteca já parseada (None se ilegível) — não vai para o JSON

    @property
    def errors(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "error"]

    @property
    def warnings(self) -> list[Problem]:
        return [p for p in self.problems if p.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "version": self.version, "updated": self.updated,
                "patterns": self.patterns, "stuck_events": self.stuck_events,
                "errors": [p.as_dict() for p in self.errors],
                "warnings": [p.as_dict() for p in self.warnings]}


class PatternMatch(NamedTuple):
    """Como um padrão se comporta perante um ecrã (ver :func:`classify_pattern`)."""

    id: str
    status: str                 # match | focus_only | text_only | miss
    focus_ok: bool | None       # None = o padrão não tem marcadores de foco
    text_ok: bool | None        # None = o padrão não tem marcadores de texto
    found: list[str]            # marcadores que casaram
    missing: list[str]          # marcadores que NÃO casaram

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


class ScreenUnavailable(Exception):
    """Não foi possível obter/ler o ecrã (exit 3): fonte em baixo, não regressão."""


# ------------------------------------------------------------- utilitários
def _kind(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    return {dict: "object", list: "array", str: "string", int: "integer",
            float: "number"}.get(type(value), type(value).__name__)


def sanitize_text(text: Any) -> str:
    """Texto do jogo → DADO limpo: sem controlos, com tamanho e nº de linhas limitados."""
    if not isinstance(text, str):
        return ""
    text = _CONTROL_CHARS.sub("", text)[:MAX_SCREEN_CHARS]
    lines = text.split("\n")
    return "\n".join(lines[:MAX_SCREEN_LINES])


class _Collector:
    """Acumula problemas limitando o ruído: no máximo N por ``code`` + resumo."""

    def __init__(self) -> None:
        self.items: list[Problem] = []
        self._seen: dict[str, int] = {}

    def add(self, level: str, code: str, where: str, message: str) -> None:
        count = self._seen.get(code, 0) + 1
        self._seen[code] = count
        if count <= MAX_PROBLEMS_PER_CODE:
            self.items.append(Problem(level, code, where, message))

    def error(self, code: str, where: str, message: str) -> None:
        self.add("error", code, where, message)

    def warn(self, code: str, where: str, message: str) -> None:
        self.add("warning", code, where, message)

    def finish(self) -> list[Problem]:
        out = list(self.items)
        shown = {}
        for p in out:
            shown[p.code] = shown.get(p.code, 0) + 1
        for code, total in self._seen.items():
            extra = total - shown.get(code, 0)
            if extra > 0:
                level = next(p.level for p in out if p.code == code)
                out.append(Problem(level, code, "*", f"(+{extra} ocorrência(s) semelhantes)"))
        return out


# --------------------------------------------------------------- validação
def _marker_list(value: Any, where: str, field: str, bad: _Collector) -> list[str]:
    """Lista de marcadores válidos (str não vazias); o resto é reportado."""
    if value is None:
        return []
    if not isinstance(value, list):
        bad.error("bad_field_type", where, f"'{field}' tem de ser uma lista (veio {_kind(value)})")
        return []
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            bad.error("bad_field_type", f"{where}.{field}[{i}]",
                      f"marcador tem de ser texto (veio {_kind(item)})")
        elif not item.strip():
            bad.error("empty_marker", f"{where}.{field}[{i}]",
                      "marcador vazio casaria com qualquer ecrã")
        else:
            out.append(item)
    return out


def _validate_action(action: str, where: str, bad: _Collector) -> None:
    """Passos executáveis (``click_re``/``key``) têm de ser bem formados."""
    for part in ACTION_SPLIT.split(action):
        part = part.strip()
        if part.startswith("click_re "):
            try:
                re.compile(part[len("click_re "):].strip())
            except re.error as exc:
                bad.error("bad_action_regex", where, f"regex do click_re não compila: {exc}")
        elif part.startswith("key "):
            name = part[len("key "):].strip()
            if not KEY_NAME.match(name):
                bad.error("bad_key_name", where,
                          f"nome de tecla inválido {name!r} (esperado df.interface_key)")


def _validate_pattern(index: int, pattern: Any, seen: dict[str, int], bad: _Collector) -> None:
    where = f"patterns[{index}]"
    if not isinstance(pattern, dict):
        bad.error("pattern_not_object", where, f"padrão tem de ser objeto (veio {_kind(pattern)})")
        return
    pid = pattern.get("id")
    if not isinstance(pid, str) or not pid.strip():
        bad.error("id_missing", where, "padrão sem 'id' (texto não vazio)")
    else:
        where = f"patterns[{index}]({pid})"
        if pid in seen:
            bad.error("id_duplicate", where, f"id repetido (já em patterns[{seen[pid]}])")
        seen.setdefault(pid, index)
        if not ID_STYLE.match(pid):
            bad.warn("id_style", where, "id fora do estilo p-kebab-case")

    focus = _marker_list(pattern.get("focus"), where, "focus", bad)
    markers = _marker_list(pattern.get("text_markers"), where, "text_markers", bad)
    outcomes = _marker_list(pattern.get("outcome_markers"), where, "outcome_markers", bad)
    for field, values, level in (("focus", focus, "error"), ("text_markers", markers, "error"),
                                 ("outcome_markers", outcomes, "warning")):
        for marker in values:
            try:
                re.compile(marker)
            except re.error as exc:
                bad.add(level, "bad_regex", where, f"{field}: regex {marker!r} não compila: {exc}")

    if not focus and not markers:
        bad.error("no_focus_or_markers", where,
                  "sem 'focus' nem 'text_markers': nunca casaria com nenhum ecrã")

    action = pattern.get("action")
    if action is not None and not isinstance(action, str):
        bad.error("bad_field_type", where, f"'action' tem de ser texto (veio {_kind(action)})")
        action = None
    if isinstance(action, str) and action.strip():
        _validate_action(action, where, bad)
    if not (isinstance(action, str) and action.strip()) and not outcomes:
        bad.error("no_action_or_outcome", where, "sem 'action' nem 'outcome_markers'")

    proven = pattern.get("proven")
    if not isinstance(proven, str) or not proven.strip():
        bad.error("proven_missing", where, "padrão sem 'proven' (proveniência): não é COMPROVADO")
    notes = pattern.get("notes")
    if notes is not None and not isinstance(notes, str):
        bad.error("bad_field_type", where, f"'notes' tem de ser texto (veio {_kind(notes)})")
    for extra in sorted(set(pattern) - PATTERN_FIELDS):
        bad.warn("unknown_field", where, f"campo desconhecido {extra!r}")


def _validate_stuck_events(events: Any, stats: Any, bad: _Collector) -> None:
    if not isinstance(events, list):
        bad.error("bad_field_type", "stuck_events",
                  f"'stuck_events' tem de ser lista (veio {_kind(events)})")
        return
    count = len(events)
    if count > STUCK_EVENTS_CAP:
        bad.error("stuck_over_cap", "stuck_events",
                  f"{count} eventos > teto do anel ({STUCK_EVENTS_CAP}; "
                  "stuck.append_pattern_event corta aos últimos 200)")
    for i, event in enumerate(events):
        where = f"stuck_events[{i}]"
        if not isinstance(event, dict):
            bad.error("stuck_event_invalid", where, f"evento tem de ser objeto (veio {_kind(event)})")
            continue
        for field in ("kind", "escalation", "ts"):
            if not isinstance(event.get(field), str) or not event[field]:
                bad.error("stuck_event_invalid", where, f"evento sem '{field}' (texto)")
        kind, escalation = event.get("kind"), event.get("escalation")
        if isinstance(kind, str) and kind not in STUCK_KINDS:
            bad.warn("stuck_vocab", where, f"kind desconhecido {kind!r}")
        if isinstance(escalation, str) and escalation not in STUCK_ESCALATIONS:
            bad.warn("stuck_vocab", where, f"escalation desconhecida {escalation!r}")

    if not isinstance(stats, dict):
        return                              # já reportado como bad_field_type
    logged = stats.get("stuck_events_logged")
    if isinstance(logged, bool) or not isinstance(logged, int) or logged < 0:
        bad.error("stats_missing", "stats.stuck_events_logged",
                  f"'stats.stuck_events_logged' tem de ser inteiro >= 0 (veio {_kind(logged)})")
        return
    # Coerente: contador == armazenados, ou anel cheio (histórico >= teto).
    if not (logged == count or (count >= STUCK_EVENTS_CAP and logged >= STUCK_EVENTS_CAP)):
        bad.error("stats_stuck_mismatch", "stats.stuck_events_logged",
                  f"stats.stuck_events_logged={logged} não bate com {count} eventos em "
                  "stuck_events (atualiza o contador ao versionar a biblioteca)")


def validate_library(data: Any) -> list[Problem]:
    """Validação estrutural pura (sem I/O) → lista de :class:`Problem`."""
    bad = _Collector()
    if not isinstance(data, dict):
        bad.error("not_object", "$", f"a biblioteca tem de ser um objeto JSON (veio {_kind(data)})")
        return bad.finish()
    for key in REQUIRED_TOP_LEVEL:
        if key not in data:
            bad.error("missing_key", key, f"falta a chave de topo {key!r}")

    version = data.get("version")
    if "version" in data and (isinstance(version, bool) or not isinstance(version, int)
                              or version < 1):
        bad.error("bad_version", "version", f"'version' tem de ser inteiro >= 1 (veio {version!r})")
    updated = data.get("updated")
    if updated is not None and not (isinstance(updated, str) and ISO_DATE.match(updated)):
        bad.warn("bad_updated", "updated", f"'updated' deveria ser AAAA-MM-DD (veio {updated!r})")

    patterns = data.get("patterns")
    if "patterns" in data:
        if not isinstance(patterns, list) or not patterns:
            bad.error("no_patterns", "patterns", "'patterns' tem de ser uma lista não vazia")
        else:
            seen: dict[str, int] = {}
            for index, pattern in enumerate(patterns):
                _validate_pattern(index, pattern, seen, bad)

    stats = data.get("stats")
    if "stats" in data and not isinstance(stats, dict):
        bad.error("bad_field_type", "stats", f"'stats' tem de ser objeto (veio {_kind(stats)})")
    elif isinstance(stats, dict) and isinstance(patterns, list):
        claimed = stats.get("patterns")
        if claimed is None:
            bad.warn("stats_missing", "stats.patterns", "stats sem o contador 'patterns'")
        elif claimed != len(patterns):
            bad.error("stats_patterns_mismatch", "stats.patterns",
                      f"stats.patterns={claimed!r} mas a biblioteca tem {len(patterns)} padrões")
    if "stuck_events" in data:
        _validate_stuck_events(data.get("stuck_events"), stats, bad)
    return bad.finish()


# ---------------------------------------------------------- carga do ficheiro
def load_library(path: Path | str = LIBRARY_PATH) -> tuple[Any, list[Problem]]:
    """Lê e parseia a biblioteca; nunca levanta (erros viram :class:`Problem`)."""
    path = Path(path)
    where = str(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return None, [Problem("error", "file_missing", where, f"não consigo ler a biblioteca: {exc}")]
    if size > MAX_LIBRARY_BYTES:
        return None, [Problem("error", "file_too_large", where,
                              f"{size} bytes > limite de {MAX_LIBRARY_BYTES}")]
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return None, [Problem("error", "file_unreadable", where, f"leitura falhou: {exc}")]
    try:
        return json.loads(raw), []
    except ValueError as exc:
        return None, [Problem("error", "json_invalid", where, f"JSON inválido: {exc}")]


def check_library(path: Path | str = LIBRARY_PATH, *, retries: int = LIBRARY_READ_RETRIES,
                  sleep: Callable[[float], None] = time.sleep) -> LibraryReport:
    """Carrega + valida. ``json_invalid`` repete (escritor não atómico do StuckDetector)."""
    data, problems = load_library(path)
    attempts = 0
    while data is None and attempts < retries and problems and problems[0].code == "json_invalid":
        attempts += 1
        sleep(LIBRARY_RETRY_SLEEP_S)
        data, problems = load_library(path)
    if data is not None:
        problems = validate_library(data)
    patterns = data.get("patterns") if isinstance(data, dict) else None
    events = data.get("stuck_events") if isinstance(data, dict) else None
    return LibraryReport(
        path=str(path), problems=problems,
        patterns=len(patterns) if isinstance(patterns, list) else 0,
        stuck_events=len(events) if isinstance(events, list) else 0,
        version=data.get("version") if isinstance(data, dict) else None,
        updated=data.get("updated") if isinstance(data, dict) else None,
        data=data)


# ------------------------------------------------------- correspondência
def _search(marker: str, haystack: str) -> bool:
    try:
        return re.search(marker, haystack) is not None
    except re.error:                  # a validação já o reportou; aqui só não rebenta
        return False


def _markers_of(value: Any) -> list[str]:
    """Marcadores utilizáveis (lista de textos não vazios); o resto já foi reportado."""
    if not isinstance(value, list):
        return []
    return [m for m in value if isinstance(m, str) and m]


def classify_pattern(pattern: dict, focus: str, text: str) -> PatternMatch:
    """Um padrão contra um ecrã — mesma semântica de ``nav_jev.matches``.

    ``match`` = o navegador real ofereceria este padrão. ``focus_only`` /
    ``text_only`` são os suspeitos de deriva (metade do padrão ainda casa).
    """
    focus_markers = _markers_of(pattern.get("focus"))
    text_markers = _markers_of(pattern.get("text_markers"))
    hay_focus = focus or ""
    hay_text = hay_focus + "\n" + (text or "")
    focus_hits = [m for m in focus_markers if _search(m, hay_focus)]
    text_hits = [m for m in text_markers if _search(m, hay_text)]
    focus_ok = bool(focus_hits) if focus_markers else None
    text_ok = bool(text_hits) if text_markers else None
    applicable = (focus_ok is not False and text_ok is not False
                  and (focus_ok is not None or text_ok is not None))
    if applicable:
        status = STATUS_MATCH
    elif focus_ok is True and text_ok is False:
        status = STATUS_FOCUS_ONLY
    elif text_ok is True and focus_ok is False:
        status = STATUS_TEXT_ONLY
    else:
        status = STATUS_MISS
    missing = ([m for m in focus_markers if m not in focus_hits]
               + [m for m in text_markers if m not in text_hits])
    return PatternMatch(id=str(pattern.get("id", "?")), status=status, focus_ok=focus_ok,
                        text_ok=text_ok, found=focus_hits + text_hits, missing=missing)


def analyze_screen(library: Any, focus: str, text: str) -> list[PatternMatch]:
    """Todos os padrões da biblioteca contra o ecrã (ordem da biblioteca)."""
    patterns = library.get("patterns") if isinstance(library, dict) else None
    if not isinstance(patterns, list):
        return []
    return [classify_pattern(p, focus, text) for p in patterns if isinstance(p, dict)]


# ---------------------------------------------------------------- ecrã
def screen_from_payload(payload: Any) -> tuple[str, str]:
    """Payload JSON do ecrã → ``(focus, texto)`` saneados.

    Aceita ``act screen --json`` (``{"data": {"focus": [...], "lines": [...]}}``),
    o próprio ``{"focus": [...], "lines": [...]}`` do dfb-act e o snapshot
    (``{"focus": "a/b", "screen_text": "..."}``). Levanta ``ValueError`` sem texto.
    """
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict):
        raise ValueError(f"payload do ecrã inesperado ({_kind(payload)})")
    raw_focus = payload.get("focus")
    if isinstance(raw_focus, list):
        focus = "/".join(str(f) for f in raw_focus)
    else:
        focus = raw_focus if isinstance(raw_focus, str) else ""
    lines = payload.get("lines")
    if isinstance(lines, list):
        text = "\n".join("" if line is None else str(line) for line in lines)
    elif isinstance(payload.get("screen_text"), str):
        text = payload["screen_text"]
    elif isinstance(payload.get("text"), str):
        text = payload["text"]
    else:
        raise ValueError("o payload não tem texto do ecrã (lines/screen_text/text)")
    return sanitize_text(focus).replace("\n", " ").strip(), sanitize_text(text)


def parse_screen_text(raw: str, focus: str | None = None) -> tuple[str, str]:
    """Ficheiro de ecrã → ``(focus, texto)``: JSON do bridge ou texto simples.

    No texto simples, uma primeira linha ``focus: dungeonmode/Default`` define o
    foco; ``focus`` (``--focus``) tem precedência. JSON válido sem texto de ecrã
    levanta :class:`ScreenUnavailable` (nunca é analisado como se fosse o ecrã).
    """
    stripped = raw.lstrip()
    if stripped[:1] in ("{", "["):
        try:
            payload = json.loads(stripped)
        except ValueError:
            payload = None                    # não é JSON → trata como texto simples
        if payload is not None:
            try:
                got_focus, text = screen_from_payload(payload)
            except ValueError as exc:         # JSON válido mas sem ecrã: erro, não texto
                raise ScreenUnavailable(str(exc)) from None
            return (focus if focus is not None else got_focus), text
    lines = raw.splitlines()
    got_focus = ""
    if lines and lines[0].lower().startswith("focus:"):
        got_focus = lines[0].split(":", 1)[1].strip()
        lines = lines[1:]
    text = sanitize_text("\n".join(lines))
    return (focus if focus is not None else sanitize_text(got_focus).strip()), text


def fetch_live_screen(df_path: str | None = None, port: int | None = None, *,
                      runner: Callable[..., Any] = subprocess.run,
                      timeout: float = BRIDGE_TIMEOUT_S) -> tuple[str, str]:
    """``df_bridge.py act screen --json`` → ``(focus, texto)``; ``ScreenUnavailable`` se falhar."""
    cmd = [sys.executable, str(BRIDGE_PATH), "act", "screen", "--json"]
    if df_path:
        cmd += ["--df-path", str(Path(df_path).expanduser())]
    if port:
        cmd += ["--port", str(port)]
    try:
        proc = runner(cmd, capture_output=True, text=True, encoding="utf-8",
                      errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise ScreenUnavailable(f"a ponte não respondeu em {timeout:.0f}s") from None
    except OSError as exc:
        raise ScreenUnavailable(f"não consegui correr a ponte: {exc}") from None
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise ScreenUnavailable(
            f"a ponte saiu com {proc.returncode}: {sanitize_text(tail[-1] if tail else '')[:200]}")
    try:
        return screen_from_payload(json.loads(proc.stdout or ""))
    except ValueError as exc:
        raise ScreenUnavailable(f"resposta da ponte ilegível: {exc}") from None


# ----------------------------------------------------------------- CLI
def _summary(matches: Sequence[PatternMatch]) -> dict[str, int]:
    out = {STATUS_MATCH: 0, STATUS_FOCUS_ONLY: 0, STATUS_TEXT_ONLY: 0, STATUS_MISS: 0}
    for m in matches:
        out[m.status] += 1
    return out


def _render_human(report: dict[str, Any]) -> str:
    lib = report["library"]
    lines = [f"Biblioteca: {lib['path']} (v{lib['version']}, atualizada {lib['updated']})",
             f"  padrões: {lib['patterns']} · stuck_events: {lib['stuck_events']} · "
             f"erros: {len(lib['errors'])} · avisos: {len(lib['warnings'])}"]
    for problem in lib["errors"] + lib["warnings"]:
        lines.append(f"  [{problem['level']}] {problem['code']} @ {problem['where']}: "
                     f"{problem['message']}")
    screen = report.get("screen")
    if screen:
        summary = screen["summary"]
        lines.append(f"Ecrã ({screen['source']}): focus={screen['focus'] or '(vazio)'} · "
                     f"{screen['lines']} linhas")
        lines.append("  casam: {match} · suspeitos de deriva: {focus_only}+{text_only} · "
                     "sem relação: {miss}".format(**summary))
        for m in screen["matches"]:
            if m["status"] in (STATUS_MATCH, STATUS_FOCUS_ONLY, STATUS_TEXT_ONLY):
                extra = f" — faltam: {m['missing']}" if m["missing"] else ""
                lines.append(f"  [{m['status']}] {m['id']}{extra}")
        expect = screen.get("expect")
        if expect:
            if expect["missing"]:
                lines.append(f"  ESPERADOS EM FALTA: {', '.join(expect['missing'])}")
            else:
                lines.append(f"  esperados presentes: {', '.join(expect['wanted'])}")
    lines.append("OK" if report["ok"] else "FALHOU")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Valida docs/padroes-navegacao.json e (opcional) cruza um ecrã real com "
                    "os padrões — alerta precoce de deriva de UI do DF 53.16.")
    ap.add_argument("--library", default=str(LIBRARY_PATH), help="caminho da biblioteca (JSON)")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--screen", metavar="FICHEIRO",
                     help="ecrã gravado: JSON de `act screen --json`/snapshot ou texto simples")
    src.add_argument("--live", action="store_true",
                     help="lê o ecrã do jogo aberto (df_bridge.py act screen --json)")
    ap.add_argument("--focus", help="foco a usar (substitui o do ficheiro)")
    ap.add_argument("--expect", metavar="ID[,ID...]",
                    help="ids de padrões que TÊM de casar neste ecrã (gate de deriva)")
    ap.add_argument("--df-path", default=None, help=f"DF para --live (predef.: {DEFAULT_DF_PATH})")
    ap.add_argument("--port", type=int, default=None, help="porta RPC do DFHack para --live")
    ap.add_argument("--strict", action="store_true", help="avisos também falham")
    ap.add_argument("--json", action="store_true", help="relatório JSON no stdout")
    return ap


def main(argv: Sequence[str] | None = None, *,
         runner: Callable[..., Any] = subprocess.run,
         sleep: Callable[[float], None] = time.sleep) -> int:
    args = build_parser().parse_args(argv)
    wanted = [t.strip() for t in (args.expect or "").split(",") if t.strip()]
    has_screen = bool(args.screen or args.live)
    if wanted and not has_screen:
        print("erro: --expect precisa de --screen ou --live", file=sys.stderr)
        return EXIT_USAGE

    lib = check_library(args.library, sleep=sleep)
    report: dict[str, Any] = {"schema": 1, "tool": "patterns_check", "library": lib.as_dict()}
    ok = lib.ok and not (args.strict and lib.warnings)
    code = EXIT_OK if ok else EXIT_FAIL

    if (has_screen and isinstance(lib.data, dict)
            and isinstance(lib.data.get("patterns"), list)):
        known = {str(p.get("id")) for p in lib.data["patterns"] if isinstance(p, dict)}
        unknown = [w for w in wanted if w not in known]
        if unknown:
            print(f"erro: ids em --expect que não existem na biblioteca: {', '.join(unknown)}",
                  file=sys.stderr)
            return EXIT_USAGE
        try:
            if args.screen:
                try:
                    with open(args.screen, "rb") as handle:
                        blob = handle.read(MAX_SCREEN_FILE_BYTES + 1)
                except OSError as exc:
                    raise ScreenUnavailable(f"não consigo ler {args.screen}: {exc}") from None
                if len(blob) > MAX_SCREEN_FILE_BYTES:
                    raise ScreenUnavailable(
                        f"{args.screen} excede {MAX_SCREEN_FILE_BYTES} bytes (não é um ecrã)")
                raw = blob.decode("utf-8", errors="replace")
                focus, text = parse_screen_text(raw, args.focus)
                source = "file"
            else:
                focus, text = fetch_live_screen(args.df_path or DEFAULT_DF_PATH, args.port,
                                                runner=runner)
                if args.focus is not None:
                    focus = args.focus
                source = "live"
        except ScreenUnavailable as exc:
            report.update(ok=False, exit_code=EXIT_NO_SCREEN, error=str(exc))
            _emit(report, args.json, f"ecrã indisponível: {exc}")
            return EXIT_NO_SCREEN
        matches = analyze_screen(lib.data, focus, text)
        by_id = {m.id: m for m in matches}
        absent = [w for w in wanted if by_id[w].status != STATUS_MATCH]
        report["screen"] = {
            "source": source, "focus": focus, "lines": len(text.split("\n")) if text else 0,
            "summary": _summary(matches), "matches": [m.as_dict() for m in matches],
            "expect": {"wanted": wanted, "missing": absent} if wanted else None}
        if absent:
            ok, code = False, EXIT_FAIL

    report.update(ok=ok, exit_code=code)
    _emit(report, args.json, _render_human(report))
    return code


def _emit(report: dict[str, Any], as_json: bool, human: str) -> None:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(human)


if __name__ == "__main__":
    sys.exit(main())
