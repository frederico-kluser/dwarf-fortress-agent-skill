#!/usr/bin/env python3
"""System One Dwarf — replays the PROVEN adventurer-creation sequence (stdlib only).

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
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_STEPS = HERE / "steps.json"
MAX_RETRIES = 5
POLL_TIMEOUT = 240  # seconds for `poll` steps (world gen / export can be slow)


def load_steps(path):
    steps = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(steps, list) or not steps:
        raise SystemExit(f"steps inválido em {path}: esperava lista nao vazia")
    return steps


class Bridge:
    def __init__(self, df_path, port=None):
        self.base = ["python3", "dfhack/df_bridge.py"]
        if df_path:
            self.base += ["--df-path", df_path]
        if port:
            self.base += ["--port", str(port)]

    def _run(self, args, timeout=120):
        return subprocess.run(self.base + list(args), capture_output=True,
                              text=True, cwd=str(ROOT), timeout=timeout)

    def screen(self):
        """-> (focus:str, text:str) from `act screen --json`."""
        proc = self._run(["act", "screen", "--json"])
        try:
            data = json.loads(proc.stdout or "{}").get("data", {})
        except ValueError:
            return "", proc.stdout or ""
        focus = "/".join(data.get("focus") or [])
        text = "\n".join(data.get("lines") or [])
        return focus, text

    def click(self, x, y):
        self._run(["act", "batch", f"click {x},{y}; sleep 500"])

    def keys(self, names):
        self._run(["act", "batch", "key " + " ".join(names) + "; sleep 500"])


def label_centre(text, needle, regex=False):
    """(x, y) of the centre of `needle` in the screen text, or None."""
    lines = text.splitlines()
    for y, line in enumerate(lines):
        if regex:
            m = re.search(needle, line)
            if m:
                return (m.start() + (m.end() - m.start()) // 2, y)
        else:
            idx = line.find(needle)
            if idx >= 0:
                return (idx + len(needle) // 2, y)
    return None


def check_expect(expect, focus, text):
    """True when the step's expect is satisfied by focus/screen text.

    `expect` may be a single string or a list of alternatives (any match passes).
    """
    if isinstance(expect, (list, tuple)):
        return any(check_expect(item, focus, text) for item in expect)
    if expect.startswith("focus:"):
        needle = expect[len("focus:"):].split(" (")[0].strip()
        return needle.lower() in focus.lower()
    if expect.startswith("screen:"):
        needle = expect[len("screen:"):].split(" (")[0].strip()
        return needle.lower() in text.lower()
    return expect.lower() in (focus + "\n" + text).lower()


def abort_report(step, screens, actions):
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


def run_step(step, bridge, out):
    keys = step.get("keys") or []
    directive = keys[0] if keys else ""
    name = step.get("name")
    print(f"   directive: {directive or '(poll/none)'}", file=out)

    # poll steps: wait for the expected focus without acting
    if directive == "poll" or directive.startswith("poll"):
        expect = step.get("expect", "")
        for _ in range(POLL_TIMEOUT // max(1, int(step.get("wait", 8)))):
            focus, text = bridge.screen()
            if check_expect(expect, focus, text):
                return [(focus, text)], ["poll"]
            import time
            time.sleep(step.get("wait", 8))
        focus, text = bridge.screen()
        abort_report(step, [(focus, text)], ["poll"])

    screens, actions = [], []
    for _attempt in range(1, MAX_RETRIES + 1):
        focus, text = bridge.screen()
        screens.append((focus, text))
        if directive.startswith("click_re "):
            pat = directive[len("click_re "):]
            centre = label_centre(text, pat, regex=True)
            if centre:
                bridge.click(*centre)
                actions.append(f"click@{centre} /{pat}/")
            else:
                actions.append(f"click_re {pat} -> NOT FOUND")
        elif directive.startswith("click "):
            label = directive[len("click "):].strip().strip('"')
            centre = label_centre(text, label)
            if centre:
                bridge.click(*centre)
                actions.append(f"click@{centre} '{label}'")
            else:
                actions.append(f"click '{label}' -> NOT FOUND")
        elif directive.startswith("key "):
            names = directive[len("key "):].split()
            bridge.keys(names)
            actions.append("key " + " ".join(names))
        else:
            actions.append(f"unhandled: {directive}")

        import time
        time.sleep(step.get("wait", 1.0))
        focus, text = bridge.screen()
        screens.append((focus, text))
        if check_expect(step.get("expect", ""), focus, text):
            print(f"   OK: {step.get('expect')}", file=out)
            return screens, actions
        print(f"   falha (tentativa {_attempt}): expect nao visto", file=out)
    abort_report(step, screens, actions)


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


def main(argv=None):
    args = parse_args(argv)
    steps = load_steps(args.steps)
    if args.dry_run:
        for i, step in enumerate(steps, 1):
            keys = "; ".join(step.get("keys") or [])
            print(f"[{i}/{len(steps)}] {step.get('name')} (guessed={step.get('guessed')})")
            print(f"    do:    {keys or '(poll/none)'}")
            print(f"    wait:  {step.get('wait')}s")
            print(f"    expect:{step.get('expect')}")
        print(f"--- dry-run: {len(steps)} passo(s), jogo NAO tocado ---")
        return 0

    bridge = Bridge(args.df_path, args.port)
    for i, step in enumerate(steps, 1):
        print(f"[{i}/{len(steps)}] {step.get('name')} (guessed={step.get('guessed')})")
        run_step(step, bridge, sys.stdout)
    print("--- fluxo de criacao concluido ---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
