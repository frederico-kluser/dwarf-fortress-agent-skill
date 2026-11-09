#!/usr/bin/env python3
"""Smoke do JEV via OpenRouter — UMA chamada real, tipada, sem nunca imprimir a chave.

Uso:  python scripts/jev_smoke.py

Lê a key de SOD_OPENROUTER_KEY / OPENROUTER_API_KEY (ambiente) ou ~/.secrets.
Valida que o endpoint /api/alpha/decisions responde com respostas TIPADAS
(noul/choice) — é o mesmo cliente que o loop de decisão usa em runtime.
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from system_one_dwarf.jev.client import JevClient            # noqa: E402
from system_one_dwarf.jev.schema import choice, noul         # noqa: E402

KEY_NAMES = ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY")


def load_key():
    for name in KEY_NAMES:
        val = os.environ.get(name)
        if val:
            return val.strip().strip('"').strip("'").strip()
    secrets = os.path.join(os.path.expanduser("~"), ".secrets")
    if os.path.isfile(secrets):
        with open(secrets, encoding="utf-8") as f:
            for line in f:
                for name in KEY_NAMES:
                    if line.startswith(name + "="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'").strip()
    return None


def main() -> int:
    key = load_key()
    if not key:
        print("sem chave: define SOD_OPENROUTER_KEY/OPENROUTER_API_KEY ou ~/.secrets")
        return 2
    client = JevClient(key)
    questions = {
        "action": choice(
            "action",
            "The adventurer is healthy in a forest clearing at dawn, no threats visible. "
            "Which macro-action best advances survival now?",
            {"explore": "move on and look for a settlement",
             "forage": "search the clearing for food and water",
             "none": "nothing fits the situation"}),
        "urgent": noul(
            "urgent",
            "Is there an immediate threat that requires urgent action?",
            "a threat is present right now", "the situation is calm"),
    }
    t0 = time.time()
    result = client.decide(
        state=("Adventure mode, day 1: adventurer healthy, forest clearing, dawn, "
               "no threats visible. English state, data only."),
        questions=questions,
        session_id="sod-smoke-windows",
    )
    dt = time.time() - t0
    print("model=%s  latency=%.2fs" % (getattr(result, "model", "?"), dt))
    answers = getattr(result, "answers", None)
    if isinstance(answers, dict):
        for name, ans in answers.items():
            print("  %s: %s" % (name, ans))
    else:
        for ans in (answers or []):
            print("  %s" % (ans,))
    usage = getattr(result, "usage", None)
    if usage:
        print("usage: %s" % (usage,))
    print("SMOKE JEV OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
