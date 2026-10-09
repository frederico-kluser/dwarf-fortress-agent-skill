"""Testes E2E offline (zero rede, zero jogo) do harness System One Dwarf."""

import importlib.util
import json
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUN_PATH = ROOT / "scripts" / "e2e" / "run.py"
CA_PATH = ROOT / "scripts" / "e2e" / "create_adventurer.py"
STEPS_PATH = ROOT / "scripts" / "e2e" / "steps.json"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# (a) run.py aborta com relatório PT após 5 tentativas falhadas.
def test_run_abort_report_apos_5_tentativas(monkeypatch, capsys):
    mod = load("sod_run_a", RUN_PATH)

    def boom(req, timeout=None):
        raise urllib.error.URLError("ligação recusada (simulada)")

    monkeypatch.setattr(mod, "urlopen", boom)
    monkeypatch.setattr(mod, "RETRY_SLEEP", 0)
    monkeypatch.setattr(mod, "sleep", lambda _s: None)

    with pytest.raises(SystemExit) as exc:
        mod.main(["--campaign", "e2e-abort"])
    assert exc.value.code == 1
    texto = capsys.readouterr()
    saida = texto.out + texto.err
    assert "RELATÓRIO DE ABORTO" in saida
    assert "Tentativas: 5" in saida
    assert "ligação recusada" in saida


# (b) a chave falsa do env nunca aparece em stdout/stderr.
def test_chave_de_env_nunca_aparece_na_saida(monkeypatch, capsys):
    mod = load("sod_run_b", RUN_PATH)
    SECRET = "sk-or-v1-CHAVE-FALSA-NAO-IMPRIMIR-98765"
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    monkeypatch.setattr(mod, "RETRY_SLEEP", 0)
    vistos = []

    class Resp:
        def __init__(self, payload, status=200):
            self.status = status
            self._raw = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._raw

    def fake(req, timeout=None):
        metodo, url = req.get_method(), req.full_url
        vistos.append((metodo, url, req.data or b""))
        if url.endswith("/api/health"):
            return Resp({"status": "ok"})
        if url.endswith("/api/campaigns") and metodo == "POST":
            return Resp({"id": "c1"})
        if url.endswith("/keys"):
            return Resp({"ok": True})
        if url.endswith("/objective"):
            return Resp({"ok": True})
        if url.endswith("/control"):
            return Resp({"ok": True})
        if url.endswith("/questions"):
            return Resp({"questions": []})
        if url.endswith("/approvals"):
            return Resp({"approvals": []})
        if url.endswith("/decisions"):
            return Resp({"decisions": []})
        if url.endswith("/spend"):
            return Resp({"usd": 0.0})
        return Resp({})

    monkeypatch.setattr(mod, "urlopen", fake)

    def fake_sleep(_s):
        raise KeyboardInterrupt  # simula Ctrl+C no fim do 1º ciclo

    monkeypatch.setattr(mod, "sleep", fake_sleep)

    rc = mod.main(["--campaign", "e2e-segredo", "--objective", "testar vazamento",
                   "--auto-approve"])
    assert rc == 0
    texto = capsys.readouterr()
    saida = texto.out + texto.err
    assert SECRET not in saida
    # a chave FOI usada (corpo do POST /keys) mas nunca impressa
    corpos_keys = [b for (m, u, b) in vistos if u.endswith("/keys")]
    assert corpos_keys and SECRET.encode("utf-8") in corpos_keys[0]


# (c) steps.json carrega e cada passo tem name/expect(lista)/keys/wait (mais guessed).
def test_steps_json_schema():
    steps = json.loads(STEPS_PATH.read_text(encoding="utf-8"))
    assert isinstance(steps, list) and steps
    obrigatorias = {"name", "expect", "keys", "wait"}
    permitidas = {"name", "expect", "keys", "wait", "guessed"}
    for s in steps:
        assert set(s) <= permitidas
        assert obrigatorias <= set(s)
        assert isinstance(s["name"], str) and s["name"]
        assert isinstance(s["expect"], list) and all(isinstance(e, str) for e in s["expect"])
        assert isinstance(s["keys"], list) and all(isinstance(k, str) for k in s["keys"])
        assert isinstance(s["wait"], (int, float)) and s["wait"] >= 0


# (d) create_adventurer --dry-run: imprime o plano sem tocar no jogo; só verbos conhecidos.
def test_create_adventurer_dry_run_plan_verbs(monkeypatch, capsys):
    ca = load("sod_ca_d", CA_PATH)

    def sem_bridge(*_args, **_kwargs):
        raise AssertionError("dry-run nao pode construir o Bridge (jogo tocado)")

    monkeypatch.setattr(ca, "Bridge", sem_bridge)  # dry-run não toca no jogo
    rc = ca.main(["--dry-run", "--steps", str(STEPS_PATH)])
    assert rc == 0

    out = capsys.readouterr().out
    steps = json.loads(STEPS_PATH.read_text(encoding="utf-8"))
    assert f"--- dry-run: {len(steps)} passo(s), jogo NAO tocado ---" in out
    for i, s in enumerate(steps, 1):
        assert f"[{i}/{len(steps)}] {s['name']}" in out

    # cada diretiva impressa usa apenas verbos do harness: click/click_re/poll/key
    diretrizes = [ln.strip()[len("do:"):].strip()
                  for ln in out.splitlines() if ln.strip().startswith("do:")]
    assert len(diretrizes) == len(steps)
    for linha in diretrizes:
        for parte in linha.split(";"):
            parte = parte.strip()
            if parte and parte != "(poll/none)":
                assert parte.split()[0] in {"click", "click_re", "poll", "key"}
