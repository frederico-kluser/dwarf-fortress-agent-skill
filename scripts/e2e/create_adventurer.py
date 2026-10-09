#!/usr/bin/env python3
"""System One Dwarf — replays the PROVEN adventurer-creation sequence (CLI).

This CLI is now a thin **wrapper** around the service piece
``src/system_one_dwarf/game/driver.py`` (the same flow the web API drives):
the step engine, the retry rule and the abort report live in
:class:`system_one_dwarf.game.driver.AdventureDriver`; this script keeps the
command-line behaviour and output of the original replay tool.

Drives DF 53.16's mouse-first menus through the DFHack bridge. This build's title
menu does NOT respond to `key SELECT`; the working input is a synthetic mouse click
on the on-screen button. Each step's action is a directive in `steps.json`:

    click "Label"     -> find `Label` in the live screen text, click its centre
    click_re <regex>  -> find the first regex match, click its centre (for labels
                         that vary per world, e.g. `Select Omon Wur`)
    poll              -> do not act; poll until the expected focus appears
    key KEY [KEY...]  -> inject df.interface_key names (SELECT, LEAVESCREEN,
                         STANDARDSCROLL_UP/DOWN, CUSTOM_A..Z, STRING_Axxx,
                         OPTION1..20, MENU_CONFIRM, ...)

Every action is verified against the step's `expect` (a list of alternatives;
any match passes — a bare string is accepted too):
    "focus:<text> ..."  -> `<text>` (before any " (") must appear in the focus
    "screen:<text> ..." -> `<text>` must appear in the visible screen text

Retry <= 5 per step; on final failure it ABORTS with a PT report (screens seen +
actions sent). `--dry-run` prints the plan and exits without touching the game.

    python3 scripts/e2e/create_adventurer.py --dry-run
    python3 scripts/e2e/create_adventurer.py --df-path /home/ondokai/Games/dwarf-fortress-53.16
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:  # o pacote é src-layout e pode não estar instalado
    sys.path.insert(0, str(SRC))

from system_one_dwarf.game.driver import (  # noqa: E402
    AdventureDriver,
    StepAbort,
    StepCancelled,
    SubprocessBridge,
    check_expect,
    label_centre,
    load_steps as _load_steps,
)

DEFAULT_STEPS = HERE / "steps.json"
MAX_RETRIES = 5
POLL_TIMEOUT = 240  # seconds for `poll` steps (world gen / export can be slow)


def load_steps(path):
    try:
        return _load_steps(path)
    except ValueError as exc:
        raise SystemExit(str(exc))


class Bridge(SubprocessBridge):
    """A ponte do replay — :class:`system_one_dwarf.game.driver.SubprocessBridge`."""


def abort_report(step, screens, actions):
    """Relatório PT de aborto (compat: o texto histórico do CLI)."""
    print("", file=sys.stderr)
    print("=== RELATORIO DE ABORTO (create_adventurer) ===", file=sys.stderr)
    print(f"Passo: {step.get('name')}", file=sys.stderr)
    print(f"Tentativas: {MAX_RETRIES}/{MAX_RETRIES}", file=sys.stderr)
    print(f"Acoes enviadas: {' | '.join(actions) or '(nenhuma)'}", file=sys.stderr)
    print(f"Telas vistas ({len(screens)}):", file=sys.stderr)
    for i, (focus, text) in enumerate(screens, 1):
        head = " / ".join(ln.strip() for ln in text.splitlines() if ln.strip())[:180]
        print(f"  [{i}] focus={focus} :: {head or '(vazia)'}", file=sys.stderr)
    print("ABORTADO: fluxo de criacao nao concluido.", file=sys.stderr)
    raise SystemExit(1)


class _Echo:
    """Reproduz no stdout a narrativa histórica do replay a partir dos eventos."""

    def __init__(self, total: int, out=sys.stdout):
        self.total = total
        self.out = out

    def __call__(self, event):
        kind = event.get("event")
        if kind == "step_start":
            index = int(event.get("index") or 0)
            print(f"[{index}/{self.total}] {event.get('name')} "
                  f"(guessed={event.get('guessed')})", file=self.out)
            print(f"   directive: {event.get('directive')}", file=self.out)
        elif kind == "step_ok":
            print(f"   OK: {event.get('expect')}", file=self.out)
        elif kind == "attempt_failed":
            print(f"   falha (tentativa {event.get('attempt')}): expect nao visto",
                  file=self.out)


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="create_adventurer.py",
                                description="Replay da criacao do aventureiro DF 53.16 via df_bridge.")
    p.add_argument("--steps", default=str(DEFAULT_STEPS),
                   help="JSON com os passos (default: %(default)s)")
    p.add_argument("--df-path", default="/home/ondokai/Games/dwarf-fortress-53.16",
                   help="diretorio do Dwarf Fortress")
    p.add_argument("--port", type=int, default=None, help="porta RPC DFHack")
    p.add_argument("--dry-run", action="store_true",
                   help="imprime o plano e sai sem tocar no jogo")
    return p.parse_args(argv)


def print_plan(steps):
    for i, step in enumerate(steps, 1):
        keys = "; ".join(step.get("keys") or [])
        print(f"[{i}/{len(steps)}] {step.get('name')} (guessed={step.get('guessed')})")
        print(f"    do:    {keys or '(poll/none)'}")
        print(f"    wait:  {step.get('wait')}s")
        print(f"    expect:{step.get('expect')}")
    print(f"--- dry-run: {len(steps)} passo(s), jogo NAO tocado ---")


def main(argv=None):
    args = parse_args(argv)
    steps = load_steps(args.steps)
    if args.dry_run:
        print_plan(steps)
        return 0

    bridge = Bridge(args.df_path, args.port)
    driver = AdventureDriver(
        bridge, steps=steps, max_attempts=MAX_RETRIES, poll_timeout=POLL_TIMEOUT
    )
    try:
        driver.run_flow(steps, on_event=_Echo(len(steps)))
    except StepAbort as exc:
        abort_report(exc.step, exc.screens, exc.actions)
    except StepCancelled:
        print("CANCELADO: fluxo interrompido pelo utilizador.", file=sys.stderr)
        return 1
    print("--- fluxo de criacao concluido ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
