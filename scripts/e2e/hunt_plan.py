#!/usr/bin/env python3
"""Plano de caça estruturado via LLM (DeepSeek) a partir do snapshot REAL.

Filosofia da missão: **LLM planeia · JEV decide · código executa**.

O plano sai SEMPRE em JSON estruturado (schema fixo em ``agent/planning.py``)::

    {
      "plan_version":       1,
      "where_am_i":         "onde estamos (localização/surroundings/pos)",
      "animal_zones":       [{"zone": "...", "why": "...", "waypoint": [x, y],
                              "biome": "..."}],
      "route_steps":        [{"action": "goto|move|wait", "target": [x, y]|"n",
                              "note": "..."}],
      "opportunistic_rule": "o que fazer se aparecer animal no caminho",
      "success_conditions": [{"kind": "kill_confirmed|animal_visible|
                              position_within|steps_elapsed", ...}],
      "assumptions":        ["..."],
      "source":             "llm" | "llm_repaired" | "fallback",
      "planning":           {"attempts": 2, "efforts": [...], "issues": [...]}
    }

e vira input do JEV: o resumo entra no ``state`` do JEV-A/JEV-D (via objetivo)
e os ``waypoints`` viram candidatos ``navigate_to:<waypoint>`` (executor:
``goto X,Y``).

Ciclo gerar → testar → criticar (ARQUITETURA §8, mudança 09): o plano do LLM
passa por críticos **em código** (``planning.generate_validated_plan``); se
falhar, o LLM é chamado outra vez com a lista de problemas (≤ 3 gerações) e, no
fim, entra o plano determinístico de *fallback* — nunca ficamos sem rota. O
esforço de raciocínio é escolhido pelo código (``planning.effort_for``) e vai
sempre no objeto aninhado ``reasoning``. Sem chave ou com ``--no-llm`` não há
chamada nenhuma: sai o *fallback* (offline).

Uso::

    python3 scripts/e2e/hunt_plan.py [--out plano.json] [--context falhas.txt]
        [--max-attempts 3] [--stuck-escalations N] [--no-llm]
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "dfhack" / "df_bridge.py"
DEFAULT_DF = str(Path.home() / "Games" / "dwarf-fortress-53.16")
COALA = ROOT / ".agents" / "dwarf-fortress-game-coala-memory-agent-skill" / "scripts" / "coala.py"

if str(ROOT / "src") not in sys.path:  # o pacote não está instalado no venv
    sys.path.insert(0, str(ROOT / "src"))

from system_one_dwarf.agent import planning  # noqa: E402

#: Prompt de sistema = o schema do plano + as regras (fonte única: ``planning``).
SYSTEM = planning.PLAN_SYSTEM_PROMPT

#: Planeamento é estruturado: menos criatividade do que a narrativa (0.4).
PLAN_TEMPERATURE = planning.PLAN_TEMPERATURE
#: Variáveis de ambiente (por ordem) e ficheiros (relativos a ``~``) com a chave.
KEY_ENV_VARS = ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY")
KEY_FILES = (".dsh/.credentials.yaml", ".secrets")
KEY_PATTERN = re.compile(r"(sk-or-v1-[A-Za-z0-9_-]+)")
SNAPSHOT_TIMEOUT_S = 120
KNOWLEDGE_TIMEOUT_S = 60


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def snapshot(df_path: str) -> dict:
    """Snapshot do jogo pela ponte; ``{}`` se a ponte falhar ou não responder JSON."""
    try:
        proc = subprocess.run(
            [sys.executable, str(BRIDGE), "snapshot", "--json", "--df-path", df_path],
            capture_output=True, text=True, timeout=SNAPSHOT_TIMEOUT_S,
        )
        data = json.loads(proc.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    return data if isinstance(data, dict) else {}


def knowledge_context(limit: int = 3) -> str:
    """Conhecimento do projeto (CoALA search) — citado, nunca obedecido."""
    if not COALA.is_file():
        return ""
    try:
        proc = subprocess.run(
            [sys.executable, str(COALA), "search", "animais caça savana água terreno",
             "--limit", str(limit), "--type", "semantic"],
            capture_output=True, text=True, timeout=KNOWLEDGE_TIMEOUT_S, cwd=str(ROOT),
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()[:1500]


def snapshot_digest(snap: dict) -> dict:
    """Resumo factual do snapshot para o prompt (dados, nunca instruções)."""
    return planning.build_digest(snap)


def fallback_plan(digest: dict) -> dict:
    """Plano determinístico quando o LLM não está disponível/valida."""
    return planning.default_fallback_plan(digest).to_dict()


def validate_plan(plan: dict) -> dict:
    """Garante o esquema mesmo que o LLM divague (normalização tolerante)."""
    return planning.Plan.from_mapping(plan).to_dict()


def find_api_key(environ: Mapping[str, str] | None = None, home: Path | None = None) -> str | None:
    """A chave OpenRouter do ambiente ou dos ficheiros de segredos (nunca logada)."""
    env = os.environ if environ is None else environ
    for name in KEY_ENV_VARS:
        if env.get(name):
            return env[name]
    base = Path.home() if home is None else home
    for rel in KEY_FILES:
        path = (base / rel).expanduser()
        try:
            found = KEY_PATTERN.search(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, UnicodeDecodeError):
            continue
        if found:
            return found.group(1)
    return None


def make_client(key: str | None = None):
    """``DeepSeekLLM`` com a chave dada/descoberta; ``None`` sem chave (offline)."""
    key = key or find_api_key()
    if not key:
        return None
    try:
        from system_one_dwarf.agent.llm import DeepSeekLLM  # noqa: PLC0415
        return DeepSeekLLM(lambda: key)
    except Exception:
        return None


def call_llm(digest: dict, knowledge: str, context: str, *, client: Any = None,
             effort: str = planning.EFFORT_MEDIUM, hints: tuple[str, ...] | list[str] = ()
             ) -> dict | None:
    """Uma chamada avulsa (sem ciclo): o dict cru do LLM, ``None`` se indisponível/erro."""
    client = client if client is not None else make_client()
    if client is None:
        return None
    try:
        user = planning.build_user_prompt(digest, knowledge=knowledge, hints=hints,
                                          context=context)
        result = client.complete(
            SYSTEM, user, reasoning={"effort": effort},
            max_tokens=planning.MAX_TOKENS_BY_EFFORT.get(effort, 2000),
            temperature=PLAN_TEMPERATURE, purpose=planning.PLAN_PURPOSE,
        )
        parsed = planning.parse_json_object(result.text or "")
        return dict(parsed) if parsed is not None else None
    except Exception:
        return None


def build_plan(
    df_path: str = DEFAULT_DF,
    context: str = "",
    *,
    llm: Any = None,
    snap: dict | None = None,
    max_attempts: int = planning.DEFAULT_MAX_ATTEMPTS,
    stuck_escalations: int | None = None,
    offline: bool = False,
    fauna_search: Callable[..., Any] | None = None,
) -> dict:
    """Snapshot → pistas de fauna → ciclo gerar/testar/criticar → plano (dict).

    O esforço de raciocínio sai de ``planning.effort_for`` (código, não o
    modelo) e viaja no objeto aninhado ``reasoning``; sem chave (ou ``offline``)
    o ``HuntPlanner`` não chama ninguém e devolve o *fallback*.
    """
    snap = snapshot(df_path) if snap is None else snap
    digest = snapshot_digest(snap)
    client = None if offline else (llm if llm is not None else make_client())
    if stuck_escalations is None:
        # O vigia só chama este script como 2.ª escalada do stuck (a 1.ª é o
        # movimento forçado por DSL): "stuck" no contexto ⇒ ronda 2 ⇒ esforço máximo.
        stuck_escalations = 2 if "stuck" in context.lower() else 0
    planner = planning.HuntPlanner(
        client, fauna_search=fauna_search, max_attempts=max_attempts,
        fallback_fn=lambda: fallback_plan(digest),
    )
    result = planner.plan(digest, context=context, knowledge=knowledge_context(),
                          stuck_round=stuck_escalations,
                          # Contexto de feedback = já houve plano nesta missão:
                          # só a chamada sem contexto é o 1.º plano (esforço
                          # máximo quando não há conhecimento do mapa).
                          first_plan=not context.strip())
    plan = result.plan.to_dict()
    plan["planning"] = result.summary()
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="onde gravar o plano JSON")
    ap.add_argument("--context", default="", help="feedback de tentativas falhadas")
    ap.add_argument("--df-path", default=DEFAULT_DF)
    ap.add_argument("--max-attempts", type=int, default=planning.DEFAULT_MAX_ATTEMPTS,
                    help="gerações do LLM por plano (depois, fallback determinístico)")
    ap.add_argument("--stuck-escalations", type=int, default=None,
                    help="ronda de stuck que pede este re-plano (StuckEvent.stuck_round; "
                         "2+ = esforço máximo; omitido: 2 se o contexto fala em stuck)")
    ap.add_argument("--no-llm", action="store_true",
                    help="não chama o LLM: plano determinístico (offline)")
    args = ap.parse_args(argv)
    plan = build_plan(args.df_path, args.context, max_attempts=args.max_attempts,
                      stuck_escalations=args.stuck_escalations, offline=args.no_llm)
    text = json.dumps(plan, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
