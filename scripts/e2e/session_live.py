#!/usr/bin/env python3
"""System One Dwarf — lançador de sessão LIVE (um único comando).

Corre a sessão completa contra o jogo REAL, sem nunca dirigir os menus do jogo
(só leitura pela ponte `df_bridge.py`; quem executa ações é o AgentLoop do
backend). Fases:

1. **Serviços** — backend (`/api/health`) e frontend (porta 5173): se estiverem
   em baixo, sobem detached (`.venv/bin/python -m uvicorn system_one_dwarf.app.main:app
   --host 127.0.0.1 --port 8000` e `cd web && npm run dev`) com espera ≤ 30 s.
2. **Chave OpenRouter** — `SOD_OPENROUTER_KEY` / `OPENROUTER_API_KEY`, senão
   extração **não-displaying** de `~/.dsh/.credentials.yaml` ou `~/.secrets`.
   Imprime apenas `chave: encontrada|ausente` — o valor NUNCA é impresso (nem em
   relatórios de erro: tudo passa por :func:`scrub`).
3. **Campanha** — cria ou anexa `campanha-e2e` → `POST .../keys` (a mesma chave
   como `openrouter` **e** `typesafe` — o gate do LoopRunner exige ambas) →
   `PUT .../objective` → `POST .../control {"mode": "checkpoint", "kill_switch": false}`.
4. **Jogo + vigia** — sondagens de `df_bridge.py snapshot --json --df-path <df>`
   cada 5 s; quando o snapshot sai com exit 0 **existe aventureiro**: valida os
   8 grupos de métricas (snapshot + API) com rótulos PT e passa a VIGIAR a
   sessão — `GET .../decisions|questions|approvals|spend` cada 3 s, auto-aprova
   com `--auto-approve`, imprime resumo PT ao vivo e escreve o relatório final
   `scripts/e2e/relatorio-sessao.json` (contagem de decisões, bandas, gasto,
   perguntas e episódios de memória via `GET .../memory`).

Regras duras: cada passo tenta no máximo 5 vezes e depois ABORTA com relatório
PT completo; Ctrl-C pausa a sessão (`mode=paused`) e sai com 0; a chave nunca é
impressa; o launcher nunca envia teclas/ações ao jogo.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BRIDGE_PATH = ROOT / "dfhack" / "df_bridge.py"
LOG_DIR = ROOT / "scripts" / "e2e" / "logs"
REPORT_PATH = ROOT / "scripts" / "e2e" / "relatorio-sessao.json"

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_CAMPAIGN = "campanha-e2e"
DEFAULT_DF_PATH = "~/Games/dwarf-fortress-53.16"
DEFAULT_OBJECTIVE = (
    "Survive and explore as an adventurer. Immediate goal: reach safety and rest."
)
WEB_PORT = 5173

HTTP_TIMEOUT = 10.0
MAX_RETRIES = 5
RETRY_SLEEP = 0.5
HEALTH_TIMEOUT = 30.0
BRIDGE_TIMEOUT = 60.0
GAME_POLL = 5.0
WATCH_POLL = 3.0

# Os 8 grupos de métricas do SNAPSHOT (ordem de `game/models.py:METRIC_GROUPS`)
# e os 8 grupos do contrato REST (`docs/api.md` "GET .../metrics") — rótulos PT.
SNAPSHOT_GROUPS_PT = {
    "health_injuries": "saúde/ferimentos",
    "needs_state": "necessidades (fome/sede/sono)",
    "inventory": "inventário",
    "threats": "ameaças",
    "location": "localização",
    "game_clock": "relógio do jogo",
    "units_nearby": "unidades próximas",
    "events": "eventos",
}
API_GROUPS_PT = {
    "health": "saúde",
    "needs": "necessidades",
    "inventory": "inventário",
    "threats": "ameaças",
    "location": "localização",
    "objective_progress": "progresso do objetivo",
    "combat_history": "histórico de combate",
    "agent": "agente (métricas)",
}

# Segredos vivos: valores registados aqui são sempre scrubbed antes de impressão.
_SECRETS: list[str] = []
# Passos já concluídos (entram no relatório de aborto).
STEPS_DONE: list[str] = []

# Símbolos injetáveis (os testes substituem-nos; zero rede/jogo nos testes).
sleep = time.sleep


# --------------------------------------------------------------------- segredos
def scrub(text):
    """Substitui qualquer segredo conhecido por ``•••`` — nunca vazar em claro."""
    out = str(text)
    for secret in _SECRETS:
        if secret:
            out = out.replace(secret, "••••••")
    return out


def _remember_secret(value):
    if isinstance(value, str) and value.strip():
        _SECRETS.append(value.strip())
    return value


# ------------------------------------------------------------------------ aborto
def abort(step, alvo, tentativas, erros):
    """Imprime o relatório PT COMPLETO de aborto e termina com SystemExit(1)."""
    print("", file=sys.stderr)
    print("=== RELATÓRIO DE ABORTO (System One Dwarf) ===", file=sys.stderr)
    print(f"Passo: {step}", file=sys.stderr)
    print(f"Alvo:  {alvo}", file=sys.stderr)
    print(f"Tentativas: {tentativas}/{MAX_RETRIES}", file=sys.stderr)
    print("Erros registados:", file=sys.stderr)
    for e in erros or ["(sem detalhe)"]:
        print(f"  - {scrub(e)}", file=sys.stderr)
    print("Passos já concluídos:", file=sys.stderr)
    if STEPS_DONE:
        for i, done in enumerate(STEPS_DONE, 1):
            print(f"  {i}. {done}", file=sys.stderr)
    else:
        print("  (nenhum)", file=sys.stderr)
    print("ABORTADO: a sessão não continuou. Corrige o problema e volta a correr.",
          file=sys.stderr)
    raise SystemExit(1)


def _done(step):
    STEPS_DONE.append(step)


# -------------------------------------------------------------------------- http
def _send(method, url, payload=None):
    """Transporte HTTP (httpx do venv do repo). Devolve ``(status, body)``.

    Só erros de TRANSPORTE levantam exceção; respostas HTTP (4xx/5xx) voltam
    como ``(status, body)`` para o chamador decidir.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - o venv do repo tem httpx
        raise RuntimeError("httpx indisponível (usa .venv/bin/python): %s" % exc)
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    resp = httpx.request(method, url, content=data, headers=headers,
                         timeout=HTTP_TIMEOUT)
    raw = resp.content or b""
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        body = {"raw": raw.decode("utf-8", "replace")}
    return resp.status_code, body


def request(method, url, payload=None, step="http", ok=(200, 201, 204),
            abort_on_fail=True, retries=MAX_RETRIES):
    """Um passo HTTP com retry ≤ 5; transport/5xx repetem, 4xx voltam ao chamador."""
    erros = []
    for attempt in range(1, retries + 1):
        try:
            status, body = _send(method, url, payload)
        except Exception as exc:  # noqa: BLE001 - qualquer erro de transporte
            erros.append(f"tentativa {attempt}: {scrub(exc)}")
            if attempt < retries:
                sleep(RETRY_SLEEP * attempt)
                continue
            break
        if status >= 500 and attempt < retries:
            erros.append(f"tentativa {attempt}: HTTP {status}")
            sleep(RETRY_SLEEP * attempt)
            continue
        return {"status": status, "body": body}
    if abort_on_fail:
        abort(step, f"{method} {url}", retries, erros)
    return None


def expect_ok(res, step, method, url, ok=(200, 201, 204)):
    if res is None or res.get("status") not in ok:
        status = None if res is None else res.get("status")
        abort(step, f"{method} {url}", MAX_RETRIES,
              [f"HTTP {status} fora de {sorted(ok)}"])
    return res


# -------------------------------------------------------------------- serviços
def port_open(port, hosts=("127.0.0.1", "::1")):
    """True se a porta TCP do loopback aceitar ligação (IPv4 ou IPv6)."""
    for host in hosts:
        try:
            with socket.create_connection((host, port), timeout=0.5):
                return True
        except OSError:
            continue
    return False


def spawn_detached(cmd, *, cwd, log_name):
    """Arranca um processo detached (grupo próprio) a anexar a um log do repo."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / log_name
    env = os.environ.copy()
    # O loop do backend lê o jogo via DF_PATH: garantir o override NON-Steam.
    env.setdefault("DF_PATH", str(Path(DEFAULT_DF_PATH).expanduser()))
    handle = open(log_path, "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
            stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
        )
    finally:
        handle.close()
    return proc.pid


def _health_ok(api):
    res = request("GET", api + "/api/health", step="verificar /api/health",
                  abort_on_fail=False, retries=1)
    return bool(res and res.get("status") == 200)


def ensure_backend(api):
    """Garante o backend em 127.0.0.1:8000 (sobe detached se preciso; espera ≤ 30 s)."""
    print(f"-> backend: a verificar {api}/api/health ...")
    if _health_ok(api):
        print("   OK: backend já está de pé")
        _done("backend pronto (já corria)")
        return True
    print("   backend em baixo — a arrancar uvicorn (detached) ...")
    host, port = _host_port(api)
    py = ROOT / ".venv" / "bin" / "python"
    python = str(py) if py.exists() else sys.executable
    pid = spawn_detached(
        [python, "-m", "uvicorn", "system_one_dwarf.app.main:app",
         "--host", host, "--port", str(port)],
        cwd=ROOT, log_name="backend.log",
    )
    print(f"   uvicorn arrancado (pid {pid}); a aguardar /api/health (≤ 30 s) ...")
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if _health_ok(api):
            print("   OK: backend saudável")
            _done("backend arrancado pelo launcher")
            return True
        sleep(1.0)
    abort("arrancar backend", f"GET {api}/api/health", MAX_RETRIES,
          [f"backend não respondeu em {HEALTH_TIMEOUT:.0f}s"])
    return False  # inalcançável


def ensure_frontend(port=WEB_PORT):
    """Garante o frontend Vite (sobe `npm run dev` detached se a porta estiver fechada)."""
    print(f"-> frontend: a verificar porta {port} ...")
    if port_open(port):
        print("   OK: frontend já está de pé")
        _done("frontend pronto (já corria)")
        return True
    print("   frontend em baixo — a arrancar `npm run dev` (detached) ...")
    pid = spawn_detached(["npm", "run", "dev"], cwd=ROOT / "web",
                         log_name="frontend.log")
    print(f"   npm run dev arrancado (pid {pid}); a aguardar porta {port} (≤ 30 s) ...")
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        if port_open(port):
            print("   OK: frontend a servir")
            _done("frontend arrancado pelo launcher")
            return True
        sleep(1.0)
    abort("arrancar frontend", f"porta TCP {port}", MAX_RETRIES,
          [f"porta {port} não abriu em {HEALTH_TIMEOUT:.0f}s"])
    return False  # inalcançável


def _host_port(api):
    """(host, port) de uma URL tipo http://127.0.0.1:8000."""
    m = re.match(r"^(?:https?://)?([^/:]+)(?::(\d+))?/?$", api)
    if not m:
        return "127.0.0.1", 8000
    return m.group(1), int(m.group(2) or 80)


# ------------------------------------------------------------------------ chave
_KEY_LINE = re.compile(
    r"^\s*(?:export\s+)?(?P<name>[A-Za-z0-9_]*OPENROUTER[A-Za-z0-9_]*)"
    r"\s*[:=]\s*(?P<value>.+?)\s*$"
)


def _clean_value(raw):
    value = (raw or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    if " #" in value:  # comentário final sem aspas
        value = value.split(" #", 1)[0].strip()
    return value or None


def _extract_from_text(text):
    """Primeira chave OpenRouter do texto (env-style `X=y` ou YAML `X: y`)."""
    for line in (text or "").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = _KEY_LINE.match(line)
        if not m:
            continue
        value = _clean_value(m.group("value"))
        if value and not value.startswith("#"):
            return value
    return None


def find_key(env=None, home=None):
    """Chave OpenRouter, SEMPRE sem a imprimir.

    Precedência: env ``SOD_OPENROUTER_KEY`` → env ``OPENROUTER_API_KEY`` →
    ``~/.dsh/.credentials.yaml`` → ``~/.secrets``. Devolve ``None`` se não houver.
    """
    environ = os.environ if env is None else env
    for name in ("SOD_OPENROUTER_KEY", "OPENROUTER_API_KEY"):
        value = _clean_value(environ.get(name))
        if value:
            return _remember_secret(value)
    base = Path(home) if home is not None else Path.home()
    for rel in (".dsh/.credentials.yaml", ".secrets"):
        path = base / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        value = _extract_from_text(text)
        if value:
            return _remember_secret(value)
    return None


# ---------------------------------------------------------------------- campanha
def ensure_campaign(base, name):
    """Cria (ou anexa) a campanha pelo nome; devolve o ``campaign_id``."""
    print(f"-> campanha {name!r}: a procurar (criar ou anexar) ...")
    lst = request("GET", base + "/api/campaigns", step="listar campanhas")
    expect_ok(lst, "listar campanhas", "GET", base + "/api/campaigns", ok=(200,))
    body = lst.get("body")
    for c in (body if isinstance(body, list) else body.get("campaigns", [])):
        if isinstance(c, dict) and c.get("name") == name:
            cid = c.get("id") or c.get("campaign_id")
            if cid is not None:
                print(f"   OK: campanha existente (anexada) id={cid}")
                _done(f"campanha {name!r} anexada (id={cid})")
                return cid
    res = request("POST", base + "/api/campaigns",
                  {"name": name, "is_new_game": True}, step="criar campanha")
    expect_ok(res, "criar campanha", "POST", base + "/api/campaigns")
    rbody = res.get("body") or {}
    cid = rbody.get("id") or rbody.get("campaign_id")
    if cid is None and isinstance(rbody.get("campaign"), dict):
        cid = rbody["campaign"].get("id") or rbody["campaign"].get("campaign_id")
    if cid is None:
        abort("criar campanha", f"POST {base}/api/campaigns", MAX_RETRIES,
              ["resposta sem id de campanha"])
    print(f"   OK: campanha criada id={cid}")
    _done(f"campanha {name!r} criada (id={cid})")
    return cid


def register_keys(base, cid, key, providers=("openrouter", "typesafe")):
    """Regista a MESMA chave nos providers exigidos pelo gate do LoopRunner."""
    if not key:
        print("-> sem chave: a continuar sem registar (loop ficará parado)")
        return []
    done = []
    for provider in providers:
        res = request("POST", f"{base}/api/campaigns/{cid}/keys",
                      {"provider": provider, "key": key},
                      step=f"registar chave {provider}")
        expect_ok(res, f"registar chave {provider}",
                  "POST", f"{base}/api/campaigns/{cid}/keys")
        masked = (res.get("body") or {}).get("masked_key")
        print(f"   OK: chave {provider} registada (mascarada: {masked!r})")
        done.append(provider)
    _done("chave registada (" + ", ".join(done) + ")")
    return done


def set_objective(base, cid, narrative):
    res = request("PUT", f"{base}/api/campaigns/{cid}/objective",
                  {"narrative": narrative}, step="definir objetivo")
    expect_ok(res, "definir objetivo", "PUT",
              f"{base}/api/campaigns/{cid}/objective")
    print("   OK: objetivo definido")
    _done("objetivo definido")
    return res.get("body") or {}


def set_control(base, cid, mode, kill_switch=False, abort_on_fail=True):
    res = request("POST", f"{base}/api/campaigns/{cid}/control",
                  {"mode": mode, "kill_switch": bool(kill_switch)},
                  step=f"control (mode={mode})", abort_on_fail=abort_on_fail)
    if res is None or res.get("status") not in (200, 201, 204):
        if abort_on_fail:
            status = None if res is None else res.get("status")
            abort(f"control (mode={mode})",
                  f"POST {base}/api/campaigns/{cid}/control", MAX_RETRIES,
                  [f"HTTP {status}"])
        return res
    print(f"   OK: control mode={mode} kill_switch={bool(kill_switch)}")
    _done(f"control mode={mode}")
    return res.get("body") or {}


# ------------------------------------------------------------------------ ponte
def run_bridge_snapshot(df_path):
    """`python3 dfhack/df_bridge.py snapshot --json --df-path <df>` (só leitura).

    Devolve ``{"exit": int, "payload": dict|None, "raw": str, "stderr": str}``.
    """
    cmd = ["python3", str(BRIDGE_PATH), "snapshot", "--json",
           "--df-path", str(df_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          timeout=BRIDGE_TIMEOUT, cwd=str(ROOT))
    out = (proc.stdout or "").strip()
    payload = None
    try:
        parsed = json.loads(out) if out else None
        if isinstance(parsed, dict):
            payload = parsed
    except ValueError:
        payload = None
    return {"exit": proc.returncode, "payload": payload, "raw": out,
            "stderr": (proc.stderr or "").strip()}


def await_adventurer(df_path, interval=GAME_POLL, max_polls=0):
    """Loop de saúde DO JOGO: exit 0 = existe aventureiro. Exit 13 = a aguardar."""
    print(f"-> jogo: a sondar snapshot cada {interval:.0f}s "
          f"(--df-path {df_path}) até existir aventureiro ...")
    polls = 0
    falhas = 0
    erros = []
    while True:
        polls += 1
        try:
            res = run_bridge_snapshot(df_path)
        except Exception as exc:  # noqa: BLE001 - ponte indisponível/timeout
            falhas += 1
            erros.append(f"sondagem {polls}: {scrub(exc)}")
            print(f"[jogo] FALHA na ponte (tentativa {falhas}/{MAX_RETRIES})")
            if falhas >= MAX_RETRIES:
                abort("esperar aventureiro", f"bridge snapshot ({df_path})",
                      falhas, erros)
            sleep(interval)
            continue
        code, payload = res.get("exit"), res.get("payload")
        if code == 0 and payload:
            adv = payload.get("adventurer") or {}
            nome = adv.get("name") or "(sem nome)"
            print(f"   ✓ AVENTUREIRO PRESENTE (snapshot exit 0): {nome}")
            _done("adventurer detectado no jogo (snapshot exit 0)")
            return payload
        if code == 13:
            # Estado DOCUMENTADO e esperado enquanto não há personagem.
            erro = (payload or {}).get("error") or "sem aventureiro"
            print(f"[jogo] {polls}: sem aventureiro ainda "
                  f"(exit 13: {scrub(erro)})")
        else:
            falhas += 1
            erro = (payload or {}).get("error") or res.get("stderr") \
                or res.get("raw") or f"exit {code}"
            erros.append(f"sondagem {polls}: exit {code}: {scrub(erro)}")
            print(f"[jogo] FALHA ponte exit={code} (tentativa {falhas}/{MAX_RETRIES})")
            if falhas >= MAX_RETRIES:
                abort("esperar aventureiro", f"bridge snapshot ({df_path})",
                      falhas, erros)
        if max_polls and polls >= max_polls:
            abort("esperar aventureiro", f"bridge snapshot ({df_path})",
                  polls, erros or [f"sem aventureiro após {polls} sondagens (exit 13)"])
        sleep(interval)


# ---------------------------------------------------------------------- métricas
def _digest(value):
    if isinstance(value, list):
        return f"{len(value)} item(ns)"
    if isinstance(value, dict):
        scalares = [f"{k}={value[k]!r}" for k in list(value)[:3]
                    if isinstance(value[k], (str, int, float, bool, type(None)))]
        return ", ".join(scalares) if scalares else f"{len(value)} campo(s)"
    return scrub(value)


def verify_metric_groups(metrics, groups_pt, titulo):
    """Valida que TODOS os grupos do contrato existem; imprime rótulos PT."""
    if not isinstance(metrics, dict):
        metrics = {}
    faltam = [g for g in groups_pt if g not in metrics]
    print(f"[métricas] {titulo}: {len(groups_pt) - len(faltam)}/{len(groups_pt)} grupos")
    for grupo, rotulo in groups_pt.items():
        marca = "✓" if grupo in metrics else "✗ EM FALTA"
        print(f"  {marca} {rotulo} ({grupo}): {_digest(metrics.get(grupo))}")
    return faltam


def check_all_metrics(base, cid, snapshot):
    """8 grupos do SNAPSHOT (fatais) + 8 da API (best-effort), todos impressos em PT.

    A verificação do snapshot é a do jogo (o contrato desta fase). A da API é
    complementar: se o endpoint estiver indisponível (ex.: defeito do backend),
    imprimimos aviso PT e registamos o erro no relatório sem abortar a sessão.
    """
    faltam_snap = verify_metric_groups(snapshot.get("metrics"), SNAPSHOT_GROUPS_PT,
                                       "8 grupos do snapshot do jogo")
    if faltam_snap:
        abort("validar 8 grupos de métricas (snapshot)",
              f"bridge snapshot ({DEFAULT_DF_PATH})", MAX_RETRIES,
              [f"snapshot sem: {faltam_snap}"])
    api_metrics: dict = {}
    api_error = None
    res = request("GET", f"{base}/api/campaigns/{cid}/metrics",
                  step="ler metrics", abort_on_fail=False)
    if res and res.get("status") == 200 and isinstance(res.get("body"), dict):
        api_metrics = res["body"]
        faltam_api = verify_metric_groups(api_metrics, API_GROUPS_PT,
                                          "8 grupos da API (GET .../metrics)")
        if faltam_api:
            api_error = f"API sem grupos: {faltam_api}"
    else:
        status = None if res is None else res.get("status")
        api_error = f"GET .../metrics devolveu HTTP {status}"
    if api_error:
        print(f"[métricas] AVISO: métricas da API indisponíveis ({scrub(api_error)}) "
              "- validação feita só sobre os 8 grupos do snapshot do jogo")
    print("   OK: 8 grupos de métricas do snapshot validados")
    _done("8 grupos de métricas validados (snapshot)"
          + (" + API" if api_error is None else " (API indisponível)"))
    return {"snapshot": list(SNAPSHOT_GROUPS_PT), "api": list(API_GROUPS_PT),
            "api_error": api_error, "source": api_metrics.get("source")}


# ------------------------------------------------------------------------ vigia
def _items(body, key):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        val = body.get(key, [])
        return val if isinstance(val, list) else []
    return []


def _pending(items):
    return [i for i in items
            if str((i or {}).get("status", "pending")).lower()
            in ("pending", "open", "waiting", "")]


def _bands(decisions):
    bandas = {}
    for d in decisions:
        b = str((d or {}).get("band") or (d or {}).get("action_band") or "?")
        bandas[b] = bandas.get(b, 0) + 1
    return bandas


def fetch(base, cid, what, query=None):
    url = f"{base}/api/campaigns/{cid}/{what}" + (f"?{query}" if query else "")
    res = request("GET", url, step=f"ler {what}")
    expect_ok(res, f"ler {what}", "GET", url, ok=(200,))
    return res.get("body")


def build_report(state):
    """Relatório final (JSON) a partir dos payloads recolhidos — chaves em EN."""
    decisions = state.get("decisions") or []
    questions = state.get("questions") or []
    approvals_all = state.get("approvals_all") or state.get("approvals") or []
    spend = state.get("spend") or {}
    memory = state.get("memory") or {}
    episodes = _items(memory, "episodes")
    return {
        "generated_at": _now_iso(),
        "campaign": {
            "id": state.get("campaign_id"),
            "name": state.get("campaign_name"),
            "mode": state.get("mode"),
            "kill_switch": bool(state.get("kill_switch")),
        },
        "key_status": state.get("key_status", "ausente"),
        "objective": {"narrative": state.get("objective")},
        "game": {
            "adventurer": (state.get("game") or {}).get("adventurer"),
            "mode": (state.get("game") or {}).get("mode"),
            "snapshot_ts": (state.get("game") or {}).get("ts"),
        },
        "metrics": state.get("metrics_check") or {},
        "decisions": {
            "count": len(decisions),
            "bands": _bands(decisions),
            "last": decisions[-1] if decisions else None,
        },
        "questions": {
            "count": len(questions),
            "pending": len(_pending(questions)),
            "items": [{"id": q.get("id"), "question": q.get("question"),
                       "status": q.get("status")} for q in questions],
        },
        "approvals": {
            "count": len(approvals_all),
            "pending": len(_pending(approvals_all)),
            "approved": sum(1 for a in approvals_all
                            if str(a.get("status", "")).lower() == "approved"),
            "rejected": sum(1 for a in approvals_all
                            if str(a.get("status", "")).lower() == "rejected"),
            "auto_approved": state.get("auto_approved", 0),
        },
        "spend": {
            "total_usd": spend.get("total_usd", spend.get("usd", 0)),
            "providers": spend.get("providers", []),
        },
        "memory": {
            "episodes_count": len(episodes),
            "episodes": [{"id": e.get("id"), "ts": e.get("ts"),
                          "kind": e.get("kind"), "content_pt": e.get("content_pt")}
                         for e in episodes],
            "narrative_pt": memory.get("narrative_pt"),
        },
        "watch": {
            "cycles": state.get("cycles", 0),
            "interval_s": state.get("interval"),
            "auto_approve": bool(state.get("auto_approve")),
        },
    }


def write_report(path, report):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def watch_session(base, cid, args, state):
    """Vigia a sessão (3 s/ciclo), auto-aprova se pedido e escreve o relatório."""
    print(f"-> a VIGIAR a sessão (ciclo a cada {args.watch_interval:.0f}s"
          + (", auto-aprove ON)" if args.auto_approve else ")"))
    inicio = time.time()
    ciclo = 0
    auto_aprovadas = 0
    while True:
        ciclo += 1
        decisions = _items(fetch(base, cid, "decisions"), "decisions")
        questions = _items(fetch(base, cid, "questions"), "questions")
        approvals = _items(fetch(base, cid, "approvals"), "approvals")
        spend = fetch(base, cid, "spend") or {}
        if args.auto_approve:
            for a in _pending(approvals):
                aid = a.get("id") or a.get("question_id")
                res = request("POST",
                              f"{base}/api/campaigns/{cid}/approvals/{aid}/approve",
                              {}, step=f"aprovar {aid}")
                expect_ok(res, f"aprovar {aid}", "POST",
                          f"{base}/api/campaigns/{cid}/approvals/{aid}/approve")
                auto_aprovadas += 1
                print(f"   ✓ aprovação {aid} libertada (auto-approve)")
        bandas = _bands(decisions)
        pend_q = _pending(questions)
        pend_a = _pending(approvals)
        print(f"--- ciclo {ciclo} | decisões={len(decisions)} | "
              f"bandas={bandas} | perguntas={len(questions)} "
              f"({len(pend_q)} pend.) | aprovações={len(pend_a)} pend. | "
              f"gasto=USD {spend.get('total_usd', spend.get('usd', 0))} ---")
        for q in pend_q:
            print(f"  ? {q.get('id')}: {q.get('question') or '(sem texto)'}")
        for a in pend_a:
            print(f"  ! {a.get('id')}: {a.get('action_id') or a.get('action_name_en')}")
        state.update(cycles=ciclo, decisions=decisions, questions=questions,
                     approvals=approvals, spend=spend, auto_approved=auto_aprovadas)
        if args.watch_cycles and ciclo >= args.watch_cycles:
            break
        if args.watch_seconds and (time.time() - inicio) >= args.watch_seconds:
            break
        sleep(args.watch_interval)
    # Fim da vigia: histórico auditável de aprovações + memória CoALA.
    state["approvals_all"] = _items(
        fetch(base, cid, "approvals", query="status=all"), "approvals")
    state["memory"] = fetch(base, cid, "memory") or {}
    report = build_report(state)
    path = write_report(args.report, report)
    print("✓ relatório da sessão escrito: %s" % path)
    print(f"  decisões={report['decisions']['count']} | "
          f"bandas={report['decisions']['bands']} | "
          f"gasto=USD {report['spend']['total_usd']} | "
          f"perguntas={report['questions']['count']} | "
          f"episódios de memória={report['memory']['episodes_count']}")
    _done("relatório da sessão escrito")
    return report


# -------------------------------------------------------------------------- CLI
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="session_live.py",
        description="System One Dwarf — lançador de sessão LIVE (um comando): "
                    "sobe backend+frontend, regista chave/campanha/objetivo/"
                    "control, espera pelo aventureiro no jogo REAL e vigia a "
                    "sessão até escrever o relatório final.",
        epilog="A chave nunca é impressa (apenas `chave: encontrada|ausente`). "
               "Ctrl-C pausa a sessão (mode=paused). O launcher nunca envia "
               "ações ao jogo — só lê via df_bridge.py.")
    p.add_argument("--api", default=DEFAULT_API,
                   help="URL base da API (default: %(default)s)")
    p.add_argument("--campaign", default=DEFAULT_CAMPAIGN,
                   help="nome da campanha (default: %(default)s)")
    p.add_argument("--df-path", default=DEFAULT_DF_PATH,
                   help="diretório do DF NON-Steam para a ponte (default: %(default)s)")
    p.add_argument("--mode", choices=("checkpoint", "autopilot"), default="checkpoint",
                   help="modo de controlo (default: %(default)s)")
    p.add_argument("--objective", default=DEFAULT_OBJECTIVE,
                   help="narrativa do objetivo (default: %(default)s)")
    p.add_argument("--key-env", default="SOD_OPENROUTER_KEY",
                   help="1ª variável de ambiente com a chave (default: %(default)s)")
    p.add_argument("--auto-approve", action="store_true",
                   help="auto-aprovar todas as aprovações pendentes na vigia")
    p.add_argument("--boot-only", action="store_true",
                   help="correr só as fases 1–3 (serviços+chave+campanha) e sair")
    p.add_argument("--game-interval", type=float, default=GAME_POLL,
                   help="segundos entre sondagens ao jogo (default: %(default)s)")
    p.add_argument("--watch-interval", type=float, default=WATCH_POLL,
                   help="segundos entre ciclos de vigia (default: %(default)s)")
    p.add_argument("--game-polls", type=int, default=0,
                   help="máx. de sondagens ao jogo antes de abortar (0 = ∞)")
    p.add_argument("--watch-cycles", type=int, default=0,
                   help="máx. de ciclos de vigia antes do relatório (0 = até Ctrl-C)")
    p.add_argument("--watch-seconds", type=float, default=0.0,
                   help="máx. de segundos de vigia antes do relatório (0 = até Ctrl-C)")
    p.add_argument("--report", default=str(REPORT_PATH),
                   help="ficheiro do relatório final (default: %(default)s)")
    return p.parse_args(argv)


def _now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")\
        .replace("+00:00", "Z")


def main(argv=None):
    args = parse_args(argv)
    base = args.api.rstrip("/")
    df_path = str(Path(args.df_path).expanduser())
    state = {
        "campaign_name": args.campaign, "objective": args.objective,
        "mode": args.mode, "kill_switch": False,
        "interval": args.watch_interval, "auto_approve": bool(args.auto_approve),
    }
    cid = None
    try:
        # ---- fase 1: serviços
        ensure_backend(base)
        ensure_frontend()
        # ---- fase 2: chave (nunca impressa)
        key = find_key()
        state["key_status"] = "encontrada" if key else "ausente"
        print("chave: " + state["key_status"])
        # ---- fase 3: campanha → keys → objective → control
        cid = ensure_campaign(base, args.campaign)
        state["campaign_id"] = cid
        register_keys(base, cid, key)
        print("-> a definir objetivo")
        set_objective(base, cid, args.objective)
        print(f"-> a activar controlo (mode={args.mode})")
        set_control(base, cid, args.mode, kill_switch=False)
        if args.boot_only:
            print("✓ fases 1–3 concluídas (--boot-only: vigia do jogo não iniciada)")
            return 0
        # ---- fase 4: jogo (até haver aventureiro) + vigia da sessão
        snapshot = await_adventurer(df_path, interval=args.game_interval,
                                    max_polls=args.game_polls)
        state["game"] = {"adventurer": (snapshot.get("adventurer") or {}).get("name"),
                         "mode": snapshot.get("mode"), "ts": snapshot.get("ts")}
        state["metrics_check"] = check_all_metrics(base, cid, snapshot)
        watch_session(base, cid, args, state)
        return 0
    except KeyboardInterrupt:
        print("\n⚠ interrompido (Ctrl+C) — a pausar a sessão ...")
        if cid is not None:
            set_control(base, cid, "paused", kill_switch=False, abort_on_fail=False)
            if state.get("decisions") is not None:
                report = build_report(state)
                path = write_report(args.report, report)
                print(f"✓ relatório parcial escrito: {path}")
        print("✓ sessão pausada (mode=paused). Até à próxima!")
        return 0


if __name__ == "__main__":
    sys.exit(main())