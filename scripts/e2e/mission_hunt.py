#!/usr/bin/env python3
"""Driver da MISSÃO DE CAÇA (fresh run): campanha → mundo → aventureiro → caça.

Regras duras desta missão:
* a chave NUNCA é impressa (usa :func:`session_live.find_key`);
* o modo de controlo tem de ficar ``checkpoint``/``autopilot`` — verificado e
  reportado (causa raiz (a) da personagem congelada);
* a PRIMEIRA PROVA DE MOVIMENTO é obrigatória nos primeiros minutos: a posição
  (x/y) tem de MUDAR (antes→depois) por uma movida aprovada pelo loop ou
  executada por DSL;
* cada obstáculo: ≤ 5 tentativas e abordagem alternativa.

Subcomandos::

    python3 scripts/e2e/mission_hunt.py setup        # campanha+chaves+objetivo+controlo
    python3 scripts/e2e/mission_hunt.py new-world    # POST new-world + poll até fim
    python3 scripts/e2e/mission_hunt.py new-char     # POST new-character + poll até fim
    python3 scripts/e2e/mission_hunt.py status       # estado da aventura + modo
    python3 scripts/e2e/mission_hunt.py move-proof   # prova de movimento (DSL goto/move)
    python3 scripts/e2e/mission_hunt.py approve-loop # aprova perguntas pendentes em fila
    python3 scripts/e2e/mission_hunt.py act "<dsl>"  # DSL crua na ponte (as mãos)
    python3 scripts/e2e/mission_hunt.py report       # relatório final da missão
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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
CAMPAIGN_NAME = "caca-2"
STATE_PATH = ROOT / "scripts" / "e2e" / "mission-hunt-state.json"
REPORT_PATH = ROOT / "scripts" / "e2e" / "mission-hunt-report.json"
LOG_PATH = ROOT / "scripts" / "e2e" / "logs" / "mission-hunt.jsonl"

MAX_TRIES = 5  # regra: ≤5 tentativas por obstáculo


# --------------------------------------------------------------------- utils
def _load_session_live():
    spec = importlib.util.spec_from_file_location(
        "session_live", ROOT / "scripts" / "e2e" / "session_live.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def log(event: str, **data) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **data}
    with LOG_PATH.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(f"[{entry['ts']}] {event}: " + json.dumps(data, ensure_ascii=False)[:400])


def state_load() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def state_save(data: dict) -> None:
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")


def api(method: str, path: str, body: dict | None = None, timeout: float = 30.0):
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


def bridge_json(args: list[str], timeout: float = 120.0) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(BRIDGE), *args, "--df-path", DEFAULT_DF],
        capture_output=True, text=True, timeout=timeout,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {"raw": (proc.stdout or "")[:500]}
    return proc.returncode, payload


def snapshot() -> tuple[int, dict]:
    return bridge_json(["snapshot", "--json"])


def snapshot_pos(data: dict) -> tuple[int | None, int | None]:
    # o snapshot é PLANO: location vive em metrics.location (nunca no topo)
    metrics = (data.get("data") or data).get("metrics") or {}
    loc = metrics.get("location") or {}
    x, y = loc.get("x"), loc.get("y")
    return (x if isinstance(x, int) else None, y if isinstance(y, int) else None)


# ------------------------------------------------------------------- comandos
def cmd_setup(_args) -> int:
    sl = _load_session_live()
    key = sl.find_key()
    log("key_status", found=bool(key))
    if not key:
        print("ERRO: sem chave OpenRouter (SOD_OPENROUTER_KEY / ~/.dsh/.credentials.yaml)")
        return 1
    status, body = api("GET", "/api/campaigns")
    cid = None
    for c in (body if isinstance(body, list) else []):
        if c.get("name") == CAMPAIGN_NAME:
            cid = c.get("id")
    if cid is None:
        status, body = api("POST", "/api/campaigns",
                           {"name": CAMPAIGN_NAME, "is_new_game": True})
        if status not in (200, 201):
            log("campaign_create_failed", status=status, body=str(body)[:300])
            return 1
        cid = body.get("id")
    log("campaign", id=cid)
    for provider in ("openrouter", "typesafe"):
        status, body = api("POST", f"/api/campaigns/{cid}/keys",
                           {"provider": provider, "key": key})
        masked = (body or {}).get("masked_key")
        log("key_registered", provider=provider, status=status, masked=masked)
        if status != 200:
            return 1
    narrative = (
        "MISSION: find a wild animal and kill it. Explore productively, hunt "
        "approach, kill it. Humans and other sapient people are PROTECTED: never "
        "attack them. Wild non-sapient animals are the legitimate prey."
    )
    status, body = api("PUT", f"/api/campaigns/{cid}/objective", {"narrative": narrative})
    log("objective", status=status)
    if status != 200:
        return 1
    status, body = api("POST", f"/api/campaigns/{cid}/control",
                       {"mode": "checkpoint", "kill_switch": False})
    log("control", status=status, mode=(body or {}).get("mode"))
    if status != 200:
        return 1
    state = state_load()
    state.update({"campaign_id": cid, "campaign_name": CAMPAIGN_NAME})
    state_save(state)
    # verificação do MODO (causa raiz (a)): tem de ficar checkpoint/autopilot
    status, detail = api("GET", f"/api/campaigns/{cid}")
    mode = ((detail or {}).get("control") or {}).get("mode")
    ok = mode in ("checkpoint", "autopilot")
    log("mode_verification", mode=mode, ok=ok)
    print(f"MODE ACTIVE: {mode!r} (ok={ok})")
    return 0 if ok else 1


def cmd_new_world(_args) -> int:
    st = state_load()
    cid = st.get("campaign_id")
    status, body = api("POST", f"/api/campaigns/{cid}/adventures/new-world",
                       {"params": "quick"}, timeout=60.0)
    log("new_world_posted", status=status, body=str(body)[:200])
    if status != 202:
        return 1
    return _poll_adventure(cid, "world", budget_s=1800)


def cmd_new_char(_args) -> int:
    st = state_load()
    cid = st.get("campaign_id")
    status, body = api("POST", f"/api/campaigns/{cid}/adventures/new-character",
                       {"randomize": True}, timeout=60.0)
    log("new_char_posted", status=status, body=str(body)[:200])
    if status != 202:
        return 1
    return _poll_adventure(cid, "character", budget_s=1500)


def _poll_adventure(cid: str, label: str, budget_s: float) -> int:
    """Poll ``adventures/status`` até a tarefa terminar (≤ budget_s, 5 erros seguidos = abort)."""
    deadline = time.monotonic() + budget_s
    errors = 0
    last = ""
    while time.monotonic() < deadline:
        status, body = api("GET", f"/api/campaigns/{cid}/adventures/status")
        if status != 200:
            errors += 1
            log("poll_error", label=label, status=status, errors=errors)
            if errors >= MAX_TRIES:
                print(f"ERRO: {label} — status endpoint falhou {errors}×")
                return 1
            time.sleep(5)
            continue
        errors = 0
        task = body.get("task") or {}
        summary = {k: task.get(k) for k in ("state", "kind", "step", "steps_total", "error")}
        text = json.dumps(summary, ensure_ascii=False)
        if text != last:
            log("poll", label=label, **{k: v for k, v in summary.items() if v is not None})
            last = text
        state = str(task.get("state") or "").lower()
        if state in ("done", "succeeded", "success", "finished", "error", "failed", "cancelled"):
            log("task_finished", label=label, state=state, body=str(body)[:400])
            if state in ("error", "failed", "cancelled"):
                return 1
            return 0
        time.sleep(6)
    print(f"ERRO: {label} — tempo esgotado ({budget_s}s)")
    return 1


def cmd_status(_args) -> int:
    st = state_load()
    cid = st.get("campaign_id")
    status, detail = api("GET", f"/api/campaigns/{cid}")
    mode = ((detail or {}).get("control") or {}).get("mode")
    status, body = api("GET", f"/api/campaigns/{cid}/adventures/status")
    code, snap = snapshot()
    pos = snapshot_pos(snap)
    print(json.dumps({
        "campaign_id": cid, "mode": mode, "mode_ok": mode in ("checkpoint", "autopilot"),
        "adventure_status": body, "snapshot_exit": code, "pos": pos,
    }, ensure_ascii=False, indent=1)[:2000])
    return 0


def cmd_move_proof(_args) -> int:
    """PROVA DE MOVIMENTO: posição antes→depois de uma movida real (DSL)."""
    code, before = snapshot()
    if code != 0:
        log("move_proof_no_snapshot", exit=code)
        print(f"ERRO: snapshot exit {code} (sem aventureiro?)")
        return 1
    x0, y0 = snapshot_pos(before)
    log("move_proof_before", x=x0, y=y0)
    # 1) goto BFS alguns tiles para norte (contorna paredes); 2) fallback: sonda
    moved = False
    for dsl in ("goto {},{}".format(x0, y0 - 4) if x0 is not None else "",
                "move n times=3", "move s times=3", "move e times=3", "move w times=3"):
        if not dsl:
            continue
        c, payload = bridge_json(["act", "batch", "--stop-on-focus-change",
                                  "--stop-on-threat", "--max-steps", "8",
                                  "--delay", "0.4", dsl])
        log("move_proof_dsl", dsl=dsl, exit=c, ok=str(payload)[:200])
        time.sleep(1.0)
        c2, after = snapshot()
        x1, y1 = snapshot_pos(after)
        log("move_proof_after", dsl=dsl, x=x1, y=y1, exit=c2)
        if (x1, y1) != (x0, y0) and x1 is not None:
            moved = True
            break
    print(f"MOVEMENT PROOF: ({x0},{y0}) -> ({x1},{y1}) moved={moved}")
    log("move_proof_result", moved=moved, before=(x0, y0), after=(x1, y1))
    return 0 if moved else 1


def cmd_approve_loop(_args) -> int:
    """Aprova a fila de perguntas pendentes (liberta ações presas no checkpoint)."""
    st = state_load()
    cid = st.get("campaign_id")
    status, body = api("GET", f"/api/campaigns/{cid}/questions?limit=50")
    items = body if isinstance(body, list) else (body or {}).get("questions", [])
    released = 0
    for q in items:
        if not isinstance(q, dict):
            continue
        qid = q.get("id") or q.get("question_id")
        answered = q.get("answered") or q.get("status") in ("answered", "resolved")
        if not qid or answered:
            continue
        opts = q.get("options") or []
        option_id = None
        for opt in opts:
            label = str((opt or {}).get("label") or (opt or {}).get("text") or "").lower()
            opt_id = (opt or {}).get("id")
            if any(w in label for w in ("approv", "execut", "sim", "yes", "allow", "libert",
                                        "proceed", "go ahead", "do it")):
                option_id = opt_id
                break
        if option_id is None and opts:
            option_id = opts[0].get("id")
        st_code, resp = api("POST", f"/api/campaigns/{cid}/questions/{qid}/answer",
                            {"option_id": option_id} if option_id is not None else {})
        log("question_answered", qid=qid, status=st_code, option=option_id)
        if st_code == 200:
            released += 1
    print(f"approved {released} pending question(s)")
    return 0


def cmd_act(args) -> int:
    dsl = args.dsl
    c, payload = bridge_json(["act", "batch", "--stop-on-focus-change",
                              "--stop-on-threat", "--max-steps", "12",
                              "--delay", "0.4", dsl])
    print(json.dumps({"exit": c, "payload": payload}, ensure_ascii=False)[:1500])
    return 0 if c == 0 else 1


def cmd_report(_args) -> int:
    st = state_load()
    code, snap = snapshot()
    data = snap.get("data") or snap
    out = {
        "campaign": st, "snapshot_exit": code, "pos": snapshot_pos(snap),
        "units_nearby": str((data.get("metrics") or {}).get("units_nearby"))[:600],
        "gamelog_tail": str((data.get("gamelog") or data.get("events")))[-800:],
    }
    REPORT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1)[:2500])
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("setup", "new-world", "new-char", "status", "move-proof",
                 "approve-loop", "report"):
        sub.add_parser(name)
    p_act = sub.add_parser("act")
    p_act.add_argument("dsl")
    args = ap.parse_args()
    handler = {
        "setup": cmd_setup, "new-world": cmd_new_world, "new-char": cmd_new_char,
        "status": cmd_status, "move-proof": cmd_move_proof,
        "approve-loop": cmd_approve_loop, "act": cmd_act, "report": cmd_report,
    }[args.cmd]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
