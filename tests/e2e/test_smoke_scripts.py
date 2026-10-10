"""Testes offline dos scripts de smoke, dos workflows de CI e do CHANGELOG (H4/H7).

Cobre ``scripts/smoke/patterns_check.py`` (validação da biblioteca de padrões +
deteção de deriva), ``scripts/smoke/df_smoke.py`` (fluxo real da ponte contra um
DFHack falso, modos offline e live com sessão/HTTP/relógio injetados),
``.github/workflows/*.yml`` (estrutura, caminhos, segurança, scripts embebidos),
``CHANGELOG.md`` e ``docs/ci.md``.

Sem rede externa e sem o jogo: os servidores RPC falsos são loopback em porta
efémera; o ``df_bridge.py`` corre **in-process** (``main()`` com ``sys.argv``) para
os testes de fluxo, e num subprocesso real em dois testes de ponta a ponta. Chaves
"realistas" são montadas em tempo de execução (o repositório varre segredos).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import textwrap
import tomllib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SMOKE_DIR = ROOT / "scripts" / "smoke"
DF_SMOKE = SMOKE_DIR / "df_smoke.py"
PATTERNS_CHECK = SMOKE_DIR / "patterns_check.py"
NAV_JEV = ROOT / "scripts" / "e2e" / "nav_jev.py"
LIBRARY = ROOT / "docs" / "padroes-navegacao.json"
WORKFLOWS = ROOT / ".github" / "workflows"
CHANGELOG = ROOT / "CHANGELOG.md"
CI_DOC = ROOT / "docs" / "ci.md"
WORKFLOW_NAMES = ("ci", "release", "smoke")


# --------------------------------------------------------------------------- #
# carregamento + utilitários                                                  #
# --------------------------------------------------------------------------- #


def load_unregistered(name: str, path: Path):
    """Como os outros testes de scripts: SEM registar em ``sys.modules``.

    Protege uma regressão real: ``@dataclass`` + ``from __future__ import
    annotations`` rebenta nesse carregador (por isso os scripts não os usam).
    """
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def pc():
    return load_unregistered("smoke_patterns_check_under_test", PATTERNS_CHECK)


@pytest.fixture(scope="module")
def ds():
    return load_unregistered("smoke_df_smoke_under_test", DF_SMOKE)


@pytest.fixture(scope="module")
def nav():
    return load_unregistered("smoke_nav_jev_reference", NAV_JEV)


@pytest.fixture(scope="module")
def bridge_mod(ds):
    return ds.load_module("df_smoke_df_bridge", ds.BRIDGE_PATH)


@pytest.fixture()
def library_data():
    return copy.deepcopy(json.loads(LIBRARY.read_text(encoding="utf-8")))


@pytest.fixture(autouse=True)
def bridge_env(monkeypatch, tmp_path):
    """Nunca instalar scripts no DF nem escrever o journal na home do utilizador."""
    monkeypatch.setenv("DF_BRIDGE_SKIP_INSTALL", "1")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data) -> Path:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeClock:
    """Relógio + sleep determinísticos (o sleep só avança o relógio)."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class InProcessBridge:
    """Substitui o subprocesso por ``df_bridge.main()`` in-process (mesmo código, sem fork)."""

    def __init__(self, module) -> None:
        self.module = module
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        argv = [str(a) for a in cmd[2:]]            # [python, df_bridge.py, *argv]
        self.calls.append(argv)
        out, err, code, old = io.StringIO(), io.StringIO(), 0, sys.argv
        sys.argv = ["df_bridge.py", *argv]
        try:
            with redirect_stdout(out), redirect_stderr(err):
                self.module.main()
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
        finally:
            sys.argv = old
        return subprocess.CompletedProcess(cmd, code, out.getvalue(), err.getvalue())

    def commands(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def stub_runner(handler):
    """``runner(cmd, **kw)`` com ``handler(argv) -> (code, stdout)``."""
    def run(cmd, **kwargs):
        code, stdout = handler([str(a) for a in cmd[2:]])
        return subprocess.CompletedProcess(cmd, code, stdout, "")
    return run


def good_snapshot(ds, bridge_mod, x=50, y=50, z=5) -> dict:
    """Snapshot que o ``df_bridge.snapshot`` emitiria para o jogo falso."""
    game = ds.FakeGame(pos=(x, y, z))
    state = game._snapshot_state()
    return bridge_mod.sanitize_snapshot({
        "schema_version": 1, "ts": "2026-10-10T12:00:00.000Z", "mode": "adventure",
        "adventurer": state["adventurer"], "metrics": state["metrics"],
        "screen_text": "You are in a forest.", "focus": "dungeonmode/Default",
        "gamelog_events": [], "warnings": []})


# =========================================================================== #
# patterns_check — validação da biblioteca                                    #
# =========================================================================== #


def codes(report, level="error") -> set[str]:
    return {p.code for p in report.problems if p.level == level}


def check_data(pc, tmp_path, data, name="lib.json"):
    return pc.check_library(write_json(tmp_path / name, data), retries=0)


def test_biblioteca_commitada_passa(pc):
    report = pc.check_library(LIBRARY)
    assert report.ok, [p.as_dict() for p in report.errors]
    assert report.patterns == len(report.data["patterns"]) >= 1
    ids = {p["id"] for p in report.data["patterns"]}
    # o smoke assenta nestes ids: renomeá-los sem atualizar o smoke tem de falhar aqui
    assert "p-move-or-goto" in ids and "p-title-create-world" in ids


def test_cli_biblioteca_commitada_exit_0_com_json_valido(pc, capsys):
    assert pc.main(["--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == 1 and report["tool"] == "patterns_check"
    assert report["ok"] is True and report["exit_code"] == 0
    assert report["library"]["errors"] == [] and "screen" not in report


def test_so_leitura_a_biblioteca_nunca_e_alterada(pc, tmp_path, library_data, capsys):
    path = write_json(tmp_path / "lib.json", library_data)
    before = sha256(path)
    pc.main(["--library", str(path)])
    screen = tmp_path / "ecra.txt"
    screen.write_text("focus: new_region\nCreate new world\n", encoding="utf-8")
    pc.main(["--library", str(path), "--screen", str(screen), "--expect", "p-title-create-world"])
    assert sha256(path) == before
    capsys.readouterr()


def _mutations():
    """(nome, mutação, código esperado) — cada uma parte uma regra distinta."""
    def dup(d):
        d["patterns"][1]["id"] = d["patterns"][0]["id"]

    def no_id(d):
        del d["patterns"][0]["id"]

    def no_match_info(d):
        d["patterns"][0]["focus"], d["patterns"][0]["text_markers"] = [], []

    def no_action(d):
        d["patterns"][0].pop("action", None)
        d["patterns"][0].pop("outcome_markers", None)

    def bad_regex(d):
        d["patterns"][0]["text_markers"] = ["Create (new world"]

    def empty_marker(d):
        d["patterns"][0]["text_markers"] = ["  "]

    def no_proven(d):
        del d["patterns"][0]["proven"]

    def stats_patterns(d):
        d["stats"]["patterns"] = len(d["patterns"]) + 5

    def stats_stuck(d):
        d["stuck_events"] = d["stuck_events"][:50]
        d["stats"]["stuck_events_logged"] = 51

    def over_cap(d):
        d["stuck_events"] = d["stuck_events"] * 2
        d["stats"]["stuck_events_logged"] = len(d["stuck_events"])

    def bad_event(d):
        d["stuck_events"][0] = {"escalation": "replan", "ts": "2026-01-01T00:00:00"}

    def no_patterns(d):
        d["patterns"] = []

    def not_list(d):
        d["patterns"] = {"p-x": {}}

    def missing_stats(d):
        del d["stats"]

    def bad_version(d):
        d["version"] = "1"

    def bad_click_re(d):
        d["patterns"][0]["action"] = "click_re Select (open"

    def bad_key(d):
        d["patterns"][0]["action"] = "key leavescreen"

    def not_object_pattern(d):
        d["patterns"][0] = "p-title"

    def logged_not_int(d):
        d["stats"]["stuck_events_logged"] = "200"

    return [
        ("duplicate id", dup, "id_duplicate"),
        ("missing id", no_id, "id_missing"),
        ("no focus or markers", no_match_info, "no_focus_or_markers"),
        ("no action or outcome", no_action, "no_action_or_outcome"),
        ("bad regex", bad_regex, "bad_regex"),
        ("empty marker", empty_marker, "empty_marker"),
        ("no provenance", no_proven, "proven_missing"),
        ("stats.patterns mismatch", stats_patterns, "stats_patterns_mismatch"),
        ("stuck count mismatch", stats_stuck, "stats_stuck_mismatch"),
        ("ring over cap", over_cap, "stuck_over_cap"),
        ("event without kind", bad_event, "stuck_event_invalid"),
        ("empty patterns", no_patterns, "no_patterns"),
        ("patterns not a list", not_list, "no_patterns"),
        ("missing stats", missing_stats, "missing_key"),
        ("bad version", bad_version, "bad_version"),
        ("bad click_re", bad_click_re, "bad_action_regex"),
        ("bad key name", bad_key, "bad_key_name"),
        ("pattern not object", not_object_pattern, "pattern_not_object"),
        ("logged not int", logged_not_int, "stats_missing"),
    ]


@pytest.mark.parametrize("label,mutate,code", _mutations(), ids=[m[0] for m in _mutations()])
def test_validacao_apanha_cada_regressao(pc, tmp_path, library_data, capsys, label, mutate, code):
    mutate(library_data)
    path = write_json(tmp_path / "lib.json", library_data)
    report = pc.check_library(path, retries=0)
    assert not report.ok and code in codes(report), (label, [p.as_dict() for p in report.problems])
    assert pc.main(["--library", str(path), "--json"]) == 1      # e o CLI sai 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_raiz_nao_objeto_e_ficheiro_em_falta_e_json_invalido(pc, tmp_path):
    assert codes(check_data(pc, tmp_path, ["x"])) == {"not_object"}
    assert codes(pc.check_library(tmp_path / "nao-existe.json", retries=0)) == {"file_missing"}
    broken = tmp_path / "broken.json"
    broken.write_text("{ nao e json", encoding="utf-8")
    assert codes(pc.check_library(broken, retries=0)) == {"json_invalid"}


def test_json_invalido_transitorio_repete_ate_o_escritor_acabar(pc, tmp_path, library_data):
    """O StuckDetector escreve sem atomicidade: uma leitura a meio não é uma regressão."""
    path = tmp_path / "lib.json"
    path.write_text('{"version": 1, "patt', encoding="utf-8")
    waits = []

    def writer_finishes(seconds):
        waits.append(seconds)
        write_json(path, library_data)

    report = pc.check_library(path, retries=2, sleep=writer_finishes)
    assert report.ok and waits == [pc.LIBRARY_RETRY_SLEEP_S]


def test_json_invalido_persistente_esgota_as_tentativas(pc, tmp_path):
    path = tmp_path / "lib.json"
    path.write_text("{", encoding="utf-8")
    waits = []
    report = pc.check_library(path, retries=3, sleep=waits.append)
    assert codes(report) == {"json_invalid"} and len(waits) == 3


def test_ficheiro_demasiado_grande_e_recusado(pc, tmp_path, monkeypatch):
    path = write_json(tmp_path / "lib.json", {"version": 1})
    monkeypatch.setattr(pc, "MAX_LIBRARY_BYTES", 5)
    assert codes(pc.check_library(path, retries=0)) == {"file_too_large"}


def test_anel_de_200_aceita_contador_historico_maior(pc, tmp_path, library_data):
    events = [{"kind": "stuck", "escalation": "replan", "ts": f"2026-10-09T10:00:{i % 60:02d}"}
              for i in range(pc.STUCK_EVENTS_CAP)]
    library_data["stuck_events"] = events
    library_data["stats"]["stuck_events_logged"] = 1234        # o anel descartou os antigos
    assert check_data(pc, tmp_path, library_data).ok
    library_data["stuck_events"] = events[:150]                # anel NÃO cheio: tem de bater
    assert "stats_stuck_mismatch" in codes(check_data(pc, tmp_path, library_data))
    library_data["stats"]["stuck_events_logged"] = 150
    assert check_data(pc, tmp_path, library_data).ok


def test_avisos_so_falham_com_strict(pc, tmp_path, library_data, capsys):
    library_data["patterns"][0]["campo_novo"] = 1
    library_data["stuck_events"][0]["kind"] = "kind-inventado"
    path = write_json(tmp_path / "lib.json", library_data)
    report = pc.check_library(path, retries=0)
    assert report.ok and {"unknown_field", "stuck_vocab"} <= codes(report, "warning")
    assert pc.main(["--library", str(path)]) == 0
    assert pc.main(["--library", str(path), "--strict"]) == 1
    capsys.readouterr()


def test_validacao_e_analise_nao_rebentam_com_tipos_inesperados(pc, tmp_path, library_data):
    library_data["stuck_events"][0]["kind"] = ["lista"]            # não hashable
    library_data["stuck_events"][1]["escalation"] = {"x": 1}
    library_data["patterns"][0]["focus"] = "new_region"           # texto em vez de lista
    library_data["patterns"][1]["text_markers"] = {"a": 1}
    library_data["patterns"][2]["notes"] = 5
    report = check_data(pc, tmp_path, library_data)
    assert not report.ok
    assert {"stuck_event_invalid", "bad_field_type"} <= codes(report)
    matches = pc.analyze_screen(report.data, "new_region", "Create new world")
    assert len(matches) == len(library_data["patterns"])
    # um `focus` inválido conta como "sem marcadores de foco": só o texto decide
    assert matches[0].focus_ok is None and matches[0].status == "match"


def test_ruido_por_codigo_e_limitado(pc, tmp_path, library_data):
    for p in library_data["patterns"]:
        p.pop("proven", None)
    report = check_data(pc, tmp_path, library_data)
    own = [p for p in report.problems if p.code == "proven_missing"]
    assert len(own) == pc.MAX_PROBLEMS_PER_CODE + 1              # N individuais + 1 resumo
    assert own[-1].where == "*" and "semelhantes" in own[-1].message


# --------------------------------------------------------------------------- #
# patterns_check — correspondência de ecrã                                    #
# --------------------------------------------------------------------------- #

SCREENS = [
    ("new_region", "Dwarf Fortress\n[ Create new world ]\n[ Settings ]"),
    ("new_region", "Welcome to Dwarf Fortress\nOkay"),
    ("new_region", "Loading object files\nCreate world"),
    ("new_region", "World generated\nPlay now"),
    ("choose_game_type", "Aventura\nAdventurer"),
    ("setupadventure", "Onward to character creation"),
    ("setupadventure", "Create Your Character\nSelect dwarf\nDwarf\nHuman"),
    ("setupadventure", "From Hodomon\nSelect Hodomon"),
    ("setupadventure", "Accept background\nAccept equipment"),
    ("setupadventure", "Adventure awaits\nReady to adventure!\nGo!"),
    ("dungeonmode/Help", "Movement (2 of 3)\nOkay\nNext"),
    ("dungeonmode/Help", "Click the help button"),
    ("dungeonmode/Options", "Return to game\nSave and continue playing"),
    ("dungeonmode/Default", "You are in a forest."),
    ("dungeonmode/Attack", "Who will you attack?"),
    ("dungeonmode/Help/dungeonmode/Attack", "Okay\nNext"),
    ("", ""),
    ("dungeonmode", ""),
]


def test_semantica_identica_a_do_navegador_real(pc, nav, library_data):
    """A paridade com ``nav_jev.matches``: o alarme tem de ver o que o agente veria."""
    for pattern in library_data["patterns"]:
        for focus, text in SCREENS:
            expected = nav.matches(pattern, focus, text)
            got = pc.classify_pattern(pattern, focus, text)
            assert (got.status == "match") == expected, (pattern["id"], focus, text)


def test_classificacao_match_focus_only_text_only_miss(pc):
    pattern = {"id": "p-x", "focus": ["new_region"], "text_markers": ["Create new world"]}
    assert pc.classify_pattern(pattern, "new_region", "[ Create new world ]").status == "match"
    drift_text = pc.classify_pattern(pattern, "new_region", "[ Generate world ]")
    assert drift_text.status == "focus_only" and drift_text.missing == ["Create new world"]
    drift_focus = pc.classify_pattern(pattern, "title", "[ Create new world ]")
    assert drift_focus.status == "text_only" and drift_focus.missing == ["new_region"]
    assert pc.classify_pattern(pattern, "other", "nada").status == "miss"
    # sem nenhum marcador o padrão nunca casa (como no navegador)
    assert pc.classify_pattern({"id": "p-y"}, "x", "y").status == "miss"
    # texto casa também por regex e o foco entra no palheiro do texto (como nav_jev)
    only_text = {"id": "p-z", "text_markers": ["new_region"]}
    assert pc.classify_pattern(only_text, "new_region", "").status == "match"


def test_sanitizacao_remove_controlos_e_limita(pc, monkeypatch):
    dirty = "Okay\x00\x07\x1b[31m\x7fNext\x85 fim\r\n"
    assert pc.sanitize_text(dirty) == "Okay[31mNext fim\n"
    assert pc.sanitize_text(None) == ""
    monkeypatch.setattr(pc, "MAX_SCREEN_CHARS", 10)
    assert len(pc.sanitize_text("x" * 100)) == 10
    monkeypatch.setattr(pc, "MAX_SCREEN_CHARS", 10_000)
    monkeypatch.setattr(pc, "MAX_SCREEN_LINES", 3)
    assert pc.sanitize_text("a\nb\nc\nd\ne") == "a\nb\nc"


def test_payloads_de_ecra_aceites_e_rejeitados(pc):
    bridge = {"ok": True, "via": "tcp", "what": "screen",
              "data": {"focus": ["dungeonmode", "Default"], "lines": ["a", None, "c"]}}
    assert pc.screen_from_payload(bridge) == ("dungeonmode/Default", "a\n\nc")
    assert pc.screen_from_payload({"focus": ["x"], "lines": ["l"]}) == ("x", "l")
    assert pc.screen_from_payload({"focus": "a/b", "screen_text": "t"}) == ("a/b", "t")
    for bad in (None, [], "texto", {"focus": ["x"]}, {"data": {"focus": "y"}}):
        with pytest.raises(ValueError):
            pc.screen_from_payload(bad)


def test_parse_screen_text_formatos(pc):
    assert pc.parse_screen_text("focus: dungeonmode/Default\nl1\nl2") == (
        "dungeonmode/Default", "l1\nl2")
    assert pc.parse_screen_text("l1\nl2") == ("", "l1\nl2")
    assert pc.parse_screen_text("FOCUS: a\nl1", focus="override") == ("override", "l1")
    as_json = json.dumps({"data": {"focus": ["f"], "lines": ["x"]}})
    assert pc.parse_screen_text(as_json) == ("f", "x")
    assert pc.parse_screen_text("{ isto parece JSON mas e ecra") == ("", "{ isto parece JSON mas e ecra")
    with pytest.raises(pc.ScreenUnavailable):                    # JSON válido, sem ecrã
        pc.parse_screen_text('{"ok": true}')


def _screen_file(tmp_path, text, name="ecra.txt"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_cli_ecra_com_expect_satisfeito_e_em_falta(pc, tmp_path, capsys):
    ok = _screen_file(tmp_path, "focus: new_region\n  [ Create new world ]\n", "ok.txt")
    assert pc.main(["--screen", str(ok), "--expect", "p-title-create-world", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["ok"] and rep["screen"]["expect"] == {"wanted": ["p-title-create-world"],
                                                     "missing": []}
    assert rep["screen"]["source"] == "file" and rep["screen"]["summary"]["match"] >= 1

    drift = _screen_file(tmp_path, "focus: new_region\n  [ Generate a world ]\n", "drift.txt")
    assert pc.main(["--screen", str(drift), "--expect", "p-title-create-world", "--json"]) == 1
    rep = json.loads(capsys.readouterr().out)
    assert rep["ok"] is False and rep["screen"]["expect"]["missing"] == ["p-title-create-world"]
    miss = next(m for m in rep["screen"]["matches"] if m["id"] == "p-title-create-world")
    assert miss["status"] == "focus_only" and miss["missing"] == ["Create new world"]


def test_ecra_gigante_e_recusado_sem_ser_lido_por_inteiro(pc, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pc, "MAX_SCREEN_FILE_BYTES", 64)
    path = _screen_file(tmp_path, "x" * 1000)
    assert pc.main(["--screen", str(path)]) == 3
    assert "excede" in capsys.readouterr().out


def test_cli_ecra_sem_expect_so_reporta(pc, tmp_path, capsys):
    path = _screen_file(tmp_path, "focus: dungeonmode/Default\nmapa\n")
    assert pc.main(["--screen", str(path)]) == 0
    out = capsys.readouterr().out
    assert "[match] p-move-or-goto" in out and out.rstrip().endswith("OK")


def test_cli_erros_de_uso_e_de_fonte(pc, tmp_path, capsys):
    assert pc.main(["--expect", "p-move-or-goto"]) == 2          # sem fonte de ecrã
    path = _screen_file(tmp_path, "focus: dungeonmode/Default\nx")
    assert pc.main(["--screen", str(path), "--expect", "p-nao-existe"]) == 2
    assert pc.main(["--screen", str(tmp_path / "nao-existe.txt")]) == 3
    capsys.readouterr()
    _screen_file(tmp_path, '{"ok": true}', "semtexto.json")
    assert pc.main(["--screen", str(tmp_path / "semtexto.json"), "--json"]) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["exit_code"] == 3
    assert "não tem texto do ecrã" in report["error"] and "screen" not in report
    with pytest.raises(SystemExit) as exc:                       # --screen e --live exclusivos
        pc.main(["--screen", str(path), "--live"])
    assert exc.value.code == 2


def test_live_usa_a_ponte_e_exit_3_quando_o_jogo_esta_em_baixo(pc, capsys):
    payload = {"ok": True, "data": {"focus": ["new_region"], "lines": ["[ Create new world ]"]}}
    seen = []

    def runner(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")

    assert pc.main(["--live", "--df-path", "/x", "--port", "5999", "--expect",
                    "p-title-create-world"], runner=runner) == 0
    cmd = seen[0]
    assert cmd[2:5] == ["act", "screen", "--json"]
    assert cmd[cmd.index("--port") + 1] == "5999" and "--df-path" in cmd
    capsys.readouterr()

    def down(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 12, "", "erro: conexão recusada\n")

    assert pc.main(["--live"], runner=down) == 3
    assert "indisponível" in capsys.readouterr().out

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    assert pc.main(["--live"], runner=boom) == 3
    capsys.readouterr()


def test_cli_real_em_subprocesso_codigos_de_saida(tmp_path):
    run = lambda *a: subprocess.run([sys.executable, str(PATTERNS_CHECK), *a],  # noqa: E731
                                    capture_output=True, text=True, timeout=60)
    ok = run()
    assert ok.returncode == 0 and "OK" in ok.stdout
    bad = write_json(tmp_path / "bad.json", {"version": 1})
    assert run("--library", str(bad)).returncode == 1
    assert run("--bogus").returncode == 2


# =========================================================================== #
# df_smoke — utilitários e contratos                                          #
# =========================================================================== #


def fake_secret(kind: str) -> str:
    return {"openrouter": "sk-or-v1-" + "0123456789abcdef" * 4,
            "generic": "sk-" + "A1b2C3d4" * 4}[kind]


def test_redact_remove_segredos_controlos_e_limita(ds):
    key = fake_secret("openrouter")
    text = (f"falhou com {key} e Bearer abcdefghijklmnopqrstuvwxyz012345 "
            f"api_key=hunter2hunter2 token: s3cr3t \x00\x1b[0m fim")
    out = ds.redact(text)
    for leaked in (key, "abcdefghijklmnopqrstuvwxyz012345", "hunter2hunter2", "s3cr3t", "\x00"):
        assert leaked not in out
    assert out.count("<redacted>") >= 4 and "falhou com" in out and out.endswith("fim")
    assert fake_secret("generic") not in ds.redact(f"x {fake_secret('generic')} y")
    assert len(ds.redact("a" * 1000, limit=50)) == 51            # 50 + reticências
    assert ds.redact(None) == "None"


def test_scrubbed_env_retira_credenciais_e_mantem_o_resto(ds):
    env = {"PATH": "/usr/bin", "HOME": "/home/u", "DISPLAY": ":0", "OPENROUTER_API_KEY": "x",
           "GITHUB_TOKEN": "y", "My_Secret": "z", "DB_PASSWORD": "w", "AWS_CREDENTIALS": "v",
           "SOD_OPENROUTER_KEY": "k", "XDG_STATE_HOME": "/s"}
    assert ds.scrubbed_env(env) == {"PATH": "/usr/bin", "HOME": "/home/u", "DISPLAY": ":0",
                                    "XDG_STATE_HOME": "/s"}


def test_extract_json_tolerante_a_ruido(ds):
    assert ds.extract_json('aviso\n{"a": 1}\nfim') == {"a": 1}
    assert ds.extract_json("[1, 2]") == [1, 2]
    assert ds.extract_json("{ nao json } depois {\"b\": 2}") == {"b": 2}
    assert ds.extract_json("") is None and ds.extract_json("sem json") is None


def test_display_path_nao_expoe_a_home(ds):
    assert ds.display_path(ROOT / "docs" / "ci.md") == "docs/ci.md"
    assert ds.display_path(Path.home() / "Games" / "df") == "~/Games/df"
    assert ds.display_path("/opt/df") == "/opt/df"


def test_snapshot_position_prefere_location_e_rejeita_lixo(ds):
    snap = {"metrics": {"location": {"x": 3, "y": 4, "z": 5}},
            "adventurer": {"pos": {"x": 9, "y": 9, "z": 9}}}
    assert ds.snapshot_position(snap) == (3, 4, 5)
    assert ds.snapshot_position({"adventurer": {"pos": {"x": 1, "y": 2}}}) == (1, 2, None)
    assert ds.snapshot_position({"metrics": {"location": {"x": True, "y": 2}}}) is None
    assert ds.snapshot_position({"metrics": {"location": {"x": None, "y": None}}}) is None
    assert ds.snapshot_position(None) is None and ds.snapshot_position({"metrics": 3}) is None


def test_validate_snapshot_aceita_o_bom_e_apanha_cada_quebra(ds, bridge_mod):
    good = good_snapshot(ds, bridge_mod)
    empty = bridge_mod.EMPTY_METRICS
    assert ds.validate_snapshot(good, empty) == []

    def broken(fn):
        snap = copy.deepcopy(good)
        fn(snap)
        return " | ".join(ds.validate_snapshot(snap, empty))

    assert "schema_version" in broken(lambda s: s.update(schema_version=2))
    assert "ts não é ISO" in broken(lambda s: s.update(ts="ontem"))
    assert "ts não é ISO" in broken(lambda s: s.update(ts="2026-10-10T12:00:00+00:00"))
    assert "mode" in broken(lambda s: s.update(mode=""))
    assert "adventurer ausente" in broken(lambda s: s.update(adventurer=None))
    assert "adventurer.pos" in broken(lambda s: s["adventurer"]["pos"].pop("z"))
    assert "metrics não é objeto" in broken(lambda s: s.update(metrics=[]))
    assert "metrics.threats não é objeto" in broken(lambda s: s["metrics"].pop("threats"))
    assert "metrics.events não é lista" in broken(lambda s: s["metrics"].update(events={}))
    assert "sem campos" in broken(lambda s: s["metrics"]["inventory"].pop("item_count"))
    assert "não é lista" in broken(lambda s: s["metrics"]["inventory"].update(items="x"))
    assert "screen_text" in broken(lambda s: s.update(screen_text=None))
    assert "focus" in broken(lambda s: s.update(focus=3))
    assert "gamelog_events" in broken(lambda s: s.update(gamelog_events="x"))
    assert "warnings" in broken(lambda s: s.update(warnings=None))
    assert "posição incoerente" in broken(lambda s: s["metrics"]["location"].update(x=99))
    assert ds.validate_snapshot([], empty) == ["o snapshot não é um objeto JSON"]


def test_classify_snapshot_failure(ds):
    R = ds.BridgeReply
    assert ds.classify_snapshot_failure(R(13, "", "erro: sem aventureiro (o jogo)")) == "no_adventurer"
    assert ds.classify_snapshot_failure(R(13, '{"error": "SEM AVENTUREIRO"}', "")) == "no_adventurer"
    assert ds.classify_snapshot_failure(R(13, "", "a Lua rebentou")) == "error"
    assert ds.classify_snapshot_failure(R(12, "", "inalcançável")) == "game_down"
    assert ds.classify_snapshot_failure(R(10, "", "")) == "game_down"
    assert ds.classify_snapshot_failure(R(-1, "", "", True)) == "timeout"


# --------------------------------------------------------------------------- #
# prova de movimento + overlays                                               #
# --------------------------------------------------------------------------- #


def test_movement_plan_comeca_por_goto_e_sonda_as_quatro_direcoes(ds):
    assert ds.movement_plan((10, 20, 5)) == [
        "goto 10,16", "move n times=3", "move s times=3", "move e times=3", "move w times=3"]
    assert ds.movement_plan((0, 0, 0), tiles=2, times=1)[:2] == ["goto 0,-2", "move n times=1"]


def test_prova_para_na_primeira_movida_com_delta(ds):
    positions = iter([(10, 10, 0), (10, 10, 0), (10, 10, 0), (12, 10, 0)])  # move só à 3.ª
    calls, settled = [], []

    def run_batch(dsl):
        calls.append(dsl)
        return 0, None

    proof = ds.prove_movement(lambda: next(positions), run_batch, settle=lambda: settled.append(1))
    assert proof.moved and proof.delta == (2, 0) and proof.diagnosis is None
    assert len(calls) == 3 == len(settled) == len(proof.attempts)
    assert [a.moved for a in proof.attempts] == [False, False, True]


def test_codigo_de_saida_zero_sem_delta_nao_prova_nada(ds):
    """O `act batch` sai 0 mesmo com o tile bloqueado: só o delta de posição conta."""
    proof = ds.prove_movement(lambda: (5, 5, 0), lambda dsl: (0, None), lambda: "dungeonmode/Default")
    assert proof.moved is False and proof.diagnosis == "blocked_or_frozen"
    assert len(proof.attempts) == 5 and all(a.exit_code == 0 and not a.moved for a in proof.attempts)


@pytest.mark.parametrize("focus,diagnosis", [
    ("dungeonmode/Help", "input_swallowed"),
    ("dungeonmode/Help/dungeonmode/Attack", "input_swallowed"),
    ("dungeonmode/Options", "modal_open"),
    ("dungeonmode/Default", "blocked_or_frozen"),
    ("dungeonmode", "blocked_or_frozen"),
    ("", "blocked_or_frozen"),
])
def test_diagnostico_da_prova_pelo_foco(ds, focus, diagnosis):
    proof = ds.prove_movement(lambda: (1, 1, 1), lambda d: (13, None), lambda: focus)
    assert not proof.moved and proof.diagnosis == diagnosis and proof.focus == focus


def test_prova_sem_posicao_ou_z_a_mudar_sozinho(ds):
    none = ds.prove_movement(lambda: None, lambda d: (0, None))
    assert not none.moved and none.diagnosis == "no_position" and none.attempts == []
    # só o z muda (ex.: queda): não é movimento planar
    seq = iter([(1, 1, 5)] + [(1, 1, 4)] * 5)
    z_only = ds.prove_movement(lambda: next(seq), lambda d: (0, None))
    assert not z_only.moved


def test_overlay_action_clica_o_ultimo_botao_e_escala(ds):
    text = "Press Next to continue\nline\n   Okay    Next\n"
    click, key = ds.OVERLAY_CLICK_SLEEP_MS, ds.OVERLAY_KEY_SLEEP_MS
    what, dsl = ds.overlay_action("dungeonmode/Help", text)
    # "   Okay    Next" → "Next" começa na coluna 11 e o centro é 11 + 4//2; linha 2
    assert what == "click Next" and dsl == f"click 13,2; sleep {click}"
    what, dsl = ds.overlay_action("dungeonmode/Help", "  Okay  \nDon't show again")
    assert what == "click Okay" and dsl == f"click 4,0; sleep {click}"
    what, dsl = ds.overlay_action("dungeonmode/Help", "Movement (2 of 3)")
    assert what == "click help button" and dsl == f"click 180,1; sleep {click}"
    what, dsl = ds.overlay_action("dungeonmode/Help", "Movement", help_clicked=True)
    assert what == "key LEAVESCREEN" and dsl == f"key LEAVESCREEN; sleep {key}"
    assert ds.overlay_action("dungeonmode/Options", "Return to game")[0] == "key LEAVESCREEN"
    assert ds.overlay_action("x", "Okayish Nextdoor")[0] == "key LEAVESCREEN"   # só palavras inteiras


def test_settle_to_map_fecha_overlays_e_e_limitado(ds):
    screens = iter([("dungeonmode/Help", "Okay\nNext"), ("dungeonmode/Help", "Okay"),
                    ("dungeonmode/Default", "mapa"), ("dungeonmode/Default", "mapa")])
    acts = []
    ok, trail, focus = ds.settle_to_map(lambda: next(screens), lambda d: acts.append(d) or 0)
    assert ok and focus == "dungeonmode/Default" and len(trail) == 2 == len(acts)
    assert trail[0].startswith("click Next")

    stuck, acts = lambda: ("dungeonmode/Help", "texto sem botoes"), []   # noqa: E731
    ok, trail, focus = ds.settle_to_map(stuck, lambda d: acts.append(d) or 0, max_steps=4)
    assert not ok and len(acts) == 4 and focus == "dungeonmode/Help"
    assert [t.split(" (")[0] for t in trail] == [
        "click help button", "key LEAVESCREEN", "key LEAVESCREEN", "key LEAVESCREEN"]

    already, acts = lambda: ("dungeonmode", ""), []                      # noqa: E731
    assert ds.settle_to_map(already, acts.append)[0] and acts == []


# --------------------------------------------------------------------------- #
# jogo falso e servidor RPC falso                                             #
# --------------------------------------------------------------------------- #


def test_fake_game_aplica_a_movida_no_pedido_seguinte(ds):
    game = ds.FakeGame(pos=(10, 10, 3))
    text, code = game.respond("dfb-act", ["move", "e"])
    assert code == 0 and json.loads(text)["pos_before"] == {"x": 10, "y": 10, "z": 3}
    assert game.pos == [10, 10, 3]                               # ainda não: "próximo frame"
    assert json.loads(game.respond("dfb-state", ["adventurer", "1"])[0])["pos"]["x"] == 11
    game.respond("dfb-act", ["move", "nw"])
    game.respond("dfb-act", ["focus"])
    assert game.pos == [10, 9, 3]


def test_fake_game_cenarios(ds):
    blocked = ds.FakeGame("blocked")
    blocked.respond("dfb-act", ["move", "n"])
    blocked.respond("dfb-act", ["focus"])
    assert blocked.pos == [50, 50, 5]

    frozen = ds.FakeGame("frozen")
    text, code = frozen.respond("dfb-act", ["move", "n"])
    assert code == 0 and json.loads(text)["ok"] is True          # reporta ok:true... (D18)
    frozen.respond("dfb-act", ["focus"])
    assert frozen.pos == [50, 50, 5]
    assert json.loads(frozen.respond("dfb-act", ["focus"])[0])["focus"] == ["dungeonmode", "Help"]

    none = ds.FakeGame("no-adventurer")
    for command, args in (("dfb-state", ["snapshot", "25"]), ("dfb-state", ["adventurer", "1"]),
                          ("dfb-act", ["move", "n"]), ("dfb-nav", ["route", "1", "1", "5"])):
        text, code = none.respond(command, args)
        assert code == 1 and "sem aventureiro" in text
    assert json.loads(none.respond("dfb-act", ["screen"])[0])["focus"] == ["new_region"]

    assert ds.FakeGame().respond("nao-existe", [])[1] == 3       # CR_NOT_FOUND
    assert ds.FakeGame().respond("dfb-act", ["move", "up"])[1] == 1
    with pytest.raises(ValueError):
        ds.FakeGame("inventado")


def test_fake_game_rota_e_fecho_do_overlay_por_cliques(ds):
    game = ds.FakeGame(pos=(5, 5, 0))
    route = json.loads(game.respond("dfb-nav", ["route", "8", "3", "0"])[0])
    assert route["ok"] and route["len"] == 3 and route["steps"] == ["ne", "ne", "e"]

    overlay = ds.FakeGame("frozen", close_after_clicks=2)
    lines = overlay.screen[1]
    okay_x = lines[1].find("Okay")
    overlay.respond("dfb-act", ["click", "0", "0"])              # fora do botão: nada
    overlay.respond("dfb-act", ["click", str(okay_x), "1"])
    assert overlay.scenario == "frozen"
    overlay.respond("dfb-act", ["click", str(lines[2].find("Next") + 1), "2"])
    assert overlay.scenario == "normal"


def test_servidor_rpc_falso_fala_o_protocolo_real(ds, bridge_mod):
    game = ds.FakeGame()
    server = ds.FakeRpcServer(game.respond, bridge_mod)
    server.start()
    try:
        assert bridge_mod.probe_rpc(server.port) is True
        res = bridge_mod.send_command("/x", server.port, ["dfb-act", "focus"], "tcp")
        assert json.loads(res["text"])["focus"] == ["dungeonmode", "Default"]
        with pytest.raises(bridge_mod.BridgeError) as exc:       # command_result ≠ 0 → exit 13
            bridge_mod.send_command("/x", server.port, ["nao-existe"], "tcp")
        assert exc.value.exit_code == bridge_mod.EXIT_CMD_FAILED
        assert [c for c, _ in server.requests][-2:] == ["dfb-act", "nao-existe"]
    finally:
        server.close()
    assert bridge_mod.probe_rpc(server.port) is False            # fechado de verdade


# =========================================================================== #
# Smoke / relatório                                                           #
# =========================================================================== #


def test_smoke_step_estados_e_precedencia_do_exit_code(ds):
    clock = FakeClock()
    smoke = ds.Smoke(clock=clock, deadline=100.0)
    smoke.step("a", lambda: (ds.PASS, "ok", {"k": 1}))
    smoke.step("b", lambda: (_ for _ in ()).throw(ds.Precondition("falta o DF")))
    smoke.step("c", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    smoke.step("d", lambda: (ds.PASS, "nunca", {}), requires=("a", "b"))
    smoke.step("e", lambda: (ds.PASS, "ok", {}), requires=("a",))
    by = {r.name: r for r in smoke.results}
    assert by["a"].status == ds.PASS and by["a"].data == {"k": 1}
    assert by["b"].status == ds.PRECONDITION and by["b"].detail == "falta o DF"
    assert by["c"].status == ds.FAIL and "RuntimeError: boom" in by["c"].detail
    assert by["d"].status == ds.SKIP and "b" in by["d"].detail
    assert by["e"].status == ds.PASS
    assert smoke.exit_code() == ds.EXIT_FAIL                     # fail vence

    only_pre = ds.Smoke(clock=clock)
    only_pre.step("x", lambda: (_ for _ in ()).throw(ds.Precondition("p")))
    assert only_pre.exit_code() == ds.EXIT_PRECONDITION
    timeout = ds.Smoke(clock=clock)
    timeout.step("x", lambda: (ds.TIMEOUT, "t", {}))
    timeout.step("y", lambda: (_ for _ in ()).throw(ds.Precondition("p")))
    assert timeout.exit_code() == ds.EXIT_TIMEOUT                # timeout vence pré-condição
    assert ds.Smoke(clock=clock).exit_code() == ds.EXIT_OK


def test_smoke_step_respeita_o_orcamento_global(ds):
    clock = FakeClock()
    smoke = ds.Smoke(clock=clock, deadline=10.0)
    smoke.step("antes", lambda: (clock.sleep(11), (ds.PASS, "lento", {}))[1])
    smoke.step("depois", lambda: (ds.PASS, "nunca corre", {}))
    assert [r.status for r in smoke.results] == [ds.PASS, ds.TIMEOUT]
    assert smoke.results[0].duration_ms == 11_000


def test_detail_e_sempre_redigido_no_relatorio(ds):
    smoke = ds.Smoke(clock=FakeClock())
    key = fake_secret("openrouter")
    smoke.step("x", lambda: (ds.FAIL, f"erro com {key}", {}))
    assert key not in smoke.results[0].detail and "<redacted>" in smoke.results[0].detail


def test_build_report_forma_e_contagens(ds):
    smoke = ds.Smoke(clock=FakeClock())
    smoke.step("a", lambda: (ds.PASS, "ok", {}))
    smoke.step("b", lambda: (ds.FAIL, "mau", {"x": (1, 2)}))
    smoke.step("c", lambda: (ds.PASS, "ok", {}), requires=("b",))
    report = ds.build_report("offline", smoke, started_at="2026-10-10T00:00:00Z", duration_ms=7,
                             environment={"python": "3"}, warnings=["w"])
    assert report["schema"] == 1 and report["tool"] == "df_smoke" and report["mode"] == "offline"
    assert report["ok"] is False and report["exit_code"] == 1 and report["warnings"] == ["w"]
    assert report["summary"] == {"total": 3, "passed": 1, "failed": 1, "skipped": 1,
                                 "preconditions": 0, "timeouts": 0}
    assert [c["name"] for c in report["checks"]] == ["a", "b", "c"]
    assert json.loads(json.dumps(report))["checks"][1]["data"] == {"x": [1, 2]}
    forced = ds.build_report("live", smoke, started_at="t", duration_ms=1, environment={},
                             exit_code=130)
    assert forced["exit_code"] == 130 and forced["ok"] is False


def test_write_report_atomico_e_cria_pastas(ds, tmp_path):
    target = tmp_path / "a" / "b" / "relatorio.json"
    ds.write_report(target, {"ok": True, "n": "ação"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True, "n": "ação"}
    assert [p.name for p in target.parent.iterdir()] == ["relatorio.json"]   # sem .tmp


# --------------------------------------------------------------------------- #
# verificações isoladas (ponte substituída por respostas escritas à mão)      #
# --------------------------------------------------------------------------- #


def make_checks(ds, bridge_mod, handler=None, runner=None, *, live=False, expect=()):
    runner = runner or stub_runner(handler)
    bridge = ds.BridgeClient(df_path="/df", runner=runner, env={})
    patterns = ds.load_module("df_smoke_patterns_check", ds.PATTERNS_CHECK_PATH)
    return ds.Checks(bridge, bridge_mod=bridge_mod, patterns=patterns, library=LIBRARY, live=live,
                     delay=0.0, sleep=lambda s: None, expect=list(expect), journal=False)


def test_dsl_parse_falha_se_o_parser_aceitar_um_passo_invalido(ds, bridge_mod):
    accepting = lambda argv: (0, json.dumps({                       # noqa: E731
        "ok": True, "dry_run": True, "summary": {"total": 8, "validated": 8}}))
    status, detail, _ = make_checks(ds, bridge_mod, accepting).dsl_parse()
    assert status == ds.FAIL and "aceite" in detail

    def partial(argv):                                            # valida só 5 dos 8 verbos
        return 0, json.dumps({"ok": True, "summary": {"validated": 5}})

    status, detail, _ = make_checks(ds, bridge_mod, partial).dsl_parse()
    assert status == ds.FAIL and "dry-run do DSL falhou" in detail


def test_bridge_status_classifica_cada_resposta(ds, bridge_mod):
    def status_for(handler, **kw):
        return make_checks(ds, bridge_mod, handler, **kw).bridge_status

    up = lambda argv: (0, json.dumps({"df_path": "/df", "ok_write": True, "dfhack_installed": True,  # noqa: E731
                                      "rpc": {"port": 5000, "reachable": True}}))
    assert status_for(up)()[0] == ds.PASS
    down = lambda argv: (0, json.dumps({"df_path": "/df", "rpc": {"port": 5000, "reachable": False}}))  # noqa: E731
    assert status_for(down)()[0] == ds.FAIL                         # offline: o servidor TEM de estar
    with pytest.raises(ds.Precondition):                            # live: o jogo é do ambiente
        status_for(down, live=True)()
    with pytest.raises(ds.Precondition):
        status_for(lambda argv: (10, json.dumps({"df_path": None})))()
    assert status_for(lambda argv: (0, "ruído sem json"))()[0] == ds.FAIL


def test_snapshot_schema_distingue_sem_aventureiro_de_jogo_em_baixo(ds, bridge_mod):
    def schema(handler):
        return make_checks(ds, bridge_mod, handler).snapshot_schema()

    with pytest.raises(ds.Precondition):
        schema(lambda argv: (13, json.dumps({"ok": False, "error": "sem aventureiro (jogo)"})))
    status, detail, data = schema(lambda argv: (12, json.dumps({"ok": False, "error": "RPC"})))
    assert status == ds.FAIL and data["kind"] == "game_down"
    status, detail, data = schema(lambda argv: (13, json.dumps({"error": "version_guard"})))
    assert status == ds.FAIL and data["kind"] == "error" and "version_guard" in detail

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    assert make_checks(ds, bridge_mod, runner=hang).snapshot_schema()[0] == ds.TIMEOUT


def test_snapshot_consumer_apanha_posicao_que_o_consumidor_nao_le(ds, bridge_mod):
    checks = make_checks(ds, bridge_mod, lambda argv: (0, "{}"))
    snap = good_snapshot(ds, bridge_mod, x=7, y=8, z=1)
    checks.snapshot = snap
    status, detail, data = checks.snapshot_consumer()
    assert status == ds.PASS and data["position"] == (7, 8, 1)
    # o consumidor lê `adventurer.pos`; se divergir da localização o smoke tem de dar por isso
    snap["adventurer"]["pos"]["x"] = 99
    checks.snapshot = snap
    assert checks.snapshot_consumer()[0] == ds.FAIL


def test_movement_proof_batch_abortado_por_ameaca_nao_e_confundido_com_parede(ds, bridge_mod):
    def runner(cmd, **kw):
        argv = [str(a) for a in cmd[2:]]
        if argv[0] == "snapshot":
            return subprocess.CompletedProcess(
                cmd, 0, json.dumps(good_snapshot(ds, bridge_mod)), "")
        if argv[:2] == ["act", "batch"]:
            report = {"ok": False, "reason": "threat", "aborted_at": 0, "steps": []}
            return subprocess.CompletedProcess(cmd, 13, json.dumps(report), "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(
            {"data": {"focus": ["dungeonmode", "Default"], "lines": []}}), "")

    status, detail, data = make_checks(ds, bridge_mod, runner=runner).movement_proof()
    assert status == ds.FAIL and "ameaça" in detail and data["reasons"] == ["threat"]
    assert "parede" not in detail


def test_movement_proof_timeout_da_ponte_nao_e_confundido_com_parede(ds, bridge_mod):
    calls = {"n": 0}

    def runner(cmd, **kw):
        argv = [str(a) for a in cmd[2:]]
        if argv[0] == "snapshot":
            snap = good_snapshot(ds, bridge_mod)
            return subprocess.CompletedProcess(cmd, 0, json.dumps(snap), "")
        calls["n"] += 1
        raise subprocess.TimeoutExpired(cmd, 1)                     # act batch/screen pendurados

    status, detail, data = make_checks(ds, bridge_mod, runner=runner).movement_proof()
    assert status == ds.TIMEOUT and "não respondeu" in detail
    assert all(a["reason"] == "timeout" for a in data["attempts"]) and calls["n"] >= 5


def test_pattern_library_e_screen_patterns_reportam_o_que_falta(ds, bridge_mod, tmp_path):
    bad = write_json(tmp_path / "lib.json", {"version": 1})
    checks = make_checks(ds, bridge_mod, lambda argv: (0, "{}"))
    checks.library = bad
    status, detail, data = checks.pattern_library()
    assert status == ds.FAIL and "missing_key" in detail and data["patterns"] == 0

    screen = {"ok": True, "data": {"focus": ["dungeonmode", "Default"], "lines": ["mapa"]}}
    handler = lambda argv: (0, json.dumps(screen))                  # noqa: E731
    ok = make_checks(ds, bridge_mod, handler, expect=["p-move-or-goto"]).screen_patterns()
    assert ok[0] == ds.PASS and ok[2]["matched"] == ["p-move-or-goto"]
    unknown = make_checks(ds, bridge_mod, handler, expect=["p-inventado"]).screen_patterns()
    assert unknown[0] == ds.FAIL and unknown[2]["missing_markers"] == {
        "p-inventado": ["(id desconhecido)"]}
    unreadable = make_checks(ds, bridge_mod, lambda argv: (13, "erro")).screen_patterns()
    assert unreadable[0] == ds.FAIL and "ilegível" in unreadable[1]


# =========================================================================== #
# df_smoke — fluxo offline (ponte real ↔ servidor RPC falso)                  #
# =========================================================================== #

OFFLINE_CHECKS = ["bridge_status", "dsl_parse", "snapshot_schema", "snapshot_consumer",
                  "pattern_library", "screen_patterns", "movement_proof", "proof_controls"]


@pytest.fixture()
def offline(ds, bridge_mod):
    bridge = InProcessBridge(bridge_mod)

    def run(**kwargs):
        return ds.run_offline(runner=bridge, sleep=lambda s: None, **kwargs)

    run.bridge = bridge
    return run


def by_name(report):
    return {c["name"]: c for c in report["checks"]}


def test_offline_normal_passa_todas_as_verificacoes(offline):
    report = offline()
    checks = by_name(report)
    assert report["ok"] and report["exit_code"] == 0 and report["mode"] == "offline"
    assert [c["name"] for c in report["checks"]] == OFFLINE_CHECKS
    assert {c["status"] for c in report["checks"]} == {"pass"}, report["checks"]
    assert report["summary"]["passed"] == len(OFFLINE_CHECKS)
    # a prova de movimento foi real: a posição mudou e passou pelo RPC
    proof = checks["movement_proof"]["data"]
    assert proof["after"] != proof["before"] and proof["delta"] != [0, 0]
    assert report["environment"]["rpc_requests"] > 20
    assert checks["screen_patterns"]["data"]["matched"] == ["p-move-or-goto"]
    # o caminho do repo e a home nunca aparecem no relatório
    blob = json.dumps(report)
    assert str(Path.home()) not in blob and str(ROOT) not in blob


def test_offline_usa_os_comandos_reais_da_ponte(offline):
    offline()
    commands = offline.bridge.commands()
    assert any(c.startswith("status --json") for c in commands)
    assert any(c.startswith("snapshot --json") for c in commands)
    assert any("act batch --json --max-steps 8 --delay 0.0 --stop-on-focus-change "
               "--stop-on-threat --no-journal goto " in c for c in commands)
    assert any("act batch --dry-run --json" in c for c in commands)
    assert all("--via tcp" in c and "--df-path" in c and "--port" in c for c in commands)


@pytest.mark.parametrize("scenario,exit_code,failing,diagnosis", [
    ("blocked", 1, "movement_proof", "blocked_or_frozen"),
    ("frozen", 1, "movement_proof", "input_swallowed"),
])
def test_offline_cenarios_de_falha_dao_exit_1_com_diagnostico(offline, scenario, exit_code,
                                                              failing, diagnosis):
    report = offline(scenario=scenario)
    checks = by_name(report)
    assert report["exit_code"] == exit_code and report["ok"] is False
    assert checks[failing]["status"] == "fail"
    assert checks[failing]["data"]["diagnosis"] == diagnosis
    assert len(checks[failing]["data"]["attempts"]) == 5
    assert checks["proof_controls"]["status"] == "pass"          # os controlos não dependem do cenário
    others = [c for n, c in checks.items() if n != failing]
    assert all(c["status"] == "pass" for c in others)


def test_offline_sem_aventureiro_e_pre_condicao_e_nao_regressao(offline):
    report = offline(scenario="no-adventurer")
    checks = by_name(report)
    assert report["exit_code"] == 3
    assert checks["snapshot_schema"]["status"] == "precondition"
    for dependent in ("snapshot_consumer", "screen_patterns", "movement_proof"):
        assert checks[dependent]["status"] == "skip"
    assert checks["bridge_status"]["status"] == "pass"
    assert checks["pattern_library"]["status"] == "pass"


def test_offline_biblioteca_invalida_e_regressao(offline, tmp_path, library_data):
    library_data["patterns"][0]["id"] = library_data["patterns"][1]["id"]
    report = offline(library=write_json(tmp_path / "lib.json", library_data))
    checks = by_name(report)
    assert report["exit_code"] == 1
    assert checks["pattern_library"]["status"] == "fail"
    assert "id_duplicate" in checks["pattern_library"]["detail"]


def test_offline_padrao_esperado_em_falta_falha_com_marcadores(offline):
    report = offline(expect=("p-title-create-world",))           # no mapa não pode casar
    check = by_name(report)["screen_patterns"]
    assert report["exit_code"] == 1 and check["status"] == "fail"
    assert check["data"]["missing_markers"]["p-title-create-world"] == [
        "new_region", "Create new world"]


def test_offline_tempo_esgotado_e_exit_4(ds, bridge_mod):
    report = ds.run_offline(runner=InProcessBridge(bridge_mod), timeout=0.0)
    assert report["exit_code"] == 4
    assert {c["status"] for c in report["checks"]} <= {"timeout", "skip"}
    assert report["checks"][0]["status"] == "timeout"


def test_offline_ponte_a_pendurar_e_timeout_sem_rebentar(ds):
    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)

    report = ds.run_offline(runner=hang)
    assert report["exit_code"] == 4
    assert by_name(report)["bridge_status"]["status"] == "timeout"
    assert "não respondeu" in by_name(report)["bridge_status"]["detail"]


def test_offline_ponte_com_lixo_e_falha_e_nao_excecao(ds):
    def garbage(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "isto não é JSON", "")

    report = ds.run_offline(runner=garbage, sleep=lambda s: None)
    checks = by_name(report)
    assert report["exit_code"] == 1
    assert checks["bridge_status"]["status"] == "fail"
    assert checks["snapshot_schema"]["status"] == "skip"


def test_offline_snapshot_da_ponte_fora_do_contrato_falha(ds, bridge_mod):
    """Se a normalização da ponte regredir, o smoke tem de o dizer."""
    real = InProcessBridge(bridge_mod)

    def corrupting(cmd, **kw):
        proc = real(cmd, **kw)
        argv = [str(a) for a in cmd[2:]]
        if argv[0] == "snapshot" and proc.returncode == 0:
            snap = json.loads(proc.stdout)
            del snap["metrics"]["threats"]
            snap["schema_version"] = 2
            return subprocess.CompletedProcess(cmd, 0, json.dumps(snap), "")
        return proc

    report = ds.run_offline(runner=corrupting, sleep=lambda s: None)
    check = by_name(report)["snapshot_schema"]
    assert report["exit_code"] == 1 and check["status"] == "fail"
    assert "schema_version" in check["detail"] and "threats" in check["detail"]


def test_offline_avisos_do_snapshot_sao_regressao(ds, bridge_mod, monkeypatch):
    real = InProcessBridge(bridge_mod)

    def warned(cmd, **kw):
        proc = real(cmd, **kw)
        if cmd[2] == "snapshot" and proc.returncode == 0:
            snap = json.loads(proc.stdout)
            snap["warnings"] = ["screen text unavailable: boom"]
            return subprocess.CompletedProcess(cmd, 0, json.dumps(snap), "")
        return proc

    check = by_name(ds.run_offline(runner=warned, sleep=lambda s: None))["snapshot_schema"]
    assert check["status"] == "fail" and "avisos inesperados" in check["detail"]


def test_offline_nao_deixa_lixo_nem_altera_a_biblioteca(ds, bridge_mod, tmp_path, monkeypatch):
    sandbox = tmp_path / "tmp"
    sandbox.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(sandbox))
    before = sha256(LIBRARY)
    ds.run_offline(runner=InProcessBridge(bridge_mod), sleep=lambda s: None)
    assert list(sandbox.iterdir()) == []                         # tmp dir e servidor limpos
    assert sha256(LIBRARY) == before


def test_cli_offline_em_subprocesso_real_emite_json_valido(tmp_path):
    report_path = tmp_path / "out" / "smoke.json"
    proc = subprocess.run(
        [sys.executable, str(DF_SMOKE), "--offline", "--json", "--report", str(report_path)],
        capture_output=True, text=True, timeout=120, cwd=str(tmp_path))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    stdout_report = json.loads(proc.stdout)
    assert stdout_report["ok"] and json.loads(report_path.read_text()) == stdout_report
    assert [c["name"] for c in stdout_report["checks"]] == OFFLINE_CHECKS
    assert {c["status"] for c in stdout_report["checks"]} == {"pass"}


def test_cli_codigos_de_saida_de_cada_modo_de_falha(ds, bridge_mod, capsys):
    bridge = InProcessBridge(bridge_mod)

    def run(*argv):
        try:
            return ds.main(list(argv), runner=bridge, sleep=lambda s: None)
        except SystemExit as exc:                                # argparse: uso inválido
            return exc.code

    assert run("--offline", "--scenario", "blocked") == 1
    assert run("--offline", "--scenario", "no-adventurer") == 3
    assert run("--live", "--df-path", str(Path.home() / "nao-existe-df-smoke")) == 3
    assert run("--offline", "--live") == 2
    assert run("--timeout", "0") == 2
    assert run("--scenario", "inventado") == 2
    capsys.readouterr()


def test_main_erros_de_uso(ds, capsys):
    assert ds.main(["--live", "--scenario", "blocked"]) == 2
    assert ds.main(["--adventure-id", "r1"]) == 2                # opção live sem --live
    assert ds.main(["--no-start"]) == 2
    assert ds.main(["--attach"]) == 2
    assert "precisam de --live" in capsys.readouterr().err


def test_main_offline_json_e_relatorio_em_ficheiro(ds, bridge_mod, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(ds, "run_offline", lambda **kw: {
        "ok": True, "exit_code": 0, "mode": "offline", "checks": [], "summary": {},
        "kw": {k: str(v) for k, v in kw.items() if k in ("scenario", "timeout")}})
    report_file = tmp_path / "r.json"
    code = ds.main(["--json", "--report", str(report_file), "--scenario", "frozen"])
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["kw"] == {"scenario": "frozen", "timeout": str(ds.OFFLINE_TIMEOUT_S)}
    assert json.loads(report_file.read_text(encoding="utf-8")) == printed


def test_main_relatorio_numa_pasta_impossivel_nao_rebenta(ds, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(ds, "run_offline", lambda **kw: {
        "ok": True, "exit_code": 0, "mode": "offline", "checks": [], "summary": {
            "passed": 0, "total": 0, "failed": 0, "skipped": 0, "preconditions": 0,
            "timeouts": 0}})
    blocker = tmp_path / "ficheiro"
    blocker.write_text("x", encoding="utf-8")
    assert ds.main(["--report", str(blocker / "sub" / "r.json")]) == 0
    assert "não consegui gravar o relatório" in capsys.readouterr().err


# =========================================================================== #
# df_smoke — live (sessão, HTTP, relógio e ponte injetados)                   #
# =========================================================================== #


class LiveRig:
    """Jogo falso + servidor RPC + sessão/HTTP falsos para correr ``run_live`` sem o jogo."""

    def __init__(self, ds, bridge_mod, tmp_path, scenario="normal", *, running=True,
                 close_after_clicks=None, task_polls=2, backend_up=True):
        tmp_path = tmp_path / f"rig-{len(list(tmp_path.glob('rig-*')))}"
        tmp_path.mkdir()
        self.ds, self.bridge_mod = ds, bridge_mod
        self.game = ds.FakeGame(scenario, close_after_clicks=close_after_clicks)
        self.port = free_port()
        self.server = None
        self.df_path = tmp_path / "df"
        self.df_path.mkdir()
        (self.df_path / "dfhack").write_text("", encoding="utf-8")
        self.bridge = InProcessBridge(bridge_mod)
        self.clock = FakeClock()
        self.sessions: list = []
        self.rpc_opens_on_start = True        # False: o `df up` nunca chega a abrir o RPC
        self.stop_closes_server = True        # False: o jogo sobrevive ao `df stop`
        self.we_started_server = False
        self.started = self.stopped = 0
        self.exit_code = None
        self.stop_result = (True, "df stop ok")
        self.backend_up = backend_up
        self.task_polls, self.task = task_polls, None
        self.http_calls: list[tuple[str, str]] = []
        self.campaigns: list[dict] = []
        if running:
            self.start_server()

    # -- RPC
    def start_server(self):
        self.server = self.ds.FakeRpcServer(self.game.respond, self.bridge_mod, port=self.port)
        self.server.start()

    def stop_server(self):
        if self.server is not None:
            self.server.close()
            self.server = None

    # -- sessão falsa
    def session_factory(self, **kw):
        rig = self

        class Session:
            def __init__(self):
                rig.sessions.append(kw)

            def start(self):
                rig.started += 1
                if rig.server is None and rig.rpc_opens_on_start:
                    rig.start_server()
                    rig.we_started_server = True

            def exited(self):
                return rig.exit_code

            def log_tail(self):
                return "game.log: sk-or-v1-" + "ab" * 16 + " falhou a arrancar"

            def stop(self, timeout=60.0):
                rig.stopped += 1
                if rig.we_started_server and rig.stop_closes_server:   # só mata o que arrancou
                    rig.stop_server()
                return rig.stop_result

        return Session()

    # -- HTTP falso do backend
    def http(self, method, url, body, timeout):
        path = url.split("127.0.0.1:", 1)[1].split("/", 1)[1]
        self.http_calls.append((method, "/" + path))
        if path == "api/health":
            if not self.backend_up:
                raise OSError("connection refused")
            return 200, {"status": "ok", "version": "0.1.0"}
        if path == "api/campaigns" and method == "GET":
            return 200, self.campaigns
        if path == "api/campaigns" and method == "POST":
            self.campaigns.append({"id": "cid-1", "name": body["name"]})
            return 201, {"id": "cid-1"}
        if path.endswith("/adventures/status"):
            self.task["polls"] += 1
            if self.task["polls"] > self.task_polls:
                if self.task["kind"] in ("new-character", "load"):
                    self.game.configure("normal")
                return 200, {"task": {"state": "done"}}
            return 200, {"task": {"state": "running"}}
        if path.endswith("/adventures/cancel"):
            return 200, {"cancelled": True}
        if "/adventures/" in path and method == "POST":
            self.task = {"kind": path.rsplit("/", 1)[1], "polls": 0}
            return 202, {"task_id": "t-1", "kind": self.task["kind"]}
        raise AssertionError(f"rota inesperada {method} {url}")

    def opts(self, *flags, startup_timeout=None):
        argv = ["--live", "--df-path", str(self.df_path), "--rpc-port", str(self.port),
                "--library", str(LIBRARY), *flags]
        if startup_timeout is not None:
            argv += ["--startup-timeout", str(startup_timeout)]
        args = self.ds.build_parser().parse_args(argv)
        args.timeout = args.timeout or self.ds.LIVE_TIMEOUT_S
        return args

    def run(self, *flags, startup_timeout=None, sleep=None):
        return self.ds.run_live(
            self.opts(*flags, startup_timeout=startup_timeout), clock=self.clock,
            sleep=sleep or self.clock.sleep, http=self.http,
            session_factory=self.session_factory, runner=self.bridge)

    def close(self):
        self.stop_server()


@pytest.fixture()
def rig_factory(ds, bridge_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "MOVE_DELAY_S", 0.0)
    monkeypatch.setattr(ds, "OVERLAY_CLICK_SLEEP_MS", 0)         # o DSL dorme dentro da ponte
    monkeypatch.setattr(ds, "OVERLAY_KEY_SLEEP_MS", 0)
    rigs = []

    def make(scenario="normal", **kw):
        rig = LiveRig(ds, bridge_mod, tmp_path, scenario, **kw)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        rig.close()


LIVE_CHECKS = ["preflight", "start", "ready", "bridge_status", "adventure", "snapshot_schema",
               "snapshot_consumer", "pattern_library", "dsl_parse", "settle_overlays",
               "screen_patterns", "movement_proof", "stop"]


def test_live_arranque_a_frio_prova_e_pára_o_que_arrancou(rig_factory):
    rig = rig_factory(running=False)
    report = rig.run()
    checks = by_name(report)
    assert report["ok"], report["checks"]
    assert [c["name"] for c in report["checks"]] == LIVE_CHECKS
    assert report["mode"] == "live" and report["environment"]["attached"] is False
    assert rig.started == 1 and rig.stopped == 1
    assert checks["start"]["data"] == {"attached": False}
    assert checks["stop"]["status"] == "pass" and "jogo parado" in checks["stop"]["detail"]
    assert checks["movement_proof"]["data"]["delta"] != [0, 0]
    session_kwargs = rig.sessions[0]
    assert session_kwargs["backend_port"] == 8000 and session_kwargs["df_path"] == str(rig.df_path)


def test_live_recusa_um_jogo_em_uso_sem_attach(rig_factory):
    rig = rig_factory(running=True)
    report = rig.run()
    checks = by_name(report)
    assert report["exit_code"] == 3
    assert checks["start"]["status"] == "precondition" and "--attach" in checks["start"]["detail"]
    assert rig.sessions == [] and rig.started == rig.stopped == 0     # nada arrancado nem parado
    assert checks["movement_proof"]["status"] == "skip"               # nenhuma movida no jogo alheio
    assert not any("act batch" in c and "--max-steps" in c for c in rig.bridge.commands())


def test_live_jogo_ja_a_correr_com_attach_nao_e_parado_por_nos(rig_factory):
    rig = rig_factory(running=True)
    report = rig.run("--attach")
    checks = by_name(report)
    assert report["ok"] and report["environment"]["attached"] is True
    assert checks["start"]["data"] == {"attached": True}
    assert rig.stopped == 1                                      # df stop corre (só pára o que é nosso)
    assert "não era nosso" in checks["stop"]["detail"]
    assert rig.server is not None                                # o jogo externo continua a correr


def test_live_stop_falhado_e_regressao(rig_factory):
    rig = rig_factory(running=False)
    rig.stop_result = (False, "df stop saiu com 1")
    report = rig.run()
    assert report["exit_code"] == 1
    assert by_name(report)["stop"]["status"] == "fail"
    assert by_name(report)["movement_proof"]["status"] == "pass"


def test_live_jogo_que_sobrevive_ao_stop_e_falha(rig_factory):
    rig = rig_factory(running=False)
    rig.stop_closes_server = False               # `df stop` "ok" mas o jogo continua a responder
    report = rig.run()
    check = by_name(report)["stop"]
    assert check["status"] == "fail" and "continua alcançável" in check["detail"]


def test_live_sem_aventureiro_sem_flags_e_pre_condicao_mas_para_o_jogo(rig_factory):
    rig = rig_factory("no-adventurer", running=False)
    report = rig.run()
    checks = by_name(report)
    assert report["exit_code"] == 3
    assert checks["adventure"]["status"] == "precondition"
    assert "--adventure-id" in checks["adventure"]["detail"]
    assert checks["snapshot_schema"]["status"] == "skip"
    assert checks["stop"]["status"] == "pass" and rig.stopped == 1


def test_live_cria_mundo_e_personagem_pela_api(rig_factory):
    rig = rig_factory("no-adventurer", running=False)
    report = rig.run("--new-adventure")
    checks = by_name(report)
    assert report["ok"], report["checks"]
    assert checks["adventure"]["data"] == {"created": True, "campaign": "smoke-df"}
    posts = [p for m, p in rig.http_calls if m == "POST"]
    assert posts == ["/api/campaigns", "/api/campaigns/cid-1/adventures/new-world",
                     "/api/campaigns/cid-1/adventures/new-character"]
    assert rig.game.scenario == "normal"
    # o backend foi exigido (adventure precisa da API) antes de arrancar a tarefa
    assert ("GET", "/api/health") in rig.http_calls


def test_live_carrega_um_save_pela_api_e_reutiliza_a_campanha(rig_factory):
    rig = rig_factory("no-adventurer", running=False)
    rig.campaigns.append({"id": "cid-9", "name": "smoke-df"})
    report = rig.run("--adventure-id", "region1")
    assert report["ok"], report["checks"]
    assert ("POST", "/api/campaigns/cid-9/adventures/load") in rig.http_calls
    assert ("POST", "/api/campaigns") not in rig.http_calls


def test_live_tarefa_que_nao_termina_e_cancelada_e_falha(rig_factory):
    rig = rig_factory("no-adventurer", running=False, task_polls=10 ** 6)
    opts_timeout = 600.0
    args = rig.opts("--new-adventure")
    args.timeout = opts_timeout
    report = rig.ds.run_live(args, clock=rig.clock, sleep=rig.clock.sleep, http=rig.http,
                             session_factory=rig.session_factory, runner=rig.bridge)
    check = by_name(report)["adventure"]
    assert report["exit_code"] == 1 and check["status"] == "fail"
    assert "cancelada" in check["detail"]
    assert ("POST", "/api/campaigns/cid-1/adventures/cancel") in rig.http_calls
    assert rig.stopped == 1


def test_live_fecha_o_overlay_do_tutorial_antes_de_provar(rig_factory):
    rig = rig_factory("frozen", running=False, close_after_clicks=2)
    report = rig.run()
    checks = by_name(report)
    assert report["ok"], report["checks"]
    trail = checks["settle_overlays"]["data"]["trail"]
    assert len(trail) == 2 and trail[0].startswith("click Next")
    assert checks["movement_proof"]["status"] == "pass"
    # a ponte recebeu cliques SEM os guardas de foco (o foco muda de propósito)
    clicks = [c for c in rig.bridge.commands() if "click " in c and "act batch" in c]
    assert clicks and all("--stop-on-focus-change" not in c for c in clicks)
    moves = [c for c in rig.bridge.commands()
             if "act batch" in c and "--max-steps" in c and " goto " in c]
    assert moves and all("--stop-on-focus-change" in c and "--stop-on-threat" in c for c in moves)


def test_live_overlay_que_nao_fecha_e_falha_com_o_trilho(rig_factory):
    rig = rig_factory("frozen", running=False, close_after_clicks=None)
    report = rig.run()
    checks = by_name(report)
    assert report["exit_code"] == 1
    assert checks["settle_overlays"]["status"] == "fail"
    assert "dungeonmode/Help" in checks["settle_overlays"]["detail"]
    assert checks["movement_proof"]["status"] == "skip"
    assert len(checks["settle_overlays"]["data"]["trail"]) == rig.ds.SETTLE_MAX_STEPS


def test_live_parede_em_todas_as_direcoes_falha_a_prova(rig_factory):
    rig = rig_factory("blocked", running=False)
    report = rig.run()
    check = by_name(report)["movement_proof"]
    assert report["exit_code"] == 1 and check["status"] == "fail"
    assert check["data"]["diagnosis"] == "blocked_or_frozen"
    assert rig.stopped == 1


def test_live_arranque_que_nunca_fica_pronto_e_timeout_exit_4(rig_factory):
    rig = rig_factory(running=False)
    rig.rpc_opens_on_start = False
    report = rig.run(startup_timeout=10)
    checks = by_name(report)
    assert report["exit_code"] == 4
    assert checks["ready"]["status"] == "timeout" and "10s" in checks["ready"]["detail"]
    assert checks["bridge_status"]["status"] == "skip"
    assert rig.stopped == 1 and rig.clock.sleeps and set(rig.clock.sleeps) == {2.0}


def test_live_df_up_que_morre_cedo_falha_com_o_log_redigido(rig_factory):
    rig = rig_factory(running=False)
    rig.rpc_opens_on_start = False
    rig.exit_code = 1                            # o supervisor já saiu com erro
    report = rig.run()
    check = by_name(report)["ready"]
    assert report["exit_code"] == 1 and check["status"] == "fail"
    assert "terminou cedo" in check["detail"] and "falhou a arrancar" in check["detail"]
    assert "ab" * 16 not in check["detail"].replace("<redacted>", "")


def test_live_ctrl_c_pára_o_jogo_e_sai_130(rig_factory):
    rig = rig_factory(running=False)
    rig.rpc_opens_on_start = False

    def interrupt(seconds):
        raise KeyboardInterrupt

    report = rig.run(sleep=interrupt)
    assert report["exit_code"] == 130 and report["ok"] is False
    assert rig.stopped == 1
    assert by_name(report)["stop"]["status"] == "pass"


def test_live_pre_condicao_df_em_falta_nao_arranca_nada(ds, bridge_mod, tmp_path):
    created = []
    missing = Path.home() / "nao-existe-df-smoke-xyz"
    args = ds.build_parser().parse_args(["--live", "--df-path", str(missing)])
    args.timeout = ds.LIVE_TIMEOUT_S
    report = ds.run_live(args, clock=FakeClock(), sleep=lambda s: None,
                         http=lambda *a: (_ for _ in ()).throw(AssertionError("sem HTTP")),
                         session_factory=lambda **kw: created.append(kw),
                         runner=InProcessBridge(bridge_mod))
    assert report["exit_code"] == 3 and created == []
    assert report["checks"][0]["status"] == "precondition"
    statuses = {c["name"]: c["status"] for c in report["checks"][1:]}
    assert statuses.pop("pattern_library") == "pass"            # independente do jogo: corre
    assert set(statuses.values()) == {"skip"}
    assert "Dwarf Fortress/DFHack não encontrado" in report["checks"][0]["detail"]
    assert "~/nao-existe-df-smoke-xyz" in report["checks"][0]["detail"]
    assert str(Path.home()) not in json.dumps(report)           # a home nunca vai para o relatório


def test_live_no_start_e_keep_running_nao_tocam_na_sessao(rig_factory):
    rig = rig_factory(running=True)
    report = rig.run("--no-start")
    assert report["ok"] and rig.sessions == [] and rig.stopped == 0
    assert "start" not in by_name(report) and "stop" not in by_name(report)

    rig2 = rig_factory(running=False)
    report2 = rig2.run("--keep-running")
    assert report2["ok"] and rig2.started == 1 and rig2.stopped == 0
    assert "stop" not in by_name(report2)


# --------------------------------------------------------------------------- #
# LiveSession / BackendApi / wait_ready — peças isoladas                      #
# --------------------------------------------------------------------------- #


def test_live_session_comandos_ambiente_e_escalada_de_paragem(ds, tmp_path, monkeypatch):
    monkeypatch.setenv("SOD_OPENROUTER_KEY", "nao-deve-passar")
    monkeypatch.setenv("MY_SERVICE_TOKEN", "nem-isto")
    popen_calls, run_calls = [], []

    class Proc:
        pid = 4242
        waits = 0

        def poll(self):
            return None

        def wait(self, timeout=None):
            Proc.waits += 1
            if Proc.waits == 1:
                raise subprocess.TimeoutExpired("df", timeout)
            return 0

    def popen(cmd, **kw):
        popen_calls.append((cmd, kw))
        return Proc()

    def runner(cmd, **kw):
        run_calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    killed = []
    monkeypatch.setattr(ds.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    session = ds.LiveSession(df_path="/df", backend_port=8123, runtime_dir=tmp_path / "rt",
                             popen=popen, runner=runner)
    session.start()
    cmd, kw = popen_calls[0]
    assert cmd[:3] == ["bash", str(ds.DF_SH_PATH), "up"] and "--no-web" in cmd
    assert cmd[cmd.index("--port") + 1] == "8123" and cmd[cmd.index("--df-path") + 1] == "/df"
    assert kw["start_new_session"] is True and kw["stdin"] == subprocess.DEVNULL
    env = kw["env"]
    assert env["DF_RUNTIME_DIR"] == str(tmp_path / "rt")
    assert env["SOD_CAMPANHAS_DIR"].startswith(str(tmp_path / "rt"))
    assert "SOD_OPENROUTER_KEY" not in env and "MY_SERVICE_TOKEN" not in env
    assert session.exited() is None

    ok, detail = session.stop(timeout=5)
    assert run_calls[0][0][2] == "stop" and "--no-web" not in run_calls[0][0]
    assert ok is False and "TERM ao grupo" in detail
    assert killed == [(4242, ds.signal.SIGTERM)]


def test_live_session_log_tail_redigido(ds, tmp_path):
    session = ds.LiveSession(df_path="/df", backend_port=1, runtime_dir=tmp_path)
    assert session.log_tail() == ""
    key = fake_secret("openrouter")
    session.log_path.write_text("\n".join(f"linha {i}" for i in range(30)) + f"\nchave {key}\n",
                                encoding="utf-8")
    tail = session.log_tail()
    assert key not in tail and "linha 29" in tail and "linha 0 " not in tail


def test_wait_ready_estados(ds, bridge_mod, tmp_path):
    rpc_state = {"up": False}

    def handler(argv):
        info = {"df_path": "/df", "rpc": {"port": 5000, "reachable": rpc_state["up"]}}
        return 0, json.dumps(info)

    bridge = ds.BridgeClient(df_path="/df", runner=stub_runner(handler), env={})
    clock = FakeClock()
    healthy = lambda *a: (200, {"status": "ok"})                    # noqa: E731

    def sleep(s):
        clock.sleep(s)
        if len(clock.sleeps) == 2:
            rpc_state["up"] = True

    status, detail, data = ds.wait_ready(
        bridge=bridge, http=healthy, backend_port=8000, need_backend=True, session=None,
        timeout=60, clock=clock, sleep=sleep)
    assert status == ds.PASS and clock.sleeps == [2.0, 2.0] and data["backend"] is True

    rpc_state["up"] = False
    clock2 = FakeClock()
    status, detail, data = ds.wait_ready(
        bridge=bridge, http=healthy, backend_port=8000, need_backend=False, session=None,
        timeout=5, clock=clock2, sleep=clock2.sleep)
    assert status == ds.TIMEOUT and "5s" in detail and data == {"rpc": False, "backend": True}

    # backend em baixo mas não exigido: o RPC chega
    rpc_state["up"] = True
    down = lambda *a: (_ for _ in ()).throw(OSError("recusado"))   # noqa: E731
    assert ds.wait_ready(bridge=bridge, http=down, backend_port=8000, need_backend=False,
                         session=None, timeout=1, clock=FakeClock(),
                         sleep=lambda s: None)[0] == ds.PASS
    assert ds.wait_ready(bridge=bridge, http=down, backend_port=8000, need_backend=True,
                         session=None, timeout=0, clock=FakeClock(),
                         sleep=lambda s: None)[0] == ds.TIMEOUT


class ScriptedHttp:
    """HTTP falso por (método, caminho); a última resposta de cada fila repete-se."""

    BASE = "http://127.0.0.1:8000"

    def __init__(self) -> None:
        self.script: dict[tuple[str, str], list] = {}
        self.calls: list[tuple[str, str]] = []

    def on(self, method, path, *responses):
        self.script[(method, path)] = list(responses)
        return self

    def __call__(self, method, url, body, timeout):
        key = (method, url[len(self.BASE):])
        self.calls.append(key)
        queue = self.script[key]
        return queue.pop(0) if len(queue) > 1 else queue[0]


def test_backend_api_anexa_a_campanha_existente_em_qualquer_forma(ds):
    for listing in ([{"id": "c1", "name": "smoke-df"}],
                    {"campaigns": [{"campaign_id": "c1", "name": "smoke-df"}]}):
        http = ScriptedHttp().on("GET", "/api/campaigns", (200, listing))
        assert ds.BackendApi(http.BASE, http).campaign_id("smoke-df") == "c1"
        assert ("POST", "/api/campaigns") not in http.calls


def test_backend_api_cria_a_campanha_quando_falta_e_falha_com_erro_http(ds):
    http = (ScriptedHttp().on("GET", "/api/campaigns", (200, []))
            .on("POST", "/api/campaigns", (201, {"campaign": {"id": "novo"}})))
    assert ds.BackendApi(http.BASE, http).campaign_id("smoke-df") == "novo"
    http.on("POST", "/api/campaigns", (500, {"detail": "x"}))
    with pytest.raises(RuntimeError, match="HTTP 500"):
        ds.BackendApi(http.BASE, http).campaign_id("smoke-df")
    http.on("POST", "/api/campaigns", (201, {}))                 # 201 sem id também é erro
    with pytest.raises(RuntimeError):
        ds.BackendApi(http.BASE, http).campaign_id("smoke-df")


ROOT_ADV = "/api/campaigns/c7/adventures"


def test_backend_api_tarefa_conclui_e_409_cancela_a_anterior_e_repete(ds):
    clock = FakeClock()
    http = (ScriptedHttp()
            .on("POST", f"{ROOT_ADV}/new-world", (409, {}), (202, {}))
            .on("POST", f"{ROOT_ADV}/cancel", (200, {}))
            .on("GET", f"{ROOT_ADV}/status", (200, {"task": {"state": "running"}}),
                (200, {"task": {"state": "done"}})))
    api = ds.BackendApi(http.BASE, http)
    assert api.run_task("c7", "new-world", {}, 100, clock=clock, sleep=clock.sleep) == (True, "done")
    assert http.calls.count(("POST", f"{ROOT_ADV}/new-world")) == 2
    assert ("POST", f"{ROOT_ADV}/cancel") in http.calls
    assert clock.sleeps == [ds.ADVENTURE_POLL_S]


def test_backend_api_tarefa_com_erro_ou_recusada_nao_conclui(ds):
    clock = FakeClock()
    http = (ScriptedHttp()
            .on("POST", f"{ROOT_ADV}/new-character", (202, {}))
            .on("GET", f"{ROOT_ADV}/status", (200, {"task": {"state": "error", "error": "boom"}}))
            .on("POST", f"{ROOT_ADV}/load", (404, {"detail": "desconhecida"})))
    api = ds.BackendApi(http.BASE, http)
    ok, detail = api.run_task("c7", "new-character", {}, 100, clock=clock, sleep=clock.sleep)
    assert ok is False and "boom" in detail
    ok, detail = api.run_task("c7", "load", {}, 100, clock=clock, sleep=clock.sleep)
    assert ok is False and "HTTP 404" in detail


def test_backend_api_tarefa_que_nao_acaba_e_cancelada_no_fim_do_orcamento(ds):
    clock = FakeClock()
    http = (ScriptedHttp()
            .on("POST", f"{ROOT_ADV}/new-world", (202, {}))
            .on("GET", f"{ROOT_ADV}/status", (200, {"task": {"state": "running"}}))
            .on("POST", f"{ROOT_ADV}/cancel", (200, {})))
    ok, detail = ds.BackendApi(http.BASE, http).run_task(
        "c7", "new-world", {}, 20, clock=clock, sleep=clock.sleep)
    assert ok is False and "cancelada" in detail
    assert http.calls[-1] == ("POST", f"{ROOT_ADV}/cancel")
    assert clock.t >= 20


def test_default_http_recusa_hosts_fora_do_loopback(ds):
    for url in ("http://example.com/api/health", "https://127.0.0.1:8000/x",
                "http://localhost:8000/api/health", "file:///etc/passwd"):
        with pytest.raises(OSError):
            ds.default_http("GET", url, None, 1.0)


def test_default_http_fala_json_com_um_servidor_loopback(ds):
    """Servidor stdlib em loopback: o único teste do transporte HTTP real."""
    import http.server
    import threading

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _reply(self, code, payload):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            if self.path == "/redir":
                self.send_response(302)
                self.send_header("Location", "http://127.0.0.1:1/nunca")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path == "/ok":
                self._reply(200, {"status": "ok"})
            elif self.path == "/404":
                self._reply(404, {"detail": "nope"})
            else:
                self._reply(200, b"nao json")

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self._reply(202, {"echo": body})

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        assert ds.default_http("GET", base + "/ok", None, 5) == (200, {"status": "ok"})
        assert ds.default_http("GET", base + "/404", None, 5) == (404, {"detail": "nope"})
        assert ds.default_http("POST", base + "/x", {"a": 1}, 5) == (202, {"echo": {"a": 1}})
        assert ds.default_http("GET", base + "/raw", None, 5) == (200, {"raw": "nao json"})
        status, _ = ds.default_http("GET", base + "/redir", None, 5)   # redirects não se seguem
        assert status == 302
    finally:
        server.shutdown()
        server.server_close()
    with pytest.raises(OSError):
        ds.default_http("GET", base + "/ok", None, 1)            # já fechado


def test_bridge_client_nunca_levanta(ds):
    def missing(cmd, **kw):
        raise FileNotFoundError("python?")

    reply = ds.BridgeClient(df_path="/df", runner=missing, env={}).call("status")
    assert reply.code == 127 and "não consegui correr a ponte" in reply.stderr

    def hang(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 3)

    reply = ds.BridgeClient(df_path="/df", runner=hang, env={}).call("status", timeout=3)
    assert reply.timed_out and reply.code == -1

    seen = {}

    def spy(cmd, **kw):
        seen.update(cmd=cmd, kw=kw)
        return subprocess.CompletedProcess(cmd, 0, 'ruído {"a": 1}', "")

    client = ds.BridgeClient(df_path="/df", port=5999, via="tcp", env={"X": "1"}, runner=spy)
    reply, obj = client.json("status", "--json")
    assert obj == {"a": 1}
    assert seen["cmd"][-6:] == ["--df-path", "/df", "--port", "5999", "--via", "tcp"]
    assert seen["kw"]["env"] == {"X": "1"} and seen["kw"]["stdin"] == subprocess.DEVNULL


def test_modulos_exportam_o_que_declaram(ds, pc):
    for module in (ds, pc):
        assert module.__all__ and all(hasattr(module, name) for name in module.__all__), module


def test_scripts_sem_dataclass_nem_rede_externa():
    """Decisões do desenho: carregáveis sem registo em sys.modules e só stdlib/loopback."""
    import ast

    stdlib = set(sys.stdlib_module_names)
    for path in (DF_SMOKE, PATTERNS_CHECK):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
            elif isinstance(node, ast.ClassDef):
                names = {getattr(d, "id", getattr(getattr(d, "func", None), "id", None))
                         for d in node.decorator_list}
                assert "dataclass" not in names, f"{path.name}: {node.name}"
        # `system_one_dwarf` só é importado dentro de uma verificação, tolerante a ImportError
        assert imported - {"system_one_dwarf"} <= stdlib | {"__future__"}, imported - stdlib
        assert "dataclasses" not in imported


# =========================================================================== #
# workflows (.github/workflows)                                               #
# =========================================================================== #


def workflow_text(name: str) -> str:
    return (WORKFLOWS / f"{name}.yml").read_text(encoding="utf-8")


def load_yaml(name: str):
    """PyYAML quando existe (uvicorn[standard] traz-o); senão ``None`` (fallback textual)."""
    try:
        import yaml
    except ImportError:
        return None
    return yaml.safe_load(workflow_text(name))


def job_blocks(text: str) -> dict[str, str]:
    """Blocos de ``jobs:`` por texto (sem PyYAML): chaves a 2 espaços de indentação."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "jobs:")
    blocks, current, name = {}, [], None
    for line in lines[start + 1:]:
        if line and not line.startswith(" ") and not line.startswith("#"):
            break
        head = re.match(r"^  ([A-Za-z0-9_-]+):\s*$", line)
        if head:
            if name:
                blocks[name] = "\n".join(current)
            name, current = head.group(1), []
        elif name is not None:
            current.append(line)
    if name:
        blocks[name] = "\n".join(current)
    return blocks


def trigger_names(text: str) -> set[str]:
    """Gatilhos de ``on:`` por texto (sem PyYAML, onde ``on`` até vira booleano)."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip() == "on:")
    names = set()
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "#")):
            break
        head = re.match(r"^  ([a-z_]+):", line)
        if head:
            names.add(head.group(1))
    return names


def run_blocks(text: str) -> list[str]:
    """Conteúdo de cada ``run:`` (escalar de bloco ou inline), por texto."""
    out, lines, i = [], text.splitlines(), 0
    while i < len(lines):
        m = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2)
        if not rest:                                  # `defaults: run:` é um mapa, não um script
            i += 1
            continue
        if rest[:1] in ("|", ">"):
            body = []
            i += 1
            while i < len(lines) and (not lines[i].strip() or
                                      len(lines[i]) - len(lines[i].lstrip()) > indent):
                body.append(lines[i])
                i += 1
            out.append(textwrap.dedent("\n".join(body)))
        else:
            out.append(rest)
            i += 1
    return out


def yaml_steps(data) -> list[dict]:
    return [s for job in data["jobs"].values() for s in job.get("steps", [])]


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_workflow_existe_e_e_texto_yaml_sensato(name):
    text = workflow_text(name)
    assert text.endswith("\n") and "\r" not in text and "\t" not in text
    assert re.search(r"(?m)^name: \S", text) and re.search(r"(?m)^on:\s*$", text)
    assert re.search(r"(?m)^permissions:\n  contents: read\n", text), "permissions mínimas"
    assert re.search(r"(?m)^jobs:\s*$", text)
    blocks = job_blocks(text)
    assert blocks, "sem jobs"
    for job, block in blocks.items():
        assert re.search(r"(?m)^    (runs-on|uses):", block), job
        if re.search(r"(?m)^    runs-on:", block):
            assert re.search(r"(?m)^    steps:", block), f"job {job} sem steps"


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_workflow_yaml_parseia_e_tem_forma(name):
    data = load_yaml(name)
    if data is None:
        pytest.skip("PyYAML indisponível: a verificação textual acima já correu")
    assert isinstance(data, dict) and isinstance(data["jobs"], dict) and data["jobs"]
    assert (True in data or "on" in data), "sem gatilhos"
    assert data.get("permissions") == {"contents": "read"}
    for job_id, job in data["jobs"].items():
        assert ("runs-on" in job and isinstance(job.get("steps"), list)) or "uses" in job, job_id
        for step in job.get("steps", []):
            assert "uses" in step or "run" in step, (job_id, step)
        for needed in ([job["needs"]] if isinstance(job.get("needs"), str) else job.get("needs", [])):
            assert needed in data["jobs"], (job_id, needed)


def test_fallback_textual_e_yaml_veem_os_mesmos_jobs_e_runs():
    for name in WORKFLOW_NAMES:
        data = load_yaml(name)
        if data is None:
            pytest.skip("PyYAML indisponível")
        assert list(job_blocks(workflow_text(name))) == list(data["jobs"])
        yaml_runs = [s["run"].strip() for s in yaml_steps(data) if "run" in s]
        text_runs = [r.strip() for r in run_blocks(workflow_text(name))]
        assert sorted(yaml_runs) == sorted(text_runs), name


@pytest.mark.parametrize("name", WORKFLOW_NAMES)
def test_workflow_seguranca_basica(name):
    text = workflow_text(name)
    assert "pull_request_target" not in text and "workflow_run" not in text
    assert "write-all" not in text
    for step_name, body in re.findall(
            r"(?m)^      - name: (.+)\n((?:        .*\n|\n)+)", text):
        if "continue-on-error: true" in body:
            assert "non-blocking" in step_name, step_name
    # ${{ }} nunca dentro de run: (injeção via nomes de branch/tag/títulos)
    for block in run_blocks(text):
        assert "${{" not in block, block
    # actions de terceiros fixadas por tag major ou SHA; as locais têm de existir
    for ref in re.findall(r"(?m)^\s*(?:- )?uses:\s*(\S+)", text):
        if ref.startswith("./"):
            assert (ROOT / ref).is_file(), ref
        else:
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@(v\d+|[0-9a-f]{40})", ref), ref
    # todo o job com steps tem timeout (um teste pendurado não come a quota)
    for job, block in job_blocks(text).items():
        if re.search(r"(?m)^    steps:", block):
            assert re.search(r"(?m)^    timeout-minutes: \d+", block), (name, job)
    # nunca persiste credenciais no checkout
    for chunk in re.findall(r"uses: actions/checkout@v\d+\n(?:\s+with:\n(?:\s+\S.*\n)*)?", text):
        assert "persist-credentials: false" in chunk, chunk


def test_ci_estrutura_e_passos_prometidos():
    text = workflow_text("ci")
    assert set(job_blocks(text)) == {"python", "web", "shell", "secrets", "gate"}
    assert trigger_names(text) == {"push", "pull_request", "workflow_dispatch", "workflow_call"}
    assert re.search(r"(?m)^    branches: \[main\]$", text)
    assert re.search(r"(?m)^  cancel-in-progress: true$", text)
    assert re.search(r'(?m)^        python-version: \["3\.12", "3\.14"\]$', text)
    assert re.search(r"(?m)^    needs: \[python, web, shell, secrets\]$", text)
    data = load_yaml("ci")
    if data is not None:
        on = data.get(True, data.get("on"))
        assert on["push"]["branches"] == ["main"]
        assert data["concurrency"]["cancel-in-progress"] is True
        assert data["jobs"]["python"]["strategy"]["matrix"]["python-version"] == ["3.12", "3.14"]
        assert data["jobs"]["gate"]["needs"] == ["python", "web", "shell", "secrets"]
    runs = "\n".join(run_blocks(text))
    for needle in ("pip install -r requirements.txt", "pytest -q", "dfhack/test_bridge.py",
                   "dfhack/test_snapshot.py", "luac", "-p dfhack/*.lua", "apt-get install",
                   "lua5.4", "scripts/smoke/df_smoke.py --offline",
                   "scripts/smoke/patterns_check.py", "python3 scripts/golden.py check",
                   "bash -n", "shellcheck", "git grep"):
        assert needle in runs, needle
    # o golden set é guardado: só corre quando o script existe
    assert re.search(r"if \[ -f scripts/golden\.py \]; then\s+python3 scripts/golden\.py check", runs)
    # o único segredo opcional é o do registo Motion+ (nunca outros)
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"MOTION_TOKEN"}
    assert "shellcheck" in text and re.search(
        r"(?s)ShellCheck \(non-blocking\)\n\s+continue-on-error: true", text)


def test_ci_web_so_usa_scripts_que_o_package_json_define():
    scripts = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))["scripts"]
    runs = "\n".join(run_blocks(workflow_text("ci")))
    used = set(re.findall(r"npm run ([\w:-]+)", runs))
    assert {"typecheck", "build"} <= used and used <= set(scripts), (used, set(scripts))
    assert (ROOT / "web" / "package-lock.json").is_file()
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"]["node_modules/oxlint"]["version"]    # o lint token-free lê isto


def test_release_estrutura_e_gates():
    text = workflow_text("release")
    data = load_yaml("release")
    assert re.search(r'tags:\s*\n\s*- "v\*\.\*\.\*"', text)
    assert trigger_names(text) == {"push"}
    assert set(job_blocks(text)) == {"verify", "release"}
    assert re.search(r"(?m)^    permissions:\n      contents: write$", text)   # só o job que publica
    assert "uses: ./.github/workflows/ci.yml" in text            # "os mesmos testes"
    assert "needs: verify" in text and "gh release create" in text and "--verify-tag" in text
    assert "GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in text
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == {"GITHUB_TOKEN"}
    if data is not None:
        assert data["jobs"]["release"]["permissions"] == {"contents": "write"}
        assert "permissions" not in data["jobs"]["verify"]
        assert data["concurrency"]["cancel-in-progress"] is False


def test_smoke_so_corre_ao_vivo_num_runner_proprio_e_nunca_em_pr():
    text = workflow_text("smoke")
    data = load_yaml("smoke")
    code = re.sub(r"(?m)^\s*#.*$", "", text)                    # os comentários explicam porquê
    assert trigger_names(text) == {"workflow_dispatch", "schedule"}
    assert "pull_request" not in code and "push:" not in code
    assert re.search(r"runs-on:\s*\[self-hosted, linux, dwarf-fortress\]", text)
    assert "scripts/smoke/df_smoke.py" in text and "--live" in text
    assert re.search(r'cron: "(\S+ ){4}\S+"', text)
    assert set(re.findall(r"secrets\.([A-Z_]+)", text)) == set()
    if data is not None:
        on = data.get(True, data.get("on"))
        assert set(on) == {"workflow_dispatch", "schedule"}
        assert on["schedule"][0]["cron"].count(" ") == 4
        assert data["jobs"]["live"]["runs-on"] == ["self-hosted", "linux", "dwarf-fortress"]


def test_caminhos_referenciados_nos_workflows_existem():
    generated = {"smoke-offline.json", "smoke-live.json", "release-notes.md", "scripts/golden.py"}
    tokens = re.compile(r"(?<![\w./-])((?:scripts|dfhack|src|tests|docs|web|\.github)/[\w./*-]+|"
                        r"install\.sh|requirements\.txt|pyproject\.toml|CHANGELOG\.md)")
    for name in WORKFLOW_NAMES:
        for block in run_blocks(workflow_text(name)):
            for token in tokens.findall(block):
                token = token.rstrip(".,;:)")
                if token in generated:
                    continue
                if "*" in token:
                    assert list(ROOT.glob(token)), (name, token)
                else:
                    assert (ROOT / token).exists(), (name, token)
    # o único passo opcional é o golden set, e está guardado por `[ -f ... ]`
    assert "scripts/golden.py" in "\n".join(run_blocks(workflow_text("ci")))


@pytest.mark.parametrize("script", [DF_SMOKE, PATTERNS_CHECK])
def test_scripts_dos_workflows_respondem_a_help(script):
    proc = subprocess.run([sys.executable, str(script), "--help"], capture_output=True,
                          text=True, timeout=60)
    assert proc.returncode == 0 and "usage:" in proc.stdout.lower()


# --------------------------------------------------------------------------- #
# varredura de segredos (job `secrets`)                                       #
# --------------------------------------------------------------------------- #


def secrets_pattern() -> str:
    match = re.search(r"(?m)^\s*PATTERN='([^']+)'$", workflow_text("ci"))
    assert match, "o job `secrets` perdeu a variável PATTERN"
    return match.group(1)


def grep_matches(pattern: str, text: str, tmp_path: Path) -> bool:
    sample = tmp_path / "amostra.txt"
    sample.write_text(text + "\n", encoding="utf-8")
    if shutil.which("grep"):
        return subprocess.run(["grep", "-E", "-e", pattern, str(sample)],
                              capture_output=True).returncode == 0
    return re.search(pattern, text) is not None


def real_looking_secrets() -> dict[str, str]:
    """Montadas em runtime: o texto literal não pode existir no repositório."""
    return {
        "openrouter": "sk-or-v1-" + "0123456789abcdef" * 4,
        "anthropic": "sk-ant-" + "api03-" + "A" * 24,
        "generic-sk": "sk-" + "A1b2C3d4" * 5,
        "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
        "github-token": "ghp_" + "a1B2" * 10,
        "github-pat": "github_pat_" + "A" * 22 + "_" + "b" * 10,
        "gitlab": "glpat-" + "x" * 20,
        "tavily": "tvly-" + "dev-" + "a" * 16,
        "npm": "npm_" + "a" * 36,
        "slack": "xoxb-" + "1234567890-" + "abcdef",
        "google": "AIza" + "A" * 35,
        "private-key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    }


def test_varredura_de_segredos_apanha_chaves_reais_e_ignora_as_falsas(tmp_path):
    pattern = secrets_pattern()
    for kind, secret in real_looking_secrets().items():
        assert grep_matches(pattern, f"export KEY={secret}", tmp_path), kind
    fakes = ["sk-or-v1-test-key-not-a-real-secret", "sk-or-v1-CHAVE-FALSA-NAO-IMPRIMIR-98765",
             "sk-or-v1-DO-FICHEIRO-NAO-IMPRIMIR-67890", "sk-or-v1-offline-test-key",
             "TEST-KEY-NOT-REAL-0001", "sk-or-v1••••••••3f9a", "sk-or-v1-...", "ghp_curto",
             "AKIAshort"]
    for fake in fakes:
        assert not grep_matches(pattern, fake, tmp_path), fake


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="precisa de um checkout git")
def test_repositorio_passa_a_varredura_de_segredos_do_ci():
    """O que o job `secrets` faria: nenhum ficheiro versionado (ou novo) casa."""
    proc = subprocess.run(
        ["git", "grep", "--untracked", "-InE", secrets_pattern(), "--", ".",
         ":(exclude)web/package-lock.json", ":(exclude)web/node_modules",
         ":(exclude)campanhas", ":(exclude).runtime", ":(exclude).venv"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)
    hits = [line.split(":", 2)[:2] for line in proc.stdout.splitlines()]
    assert proc.returncode == 1 and not hits, hits               # 1 = sem correspondências


# --------------------------------------------------------------------------- #
# release.yml — scripts embebidos (validação de tag e notas)                  #
# --------------------------------------------------------------------------- #


def embedded_python(name: str) -> list[str]:
    snippets = re.findall(r"python3 - <<'PY'\n(.*?)\n\s*PY\n", workflow_text(name), re.S)
    return [textwrap.dedent(s) for s in snippets]


@pytest.fixture()
def release_tree(tmp_path):
    (tmp_path / "src" / "system_one_dwarf").mkdir(parents=True)
    (tmp_path / "web").mkdir()
    return tmp_path


def seed_versions(root: Path, version: str, web: str | None = None) -> None:
    (root / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n',
                                         encoding="utf-8")
    (root / "src" / "system_one_dwarf" / "__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8")
    (root / "web" / "package.json").write_text(
        json.dumps({"name": "w", "version": web or version}), encoding="utf-8")


def run_snippet(code: str, cwd: Path, **env) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if k not in ("TAG", "ALLOW_STABLE")}
    base.update(env)
    return subprocess.run([sys.executable, "-"], input=code, cwd=cwd, env=base,
                          capture_output=True, text=True, timeout=60)


def test_release_tem_os_dois_scripts_embebidos():
    assert len(embedded_python("release")) == 2


@pytest.mark.parametrize("tag,files,allow,ok,needle", [
    ("v0.1.0", "0.1.0", "", True, "coerente"),
    ("v0.2.0", "0.1.0", "", False, "tem versão '0.1.0' mas a tag é v0.2.0"),
    ("v1.0.0", "1.0.0", "", False, "política 0.y.z"),
    ("v1.0.0", "1.0.0", "true", True, "coerente"),
    ("v1.2.3", "1.2.3", "TRUE", True, "coerente"),
    ("v0.2.0-rc.1", "0.2.0", "", True, "coerente"),
    ("v0.2.0-rc.1", "0.2.0rc1", "", True, "coerente"),
    ("v0.2.0-rc.1", "0.1.0", "", False, "tag"),
    ("v1.0", "1.0", "", False, "não é SemVer"),
    ("v01.0.0", "01.0.0", "", False, "não é SemVer"),
    ("release-1", "0.1.0", "", False, "não é SemVer"),
])
def test_release_valida_tag_versao_e_politica(release_tree, tag, files, allow, ok, needle):
    seed_versions(release_tree, files)
    validate = embedded_python("release")[0]
    proc = run_snippet(validate, release_tree, TAG=tag, ALLOW_STABLE=allow)
    assert (proc.returncode == 0) is ok, proc.stdout + proc.stderr
    assert needle in proc.stdout, proc.stdout


def test_release_avisa_quando_web_package_json_diverge(release_tree):
    seed_versions(release_tree, "0.3.0", web="0.1.0")
    proc = run_snippet(embedded_python("release")[0], release_tree, TAG="v0.3.0")
    assert proc.returncode == 0 and "::warning::" in proc.stdout


CHANGELOG_FIXTURE = """# Changelog

## [Unreleased]

### Added
- coisa por sair

## [0.2.0] - 2026-11-01

### Added
- segunda versão

### Fixed
- um erro

## [0.1.0] - 2026-10-09

### Added
- primeira versão

[Unreleased]: https://example.invalid/compare/v0.2.0...HEAD
[0.1.0]: https://example.invalid/releases/tag/v0.1.0
"""


def extract_notes(tmp_path, tag, changelog=CHANGELOG_FIXTURE):
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    (tmp_path / "release-notes.md").unlink(missing_ok=True)
    proc = run_snippet(embedded_python("release")[1], tmp_path, TAG=tag)
    notes = tmp_path / "release-notes.md"
    return proc, (notes.read_text(encoding="utf-8") if notes.exists() else None)


def test_release_extrai_so_a_seccao_da_versao(tmp_path):
    proc, notes = extract_notes(tmp_path, "v0.2.0")
    assert proc.returncode == 0, proc.stdout
    assert notes == "### Added\n- segunda versão\n\n### Fixed\n- um erro\n"
    assert "primeira versão" not in notes and "coisa por sair" not in notes


def test_release_ultima_seccao_nao_leva_links_de_rodape(tmp_path):
    proc, notes = extract_notes(tmp_path, "v0.1.0")
    assert proc.returncode == 0 and notes == "### Added\n- primeira versão\n"
    assert "example.invalid" not in notes


def test_release_versao_sem_seccao_ou_vazia_falha(tmp_path):
    proc, notes = extract_notes(tmp_path, "v0.9.0")
    assert proc.returncode == 1 and notes is None and "não tem a secção" in proc.stdout
    empty = "## [Unreleased]\n\n## [0.3.0] - 2026-12-01\n\n## [0.1.0] - 2026-10-09\n- x\n"
    proc, notes = extract_notes(tmp_path, "v0.3.0", empty)
    assert proc.returncode == 1 and notes is None


def test_release_prerelease_sem_seccao_usa_unreleased(tmp_path):
    proc, notes = extract_notes(tmp_path, "v0.3.0-rc.1")
    assert proc.returncode == 0 and notes == "### Added\n- coisa por sair\n"
    stable, none = extract_notes(tmp_path, "v0.3.0")             # estável NÃO tem fallback
    assert stable.returncode == 1 and none is None


def test_release_extrai_notas_do_changelog_real_para_a_versao_do_codigo(tmp_path):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["version"]
    proc, notes = extract_notes(tmp_path, f"v{version}", CHANGELOG.read_text(encoding="utf-8"))
    assert proc.returncode == 0, proc.stdout
    assert notes and len(notes.splitlines()) > 5 and "##" not in notes.replace("###", "")


# =========================================================================== #
# CHANGELOG.md e docs/ci.md                                                   #
# =========================================================================== #


def changelog_sections() -> list[tuple[str, str]]:
    text = CHANGELOG.read_text(encoding="utf-8")
    heads = list(re.finditer(r"(?m)^## \[([^\]]+)\](.*)$", text))
    return [(m.group(1), m.group(2)) for m in heads]


def test_changelog_formato_keep_a_changelog():
    text = CHANGELOG.read_text(encoding="utf-8")
    assert text.startswith("# Changelog\n")
    assert "keepachangelog.com" in text and "semver.org" in text
    sections = changelog_sections()
    assert sections[0] == ("Unreleased", "")
    versions = [s for s in sections[1:]]
    assert versions, "sem versões lançadas"
    semver = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
    previous = None
    for version, rest in versions:
        assert semver.match(version), version
        assert re.fullmatch(r" - \d{4}-\d{2}-\d{2}", rest), (version, rest)
        key = tuple(int(p) for p in version.split("."))
        assert previous is None or key < previous, "versões fora de ordem decrescente"
        previous = key
    allowed = {"Added", "Changed", "Deprecated", "Removed", "Fixed", "Security"}
    assert set(re.findall(r"(?m)^### (.+)$", text)) <= allowed
    assert re.search(r"(?m)^\[Unreleased\]: https://", text)


def test_changelog_tem_a_versao_do_codigo_e_a_do_mvp():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"]["version"]
    released = [v for v, _ in changelog_sections()[1:]]
    assert version in released, "a versão do código não tem secção no CHANGELOG"
    assert "0.1.0" in released
    assert f'__version__ = "{version}"' in (
        ROOT / "src" / "system_one_dwarf" / "__init__.py").read_text(encoding="utf-8")


def test_changelog_unreleased_lista_as_12_mudancas_e_os_riscos():
    text = CHANGELOG.read_text(encoding="utf-8")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert {f"{n:02d}" for n in range(1, 13)} <= set(re.findall(r"§8 #(\d\d)", unreleased))
    for needle in ("CI no GitHub", "Smoke automatizado", "Riscos de §9", "H4", "H7"):
        assert needle in unreleased, needle
    arquitetura = (ROOT / "ARQUITETURA.md").read_text(encoding="utf-8")
    assert len(re.findall(r"(?m)^\| (\d\d) \| \*\*", arquitetura)) == 12   # a fonte continua a ter 12


def test_changelog_0_1_0_resume_o_mvp():
    text = CHANGELOG.read_text(encoding="utf-8")
    mvp = text.split("## [0.1.0]", 1)[1]
    for needle in ("Ponte DFHack", "Frota JEV", "Backend FastAPI", "UI web", "comando global `df`",
                   "Biblioteca de padrões", "`caca-2`"):
        assert needle in mvp, needle


def test_doc_ci_descreve_workflows_scripts_e_comandos_que_existem():
    doc = CI_DOC.read_text(encoding="utf-8")
    for needle in ("ci.yml", "release.yml", "smoke.yml", "df_smoke.py", "patterns_check.py",
                   "0.y.z", "MOTION_TOKEN", "SOD_ALLOW_STABLE_RELEASE", "dwarf-fortress",
                   "v3.6.0", "Commits Convencionais"):
        assert needle in doc, needle
    suggested = {"scripts/golden.py", ".github/dependabot.yml"}    # documentados como futuros/opcionais
    for token in set(re.findall(r"`((?:scripts|dfhack|docs|\.github)/[\w./-]+\.\w+)`", doc)):
        if token not in suggested:
            assert (ROOT / token).exists(), token
    for code in ("0", "1", "2", "3", "4", "130"):
        assert re.search(rf"\| `{code}` \|", doc), code


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="precisa de um checkout git")
def test_comando_de_segredos_documentado_funciona_tal_como_escrito():
    doc = CI_DOC.read_text(encoding="utf-8")
    row = next(line for line in doc.splitlines() if line.startswith("| segredos |"))
    command = re.search(r"`(PATTERN=.+?)` \(sem saída", row).group(1).replace("\\|", "|")
    proc = subprocess.run(["bash", "-c", command], cwd=ROOT, capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "", proc.stdout
    assert "PATTERN=" in command and "git grep" in command


def test_nome_do_ficheiro_de_teste_e_unico_na_arvore():
    same = [p for p in (ROOT / "tests").rglob("test_smoke_scripts.py")]
    assert same == [Path(__file__).resolve()]
