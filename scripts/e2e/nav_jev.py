#!/usr/bin/env python3
"""Navegação dirigida por JEV sobre a biblioteca de padrões COMPROVADOS.

Fonte de verdade: ``docs/padroes-navegacao.json`` (ecrã → ação). Este script:

1. lê o ecrã real (``act screen --json``) e cruza-o com os padrões da
   biblioteca (marcadores regex sobre ``focus``/``screen_text``);
2. monta os candidatos compatíveis e pede UMA decisão ``choice`` ao
   ``JevClient`` (``typesafe/jev-1.13``) — JEV decide o próximo padrão;
3. banda ``auto`` → executa o padrão pela ponte (as "mãos" do agente);
   bandas ``hitl``/``abstain`` → cai para o **fluxo provado do steps.json**
   (fallback determinístico: primeiro padrão compatível pela ordem do fluxo);
4. cada decisão é contada (JEV vs fallback) para o relatório.

Uso::

    python3 scripts/e2e/nav_jev.py candidates            # candidatos do ecrã
    python3 scripts/e2e/nav_jev.py step [--execute]       # decide (+ executa)
    python3 scripts/e2e/nav_jev.py act "<dsl ponte>"      # executa DSL crua

A app nunca é chamada: a chave sai de ``SOD_OPENROUTER_KEY``/``OPENROUTER_API_KEY``
> ``~/.dsh/.credentials.yaml`` > ``~/.secrets`` (nunca imprimida).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = ROOT / "docs" / "padroes-navegacao.json"
BRIDGE = ROOT / "dfhack" / "df_bridge.py"
DEFAULT_DF = str(Path.home() / "Games" / "dwarf-fortress-53.16")
STATS_ENV = "NAV_JEV_STATS"


# --------------------------------------------------------------------------- #
# biblioteca + ecrã                                                           #
# --------------------------------------------------------------------------- #

def load_library(path: Path = LIBRARY) -> dict:
    """Biblioteca de padrões (fonte de verdade)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("patterns"), list):
        raise ValueError(f"biblioteca inválida em {path}")
    return data


def bridge_screen(df_path: str = DEFAULT_DF) -> tuple[str, str, int]:
    """-> (focus, screen_text, exit_code) do ``act screen --json`` real."""
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), "act", "screen", "--json", "--df-path", df_path],
        capture_output=True, text=True, timeout=120,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {}
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return "", "", proc.returncode
    focus = "/".join(data.get("focus") or [])
    return focus, "\n".join(data.get("lines") or []), proc.returncode


def matches(pattern: dict, focus: str, text: str) -> bool:
    """O padrão aplica-se a este ecrã? (focus markers E text markers)."""
    focus_markers = pattern.get("focus") or []
    text_markers = pattern.get("text_markers") or []
    hay_focus = focus or ""
    hay_text = (focus or "") + "\n" + (text or "")
    if focus_markers and not any(re.search(str(m), hay_focus) for m in focus_markers):
        return False
    if text_markers and not any(re.search(str(m), hay_text) for m in text_markers):
        return False
    return bool(focus_markers or text_markers)


def candidates(library: dict, focus: str, text: str) -> list[dict]:
    """Padrões compatíveis com o ecrã (ordem da biblioteca = ordem do fluxo)."""
    return [p for p in library.get("patterns", []) if matches(p, focus, text)]


# --------------------------------------------------------------------------- #
# JEV: decidir entre candidatos                                              #
# --------------------------------------------------------------------------- #

def build_nav_request(cands: list[dict], focus: str, text: str) -> dict:
    """Request JEV-A reutilizando o builder validado do fleet."""
    sys.path.insert(0, str(ROOT / "src"))
    from system_one_dwarf.jev.fleet import build_action  # noqa: PLC0415

    head = "\n".join((text or "").splitlines()[:6])
    state = (
        f"NAVIGATION SCREEN (adventure-mode DF menus). Focus: {focus or 'unknown'}.\n"
        f"Screen head:\n{head}\n\n"
        "Which proven navigation pattern should run next? "
        "(candidates are proven screen->action patterns from the library)"
    )
    return build_action(
        [
            {
                "id": str(p["id"]),
                "name": f"Pattern {p['id']}: {p['action']}",
                "description": str(p.get("notes") or p.get("proven") or "proven pattern"),
            }
            for p in cands
        ],
        state,
    )


def load_jev_client():
    """JevClient com a chave do ambiente/config (nunca imprimida)."""
    spec = importlib.util.spec_from_file_location(
        "session_live", ROOT / "scripts" / "e2e" / "session_live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    key = mod.find_key()
    if not key:
        return None
    sys.path.insert(0, str(ROOT / "src"))
    from system_one_dwarf.jev import JevClient  # noqa: PLC0415
    return JevClient(key, model="typesafe/jev-1.13")


def decide(cands: list[dict], focus: str, text: str, client=None) -> dict:
    """Decisão JEV → {pattern, band, source} — fallback determinístico sem JEV.

    ``source`` é ``"jev"`` (o JEV escolheu) ou ``"fallback"`` (banda hitl/abstain
    ou JEV indisponível: primeiro candidato pela ordem do fluxo provado).
    """
    if not cands:
        return {"pattern": None, "band": "abstain", "source": "none",
                "reason": "no library pattern matches this screen"}
    client = client if client is not None else load_jev_client()
    if client is None:
        return {"pattern": cands[0], "band": "abstain", "source": "fallback",
                "reason": "JEV indisponível (sem chave) — fluxo provado"}
    request = build_nav_request(cands, focus, text)
    result = client.decide_request(request)
    answer = result.answer("action")
    band = result.band("action")
    chosen = next((p for p in cands if p["id"] == answer.value), None)
    if chosen is not None and band == "auto":
        return {"pattern": chosen, "band": band, "source": "jev",
                "reason": f"JEV-A escolheu {answer.value} (p={answer.probability})"}
    return {"pattern": cands[0], "band": band, "source": "fallback",
            "reason": f"banda {band!r} → fluxo provado (1º candidato)"}


# --------------------------------------------------------------------------- #
# execução (as "mãos": DSL da ponte)                                          #
# --------------------------------------------------------------------------- #

def split_actions(action: str) -> list[str]:
    """``click X; depois click Y`` → ['click X', 'click Y']."""
    parts = re.split(r"\s*;\s*(?:depois\s+)?|\s+depois\s+", action or "")
    return [p.strip() for p in parts if p.strip()]


def action_to_dsl(step: str) -> list[str]:
    """Padrão da biblioteca → comandos da ponte (lista argv por comando).

    Só operações fechadas: ``click "label"`` (centro no screen_text),
    ``click_re PAT``, ``click (x,y)``, ``key NAME``, ``poll`` (noop).
    """
    step = step.strip()
    if step.startswith("click_re "):
        pat = step[len("click_re "):].strip()
        return ["click_re", pat]
    if step.startswith("click ("):
        m = re.match(r"click \((\d+)\s*,\s*(\d+)\)", step)
        if m:
            return ["click_xy", m.group(1), m.group(2)]
    if step.startswith("click "):
        return ["click_label", step[len("click "):].strip().strip('"')]
    if step.startswith("key "):
        return ["key", step[len("key "):].strip()]
    if step.startswith("poll"):
        return ["noop"]
    return ["raw", step]


def run_dsl(cmd: list[str], text: str, df_path: str = DEFAULT_DF) -> tuple[int, str]:
    """Executa um comando de padrão contra o jogo → (exit, resumo)."""
    def batch(dsl: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(BRIDGE), "act", "batch", "--df-path", df_path, dsl],
            capture_output=True, text=True, timeout=180,
        )

    kind = cmd[0]
    if kind == "noop":
        return 0, "noop (poll)"
    if kind == "click_xy":
        proc = batch(f"click {cmd[1]},{cmd[2]}; sleep 600")
        return proc.returncode, f"click {cmd[1]},{cmd[2]}"
    if kind == "key":
        proc = batch(f"key {cmd[1]}; sleep 500")
        return proc.returncode, f"key {cmd[1]}"
    if kind in ("click_label", "click_re", "raw"):
        needle, regex = "", False
        if kind == "click_label":
            needle = cmd[1]
        elif kind == "click_re":
            needle, regex = cmd[1], True
        else:
            return 13, f"dsl não mapeada: {cmd[1]!r}"
        centre = None
        for y, line in enumerate((text or "").splitlines()):
            m = re.search(needle, line) if regex else None
            idx = line.find(needle) if not regex else (m.start() if m else -1)
            if idx >= 0:
                if regex:
                    centre = (m.start() + (m.end() - m.start()) // 2, y)
                else:
                    centre = (idx + len(needle) // 2, y)
                break
        if centre is None:
            return 13, f"label não encontrado: {needle!r}"
        proc = batch(f"click {centre[0]},{centre[1]}; sleep 600")
        return proc.returncode, f"click {centre} {needle!r}"
    return 13, f"comando desconhecido: {cmd!r}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=("candidates", "step", "act"))
    ap.add_argument("arg", nargs="?", default=None, help="dsl crua para `act`")
    ap.add_argument("--execute", action="store_true", help="executa o padrão escolhido")
    ap.add_argument("--df-path", default=DEFAULT_DF)
    ap.add_argument("--library", default=str(LIBRARY))
    args = ap.parse_args(argv)

    if args.command == "act":
        if not args.arg:
            print("falta a DSL", file=sys.stderr)
            return 2
        proc = subprocess.run(
            [sys.executable, str(BRIDGE), "act", "batch", "--df-path", args.df_path, args.arg],
            capture_output=True, text=True, timeout=180,
        )
        print(json.dumps({"exit": proc.returncode, "stdout": proc.stdout[-400:],
                          "stderr": proc.stderr[-200:]}, ensure_ascii=False))
        return proc.returncode

    library = load_library(Path(args.library))
    focus, text, code = bridge_screen(args.df_path)
    cands = candidates(library, focus, text)
    if args.command == "candidates":
        print(json.dumps({
            "exit": code, "focus": focus,
            "candidates": [p["id"] for p in cands],
        }, ensure_ascii=False, indent=2))
        return 0

    verdict = decide(cands, focus, text)
    out = {
        "focus": focus,
        "chosen": (verdict["pattern"] or {}).get("id"),
        "action": (verdict["pattern"] or {}).get("action"),
        "band": verdict["band"],
        "source": verdict["source"],
        "reason": verdict["reason"],
    }
    if args.execute and verdict["pattern"]:
        results = []
        for part in split_actions(verdict["pattern"]["action"]):
            c, summary = run_dsl(action_to_dsl(part), text, args.df_path)
            results.append({"dsl": part, "exit": c, "summary": summary})
            focus, text, _ = bridge_screen(args.df_path)
        out["executed"] = results
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
