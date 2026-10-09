"""Testes offline da API de aventuras + system control (tudo em fakes).

Provam o contrato de ``docs/api.md`` sem jogo, sem rede e sem processos reais:

* ``GET  /api/campaigns/{id}/adventures`` — lista de saves (pasta falsa, com o
  driver real) + ``current`` do snapshot;
* ``POST .../adventures/{new-world,new-character,load}`` — ``202`` + ciclo de
  vida da tarefa (``202`` → status ``running`` → ``done``/``error`` com
  relatório PT), ``409`` quando ocupada, validação ``422`` do ``load``;
* ``POST .../adventures/cancel`` — cancela (``error: "cancelled"``) e o worker
  nunca passa a ``done``;
* eventos ``adventure_task`` no bus (WS) com a forma do contrato;
* ``GET /api/system/status`` e ``POST /api/system/game`` — com um gestor de
  processo **injetado** (o jogo real nunca é lançado nem morto).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[1] / "src"
for _path in (str(SRC), str(HERE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from fastapi.testclient import TestClient  # noqa: E402

from fakes import FakeTranslator  # noqa: E402
from system_one_dwarf.app.adventure_flow import ADVENTURE_TASK_EVENT  # noqa: E402
from system_one_dwarf.app.main import create_app  # noqa: E402
from system_one_dwarf.game.driver import (  # noqa: E402
    AdventureDriver,
    GameProcessError,
    StepAbort,
    load_steps,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                         #
# --------------------------------------------------------------------------- #


class SyncRunner:
    """Runner síncrono: a tarefa corre na própria chamada (determinista)."""

    def __call__(self, target):
        target()


class ManualRunner:
    """Runner diferido: o worker só corre quando ``run_all()`` é chamado."""

    def __init__(self):
        self.pending = []

    def __call__(self, target):
        self.pending.append(target)

    def run_all(self):
        while self.pending:
            self.pending.pop(0)()


class FakeDriver:
    """Driver de aventura falso: regista chamadas e emite progresso."""

    def __init__(self, adventures=None, current=None):
        self.adventures = list(adventures or [])
        self.current = current
        self.calls = []
        self.raise_on = {}

    # ---- leituras
    def list_adventures(self):
        self.calls.append(("list_adventures", {}))
        return [dict(a) for a in self.adventures]

    def current_state(self, *, adventure_id=None):
        self.calls.append(("current_state", {"adventure_id": adventure_id}))
        if self.current is None:
            return None
        current = dict(self.current)
        if adventure_id:
            current["adventure_id"] = adventure_id
        return current

    # ---- fluxos
    def _run(self, kind, kw):
        self.calls.append((kind, dict(kw)))
        exc = self.raise_on.get(kind)
        if exc is not None:
            raise exc
        on_event = kw.get("on_event")
        if on_event is not None:
            on_event({"event": "step_start", "index": 1, "total": 2,
                      "name": "passo1", "directive": 'click "X"'})
            on_event({"event": "screen", "index": 1, "total": 2, "name": "passo1",
                      "screen_text": "First screen"})
            on_event({"event": "step_start", "index": 2, "total": 2,
                      "name": "passo2", "directive": 'click "Y"'})
            on_event({"event": "screen", "index": 2, "total": 2, "name": "passo2",
                      "screen_text": "Hello screen"})
        return {"ok": True, "adventure": {"id": "region1"}}

    def create_world(self, **kw):
        return self._run("create_world", kw)

    def create_character(self, **kw):
        return self._run("create_character", kw)

    def load_adventure(self, adventure_id, **kw):
        kw = dict(kw)
        kw["adventure_id"] = adventure_id
        return self._run("load_adventure", kw)


class FakeGameManager:
    """Gestor do processo do jogo falso (nunca lança nem mata processos)."""

    def __init__(self, running=False, pid=None, rpc=False, fail=False):
        self.running = running
        self.pid = pid
        self.rpc = rpc
        self.fail = fail
        self.actions = []

    def status(self):
        return {"running": self.running, "pid": self.pid, "rpc": self.rpc,
                "path": "/fake/df"}

    def start(self):
        self.actions.append("start")
        if self.fail:
            raise GameProcessError("lancador nao encontrado: /fake/df/dfhack")
        self.running, self.pid = True, 99
        return {"action": "start", "already_running": False, "running": True,
                "pid": 99, "rpc": False, "path": "/fake/df"}

    def stop(self):
        self.actions.append("stop")
        if self.fail:
            raise GameProcessError("falha simulada")
        self.running, self.pid = False, None
        return {"action": "stop", "stopped": True, "running": False,
                "pid": None, "rpc": False, "path": "/fake/df"}


class StubBridge:
    """Superfície mínima do driver real (saves numa pasta falsa + snapshot fixo)."""

    def __init__(self, save_root, snapshot=None):
        self._save_root = Path(save_root)
        self._snapshot = snapshot

    def save_root(self):
        return self._save_root

    def snapshot(self):
        return self._snapshot or {}


# --------------------------------------------------------------------------- #
# Fixtures                                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_api(campaigns_root, fake_cli, fake_installer):
    """Factory: app com todos os seams injetados + TestClient com lifespan."""

    def _make(*, driver=None, runner=None, game=None, translator=None,
              driver_factory=None, frontend_probe=None):
        driver = driver if driver is not None else FakeDriver()
        game = game if game is not None else FakeGameManager()
        app = create_app(
            campaigns_root=campaigns_root,
            installer=fake_installer,
            cli_factory=lambda _root, _slug: fake_cli,
            translator=translator,
            adventure_driver_factory=driver_factory or (lambda _campaign: driver),
            adventure_task_runner=runner or SyncRunner(),
            game_manager=game,
            frontend_probe=frontend_probe or (lambda _port: False),
        )
        client = TestClient(app)
        client.__enter__()
        return SimpleNamespace(client=client, driver=driver, game=game, app=app)

    return _make


@pytest.fixture
def api(make_api):
    return make_api()


@pytest.fixture
def campaign_id(api) -> str:
    response = api.client.post(
        "/api/campaigns", json={"name": "Urist McAdventurer", "is_new_game": True}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def step_abort() -> StepAbort:
    return StepAbort(
        {"name": "select_human"},
        [("title", "Select human ausente")],
        ["click 'Select human' -> NOT FOUND"],
    )


# --------------------------------------------------------------------------- #
# GET /adventures                                                               #
# --------------------------------------------------------------------------- #


def test_get_adventures_lista_e_current(api, campaign_id):
    api.driver.adventures = [
        {"id": "region1", "name": "region1", "kind": "world",
         "folder": "/fake/df/data/save/region1", "saved_at": "2026-10-09T00:00:00.000Z",
         "characters": None},
        {"id": "region2", "name": "region2", "kind": "active",
         "folder": "/fake/df/data/save/region2", "saved_at": "2026-10-10T00:00:00.000Z",
         "characters": ["Omba"]},
    ]
    api.driver.current = {"adventurer_name": "Omba", "world_name": "Planetas",
                          "alive": True}

    response = api.client.get(f"/api/campaigns/{campaign_id}/adventures")
    assert response.status_code == 200
    body = response.json()
    assert [a["id"] for a in body["adventures"]] == ["region1", "region2"]
    first = body["adventures"][0]
    assert set(first) == {"id", "name", "kind", "folder", "saved_at"}  # characters omitido
    assert body["adventures"][1]["characters"] == ["Omba"]


def test_get_adventures_current_shape(api, campaign_id):
    api.driver.current = {"adventurer_name": "Omba", "world_name": "Planetas",
                          "alive": True}
    body = api.client.get(f"/api/campaigns/{campaign_id}/adventures").json()
    # `adventure_id?` é opcional no contrato: sem id conhecido, falta (não é null)
    assert body["current"] == {"adventurer_name": "Omba", "world_name": "Planetas",
                               "alive": True}


def test_get_adventures_sem_jogo_devolve_listas_vazias(api, campaign_id):
    response = api.client.get(f"/api/campaigns/{campaign_id}/adventures")
    assert response.status_code == 200
    assert response.json() == {"adventures": [], "current": None}


def test_get_adventures_com_driver_real_e_pasta_de_saves_falsa(make_api, campaign_id, tmp_path):
    save = tmp_path / "data" / "save"
    (save / "region1").mkdir(parents=True)
    (save / "region1" / "world.sav").write_text("x", encoding="utf-8")
    (save / "region2").mkdir(parents=True)
    (save / "region2" / "world.dat").write_text("x", encoding="utf-8")

    bridge = StubBridge(save, snapshot={
        "adventurer": {"name": "Omba", "health": {"blood": 100}},
        "metrics": {"location": {"world": "region1"}},
        "gamelog_events": [],
    })
    driver = AdventureDriver(bridge, steps=load_steps())
    api = make_api(driver_factory=lambda _campaign: driver)

    body = api.client.get(f"/api/campaigns/{campaign_id}/adventures").json()
    by_id = {a["id"]: a for a in body["adventures"]}
    assert by_id["region1"]["kind"] == "active"
    assert by_id["region2"]["kind"] == "world"
    # o snapshot diz o mundo "region1" → o id da aventura é imputado pela pasta
    assert body["current"]["adventure_id"] == "region1"
    assert body["current"]["adventurer_name"] == "Omba"


# --------------------------------------------------------------------------- #
# POSTs: ciclo de vida das tarefas                                              #
# --------------------------------------------------------------------------- #


def test_post_new_world_202_e_status_done(make_api, campaign_id):
    translator = FakeTranslator(en_to_pt={"Hello screen": "Olá ecrã"})
    api = make_api(runner=SyncRunner(), translator=translator)

    response = api.client.post(
        f"/api/campaigns/{campaign_id}/adventures/new-world",
        json={"world_name": "Planetas", "params": "quick"},
    )
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["kind"] == "new_world" and accepted["task_id"].startswith("t-")

    status = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()
    task = status["task"]
    assert task["id"] == accepted["task_id"]
    assert task["state"] == "done"
    assert task["step"] == 2 and task["steps_total"] == 2
    assert task["last_screen_pt"] == "Olá ecrã"   # seam display_pt (tradutor)
    assert "error" not in task

    # os parâmetros chegaram ao driver
    kind, kw = api.driver.calls[0]
    assert kind == "create_world"
    assert kw["world_name"] == "Planetas" and kw["params"] == "quick"
    assert callable(kw["on_event"]) and callable(kw["cancel"])


def test_post_new_character_defaults(api, campaign_id):
    response = api.client.post(
        f"/api/campaigns/{campaign_id}/adventures/new-character",
        json={"randomize": False},
    )
    assert response.status_code == 202
    assert response.json()["kind"] == "new_character"
    kind, kw = api.driver.calls[0]
    assert kind == "create_character"
    assert kw["name"] is None and kw["race"] is None and kw["origin"] is None
    assert kw["randomize"] is False
    assert api.client.get(
        f"/api/campaigns/{campaign_id}/adventures/status"
    ).json()["task"]["state"] == "done"


def test_post_new_character_com_params(api, campaign_id):
    api.client.post(
        f"/api/campaigns/{campaign_id}/adventures/new-character",
        json={"name": "Urist", "race": "dwarf", "origin": "Morningflashes",
              "randomize": True},
    )
    kind, kw = api.driver.calls[0]
    assert kind == "create_character"
    assert kw["name"] == "Urist" and kw["race"] == "dwarf"
    assert kw["origin"] == "Morningflashes" and kw["randomize"] is True


def test_task_error_traz_relatorio_pt(make_api, campaign_id):
    api = make_api(runner=SyncRunner())
    api.driver.raise_on["create_character"] = step_abort()

    api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-character",
                    json={"randomize": False})
    task = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()["task"]
    assert task["state"] == "error"
    assert "RELATORIO DE ABORTO" in task["error"]
    assert "Passo: select_human" in task["error"]


def test_task_running_visivel_com_runner_diferido(make_api, campaign_id):
    runner = ManualRunner()
    api = make_api(runner=runner)

    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-world",
                               json={"params": "default"})
    task_id = response.json()["task_id"]
    task = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()["task"]
    assert task["id"] == task_id and task["state"] == "running"
    assert task["step"] == 0 and task["steps_total"] == 0

    runner.run_all()
    task = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()["task"]
    assert task["state"] == "done" and task["step"] == 2


def test_busy_409_quando_ha_tarefa_em_curso(make_api, campaign_id):
    runner = ManualRunner()
    api = make_api(runner=runner)

    api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-world",
                    json={"params": "quick"})
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-character",
                               json={"randomize": False})
    assert response.status_code == 409
    assert "detail" in response.json()

    runner.run_all()  # a tarefa termina → a campanha volta a ficar livre
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-character",
                               json={"randomize": False})
    assert response.status_code == 202


def test_cancel_para_a_tarefa_e_nunca_fica_done(make_api, campaign_id):
    runner = ManualRunner()
    api = make_api(runner=runner)

    api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-character",
                    json={"randomize": False})
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/cancel")
    assert response.status_code == 200
    body = response.json()
    assert body["cancelled"] is True
    assert body["task"]["state"] == "error" and body["task"]["error"] == "cancelled"

    runner.run_all()  # o worker atrasado não pode passar a "done"
    task = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()["task"]
    assert task["state"] == "error" and task["error"] == "cancelled"
    # o cancelamento foi comunicado ao driver (para de enviar teclas)
    _kind, kw = api.driver.calls[0]
    assert kw["cancel"]() is True


def test_cancel_sem_tarefa(api, campaign_id):
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/cancel")
    assert response.status_code == 200
    assert response.json() == {"cancelled": False, "task": None}


def test_load_valida_o_adventure_id(make_api, campaign_id):
    api = make_api(runner=SyncRunner())
    api.driver.adventures = [
        {"id": "region1", "name": "region1", "kind": "active",
         "folder": "/fake/df/data/save/region1", "saved_at": None, "characters": None},
    ]
    api.driver.current = {"adventurer_name": "Omba", "world_name": "region1",
                          "alive": True}
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/load",
                               json={"adventure_id": "nao_existe"})
    assert response.status_code == 422
    assert "nao_existe" in response.json()["detail"]

    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/load",
                               json={"adventure_id": "region1"})
    assert response.status_code == 202
    assert response.json()["kind"] == "load"

    # a carga bem-sucedida fica no `current` (adventure_id memorizado)
    status = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()
    assert status["current"]["adventure_id"] == "region1"
    assert api.driver.calls[-1][0] == "current_state"
    assert api.driver.calls[-1][1]["adventure_id"] == "region1"


def test_load_sem_pasta_de_saves_422(api, campaign_id):
    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/load",
                               json={"adventure_id": "region1"})
    assert response.status_code == 422


def test_status_current_null_sem_aventureiro(api, campaign_id):
    api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-world",
                    json={"params": "quick"})
    status = api.client.get(f"/api/campaigns/{campaign_id}/adventures/status").json()
    assert status["current"] is None
    assert status["task"]["state"] == "done"


# --------------------------------------------------------------------------- #
# Eventos WS (adventure_task)                                                   #
# --------------------------------------------------------------------------- #


def test_adventure_task_events_no_bus(make_api, campaign_id, monkeypatch):
    api = make_api(runner=SyncRunner())
    seen = []
    bus = api.app.state.services.bus
    monkeypatch.setattr(bus, "publish_threadsafe",
                        lambda cid, etype, payload: seen.append((cid, etype, payload)))

    response = api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-world",
                               json={"params": "quick"})
    task_id = response.json()["task_id"]

    events = [payload for _cid, etype, payload in seen if etype == ADVENTURE_TASK_EVENT]
    assert events, "o fluxo tem de publicar adventure_task no bus"
    assert events[0]["task_id"] == task_id
    assert events[0]["kind"] == "new_world"
    assert events[0]["state"] == "running"
    assert events[-1]["state"] == "done"
    # a forma do contrato: {task_id, kind, state, step, steps_total, ...}
    assert {"task_id", "kind", "state", "step", "steps_total"} <= set(events[-1])
    assert events[-1]["step"] == 2 and events[-1]["steps_total"] == 2


def test_adventure_task_event_de_cancelamento(make_api, campaign_id, monkeypatch):
    runner = ManualRunner()
    api = make_api(runner=runner)
    seen = []
    bus = api.app.state.services.bus
    monkeypatch.setattr(bus, "publish_threadsafe",
                        lambda cid, etype, payload: seen.append((cid, etype, payload)))

    api.client.post(f"/api/campaigns/{campaign_id}/adventures/new-character",
                    json={"randomize": False})
    api.client.post(f"/api/campaigns/{campaign_id}/adventures/cancel")
    events = [p for _c, t, p in seen if t == ADVENTURE_TASK_EVENT]
    assert events[-1]["state"] == "error" and events[-1]["error"] == "cancelled"


# --------------------------------------------------------------------------- #
# /api/system                                                                   #
# --------------------------------------------------------------------------- #


def test_system_status_forma_do_contrato(make_api, campaign_id):
    game = FakeGameManager(running=True, pid=7, rpc=True)
    api = make_api(game=game, frontend_probe=lambda port: port == 5173)

    response = api.client.get("/api/system/status")
    assert response.status_code == 200
    body = response.json()
    assert body["game"] == {"running": True, "pid": 7, "rpc": True, "path": "/fake/df"}
    assert body["backend"]["version"]
    assert body["frontend"] == {"running": True, "port": 5173}
    assert body["loop"] == {"campaigns": [{"id": campaign_id, "mode": "autopilot",
                                           "running": False}]}


def test_system_status_sem_jogo_e_sem_frontend(api):
    body = api.client.get("/api/system/status").json()
    assert body["game"]["running"] is False
    assert "pid" not in body["game"]  # pid é opcional no contrato
    assert body["frontend"] == {"running": False, "port": 5173}
    assert body["loop"] == {"campaigns": []}


def test_system_game_start_stop(make_api):
    game = FakeGameManager()
    api = make_api(game=game)

    response = api.client.post("/api/system/game", json={"action": "start"})
    assert response.status_code == 200
    assert response.json() == {"action": "start", "running": True, "pid": 99,
                               "already_running": False}
    assert game.actions == ["start"]

    response = api.client.post("/api/system/game", json={"action": "stop"})
    assert response.status_code == 200
    assert response.json()["action"] == "stop" and response.json()["running"] is False
    assert game.actions == ["start", "stop"]


def test_system_game_acao_invalida_422(api):
    response = api.client.post("/api/system/game", json={"action": "reboot"})
    assert response.status_code == 422


def test_system_game_erro_do_gestor_503(make_api):
    api = make_api(game=FakeGameManager(fail=True))
    response = api.client.post("/api/system/game", json={"action": "start"})
    assert response.status_code == 503
    assert "lancador nao encontrado" in response.json()["detail"]
