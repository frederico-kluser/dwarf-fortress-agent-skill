#!/usr/bin/env python3
"""Smoke do System One Dwarf: o fluxo real contra um DFHack FALSO (offline) ou contra o jogo (live).

Porquê (ARQUITETURA §9 "Processo", PROBLEMAS-E-SOLUCOES H4/H7): o E2E ao vivo foi
sempre manual e as regressões de DF/DFHack/ponte só se descobriam a jogar. Este
script automatiza a prova mínima: **a ponte fala com o jogo, o snapshot cumpre o
contrato, a biblioteca de padrões lê-se, e uma movida com prova (posição antes→
depois) funciona**.

Dois modos (``--offline`` é o predefinido):

* ``--offline`` — corre o fluxo REAL (``dfhack/df_bridge.py`` como subprocesso, o
  mesmo caminho do executor) contra um servidor RPC falso em loopback + um jogo
  simulado. Determinístico, sem jogo, sem chaves, ~2 s: corre em todo o PR. Inclui
  **controlos negativos** (parede em todas as direções, overlay a engolir o input,
  sem aventureiro) para provar que a lógica da prova de movimento PODE falhar.
* ``--live`` — ``df up --no-web`` → pronto (RPC + ``/api/health``) → aventureiro
  (já presente, ``--adventure-id`` carrega um save, ``--new-adventure`` cria mundo
  + personagem pela API) → snapshot → padrões → fecho de overlays → movida com
  prova → ``df stop``. Só para um runner com o jogo (ver ``docs/ci.md``). Nunca
  pára o que não arrancou; nunca envia chaves (variáveis com KEY/TOKEN/SECRET são
  retiradas do ambiente filho).

Contrato:

* Saída: relatório JSON (``--report FICHEIRO`` e/ou ``--json`` no stdout) com
  ``schema``, ``mode``, ``ok``, ``exit_code``, ``checks[]`` (``name``, ``status``,
  ``detail``, ``duration_ms``, ``data``) e ``summary``. Texto vindo do jogo/processos
  passa por :func:`redact` (segredos, controlos, tamanho).
* Códigos de saída: ``0`` ok · ``1`` regressão (alguma verificação falhou) · ``2``
  uso inválido · ``3`` pré-condição em falta (DF não instalado, sem aventureiro,
  não é Linux) · ``4`` tempo esgotado · ``130`` interrompido (o jogo é parado).
* Todos os números ajustáveis são constantes nomeadas deste módulo; relógio, sleep,
  HTTP, subprocessos e ponte são injetáveis (os testes não usam rede nem o jogo).

Decisões: sem ``dataclass`` (os testes carregam os scripts por caminho e o
``@dataclass`` com ``from __future__ import annotations`` rebenta fora de
``sys.modules``); o servidor falso vive aqui (autocontido, o ``test_bridge.py`` pode
mudar sem partir o smoke).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, NamedTuple, Sequence

__all__ = [
    "BridgeClient", "BridgeReply", "CheckResult", "FakeGame", "FakeRpcServer", "LiveSession",
    "MoveProof", "Precondition", "Smoke", "build_report", "classify_snapshot_failure",
    "default_http", "main", "movement_plan", "overlay_action", "prove_movement", "redact",
    "run_live", "run_offline", "scrubbed_env", "settle_to_map", "snapshot_position",
    "validate_snapshot",
]

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "dfhack" / "df_bridge.py"
DF_SH_PATH = ROOT / "scripts" / "df.sh"
LIBRARY_PATH = ROOT / "docs" / "padroes-navegacao.json"
PATTERNS_CHECK_PATH = Path(__file__).resolve().with_name("patterns_check.py")
SRC_DIR = ROOT / "src"

# ------------------------------------------------------------ constantes
REPORT_SCHEMA = 1
SNAPSHOT_SCHEMA_VERSION = 1
DEFAULT_DF_PATH = "~/Games/dwarf-fortress-53.16"
DEFAULT_BACKEND_PORT = 8000
SMOKE_CAMPAIGN = "smoke-df"

OFFLINE_TIMEOUT_S = 120.0        # orçamento global do modo offline
LIVE_TIMEOUT_S = 3600.0          # orçamento global do live (cobre worldgen)
BRIDGE_TIMEOUT_S = 60.0          # um comando da ponte (= session_live.BRIDGE_TIMEOUT)
BATCH_TIMEOUT_S = 180.0          # `act batch` (= nav_jev)
STARTUP_TIMEOUT_S = 240.0        # `df up` → RPC + backend prontos
STOP_TIMEOUT_S = 60.0            # `df stop` + saída do supervisor
POLL_INTERVAL_S = 2.0
HTTP_TIMEOUT_S = 10.0
MAX_HTTP_BYTES = 1_000_000
ADVENTURE_TIMEOUT_S = 1800.0     # worldgen "quick" (= mission_hunt: 1800 s)
ADVENTURE_POLL_S = 6.0
MAX_DETAIL_CHARS = 400           # tamanho máximo de um `detail` no relatório
LOG_TAIL_LINES = 12

MOVE_DELAY_S = 0.4               # `act batch --delay` é em SEGUNDOS
MOVE_MAX_STEPS = 8
MOVE_SETTLE_S = 1.0              # pausa antes de reler a posição
MOVE_PROBE_TILES = 4             # goto x, y-4 (= mission_hunt)
MOVE_PROBE_TIMES = 3             # move <dir> times=3
SETTLE_MAX_STEPS = 12            # tentativas de fechar overlays
HELP_BUTTON_XY = (180, 1)        # botão Help, topo direito (overlays.tutorial_help)
OVERLAY_CLICK_SLEEP_MS = 600     # `sleep N` do DSL é em MILISSEGUNDOS (= nav_jev)
OVERLAY_KEY_SLEEP_MS = 500
DEFAULT_EXPECT_PATTERNS = ("p-move-or-goto",)   # no mapa, este padrão tem de casar
SECRET_ENV_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_PRECONDITION, EXIT_TIMEOUT, EXIT_INTERRUPTED = (
    0, 1, 2, 3, 4, 130)
PASS, FAIL, SKIP, PRECONDITION, TIMEOUT = "pass", "fail", "skip", "precondition", "timeout"
SCENARIOS = ("normal", "blocked", "frozen", "no-adventurer")

DSL_PROBE = ("goto 1,1; move n times=2; key LEAVESCREEN; click 3,4; wait short; "
             "read screen; expect focus dungeonmode; sleep 10")
DSL_PROBE_STEPS = 8
ISO_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
MAP_FOCUS = re.compile(r"^dungeonmode(?:/Default)?$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_SECRETS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password)\b(\s*[:=]\s*)\S+"),
)


# ------------------------------------------------------------------ tipos
class BridgeReply(NamedTuple):
    code: int
    stdout: str
    stderr: str
    timed_out: bool = False


class CheckResult(NamedTuple):
    name: str
    status: str
    detail: str
    duration_ms: int
    data: dict

    def as_dict(self) -> dict[str, Any]:
        return self._asdict()


class MoveAttempt(NamedTuple):
    dsl: str
    exit_code: int
    reason: str | None
    pos_after: tuple | None
    moved: bool


class MoveProof(NamedTuple):
    moved: bool
    before: tuple | None
    after: tuple | None
    delta: tuple | None
    attempts: list
    diagnosis: str | None
    focus: str | None


class Precondition(Exception):
    """Falta algo do ambiente (não é regressão): sem aventureiro, DF em falta, …"""


# ------------------------------------------------------------- utilitários
def redact(text: Any, limit: int = MAX_DETAIL_CHARS) -> str:
    """Texto externo → seguro para relatório: sem segredos, sem controlos, limitado."""
    out = _CONTROL.sub("", str(text))
    out = _SECRETS[0].sub("<redacted>", out)
    out = _SECRETS[1].sub("<redacted>", out)
    out = _SECRETS[2].sub("bearer <redacted>", out)
    out = _SECRETS[3].sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", out)
    out = out.strip()
    return out if len(out) <= limit else out[:limit] + "…"


def scrubbed_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Ambiente para processos filhos SEM nada que pareça uma credencial."""
    env = dict(os.environ if environ is None else environ)
    return {k: v for k, v in env.items()
            if not any(hint in k.upper() for hint in SECRET_ENV_HINTS)}


def load_module(name: str, path: Path) -> Any:
    """Carrega um módulo irmão por caminho (registado em ``sys.modules``)."""
    cached = sys.modules.get(name)
    if cached is not None and getattr(cached, "__file__", None) == str(path):
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"não consigo carregar {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def extract_json(text: str) -> Any:
    """Primeiro objeto/lista JSON do texto (a ponte pode vir com ruído à volta)."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text or ""):
        if ch in "{[":
            try:
                return decoder.raw_decode(text[i:])[0]
            except ValueError:
                continue
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def display_path(path: Any) -> str:
    """Caminho para relatórios: relativo ao repo ou com ``~`` (sem a home do utilizador)."""
    p = Path(str(path)).expanduser()
    for base, prefix in ((ROOT, ""), (Path.home(), "~/")):
        try:
            return prefix + str(p.resolve().relative_to(base.resolve()))
        except (ValueError, OSError):
            continue
    return str(p)


# --------------------------------------------------- contrato do snapshot
def snapshot_position(snap: Any) -> tuple | None:
    """``(x, y, z)`` do snapshot (``metrics.location``, senão ``adventurer.pos``)."""
    if not isinstance(snap, dict):
        return None
    metrics = snap.get("metrics") if isinstance(snap.get("metrics"), dict) else {}
    adventurer = snap.get("adventurer") if isinstance(snap.get("adventurer"), dict) else {}
    for source in (metrics.get("location"), adventurer.get("pos")):
        if not isinstance(source, dict):
            continue
        x, y, z = source.get("x"), source.get("y"), source.get("z")
        if all(isinstance(v, int) and not isinstance(v, bool) for v in (x, y)):
            return (x, y, z if isinstance(z, int) and not isinstance(z, bool) else None)
    return None


def validate_snapshot(snap: Any, empty_metrics: dict) -> list[str]:
    """Problemas do snapshot face ao contrato do ``df_bridge.py`` (vazio = válido).

    ``empty_metrics`` é o ``EMPTY_METRICS`` da ponte (fonte de verdade dos 8
    grupos e dos campos "nulls, nunca ausentes").
    """
    if not isinstance(snap, dict):
        return ["o snapshot não é um objeto JSON"]
    out: list[str] = []
    if snap.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        out.append(f"schema_version={snap.get('schema_version')!r} (esperado "
                   f"{SNAPSHOT_SCHEMA_VERSION})")
    if not (isinstance(snap.get("ts"), str) and ISO_TS.match(snap["ts"])):
        out.append("ts não é ISO-8601 UTC com sufixo Z")
    if not (isinstance(snap.get("mode"), str) and snap["mode"]):
        out.append("mode não é texto")
    adventurer = snap.get("adventurer")
    if not isinstance(adventurer, dict):
        out.append("adventurer ausente (sem aventureiro?)")
    else:
        pos = adventurer.get("pos")
        if not (isinstance(pos, dict) and all(
                isinstance(pos.get(k), int) and not isinstance(pos.get(k), bool) for k in "xyz")):
            out.append("adventurer.pos sem x/y/z inteiros")
    metrics = snap.get("metrics")
    if not isinstance(metrics, dict):
        out.append("metrics não é objeto")
    else:
        for group, shape in empty_metrics.items():
            value = metrics.get(group)
            if group == "events":
                if not isinstance(value, list):
                    out.append("metrics.events não é lista")
            elif not isinstance(value, dict):
                out.append(f"metrics.{group} não é objeto")
            else:
                missing = [f for f in shape if f not in value]
                if missing:
                    out.append(f"metrics.{group} sem campos {missing}")
                out += [f"metrics.{group}.{f} não é lista" for f, d in shape.items()
                        if isinstance(d, list) and f in value and not isinstance(value[f], list)]
    if not isinstance(snap.get("screen_text"), str):
        out.append("screen_text não é texto")
    if snap.get("focus") is not None and not isinstance(snap.get("focus"), str):
        out.append("focus não é texto nem null")
    for key in ("gamelog_events", "warnings"):
        if not isinstance(snap.get(key), list):
            out.append(f"{key} não é lista")
    location = metrics.get("location") if isinstance(metrics, dict) else None
    if isinstance(adventurer, dict) and isinstance(location, dict):
        pos = adventurer.get("pos")
        if isinstance(pos, dict):
            for k in "xyz":
                a, b = pos.get(k), location.get(k)
                if isinstance(a, int) and isinstance(b, int) and a != b:
                    out.append(f"posição incoerente: adventurer.pos.{k}={a} vs location.{k}={b}")
    return out


def classify_snapshot_failure(reply: BridgeReply) -> str:
    """Porque é que o snapshot falhou: ``no_adventurer`` | ``game_down`` | ``timeout`` | ``error``."""
    if reply.timed_out:
        return "timeout"
    if reply.code in (10, 12):
        return "game_down"
    blob = f"{reply.stdout}\n{reply.stderr}".lower()
    if reply.code == 13 and "sem aventureiro" in blob:
        return "no_adventurer"
    return "error"


# ------------------------------------------------------- prova de movimento
def movement_plan(before: tuple, tiles: int = MOVE_PROBE_TILES,
                  times: int = MOVE_PROBE_TIMES) -> list[str]:
    """Candidatos por ordem: ``goto`` para norte, depois sondar as 4 direções (paredes)."""
    x, y = before[0], before[1]
    return [f"goto {x},{y - tiles}"] + [f"move {d} times={times}" for d in ("n", "s", "e", "w")]


def _diagnose(moved: bool, focus: str | None) -> str | None:
    if moved:
        return None
    f = (focus or "").lower()
    if "help" in f:
        return "input_swallowed"       # D18: dungeonmode/Help engole TODO o simulateInput
    if f and not MAP_FOCUS.match(focus or ""):
        return "modal_open"
    return "blocked_or_frozen"         # paredes em todas as direções OU tempo congelado


def prove_movement(read_position: Callable[[], tuple | None],
                   run_batch: Callable[[str], tuple[int, str | None]],
                   read_focus: Callable[[], str | None] | None = None, *,
                   plan: Callable[[tuple], Sequence[str]] = movement_plan,
                   settle: Callable[[], None] = lambda: None) -> MoveProof:
    """Prova de movimento: a posição (x, y) TEM de mudar depois de uma movida real.

    ``read_position`` → ``(x, y, z)`` ou ``None``; ``run_batch(dsl)`` → ``(exit, motivo)``.
    Pára à primeira movida com delta; sem delta em todos os candidatos devolve
    ``moved=False`` + diagnóstico (o código de saída da ponte sozinho não prova nada).
    """
    before = read_position()
    if before is None:
        return MoveProof(False, None, None, None, [], "no_position", None)
    attempts: list[MoveAttempt] = []
    after = before
    for dsl in plan(before):
        code, reason = run_batch(dsl)
        settle()
        after = read_position()
        moved = after is not None and (after[0], after[1]) != (before[0], before[1])
        attempts.append(MoveAttempt(dsl, code, reason, after, moved))
        if moved:
            delta = (after[0] - before[0], after[1] - before[1])
            return MoveProof(True, before, after, delta, attempts, None, None)
    focus = read_focus() if read_focus is not None else None
    return MoveProof(False, before, after, None, attempts, _diagnose(False, focus), focus)


# ------------------------------------------------------ overlays (live)
def overlay_action(focus: str, text: str, *, help_clicked: bool = False) -> tuple[str, str]:
    """Próxima ação para fechar um overlay: ``(descrição, DSL da ponte)``.

    Segue ``overlays``/``p-tutorial-*``/``p-help-buttons-close`` da biblioteca:
    clicar Next/Okay; páginas interativas → botão Help (1×); depois LEAVESCREEN.
    """
    lines = (text or "").split("\n")
    for label in ("Next", "Okay"):
        rx = re.compile(r"\b" + re.escape(label) + r"\b")
        hits = [(y, m) for y, line in enumerate(lines) if (m := rx.search(line))]
        if hits:                       # o ÚLTIMO: os botões ficam no fundo do painel
            y, m = hits[-1]
            return f"click {label}", (f"click {m.start() + len(label) // 2},{y}; "
                                      f"sleep {OVERLAY_CLICK_SLEEP_MS}")
    if "help" in (focus or "").lower() and not help_clicked:
        return "click help button", (f"click {HELP_BUTTON_XY[0]},{HELP_BUTTON_XY[1]}; "
                                     f"sleep {OVERLAY_CLICK_SLEEP_MS}")
    return "key LEAVESCREEN", f"key LEAVESCREEN; sleep {OVERLAY_KEY_SLEEP_MS}"


def settle_to_map(read_screen: Callable[[], tuple[str, str]], act: Callable[[str], int], *,
                  max_steps: int = SETTLE_MAX_STEPS) -> tuple[bool, list[str], str]:
    """Fecha overlays até ``focus=dungeonmode/Default`` → ``(assente, trilho, foco)``."""
    trail: list[str] = []
    help_clicked = False
    focus = ""
    for _ in range(max_steps):
        focus, text = read_screen()
        if MAP_FOCUS.match(focus or ""):
            return True, trail, focus
        what, dsl = overlay_action(focus, text, help_clicked=help_clicked)
        help_clicked = help_clicked or what == "click help button"
        code = act(dsl)
        trail.append(f"{what} (exit {code})")
    focus, _ = read_screen()
    return bool(MAP_FOCUS.match(focus or "")), trail, focus


# --------------------------------------------------------- DFHack FALSO
_DIRS = {"n": (0, -1), "s": (0, 1), "e": (1, 0), "w": (-1, 0),
         "ne": (1, -1), "nw": (-1, -1), "se": (1, 1), "sw": (-1, 1)}
_NO_ADVENTURER = "sem aventureiro (o jogo esta em adventure mode?)"
_SCREENS = {
    "map": (["dungeonmode", "Default"], ["You are in a temperate forest.", "Nothing here."]),
    "help": (["dungeonmode", "Help"], ["Movement (2 of 3)", "Okay", "Next"]),
    "title": (["new_region"], ["Dwarf Fortress", "Create new world", "Settings"]),
}


class FakeGame:
    """Jogo simulado com a forma das respostas dos ``dfb-*.lua``.

    A movida só se aplica no pedido SEGUINTE (o jogo real processa o passo no
    próximo frame: ``pos_before`` é a anterior). Cenários: ``normal``;
    ``blocked`` (parede em todas as direções); ``frozen`` (``dungeonmode/Help``
    engole o input mas reporta ``ok:true`` — a causa-raiz D18); ``no-adventurer``
    (título; os scripts fazem ``qerror``). Em ``frozen``, ``close_after_clicks=N``
    fecha o overlay à N-ésima clicada num botão (Okay/Next) — como o tutorial real.
    """

    def __init__(self, scenario: str = "normal", pos: Sequence[int] = (50, 50, 5), *,
                 close_after_clicks: int | None = None) -> None:
        self.pos = list(pos)
        self.pending: list[str] = []
        self.scenario = "normal"
        self.close_after_clicks = close_after_clicks
        self.configure(scenario)

    def configure(self, scenario: str, pos: Sequence[int] | None = None) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"cenário desconhecido: {scenario!r} (válidos: {SCENARIOS})")
        self.scenario = scenario
        self.pending.clear()
        if pos is not None:
            self.pos = list(pos)

    @property
    def screen(self) -> tuple[list[str], list[str]]:
        key = {"frozen": "help", "no-adventurer": "title"}.get(self.scenario, "map")
        return _SCREENS[key]

    def _on_button(self, x: int, y: int) -> bool:
        lines = self.screen[1]
        if not 0 <= y < len(lines):
            return False
        for label in ("Okay", "Next"):
            i = lines[y].find(label)
            if i >= 0 and i <= x < i + len(label):
                return True
        return False

    def _tick(self) -> None:
        for direction in self.pending:
            if self.scenario not in ("blocked", "frozen"):
                dx, dy = _DIRS.get(direction, (0, 0))
                self.pos[0] += dx
                self.pos[1] += dy
        self.pending.clear()

    def _pos(self) -> dict[str, int]:
        return {"x": self.pos[0], "y": self.pos[1], "z": self.pos[2]}

    def _route(self, tx: int, ty: int) -> list[str]:
        x, y, steps = self.pos[0], self.pos[1], []
        while (x, y) != (tx, ty) and len(steps) < 200:
            dx = (tx > x) - (tx < x)
            dy = (ty > y) - (ty < y)
            steps.append(next(d for d, v in _DIRS.items() if v == (dx, dy)))
            x, y = x + dx, y + dy
        return steps

    def _snapshot_state(self) -> dict[str, Any]:
        p = self._pos()
        return {
            "mode": "adventure",
            "adventurer": {"name": "Smoke Tester", "race": "Human", "profession": "Adventurer",
                           "pos": p, "kills": 0, "stress_category": 3},
            "metrics": {
                "health_injuries": {"blood_current": 5000, "blood_max": 5000, "wound_count": 0,
                                    "wounds": [], "pain": 0, "status": "healthy",
                                    "status_conditions": []},
                "needs_state": {"hunger": "ok", "thirst": "ok", "sleep": "ok",
                                "hunger_timer": 1000, "thirst_timer": 1000,
                                "sleepiness_timer": 1000, "stress_level": 3},
                "inventory": {"items": [], "item_count": 0, "total_weight_kg": 0.0,
                              "equipped_weapon": None, "equipped_armor": []},
                "threats": {"count": 0, "level": "none", "hostiles": []},
                "location": {**p, "region": "forest", "surroundings": "outdoors, forest",
                             "tile": "Grass"},
                "game_clock": {"date_text": "Granite 1, year 100", "year": 100,
                               "month": "Granite", "day": 1, "season": "spring",
                               "tick": 0, "hour": 0},
                "units_nearby": {"count": 0, "units": []},
                "events": [],
            },
        }

    def respond(self, command: str, args: Sequence[str]) -> tuple[str, int]:
        """Resposta ``(texto, command_result)`` a um RunCommand (0 = CR_OK)."""
        self._tick()
        focus, lines = self.screen
        dump = json.dumps
        sub = args[0] if args else ""
        if command == "dfb-act" and sub == "focus":
            return dump({"focus": focus}), 0
        if command == "dfb-act" and sub == "screen":
            return dump({"focus": focus, "lines": lines}), 0
        if self.scenario == "no-adventurer" and command in ("dfb-state", "dfb-nav"):
            return _NO_ADVENTURER, 1
        if command == "dfb-act" and sub == "key":
            return dump({"ok": True, "pressed": list(args[1:]), "focus": focus}), 0
        if command == "dfb-act" and sub == "click":
            try:
                x, y = int(args[1]), int(args[2])
            except (IndexError, ValueError):
                return "uso: dfb-act click <x> <y> [left|right]", 1
            if (self.scenario == "frozen" and self.close_after_clicks is not None
                    and self._on_button(x, y)):
                self.close_after_clicks -= 1
                if self.close_after_clicks <= 0:
                    self.configure("normal")
            return dump({"ok": True, "clicked": {"x": x, "y": y}, "focus": focus}), 0
        if command == "dfb-act" and sub == "move":
            if self.scenario == "no-adventurer":
                return _NO_ADVENTURER, 1
            direction = (args[1] if len(args) > 1 else "").lower()
            if direction not in _DIRS:
                return "uso: dfb-act move n|s|e|w|ne|nw|se|sw|up|down|wait", 1
            self.pending.append(direction)
            return dump({"ok": True, "dir": direction, "pos_before": self._pos(),
                         "focus": focus}), 0
        if command == "dfb-state" and sub == "adventurer":
            return dump({"name": "Smoke Tester", "pos": self._pos()}), 0
        if command == "dfb-state" and sub == "threats":
            return dump({"radius": 10, "threats": []}), 0
        if command == "dfb-state" and sub == "snapshot":
            return dump(self._snapshot_state()), 0
        if command == "dfb-nav" and sub == "route" and len(args) >= 4:
            try:
                steps = self._route(int(args[1]), int(args[2]))
            except ValueError:
                return "coordenadas inválidas", 1
            return dump({"ok": True, "len": len(steps), "steps": steps}), 0
        return "", 3                                      # CR_NOT_FOUND


class FakeRpcServer(threading.Thread):
    """Servidor do protocolo remoto do DFHack em loopback (uma ligação de cada vez)."""

    def __init__(self, responder: Callable[[str, Sequence[str]], tuple[str, int]],
                 bridge: Any, port: int = 0) -> None:
        super().__init__(daemon=True, name="df-smoke-fake-dfhack")
        self.responder, self.B = responder, bridge
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))      # 0 = porta efémera do SO
        self.srv.listen(8)
        self.srv.settimeout(0.2)
        self.port = self.srv.getsockname()[1]
        self.requests: list[tuple[str, list[str]]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(5.0)
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                try:
                    self._serve(conn)
                except Exception:  # noqa: BLE001 - o servidor falso nunca morre a meio do smoke
                    pass

    @staticmethod
    def _recv(conn: socket.socket, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise OSError("eof")
            buf += chunk
        return buf

    def _serve(self, conn: socket.socket) -> None:
        B = self.B
        if self._recv(conn, 12)[:8] != B.REQUEST_MAGIC:
            return
        conn.sendall(B.RESPONSE_MAGIC + struct.pack("<i", B.PROTOCOL_VERSION))
        while True:
            mid, size = B.HEADER.unpack(self._recv(conn, 8))
            if mid == B.RPC_REQUEST_QUIT:
                return
            payload = self._recv(conn, size) if size else b""
            command, args = "", []
            for field, wire, value in B._pb_fields(payload):
                if field == 1 and wire == 2:
                    command = value.decode("utf-8")
                elif field == 2 and wire == 2:
                    args.append(value.decode("utf-8"))
            self.requests.append((command, args))
            text, code = self.responder(command, args)
            frames = b""
            if text:
                inner = B._pb_str(1, text)
                body = bytes([(1 << 3) | 2]) + B._pb_varint(len(inner)) + inner
                frames += B.HEADER.pack(B.RPC_REPLY_TEXT, len(body)) + body
            frames += B.HEADER.pack(B.RPC_REPLY_RESULT if code == 0 else B.RPC_REPLY_FAIL,
                                    0 if code == 0 else code)
            conn.sendall(frames)        # UM só envio: dois pequenos + Nagle = +40 ms por pedido

    def close(self) -> None:
        self._halt.set()
        try:
            self.srv.close()
        except OSError:
            pass
        if self.is_alive():
            self.join(timeout=2.0)


# --------------------------------------------------------------- ponte
class BridgeClient:
    """Corre ``df_bridge.py`` como subprocesso — o caminho que a app e o executor usam."""

    def __init__(self, *, df_path: str, port: int | None = None, via: str | None = None,
                 env: dict[str, str] | None = None, python: str | None = None,
                 bridge: Path = BRIDGE_PATH, timeout: float = BRIDGE_TIMEOUT_S,
                 runner: Callable[..., Any] = subprocess.run) -> None:
        self.df_path, self.port, self.via = str(df_path), port, via
        self.env = env if env is not None else scrubbed_env()
        self.python, self.bridge = python or sys.executable, Path(bridge)
        self.timeout, self.runner = timeout, runner

    def call(self, *argv: str, timeout: float | None = None) -> BridgeReply:
        cmd = [self.python, str(self.bridge), *argv, "--df-path", self.df_path]
        if self.port:
            cmd += ["--port", str(self.port)]
        if self.via:
            cmd += ["--via", self.via]
        limit = timeout if timeout is not None else self.timeout
        try:
            proc = self.runner(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=limit, env=self.env,
                               stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return BridgeReply(-1, "", f"a ponte não respondeu em {limit:.0f}s", True)
        except OSError as exc:
            return BridgeReply(127, "", f"não consegui correr a ponte: {exc}")
        return BridgeReply(proc.returncode, proc.stdout or "", proc.stderr or "")

    def json(self, *argv: str, timeout: float | None = None) -> tuple[BridgeReply, Any]:
        reply = self.call(*argv, timeout=timeout)
        return reply, extract_json(reply.stdout)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Um redirecionamento nunca pode levar o smoke para fora do loopback."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def default_http(method: str, url: str, body: Any, timeout: float) -> tuple[int, Any]:
    """HTTP JSON só para loopback (nunca fala com hosts externos); ``OSError`` em transporte."""
    if not url.startswith("http://127.0.0.1:"):
        raise OSError(f"URL fora do loopback recusada: {url}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as res:   # noqa: S310 (loopback, sem redirects)
            status, raw = res.status, res.read(MAX_HTTP_BYTES)
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read(MAX_HTTP_BYTES)
    except urllib.error.URLError as exc:
        raise OSError(str(exc.reason)) from None
    try:
        return status, json.loads(raw.decode("utf-8")) if raw.strip() else {}
    except ValueError:
        return status, {"raw": raw.decode("utf-8", "replace")[:300]}


# ---------------------------------------------------- sessão live (df.sh)
class LiveSession:
    """``df up --no-web`` / ``df stop`` com runtime isolado; só pára o que arrancou."""

    def __init__(self, *, df_path: str, backend_port: int, runtime_dir: Path,
                 popen: Callable[..., Any] = subprocess.Popen,
                 runner: Callable[..., Any] = subprocess.run,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.df_path, self.backend_port = str(df_path), backend_port
        self.runtime_dir = Path(runtime_dir)
        self.popen, self.runner, self.clock, self.sleep = popen, runner, clock, sleep
        self.proc: Any = None
        self.log_path = self.runtime_dir / "df-up.log"
        self.env = scrubbed_env()
        self.env["DF_RUNTIME_DIR"] = str(self.runtime_dir)
        self.env["SOD_CAMPANHAS_DIR"] = str(self.runtime_dir / "campanhas")

    def _cmd(self, verb: str) -> list[str]:
        cmd = ["bash", str(DF_SH_PATH), verb]
        if verb == "up":
            cmd.append("--no-web")
        return cmd + ["--port", str(self.backend_port), "--df-path", self.df_path]

    def start(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "wb") as log:
            self.proc = self.popen(self._cmd("up"), cwd=str(ROOT), env=self.env,
                                   stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)

    def exited(self) -> int | None:
        return None if self.proc is None else self.proc.poll()

    def log_tail(self) -> str:
        try:
            lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return ""
        return redact(" | ".join(lines[-LOG_TAIL_LINES:]))

    def stop(self, timeout: float = STOP_TIMEOUT_S) -> tuple[bool, str]:
        """``df stop`` + espera pelo supervisor; escala TERM→KILL ao grupo se preciso."""
        notes: list[str] = []
        ok = True
        try:
            res = self.runner(self._cmd("stop"), cwd=str(ROOT), env=self.env, capture_output=True,
                              text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            if res.returncode != 0:
                ok = False
                notes.append(f"df stop saiu com {res.returncode}")
        except (subprocess.TimeoutExpired, OSError) as exc:
            ok = False
            notes.append(f"df stop falhou: {exc}")
        if self.proc is not None:
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                ok = False
                notes.append("o supervisor não saiu — TERM ao grupo")
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(self.proc.pid, sig)
                        self.proc.wait(timeout=10)
                        break
                    except (OSError, subprocess.TimeoutExpired):
                        continue
        return ok, "; ".join(notes) or "df stop ok"


# ---------------------------------------------------------- verificações
class Smoke:
    """Corre verificações em sequência e acumula :class:`CheckResult` (nunca levanta)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic,
                 deadline: float | None = None) -> None:
        self.clock, self.deadline = clock, deadline
        self.results: list[CheckResult] = []

    def status(self, name: str) -> str | None:
        return next((r.status for r in reversed(self.results) if r.name == name), None)

    def step(self, name: str, fn: Callable[[], tuple[str, str, dict]],
             requires: Sequence[str] = ()) -> CheckResult:
        t0 = self.clock()
        blocked = [r for r in requires if self.status(r) != PASS]
        if blocked:
            return self._add(name, SKIP, f"depende de {', '.join(blocked)}", t0, {})
        if self.deadline is not None and t0 >= self.deadline:
            return self._add(name, TIMEOUT, "orçamento de tempo esgotado", t0, {})
        try:
            status, detail, data = fn()
        except Precondition as exc:
            status, detail, data = PRECONDITION, str(exc), {}
        except Exception as exc:  # noqa: BLE001 - uma verificação nunca derruba o relatório
            status, detail, data = FAIL, f"{type(exc).__name__}: {exc}", {}
        return self._add(name, status, detail, t0, data)

    def _add(self, name: str, status: str, detail: str, t0: float, data: dict) -> CheckResult:
        result = CheckResult(name, status, redact(detail), int((self.clock() - t0) * 1000), data)
        self.results.append(result)
        return result

    def exit_code(self) -> int:
        statuses = {r.status for r in self.results}
        if FAIL in statuses:
            return EXIT_FAIL
        if TIMEOUT in statuses:
            return EXIT_TIMEOUT
        if PRECONDITION in statuses:
            return EXIT_PRECONDITION
        return EXIT_OK


class Checks:
    """As verificações partilhadas (offline e live) sobre uma ponte + biblioteca."""

    def __init__(self, bridge: BridgeClient, *, bridge_mod: Any, patterns: Any,
                 library: Path, live: bool, delay: float, sleep: Callable[[float], None],
                 expect: Sequence[str], journal: bool) -> None:
        self.bridge, self.B, self.pc = bridge, bridge_mod, patterns
        self.library, self.live, self.delay, self.sleep = Path(library), live, delay, sleep
        self.expect, self.journal = list(expect), journal
        self.snapshot: dict | None = None

    # -- helpers
    def _timed_out(self, reply: BridgeReply, what: str) -> tuple[str, str, dict] | None:
        if reply.timed_out:
            return TIMEOUT, f"{what}: {redact(reply.stderr)}", {}
        return None

    def read_snapshot(self, events: int = 0) -> tuple[BridgeReply, Any]:
        return self.bridge.json("snapshot", "--json", "--events", str(events))

    def read_screen(self) -> tuple[str, str]:
        reply, obj = self.bridge.json("act", "screen", "--json")
        if reply.code != 0 or obj is None:
            return "", ""
        try:
            return self.pc.screen_from_payload(obj)
        except ValueError:
            return "", ""

    def batch(self, dsl: str, guards: bool = True) -> tuple[int, str | None]:
        """``act batch``; ``guards=False`` (fechar overlays) não aborta por mudança de foco."""
        args = ["act", "batch", "--json", "--max-steps", str(MOVE_MAX_STEPS),
                "--delay", str(self.delay)]
        if guards:
            args += ["--stop-on-focus-change", "--stop-on-threat"]
        if not self.journal:
            args.append("--no-journal")
        reply, rep = self.bridge.json(*args, dsl, timeout=BATCH_TIMEOUT_S)
        if reply.timed_out:
            return reply.code, "timeout"
        return reply.code, (rep.get("reason") if isinstance(rep, dict) else None)

    # -- verificações
    def bridge_status(self) -> tuple[str, str, dict]:
        reply, info = self.bridge.json("status", "--json")
        if (t := self._timed_out(reply, "status")):
            return t
        if reply.code == 10 or (isinstance(info, dict) and info.get("df_path") is None):
            raise Precondition("Dwarf Fortress não encontrado (usa --df-path ou DF_PATH)")
        if not isinstance(info, dict):
            return FAIL, f"status sem JSON (exit {reply.code}): {redact(reply.stderr)}", {}
        rpc = info.get("rpc") if isinstance(info.get("rpc"), dict) else {}
        if not rpc.get("reachable"):
            msg = f"RPC do DFHack inalcançável na porta {rpc.get('port')}"
            if self.live:
                raise Precondition(msg + " (o jogo arrancou com ./dfhack?)")
            return FAIL, msg, {"rpc": rpc}
        return PASS, f"RPC alcançável na porta {rpc.get('port')}", {
            "port": rpc.get("port"), "dfhack_installed": info.get("dfhack_installed"),
            "dfhack_run": info.get("dfhack_run"), "ok_write": info.get("ok_write")}

    def dsl_parse(self) -> tuple[str, str, dict]:
        reply, rep = self.bridge.json("act", "batch", "--dry-run", "--json", DSL_PROBE)
        if (t := self._timed_out(reply, "dry-run")):
            return t
        summary = rep.get("summary") if isinstance(rep, dict) else None
        if not (reply.code == 0 and isinstance(summary, dict)
                and summary.get("validated") == DSL_PROBE_STEPS):
            return FAIL, f"dry-run do DSL falhou (exit {reply.code}): {redact(reply.stdout)}", {}
        bad_reply, bad = self.bridge.json("act", "batch", "--dry-run", "--json", "voar n")
        if bad_reply.code == 0 or not (isinstance(bad, dict) and bad.get("ok") is False):
            return FAIL, "um passo inválido ('voar n') foi aceite pelo parser do DSL", {}
        return PASS, f"{DSL_PROBE_STEPS} passos validados; passo inválido rejeitado", {
            "steps": DSL_PROBE_STEPS}

    def snapshot_schema(self) -> tuple[str, str, dict]:
        reply, snap = self.read_snapshot(events=5)
        if (t := self._timed_out(reply, "snapshot")):
            return t
        if reply.code != 0:
            kind = classify_snapshot_failure(reply)
            if kind == "no_adventurer":
                raise Precondition("sem aventureiro carregado (snapshot exit 13: 'sem aventureiro')")
            return FAIL, (f"snapshot falhou ({kind}, exit {reply.code}): "
                          f"{redact(reply.stdout or reply.stderr)}"), {"kind": kind}
        problems = validate_snapshot(snap, self.B.EMPTY_METRICS)
        raw_warnings = snap.get("warnings") if isinstance(snap, dict) else None
        warnings = ([redact(w, 160) for w in raw_warnings]
                    if isinstance(raw_warnings, list) else [])
        if problems:
            return FAIL, "; ".join(problems[:5]), {"problems": problems[:20]}
        if warnings and not self.live:     # offline é determinístico: avisos = regressão
            return FAIL, f"snapshot com avisos inesperados: {warnings[:3]}", {"warnings": warnings}
        self.snapshot = snap
        return PASS, "contrato válido (8 grupos, nulls, ts ISO)", {
            "position": snapshot_position(snap), "focus": snap.get("focus"), "warnings": warnings}

    def snapshot_consumer(self) -> tuple[str, str, dict]:
        try:
            if str(SRC_DIR) not in sys.path:
                sys.path.insert(0, str(SRC_DIR))
            from system_one_dwarf.game.models import GameState  # noqa: PLC0415
        except ImportError as exc:
            return SKIP, f"consumidor indisponível: {exc}", {}
        state = GameState.from_dict(self.snapshot)
        expected = snapshot_position(self.snapshot)
        got = state.adventurer.pos if state.adventurer is not None else None
        if state.adventurer is None or expected is None or tuple(got[:2]) != tuple(expected[:2]):
            return FAIL, f"GameState lê posição {got} e o snapshot tem {expected}", {}
        if len(state.metrics.GROUP_NAMES) != 8 or state.schema_version != SNAPSHOT_SCHEMA_VERSION:
            return FAIL, "GameState não reconhece os 8 grupos / schema_version", {}
        return PASS, f"GameState.from_dict lê posição {got[:2]}", {"position": got}

    def pattern_library(self) -> tuple[str, str, dict]:
        report = self.pc.check_library(self.library, sleep=self.sleep)
        data = {"path": display_path(self.library), "patterns": report.patterns,
                "stuck_events": report.stuck_events, "warnings": len(report.warnings)}
        if not report.ok:
            first = [f"{p.code}@{p.where}" for p in report.errors[:3]]
            return FAIL, f"{len(report.errors)} erro(s) na biblioteca: {', '.join(first)}", data
        return PASS, f"{report.patterns} padrões, {report.stuck_events} stuck_events", data

    def screen_patterns(self) -> tuple[str, str, dict]:
        reply, obj = self.bridge.json("act", "screen", "--json")
        if (t := self._timed_out(reply, "screen")):
            return t
        try:
            focus, text = self.pc.screen_from_payload(obj)
        except ValueError as exc:
            return FAIL, f"ecrã ilegível (exit {reply.code}): {exc}", {}
        library = self.pc.load_library(self.library)[0]
        matches = self.pc.analyze_screen(library, focus, text)
        by_id = {m.id: m for m in matches}
        matched = [m.id for m in matches if m.status == "match"]
        suspects = [m.id for m in matches if m.status in ("focus_only", "text_only")]
        absent = [e for e in self.expect if e not in matched]
        data = {"focus": focus, "matched": matched, "suspects": suspects}
        if absent:
            missing = {a: by_id[a].missing if a in by_id else ["(id desconhecido)"] for a in absent}
            data["missing_markers"] = missing
            return FAIL, f"padrões esperados não casam com focus={focus!r}: {absent}", data
        return PASS, f"focus={focus!r}; casam {matched}", data

    def movement_proof(self) -> tuple[str, str, dict]:
        def read_position() -> tuple | None:
            reply, snap = self.read_snapshot(events=0)
            return snapshot_position(snap) if reply.code == 0 else None

        proof = prove_movement(
            read_position, self.batch, lambda: self.read_screen()[0],
            settle=lambda: self.sleep(MOVE_SETTLE_S if self.live else 0.0))
        data = {"before": proof.before, "after": proof.after, "delta": proof.delta,
                "attempts": [a._asdict() for a in proof.attempts], "diagnosis": proof.diagnosis,
                "focus": proof.focus}
        if proof.moved:
            return PASS, f"posição {proof.before[:2]} → {proof.after[:2]} (Δ={proof.delta})", data
        if any(a.reason == "timeout" for a in proof.attempts):
            return TIMEOUT, "a ponte não respondeu a tempo durante a prova de movimento", data
        reasons = {a.reason for a in proof.attempts if a.reason}
        data["reasons"] = sorted(reasons)
        if reasons & {"threat", "focus_changed"}:
            return FAIL, (f"o batch abortou ({', '.join(sorted(reasons))}) em "
                          f"{len(proof.attempts)} tentativa(s): ameaça próxima ou mudança de ecrã; "
                          "repete num local calmo"), data
        hint = {"input_swallowed": "um overlay (dungeonmode/Help) está a engolir o input (D18)",
                "modal_open": f"um menu modal está aberto (focus={proof.focus!r})",
                "no_position": "o snapshot não trouxe posição"}.get(
                    proof.diagnosis or "", "parede em todas as direções ou tempo congelado")
        return FAIL, f"sem movimento em {len(proof.attempts)} tentativa(s): {hint}", data

    def proof_controls(self, game: FakeGame) -> tuple[str, str, dict]:
        """Controlos negativos: a lógica da prova TEM de falhar quando não há movimento."""
        original = game.scenario
        outcome: dict[str, Any] = {}
        try:
            for scenario, expected in (("blocked", "blocked_or_frozen"), ("frozen", "input_swallowed")):
                game.configure(scenario)
                proof = prove_movement(
                    lambda: snapshot_position(self.read_snapshot(0)[1]), self.batch,
                    lambda: self.read_screen()[0])
                outcome[scenario] = {"moved": proof.moved, "diagnosis": proof.diagnosis}
                if proof.moved or proof.diagnosis != expected:
                    return FAIL, (f"controlo '{scenario}': moved={proof.moved} "
                                  f"diagnosis={proof.diagnosis!r} (esperado {expected!r})"), outcome
            game.configure("no-adventurer")
            reply, _ = self.read_snapshot(0)
            kind = classify_snapshot_failure(reply)
            outcome["no-adventurer"] = {"exit": reply.code, "kind": kind}
            if kind != "no_adventurer":
                return FAIL, f"sem aventureiro foi classificado como {kind!r}", outcome
        finally:
            game.configure(original)
        return PASS, "parede, overlay e sem-aventureiro detetados como esperado", outcome


# ----------------------------------------------------------- relatório
def build_report(mode: str, smoke: Smoke, *, started_at: str, duration_ms: int,
                 environment: dict, warnings: Sequence[str] = (),
                 exit_code: int | None = None) -> dict[str, Any]:
    code = smoke.exit_code() if exit_code is None else exit_code
    counts = {s: sum(1 for r in smoke.results if r.status == s)
              for s in (PASS, FAIL, SKIP, PRECONDITION, TIMEOUT)}
    return {"schema": REPORT_SCHEMA, "tool": "df_smoke", "mode": mode, "ok": code == EXIT_OK,
            "exit_code": code, "started_at": started_at, "duration_ms": duration_ms,
            "environment": environment, "warnings": list(warnings),
            "checks": [r.as_dict() for r in smoke.results],
            "summary": {"total": len(smoke.results), "passed": counts[PASS],
                        "failed": counts[FAIL], "skipped": counts[SKIP],
                        "preconditions": counts[PRECONDITION], "timeouts": counts[TIMEOUT]}}


def write_report(path: Path, report: dict) -> None:
    """Escrita atómica do relatório (nunca deixa um JSON truncado)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def render_human(report: dict) -> str:
    icon = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP", PRECONDITION: "PRE ", TIMEOUT: "TIME"}
    lines = [f"=== df_smoke ({report['mode']}) ==="]
    for c in report["checks"]:
        lines.append(f"[{icon.get(c['status'], c['status'])}] {c['name']:<20} {c['detail']} "
                     f"({c['duration_ms']} ms)")
    s = report["summary"]
    lines.append(f"{'OK' if report['ok'] else 'FALHOU'} — {s['passed']}/{s['total']} ok, "
                 f"{s['failed']} falhas, {s['skipped']} ignoradas, "
                 f"{s['preconditions']} pré-condições, {s['timeouts']} timeouts "
                 f"(exit {report['exit_code']})")
    return "\n".join(lines)


# ----------------------------------------------------------- modos
def run_offline(*, scenario: str = "normal", library: Path = LIBRARY_PATH,
                timeout: float = OFFLINE_TIMEOUT_S, expect: Sequence[str] = DEFAULT_EXPECT_PATTERNS,
                clock: Callable[[], float] = time.monotonic,
                sleep: Callable[[float], None] = time.sleep,
                runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Fluxo real contra o DFHack falso → relatório (nunca levanta)."""
    started, t0 = _utc_now(), clock()
    bridge_mod = load_module("df_smoke_df_bridge", BRIDGE_PATH)
    patterns = load_module("df_smoke_patterns_check", PATTERNS_CHECK_PATH)
    smoke = Smoke(clock=clock, deadline=t0 + timeout)
    tmp = Path(tempfile.mkdtemp(prefix="df-smoke-"))
    server, rpc_requests = None, 0
    try:
        (tmp / "gamelog.txt").write_text(
            "*** Log file started ***\n(100) Granite 1, year 100: Smoke Tester arrives.\n",
            encoding="utf-8")
        game = FakeGame(scenario)
        server = FakeRpcServer(game.respond, bridge_mod)
        server.start()
        env = scrubbed_env()
        env.update({"DF_BRIDGE_SKIP_INSTALL": "1", "PYTHONDONTWRITEBYTECODE": "1",
                    "XDG_STATE_HOME": str(tmp / "state")})
        bridge = BridgeClient(df_path=str(tmp), port=server.port, via="tcp", env=env, runner=runner)
        checks = Checks(bridge, bridge_mod=bridge_mod, patterns=patterns, library=library,
                        live=False, delay=0.0, sleep=sleep, expect=expect, journal=False)
        smoke.step("bridge_status", checks.bridge_status)
        smoke.step("dsl_parse", checks.dsl_parse, ("bridge_status",))
        smoke.step("snapshot_schema", checks.snapshot_schema, ("bridge_status",))
        smoke.step("snapshot_consumer", checks.snapshot_consumer, ("snapshot_schema",))
        smoke.step("pattern_library", checks.pattern_library)
        smoke.step("screen_patterns", checks.screen_patterns, ("snapshot_schema",))
        smoke.step("movement_proof", checks.movement_proof, ("snapshot_schema",))
        smoke.step("proof_controls", lambda: checks.proof_controls(game), ("bridge_status",))
    finally:
        rpc_requests = len(server.requests) if server is not None else 0
        if server is not None:
            server.close()
        shutil.rmtree(tmp, ignore_errors=True)
    env_info = {"python": sys.version.split()[0], "platform": sys.platform, "scenario": scenario,
                "library": display_path(library), "rpc_requests": rpc_requests}
    return build_report("offline", smoke, started_at=started,
                        duration_ms=int((clock() - t0) * 1000), environment=env_info)


# ------------------------------------------------------------ live
HttpFn = Callable[[str, str, Any, float], tuple[int, Any]]


def rpc_reachable(bridge: BridgeClient) -> bool:
    """``status`` da ponte: o handshake RPC completo responde? (nunca levanta)"""
    info = bridge.json("status", "--json")[1]
    rpc = info.get("rpc") if isinstance(info, dict) else None
    return bool(isinstance(rpc, dict) and rpc.get("reachable"))


def backend_healthy(http: HttpFn, port: int) -> bool:
    try:
        status, body = http("GET", f"http://127.0.0.1:{port}/api/health", None, HTTP_TIMEOUT_S)
    except OSError:
        return False
    return status == 200 and isinstance(body, dict) and body.get("status") == "ok"


def wait_ready(*, bridge: BridgeClient, http: HttpFn, backend_port: int, need_backend: bool,
               session: LiveSession | None, timeout: float, clock: Callable[[], float],
               sleep: Callable[[float], None]) -> tuple[str, str, dict]:
    """Espera RPC (+ backend quando é preciso): FAIL se o ``df up`` morrer, TIMEOUT no fim."""
    deadline = clock() + timeout
    while True:
        if session is not None and session.exited() is not None:
            return FAIL, (f"df up terminou cedo (exit {session.exited()}): "
                          f"{session.log_tail()}"), {}
        rpc_ok, api_ok = rpc_reachable(bridge), backend_healthy(http, backend_port)
        if rpc_ok and (api_ok or not need_backend):
            return PASS, "RPC" + (" e backend" if api_ok else "") + " prontos", {
                "rpc": True, "backend": api_ok}
        if clock() >= deadline:
            tail = session.log_tail() if session is not None else ""
            return TIMEOUT, (f"não ficou pronto em {timeout:.0f}s (rpc={rpc_ok}, "
                             f"backend={api_ok}) {tail}"), {"rpc": rpc_ok, "backend": api_ok}
        sleep(POLL_INTERVAL_S)


class BackendApi:
    """Mínimo da API do backend para criar/carregar uma aventura (só loopback)."""

    def __init__(self, base: str, http: HttpFn) -> None:
        self.base, self.http = base, http

    def campaign_id(self, name: str) -> str:
        """Id da campanha ``name`` (anexa se existir, senão cria)."""
        status, body = self.http("GET", f"{self.base}/api/campaigns", None, HTTP_TIMEOUT_S)
        items = body if isinstance(body, list) else (
            body.get("campaigns") if isinstance(body, dict) else None)
        if status == 200 and isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name") == name:
                    cid = item.get("id") or item.get("campaign_id")
                    if cid:
                        return str(cid)
        status, body = self.http("POST", f"{self.base}/api/campaigns",
                                 {"name": name, "is_new_game": True}, HTTP_TIMEOUT_S)
        node = body if isinstance(body, dict) else {}
        if isinstance(node.get("campaign"), dict):
            node = node["campaign"]
        cid = node.get("id") or node.get("campaign_id")
        if status not in (200, 201) or not cid:
            raise RuntimeError(f"criar a campanha {name!r} falhou (HTTP {status})")
        return str(cid)

    def run_task(self, cid: str, kind: str, body: dict, budget: float, *,
                 clock: Callable[[], float], sleep: Callable[[float], None]) -> tuple[bool, str]:
        """POST da tarefa (202) + poll até ``done``/``error``; cancela ao esgotar ``budget``."""
        root = f"{self.base}/api/campaigns/{cid}/adventures"
        status, resp = self.http("POST", f"{root}/{kind}", body, HTTP_TIMEOUT_S)
        if status == 409:                   # sobra de uma corrida anterior: cancela e repete 1×
            self.http("POST", f"{root}/cancel", {}, HTTP_TIMEOUT_S)
            status, resp = self.http("POST", f"{root}/{kind}", body, HTTP_TIMEOUT_S)
        if status != 202:
            return False, f"POST devolveu HTTP {status}: {redact(resp)}"
        deadline = clock() + budget
        while clock() < deadline:
            status, resp = self.http("GET", f"{root}/status", None, HTTP_TIMEOUT_S)
            task = resp.get("task") if status == 200 and isinstance(resp, dict) else None
            state = str((task or {}).get("state") or "").lower()
            if state == "done":
                return True, "done"
            if state == "error":
                return False, f"erro: {redact((task or {}).get('error'))}"
            sleep(ADVENTURE_POLL_S)
        self.http("POST", f"{root}/cancel", {}, HTTP_TIMEOUT_S)
        return False, f"sem fim em {budget:.0f}s (cancelada)"


def ensure_adventurer(checks: Checks, opts: argparse.Namespace, http: HttpFn, *, budget: float,
                      clock: Callable[[], float],
                      sleep: Callable[[float], None]) -> tuple[str, str, dict]:
    """Aventureiro presente, carregado (``--adventure-id``) ou criado (``--new-adventure``)."""
    reply, _ = checks.read_snapshot(0)
    if reply.code == 0:
        return PASS, "aventureiro já presente", {"created": False}
    kind = classify_snapshot_failure(reply)
    if kind == "timeout":
        return TIMEOUT, "o snapshot não respondeu a tempo", {}
    if kind != "no_adventurer":
        return FAIL, (f"snapshot falhou ({kind}, exit {reply.code}): "
                      f"{redact(reply.stdout or reply.stderr)}"), {"kind": kind}
    if not (opts.adventure_id or opts.new_adventure):
        raise Precondition("sem aventureiro: usa --adventure-id <save> ou --new-adventure")
    api = BackendApi(f"http://127.0.0.1:{opts.backend_port}", http)
    cid = api.campaign_id(SMOKE_CAMPAIGN)
    if opts.adventure_id:
        steps = [("load", {"adventure_id": opts.adventure_id})]
    else:
        steps = [("new-world", {"params": "quick"}), ("new-character", {"randomize": True})]
    for kind_, body in steps:
        ok, detail = api.run_task(cid, kind_, body, budget, clock=clock, sleep=sleep)
        if not ok:
            return FAIL, f"tarefa {kind_}: {detail}", {"task": kind_}
    reply, _ = checks.read_snapshot(0)
    if reply.code != 0:
        return FAIL, "a tarefa terminou mas o snapshot continua sem aventureiro", {}
    return PASS, f"aventureiro obtido ({', '.join(k for k, _ in steps)})", {
        "created": True, "campaign": SMOKE_CAMPAIGN}


def stop_session(session: LiveSession, bridge: BridgeClient, started_game: bool,
                 timeout: float = STOP_TIMEOUT_S) -> tuple[bool, str]:
    """``df stop`` e, se fomos nós a arrancar o jogo, prova de que já não responde."""
    ok, detail = session.stop(timeout)
    if started_game and rpc_reachable(bridge):
        return False, "o jogo continua alcançável após df stop"
    return ok, detail + ("; jogo parado" if started_game else "; jogo não era nosso (mantido)")


def run_live(opts: argparse.Namespace, *, clock: Callable[[], float] = time.monotonic,
             sleep: Callable[[float], None] = time.sleep, http: HttpFn = default_http,
             session_factory: Callable[..., LiveSession] = LiveSession,
             runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    """Fluxo live (``df up --no-web`` → provas → ``df stop``) → relatório (nunca levanta)."""
    started, t0 = _utc_now(), clock()
    bridge_mod = load_module("df_smoke_df_bridge", BRIDGE_PATH)
    patterns = load_module("df_smoke_patterns_check", PATTERNS_CHECK_PATH)
    smoke = Smoke(clock=clock, deadline=t0 + opts.timeout)
    df_path = Path(opts.df_path or os.environ.get("DF_PATH") or DEFAULT_DF_PATH).expanduser()
    runtime = Path(tempfile.mkdtemp(prefix="df-smoke-live-"))
    bridge = BridgeClient(df_path=str(df_path), port=opts.rpc_port, runner=runner)
    checks = Checks(bridge, bridge_mod=bridge_mod, patterns=patterns,
                    library=Path(opts.library), live=True, delay=MOVE_DELAY_S, sleep=sleep,
                    expect=opts.expect_pattern or DEFAULT_EXPECT_PATTERNS, journal=True)
    need_backend = bool(opts.adventure_id or opts.new_adventure)
    warnings: list[str] = []
    session: LiveSession | None = None
    started_game = interrupted = False

    def preflight() -> tuple[str, str, dict]:
        if not sys.platform.startswith("linux"):
            raise Precondition(f"só Linux x86_64 é suportado (plataforma: {sys.platform})")
        for needed in (DF_SH_PATH, BRIDGE_PATH):
            if not needed.is_file():
                raise Precondition(f"ficheiro do repositório em falta: {needed}")
        if not df_path.is_dir() or not (df_path / "dfhack").exists():
            raise Precondition(f"Dwarf Fortress/DFHack não encontrado em {display_path(df_path)} "
                               "(usa --df-path ou DF_PATH)")
        if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            warnings.append("sem DISPLAY: o jogo precisa de um X (ex.: xvfb-run -a)")
        return PASS, f"DF em {display_path(df_path)}", {}

    def start() -> tuple[str, str, dict]:
        nonlocal session, started_game
        was_up = rpc_reachable(bridge)
        if was_up and not opts.attach:
            # a prova faz movidas REAIS: nunca num jogo em uso sem consentimento explícito
            raise Precondition("já há um jogo a correr (RPC alcançável): usa --attach ou "
                               "--no-start para provar movimento nele, ou pára-o primeiro")
        session = session_factory(df_path=str(df_path), backend_port=opts.backend_port,
                                  runtime_dir=runtime, runner=runner, clock=clock, sleep=sleep)
        session.start()
        started_game = not was_up
        return PASS, ("df up --no-web lançado" +
                      (" (o jogo já estava a correr: fica como está)" if was_up else "")), {
                          "attached": was_up}

    def settle() -> tuple[str, str, dict]:
        ok, trail, focus = settle_to_map(checks.read_screen, lambda d: checks.batch(d, False)[0])
        if not ok:
            return FAIL, f"overlays não fecharam (focus={focus!r}); trilho: {trail[-4:]}", {
                "trail": trail, "focus": focus}
        return PASS, f"mapa livre (focus={focus!r}, {len(trail)} ação/ões)", {"trail": trail}

    try:
        smoke.step("preflight", preflight)
        gate: tuple[str, ...] = ("preflight",)
        if not opts.no_start:
            smoke.step("start", start, gate)
            gate = ("preflight", "start")
        smoke.step("ready", lambda: wait_ready(
            bridge=bridge, http=http, backend_port=opts.backend_port, need_backend=need_backend,
            session=session, timeout=opts.startup_timeout, clock=clock, sleep=sleep), gate)
        smoke.step("bridge_status", checks.bridge_status, ("ready",))
        smoke.step("adventure", lambda: ensure_adventurer(
            checks, opts, http, clock=clock, sleep=sleep,
            budget=max(1.0, min(ADVENTURE_TIMEOUT_S, (smoke.deadline or 0) - clock()))),
            ("bridge_status",))
        smoke.step("snapshot_schema", checks.snapshot_schema, ("adventure",))
        smoke.step("snapshot_consumer", checks.snapshot_consumer, ("snapshot_schema",))
        smoke.step("pattern_library", checks.pattern_library)
        smoke.step("dsl_parse", checks.dsl_parse, ("preflight",))
        smoke.step("settle_overlays", settle, ("snapshot_schema",))
        smoke.step("screen_patterns", checks.screen_patterns, ("settle_overlays",))
        smoke.step("movement_proof", checks.movement_proof, ("settle_overlays",))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        if session is not None and not opts.keep_running:
            t_stop = clock()
            ok, detail = stop_session(session, bridge, started_game)
            smoke._add("stop", PASS if ok else FAIL, detail, t_stop,
                       {"started_game": started_game})
        shutil.rmtree(runtime, ignore_errors=True)
    env_info = {"python": sys.version.split()[0], "platform": sys.platform,
                "df_path": display_path(df_path), "backend_port": opts.backend_port,
                "attached": bool(session is not None and not started_game),
                "library": display_path(opts.library)}
    return build_report("live", smoke, started_at=started, duration_ms=int((clock() - t0) * 1000),
                        environment=env_info, warnings=warnings,
                        exit_code=EXIT_INTERRUPTED if interrupted else None)


# ------------------------------------------------------------------ CLI
def _positive(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("tem de ser > 0")
    return value


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Smoke do System One Dwarf: ponte → snapshot → padrões → movida com prova.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--offline", action="store_true",
                      help="DFHack falso em loopback (predefinição; corre em CI)")
    mode.add_argument("--live", action="store_true",
                      help="jogo real: df up --no-web → provas → df stop (runner com DF)")
    ap.add_argument("--scenario", choices=SCENARIOS, default="normal",
                    help="offline: cenário do jogo falso (valida o próprio smoke)")
    ap.add_argument("--library", default=str(LIBRARY_PATH), help="biblioteca de padrões (JSON)")
    ap.add_argument("--expect-pattern", action="append", metavar="ID",
                    help="padrão que TEM de casar no mapa (repetível; predef.: p-move-or-goto)")
    ap.add_argument("--report", metavar="FICHEIRO", help="grava o relatório JSON aqui")
    ap.add_argument("--json", action="store_true", help="relatório JSON no stdout")
    ap.add_argument("--timeout", type=_positive, default=None,
                    help=f"orçamento global em s (offline {OFFLINE_TIMEOUT_S:.0f}, "
                         f"live {LIVE_TIMEOUT_S:.0f})")
    live = ap.add_argument_group("live")
    live.add_argument("--df-path", default=None, help=f"DF (predef.: $DF_PATH ou {DEFAULT_DF_PATH})")
    live.add_argument("--rpc-port", type=int, default=None, help="porta RPC do DFHack")
    live.add_argument("--backend-port", type=int, default=DEFAULT_BACKEND_PORT)
    live.add_argument("--startup-timeout", type=_positive, default=STARTUP_TIMEOUT_S)
    live.add_argument("--no-start", action="store_true",
                      help="não corre df up/df stop (jogo e backend já estão a correr; "
                           "implica --attach)")
    live.add_argument("--attach", action="store_true",
                      help="aceita um jogo já a correr (a prova faz movidas reais nele; "
                           "o jogo nunca é parado)")
    live.add_argument("--keep-running", action="store_true", help="não corre df stop no fim")
    adv = live.add_mutually_exclusive_group()
    adv.add_argument("--adventure-id", help="carrega este save pela API se não houver aventureiro")
    adv.add_argument("--new-adventure", action="store_true",
                     help="cria mundo (quick) + personagem pela API se não houver aventureiro")
    return ap


def main(argv: Sequence[str] | None = None, *, clock: Callable[[], float] = time.monotonic,
         sleep: Callable[[float], None] = time.sleep,
         runner: Callable[..., Any] = subprocess.run) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    live = bool(args.live)
    if live and args.scenario != "normal":
        print("erro: --scenario só existe no modo --offline", file=sys.stderr)
        return EXIT_USAGE
    if not live and (args.adventure_id or args.new_adventure or args.no_start or args.attach
                     or args.keep_running or args.df_path):
        print("erro: opções de live (--df-path, --adventure-id, --new-adventure, --no-start, "
              "--attach, --keep-running) precisam de --live", file=sys.stderr)
        return EXIT_USAGE
    args.timeout = args.timeout or (LIVE_TIMEOUT_S if live else OFFLINE_TIMEOUT_S)

    previous = None
    if live:
        def _terminate(_signum, _frame):
            raise KeyboardInterrupt
        previous = signal.signal(signal.SIGTERM, _terminate)   # CI cancelado → pára o jogo
    try:
        if live:
            report = run_live(args, clock=clock, sleep=sleep, runner=runner)
        else:
            report = run_offline(scenario=args.scenario, library=Path(args.library),
                                 timeout=args.timeout, clock=clock, sleep=sleep, runner=runner,
                                 expect=args.expect_pattern or DEFAULT_EXPECT_PATTERNS)
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if args.report:
        try:
            write_report(Path(args.report), report)
        except OSError as exc:
            print(f"aviso: não consegui gravar o relatório: {exc}", file=sys.stderr)
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_human(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
