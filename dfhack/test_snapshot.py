#!/usr/bin/env python3
"""Testes offline do `df_bridge.py snapshot` e do `dfb-state.lua` (modo snapshot).

Sem o jogo: reaproveita o servidor DFHack FALSO de `test_bridge.py` (handshake +
RunCommand RPC numa porta efêmera) e um stub de ambiente DFHack em Lua (quando há um
interpretador `lua`/`lua5.4`/`luajit` no PATH) para exercitar o script Lua real.

Cobre: forma do JSON do snapshot, resiliência a campos ausentes (nulls, sem crash),
parsing do gamelog, códigos de saída do CLI, journal opcional e as contas do Lua
(peso em kg, ferimentos, ameaças, relógio do jogo).

Rode:  python3 dfhack/test_snapshot.py
"""
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["DF_BRIDGE_SKIP_INSTALL"] = "1"          # nunca instalar scripts nos testes
import df_bridge as B                                # noqa: E402
import test_bridge as TB                             # noqa: E402  (harness do servidor falso)

SRC = os.path.join(os.path.dirname(HERE), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
try:
    from system_one_dwarf.game.readiness import CombatReadiness
except ImportError:                                   # pragma: no cover
    CombatReadiness = None

ISO_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
LUA = shutil.which("lua5.4") or shutil.which("lua") or shutil.which("luajit")

# ---------- fixtures: o que o dfb-state.lua (modo snapshot) devolve ----------
SCREEN = {"focus": ["dungeonmode", "Default"], "lines": ["You are in a forest.", "Conversation"]}

GAMELOG_LINES = [
    "*** Log file started ***",
    "(100) Granite 3, year 100: Urist McTest has been struck down.",
    "It is now Spring.",
    "The goblin attacks the adventurer!",
]

RICH_STATE = {
    "mode": "adventure",
    "adventurer": {
        "name": "Urist McTest", "race": "Dwarf", "profession": "Adventurer",
        "pos": {"x": 10, "y": 20, "z": 30},
        "health": {"blood": 4000, "blood_max": 5000, "wounds": 1, "pain": 12},
        "needs": {"hunger_timer": 80000, "hungry": True, "thirst_timer": 30000,
                  "sleepiness_timer": 60000, "drowsy": True},
        "kills": 7, "stress_category": 5,
    },
    "metrics": {
        # Fidelidade ao Lua: `nil` SOME da tabela, logo estas chaves nem aparecem no
        # JSON do jogo (hp_current/hp_max, capacity_kg, travel_destination, world).
        # O snapshot em Python é que as tem de emitir como null explícito.
        "health_injuries": {
            "blood_current": 4000, "blood_max": 5000,
            "wound_count": 1,
            "wounds": [{"body_part": "right upper arm", "severity": 40, "bleeding": True,
                        "severed": False, "pain": 5}],
            "pain": 12, "status": "bleeding", "status_conditions": ["winded", "bleeding"],
        },
        "needs_state": {"hunger": "hungry", "thirst": "thirsty", "sleep": "drowsy",
                        "hunger_timer": 80000, "thirst_timer": 30000,
                        "sleepiness_timer": 60000, "stress_level": 5},
        "inventory": {
            "items": [{"name": "iron sword", "quantity": 1, "weight_kg": 1.5,
                       "equipped": True, "slot": "Weapon", "material": "iron"}],
            "item_count": 1, "total_weight_kg": 1.5,
            "equipped_weapon": "iron sword", "equipped_armor": [],
        },
        "threats": {"count": 1, "level": "medium",
                    "hostiles": [{"name": "goblin", "distance": 5, "direction": "E",
                                  "hostile": True, "visible": True, "great_danger": False}]},
        "location": {"x": 10, "y": 20, "z": 30,
                     "region": "forest",
                     "site": "Boatmurdered", "site_type": "DarkFortress",
                     "surroundings": "outdoors, forest", "tile": "Grass"},
        "game_clock": {"date_text": "Timber 27, year 100", "year": 100, "month": "Timber",
                       "day": 27, "season": "autumn", "tick": 300000, "hour": 0},
        "units_nearby": {"count": 1,
                         "units": [{"name": "goblin", "distance": 5, "direction": "E",
                                    "hostile": True, "visible": True}]},
        "events": [],
    },
}


def snapshot_responder(state=None, screen=SCREEN, state_code=0, screen_code=0):
    """Responder do servidor falso para as chamadas do `snapshot`."""
    payload = RICH_STATE if state is None else state

    def respond(command, args):
        if command == "dfb-state" and args and args[0] == "snapshot":
            if state_code:
                return "", state_code
            return json.dumps(payload), 0
        if command == "dfb-act" and args and args[0] == "screen":
            if screen_code:
                return "", screen_code
            return json.dumps(screen), 0
        return "ok", 0
    return respond


def run_cli(argv):
    """Roda o main() do df_bridge.py com argv → (código de saída, stdout)."""
    out, old = io.StringIO(), sys.argv
    code = 0
    sys.argv = ["df_bridge.py"] + list(argv)
    try:
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            B.main()
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    finally:
        sys.argv = old
    return code, out.getvalue()


def make_df_dir(gamelog=True, text=None):
    d = tempfile.mkdtemp(prefix="dfsnap-")
    if gamelog:
        with open(os.path.join(d, "gamelog.txt"), "w", encoding="utf-8") as f:
            f.write(("\n".join(GAMELOG_LINES) if text is None else text) + "\n")
    return d


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SnapshotCliTests(unittest.TestCase):
    """CLI `snapshot`: forma do JSON, tela, gamelog, journal e códigos de saída."""

    def setUp(self):
        self.df = make_df_dir()
        self.srv = TB.FakeDFHack(snapshot_responder())
        self.srv.start()
        self.addCleanup(self.srv.stop)
        self.addCleanup(shutil.rmtree, self.df, True)

    def cli(self, *extra, df=None, port=None):
        return run_cli(["snapshot", "--df-path", df or self.df,
                        "--port", str(port or self.srv.port), "--via", "tcp", *extra])

    def test_json_shape(self):
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertEqual(snap["schema_version"], 1)
        self.assertEqual(snap["mode"], "adventure")
        self.assertRegex(snap["ts"], ISO_UTC)
        self.assertEqual(snap["adventurer"]["name"], "Urist McTest")
        self.assertEqual(snap["screen_text"], "You are in a forest.\nConversation")
        self.assertEqual(snap["focus"], "dungeonmode/Default")
        self.assertIsInstance(snap["gamelog_events"], list)
        self.assertEqual(sorted(snap["metrics"]), sorted(B.EMPTY_METRICS))
        self.assertEqual(snap["metrics"]["location"]["x"], 10)
        self.assertEqual(snap["metrics"]["threats"]["level"], "medium")
        self.assertEqual(snap["metrics"]["inventory"]["total_weight_kg"], 1.5)

    def test_plain_output_is_english_summary(self):
        code, out = self.cli()
        self.assertEqual(code, B.EXIT_OK)
        self.assertIn("Game snapshot [adventure]", out)
        self.assertIn("Threats: 1 (medium)", out)
        self.assertIn("You are in a forest.", out)
        with self.assertRaises(ValueError):
            json.loads(out)                     # sem --json não sai JSON

    def test_gamelog_events_parsed_and_mirrored_in_metrics(self):
        code, out = self.cli("--json")
        snap = json.loads(out)
        events = snap["gamelog_events"]
        self.assertEqual(len(events), len(GAMELOG_LINES))
        self.assertEqual(snap["metrics"]["events"], events)
        self.assertEqual(events[1]["category"], "death")
        self.assertEqual(events[1]["game_date"], "Granite 3, year 100")
        self.assertEqual(events[2]["category"], "season")
        self.assertIsNone(events[2]["game_date"])
        self.assertEqual(snap["warnings"], [])

    def test_events_flag_limits_tail(self):
        code, out = self.cli("--json", "--events", "2")
        snap = json.loads(out)
        self.assertEqual([e["text"] for e in snap["gamelog_events"]],
                         GAMELOG_LINES[-2:])

    def test_events_zero_means_no_events(self):
        code, out = self.cli("--json", "--events", "0")
        self.assertEqual(code, B.EXIT_OK)
        self.assertEqual(json.loads(out)["gamelog_events"], [])

    def test_missing_fields_emit_nulls(self):
        self.srv.stop()
        self.srv = TB.FakeDFHack(snapshot_responder(state={"mode": "adventure"}))
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertIsNone(snap["adventurer"])
        for group, shape in B.EMPTY_METRICS.items():
            if group == "events":
                continue
            got = snap["metrics"][group]
            self.assertEqual(sorted(got), sorted(shape), "grupo %s" % group)
            for key, value in shape.items():
                self.assertEqual(got[key], value, "%s.%s" % (group, key))
        self.assertEqual(snap["metrics"]["health_injuries"]["hp_current"], None)
        self.assertEqual(snap["metrics"]["inventory"]["capacity_kg"], None)

    def test_partial_metrics_kept_and_filled(self):
        self.srv.stop()
        partial = {"mode": "adventure",
                   "metrics": {"health_injuries": {"status": "healthy", "wound_count": 0},
                               "location": {"x": 5, "y": 6, "z": 7}}}
        self.srv = TB.FakeDFHack(snapshot_responder(state=partial))
        self.srv.start()
        code, out = self.cli("--json")
        snap = json.loads(out)
        self.assertEqual(snap["metrics"]["health_injuries"]["status"], "healthy")
        self.assertEqual(snap["metrics"]["location"]["x"], 5)
        self.assertEqual(sorted(snap["metrics"]), sorted(B.EMPTY_METRICS))
        self.assertEqual(snap["metrics"]["threats"]["count"], 0)

    def test_lua_absent_fields_come_out_as_explicit_nulls(self):
        """D1: payload realista do Lua (sem as chaves que `nil` faz desaparecer) →
        o snapshot tem de trazer os campos do contrato PRESENTES e a null."""
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        hl, inv = snap["metrics"]["health_injuries"], snap["metrics"]["inventory"]
        loc = snap["metrics"]["location"]
        for group, key in ((hl, "hp_current"), (hl, "hp_max"), (inv, "capacity_kg"),
                           (loc, "travel_destination"), (loc, "world")):
            self.assertIn(key, group, "campo ausente em vez de null: %s" % key)
            self.assertIsNone(group[key])
        for group, shape in B.EMPTY_METRICS.items():
            if group == "events":
                continue
            self.assertEqual(sorted(snap["metrics"][group]), sorted(shape),
                             "conjunto de campos do grupo %s" % group)

    def test_malformed_group_is_warning_and_exit_0(self):
        """D2: grupo que não é objeto → default do contrato + warning, sem traceback."""
        self.srv.stop()
        bad = {"mode": "adventure",
               "metrics": {"inventory": "garbage",
                           "health_injuries": {"status": "healthy", "wounds": "nope"},
                           "threats": {"count": 2, "level": {"a": 1}}}}
        self.srv = TB.FakeDFHack(snapshot_responder(state=bad))
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertEqual(snap["metrics"]["inventory"], B.EMPTY_METRICS["inventory"])
        self.assertEqual(snap["metrics"]["health_injuries"]["wounds"], [])
        self.assertEqual(snap["metrics"]["health_injuries"]["status"], "healthy")
        self.assertIsNone(snap["metrics"]["threats"]["level"])
        self.assertEqual(snap["metrics"]["threats"]["count"], 2)
        self.assertTrue(any("inventory" in w for w in snap["warnings"]), snap["warnings"])
        self.assertTrue(any("health_injuries.wounds" in w for w in snap["warnings"]),
                        snap["warnings"])
        code, out = self.cli()                                # modo texto: sem traceback
        self.assertEqual(code, B.EXIT_OK)
        self.assertIn("Inventory: 0 item(s), weight unknown, weapon: none", out)  # shape nulo
        self.assertIn("warning: metrics.inventory is not a JSON object (string)", out)
        self.assertIn("warning: metrics.health_injuries.wounds is not a JSON array (string)",
                      out)
        self.assertIn("warning: metrics.threats.level is not a scalar (object)", out)
        with self.assertRaises(ValueError):
            json.loads(out)

    def test_malformed_top_level_fields_are_coerced(self):
        self.srv.stop()
        self.srv = TB.FakeDFHack(snapshot_responder(
            state={"mode": 7, "metrics": "garbage", "adventurer": "nope"}))
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertEqual(snap["mode"], "adventure")
        self.assertIsNone(snap["adventurer"])
        self.assertIsInstance(snap["metrics"], dict)
        self.assertEqual(snap["metrics"]["inventory"], B.EMPTY_METRICS["inventory"])
        for frag in ("mode is not a string", "adventurer is not a JSON object",
                     "metrics is not a JSON object"):
            self.assertTrue(any(frag in w for w in snap["warnings"]), (frag, snap["warnings"]))

    def test_unreadable_json_from_game_script_is_exit_13(self):
        """Resposta Lua truncada/sem JSON = erro de ponte (13), nunca traceback nem 0."""
        self.srv.stop()
        self.srv = TB.FakeDFHack(lambda command, argv: ("no json here, sorry", 0))
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_CMD_FAILED)
        self.assertEqual(code, 13)
        body = json.loads(out)
        self.assertFalse(body["ok"])
        self.assertEqual(body["exit_code"], 13)
        self.assertIn("sem JSON", body["error"])

    def test_screen_failure_becomes_warning(self):
        self.srv.stop()
        self.srv = TB.FakeDFHack(snapshot_responder(screen_code=1))   # CR_FAILURE
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertEqual(snap["screen_text"], "")
        self.assertIsNone(snap["focus"])
        self.assertTrue(any("screen" in w for w in snap["warnings"]), snap["warnings"])

    def test_gamelog_missing_becomes_warning(self):
        bare = make_df_dir(gamelog=False)
        self.addCleanup(shutil.rmtree, bare, True)
        code, out = self.cli("--json", df=bare)
        self.assertEqual(code, B.EXIT_OK)
        snap = json.loads(out)
        self.assertEqual(snap["gamelog_events"], [])
        self.assertEqual(snap["metrics"]["events"], [])
        self.assertTrue(any("gamelog" in w for w in snap["warnings"]), snap["warnings"])

    def test_game_script_failure_is_exit_13(self):
        self.srv.stop()
        self.srv = TB.FakeDFHack(snapshot_responder(state_code=3))    # CR_NOT_FOUND
        self.srv.start()
        code, out = self.cli("--json")
        self.assertEqual(code, B.EXIT_CMD_FAILED)
        self.assertEqual(code, 13)
        body = json.loads(out)
        self.assertFalse(body["ok"])
        self.assertEqual(body["exit_code"], B.EXIT_CMD_FAILED)
        self.assertIn("CR_NOT_FOUND", body["error"])

    def test_game_closed_is_exit_12(self):
        code, out = self.cli("--json", port=free_port())              # porta sem ninguém
        self.assertEqual(code, B.EXIT_UNREACHABLE)
        self.assertEqual(code, 12)
        body = json.loads(out)
        self.assertFalse(body["ok"])
        self.assertEqual(body["exit_code"], 12)

    def test_journal_is_opt_in_and_compact(self):
        state_home = tempfile.mkdtemp(prefix="dfstate-")
        self.addCleanup(shutil.rmtree, state_home, True)
        os.environ["XDG_STATE_HOME"] = state_home
        self.addCleanup(os.environ.pop, "XDG_STATE_HOME", None)
        code, _ = self.cli()                                          # sem --journal
        self.assertFalse(os.path.exists(os.path.join(state_home, "df-bridge", "journal",
                                                     "snapshots.jsonl")))
        code, out = self.cli("--journal")
        path = os.path.join(state_home, "df-bridge", "journal", "snapshots.jsonl")
        self.assertEqual(code, B.EXIT_OK)
        self.assertIn("journal: " + path, out)
        with open(path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        self.assertEqual(record["kind"], "snapshot")
        self.assertEqual(record["pos"], {"x": 10, "y": 20, "z": 30})
        self.assertEqual(record["threats"], {"count": 1, "level": "medium"})
        self.assertNotIn("screen_text", record)                       # registo compacto


class GamelogParseTests(unittest.TestCase):
    """Parsing puro do gamelog (sem servidor falso)."""

    def test_date_prefix_variants(self):
        self.assertEqual(B.parse_game_date_prefix("(100) Granite 3, year 100: boom"),
                         "Granite 3, year 100")
        self.assertEqual(B.parse_game_date_prefix("Granite 3"), "Granite 3")
        self.assertEqual(B.parse_game_date_prefix("Obsidian 28, year 250 died"),
                         "Obsidian 28, year 250")
        self.assertIsNone(B.parse_game_date_prefix("It is now Spring."))
        self.assertIsNone(B.parse_game_date_prefix(""))
        self.assertIsNone(B.parse_game_date_prefix(None))

    def test_events_have_text_category_date(self):
        events = B.parse_gamelog_events(["", "  Urist has been struck down.  ", "It is now Spring."])
        self.assertEqual(len(events), 2)                              # linha vazia ignorada
        self.assertEqual(events[0]["text"], "Urist has been struck down.")
        self.assertEqual(events[0]["category"], "death")
        self.assertIsNone(events[0]["game_date"])
        self.assertEqual(events[1]["category"], "season")

    def test_events_flag_zero_is_empty(self):
        self.assertEqual(B.parse_gamelog_events([]), [])


class NormalizeMetricsTests(unittest.TestCase):
    """Contrato "nulls, nunca ausentes" + tolerância a campos malformados (D1)."""

    def test_realistic_lua_groups_gain_contract_fields_as_null(self):
        w = []
        metrics = B.normalize_metrics({"health_injuries": {"status": "healthy", "wound_count": 0},
                                       "inventory": {"items": [], "item_count": 0}},
                                      w)
        self.assertIsNone(metrics["health_injuries"]["hp_current"])
        self.assertIsNone(metrics["health_injuries"]["hp_max"])
        self.assertIsNone(metrics["inventory"]["capacity_kg"])
        self.assertIsNone(metrics["location"]["travel_destination"])
        self.assertEqual(w, [])                              # nada de anómalo aqui
        for group, shape in B.EMPTY_METRICS.items():
            if group != "events":
                self.assertEqual(sorted(metrics[group]), sorted(shape), group)

    def test_unknown_keys_of_the_producer_are_preserved(self):
        metrics = B.normalize_metrics({"inventory": {"item_count": 3, "carried_liters": 7.5}})
        self.assertEqual(metrics["inventory"]["item_count"], 3)
        self.assertEqual(metrics["inventory"]["carried_liters"], 7.5)

    def test_group_that_is_not_an_object_uses_null_shape(self):
        w = []
        metrics = B.normalize_metrics({"inventory": "garbage"}, w)
        self.assertEqual(metrics["inventory"], B.EMPTY_METRICS["inventory"])
        self.assertTrue(any("metrics.inventory" in x and "object" in x for x in w), w)

    def test_scalar_where_list_expected_becomes_empty_list(self):
        w = []
        metrics = B.normalize_metrics({"health_injuries": {"wounds": "nope"}}, w)
        self.assertEqual(metrics["health_injuries"]["wounds"], [])
        self.assertEqual(metrics["health_injuries"]["status_conditions"], [])
        self.assertTrue(any("health_injuries.wounds" in x for x in w), w)

    def test_container_where_scalar_expected_becomes_default(self):
        w = []
        metrics = B.normalize_metrics({"threats": {"count": {"a": 1}, "level": ["high"]},
                                       "health_injuries": {"status": {"x": 1}}}, w)
        self.assertEqual(metrics["threats"]["count"], 0)
        self.assertIsNone(metrics["threats"]["level"])
        self.assertIsNone(metrics["health_injuries"]["status"])
        self.assertEqual(len([x for x in w if "is not a scalar" in x]), 3, w)

    def test_events_group_must_be_an_array(self):
        w = []
        self.assertEqual(B.normalize_metrics({"events": {"text": "x"}}, w)["events"], [])
        self.assertEqual(B.normalize_metrics({"events": [{"text": "x"}]}, [])["events"],
                         [{"text": "x"}])
        self.assertTrue(any("metrics.events" in x for x in w), w)

    def test_metrics_that_is_not_an_object_uses_null_defaults(self):
        w = []
        metrics = B.normalize_metrics("garbage", w)
        self.assertEqual(metrics, B.EMPTY_METRICS)
        self.assertNotIn(metrics["health_injuries"], (None,))     # shape nulo, não None
        self.assertTrue(any("metrics is not a JSON object" in x for x in w), w)
        self.assertEqual(B.normalize_metrics(None), B.EMPTY_METRICS)


class SanitizeSnapshotTests(unittest.TestCase):
    """`sanitize_snapshot`: contrato de topo, espelho do gamelog, nunca levanta."""

    def test_top_level_types_are_coerced_with_warnings(self):
        w = []
        snap = B.sanitize_snapshot({"schema_version": 99, "ts": 5, "mode": 7,
                                    "adventurer": "nope", "metrics": "garbage",
                                    "screen_text": 5, "focus": 9, "gamelog_events": "x"},
                                   w)
        self.assertEqual(snap["schema_version"], 1)
        self.assertRegex(snap["ts"], ISO_UTC)
        self.assertEqual(snap["mode"], "adventure")
        self.assertIsNone(snap["adventurer"])
        self.assertEqual(snap["metrics"], B.EMPTY_METRICS)
        self.assertEqual(snap["screen_text"], "")
        self.assertIsNone(snap["focus"])
        self.assertEqual(snap["gamelog_events"], [])
        self.assertEqual(snap["metrics"]["events"], [])
        for frag in ("ts is not", "mode is not", "adventurer is not", "metrics is not",
                     "screen_text is not", "focus is not", "gamelog_events is not"):
            self.assertTrue(any(frag in x for x in snap["warnings"]), (frag, snap["warnings"]))
        self.assertIs(snap["warnings"], w)                   # a lista do chamador é a usada

    def test_gamelog_events_mirror_empty_metrics_events_group(self):
        events = [{"text": "Urist died.", "category": "death", "game_date": None}]
        snap = B.sanitize_snapshot({"gamelog_events": events, "metrics": {}})
        self.assertEqual(snap["metrics"]["events"], events)

    def test_lua_events_are_kept_when_gamelog_is_empty(self):
        lua_events = [{"text": "from lua", "category": "other", "game_date": None}]
        snap = B.sanitize_snapshot({"gamelog_events": [], "metrics": {"events": lua_events}})
        self.assertEqual(snap["metrics"]["events"], lua_events)

    def test_never_raises_on_garbage(self):
        for bad in (None, 7, "x", [], {"metrics": 7}, {"warnings": object()}):
            snap = B.sanitize_snapshot(bad)
            self.assertIsInstance(snap, dict)
            self.assertEqual(snap["schema_version"], 1)
            self.assertEqual(sorted(snap["metrics"]), sorted(B.EMPTY_METRICS))

    def test_journal_record_never_raises_on_garbage(self):
        rec = B.journal_snapshot_record({"metrics": "garbage", "gamelog_events": "x",
                                         "adventurer": "nope"})
        self.assertEqual(rec["kind"], "snapshot")
        self.assertIsNone(rec["adventurer"])
        self.assertIsNone(rec["pos"])
        self.assertIsNone(rec["health_status"])
        self.assertEqual(rec["event_count"], 0)
        self.assertEqual(rec["threats"], {"count": 0, "level": None})
        self.assertEqual(B.journal_snapshot_record(None)["kind"], "snapshot")


class RenderSnapshotTextTests(unittest.TestCase):
    """D2: `render_snapshot_text` nunca levanta — placeholder + linha `warning:`."""

    def test_malformed_group_is_placeholder_plus_warning(self):
        text = B.render_snapshot_text({"mode": "adventure", "metrics": {"inventory": "garbage"}})
        self.assertIn("Game snapshot [adventure]", text)
        self.assertIn("Inventory: unknown item(s), weight unknown, weapon: none", text)
        self.assertIn("warning: metrics.inventory is not a JSON object (string)", text)

    def test_never_raises_on_garbage_snapshot(self):
        cases = (None, 7, "x", [], {"metrics": "garbage"}, {"metrics": 7},
                 {"adventurer": "nope"}, {"adventurer": {"pos": "here"}},
                 {"adventurer": {"name": 5, "race": [], "profession": {}}},
                 {"gamelog_events": "x"}, {"gamelog_events": [1, "a", None]},
                 {"gamelog_events": [{"category": 7, "game_date": [], "text": {}}]},
                 {"metrics": {"health_injuries": {"status": 7, "status_conditions": 5}}},
                 {"metrics": {"health_injuries": {"wounds": "x", "wound_count": {"a": 1}}}},
                 {"metrics": {"inventory": {"total_weight_kg": {"a": 1}}}},
                 {"metrics": {"location": {"site": 7, "surroundings": []}}},
                 {"metrics": {"game_clock": {"date_text": [], "season": {}}}},
                 {"metrics": {"needs_state": {"hunger": {"a": 1}}}},
                 {"metrics": {"threats": {"count": [], "level": {}}}},
                 {"warnings": "not a list"}, {"warnings": [{"a": 1}], "focus": 9},
                 {"screen_text": 5}, {"metrics": {"events": "x"}})
        for bad in cases:
            text = B.render_snapshot_text(bad)
            self.assertIsInstance(text, str, bad)
            self.assertTrue(text.startswith("Game snapshot ["), (bad, text[:40]))
            self.assertIn("Recent events (", text)

    def test_valid_snapshot_renders_expected_lines(self):
        snap = B.sanitize_snapshot({"mode": "adventure",
                                    "adventurer": {"name": "Urist", "race": "Dwarf",
                                                   "profession": "Adventurer",
                                                   "pos": {"x": 1, "y": 2, "z": 3}},
                                    "metrics": json.loads(json.dumps(RICH_STATE["metrics"])),
                                    "screen_text": "You are in a forest.", "focus": "dmap"})
        text = B.render_snapshot_text(snap)
        self.assertIn("Adventurer: Urist — Dwarf Adventurer", text)
        self.assertIn("Position: x=1 y=2 z=3 — outdoors, forest — site: Boatmurdered", text)
        self.assertIn("Health: bleeding — 1 wound(s), pain 12, conditions: winded, bleeding", text)
        self.assertIn("Inventory: 1 item(s), 1.5 kg, weapon: iron sword", text)
        self.assertIn("Threats: 1 (medium)", text)
        self.assertIn("Focus: dmap", text)

    def test_warnings_of_the_snapshot_are_shown(self):
        text = B.render_snapshot_text({"metrics": {}, "warnings": ["gamelog unavailable: x"]})
        self.assertIn("warning: gamelog unavailable: x", text)


def _lua_harness(scenario, checks, dump=False):
    """Harness Lua: stub mínimo do ambiente DFHack em volta do dfb-state.lua real.

    `dump=True` imprime também `JSON:<payload>` com um encoder fiel ao do DFHack no
    essencial: chave com valor `nil` NÃO existe (em Lua nem sequer é armazenável), e
    tabela vazia sai como `[]`. É esse JSON que o df_bridge.py recebe do jogo.
    """
    return """
local CAPTURED = nil
local UNIT, ACTIVE = nil, {}

df = {
  global = { cur_year = 100, cur_year_tick = 300000,
             world = { units = { active = ACTIVE }, world_data = {} } },
  inv_item_role_type = { [0] = 'NONE', [1] = 'Hauled', [2] = 'Weapon', [3] = 'Worn' },
  biome_type = { [0] = 'FOREST' },
  tiletype = { [0] = 'Grass' },
  site_type = { [0] = 'DarkFortress' },
}

local no = function() return false end
dfhack = {
  df2utf = function(s) return s end,
  units = {
    getReadableName = function(u) return u.desc or 'Urist' end,
    getRaceReadableName = function(u) return 'Dwarf' end,
    getRaceReadableNameById = function(r) return 'Dwarf' end,
    getProfessionName = function(u) return 'Adventurer' end,
    getKillCount = function(u) return 7 end,
    getStressCategory = function(u) return 5 end,
    isAlive = function(u) return true end,
    isVisible = function(u) return true end,
    isDanger = function(u) return u.hostile == true end,
    isGreatDanger = function(u) return u.great_danger == true end,
    isInvader = no, isUndead = no, isAgitated = no,
    isWildlife = no, isTame = no, isHidden = no, isCrazed = no,
  },
  items = { getReadableDescription = function(i) return i.desc end },
  matinfo = { decode = function(i) return { toString = function() return i.material end } end },
  maps = {
    getTileBiomeRgn = function() return 0, 0 end,
    getBiomeType = function() return 0 end,
    getTileType = function() return 0 end,
    getTileFlags = function() return { bits = { outside = true } } end,
  },
  world = { getCurrentSite = function() return { name = {}, type = 0 } end },
  translation = { translateName = function() return 'Boatmurdered' end },
}

qerror = function(msg) error(msg, 0) end
print = function(s) end

reqscript = function(name)
  return {
    utf = dfhack.df2utf,
    json = { encode = function(v) CAPTURED = v; return 'CAPTURED' end },
    version_guard = function() end,
    adventurer = function() return UNIT end,
    safe = function(obj, field, default)
      if obj == nil then return default end
      local ok, v = pcall(function() return obj[field] end)
      if ok and v ~= nil then return v end
      return default
    end,
  }
end

-- ---------- cenário ----------
__SCENARIO__

local chunk = assert(loadfile(arg[1]))
chunk(arg[2] or 'snapshot', '25')
assert(CAPTURED ~= nil, 'o script nao produziu JSON')

-- ---------- verificações ----------
__CHECKS__

-- ---------- dump fiel (opcional) ----------
__DUMP__
io.write('OK\\n')
""".replace("__SCENARIO__", scenario).replace("__CHECKS__", checks).replace("__DUMP__", _LUA_DUMP if dump else "")


# Encoder Lua→JSON do harness (não é o do DFHack; reproduz o que importa aqui: chave
# ausente continua ausente). Só corre quando o teste pede `dump=True`.
_LUA_DUMP = r"""
local function q(s)
  s = s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t')
  return '"' .. s .. '"'
end
local function num(v)
  if v == math.floor(v) and math.abs(v) < 2 ^ 53 then return string.format('%d', v) end
  return string.format('%.6f', v)
end
local enc
enc = function(v)
  local t = type(v)
  if t == 'nil' then return 'null' end
  if t == 'boolean' then return v and 'true' or 'false' end
  if t == 'number' then return num(v) end
  if t == 'string' then return q(v) end
  if t ~= 'table' then return 'null' end
  local n, is_arr = 0, true
  for k in pairs(v) do
    if type(k) ~= 'number' then is_arr = false break end
    n = n + 1
  end
  if is_arr then
    for i = 1, n do if v[i] == nil then is_arr = false break end end
  end
  local parts = {}
  if is_arr then
    for i = 1, n do parts[#parts + 1] = enc(v[i]) end
    return '[' .. table.concat(parts, ',') .. ']'
  end
  for k, val in pairs(v) do
    if type(k) == 'string' then parts[#parts + 1] = q(k) .. ':' .. enc(val) end
  end
  return '{' .. table.concat(parts, ',') .. '}'
end
io.write('JSON:' .. enc(CAPTURED) .. '\n')
"""


EMPTY_SCENARIO = """
UNIT = { pos = { x = 88, y = 72, z = 5 } }        -- sem counters/body/inventory
ACTIVE[1] = UNIT
"""

EMPTY_CHECKS = """
assert(CAPTURED.mode == 'adventure')
assert(CAPTURED.adventurer.pos.x == 88 and CAPTURED.adventurer.pos.z == 5)
assert(CAPTURED.adventurer.health.wounds == 0)
assert(CAPTURED.adventurer.kills == 7)
local m = CAPTURED.metrics
for _, k in ipairs({'health_injuries', 'needs_state', 'inventory', 'threats', 'location',
                    'game_clock', 'units_nearby', 'events'}) do
  assert(m[k] ~= nil, 'grupo ausente: ' .. k)
end
assert(m.health_injuries.hp_current == nil)          -- adventure mode nao tem HP
assert(m.health_injuries.status == 'healthy')
assert(#m.health_injuries.wounds == 0)
assert(m.inventory.item_count == 0 and m.inventory.total_weight_kg == nil)
assert(m.inventory.capacity_kg == nil)
assert(m.threats.count == 0 and m.threats.level == 'none')
assert(m.location.x == 88 and m.location.y == 72)
assert(m.location.travel_destination == nil)
assert(m.units_nearby.count == 0)
assert(#m.events == 0)
"""

RICH_SCENARIO = """
local sword = { desc = 'iron sword', material = 'iron', stack_size = 1,
                flags = { bits = { weight_computed = true } },
                weight = { whole = 1, fraction = 500000 } }
local armor = { desc = 'leather armor', material = 'leather', stack_size = 1,
                flags = { bits = { weight_computed = true } },
                weight = { whole = 3, fraction = 0 } }
UNIT = {
  pos = { x = 10, y = 20, z = 30 },
  desc = 'Urist McTest',
  counters = { pain = 12, stunned = 0, unconscious = 0, winded = 2 },
  counters2 = { hunger_timer = 80000, thirst_timer = 30000, sleepiness_timer = 60000 },
  body = {
    blood_count = 4000, blood_max = 5000,
    body_plan = { body_parts = { [3] = { name_singular = { [0] = 'right upper arm' } } } },
    wounds = { { parts = { { body_part_id = 3, surface_perc = 40, bleeding = 12 } },
                 flags = { bits = {} }, pain = 5 } },
  },
  inventory = { { mode = 2, item = sword }, { mode = 3, item = armor } },
}
ACTIVE[1] = UNIT
ACTIVE[2] = { pos = { x = 15, y = 20, z = 30 }, desc = 'goblin', hostile = true }
"""

RICH_CHECKS = """
local m = CAPTURED.metrics
assert(CAPTURED.adventurer.name == 'Urist McTest')
assert(CAPTURED.adventurer.stress_category == 5)
assert(m.health_injuries.wound_count == 1)
assert(m.health_injuries.wounds[1].body_part == 'right upper arm')
assert(m.health_injuries.wounds[1].severity == 40)
assert(m.health_injuries.wounds[1].bleeding == true)
assert(m.health_injuries.status == 'bleeding')
assert(m.health_injuries.pain == 12)
assert(#m.health_injuries.status_conditions == 2)
assert(m.needs_state.hunger == 'hungry')
assert(m.needs_state.thirst == 'thirsty')
assert(m.needs_state.sleep == 'drowsy')
assert(m.needs_state.stress_level == 5)
assert(m.inventory.item_count == 2)
assert(math.abs(m.inventory.total_weight_kg - 4.5) < 1e-9)
assert(m.inventory.items[1].weight_kg == 1.5)
assert(m.inventory.items[1].equipped == true)
assert(m.inventory.items[1].quantity == 1)
assert(m.inventory.items[1].material == 'iron')
assert(m.inventory.equipped_weapon == 'iron sword')
assert(m.inventory.equipped_armor[1] == 'leather armor')
assert(m.threats.count == 1 and m.threats.level == 'medium')
assert(m.threats.hostiles[1].name == 'goblin')
assert(m.threats.hostiles[1].distance == 5)
assert(m.threats.hostiles[1].direction == 'E')
assert(m.health_injuries.status_conditions[1] == 'winded')
assert(m.units_nearby.count == 1)
assert(m.location.surroundings == 'outdoors, forest')
assert(m.location.region == 'forest')
assert(m.location.site == 'Boatmurdered')
assert(m.location.site_type == 'DarkFortress')
assert(m.location.tile == 'Grass')
assert(m.game_clock.date_text == 'Timber 27, year 100')
assert(m.game_clock.season == 'autumn')
assert(m.game_clock.hour == 0)
"""


def _load_wrapper():
    """Importa a camada GAME STATE (src/) para o teste de integração; None se indisponível."""
    src = os.path.join(os.path.dirname(HERE), "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from system_one_dwarf.game import GameStateClient
    except ImportError:                                        # pragma: no cover
        return None
    return GameStateClient


WRAPPER_CLIENT = _load_wrapper()


@unittest.skipUnless(WRAPPER_CLIENT, "pacote system_one_dwarf indisponível")
class WrapperIntegrationTests(unittest.TestCase):
    """Wrapper Python (backend TCP) contra o servidor falso — ponta a ponta, sem jogo."""

    def test_wrapper_reads_snapshot_over_rpc(self):
        df = make_df_dir()
        self.addCleanup(shutil.rmtree, df, True)
        srv = TB.FakeDFHack(snapshot_responder())
        srv.start()
        self.addCleanup(srv.stop)
        client = WRAPPER_CLIENT(df_path=df, port=srv.port, backend="tcp")
        state = client.snapshot()
        self.assertEqual(state.adventurer.name, "Urist McTest")
        self.assertEqual(state.metrics.threats.level, "medium")
        self.assertEqual(state.metrics.threats.nearest.direction, "E")
        self.assertEqual(state.metrics.inventory.total_weight_kg, 1.5)
        self.assertEqual(state.metrics.location.surroundings, "outdoors, forest")
        self.assertEqual(state.screen_text, "You are in a forest.\nConversation")
        self.assertEqual(state.recent_events[1].category, "death")
        # payload do Lua sem chaves `nil` → o bridge repõe null e o wrapper lê None
        self.assertIsNone(state.metrics.health_injuries.hp_current)
        self.assertIsNone(state.metrics.health_injuries.hp_max)
        self.assertIsNone(state.metrics.inventory.capacity_kg)
        self.assertIsNone(state.metrics.location.travel_destination)
        self.assertIsNotNone(state.ts)
        self.assertIsNotNone(state.ts.tzinfo)                   # ts do envelope, em UTC

    def test_wrapper_reports_game_not_running(self):
        from system_one_dwarf.game import GameNotRunning
        df = make_df_dir()
        self.addCleanup(shutil.rmtree, df, True)
        client = WRAPPER_CLIENT(df_path=df, port=free_port(), backend="tcp")
        with self.assertRaises(GameNotRunning) as ctx:
            client.snapshot()
        self.assertEqual(ctx.exception.exit_code, B.EXIT_UNREACHABLE)


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class LuaStateScriptTests(unittest.TestCase):
    """Exercita o dfb-state.lua REAL com um stub do ambiente DFHack (sem o jogo)."""

    def run_lua(self, scenario, checks, mode="snapshot", dump=False):
        work = tempfile.mkdtemp(prefix="dflua-")
        self.addCleanup(shutil.rmtree, work, True)
        path = os.path.join(work, "harness.lua")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_lua_harness(scenario, checks, dump=dump))
        proc = subprocess.run([LUA, path, os.path.join(HERE, "dfb-state.lua"), mode],
                              capture_output=True, text=True, cwd=work, timeout=30)
        return proc

    def test_state_with_absent_fields_does_not_crash(self):
        proc = self.run_lua(EMPTY_SCENARIO, EMPTY_CHECKS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_state_counts_wounds_weight_threats_clock(self):
        proc = self.run_lua(RICH_SCENARIO, RICH_CHECKS)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_lua_snapshot_json_normalizes_to_explicit_nulls(self):
        """D1 ponta a ponta: JSON FIEL do Lua (chave `nil` ausente) → o normalizador do
        df_bridge.py repõe os campos do contrato como `null`, sem inventar valores."""
        proc = self.run_lua(RICH_SCENARIO, RICH_CHECKS, dump=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        line = [l for l in proc.stdout.splitlines() if l.startswith("JSON:")]
        self.assertTrue(line, proc.stdout)
        payload = json.loads(line[0][len("JSON:"):])
        raw = payload["metrics"]
        for group, key in (("health_injuries", "hp_current"), ("health_injuries", "hp_max"),
                           ("inventory", "capacity_kg"), ("location", "travel_destination"),
                           ("location", "world")):
            self.assertNotIn(key, raw[group], "o Lua nao devia emitir %s.%s" % (group, key))
        w = []
        snap = B.sanitize_snapshot({"mode": payload.get("mode"),
                                    "adventurer": payload.get("adventurer"),
                                    "metrics": raw, "gamelog_events": []}, w)
        self.assertEqual(w, [])                     # payload fiel: nada de anómalo
        self.assertIsNone(snap["metrics"]["health_injuries"]["hp_current"])
        self.assertIsNone(snap["metrics"]["health_injuries"]["hp_max"])
        self.assertIsNone(snap["metrics"]["inventory"]["capacity_kg"])
        self.assertIsNone(snap["metrics"]["location"]["travel_destination"])
        self.assertIsNone(snap["metrics"]["location"]["world"])
        for group, shape in B.EMPTY_METRICS.items():
            if group != "events":
                self.assertEqual(sorted(snap["metrics"][group]), sorted(shape), group)
        text = B.render_snapshot_text(snap)
        self.assertIn("Health: bleeding — 1 wound(s), pain 12", text)
        self.assertIn("Threats: 1 (medium)", text)

    def test_threat_level_ladder(self):
        # hostil longe (12 tiles) → low
        low_scenario = RICH_SCENARIO.replace(
            "ACTIVE[2] = { pos = { x = 15, y = 20, z = 30 }, desc = 'goblin', hostile = true }",
            "ACTIVE[2] = { pos = { x = 22, y = 20, z = 30 }, desc = 'goblin', hostile = true }")
        proc = self.run_lua(low_scenario,
                            "assert(CAPTURED.metrics.threats.count == 1)\n"
                            "assert(CAPTURED.metrics.threats.level == 'low')\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # great danger perto (3 tiles) → high, mesmo com outro hostil a 7
        high_scenario = RICH_SCENARIO + (
            "ACTIVE[3] = { pos = { x = 13, y = 20, z = 30 }, desc = 'titan', "
            "hostile = true, great_danger = true }\n")
        proc = self.run_lua(high_scenario,
                            "assert(CAPTURED.metrics.threats.count == 2)\n"
                            "assert(CAPTURED.metrics.threats.level == 'high')\n")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_legacy_modes_still_dispatch(self):
        # `all` continua a funcionar (nenhuma regressão nos modos antigos)
        checks = """
assert(CAPTURED.date ~= nil and CAPTURED.adventurer ~= nil)
assert(CAPTURED.threats ~= nil)
assert(CAPTURED.adventurer.pos.x == 88)
"""
        proc = self.run_lua(EMPTY_SCENARIO, checks, mode="all")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)


class AdventurerCombatSnapshotTests(unittest.TestCase):
    """Bloco `adventurer.combat` (prontidão de combate, mudança 02) na ponte Python.

    O bloco nasce no `dfb-state.lua` (`combat_block`) e tem de atravessar o
    `sanitize_snapshot` **intacto** — é dele que `game/readiness.py` tira as
    skills efetivas, atributos, dor/exaustão e sangue para as decisões de
    ataque/fuga. Aditividade: nada do contrato antigo muda e nada é inventado.
    """

    COMBAT = {
        "weapon": {"name": "iron spear", "skill_token": "SPEAR", "skill_noun": "Spearman",
                   "skill": 7, "ranged": False, "armed": True},
        "skills": {"fighter": 7, "dodger": 6, "shield_user": 2, "armor_user": 4,
                   "wrestler": 1, "striker": 2, "kicker": 1},
        "attributes": {"strength": 1300, "agility": 1200, "toughness": 1200, "endurance": 1100,
                       "spatial_sense": 1000, "kinesthetic_sense": 1000, "willpower": 1000},
        "condition": {"blood_ratio": 0.55, "pain": 20, "exhaustion": 1500, "winded": 0,
                      "stunned": 0, "nausea": 0, "dizziness": 0, "webbed": 0,
                      "wound_count": 2, "bleeding": 1},
        "armor": {"pieces": 3, "slots": 3, "shield": True},
    }

    def test_combat_block_passes_through_the_sanitizer_verbatim(self):
        snap = B.sanitize_snapshot({"adventurer": {"name": "Urist McTest",
                                                  "combat": json.loads(json.dumps(self.COMBAT))},
                                    "metrics": {}})
        self.assertEqual(snap["adventurer"]["combat"], self.COMBAT)
        self.assertEqual(snap["adventurer"]["name"], "Urist McTest")
        # os grupos de métricas continuam normalizados à parte (aditividade)
        for group in ("health_injuries", "threats", "inventory", "events"):
            self.assertIn(group, snap["metrics"])

    def test_missing_combat_block_stays_missing_nothing_is_invented(self):
        snap = B.sanitize_snapshot({"adventurer": {"name": "Urist"}, "metrics": {}})
        self.assertNotIn("combat", snap["adventurer"])

    def test_garbage_combat_block_is_kept_and_parses_to_unknown_readiness(self):
        for junk in (["list"], 42, "text", None, {"weapon": "not-an-object"}):
            snap = B.sanitize_snapshot({"adventurer": {"name": "U", "combat": junk}, "metrics": {}})
            self.assertEqual(snap["adventurer"]["combat"], junk)   # preservado, nunca fatal
            if CombatReadiness is not None:
                readiness = CombatReadiness.from_dict(snap["adventurer"])
                self.assertIsInstance(readiness, CombatReadiness)
                self.assertFalse(readiness.known)             # lixo nunca vira medição

    def test_render_survives_the_combat_block(self):
        snap = B.sanitize_snapshot({"adventurer": {"name": "Urist McTest",
                                                  "combat": dict(self.COMBAT)},
                                    "metrics": {}})
        text = B.render_snapshot_text(snap)
        self.assertIn("Urist McTest", text)

    @unittest.skipIf(CombatReadiness is None, "sem src/ no PATH")
    def test_bridge_round_trip_into_the_python_readiness(self):
        snap = B.sanitize_snapshot({"adventurer": {"name": "Urist",
                                                  "combat": json.loads(json.dumps(self.COMBAT))},
                                    "metrics": {}})
        readiness = CombatReadiness.from_dict(snap["adventurer"]["combat"])
        self.assertEqual(readiness.weapon_skill, 7)          # skill EFETIVA da arma
        self.assertEqual(readiness.fighter, 7)
        self.assertEqual(readiness.blood_ratio, 0.55)        # dor/exaustão/sangue
        self.assertEqual(readiness.pain, 20)
        self.assertEqual(readiness.exhaustion, 1500)
        self.assertTrue(readiness.known)


if __name__ == "__main__":
    unittest.main(verbosity=2)
