"""Testes E2E offline (zero rede, zero jogo) do lançador de sessão LIVE.

Fakes: o transporte HTTP (`_send`) e o runner da ponte (`run_bridge_snapshot`).
Cobrem (a) a chave nunca é impressa, (b) a sequência de arranque da campanha
chama os endpoints certos pela ordem certa, (c) o relatório PT de aborto após 5
falhas, (d) o caminho «apareceu aventureiro» gera um relatório JSON válido a
partir de payloads falsos da API.
"""

import importlib.util
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
SESSION_PATH = ROOT / "scripts" / "e2e" / "session_live.py"

SECRET = "sk-or-v1-CHAVE-FALSA-NAO-IMPRIMIR-12345"
SECRET_FICHEIRO = "sk-or-v1-DO-FICHEIRO-NAO-IMPRIMIR-67890"


def load(name, path=SESSION_PATH):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeHTTP:
    """Transporte HTTP falso: `routes[(METODO, path)] = (status, body)`."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []          # (method, url, payload)
        self.fail_paths = set()  # paths que levantam exceção (erro de transporte)

    def __call__(self, method, url, payload=None):
        path = urlparse(url).path
        self.calls.append((method, path, payload))
        if path in self.fail_paths:
            raise OSError("ligação recusada (simulada)")
        key = (method, path)
        if key in self.routes:
            status, body = self.routes[key]
            return status, body
        for (m, p), resp in self.routes.items():
            if m == method and path.endswith(p):
                return resp
        return 404, {"detail": "not found (fake)"}

    def sequence(self):
        return [(m, p) for (m, p, _b) in self.calls]

    def payloads_to(self, method, path):
        return [b for (m, p, b) in self.calls if m == method and p == path]


def fake_bridge(adventurer=True):
    """Runner de ponte falso (snapshot exit 0 com/sem aventureiro)."""
    snapshot = {
        "schema_version": 1,
        "ts": "2026-10-09T03:30:33.910Z",
        "mode": "adventure",
        "adventurer": {"name": "Omba Ziltacathi \"Amazedwonders\"",
                       "profession": "Hammerman"},
        "metrics": {
            "health_injuries": {"hp_current": 3200, "hp_max": 3200, "wounds": []},
            "needs_state": {"hunger": "none", "thirst": "none", "sleep": "rested"},
            "inventory": {"items": [], "item_count": 0},
            "threats": {"count": 0, "level": "low", "hostiles": []},
            "location": {"region": "The Hills", "world": "Terra"},
            "game_clock": {"date_text": "Granite 3, year 100"},
            "units_nearby": {"count": 1, "units": []},
            "events": [],
        },
        "screen_text": "Encontra-se numa clareira.",
        "focus": "",
        "gamelog_events": [],
        "warnings": [],
    }
    if not adventurer:
        return lambda df_path: {"exit": 13, "payload": {
            "ok": False, "error": "dfb-state: sem aventureiro",
            "exit_code": 13}, "raw": "", "stderr": ""}
    return lambda df_path: {"exit": 0, "payload": snapshot, "raw": "", "stderr": ""}


API_GROUPS = {
    "ts": "2026-10-09T03:31:00.000Z",
    "source": "live",
    "health": {"wounds": [], "status": "healthy"},
    "needs": {"hunger": "none", "thirst": "none", "sleep": "rested"},
    "inventory": {"weight_kg": 42.5, "items": []},
    "threats": {"level": "low", "combat": False, "hostiles": []},
    "location": {"region": "The Hills", "path": []},
    "objective_progress": {"percent": 0.35, "active_subgoal_id": "sg-1"},
    "combat_history": [],
    "agent": {"decisions_per_min": 12.4, "confidence_avg": 0.87, "calls": 318,
              "cost_usd": 0.042318, "latency_ms": 410},
}

DECISIONS = [
    {"id": "d-91", "ts": "2026-10-09T03:30:58.000Z", "agent": "JEV-A",
     "request_summary": "choice(6 action candidates)", "answers": {"choice": "goto:river_bank"},
     "probabilities": {"goto:river_bank": 0.94}, "confidence": 0.94,
     "band": "auto", "latency_ms": 410, "cost_usd": 0.000133},
    {"id": "d-92", "ts": "2026-10-09T03:30:59.000Z", "agent": "JEV-A",
     "request_summary": "choice(4 action candidates)", "answers": {"choice": "rest"},
     "probabilities": {"rest": 0.62}, "confidence": 0.62,
     "band": "hitl", "latency_ms": 380, "cost_usd": 0.000121},
]

QUESTIONS = [
    {"id": "q-4", "ts": "2026-10-09T03:30:59.000Z",
     "question": "Atravessar o rio a nadar ou procurar uma ponte?",
     "options": [{"id": "opt-1", "text": "Nadar"}, {"id": "opt-2", "text": "Procurar ponte"}],
     "free_text_allowed": True, "status": "pending", "band": "hitl", "role": "JEV-A",
     "action_id": "goto:river_bank", "why_pt": "a confiança ficou na banda hitl"},
]

APPROVALS = [
    {"id": "q-3-1", "question_id": "q-3-1", "ts": "2026-10-09T03:30:59.000Z",
     "action_id": "goto:river_bank", "action_name_en": "Go to the river bank",
     "question": "Posso executar «goto:river_bank»?", "band": "auto",
     "status": "pending"},
]

SPEND = {
    "total_usd": 0.042318,
    "providers": [{"provider": "typesafe", "models": [
        {"model": "typesafe/jev-1.13", "calls": 318, "cost_usd": 0.042318}]}],
    "ts": "2026-10-09T03:31:00.000Z",
}

MEMORY = {
    "narrative": "The adventurer wakes in a clearing...",
    "narrative_pt": "O aventureiro acorda numa clareira...",
    "episodes": [
        {"id": "ep-001", "ts": "2026-10-09T03:30:00.000Z", "kind": "episodic",
         "content": "Climbed the hill.", "content_pt": "Subiu a colina."},
        {"id": "ep-002", "ts": "2026-10-09T03:30:30.000Z", "kind": "episodic",
         "content": "Reached the river.", "content_pt": "Chegou ao rio."},
    ],
    "working_memory": "…",
    "updated_at": "2026-10-09T03:31:00.000Z",
}


def boot_routes(campaign_id="c1", existing=None):
    """Rotas do arranque: health, campanhas (lista/criação), keys, objective, control."""
    lista = existing if existing is not None else []
    return {
        ("GET", "/api/health"): (200, {"status": "ok", "version": "0.1.0"}),
        ("GET", "/api/campaigns"): (200, lista),
        ("POST", "/api/campaigns"): (201, {"id": campaign_id, "name": "campanha-e2e"}),
        ("POST", f"/api/campaigns/{campaign_id}/keys"): (
            200, {"provider": "openrouter", "masked_key": "TEST-KEY-••••••••0001",
                  "set": True}),
        ("PUT", f"/api/campaigns/{campaign_id}/objective"): (
            200, {"narrative": "Survive and explore as an adventurer.",
                  "subgoals": []}),
        ("POST", f"/api/campaigns/{campaign_id}/control"): (
            200, {"mode": "checkpoint", "kill_switch": False,
                  "ts": "2026-10-09T03:30:00.000Z"}),
    }


def wire(mod, monkeypatch, fake, bridge=None):
    monkeypatch.setattr(mod, "_send", fake)
    monkeypatch.setattr(mod, "sleep", lambda _s: None)
    monkeypatch.setattr(mod, "RETRY_SLEEP", 0)
    monkeypatch.setattr(mod, "port_open", lambda *a, **k: True)
    if bridge is not None:
        monkeypatch.setattr(mod, "run_bridge_snapshot", bridge)


# (a) a chave — de env ou de ficheiro — nunca aparece em stdout/stderr.
def test_chave_nunca_impressa_env(monkeypatch, capsys):
    mod = load("sod_live_a1")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    fake = FakeHTTP(boot_routes())
    wire(mod, monkeypatch, fake)

    rc = mod.main(["--campaign", "campanha-e2e", "--boot-only"])
    assert rc == 0
    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert SECRET not in texto
    assert "chave: encontrada" in texto
    # a chave FOI usada (corpo dos POST /keys) mas nunca impressa
    corpos = fake.payloads_to("POST", "/api/campaigns/c1/keys")
    assert corpos and all(c["key"] == SECRET for c in corpos)


def test_chave_nunca_impressa_ficheiros(monkeypatch, capsys, tmp_path):
    mod = load("sod_live_a2")
    (tmp_path / ".dsh").mkdir()
    (tmp_path / ".dsh" / ".credentials.yaml").write_text(
        "version: 1\n"
        "refs:\n"
        "  # export OPENROUTER_API_KEY=comentario-a-ignorar\n"
        "  OPENROUTER_MUNDODEV_API_KEY: %s\n" % SECRET_FICHEIRO,
        encoding="utf-8")
    (tmp_path / ".secrets").write_text(
        "# export OPENROUTER_API_KEY=NAO_USAR\n"
        "export OPENROUTER_API_KEY=%s\n" % SECRET, encoding="utf-8")
    monkeypatch.delenv("SOD_OPENROUTER_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    fake = FakeHTTP(boot_routes())
    wire(mod, monkeypatch, fake)

    rc = mod.main(["--campaign", "campanha-e2e", "--boot-only"])
    assert rc == 0
    texto = capsys.readouterr()
    saida = texto.out + texto.err
    assert "chave: encontrada" in saida
    assert SECRET not in saida
    assert SECRET_FICHEIRO not in saida
    corpos = fake.payloads_to("POST", "/api/campaigns/c1/keys")
    assert corpos and all(c["key"] == SECRET_FICHEIRO for c in corpos)


# (b) a sequência de arranque chama os endpoints certos PELA ORDEM certa.
def test_sequencia_de_arranque_da_campanha(monkeypatch, capsys):
    mod = load("sod_live_b")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    fake = FakeHTTP(boot_routes())
    wire(mod, monkeypatch, fake)

    rc = mod.main(["--campaign", "campanha-e2e", "--boot-only"])
    assert rc == 0
    assert fake.sequence() == [
        ("GET", "/api/health"),
        ("GET", "/api/campaigns"),
        ("POST", "/api/campaigns"),
        ("POST", "/api/campaigns/c1/keys"),
        ("POST", "/api/campaigns/c1/keys"),
        ("PUT", "/api/campaigns/c1/objective"),
        ("POST", "/api/campaigns/c1/control"),
    ]
    # providers das chaves (o gate do LoopRunner exige typesafe E openrouter)
    providers = [c["provider"] for c in fake.payloads_to("POST", "/api/campaigns/c1/keys")]
    assert providers == ["openrouter", "typesafe"]
    # objetivo fixo do pedido
    objetivos = fake.payloads_to("PUT", "/api/campaigns/c1/objective")
    assert objetivos == [{"narrative":
                          "Survive and explore as an adventurer. "
                          "Immediate goal: reach safety and rest."}]
    # control: checkpoint sem kill-switch
    controlos = fake.payloads_to("POST", "/api/campaigns/c1/control")
    assert controlos == [{"mode": "checkpoint", "kill_switch": False}]
    assert "campanha criada id=c1" in capsys.readouterr().out


def test_arranque_anexa_campanha_existente(monkeypatch):
    mod = load("sod_live_b2")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    existente = [{"id": "1bde4d26", "name": "campanha-e2e",
                  "control": {"mode": "checkpoint", "kill_switch": False}}]
    fake = FakeHTTP(boot_routes(campaign_id="1bde4d26", existing=existente))
    wire(mod, monkeypatch, fake)

    assert mod.main(["--campaign", "campanha-e2e", "--boot-only"]) == 0
    assert ("POST", "/api/campaigns") not in fake.sequence()  # só anexa
    assert fake.sequence()[2] == ("POST", "/api/campaigns/1bde4d26/keys")


# (c) 5 falhas no mesmo passo → relatório PT COMPLETO de aborto e exit 1.
def test_relatorio_de_aborto_apos_5_falhas(monkeypatch, capsys):
    mod = load("sod_live_c")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    fake = FakeHTTP(boot_routes())
    fake.fail_paths.add("/api/campaigns/c1/objective")
    wire(mod, monkeypatch, fake)

    with pytest.raises(SystemExit) as exc:
        mod.main(["--campaign", "campanha-e2e", "--boot-only"])
    assert exc.value.code == 1
    texto = capsys.readouterr()
    saida = texto.out + texto.err
    assert "RELATÓRIO DE ABORTO" in saida
    assert "Tentativas: 5/5" in saida
    assert "definir objetivo" in saida
    assert "ligação recusada (simulada)" in saida
    assert "Passos já concluídos:" in saida
    assert SECRET not in saida
    # exactamente 5 tentativas no passo falhado
    tentativas = [c for c in fake.calls
                  if c[0] == "PUT" and c[1] == "/api/campaigns/c1/objective"]
    assert len(tentativas) == 5


# (d) caminho «apareceu aventureiro»: valida métricas e escreve relatório JSON válido.
def test_aventureiro_apareceu_gera_relatorio(monkeypatch, capsys, tmp_path):
    mod = load("sod_live_d")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    report_path = tmp_path / "relatorio-sessao.json"
    rotas = boot_routes()
    rotas.update({
        ("GET", "/api/campaigns/c1/metrics"): (200, API_GROUPS),
        ("GET", "/api/campaigns/c1/decisions"): (200, DECISIONS),
        ("GET", "/api/campaigns/c1/questions"): (200, QUESTIONS),
        ("GET", "/api/campaigns/c1/approvals"): (200, APPROVALS),
        ("GET", "/api/campaigns/c1/spend"): (200, SPEND),
        ("GET", "/api/campaigns/c1/memory"): (200, MEMORY),
    })
    fake = FakeHTTP(rotas)
    wire(mod, monkeypatch, fake, bridge=fake_bridge())

    rc = mod.main(["--campaign", "campanha-e2e", "--watch-cycles", "2",
                   "--report", str(report_path)])
    assert rc == 0

    saida = capsys.readouterr()
    texto = saida.out + saida.err
    assert "AVENTUREIRO PRESENTE" in texto
    assert "8 grupos do snapshot do jogo: 8/8 grupos" in texto
    assert "8 grupos da API (GET .../metrics): 8/8 grupos" in texto
    assert "relatório da sessão escrito" in texto

    rel = json.loads(report_path.read_text(encoding="utf-8"))
    assert rel["campaign"] == {"id": "c1", "name": "campanha-e2e",
                               "mode": "checkpoint", "kill_switch": False}
    assert rel["decisions"]["count"] == 2
    assert rel["decisions"]["bands"] == {"auto": 1, "hitl": 1}
    assert rel["decisions"]["last"]["id"] == "d-92"
    assert rel["spend"]["total_usd"] == 0.042318
    assert rel["questions"]["count"] == 1 and rel["questions"]["pending"] == 1
    assert rel["questions"]["items"][0]["id"] == "q-4"
    assert rel["approvals"]["count"] == 1 and rel["approvals"]["pending"] == 1
    assert rel["memory"]["episodes_count"] == 2
    assert rel["memory"]["episodes"][0]["id"] == "ep-001"
    assert rel["metrics"]["snapshot"] == list(mod.SNAPSHOT_GROUPS_PT)
    assert rel["metrics"]["api"] == list(mod.API_GROUPS_PT)
    assert rel["watch"]["cycles"] == 2
    assert rel["key_status"] == "encontrada"
    assert rel["objective"]["narrative"].startswith("Survive and explore")
    # a chave também não vaza para o relatório
    assert SECRET not in report_path.read_text(encoding="utf-8")
    # o launcher nunca envia ações ao jogo: só GET/POST de API
    assert all(p.startswith("/api/") for (_m, p) in fake.sequence())


def test_auto_approve_liberta_aprovacoes_pendentes(monkeypatch, tmp_path):
    mod = load("sod_live_e")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    report_path = tmp_path / "relatorio-sessao.json"
    rotas = boot_routes()
    rotas.update({
        ("GET", "/api/campaigns/c1/metrics"): (200, API_GROUPS),
        ("GET", "/api/campaigns/c1/decisions"): (200, DECISIONS),
        ("GET", "/api/campaigns/c1/questions"): (200, QUESTIONS),
        ("GET", "/api/campaigns/c1/approvals"): (200, APPROVALS),
        ("GET", "/api/campaigns/c1/spend"): (200, SPEND),
        ("GET", "/api/campaigns/c1/memory"): (200, MEMORY),
        ("POST", "/api/campaigns/c1/approvals/q-3-1/approve"): (
            200, {"id": "q-3-1", "status": "approved"}),
    })
    fake = FakeHTTP(rotas)
    wire(mod, monkeypatch, fake, bridge=fake_bridge())

    rc = mod.main(["--campaign", "campanha-e2e", "--watch-cycles", "1",
                   "--auto-approve", "--report", str(report_path)])
    assert rc == 0
    assert fake.payloads_to("POST", "/api/campaigns/c1/approvals/q-3-1/approve") == [{}]
    rel = json.loads(report_path.read_text(encoding="utf-8"))
    assert rel["approvals"]["auto_approved"] == 1


def test_metrics_api_indisponivel_nao_aborta_sessao(monkeypatch, capsys, tmp_path):
    """GET .../metrics avariado (500) → aviso PT + relatório com api_error, sem abort."""
    mod = load("sod_live_f")
    monkeypatch.setenv("SOD_OPENROUTER_KEY", SECRET)
    report_path = tmp_path / "relatorio-sessao.json"
    rotas = boot_routes()
    rotas.update({
        ("GET", "/api/campaigns/c1/metrics"): (500, {"raw": "Internal Server Error"}),
        ("GET", "/api/campaigns/c1/decisions"): (200, DECISIONS),
        ("GET", "/api/campaigns/c1/questions"): (200, QUESTIONS),
        ("GET", "/api/campaigns/c1/approvals"): (200, APPROVALS),
        ("GET", "/api/campaigns/c1/spend"): (200, SPEND),
        ("GET", "/api/campaigns/c1/memory"): (200, MEMORY),
    })
    fake = FakeHTTP(rotas)
    wire(mod, monkeypatch, fake, bridge=fake_bridge())

    rc = mod.main(["--campaign", "campanha-e2e", "--watch-cycles", "1",
                   "--report", str(report_path)])
    assert rc == 0
    texto = capsys.readouterr()
    saida = texto.out + texto.err
    assert "8 grupos do snapshot do jogo: 8/8 grupos" in saida
    assert "AVISO: métricas da API indisponíveis" in saida
    assert "HTTP 500" in saida
    rel = json.loads(report_path.read_text(encoding="utf-8"))
    assert rel["metrics"]["snapshot"] == list(mod.SNAPSHOT_GROUPS_PT)
    assert "HTTP 500" in rel["metrics"]["api_error"]
    assert rel["decisions"]["count"] == 2  # a vigia aconteceu na mesma