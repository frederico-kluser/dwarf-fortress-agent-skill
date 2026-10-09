#!/usr/bin/env python3
"""System One Dwarf — runner de sessão E2E (apenas stdlib, urllib).

Fluxo: esperar /api/health -> criar campanha -> registar chave (só via env) ->
objetivo -> control (mode) -> loop de perguntas / aprovações / decisões+gasto.

Regras:
- a chave da API vem EXCLUSIVAMENTE de --key-env (variável de ambiente) e
  NUNCA é escrita na saída (stdout/stderr) nem em relatórios de erro;
- cada passo HTTP tem no máximo 5 tentativas e depois ABORTA com relatório
  em português das tentativas efectuadas;
- Ctrl+C (KeyboardInterrupt) pausa a sessão (mode=paused) e sai com código 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

HTTP_TIMEOUT = 10.0
MAX_RETRIES = 5
RETRY_SLEEP = 0.5
HEALTH_TIMEOUT = 30.0

# Símbolos injetáveis (os testes substituem-nos; zero rede nos testes).
urlopen = urllib.request.urlopen
sleep = time.sleep


# --------------------------------------------------------------------- aborto
def abort(step, alvo, tentativas, erros):
    """Imprime relatório PT de aborto e termina com SystemExit(1)."""
    print("", file=sys.stderr)
    print("=== RELATÓRIO DE ABORTO (System One Dwarf) ===", file=sys.stderr)
    print(f"Passo: {step}", file=sys.stderr)
    print(f"Alvo:  {alvo}", file=sys.stderr)
    print(f"Tentativas: {tentativas}/{MAX_RETRIES}", file=sys.stderr)
    print("Erros registados:", file=sys.stderr)
    for e in erros or ["(sem detalhe)"]:
        print(f"  - {e}", file=sys.stderr)
    print("ABORTADO: a sessão não continuou. Corrige o problema e volta a correr.",
          file=sys.stderr)
    raise SystemExit(1)


# ----------------------------------------------------------------------- http
def _decode(raw):
    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return {"raw": raw.decode("utf-8", "replace")}


def request(method, url, payload=None, step="http", abort_on_fail=True):
    """Um passo HTTP com retry <= 5; transport/5xx repetem, 4xx voltam ao chamador."""
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    erros = []
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            resp = urlopen(req, timeout=HTTP_TIMEOUT)
            status = getattr(resp, "status", None) or 200
            raw = resp.read() or b""
            return {"status": status, "body": _decode(raw)}
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                raw = exc.read() or b""
            except Exception:  # noqa: BLE001
                raw = b""
            if status >= 500 and attempt < MAX_RETRIES:
                erros.append(f"tentativa {attempt}: HTTP {status}")
                sleep(RETRY_SLEEP * attempt)
                continue
            return {"status": status, "body": _decode(raw)}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            erros.append(f"tentativa {attempt}: {exc}")
            if attempt < MAX_RETRIES:
                sleep(RETRY_SLEEP * attempt)
                continue
    if abort_on_fail:
        abort(step, f"{method} {url}", MAX_RETRIES, erros)
    return None


def expect_ok(res, step, method, url, ok=(200, 201, 204)):
    if res is None or res.get("status") not in ok:
        status = None if res is None else res.get("status")
        abort(step, f"{method} {url}", MAX_RETRIES,
              [f"HTTP {status} fora de {sorted(ok)}"])
    return res


# -------------------------------------------------------------------- helpers
def items(body, key):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        val = body.get(key, [])
        return val if isinstance(val, list) else []
    return []


def pick(d, names, default=None):
    if isinstance(d, dict):
        for n in names:
            if d.get(n) is not None:
                return d[n]
    return default


def is_pending(item):
    return str((item or {}).get("status", "pending")).lower() in ("pending", "open", "waiting", "")


# --------------------------------------------------------------------- passos
def wait_health(base):
    print("-> a aguardar /api/health ...")
    deadline = time.time() + HEALTH_TIMEOUT
    erros = []
    tentativas = 0
    while True:
        tentativas += 1
        res = request("GET", base + "/api/health", step="aguardar /api/health")
        if res and res.get("status") == 200:
            print("   OK: API saudável")
            return
        erros.append(f"tentativa {tentativas}: HTTP {None if res is None else res.get('status')}")
        if time.time() >= deadline or tentativas >= MAX_RETRIES:
            abort("aguardar /api/health", f"GET {base}/api/health", tentativas, erros)
        sleep(1.0)


def ensure_campaign(base, name):
    print(f"-> a criar campanha {name!r} ...")
    res = request("POST", base + "/api/campaigns",
                  {"name": name, "is_new_game": True}, step="criar campanha")
    if res and res.get("status") in (200, 201):
        cid = pick(res.get("body"), ("id", "campaign_id"))
        if isinstance(res.get("body"), dict) and isinstance(res["body"].get("campaign"), dict):
            cid = cid or pick(res["body"]["campaign"], ("id", "campaign_id"))
        if cid is not None:
            print(f"   OK: campanha id={cid}")
            return cid
    # 409/exists (ou resposta sem id): procurar por nome
    print("   campanha pode já existir; a procurar por nome ...")
    lst = request("GET", base + "/api/campaigns", step="listar campanhas")
    expect_ok(lst, "listar campanhas", "GET", base + "/api/campaigns", ok=(200,))
    for c in items(lst.get("body"), "campaigns"):
        if pick(c, ("name",)) == name:
            cid = pick(c, ("id", "campaign_id"))
            if cid is not None:
                print(f"   OK: campanha existente id={cid}")
                return cid
    abort("encontrar campanha por nome", f"GET {base}/api/campaigns", MAX_RETRIES,
          [f"campanha {name!r} não encontrada"])
    return None  # inalcançável


def register_key(base, cid, key):
    res = request("POST", f"{base}/api/campaigns/{cid}/keys",
                  {"provider": "openrouter", "key": key}, step="registar chave")
    expect_ok(res, "registar chave", "POST", f"{base}/api/campaigns/{cid}/keys")
    print("   OK: chave openrouter registada (valor oculto)")


def set_objective(base, cid, narrative):
    res = request("PUT", f"{base}/api/campaigns/{cid}/objective",
                  {"narrative": narrative}, step="definir objetivo")
    expect_ok(res, "definir objetivo", "PUT", f"{base}/api/campaigns/{cid}/objective")
    print("   OK: objetivo definido")


def set_control(base, cid, mode, abort_on_fail=True):
    res = request("POST", f"{base}/api/campaigns/{cid}/control",
                  {"mode": mode, "kill_switch": False},
                  step=f"control (mode={mode})", abort_on_fail=abort_on_fail)
    if res is None:
        return None
    if res.get("status") not in (200, 201, 204):
        if abort_on_fail:
            abort(f"control (mode={mode})", f"POST {base}/api/campaigns/{cid}/control",
                  MAX_RETRIES, [f"HTTP {res.get('status')}"])
        return res
    print(f"   OK: mode={mode}")
    return res


def show_questions(base, cid):
    res = request("GET", f"{base}/api/campaigns/{cid}/questions", step="ler perguntas")
    expect_ok(res, "ler perguntas", "GET", f"{base}/api/campaigns/{cid}/questions", ok=(200,))
    pend = [q for q in items(res.get("body"), "questions") if is_pending(q)]
    print(f"[perguntas] {len(pend)} pendente(s)")
    for q in pend:
        texto = pick(q, ("question", "text", "prompt"), "(sem texto)")
        print(f"  ? {texto}")
        opts = q.get("options") or []
        if opts:
            print("    opções: " + " | ".join(str(o) for o in opts))
        hint = pick(q, ("free_text_hint",))
        if hint:
            print(f"    {hint}")
        elif q.get("free_text") or q.get("allow_free_text"):
            print("    (texto livre permitido)")


def show_approvals(base, cid, auto_approve):
    res = request("GET", f"{base}/api/campaigns/{cid}/approvals", step="ler aprovações")
    expect_ok(res, "ler aprovações", "GET", f"{base}/api/campaigns/{cid}/approvals", ok=(200,))
    pend = [a for a in items(res.get("body"), "approvals") if is_pending(a)]
    print(f"[aprovações] {len(pend)} pendente(s)")
    for a in pend:
        aid = pick(a, ("id", "approval_id"), "?")
        desc = pick(a, ("summary", "description", "action", "text"), "(sem descrição)")
        print(f"  ! {aid} — {desc}")
        if auto_approve:
            r = request("POST", f"{base}/api/campaigns/{cid}/approvals/{aid}/approve", {},
                        step=f"aprovar {aid}")
            if r and r.get("status") in (200, 201, 204):
                print(f"    auto-aprovada ({aid})")
            else:
                print(f"    FALHA ao auto-aprovar {aid} (HTTP "
                      f"{None if r is None else r.get('status')})")


def show_summary(base, cid):
    dres = request("GET", f"{base}/api/campaigns/{cid}/decisions", step="ler decisões")
    expect_ok(dres, "ler decisões", "GET", f"{base}/api/campaigns/{cid}/decisions", ok=(200,))
    sres = request("GET", f"{base}/api/campaigns/{cid}/spend", step="ler gasto")
    expect_ok(sres, "ler gasto", "GET", f"{base}/api/campaigns/{cid}/spend", ok=(200,))
    decisions = items(dres.get("body"), "decisions")
    bandas = {}
    for d in decisions:
        b = str(pick(d, ("band", "action_band", "banda"), "?"))
        bandas[b] = bandas.get(b, 0) + 1
    body = sres.get("body") or {}
    usd = pick(body, ("usd", "total_usd", "spend_usd", "spent"), 0)
    if isinstance(body, dict) and isinstance(body.get("spend"), dict):
        usd = pick(body["spend"], ("usd", "total_usd"), usd)
    print(f"Resumo: decisões={len(decisions)} | bandas={bandas} | gasto=USD {usd}")


def run_loop(base, cid, args):
    while True:
        print(f"--- ciclo ({args.mode}) ---")
        show_questions(base, cid)
        show_approvals(base, cid, args.auto_approve)
        show_summary(base, cid)
        sleep(args.interval)


# ------------------------------------------------------------------------ CLI
def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="run.py",
        description="System One Dwarf — runner de sessão E2E (stdlib only).")
    p.add_argument("--api", default="http://127.0.0.1:8000",
                   help="URL base da API (default: %(default)s)")
    p.add_argument("--campaign", required=True, help="nome da campanha")
    p.add_argument("--mode", choices=("checkpoint", "autopilot"), default="checkpoint",
                   help="modo de controlo (default: %(default)s)")
    p.add_argument("--objective", default=None, help="narrativa do objetivo")
    p.add_argument("--task", default=None, help="tarefa inicial (opcional)")
    p.add_argument("--key-env", default="SOD_OPENROUTER_KEY",
                   help="variável de ambiente com a chave (default: %(default)s)")
    p.add_argument("--auto-approve", action="store_true",
                   help="aprovar automaticamente todas as aprovações pendentes")
    p.add_argument("--interval", type=float, default=2.0,
                   help="segundos entre ciclos (default: %(default)s)")
    return p.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    base = args.api.rstrip("/")
    narrative = args.objective or args.task or ""
    if args.objective and args.task:
        narrative = args.objective + "\n\nTarefa inicial: " + args.task
    # A chave é lida SÓ do ambiente e nunca é impressa.
    key = (os.environ.get(args.key_env) or "") if args.key_env else ""
    cid = None

    try:
        wait_health(base)
        cid = ensure_campaign(base, args.campaign)
        if key:
            print("-> a registar chave do provider (a partir do env)")
            register_key(base, cid, key)
        else:
            print(f"-> sem chave em {args.key_env!s}; a continuar sem registar chave")
        print("-> a definir objetivo")
        set_objective(base, cid, narrative)
        print(f"-> a activar controlo (mode={args.mode})")
        set_control(base, cid, args.mode)
        run_loop(base, cid, args)
    except KeyboardInterrupt:
        print("\n⚠ interrompido (Ctrl+C) — a pausar a sessão ...")
        if cid is not None:
            request("POST", f"{base}/api/campaigns/{cid}/control",
                    {"mode": "paused", "kill_switch": False},
                    step="pausar sessão", abort_on_fail=False)
        print("✓ sessão pausada (mode=paused). Até à próxima!")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
