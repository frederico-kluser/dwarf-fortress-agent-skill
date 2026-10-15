#!/usr/bin/env python3
"""Vigia de CAÇA: aprova as perguntas do loop, vigia a presa e deteta a morte.

Filosofia: **LLM planeia · JEV decide · código executa** — este vigia é o
operador humano da arquitetura: liberta as decisões do loop (checkpoint),
valida cada movimento pelo snapshot e só intervém por DSL quando o loop não
consegue (regra das ≤5 tentativas por obstáculo).

* aprova fila de perguntas com a opção mais orientada à caça (palavras-chave);
* valida progresso (posição muda) e deteta **stuck** (3 validações sem mudança)
  → força movimento por DSL em rumo não visitado → 2.ª vez = re-plano LLM
  (``hunt_plan.py --context``) → nunca repete a ação que falhou;
* oportunismo: animal não-sapiente a ≤ 8 tiles sem pergunta de ataque do loop
  → ataque *bump* por DSL (aproximação + passo para o tile da presa);
* PROVA DE MORTE: entrada no gamelog ("slain/killed/dies/…") + unidades
  antes→depois → relatório e saída 0.

Uso: ``python3 scripts/e2e/hunt_watch.py [--minutes 40]``
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "dfhack" / "df_bridge.py"
DEFAULT_DF = str(Path.home() / "Games" / "dwarf-fortress-53.16")
BASE = "http://127.0.0.1:8000"
LOG_PATH = ROOT / "scripts" / "e2e" / "logs" / "mission-hunt.jsonl"
REPORT_PATH = ROOT / "scripts" / "e2e" / "mission-hunt-report.json"
PLAN_PATH = ROOT / "scripts" / "e2e" / "hunt-plan-live.json"
STATE_PATH = ROOT / "scripts" / "e2e" / "mission-hunt-state.json"

DEATH_WORDS = ("slain", "struck down", "killed", "killed by", "dies", "died",
               "dead", "falls", "butcher", "no longer")
ANIMAL_WORDS = ("rabbit", "hare", "mule", "deer", "boar", "bird", "fox", "wolf",
                "dog", "cat", "cattle", "cow", "sheep", "goat", "horse", "donkey",
                "animal", "doe", "buck", "sow", "badger", "beaver")
HUNT_FORWARD = ("procurar", "caça", "caca", "presa", "rastro", "seguir", "explorar",
                "sul", "atacar", "matar", "abater", "animal", "matagal", "vegetação",
                "search", "hunt", "prey", "attack", "kill", "explore", "tracks")
HUNT_BACK = ("esperar", "ficar parado", "voltar", "perguntar", "falar com", "conversar",
             "wait", "go back", "ask ", "talk")


def log(event: str, **data) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **data}
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[{entry['ts']}] {event}: " + json.dumps(data, ensure_ascii=False)[:400],
          flush=True)


def api(method: str, path: str, body: dict | None = None, timeout: float = 25.0):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            raw = res.read().decode()
            return res.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw) if raw.strip() else {}
        except ValueError:
            return exc.code, {"detail": raw[:300]}
    except Exception as exc:  # rede: o vigia sobrevive
        return 0, {"detail": f"{type(exc).__name__}: {exc}"}


def bridge_json(args: list[str], timeout: float = 150.0) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), *args, "--df-path", DEFAULT_DF],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {"raw": (proc.stdout or "")[-400:]}
    return proc.returncode, payload


def snapshot() -> tuple[int, dict]:
    code, payload = bridge_json(["snapshot", "--json"])
    return code, payload if isinstance(payload, dict) else {}


def snap_view(data: dict) -> dict:
    metrics = data.get("metrics") or {}
    loc = metrics.get("location") or {}
    units = (metrics.get("units_nearby") or {}).get("units") or []
    return {
        "pos": [loc.get("x"), loc.get("y"), loc.get("z")],
        "surroundings": loc.get("surroundings"),
        "units": [{"name": u.get("name"), "kind": u.get("kind"), "distance": u.get("distance"),
                   "direction": u.get("direction"), "pos": u.get("pos"), "hostile": u.get("hostile")}
                  for u in units],
        "gamelog": [e.get("text") if isinstance(e, dict) else str(e)
                    for e in (data.get("gamelog_events") or [])][-25:],
    }


def pick_option(question: dict) -> str | None:
    options = question.get("options") or []
    best, best_score = None, -99
    for opt in options:
        text = str(opt.get("text") or "").lower()
        score = sum(2 for word in HUNT_FORWARD if word in text)
        score -= sum(3 for word in HUNT_BACK if word in text)
        if score > best_score:
            best, best_score = opt.get("id"), score
    return best


def dsl(plan: str) -> tuple[int, dict]:
    return bridge_json(["act", "batch", "--stop-on-focus-change", "--stop-on-threat",
                        "--max-steps", "10", "--delay", "0.4", "--json", plan])


def load_stuck_module():
    sys.path.insert(0, str(ROOT / "src"))
    import system_one_dwarf.agent.stuck as stuck  # noqa: PLC0415  (arranque tardio)
    return stuck


def record_stuck(stuck_mod, kind: str, escalation: str, detail: str, **extra) -> None:
    event = stuck_mod.StuckEvent(kind, escalation, detail, **extra)
    stuck_mod.append_pattern_event(None, event)  # biblioteca de padrões
    log("stuck_event", **event.as_dict())


def bump_attack(target: dict, my_pos: list) -> tuple[int, dict]:
    """Ataque *bump* (≤ 5 golpes): goto até à adjacência + passo para o tile da presa."""
    pos = target.get("pos") or {}
    x, y = pos.get("x"), pos.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return 1, {"detail": "sem coordenadas do alvo"}
    code, rep = dsl(f"goto {x},{y}")
    px, py = (my_pos or [None, None])[:2]
    if not isinstance(px, int) or not isinstance(py, int):
        return code, rep
    dx, dy = x - px, y - py
    if max(abs(dx), abs(dy)) <= 1 and (dx or dy):
        direction = {(0, -1): "n", (0, 1): "s", (1, 0): "e", (-1, 0): "w",
                     (1, -1): "ne", (-1, -1): "nw", (1, 1): "se", (-1, 1): "sw"}[(dx, dy)]
        return dsl(f"move {direction} times=1")  # passo para o tile = golpe
    return code, rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--minutes", type=float, default=40.0)
    ap.add_argument("--poll", type=float, default=8.0)
    args = ap.parse_args()
    state = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    cid = state.get("campaign_id")
    if not cid:
        print("ERRO: sem campaign_id em mission-hunt-state.json")
        return 1
    stuck_mod = load_stuck_module()
    deadline = time.monotonic() + args.minutes * 60
    before = None
    unchanged = 0
    stuck_rounds = 0
    tried_dirs: list[str] = []
    answered = 0
    last_pos = None
    kill = None
    view: dict | None = None

    code, payload = snapshot()
    before = snap_view(payload) if code == 0 else {"pos": [None] * 3, "units": [], "gamelog": []}
    log("hunt_start", pos=before["pos"],
        animals=[u for u in before["units"] if u.get("kind") == "animal"])
    seen_death_lines: set[str] = set(before.get("gamelog") or ())

    while time.monotonic() < deadline:
        # ---- 1. libertar as decisões do loop (checkpoint) -----------------
        status, body = api("GET", f"/api/campaigns/{cid}/questions?limit=25")
        items = body if isinstance(body, list) else []
        for q in items:
            if not isinstance(q, dict) or q.get("status") not in (None, "pending"):
                continue
            qid = q.get("id")
            if not qid:
                continue
            option = pick_option(q)
            st, resp = api("POST", f"/api/campaigns/{cid}/questions/{qid}/answer",
                           {"option_id": option} if option else {})
            if st == 200:
                answered += 1
                log("answered", qid=qid, option=option,
                    action=(q.get("action_id") or "")[:40],
                    text=(q.get("question") or "")[:90])

        # ---- 2. snapshot + validação --------------------------------------
        code, payload = snapshot()
        if code != 0:
            log("snapshot_error", exit=code)
            time.sleep(args.poll)
            continue
        view = snap_view(payload)
        animals = [u for u in view["units"]
                   if u.get("kind") == "animal"
                   or any(w in str(u.get("name") or "").lower() for w in ANIMAL_WORDS)]

        # ---- 3. PROVA DE MORTE -------------------------------------------
        new_lines = [line for line in (view.get("gamelog") or [])
                     if line not in seen_death_lines]
        seen_death_lines.update(new_lines)
        death = [line for line in new_lines
                 if any(w in line.lower() for w in DEATH_WORDS)
                 and any(a in line.lower() for a in ANIMAL_WORDS)]
        if death:
            kill = {"gamelog": death, "units_after": view["units"],
                    "units_before": before["units"], "pos": view["pos"],
                    "answered_questions": answered}
            log("KILL_DETECTED", **kill)
            REPORT_PATH.write_text(json.dumps({
                "result": "KILL", "kill": kill, "view": view,
                "started": before, "answered": answered,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            return 0

        # ---- 4. validação de progresso / stuck ---------------------------
        if view["pos"] == last_pos:
            unchanged += 1
        else:
            unchanged = 0
            stuck_rounds = 0
        last_pos = view["pos"]

        # ---- 5. oportunismo: presa perto sem ataque do loop ---------------
        close_prey = [u for u in animals if isinstance(u.get("distance"), (int, float))
                      and u["distance"] <= 8]
        if close_prey:
            prey = sorted(close_prey, key=lambda u: u["distance"] or 99)[0]
            log("opportunistic_prey", prey=prey)
            st, qbody = api("GET", f"/api/campaigns/{cid}/questions?limit=10")
            attack_q = [q for q in (qbody if isinstance(qbody, list) else [])
                        if str((q or {}).get("action_id", "")).startswith("attack")]
            if not attack_q:
                rc, rep = bump_attack(prey, view["pos"])
                log("dsl_bump_attack", prey=prey.get("name"), exit=rc,
                    report=str(rep)[:250])

        # ---- 6. stuck → escada: forçar exploração → re-plano LLM ----------
        if unchanged >= 3:
            stuck_rounds += 1
            if stuck_rounds % 2 == 1:
                for direction in ("n", "e", "s", "w", "ne", "nw", "se", "sw"):
                    if direction not in tried_dirs:
                        tried_dirs.append(direction)
                        break
                else:
                    tried_dirs.clear()
                    direction = "n"
                x, y = (view["pos"] or [None, None])[:2]
                plan = f"goto {x},{y - 5}" if isinstance(x, int) else f"move {direction} times=4"
                rc, rep = dsl(plan)
                record_stuck(stuck_mod, "stuck", "force_explore",
                             f"watcher: position unchanged for {unchanged} polls — DSL move {plan!r}",
                             direction=direction, repeat=unchanged)
                log("forced_move", plan=plan, exit=rc, report=str(rep)[:200])
            else:
                rc = subprocess.run(
                    [sys.executable, str(ROOT / "scripts" / "e2e" / "hunt_plan.py"),
                     "--out", str(PLAN_PATH), "--context", f"stuck at {view['pos']}",
                     # Cache de planos (Passo 19): re-planos com o mesmo estado
                     # normalizado + objetivo saem do cache, sem tokens.
                     "--cache", str(ROOT / ".runtime" / "hunt-plan-cache.json")],
                    capture_output=True, text=True, timeout=200).returncode
                record_stuck(stuck_mod, "stuck", "replan",
                             f"watcher: still stuck (round {stuck_rounds}) — LLM hunt re-plan",
                             repeat=unchanged, stuck_round=stuck_rounds)
                log("llm_replan", exit=rc)
            unchanged = 0

        time.sleep(args.poll)

    REPORT_PATH.write_text(json.dumps({
        "result": "TIMEOUT", "last_view": view if code == 0 else None,
        "started": before, "answered": answered,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log("hunt_timeout", answered=answered, pos=(view or {}).get("pos") if code == 0 else None)
    return 1


if __name__ == "__main__":
    sys.exit(main())
