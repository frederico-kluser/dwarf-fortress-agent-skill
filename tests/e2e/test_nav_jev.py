"""Testes offline do navegador dirigido por JEV (scripts/e2e/nav_jev.py).

A biblioteca ``docs/padroes-navegacao.json`` é a fonte de verdade dos padrões
COMPROVADOS: estes testes garantem que ela carrega, que a correspondência
ecrã→padrão funciona e que o fallback determinístico (fluxo provado) cobre a
ausência do JEV — sem tocar no jogo nem na rede.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
NAV_PATH = ROOT / "scripts" / "e2e" / "nav_jev.py"
LIBRARY_PATH = ROOT / "docs" / "padroes-navegacao.json"


def load(name, path=NAV_PATH):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def nav():
    return load("nav_jev_under_test")


@pytest.fixture(scope="module")
def library(nav):
    return nav.load_library(LIBRARY_PATH)


# --------------------------------------------------------------------------- #
# biblioteca                                                                  #
# --------------------------------------------------------------------------- #

def test_biblioteca_carrega_e_tem_padroes_e_fluxos(library):
    assert library["version"] >= 1
    assert library["patterns"], "a biblioteca tem de ter padrões"
    assert library["flows"]["title_to_ingame"], "o fluxo título→in-game tem de existir"
    for p in library["patterns"]:
        assert p["id"] and p["action"], f"padrão incompleto: {p}"
        assert "proven" in p, f"padrão sem proveniência: {p['id']}"
    ids = [p["id"] for p in library["patterns"]]
    assert len(ids) == len(set(ids)), "ids de padrão duplicados"


def test_biblioteca_tem_mecanica_de_combate_e_feedback_da_ponte(library):
    combat = library["combat"]
    assert "goto" in combat["bump_attack"] or "move" in combat["bump_attack"]
    assert "A_ATTACK" in combat["explicit_key"]
    codes = library["bridge_feedback"]["exit_codes"]
    assert codes["0"].startswith("EXIT_OK") and "13" in codes


# --------------------------------------------------------------------------- #
# correspondência ecrã → padrão                                               #
# --------------------------------------------------------------------------- #

def test_matches_exige_focus_e_texto(nav):
    p = {"id": "x", "focus": ["setupadventure"], "text_markers": ["Go!"], "action": "click"}
    assert nav.matches(p, "setupadventure", "… Go! …")
    assert not nav.matches(p, "dungeonmode", "… Go! …")
    assert not nav.matches(p, "setupadventure", "nada aqui")
    # sem marcadores de texto: o focus chega
    p2 = {"id": "y", "focus": ["dungeonmode"], "text_markers": [], "action": "key"}
    assert nav.matches(p2, "dungeonmode/Default", "")


def test_candidatos_para_ecrã_em_jogo(library, nav):
    cands = nav.candidates(library, "dungeonmode/Default", "mapa de aventura")
    ids = [p["id"] for p in cands]
    assert "p-move-or-goto" in ids
    assert "p-go-launch" not in ids  # padrão de outro ecrã


def test_candidatos_para_ecrã_de_lancamento(library, nav):
    text = "Adventure awaits\n┌──────┐\n│ Go!  │\n└──────┘"
    cands = nav.candidates(library, "setupadventure", text)
    ids = [p["id"] for p in cands]
    assert "p-ready-and-go" in ids


# --------------------------------------------------------------------------- #
# ações → DSL                                                                 #
# --------------------------------------------------------------------------- #

def test_split_actions_separa_sequencias(nav):
    assert nav.split_actions('click "A"; depois click "B"') == ['click "A"', 'click "B"']
    assert nav.split_actions('click_re \\bHuman\\b; click_re Select [a-z]+') == [
        "click_re \\bHuman\\b", "click_re Select [a-z]+"]
    assert nav.split_actions("key LEAVESCREEN") == ["key LEAVESCREEN"]


def test_action_to_dsl_tipos_fechados(nav):
    assert nav.action_to_dsl('click "Go!"') == ["click_label", "Go!"]
    assert nav.action_to_dsl("click_re Aventura|Adventurer") == ["click_re", "Aventura|Adventurer"]
    assert nav.action_to_dsl("click (180,1)") == ["click_xy", "180", "1"]
    assert nav.action_to_dsl("key LEAVESCREEN") == ["key", "LEAVESCREEN"]
    assert nav.action_to_dsl("poll (aguardar)") == ["noop"]


def test_run_dsl_label_nao_encontrado_nao_toca_no_jogo(nav):
    code, summary = nav.run_dsl(["click_label", "Nao Existe"], "ecrã sem o label", "df/path")
    assert code == 13 and "não encontrado" in summary


# --------------------------------------------------------------------------- #
# decisão: JEV ou fallback determinístico                                     #
# --------------------------------------------------------------------------- #

class FakeAnswer:
    def __init__(self, value, probability=0.9):
        self.value = value
        self.probability = probability


class FakeResult:
    def __init__(self, value, band):
        self._value, self._band = value, band

    def answer(self, name):
        return FakeAnswer(self._value)

    def band(self, name):
        return self._band


class FakeJev:
    def __init__(self, value="p2", band="auto"):
        self._value, self._band = value, band
        self.requests = []

    def decide_request(self, request):
        self.requests.append(request)
        return FakeResult(self._value, self._band)


def test_decide_jev_auto_escolhe_o_padrao(nav):
    cands = [{"id": "p1", "action": "key X"}, {"id": "p2", "action": "key Y"}]
    verdict = nav.decide(cands, "f", "t", client=FakeJev("p2", "auto"))
    assert verdict["source"] == "jev"
    assert verdict["pattern"]["id"] == "p2"


def test_decide_banda_hitl_cai_para_o_fluxo_provado(nav):
    cands = [{"id": "p1", "action": "key X"}, {"id": "p2", "action": "key Y"}]
    verdict = nav.decide(cands, "f", "t", client=FakeJev("p2", "hitl"))
    assert verdict["source"] == "fallback"
    assert verdict["pattern"]["id"] == "p1"  # 1º candidato = ordem do fluxo provado
    verdict = nav.decide(cands, "f", "t", client=FakeJev("p2", "abstain"))
    assert verdict["source"] == "fallback"


def test_decide_sem_candidatos_nao_inventa(nav):
    verdict = nav.decide([], "f", "t", client=FakeJev())
    assert verdict["pattern"] is None and verdict["source"] == "none"


def test_decide_sem_jev_usa_fallback(nav, monkeypatch):
    monkeypatch.setattr(nav, "load_jev_client", lambda: None)
    cands = [{"id": "p1", "action": "key X"}]
    verdict = nav.decide(cands, "f", "t")
    assert verdict["source"] == "fallback"
    assert verdict["pattern"]["id"] == "p1"


def test_request_jev_tem_saida_e_valida(nav):
    cands = [{"id": "p1", "action": "key X", "notes": "nota"}]
    request = nav.build_nav_request(cands, "dungeonmode/Default", "texto")
    assert request["questions"]["action"]["criteria"], "choice sem opções"
    assert "other" in request["questions"]["action"]["criteria"], "sem opção de saída"
    assert request["meta"]["role"] == "JEV-A"
    json.dumps(request)  # serializável para o transporte
