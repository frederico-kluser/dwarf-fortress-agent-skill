"""Testes offline do driver de aventura — bridge falso, zero jogo, zero rede.

Cobrem o motor de passos (regra das 5 tentativas + relatório PT), o cancelamento
(para de enviar teclas), os passos soft (GUESS nunca aborta o fluxo), a
construção dos fluxos com os parâmetros da API, a listagem de saves (pasta
falsa), o ``current`` pelo snapshot e o ``GameProcessManager`` (arranque/paragem
com o escalonamento TERM→KILL provado) com operações de processo injetadas.
"""
from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from system_one_dwarf.game.driver import (  # noqa: E402
    AdventureDriver,
    BridgeCommandError,
    GameProcessError,
    GameProcessManager,
    StepAbort,
    StepCancelled,
    SubprocessBridge,
    UnknownAdventure,
    check_expect,
    label_centre,
    load_steps,
    typed_keys,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #


class FakeBridge:
    """Bridge falso por fila: cada ``screen()`` devolve o próximo ecrã (o último repete)."""

    def __init__(self, screens=None, *, snapshot=None, save_root=None):
        self.screens = list(screens or [])
        self.calls = []
        self._snapshot = snapshot
        self._save_root = Path(save_root) if save_root else Path("/fake/df/data/save")
        self.on_action = None  # hook: chamado antes de cada ação (testes de cancelamento)

    def screen(self):
        self.calls.append(("screen",))
        if not self.screens:
            return "", ""
        return self.screens[0] if len(self.screens) == 1 else self.screens.pop(0)

    def click(self, x, y):
        self.calls.append(("click", x, y))
        if self.on_action is not None:
            self.on_action("click")

    def keys(self, names):
        self.calls.append(("keys", list(names)))
        if self.on_action is not None:
            self.on_action("keys")

    def command(self, argv):
        self.calls.append(("command", list(argv)))
        return ""

    def snapshot(self):
        self.calls.append(("snapshot",))
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot if self._snapshot is not None else {}

    def save_root(self):
        return self._save_root

    @property
    def actions(self):
        return [c for c in self.calls if c[0] in ("click", "keys")]


def step(name, directive, expect, **kw):
    return {"name": name, "expect": [expect], "keys": [directive], "wait": 0, **kw}


def make_driver(bridge, steps, **kw):
    kw.setdefault("sleep", lambda _s: None)
    kw.setdefault("poll_timeout", 2.0)
    return AdventureDriver(bridge, steps=steps, **kw)


# --------------------------------------------------------------------------- #
# Helpers do motor                                                              #
# --------------------------------------------------------------------------- #


def test_label_centre_devolve_o_centro_do_label():
    text = "menu\nCreate new world\noutro"
    assert label_centre(text, "Create new world") == (8, 1)
    assert label_centre(text, "Inexistente") is None
    assert label_centre("Select Omon Wur", r"^Select [A-Z]", regex=True) == (4, 0)


def test_check_expect_focus_screen_e_alternativas():
    assert check_expect("focus:new_region (anotação)", "new_region/Basic", "")
    assert not check_expect("focus:choose_game_type", "new_region/Basic", "")
    assert check_expect("screen: welcome (anotação)", "x", "Welcome to DF")
    assert check_expect(["focus:a", "focus:b"], "b/Default", "")
    assert check_expect("", "qualquer", "qualquer")  # vazio é sempre verdade (soft)


def test_typed_keys_escreve_ascii_como_string_a():
    assert typed_keys("Ab9") == ["STRING_A065", "STRING_A098", "STRING_A057"]
    assert typed_keys("á") == []  # não-ASCII imprimível: ignorado


# --------------------------------------------------------------------------- #
# Motor de passos                                                               #
# --------------------------------------------------------------------------- #


def test_passo_ok_clique_derivado_do_screen_text():
    bridge = FakeBridge([("title/Default", "menu\nCreate new world\n"),
                         ("new_region/Basic", "criado")])
    driver = make_driver(bridge, [step("s1", 'click "Create new world"', "focus:new_region")])
    screens, actions = driver.run_step(driver.steps[0])
    assert actions == ["click@(8, 1) 'Create new world'"]
    assert bridge.actions == [("click", 8, 1)]
    assert len(screens) == 2


def test_falha_final_aborta_com_relatorio_pt_apos_5_tentativas():
    bridge = FakeBridge([("title/Default", "X sempre aqui")])
    driver = make_driver(bridge, [step("s1", 'click "X"', "focus:alvo")])
    with pytest.raises(StepAbort) as exc:
        driver.run_step(driver.steps[0])
    report = exc.value.pt_report()
    assert "RELATORIO DE ABORTO" in report
    assert "Passo: s1" in report
    assert "Tentativas: 5/5" in report
    assert "ABORTADO: fluxo de criacao nao concluido." in report
    assert len([c for c in bridge.calls if c[0] == "click"]) == 5  # exatamente 5
    assert exc.value.to_dict()["attempts"] == 5


def test_retry_depois_de_falha_temporaria_nao_aborta():
    # tentativa 1: sem o label (falha); tentativa 2: label + expect satisfeito
    bridge = FakeBridge([("t", "sem label"),
                         ("t", "ainda nada"),
                         ("t", "Go!"),
                         ("dungeonmode", "em jogo")])
    driver = make_driver(bridge, [step("s1", 'click "Go!"', "focus:dungeonmode")])
    screens, actions = driver.run_step(driver.steps[0])
    assert len(actions) == 2 and "NOT FOUND" in actions[0] and "NOT FOUND" not in actions[1]
    assert bridge.actions == [("click", 1, 0)]  # só uma ação real (label "Go!")


def test_poll_espera_pelo_expect_sem_agir():
    bridge = FakeBridge([("export_region", "Saving world"),
                         ("export_region", "Offloading units"),
                         ("choose_game_type", "Select a game type")])
    driver = make_driver(bridge, [step("poll1", "poll", "focus:choose_game_type", wait=0)])
    screens, actions = driver.run_step(driver.steps[0])
    assert actions == ["poll"] and bridge.actions == []
    assert len(screens) == 1


def test_poll_timeout_aborta_com_relatorio():
    bridge = FakeBridge([("export_region", "Saving world forever")])
    driver = make_driver(bridge, [step("poll1", "poll", "focus:choose_game_type", wait=0)])
    with pytest.raises(StepAbort) as exc:
        driver.run_step(driver.steps[0])
    assert "Passo: poll1" in exc.value.pt_report()


def test_cancelamento_para_de_enviar_teclas():
    cancelled = {"value": False}
    bridge = FakeBridge([("t", "Go!"), ("dungeonmode", "ok"),
                         ("t2", "Next"), ("dungeonmode2", "ok")])

    def on_action(_kind):
        cancelled["value"] = True  # o utilizador cancelou durante o 1º passo

    bridge.on_action = on_action
    steps = [step("s1", 'click "Go!"', "focus:dungeonmode"),
             step("s2", 'click "Next"', "focus:dungeonmode2")]
    driver = make_driver(bridge, steps)
    with pytest.raises(StepCancelled):
        driver.run_flow(steps, cancel=lambda: cancelled["value"])
    assert [c for c in bridge.calls if c[0] in ("click", "keys")] == [("click", 1, 0)]
    # só a 1ª ação saiu — o passo 2 nunca chegou a enviar teclas


def test_passo_soft_falha_sem_abortar_o_fluxo():
    # soft: 5 tentativas falham (10 leituras de ecrã) e o fluxo segue para o passo duro
    bridge = FakeBridge([("t", "sem label")] * 10 + [("t", "Go!"), ("dungeonmode", "ok")])
    soft = step("soft1", 'click "Inexistente"', "focus:nunca", soft=True)
    hard = step("s2", 'click "Go!"', "focus:dungeonmode")
    driver = make_driver(bridge, [soft, hard])
    result = driver.run_flow([soft, hard])
    assert result["ok"] is True
    assert [c for c in bridge.calls if c[0] == "click"] == [("click", 1, 0)]


def test_eventos_do_motor_tem_step_e_ecra():
    events = []
    bridge = FakeBridge([("t", "Go!"), ("dungeonmode", "ok")])
    driver = make_driver(bridge, [step("s1", 'click "Go!"', "focus:dungeonmode")])
    driver.run_flow(driver.steps, on_event=events.append)
    kinds = [e["event"] for e in events]
    assert "step_start" in kinds and "action" in kinds and "step_ok" in kinds
    screens = [e for e in events if e["event"] == "screen"]
    assert screens and screens[0]["screen_text"] == "Go!"


def test_type_envia_teclas_string_a():
    bridge = FakeBridge([("t", "campo"), ("t", "campo")])
    driver = make_driver(bridge, [step("s1", "type Ab", "")])
    _screens, actions = driver.run_step(driver.steps[0])
    assert bridge.actions == [("keys", ["STRING_A065", "STRING_A098"])]
    assert actions == ["type 2 keys"]


# --------------------------------------------------------------------------- #
# Construção dos fluxos (parâmetros da API)                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def real_steps():
    return load_steps()


def test_build_character_steps_guiam_os_labels(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    steps = driver.build_character_steps(
        name="Urist", race="dwarf", origin="Morningflashes", randomize=True
    )
    by_name = {s["name"]: (s["keys"][0] if s.get("keys") else "") for s in steps}
    assert by_name["select_human"] == 'click "Select dwarf"'
    assert by_name["select_origin"] == 'click "Select Morningflashes"'
    assert by_name["character_name_type"] == "type Urist"
    assert "randomize_looks" in by_name and "randomize_personality" in by_name
    # os passos soft são marcados como guessed e nunca abortam
    assert all(s["soft"] for s in steps if s["name"].startswith(("character_", "randomize_")))


def test_build_character_steps_defaults_aceitam_tudo(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    steps = driver.build_character_steps()
    by_name = {s["name"]: (s["keys"][0] if s.get("keys") else "") for s in steps}
    assert by_name["select_human"] == 'click "Select human"'
    assert by_name["select_origin"] == "click_re ^Select [A-Z]"
    assert not any(s["name"].startswith(("character_", "randomize_")) for s in steps)
    # o fluxo provado: 13 passos, do tipo de jogo ao in-game
    assert [s["name"] for s in steps][0] == "choose_adventurer"
    assert [s["name"] for s in steps][-1] == "tutorial_pages"


def test_build_world_steps_quick_e_nome(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    quick = driver.build_world_steps(world_name="Planetas", params="quick")
    by_name = {s["name"]: (s["keys"][0] if s.get("keys") else "") for s in quick}
    assert by_name["quick_world_params"].startswith("click_re ")
    assert by_name["world_name_type"] == "type Planetas"
    names = [s["name"] for s in quick]
    assert names.index("world_name_type") < names.index("create_world_defaults")

    default = driver.build_world_steps(params="default")
    assert not any(s["name"] in ("quick_world_params", "world_name_type") for s in default)
    assert [s["name"] for s in default][0] == "title_to_create_world"
    assert [s["name"] for s in default][-1] == "poll_export"


def test_create_world_params_invalido_levanta():
    driver = make_driver(FakeBridge(), load_steps())
    with pytest.raises(ValueError):
        driver.create_world(params="turbo")


# --------------------------------------------------------------------------- #
# Fluxos completos com o steps.json provado (bridge falso por fila)             #
# --------------------------------------------------------------------------- #


def satisfying_screen(s):
    """Ecrã que satisfaz o ``expect`` do passo (necessário e suficiente)."""
    expect = (s.get("expect") or [""])[0]
    if expect.startswith("focus:"):
        needle = expect[len("focus:"):].split(" (")[0].strip()
        return (needle, "tela")
    if expect.startswith("screen:"):
        needle = expect[len("screen:"):].split(" (")[0].strip()
        return ("focus/x", f"linha\n{needle}\n")
    return ("focus/x", str(expect))


def pre_screen(s):
    """Ecrã com o label que o passo vai clicar (coordenadas derivadas dele)."""
    directive = (s.get("keys") or [""])[0]
    if directive.startswith("click "):
        label = directive[len("click "):].strip().strip('"')
        return ("pre/focus", f"menu\n{label}\n")
    if directive.startswith("click_re "):
        return ("pre/focus", "menu\nSelect Omon Wur\n")
    return ("pre/focus", "menu")


def screen_queue(steps):
    queue = []
    for s in steps:
        if ((s.get("keys") or [""])[0]).startswith("poll"):
            queue.append(satisfying_screen(s))
        else:
            queue.append(pre_screen(s))
            queue.append(satisfying_screen(s))
    return queue


def test_create_world_fluxo_provado_ponta_a_ponta(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    steps = driver.build_world_steps(params="default")
    bridge = FakeBridge(screen_queue(steps))
    driver = AdventureDriver(bridge, steps=real_steps, sleep=lambda _s: None, poll_timeout=2)
    result = driver.create_world(params="default")
    assert result["ok"] is True
    # 4 cliques (Create new world → Okay → Create world → Play now); os polls não clicam
    assert len([c for c in bridge.calls if c[0] == "click"]) == 4
    assert len([s for s in steps if (s.get("keys") or [""])[0].startswith("poll")]) == 2


def test_create_character_fluxo_provado_ponta_a_ponta(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    steps = driver.build_character_steps()
    bridge = FakeBridge(screen_queue(steps))
    driver = AdventureDriver(bridge, steps=real_steps, sleep=lambda _s: None, poll_timeout=2)
    result = driver.create_character()
    assert result["ok"] is True
    # um clique por passo de clique (Adventurer → … → Go! → tutorial cosmético)
    click_steps = [s for s in steps if (s.get("keys") or [""])[0].startswith(("click", "click_re"))]
    assert len([c for c in bridge.calls if c[0] == "click"]) == len(click_steps)
    assert bridge.calls[-1][0] == "screen"  # terminou em verificação, não em clique


def test_create_character_com_params_usa_os_labels_dos_params(real_steps):
    driver = AdventureDriver(FakeBridge(), steps=real_steps)
    steps = driver.build_character_steps(race="dwarf", origin="Morningflashes")
    click_steps = [s for s in steps if (s.get("keys") or [""])[0].startswith(("click", "click_re"))]
    bridge = FakeBridge(screen_queue(steps))
    driver = AdventureDriver(bridge, steps=real_steps, sleep=lambda _s: None, poll_timeout=2)
    result = driver.create_character(race="dwarf", origin="Morningflashes")
    assert result["ok"] is True
    # um clique por passo de clique: os ecrãs pré-ação traziam os labels dos parâmetros
    assert len([c for c in bridge.calls if c[0] == "click"]) == len(click_steps)


# --------------------------------------------------------------------------- #
# Saves + estado atual                                                          #
# --------------------------------------------------------------------------- #


def test_list_adventures_pasta_de_saves_falsa(tmp_path):
    save = tmp_path / "data" / "save"
    (save / "region1").mkdir(parents=True)
    (save / "region1" / "world.dat").write_text("x", encoding="utf-8")
    (save / "region2-00202-01-01").mkdir(parents=True)
    (save / "region2-00202-01-01" / "world.sav").write_text("x", encoding="utf-8")
    (save / "notes.txt").write_text("ignorado", encoding="utf-8")

    os.utime(save / "region1" / "world.dat", (1_000_000, 1_000_000))
    os.utime(save / "region2-00202-01-01" / "world.sav", (2_000_000, 2_000_000))

    driver = AdventureDriver(FakeBridge(save_root=save), steps=load_steps())
    adventures = driver.list_adventures()
    assert [a["id"] for a in adventures] == ["region2-00202-01-01", "region1"]  # mtime desc
    by_id = {a["id"]: a for a in adventures}
    assert by_id["region1"]["kind"] == "world"
    assert by_id["region2-00202-01-01"]["kind"] == "active"
    assert by_id["region2-00202-01-01"]["name"] == "region2"  # sufixo de cópia sazonal removido
    assert by_id["region1"]["saved_at"].startswith("1970-01-12T")
    assert by_id["region1"]["folder"].endswith("region1")
    assert by_id["region1"]["characters"] is None  # GUESS: não parseável offline


def test_list_adventures_sem_pasta_devolve_vazio(tmp_path):
    driver = AdventureDriver(FakeBridge(save_root=tmp_path / "nao_existe"), steps=load_steps())
    assert driver.list_adventures() == []


def test_current_state_com_aventureiro():
    snapshot = {
        "adventurer": {"name": "Urist McTest", "health": {"blood": 4000}},
        "metrics": {"location": {"world": "The World of Tests"}},
        "gamelog_events": [],
    }
    driver = AdventureDriver(FakeBridge(snapshot=snapshot), steps=load_steps())
    assert driver.current_state() == {
        "adventure_id": None,
        "adventurer_name": "Urist McTest",
        "world_name": "The World of Tests",
        "alive": True,
    }


def test_current_state_sem_aventureiro_ou_sem_jogo():
    driver = AdventureDriver(FakeBridge(snapshot={}), steps=load_steps())
    assert driver.current_state() is None
    driver = AdventureDriver(FakeBridge(snapshot=RuntimeError("jogo em baixo")), steps=load_steps())
    assert driver.current_state() is None


def test_current_state_alive_false_com_morte_no_gamelog():
    snapshot = {
        "adventurer": {"name": "Urist", "health": {"blood": 1}},
        "metrics": {"location": {"world": "W"}},
        "gamelog_events": [{"category": "death", "text": "Urist has been struck down."}],
    }
    driver = AdventureDriver(FakeBridge(snapshot=snapshot), steps=load_steps())
    assert driver.current_state()["alive"] is False


def test_current_state_acede_ao_adventure_id_da_carga():
    snapshot = {
        "adventurer": {"name": "Urist", "health": {"blood": 1}},
        "metrics": {"location": {"world": "W"}},
        "gamelog_events": [],
    }
    driver = AdventureDriver(FakeBridge(snapshot=snapshot), steps=load_steps())
    assert driver.current_state(adventure_id="region1")["adventure_id"] == "region1"


# --------------------------------------------------------------------------- #
# load_adventure                                                                #
# --------------------------------------------------------------------------- #


def test_load_save_ativo_usa_load_save_do_dfhack(tmp_path):
    save = tmp_path / "save"
    (save / "region1").mkdir(parents=True)
    (save / "region1" / "world.sav").write_text("x", encoding="utf-8")
    bridge = FakeBridge([("dungeonmode/Default", "em jogo")], save_root=save)
    driver = AdventureDriver(bridge, steps=load_steps(), sleep=lambda _s: None, poll_timeout=2)
    result = driver.load_adventure("region1")
    assert result["via"] == "load-save"
    assert ("command", ["run", "load-save", "region1"]) in bridge.calls


def test_load_save_inativo_vai_para_o_menu(tmp_path):
    save = tmp_path / "save"
    (save / "region1").mkdir(parents=True)
    (save / "region1" / "world.dat").write_text("x", encoding="utf-8")
    driver = AdventureDriver(FakeBridge(save_root=save), steps=load_steps())
    steps = driver._load_menu_steps({"id": "region1", "name": "region1"})
    bridge = FakeBridge(screen_queue(steps), save_root=save)
    driver = AdventureDriver(bridge, steps=load_steps(), sleep=lambda _s: None, poll_timeout=2)
    result = driver.load_adventure("region1")
    assert result["via"] == "menu"
    assert not any(c[0] == "command" for c in bridge.calls)  # sem load-save


def test_load_aventura_desconhecida_levanta():
    driver = AdventureDriver(FakeBridge(save_root=Path("/inexistente")), steps=load_steps())
    with pytest.raises(UnknownAdventure):
        driver.load_adventure("region99")


# --------------------------------------------------------------------------- #
# SubprocessBridge (runner injetado)                                            #
# --------------------------------------------------------------------------- #


def test_subprocess_bridge_screen_click_keys_command():
    seen = []

    def runner(cmd):
        seen.append(list(cmd))
        if "screen" in cmd:
            return SimpleNamespace(returncode=0, stderr="",
                                   stdout=json.dumps({"data": {"focus": ["a", "b"],
                                                               "lines": ["l1", "l2"]}}))
        return SimpleNamespace(returncode=0, stderr="", stdout="ok")

    bridge = SubprocessBridge(df_path="/fake/df", port=1234, runner=runner)
    assert bridge.screen() == ("a/b", "l1\nl2")
    bridge.click(3, 4)
    bridge.keys(["SELECT"])
    assert "click 3,4" in seen[-2][-1]
    assert "key SELECT" in seen[-1][-1]
    assert seen[0][:2] == ["python3", str(bridge.bridge_path)]
    assert "--df-path" in seen[0] and "/fake/df" in seen[0]
    assert bridge.save_root() == Path("/fake/df/data/save")


def test_subprocess_bridge_command_falha_levanta():
    def runner(_cmd):
        return SimpleNamespace(returncode=13, stderr="sem aventureiro", stdout="")

    bridge = SubprocessBridge(df_path="/fake/df", runner=runner)
    with pytest.raises(BridgeCommandError):
        bridge.command(["snapshot", "--json"])


def test_subprocess_bridge_snapshot_parseia_o_json():
    def runner(_cmd):
        return SimpleNamespace(returncode=0, stderr="",
                               stdout=json.dumps({"adventurer": {"name": "U"}}))

    bridge = SubprocessBridge(df_path="/fake/df", runner=runner)
    assert bridge.snapshot() == {"adventurer": {"name": "U"}}


# --------------------------------------------------------------------------- #
# GameProcessManager (operacoes injetadas — nunca lança nem mata processos)     #
# --------------------------------------------------------------------------- #


class FakeOps:
    """Operações de processo falsas: regista tudo, não toca em nenhum processo."""

    def __init__(self, procs=None, die_on_term=True, rpc=False):
        self.procs = list(procs or [])
        self.die_on_term = die_on_term
        self.rpc = rpc
        self.signals = []
        self.spawned = []
        self.slept = []

    def find_game(self, df_path):
        return [dict(p) for p in self.procs]

    def spawn(self, cmd, *, cwd, env, log_path):
        self.spawned.append({"cmd": list(cmd), "cwd": cwd, "env": dict(env),
                             "log_path": str(log_path)})
        return 4242

    def signal_group(self, pgid, sig):
        self.signals.append((pgid, sig))
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and self.die_on_term):
            self.procs = [p for p in self.procs if p["pgid"] != pgid]
        return True

    def group_alive(self, pgid):
        return any(p["pgid"] == pgid for p in self.procs)

    def sleep(self, seconds):
        self.slept.append(seconds)

    def probe_rpc(self, port):
        return self.rpc


def test_game_status_com_jogo_e_sem_jogo(tmp_path):
    ops = FakeOps(procs=[{"pid": 7, "pgid": 7}], rpc=True)
    manager = GameProcessManager(df_path=tmp_path, ops=ops)
    status = manager.status()
    assert status == {"running": True, "pid": 7, "rpc": True, "path": str(tmp_path)}

    manager = GameProcessManager(df_path=tmp_path, ops=FakeOps())
    assert manager.status()["running"] is False


def test_game_start_guarda_e_lancamento(tmp_path):
    ops = FakeOps()
    manager = GameProcessManager(df_path=tmp_path, ops=ops, runtime_dir=tmp_path / "rt")
    with pytest.raises(GameProcessError):
        manager.start()  # guarda: sem <df>/dfhack não há lançamento

    launcher = tmp_path / "dfhack"
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")
    launcher.chmod(0o755)
    result = manager.start()
    assert result["action"] == "start" and result["pid"] == 4242
    assert result["running"] is True and result["already_running"] is False
    spawn = ops.spawned[0]
    assert spawn["cmd"] == [str(launcher)] and spawn["cwd"] == str(tmp_path)
    assert spawn["env"]["SDL_AUDIODRIVER"] == "dummy"       # regra: sem som
    assert spawn["env"]["SDL_VIDEODRIVER"] == "x11"
    assert spawn["env"]["LIBGL_ALWAYS_SOFTWARE"] == "1"
    assert (tmp_path / "rt" / "game.pid").read_text(encoding="utf-8").strip() == "4242"


def test_game_start_ja_a_correr_nao_lanca_outro(tmp_path):
    launcher = tmp_path / "dfhack"
    launcher.write_text("x", encoding="utf-8")
    launcher.chmod(0o755)
    ops = FakeOps(procs=[{"pid": 7, "pgid": 7}])
    manager = GameProcessManager(df_path=tmp_path, ops=ops)
    result = manager.start()
    assert result["already_running"] is True and ops.spawned == []


def test_game_stop_escalonamento_term_para_kill(tmp_path):
    ops = FakeOps(procs=[{"pid": 7, "pgid": 7}], die_on_term=False)
    manager = GameProcessManager(df_path=tmp_path, ops=ops)
    result = manager.stop(grace=0)  # grace=0: o escalonamento é imediato no teste
    assert ops.signals == [(7, signal.SIGTERM), (7, signal.SIGKILL)]
    assert result["action"] == "stop" and result["forced_groups"] == [7]
    assert result["running"] is False


def test_game_stop_basta_com_term(tmp_path):
    ops = FakeOps(procs=[{"pid": 7, "pgid": 7}], die_on_term=True)
    manager = GameProcessManager(df_path=tmp_path, ops=ops)
    result = manager.stop(grace=5)
    assert ops.signals == [(7, signal.SIGTERM)]  # morreu com TERM: sem KILL
    assert result["stopped"] is True and result["forced_groups"] == []


def test_game_stop_sem_jogo_e_noop(tmp_path):
    ops = FakeOps()
    manager = GameProcessManager(df_path=tmp_path, ops=ops)
    result = manager.stop()
    assert result == {"action": "stop", "stopped": False,
                      "running": False, "pid": None, "rpc": False, "path": str(tmp_path)}
    assert ops.signals == []
