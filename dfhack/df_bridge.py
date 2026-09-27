#!/usr/bin/env python3
"""Ponte ao vivo com o Dwarf Fortress via DFHack (Linux) — stdlib-only.

LEITURA  — acompanha o gamelog.txt (anúncios/eventos do jogo, em inglês);
ESCRITA  — envia comandos ao console do DFHack de um jogo aberto, via o binário
           dfhack-run (preferido) ou um cliente TCP próprio do protocolo remoto
           do DFHack (localhost, porta 5000 por padrão).

Uso:
  python3 df_bridge.py status --json            # o que foi detectado? dá p/ ler/escrever?
  python3 df_bridge.py log --lines 30           # últimas linhas do gamelog
  python3 df_bridge.py log --new --json         # só o que aconteceu desde a última checagem
  python3 df_bridge.py watch --seconds 15       # segue eventos novos por 15s (termina sozinho)
  python3 df_bridge.py state all                # estado estruturado em JSON (aventureiro, ameaças, data)
  python3 df_bridge.py act screen               # lê o texto visível na tela do jogo
  python3 df_bridge.py act move n --times 3     # anda 3 passos para o norte (adventure)
  python3 df_bridge.py act batch "goto 88,72; key A_TALK; wait short; read screen"  # array de ações
  python3 df_bridge.py run prospect all         # comando arbitrário do console DFHack
  python3 df_bridge.py pause | unpause          # atalhos seguros (lua SetPauseState)

Flags comuns (logo após o subcomando): --df-path, --port, --json, --via auto|run|tcp.
Códigos de saída: 0 ok · 10 DF não encontrado · 11 gamelog ausente ·
12 DFHack/RPC inalcançável (jogo fechado?) · 13 o comando falhou dentro do jogo.
"""
import argparse, glob, json, os, re, socket, struct, subprocess, sys, time

# ---------- protocolo remoto do DFHack (fonte: RemoteClient.h / CoreProtocol.proto) ----------
DEFAULT_PORT = 5000                 # default real do DFHack (remote-server.json)
REQUEST_MAGIC = b"DFHack?\n"
RESPONSE_MAGIC = b"DFHack!\n"
PROTOCOL_VERSION = 1
RPC_RUN_COMMAND = 1                 # método pré-vinculado: dispensa BindMethod
RPC_REPLY_RESULT = -1
RPC_REPLY_FAIL = -2
RPC_REPLY_TEXT = -3
RPC_REQUEST_QUIT = -4
MAX_MESSAGE_SIZE = 64 * 1024 * 1024
HEADER = struct.Struct("<h2xi")     # int16 id, 2 bytes de padding, int32 size (LE)

CR_NAMES = {                        # command_result — no REPLY_FAIL vem no campo size
    -3: "CR_LINK_FAILURE", -2: "CR_WOULD_BREAK", -1: "CR_NOT_IMPLEMENTED",
    0: "CR_OK", 1: "CR_FAILURE", 2: "CR_WRONG_USAGE", 3: "CR_NOT_FOUND",
}

EXIT_OK, EXIT_NO_DF, EXIT_NO_GAMELOG, EXIT_UNREACHABLE, EXIT_CMD_FAILED = 0, 10, 11, 12, 13

STEAM_CANDIDATES = (                # instalação clássica, XDG e flatpak
    "~/.steam/steam/steamapps",
    "~/.local/share/Steam/steamapps",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps",
)

# Categorização leve dos anúncios p/ --json (1º match vence). A tradução de
# verdade fica com o agente — isto só ajuda a triagem.
EVENT_PATTERNS = (
    ("death", re.compile(r"struck down|has died|bled to death|drowned|starved to death|"
                         r"died of thirst|suffocated|found dead|has been killed|killed by", re.I)),
    ("siege", re.compile(r"vile force of darkness|laying siege|under siege|ambush|"
                         r"snatcher|thief", re.I)),
    ("mood", re.compile(r"fey mood|secretive mood|possessed|macabre|fell mood|strange mood|"
                        r"mysterious construction|withdraws from society|claimed a", re.I)),
    ("birth", re.compile(r"given birth|hatched|grown to become", re.I)),
    ("trade", re.compile(r"caravan|merchants|liaison|trade depot", re.I)),
    ("migrants", re.compile(r"migrants? (?:has|have) arrived", re.I)),
    ("combat", re.compile(r"strikes|attacks|charges|wrestles|is fighting|lodges firmly|"
                          r"batters|punches|kicks|bites|stabs|slashes", re.I)),
    ("job_cancel", re.compile(r"cancels .*:|needs .* but|interrupted by", re.I)),
    ("season", re.compile(r"^It is now|has arrived on the calendar|"
                          r"^(Spring|Summer|Autumn|Winter) has arrived", re.I)),
)

# ---------- camada de AÇÃO (act batch) ----------
MOVE_DIRS = frozenset("n s e w ne nw se sw up down wait".split())
WAIT_KEYS = {"short": "A_SHORT_WAIT", "long": "A_WAIT"}
# teclas que invalidam coordenadas locais (viagem recarrega o mapa/sítio)
TRAVEL_VERBS = frozenset(("A_TRAVEL", "A_TRAVEL_MAP", "A_TRAVEL_SLEEP", "A_END_TRAVEL"))
COORD_OPS = frozenset(("goto", "move", "click"))
ACTION_OPS = frozenset(("goto", "move", "key", "click", "wait"))   # ops que mexem no jogo
READ_WHATS = frozenset(("screen", "adventurer", "threats", "units",
                        "inventory", "date", "all", "state"))
_KEY_SHAPE = re.compile(r"^[A-Z_][A-Z0-9_]*$")    # forma de um df.interface_key


class BridgeError(Exception):
    """Erro operacional: mensagem PT + código de saída + dica p/ o agente."""

    def __init__(self, msg, exit_code, hint=None):
        super().__init__(msg)
        self.exit_code = exit_code
        self.hint = hint


# ---------- descoberta do Dwarf Fortress ----------
def steam_library_dirs():
    """steamapps/ candidatos + bibliotecas extras do libraryfolders.vdf."""
    seen, out = set(), []

    def add(path):
        real = os.path.realpath(path)
        if real not in seen and os.path.isdir(path):
            seen.add(real)
            out.append(path)

    for cand in STEAM_CANDIDATES:
        lib = os.path.expanduser(cand)
        add(lib)
        vdf = os.path.join(lib, "libraryfolders.vdf")
        if os.path.isfile(vdf):
            try:
                with open(vdf, encoding="utf-8", errors="replace") as f:
                    for m in re.finditer(r'"path"\s*"([^"]+)"', f.read()):
                        add(os.path.join(m.group(1), "steamapps"))
            except OSError:
                pass
    return out


def looks_like_df(d):
    return any(os.path.exists(os.path.join(d, n)) for n in ("gamelog.txt", "dwarfort", "data"))


def find_df_path(cli_path):
    """(path|None, origem) — flag > env DF_PATH > auto-detecção nas bibliotecas Steam."""
    if cli_path:
        if os.path.isdir(cli_path):
            return cli_path, "--df-path"
        return None, "--df-path inválido: " + cli_path
    env = os.environ.get("DF_PATH")
    if env:
        if os.path.isdir(env):
            return env, "env DF_PATH"
        return None, "DF_PATH inválido: " + env
    for lib in steam_library_dirs():
        cand = os.path.join(lib, "common", "Dwarf Fortress")
        if os.path.isdir(cand) and looks_like_df(cand):
            return cand, "auto (Steam)"
    return None, "não encontrado nas bibliotecas Steam"


def classify(df):
    """nativo|proton|desconhecido — escrita via DFHack Linux só faz sentido no nativo."""
    if os.path.exists(os.path.join(df, "dwarfort")):
        return "nativo"
    if os.path.exists(os.path.join(df, "Dwarf Fortress.exe")):
        return "proton"
    return "desconhecido"


def require_df(args):
    df, how = find_df_path(args.df_path)
    if df is None:
        raise BridgeError("Dwarf Fortress não encontrado (%s)" % how, EXIT_NO_DF,
                          'passe --df-path "/caminho/Dwarf Fortress" ou exporte DF_PATH')
    return df


def resolve_port(args, df):
    """--port > env DFHACK_PORT > remote-server.json do DF > 5000 (default DFHack)."""
    if args.port:
        return args.port
    env = os.environ.get("DFHACK_PORT", "")
    if env.isdigit() and int(env) > 0:
        return int(env)
    if df:
        try:
            with open(os.path.join(df, "dfhack-config", "remote-server.json"),
                      encoding="utf-8") as f:
                port = json.load(f).get("port")
            if isinstance(port, int) and port > 0:
                return port
        except (OSError, ValueError):
            pass
    return DEFAULT_PORT


# ---------- LEITURA: gamelog.txt ----------
def gamelog_path(df):
    path = os.path.join(df, "gamelog.txt")
    if not os.path.isfile(path):
        raise BridgeError("gamelog.txt ainda não existe em %s" % df, EXIT_NO_GAMELOG,
                          "rode o jogo ao menos uma vez para o arquivo ser criado")
    return path


def tail_lines(path, n):
    """Últimas n linhas sem ler o arquivo inteiro (blocos de 8 KiB a partir do fim)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        data = b""
        while size > 0 and data.count(b"\n") <= n:
            step = min(8192, size)
            size -= step
            f.seek(size)
            data = f.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-n:]


def state_file():
    """Bookmark fora do repo/instalação: o installer npm recria scripts/ a cada update."""
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, "df-bridge")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "offsets.json")


def _load_state():
    try:
        with open(state_file(), encoding="utf-8") as f:
            st = json.load(f)
        if isinstance(st, dict) and isinstance(st.get("entries"), dict):
            return st
    except (OSError, ValueError):
        pass
    return {"version": 1, "entries": {}}


def _save_state(st):
    # Escrita atômica; leitores concorrentes = last-writer-wins (ferramenta single-user).
    path = state_file()
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f)
        os.replace(tmp, path)
    except OSError as e:
        print("aviso: não consegui salvar o bookmark (%s)" % e, file=sys.stderr)


def read_new(path):
    """Linhas novas desde o último --new. 1ª vez: ancora no fim e não emite nada
    (evita despejar um histórico de MB no contexto do agente). Rotação/truncamento
    (inode trocou ou offset > tamanho) recomeça do zero."""
    st = _load_state()
    key = os.path.abspath(path)
    stat = os.stat(path)
    entry = st["entries"].get(key)
    if not isinstance(entry, dict):
        start = stat.st_size
    elif entry.get("inode") != stat.st_ino or not isinstance(entry.get("offset"), int) \
            or entry["offset"] > stat.st_size:
        start = 0
    else:
        start = entry["offset"]

    with open(path, "rb") as f:
        f.seek(start)
        chunk = f.read()
    if chunk and not chunk.endswith(b"\n"):
        cut = chunk.rfind(b"\n") + 1    # segura a linha parcial (jogo no meio de um write)
        chunk = chunk[:cut]
    end = start + len(chunk)

    st["entries"][key] = {"offset": end, "inode": stat.st_ino, "size": stat.st_size}
    _save_state(st)
    return chunk.decode("utf-8", errors="replace").splitlines()


def classify_event(line):
    for cat, rx in EVENT_PATTERNS:
        if rx.search(line):
            return cat
    return "other"


# ---------- ESCRITA: protobuf mínimo (proto2, pacote dfproto) ----------
def _pb_varint(n):
    out = bytearray()
    while True:
        byte = n & 0x7F
        n >>= 7
        if n:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _pb_str(field, s):
    data = s.encode("utf-8")
    return bytes([(field << 3) | 2]) + _pb_varint(len(data)) + data


def encode_run_command(command, arguments):
    """dfproto.CoreRunCommandRequest{1: command, 2: arguments (repetido)}."""
    payload = _pb_str(1, command)
    for arg in arguments:
        payload += _pb_str(2, arg)
    return payload


def _read_varint(buf, i):
    val = shift = 0
    while i < len(buf):
        byte = buf[i]
        i += 1
        val |= (byte & 0x7F) << shift
        if not byte & 0x80:
            break
        shift += 7
    return val, i


def _pb_fields(buf):
    """Itera (campo, wiretype, valor) de uma mensagem protobuf simples."""
    i, n = 0, len(buf)
    while i < n:
        key, i = _read_varint(buf, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(buf, i)
        elif wt == 2:
            ln, i = _read_varint(buf, i)
            val = buf[i:i + ln]
            i += ln
        elif wt == 5:
            val = buf[i:i + 4]
            i += 4
        elif wt == 1:
            val = buf[i:i + 8]
            i += 8
        else:
            return                       # wiretype desconhecido: aborta sem quebrar
        yield field, wt, val


def decode_text_notification(buf):
    """dfproto.CoreTextNotification{1: repeated CoreTextFragment{1: text}}."""
    parts = []
    for field, wt, val in _pb_fields(buf):
        if field == 1 and wt == 2:
            for f2, w2, v2 in _pb_fields(val):
                if f2 == 1 and w2 == 2:
                    parts.append(v2.decode("utf-8", errors="replace"))
    return "".join(parts)


# ---------- ESCRITA: cliente RPC ----------
class DFHackRPC:
    """Cliente mínimo do protocolo remoto do DFHack (só o RunCommand, id 1)."""

    def __init__(self, port, timeout=5.0):
        self.port = port
        self.timeout = timeout
        self.sock = None

    def connect(self):
        try:
            self.sock = socket.create_connection(("127.0.0.1", self.port),
                                                 timeout=self.timeout)
        except ConnectionRefusedError:
            raise BridgeError(
                "conexão recusada em 127.0.0.1:%d — o jogo com DFHack está aberto?" % self.port,
                EXIT_UNREACHABLE, "rode `df_bridge.py status`; o DF precisa ter sido lançado via ./dfhack")
        except OSError as e:
            raise BridgeError("não conectou em 127.0.0.1:%d: %s" % (self.port, e),
                              EXIT_UNREACHABLE)
        self.sock.sendall(REQUEST_MAGIC + struct.pack("<i", PROTOCOL_VERSION))
        reply = self._recv_exact(12)
        if reply[:8] != RESPONSE_MAGIC or struct.unpack("<i", reply[8:])[0] != PROTOCOL_VERSION:
            raise BridgeError("a porta %d respondeu, mas não é um DFHack (handshake inválido)"
                              % self.port, EXIT_UNREACHABLE,
                              "outro serviço está usando a porta? confira com --port")

    def run_command(self, command, arguments):
        """→ (texto_do_console, command_result). 0 = CR_OK."""
        payload = encode_run_command(command, arguments)
        self._send(HEADER.pack(RPC_RUN_COMMAND, len(payload)) + payload)
        text = []
        while True:
            mid, size = HEADER.unpack(self._recv_exact(8))
            if mid == RPC_REPLY_FAIL:
                return "".join(text), size          # size É o command_result; FAIL não tem payload
            if size < 0 or size > MAX_MESSAGE_SIZE:
                raise BridgeError("resposta com tamanho inválido (%d) — protocolo dessincronizado"
                                  % size, EXIT_UNREACHABLE)
            body = self._recv_exact(size) if size else b""
            if mid == RPC_REPLY_TEXT:
                text.append(decode_text_notification(body))
            elif mid == RPC_REPLY_RESULT:
                return "".join(text), 0
            else:
                raise BridgeError("id de resposta inesperado do DFHack: %d" % mid,
                                  EXIT_UNREACHABLE)

    def close(self):
        if self.sock is not None:
            try:
                self.sock.sendall(HEADER.pack(RPC_REQUEST_QUIT, 0))
            except OSError:
                pass
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def _send(self, data):
        try:
            self.sock.sendall(data)
        except OSError as e:
            raise BridgeError("erro enviando ao DFHack: %s" % e, EXIT_UNREACHABLE)

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            try:
                chunk = self.sock.recv(n - len(buf))
            except socket.timeout:
                raise BridgeError("timeout falando com o DFHack (o jogo está travado ou ocupado?)",
                                  EXIT_UNREACHABLE)
            except OSError as e:
                raise BridgeError("erro lendo do DFHack: %s" % e, EXIT_UNREACHABLE)
            if not chunk:
                raise BridgeError("o DFHack fechou a conexão no meio da resposta",
                                  EXIT_UNREACHABLE)
            buf += chunk
        return buf


def probe_rpc(port):
    """Handshake completo (não só connect — evita falso-positivo com outro serviço)."""
    rpc = DFHackRPC(port, timeout=2.0)
    try:
        rpc.connect()
        return True
    except BridgeError:
        return False
    finally:
        rpc.close()


def run_via_binary(df, port, argv, timeout=20):
    exe = os.path.join(df, "dfhack-run")
    env = dict(os.environ, DFHACK_PORT=str(port))
    try:
        # cwd=df: o dfhack-run resolve dfhack-config/remote-server.json relativo ao cwd
        proc = subprocess.run([exe] + argv, cwd=df, env=env, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        raise BridgeError("dfhack-run não existe em %s — DFHack não instalado?" % df,
                          EXIT_UNREACHABLE, "rode install_dfhack_linux.sh, ou use --via tcp")
    except PermissionError:
        raise BridgeError("dfhack-run existe mas não é executável (chmod +x?)",
                          EXIT_UNREACHABLE, "use --via tcp")
    except subprocess.TimeoutExpired:
        raise BridgeError("dfhack-run não respondeu em %ds (jogo travado?)" % timeout,
                          EXIT_UNREACHABLE)
    out = (proc.stdout or "") + (proc.stderr or "")
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)   # o console do DFHack vaza cores ANSI
    if proc.returncode != 0:
        low = out.lower()
        if "connect" in low or "socket" in low or not out.strip():
            raise BridgeError("dfhack-run não alcançou o jogo (rc=%d): %s"
                              % (proc.returncode, out.strip() or "sem saída"),
                              EXIT_UNREACHABLE, "o jogo com DFHack está aberto?")
        raise BridgeError("o comando falhou no jogo: %s" % out.strip(), EXIT_CMD_FAILED)
    return out


def send_command(df, port, argv, via):
    """Fachada: dfhack-run quando disponível, senão TCP. Retorna dict normalizado."""
    exe = os.path.join(df, "dfhack-run") if df else None
    run_ok = bool(exe) and os.access(exe, os.X_OK)
    if via == "run" and not run_ok:
        raise BridgeError("--via run, mas dfhack-run não está disponível/executável no diretório do DF",
                          EXIT_UNREACHABLE, "use --via tcp, ou rode install_dfhack_linux.sh")
    if via == "run" or (via == "auto" and run_ok):
        return {"ok": True, "via": "dfhack-run", "code": 0,
                "text": run_via_binary(df, port, argv)}
    rpc = DFHackRPC(port)
    try:
        rpc.connect()
        text, code = rpc.run_command(argv[0], argv[1:])
    finally:
        rpc.close()
    if code != 0:
        cr = CR_NAMES.get(code, str(code))
        raise BridgeError("o comando falhou no jogo (%s): %s" % (cr, text.strip() or "sem saída do console"),
                          EXIT_CMD_FAILED,
                          "CR_NOT_FOUND = esse comando não existe nesta versão do DFHack" if code == 3 else None)
    return {"ok": True, "via": "tcp", "code": 0, "text": text}


# ---------- subcomandos ----------
def emit(payload, args, human):
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(human)


def cmd_status(args):
    df, how = find_df_path(args.df_path)
    info = {"ok": df is not None, "df_path": df, "found_via": how}
    if df is None:
        emit(info, args, "Dwarf Fortress não encontrado (%s).\n"
                         '  Dica: --df-path "/caminho/Dwarf Fortress" ou exporte DF_PATH.' % how)
        return EXIT_NO_DF

    kind = classify(df)
    glog = os.path.join(df, "gamelog.txt")
    has_glog = os.path.isfile(glog)
    port = resolve_port(args, df)
    exe = os.path.join(df, "dfhack-run")
    info.update({
        "kind": kind,
        "gamelog": {"path": glog, "exists": has_glog,
                    "size": os.path.getsize(glog) if has_glog else 0,
                    "mtime": int(os.path.getmtime(glog)) if has_glog else None},
        "dfhack_installed": os.path.isdir(os.path.join(df, "hack")),
        "dfhack_run": os.access(exe, os.X_OK),
        "rpc": {"port": port, "reachable": probe_rpc(port)},
    })
    info["ok_read"] = info["gamelog"]["exists"]
    info["ok_write"] = info["rpc"]["reachable"]
    if kind == "proton":
        info["warning"] = ("instalação Windows/Proton detectada — o DFHack Linux não se aplica; "
                           "a leitura do gamelog ainda funciona")

    lines = ["Dwarf Fortress: %s  [%s, %s]" % (df, how, kind)]
    g = info["gamelog"]
    lines.append("gamelog.txt:    %s" % ("ok — %d bytes" % g["size"] if g["exists"]
                                         else "AUSENTE (rode o jogo ao menos uma vez)"))
    lines.append("DFHack:         %s%s" % ("instalado" if info["dfhack_installed"]
                                           else "NÃO instalado (rode install_dfhack_linux.sh)",
                                           ", dfhack-run ok" if info["dfhack_run"] else ""))
    lines.append("RPC:            porta %d — %s" % (port, "ALCANÇÁVEL (jogo aberto)"
                                                    if info["rpc"]["reachable"]
                                                    else "inalcançável (jogo fechado?)"))
    lines.append("→ leitura %s · escrita %s" % ("OK" if info["ok_read"] else "indisponível",
                                                "OK" if info["ok_write"] else "indisponível"))
    if info.get("warning"):
        lines.append("aviso: " + info["warning"])
    emit(info, args, "\n".join(lines))
    return EXIT_OK


def cmd_log(args):
    glog = gamelog_path(require_df(args))
    lines = read_new(glog) if args.new else tail_lines(glog, args.lines)
    if args.json:
        print(json.dumps([{"text": l, "category": classify_event(l)} for l in lines],
                         ensure_ascii=False, indent=2))
    elif not lines:
        print("(nenhum evento novo)" if args.new else "(gamelog vazio)")
    else:
        for l in lines:
            print(l)
    return EXIT_OK


def cmd_watch(args):
    glog = gamelog_path(require_df(args))
    deadline = time.monotonic() + args.seconds
    pos = os.path.getsize(glog)
    buf = b""
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(glog)
        except OSError:                  # arquivo sumiu no meio (rotação): espera voltar
            time.sleep(args.poll)
            continue
        if size < pos:                   # truncado/rotacionado: recomeça do zero
            pos, buf = 0, b""
        if size > pos:
            with open(glog, "rb") as f:
                f.seek(pos)
                chunk = f.read()
            pos += len(chunk)
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", errors="replace").rstrip("\r")
                if args.json:            # JSON-lines: 1 objeto por linha, p/ streaming
                    print(json.dumps({"text": line, "category": classify_event(line)},
                                     ensure_ascii=False), flush=True)
                else:
                    print(line, flush=True)
        time.sleep(args.poll)
    return EXIT_OK


def cmd_run(args):
    argv = list(args.command or [])
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        raise BridgeError("uso: df_bridge.py run <comando> [args...] — ex.: run prospect all",
                          EXIT_CMD_FAILED)
    df, _ = find_df_path(args.df_path)
    if df is None and args.via != "tcp":
        # sem o diretório do DF não há dfhack-run; só dá para seguir por TCP puro
        if args.via == "run":
            raise BridgeError("Dwarf Fortress não encontrado e --via run exige o binário dfhack-run",
                              EXIT_NO_DF, "passe --df-path, ou use --via tcp")
        args.via = "tcp"
    port = resolve_port(args, df)
    res = send_command(df, port, argv, args.via)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        out = res["text"].strip()
        print(out if out else "ok (via %s, sem saída do console)" % res["via"])
    return EXIT_OK


def cmd_pause(args, state):
    args.command = ["lua", "dfhack.world.SetPauseState(%s)" % ("true" if state else "false")]
    return cmd_run(args)


_SCRIPTS_INSTALLED = False


def ensure_game_scripts(df):
    """Instala/atualiza todos os dfb-*.lua no script-path do DF (1x por processo).
    DF_BRIDGE_SKIP_INSTALL=1 pula tudo (testes offline / DF remoto)."""
    global _SCRIPTS_INSTALLED
    if _SCRIPTS_INSTALLED or os.environ.get("DF_BRIDGE_SKIP_INSTALL"):
        return
    here = os.path.dirname(os.path.abspath(__file__))
    srcs = sorted(glob.glob(os.path.join(here, "dfb-*.lua")))
    if not srcs:
        raise BridgeError("nenhum dfb-*.lua ao lado do df_bridge.py",
                          EXIT_CMD_FAILED, "instalação das skills incompleta?")
    dst_dir = os.path.join(df, "dfhack-config", "scripts")
    for src in srcs:
        dst = os.path.join(dst_dir, os.path.basename(src))
        with open(src, "rb") as f:
            want = f.read()
        try:
            with open(dst, "rb") as f:
                if f.read() == want:           # já instalado e atual
                    continue
        except OSError:
            pass
        try:
            os.makedirs(dst_dir, exist_ok=True)
            with open(dst, "wb") as f:
                f.write(want)
        except OSError as e:
            raise BridgeError("não consegui instalar %s em %s (%s)"
                              % (os.path.basename(src), dst_dir, e), EXIT_CMD_FAILED)
    _SCRIPTS_INSTALLED = True


def run_game_script(args, df, argv):
    """Roda um script dfb-* dentro do jogo e devolve (JSON impresso, via)."""
    ensure_game_scripts(df)
    port = resolve_port(args, df)
    res = send_command(df, port, argv, args.via)
    text = res["text"].strip()
    # o script imprime só JSON ({...} ou [...]); extrai pelo delimitador que ABRE primeiro
    brace, bracket = text.find("{"), text.find("[")
    if bracket >= 0 and (brace < 0 or bracket < brace):
        start, end = bracket, text.rfind("]")
    else:
        start, end = brace, text.rfind("}")
    if start < 0 or end <= start:
        raise BridgeError("resposta sem JSON de %s: %s" % (argv[0], text[:200] or "(vazia)"),
                          EXIT_CMD_FAILED, "há um mundo carregado? rode status")
    try:
        return json.loads(text[start:end + 1]), res["via"]
    except ValueError as e:
        raise BridgeError("JSON inválido de %s (%s): %s" % (argv[0], e, text[:200]),
                          EXIT_CMD_FAILED)


def emit_data(args, data, via, what):
    if args.json:
        print(json.dumps({"ok": True, "via": via, "what": what, "data": data},
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_state(args):
    df = require_df(args)
    data, via = run_game_script(args, df, ["dfb-state", args.what, str(args.radius)])
    emit_data(args, data, via, args.what)
    return EXIT_OK


def _goto(args, df, tx, ty, max_steps):
    """Anda até (tx,ty) com verificação por passo e recomputação de rota ao travar."""
    steps_taken, recomputes = 0, 0
    adv, _ = run_game_script(args, df, ["dfb-state", "adventurer", "1"])
    pos = adv["pos"]
    while steps_taken < max_steps:
        if max(abs(pos["x"] - tx), abs(pos["y"] - ty)) <= 1:
            return {"ok": True, "arrived": True, "pos": pos, "steps": steps_taken,
                    "recomputes": recomputes}
        route, _ = run_game_script(args, df, ["dfb-nav", "route",
                                              str(tx), str(ty), str(pos["z"])])
        if not route.get("ok"):
            return {"ok": False, "error": "sem rota até o alvo", "pos": pos,
                    "steps": steps_taken, "detail": route}
        progressed = False
        for step in route["steps"]:
            run_game_script(args, df, ["dfb-act", "move", step])
            time.sleep(args.delay)
            after, _ = run_game_script(args, df, ["dfb-state", "adventurer", "1"])
            steps_taken += 1
            if after["pos"] == pos:        # bloqueado (NPC/porta): recomputa a rota
                break
            pos = after["pos"]
            progressed = True
            if max(abs(pos["x"] - tx), abs(pos["y"] - ty)) <= 1 \
                    or steps_taken >= max_steps:
                break
        recomputes += 1
        if not progressed and recomputes >= 5:
            return {"ok": False, "error": "preso: 5 rotas seguidas sem progresso",
                    "pos": pos, "steps": steps_taken}
    return {"ok": max(abs(pos["x"] - tx), abs(pos["y"] - ty)) <= 1,
            "arrived": max(abs(pos["x"] - tx), abs(pos["y"] - ty)) <= 1,
            "pos": pos, "steps": steps_taken, "recomputes": recomputes,
            "note": "orçamento de passos esgotado" if steps_taken >= max_steps else None}


def _do_move(args, df, direction, times):
    """Anda `times` passos numa direção, relê a posição e devolve (data, via).
    Usado pelo `act move` e pelo executor de batch."""
    times = max(1, times)
    data, via = None, None
    for _ in range(times):
        data, via = run_game_script(args, df, ["dfb-act", "move", direction])
        time.sleep(args.delay)                 # cada passo é 1 turno; deixa o frame rodar
    after, _ = run_game_script(args, df, ["dfb-state", "adventurer", "1"])
    data["pos_after"] = after.get("pos")
    data["steps"] = times
    return data, via


def cmd_act(args):
    if args.action == "batch":
        return cmd_act_batch(args)
    df = require_df(args)
    if args.action == "goto":
        if len(args.args) < 2:
            raise BridgeError("uso: act goto <x> <y> [--max-steps N]", EXIT_CMD_FAILED)
        data = _goto(args, df, int(args.args[0]), int(args.args[1]), args.max_steps)
        emit_data(args, data, "auto", "goto")
        return EXIT_OK if data.get("ok") else EXIT_CMD_FAILED
    if args.action == "move":
        if not args.args:
            raise BridgeError("uso: act move <n|s|e|w|ne|nw|se|sw|up|down|wait> [--times N]",
                              EXIT_CMD_FAILED)
        data, via = _do_move(args, df, args.args[0], args.times)
        emit_data(args, data, via, "move")
    else:
        data, via = run_game_script(args, df, ["dfb-act", args.action] + list(args.args))
        if args.action == "screen" and not args.json:
            print("\n".join(data.get("lines", [])))
            print("· focus: %s" % "/".join(data.get("focus", [])))
            return EXIT_OK
        emit_data(args, data, via, args.action)
    return EXIT_OK


# ---------- act batch: parsing ----------
def _parse_dsl_line(line):
    """Uma linha do DSL → passo normalizado. Ex.: 'goto 88,72' 'key A_TALK' 'wait short'."""
    parts = line.split()
    op = parts[0].lower()
    rest = parts[1:]
    try:
        if op == "goto":
            nums = " ".join(rest).replace(",", " ").split()
            step = {"op": "goto", "x": int(nums[0]), "y": int(nums[1])}
            for tok in nums[2:]:
                if tok.startswith("maxsteps="):
                    step["maxsteps"] = int(tok.split("=", 1)[1])
            return step
        if op == "move":
            step = {"op": "move", "dir": rest[0].lower(), "times": 1}
            for tok in rest[1:]:
                if tok.startswith("times="):
                    step["times"] = int(tok.split("=", 1)[1])
            return step
        if op == "key":
            return {"op": "key", "keys": list(rest)}
        if op == "click":
            nums = " ".join(rest).replace(",", " ").split()
            button = "right" if any(t.lower() == "right" for t in nums[2:]) else "left"
            return {"op": "click", "x": int(nums[0]), "y": int(nums[1]), "button": button}
        if op == "wait":
            return {"op": "wait", "kind": (rest[0].lower() if rest else "short")}
        if op == "read":
            return {"op": "read", "what": (rest[0].lower() if rest else "screen")}
        if op == "expect":
            return {"op": "expect", "kind": rest[0].lower(), "needle": " ".join(rest[1:])}
        if op == "sleep":
            return {"op": "sleep", "ms": int(rest[0])}
    except (IndexError, ValueError):
        raise BridgeError("passo mal formado: %r" % line, EXIT_CMD_FAILED,
                          "sintaxe: goto X,Y | move DIR | key NOME | click X,Y | wait short | read screen")
    raise BridgeError("passo desconhecido: %r" % line, EXIT_CMD_FAILED)


def _validate_step(s, i):
    op = s.get("op")
    if op == "move":
        if s.get("dir") not in MOVE_DIRS:
            raise BridgeError("passo %d move: direção inválida %r" % (i, s.get("dir")), EXIT_CMD_FAILED)
        s["times"] = int(s.get("times", 1))
    elif op == "goto":
        s["x"], s["y"] = int(s["x"]), int(s["y"])
    elif op == "click":
        s["x"], s["y"] = int(s["x"]), int(s["y"])
        s["button"] = s.get("button", "left")
    elif op == "key":
        keys = s.get("keys") or ([s["key"]] if s.get("key") else [])
        if not keys:
            raise BridgeError("passo %d key: sem teclas" % i, EXIT_CMD_FAILED)
        for k in keys:
            if not _KEY_SHAPE.match(k):
                raise BridgeError("passo %d key: nome inválido %r" % (i, k), EXIT_CMD_FAILED,
                                  "use nomes de df.interface_key, ex.: A_TALK, SELECT, CUSTOM_A")
        s["keys"] = keys
    elif op == "wait":
        s["kind"] = str(s.get("kind", "short"))
    elif op == "read":
        if s.get("what", "screen") not in READ_WHATS:
            raise BridgeError("passo %d read: alvo inválido %r" % (i, s.get("what")), EXIT_CMD_FAILED)
        s["what"] = s.get("what", "screen")
    elif op == "expect":
        if s.get("kind") not in ("focus", "text"):
            raise BridgeError("passo %d expect: use 'focus' ou 'text'" % i, EXIT_CMD_FAILED)
        if not s.get("needle"):
            raise BridgeError("passo %d expect: falta o texto a procurar" % i, EXIT_CMD_FAILED)
    elif op == "sleep":
        s["ms"] = int(s.get("ms", 0))
    else:
        raise BridgeError("passo %d: op desconhecido %r" % (i, op), EXIT_CMD_FAILED)
    return s


def parse_batch(text_or_obj):
    """DSL (linhas/`;`, `#` comentário) OU lista JSON → lista de passos validados."""
    if isinstance(text_or_obj, list):
        raw_steps = text_or_obj
    else:
        text = text_or_obj.strip()
        if text.startswith("["):
            try:
                raw_steps = json.loads(text)
            except ValueError as e:
                raise BridgeError("JSON de batch inválido: %s" % e, EXIT_CMD_FAILED)
            if not isinstance(raw_steps, list):
                raise BridgeError("batch JSON deve ser uma lista de passos", EXIT_CMD_FAILED)
        else:
            raw_steps = []
            for chunk in re.split(r"[;\n]", text):
                line = chunk.split("#", 1)[0].strip()
                if line:
                    raw_steps.append(_parse_dsl_line(line))
    steps = [_validate_step(dict(s), i) for i, s in enumerate(raw_steps)]
    if not steps:
        raise BridgeError("batch vazio (nada para executar)", EXIT_CMD_FAILED)
    return steps


# ---------- act batch: journal ----------
def journal_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    d = os.path.join(base, "df-bridge", "journal")
    os.makedirs(d, exist_ok=True)
    return d


def journal_append(path, record):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print("aviso: journal falhou (%s)" % e, file=sys.stderr)


# ---------- act batch: execução ----------
def _threat_summary(args, df):
    """Lê ameaças ao redor (sempre reportado; não aborta salvo --stop-on-threat)."""
    try:
        data, _ = run_game_script(args, df, ["dfb-state", "threats", str(args.threat_radius)])
    except BridgeError:
        return {"count": 0, "error": "scan de ameaça falhou"}
    threats = data.get("threats", [])
    return {"count": len(threats), "nearest": threats[0] if threats else None}


def emit_batch(args, report):
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for r in report["steps"]:
        line = "  [%d] %s" % (r["i"], r["op"])
        if r.get("would"):
            line += " (dry-run)"
        if "error" in r:
            line += " ERRO: " + r["error"]
        elif r.get("ok") is False:
            line += " (falhou)"
        t = r.get("threats") or {}
        if t.get("count"):
            line += " ⚠ %d ameaça(s)" % t["count"]
        print(line)
    s = report["summary"]
    if report.get("reason"):
        print("ABORTADO no passo %s: %s" % (report["aborted_at"], report["reason"]))
    print("resumo: %d ok / %d passos · %d falhou%s"
          % (s.get("ok", 0), s.get("total", 0), s.get("failed", 0),
             ("\njournal: " + report["journal"]) if report.get("journal") else ""))


def cmd_act_batch(args):
    df = require_df(args)
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    elif args.stdin:
        text = sys.stdin.read()
    else:
        text = " ".join(args.args or [])
    if not text.strip():
        raise BridgeError("act batch: nada para executar", EXIT_CMD_FAILED,
                          'passe DSL ("goto 10,10; key A_TALK"), --file ou --stdin')
    steps = parse_batch(text)

    if args.dry_run:
        report = {"ok": True, "via": "dry-run", "dry_run": True,
                  "steps": [{"i": i, "op": s["op"], "would": s} for i, s in enumerate(steps)],
                  "summary": {"total": len(steps), "validated": len(steps)},
                  "aborted_at": None, "reason": None, "journal": None}
        emit_batch(args, report)
        return EXIT_OK

    jpath = None
    if not args.no_journal:
        jpath = os.path.join(journal_dir(), time.strftime("%Y%m%dT%H%M%S") + ".jsonl")

    results = []
    summary = {"total": len(steps), "executed": 0, "ok": 0, "failed": 0, "move_steps": 0}
    aborted_at, reason, via, traveled, last_screen = None, None, "auto", False, ""
    try:
        baseline_focus = run_game_script(args, df, ["dfb-act", "focus"])[0].get("focus")
    except BridgeError:
        baseline_focus = None

    def finish(rec, is_ok):
        results.append(rec)
        summary["executed"] += 1
        summary["ok" if is_ok else "failed"] += 1
        if jpath:
            journal_append(jpath, rec)

    for i, s in enumerate(steps):
        op = s["op"]
        rec = {"i": i, "op": op}
        # barreira de viagem: coords ficam inválidas após A_TRAVEL
        if op in COORD_OPS and traveled and not args.allow_travel_barrier:
            rec.update({"ok": False, "skipped": "coords inválidas após viagem"})
            finish(rec, False)
            aborted_at, reason = i, "travel_barrier"
            break
        try:
            if op == "goto":
                data = _goto(args, df, s["x"], s["y"], s.get("maxsteps", args.max_steps))
                rec.update(data)
                summary["move_steps"] += data.get("steps", 0)
            elif op == "move":
                data, via = _do_move(args, df, s["dir"], s["times"])
                rec.update({"ok": data["pos_after"] != data.get("pos_before"),
                            "dir": s["dir"], "pos_after": data["pos_after"],
                            "steps": data["steps"], "focus": data.get("focus")})
                summary["move_steps"] += s["times"]
            elif op == "key":
                data, via = run_game_script(args, df, ["dfb-act", "key"] + s["keys"])
                rec.update({"ok": True, "pressed": s["keys"], "focus": data.get("focus")})
                if any(k in TRAVEL_VERBS for k in s["keys"]):
                    traveled = True
            elif op == "click":
                data, via = run_game_script(args, df,
                                            ["dfb-act", "click", str(s["x"]), str(s["y"]), s["button"]])
                rec.update({"ok": True, "clicked": data.get("clicked"), "focus": data.get("focus")})
            elif op == "wait":
                kind = s["kind"]
                keys = [WAIT_KEYS[kind]] if kind in WAIT_KEYS else ["A_SHORT_WAIT"] * max(1, int(kind))
                for k in keys:
                    data, via = run_game_script(args, df, ["dfb-act", "key", k])
                    time.sleep(args.delay)
                rec.update({"ok": True, "waited": kind})
            elif op == "read":
                what = s["what"]
                if what == "screen":
                    data, via = run_game_script(args, df, ["dfb-act", "screen"])
                    last_screen = "\n".join(data.get("lines", []))
                    rec.update({"ok": True, "lines": data.get("lines"), "focus": data.get("focus")})
                else:
                    data, via = run_game_script(args, df,
                                                ["dfb-state", "all" if what == "state" else what,
                                                 str(args.threat_radius)])
                    rec.update({"ok": True, "state": data})
            elif op == "expect":
                cur = run_game_script(args, df, ["dfb-act", "focus"])[0].get("focus", [])
                hay = "/".join(cur) if s["kind"] == "focus" else last_screen
                ok = s["needle"].lower() in hay.lower()
                rec.update({"ok": ok, "kind": s["kind"], "needle": s["needle"],
                            "saw": "/".join(cur) if s["kind"] == "focus" else None})
                finish(rec, ok)
                if not ok:
                    aborted_at, reason = i, "expect_failed"
                    break
                continue
            elif op == "sleep":
                time.sleep(s["ms"] / 1000.0)
                rec.update({"ok": True, "ms": s["ms"]})
        except BridgeError as e:
            rec.update({"ok": False, "error": str(e)})
            finish(rec, False)
            if e.exit_code == EXIT_UNREACHABLE:          # jogo morreu no meio
                aborted_at, reason = i, "game_lost"
                break
            continue                                     # erro de passo isolado: segue (autonomia total)

        # após ações: deixa o frame rodar e SEMPRE reporta ameaças
        if op in ACTION_OPS:
            time.sleep(args.delay)
            rec["threats"] = _threat_summary(args, df)
            cur_focus = rec.get("focus")
            finish(rec, rec.get("ok", True))
            if args.stop_on_threat and rec["threats"]["count"] > 0:
                aborted_at, reason = i, "threat"
                break
            if (args.stop_on_focus_change and baseline_focus is not None
                    and cur_focus and cur_focus != baseline_focus):
                aborted_at, reason = i, "focus_changed"
                break
        else:
            finish(rec, rec.get("ok", True))

    report = {"ok": aborted_at is None, "via": via, "dry_run": False, "steps": results,
              "summary": summary, "aborted_at": aborted_at, "reason": reason, "journal": jpath}
    if jpath:
        journal_append(jpath, {"final": True, "summary": summary,
                               "aborted_at": aborted_at, "reason": reason})
    emit_batch(args, report)
    if reason == "game_lost":
        return EXIT_UNREACHABLE
    return EXIT_OK if aborted_at is None else EXIT_CMD_FAILED


def fail(msg, code, hint, as_json):
    if as_json:
        print(json.dumps({"ok": False, "error": msg, "hint": hint, "exit_code": code},
                         ensure_ascii=False, indent=2))
    else:
        print("erro: " + msg, file=sys.stderr)
        if hint:
            print("dica: " + hint, file=sys.stderr)
    sys.exit(code)


def main():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--df-path", help="diretório do Dwarf Fortress (senão: $DF_PATH ou auto-detecção Steam)")
    common.add_argument("--port", type=int, help="porta RPC do DFHack (senão: $DFHACK_PORT, remote-server.json, 5000)")
    common.add_argument("--json", action="store_true", help="saída estruturada para agentes")
    common.add_argument("--via", choices=("auto", "run", "tcp"), default="auto",
                        help="como enviar comandos: binário dfhack-run, TCP direto, ou auto (padrão)")

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", parents=[common], help="o que foi detectado? dá para ler/escrever?")
    p = sub.add_parser("log", parents=[common], help="últimas linhas do gamelog")
    p.add_argument("--lines", type=int, default=20, help="quantas linhas do fim (padrão 20)")
    p.add_argument("--new", action="store_true", help="só o que apareceu desde o último --new")
    p = sub.add_parser("watch", parents=[common], help="segue eventos novos por N segundos")
    p.add_argument("--seconds", type=float, default=10.0, help="duração (padrão 10s; sempre termina)")
    p.add_argument("--poll", type=float, default=0.5, help="intervalo de checagem (padrão 0.5s)")
    p = sub.add_parser("run", parents=[common], help="envia um comando ao console do DFHack")
    p.add_argument("command", nargs=argparse.REMAINDER,
                   help="comando e argumentos (ex.: prospect all) — flags vêm ANTES dele")
    sub.add_parser("pause", parents=[common], help="pausa o jogo (lua SetPauseState)")
    sub.add_parser("unpause", parents=[common], help="despausa o jogo")
    p = sub.add_parser("state", parents=[common],
                       help="estado estruturado do jogo em JSON (script Lua embarcado)")
    p.add_argument("what", choices=("adventurer", "threats", "units", "inventory", "date", "all"),
                   help="qual recorte do estado ler")
    p.add_argument("--radius", type=int, default=25,
                   help="raio de varredura para units/threats (padrão 25)")
    p = sub.add_parser("act", parents=[common],
                       help="observa a tela e age no jogo (camada de copiloto)")
    p.add_argument("action", choices=("focus", "screen", "key", "click", "move", "goto", "batch"),
                   help="focus/screen/key/click/move/goto · batch=array de ações (DSL ou JSON)")
    p.add_argument("args", nargs="*",
                   help="move: dir · key: teclas · click: x y [right] · screen: [y1 y2] · goto: x y · batch: passos DSL")
    p.add_argument("--times", type=int, default=1, help="repete o passo (move) N vezes")
    p.add_argument("--delay", type=float, default=0.4,
                   help="pausa entre passos e antes de ler o efeito (padrão 0.4s)")
    p.add_argument("--max-steps", type=int, default=120,
                   help="orçamento de passos do goto/batch (padrão 120)")
    # batch (array de ações):
    p.add_argument("--dry-run", action="store_true",
                   help="batch: valida e ecoa o plano sem pressionar nada")
    p.add_argument("--stop-on-threat", action="store_true",
                   help="batch: aborta se aparecer ameaça (padrão: só reporta)")
    p.add_argument("--stop-on-focus-change", action="store_true",
                   help="batch: aborta se a tela mudar sozinha")
    p.add_argument("--threat-radius", type=int, default=10,
                   help="batch: raio do scan de ameaças por passo (padrão 10)")
    p.add_argument("--allow-travel-barrier", action="store_true",
                   help="batch: permite passos de coordenada após uma viagem (coords ficam inválidas)")
    p.add_argument("--file", help="batch: lê o plano (DSL ou JSON) de um arquivo")
    p.add_argument("--stdin", action="store_true", help="batch: lê o plano do stdin")
    p.add_argument("--no-journal", action="store_true", help="batch: não grava o journal JSONL")

    args = ap.parse_args()
    handlers = {
        "status": cmd_status,
        "log": cmd_log,
        "watch": cmd_watch,
        "run": cmd_run,
        "pause": lambda a: cmd_pause(a, True),
        "unpause": lambda a: cmd_pause(a, False),
        "state": cmd_state,
        "act": cmd_act,
    }
    try:
        sys.exit(handlers[args.cmd](args))
    except BridgeError as e:
        fail(str(e), e.exit_code, e.hint, args.json)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
