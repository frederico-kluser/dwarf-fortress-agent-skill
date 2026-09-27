#!/usr/bin/env python3
"""Testes offline da ponte df_bridge.py — stdlib only (unittest), sem o jogo.

Usa um servidor DFHack FALSO (handshake + RunCommand RPC) para exercitar o cliente
TCP, o parser de batch, o --dry-run e o journaling sem precisar do Dwarf Fortress
aberto. Rode:  python3 .agents/skills/scripts/test_bridge.py

O servidor falso responde a cada RunCommand conforme um roteiro: o primeiro token do
comando (ou 'lua:<script>') mapeia para (texto, código). Scripts dfb-* devolvem JSON.
"""
import io, json, os, socket, struct, sys, tempfile, threading, unittest
from contextlib import redirect_stdout, redirect_stderr

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["DF_BRIDGE_SKIP_INSTALL"] = "1"          # nunca instalar scripts nos testes
import df_bridge as B                                # noqa: E402


# ---------- servidor DFHack falso ----------
class FakeDFHack(threading.Thread):
    """Fala o protocolo remoto real do DFHack numa porta efêmera.
    `responder(command, args) -> (text, code)` decide cada resposta."""

    def __init__(self, responder):
        super().__init__(daemon=True)
        self.responder = responder
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(8)
        self.port = self.srv.getsockname()[1]
        self.requests = []

    def run(self):
        while True:
            try:
                conn, _ = self.srv.accept()
            except OSError:
                return
            try:
                self._serve(conn)
            except (OSError, ValueError):
                pass
            finally:
                conn.close()

    def _recv(self, conn, n):
        buf = b""
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                raise OSError("eof")
            buf += chunk
        return buf

    def _serve(self, conn):
        hs = self._recv(conn, 12)
        assert hs[:8] == B.REQUEST_MAGIC
        conn.sendall(B.RESPONSE_MAGIC + struct.pack("<i", B.PROTOCOL_VERSION))
        while True:
            try:
                mid, size = B.HEADER.unpack(self._recv(conn, 8))
            except OSError:
                return
            if mid == B.RPC_REQUEST_QUIT:
                return
            payload = self._recv(conn, size) if size else b""
            command, args = self._decode(payload)
            self.requests.append((command, args))
            text, code = self.responder(command, args)
            for frag in (text or "").split("\x00"):     # permite múltiplos TEXT
                if frag:
                    body = self._text_frame(frag)
                    conn.sendall(B.HEADER.pack(B.RPC_REPLY_TEXT, len(body)) + body)
            if code == 0:
                conn.sendall(B.HEADER.pack(B.RPC_REPLY_RESULT, 0))
            else:
                conn.sendall(B.HEADER.pack(B.RPC_REPLY_FAIL, code))   # size = command_result

    @staticmethod
    def _decode(payload):
        command, args = None, []
        for field, wt, val in B._pb_fields(payload):
            if field == 1 and wt == 2:
                command = val.decode("utf-8")
            elif field == 2 and wt == 2:
                args.append(val.decode("utf-8"))
        return command, args

    @staticmethod
    def _text_frame(s):
        # CoreTextNotification{1: CoreTextFragment{1: text}}
        inner = B._pb_str(1, s)
        return bytes([(1 << 3) | 2]) + B._pb_varint(len(inner)) + inner

    def stop(self):
        self.srv.close()


def make_args(**kw):
    """argparse.Namespace mínimo com os defaults que os comandos esperam."""
    ns = type("NS", (), {})()
    defaults = dict(df_path="/tmp", port=None, json=False, via="tcp", lines=20, new=False,
                    seconds=1.0, poll=0.1, command=None, what="all", radius=10,
                    action=None, args=[], times=1, delay=0.0, max_steps=50,
                    dry_run=False, stop_on_threat=False, stop_on_focus_change=False,
                    threat_radius=10, allow_travel_barrier=False, file=None, stdin=False,
                    no_journal=True)
    defaults.update(kw)
    for k, v in defaults.items():
        setattr(ns, k, v)
    return ns


def capture(fn, *a, **k):
    """Roda fn capturando stdout; devolve (retorno, stdout)."""
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        rv = fn(*a, **k)
    return rv, out.getvalue()


# responder que imita os scripts dfb-* que o batch usa
def scripted_responder(state_seq=None, focus="dungeonmode/Default"):
    """state_seq: lista de respostas p/ chamadas 'dfb-state adventurer' consecutivas
    (cada uma {pos:{...}}). Outras chamadas devolvem JSON plausível."""
    calls = {"adv": 0}
    seq = state_seq or [{"pos": {"x": 10, "y": 10, "z": 5}}]

    def respond(command, args):
        if command == "dfb-act":
            sub = args[0] if args else ""
            if sub == "focus":
                return json.dumps({"focus": [focus]}), 0
            if sub == "key":
                return json.dumps({"ok": True, "pressed": args[1:], "focus": [focus]}), 0
            if sub == "click":
                return json.dumps({"ok": True, "clicked": {"x": int(args[1]), "y": int(args[2])},
                                   "focus": [focus]}), 0
            if sub == "move":
                return json.dumps({"ok": True, "dir": args[1], "pos_before": {"x": 10, "y": 10, "z": 5},
                                   "focus": [focus]}), 0
            if sub == "screen":
                return json.dumps({"focus": [focus], "lines": ["linha um", "Conversation aqui"]}), 0
        if command == "dfb-state":
            what = args[0] if args else "all"
            if what == "adventurer":
                i = min(calls["adv"], len(seq) - 1)
                calls["adv"] += 1
                return json.dumps(seq[i]), 0
            if what == "threats":
                return json.dumps({"radius": 10, "threats": []}), 0
            if what == "date":
                return json.dumps({"year": 100, "month": "Granite", "day": 1, "season": "spring"}), 0
            return json.dumps({"date": {"year": 100}, "adventurer": seq[0], "threats": []}), 0
        if command == "dfb-nav":
            return json.dumps({"ok": True, "len": 1, "steps": ["e"]}), 0
        return "ok", 0
    return respond


class ProtocolTests(unittest.TestCase):
    def test_handshake_and_text_and_result(self):
        srv = FakeDFHack(lambda c, a: ("hello world", 0)); srv.start()
        try:
            res = B.send_command("/tmp", srv.port, ["lua", "print(1)"], "tcp")
            self.assertTrue(res["ok"])
            self.assertEqual(res["text"], "hello world")
        finally:
            srv.stop()

    def test_fail_maps_to_exit_13(self):
        srv = FakeDFHack(lambda c, a: ("", 3)); srv.start()   # CR_NOT_FOUND
        try:
            with self.assertRaises(B.BridgeError) as cm:
                B.send_command("/tmp", srv.port, ["naoexiste"], "tcp")
            self.assertEqual(cm.exception.exit_code, B.EXIT_CMD_FAILED)
            self.assertIn("CR_NOT_FOUND", str(cm.exception))
        finally:
            srv.stop()

    def test_multi_fragment_text(self):
        srv = FakeDFHack(lambda c, a: ("parte1\x00parte2", 0)); srv.start()
        try:
            res = B.send_command("/tmp", srv.port, ["x"], "tcp")
            self.assertEqual(res["text"], "parte1parte2")
        finally:
            srv.stop()


class ParseTests(unittest.TestCase):
    def test_dsl_all_ops(self):
        steps = B.parse_batch("goto 88,72; move n times=3; key A_TALK; click 26,9 right; "
                              "wait short; read screen; expect focus Conversation; sleep 100")
        self.assertEqual([s["op"] for s in steps],
                         ["goto", "move", "key", "click", "wait", "read", "expect", "sleep"])
        self.assertEqual(steps[0]["x"], 88); self.assertEqual(steps[1]["times"], 3)
        self.assertEqual(steps[3]["button"], "right")

    def test_comments_and_separators(self):
        steps = B.parse_batch("key A_TALK  # fala\nkey SELECT")
        self.assertEqual(len(steps), 2)

    def test_json_form(self):
        steps = B.parse_batch('[{"op":"move","dir":"s","times":2}]')
        self.assertEqual(steps[0]["dir"], "s")

    def test_rejects_bad_key(self):
        with self.assertRaises(B.BridgeError):
            B.parse_batch("key minuscula")

    def test_rejects_bad_op(self):
        with self.assertRaises(B.BridgeError):
            B.parse_batch("voar n")

    def test_rejects_bad_dir(self):
        with self.assertRaises(B.BridgeError):
            B.parse_batch("move xyz")

    def test_empty_is_error(self):
        with self.assertRaises(B.BridgeError):
            B.parse_batch("   ")


class BatchExecTests(unittest.TestCase):
    def test_dry_run_presses_nothing(self):
        srv = FakeDFHack(scripted_responder()); srv.start()
        try:
            args = make_args(action="batch", dry_run=True, port=srv.port,
                             args=["key A_TALK; move n"])
            rc, out = capture(B.cmd_act_batch, args)
            self.assertEqual(rc, B.EXIT_OK)
            self.assertEqual(srv.requests, [])          # ZERO chamadas ao jogo
        finally:
            srv.stop()

    def test_runs_sequence(self):
        srv = FakeDFHack(scripted_responder()); srv.start()
        try:
            args = make_args(action="batch", json=True, port=srv.port,
                             args=["key A_TALK; read screen"])
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertTrue(rep["ok"])
            self.assertEqual(rep["summary"]["total"], 2)
            self.assertIsNone(rep["aborted_at"])
        finally:
            srv.stop()

    def test_expect_failed_aborts(self):
        srv = FakeDFHack(scripted_responder(focus="dungeonmode/Default")); srv.start()
        try:
            args = make_args(action="batch", json=True, port=srv.port,
                             args=["expect focus Conversation"])   # foco é Default → falha
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertEqual(rc, B.EXIT_CMD_FAILED)
            self.assertEqual(rep["reason"], "expect_failed")
            self.assertEqual(rep["aborted_at"], 0)
        finally:
            srv.stop()

    def test_stop_on_threat(self):
        def responder(command, args):
            if command == "dfb-state" and args and args[0] == "threats":
                return json.dumps({"threats": [{"name": "dragon", "dist": 3, "danger": True}]}), 0
            return scripted_responder()(command, args)
        srv = FakeDFHack(responder); srv.start()
        try:
            args = make_args(action="batch", json=True, port=srv.port,
                             stop_on_threat=True, args=["key A_TALK; key SELECT"])
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertEqual(rep["reason"], "threat")
            self.assertEqual(rep["aborted_at"], 0)       # para no 1º passo já com ameaça
        finally:
            srv.stop()

    def test_threats_always_reported(self):
        srv = FakeDFHack(scripted_responder()); srv.start()
        try:
            args = make_args(action="batch", json=True, port=srv.port, args=["key A_TALK"])
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertIn("threats", rep["steps"][0])    # autonomia total: reporta, não aborta
        finally:
            srv.stop()

    def test_travel_barrier(self):
        srv = FakeDFHack(scripted_responder()); srv.start()
        try:
            args = make_args(action="batch", json=True, port=srv.port,
                             args=["key A_TRAVEL; goto 5,5"])
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertEqual(rep["reason"], "travel_barrier")
        finally:
            srv.stop()

    def test_journal_written(self):
        srv = FakeDFHack(scripted_responder()); srv.start()
        tmp = tempfile.mkdtemp()
        try:
            os.environ["XDG_STATE_HOME"] = tmp
            args = make_args(action="batch", json=True, port=srv.port,
                             no_journal=False, args=["key A_TALK"])
            rc, out = capture(B.cmd_act_batch, args)
            rep = json.loads(out)
            self.assertTrue(os.path.isfile(rep["journal"]))
            with open(rep["journal"]) as f:
                lines = [json.loads(l) for l in f if l.strip()]
            self.assertTrue(any(r.get("final") for r in lines))
        finally:
            os.environ.pop("XDG_STATE_HOME", None)
            srv.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
