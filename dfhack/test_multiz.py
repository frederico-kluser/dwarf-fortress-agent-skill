#!/usr/bin/env python3
"""Testes offline do roteamento MULTI-Z: `dfb-nav.lua` (route / route3d / reach) e o
`goto X,Y,Z` do `df_bridge.py` (ARQUITETURA §9 / PROBLEMAS H2).

Sem o jogo. Duas camadas:

* Lua REAL (`lua5.4`/`lua`/`luajit` no PATH, senão salta): o `dfb-nav.lua` corre dentro
  de um stub do ambiente DFHack com um mapa 3D pequeno (escadas, rampas, paredes,
  portas) descrito em ASCII. Inclui regressão da BFS planar contra uma CÓPIA da v1 e
  um oráculo Python independente (grafo explícito) sobre mapas aleatórios com semente.
* Ponte Python: parse do DSL (`goto X,Y,Z`) e `_goto3d` contra o servidor DFHack FALSO
  de `test_bridge.py`, com um simulador de jogo que aplica as MESMAS regras do mapa.

Tudo o que é multi-z é EXPERIMENTAL (nunca foi corrido ao vivo): estes testes provam a
coerência interna (regras documentadas ↔ BFS ↔ verificação por passo), não o jogo.

Rode:  python3 dfhack/test_multiz.py
"""
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import deque
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["DF_BRIDGE_SKIP_INSTALL"] = "1"          # nunca instalar scripts nos testes
import df_bridge as B                                # noqa: E402
import test_bridge as TB                             # noqa: E402  (servidor DFHack falso)

LUA = shutil.which("lua5.4") or shutil.which("lua") or shutil.which("luajit")
NAV = os.path.join(HERE, "dfb-nav.lua")
DF = tempfile.gettempdir()          # "diretório do DF" dos testes: só tem de existir (TMPDIR)

# ====================================================================================
# Mapa 3D em ASCII + oráculo Python (independente da BFS do Lua)
# ====================================================================================
SHAPE_ORDER = ["EMPTY", "FLOOR", "BOULDER", "PEBBLES", "WALL", "FORTIFICATION",
               "STAIR_UP", "STAIR_DOWN", "STAIR_UPDOWN", "RAMP", "RAMP_TOP"]
MATERIAL_ORDER = ["AIR", "STONE", "LAVA_STONE"]
ATTR_WALKABLE = ["FLOOR", "BOULDER", "PEBBLES", "STAIR_UP", "STAIR_DOWN", "STAIR_UPDOWN", "RAMP"]

# glifo → (forma, material, occupancy.building)
GLYPHS = {
    " ": ("EMPTY", "AIR", 0),            # espaço aberto
    ".": ("FLOOR", "STONE", 0),
    "#": ("WALL", "STONE", 0),
    "F": ("FORTIFICATION", "STONE", 0),
    "<": ("STAIR_UP", "STONE", 0),
    ">": ("STAIR_DOWN", "STONE", 0),
    "X": ("STAIR_UPDOWN", "STONE", 0),
    "/": ("RAMP", "STONE", 0),
    "^": ("RAMP_TOP", "AIR", 0),         # o espaço aberto que o DF escreve sobre uma rampa
    "D": ("FLOOR", "STONE", 3),          # porta: chão + edifício
    "L": ("WALL", "LAVA_STONE", 0),      # "semi-molten rock": não conta como suporte de rampa
    "b": ("WALL", "STONE", 3),           # parede com edifício (a v1 conta-a como passável)
}
DIRS = [("n", 0, -1), ("s", 0, 1), ("e", 1, 0), ("w", -1, 0),
        ("ne", 1, -1), ("nw", -1, -1), ("se", 1, 1), ("sw", -1, 1)]
OPPOSITE = {"n": "s", "s": "n", "e": "w", "w": "e", "ne": "sw", "sw": "ne", "nw": "se", "se": "nw"}


class World:
    """Mapa 3D (`layers[z][y]` = linha de glifos) + as regras documentadas como ORÁCULO.

    O grafo de ligações em z é construído por varrimento (centrado na rampa/escada e
    bidirecional), de propósito diferente da expansão por nó do Lua."""

    def __init__(self, layers):
        self.layers = [list(layer) for layer in layers]
        self.depth = len(self.layers)
        self.height = max(len(layer) for layer in self.layers)
        self.width = max(len(row) for layer in self.layers for row in layer)
        self._special = None

    # ----- tiles
    def glyph(self, x, y, z):
        if x < 0 or y < 0 or z < 0 or z >= self.depth:
            return None
        layer = self.layers[z]
        if y >= len(layer) or x >= len(layer[y]):
            return None
        return layer[y][x]

    def info(self, x, y, z):
        g = self.glyph(x, y, z)
        return GLYPHS[g] if g is not None else None

    def shape(self, x, y, z):
        info = self.info(x, y, z)
        return info[0] if info else None

    def walkable(self, x, y, z):
        """Predicado da v1 (porta/edifício conta) + escadas/rampas."""
        info = self.info(x, y, z)
        return bool(info) and (info[0] in ATTR_WALKABLE or info[2] != 0)

    def solid(self, x, y, z):
        info = self.info(x, y, z)
        return bool(info) and info[0] in ("WALL", "FORTIFICATION") and info[1] != "LAVA_STONE"

    # ----- grafo
    def special_edges(self):
        if self._special is not None:
            return self._special
        edges = {}

        def add(a, token, b):
            edges.setdefault(a, []).append((token, b))

        for z in range(self.depth):
            for y in range(self.height):
                for x in range(self.width):
                    shape = self.shape(x, y, z)
                    if shape in ("STAIR_UP", "STAIR_UPDOWN") and \
                            self.shape(x, y, z + 1) in ("STAIR_DOWN", "STAIR_UPDOWN"):
                        add((x, y, z), "up", (x, y, z + 1))
                        add((x, y, z + 1), "down", (x, y, z))
                    if shape == "RAMP" and self.shape(x, y, z + 1) in ("EMPTY", "RAMP_TOP"):
                        for name, dx, dy in DIRS:
                            ux, uy = x + dx, y + dy
                            if self.solid(ux, uy, z) and self.walkable(ux, uy, z + 1):
                                add((x, y, z), name + "_up", (ux, uy, z + 1))
                                add((ux, uy, z + 1), OPPOSITE[name] + "_down", (x, y, z))
        self._special = edges
        return edges

    def moves(self, pos):
        x, y, z = pos
        out = [(name, (x + dx, y + dy, z)) for name, dx, dy in DIRS
               if self.walkable(x + dx, y + dy, z)]
        out.extend(self.special_edges().get(pos, []))
        return out

    def shortest(self, start, goal):
        """Nº mínimo de passos start→goal, ou None."""
        seen, queue = {start: 0}, deque([start])
        while queue:
            cur = queue.popleft()
            if cur == goal:
                return seen[cur]
            for _name, nxt in self.moves(cur):
                if nxt not in seen:
                    seen[nxt] = seen[cur] + 1
                    queue.append(nxt)
        return None

    def replay(self, start, steps):
        """Aplica os passos; devolve a posição final ou None se algum for ilegal."""
        pos = start
        for token in steps:
            nxt = [dest for name, dest in self.moves(pos) if name == token]
            if not nxt:
                return None
            pos = nxt[0]
        return pos

    def trace(self, start, steps):
        """Posições visitadas (incluindo a inicial) ao aplicar os passos."""
        out, pos = [start], start
        for token in steps:
            pos = [dest for name, dest in self.moves(pos) if name == token][0]
            out.append(pos)
        return out

    def components(self):
        """Grupo de caminhada (id > 0) de cada tile caminhável; 0 = não caminhável."""
        group, next_id = {}, 1
        for z in range(self.depth):
            for y in range(self.height):
                for x in range(self.width):
                    pos = (x, y, z)
                    if pos in group or not self.walkable(x, y, z):
                        continue
                    group[pos] = next_id
                    queue = deque([pos])
                    while queue:
                        cur = queue.popleft()
                        for _name, nxt in self.moves(cur):
                            if nxt not in group and self.walkable(*nxt):
                                group[nxt] = next_id
                                queue.append(nxt)
                    next_id += 1
        return group

    def groups_table(self):
        """O mesmo que `components`, na forma que o stub Lua lê: {"x,y,z": id}."""
        return {"%d,%d,%d" % pos: gid for pos, gid in self.components().items()}

    def walkable_tiles(self):
        return [(x, y, z) for z in range(self.depth) for y in range(self.height)
                for x in range(self.width) if self.walkable(x, y, z)]


# ----- mapas de cenário (cada um com o desenho e a rota esperada no teste) -----
# Escada: corredor z=0 com STAIR_UP em x=4; z=1 com STAIR_DOWN no mesmo (x,y).
MAP_STAIRS = World([["##########", "#...<....#", "##########"],
                    ["##########", "#...>....#", "##########"]])
# Rampa: terraço em z=1 (x=1..4, em cima de paredes) liga à RAMP (5,1,0).
MAP_RAMP = World([["##########", "#####/....", "##########"],
                  ["##########", "#....^    ", "##########"]])
# Combinado: parede (5..6,1,0) obriga a subir a escada (x=3), andar em z=1 e descer a
# rampa (7,1,0) pelo terraço (6,1,1).
MAP_COMBO = World([["############", "#..<.##/...#", "############"],
                   ["############", "###>...^   #", "############"]])
# Contorno: a parede (5,1,0) corta o corredor; sobe-se em x=3 e desce-se em x=7.
MAP_DETOUR = World([["###########", "#..<.#.<..#", "###########"],
                    ["###########", "#..>...>..#", "###########"]])
# Porta (edifício num tile de chão) no caminho para a escada em x=6.
MAP_DOOR = World([["##########", "#...D.<..#", "##########"],
                  ["##########", "#.....>..#", "##########"]])


def _lua_literal(value):
    """Python → literal Lua (só ASCII: o json.dumps é um literal de string válido)."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, dict):
        return "{" + ",".join("[%s]=%s" % (_lua_literal(k), _lua_literal(v))
                              for k, v in value.items()) + "}"
    return "{" + ",".join(_lua_literal(v) for v in value) + "}"


HARNESS = r"""
-- stub mínimo do ambiente DFHack em volta do dfb-nav.lua REAL
local SCRIPT = arg[1]
local LAYERS = __LAYERS__
local GLYPHS = __GLYPHS__
local SHAPES = __SHAPES__
local MATERIALS = __MATERIALS__
local ATTR_WALKABLE = __ATTR_WALKABLE__
local GROUPS = __GROUPS__
local QUERIES = __QUERIES__
local unpack = table.unpack or unpack

df = { tiletype_shape = { attrs = {} }, tiletype_material = {}, tiletype = { attrs = {} } }
for i, name in ipairs(SHAPES) do
  df.tiletype_shape[name] = i - 1
  df.tiletype_shape.attrs[i - 1] = { walkable = false }
end
for _, name in ipairs(ATTR_WALKABLE) do
  df.tiletype_shape.attrs[df.tiletype_shape[name]].walkable = true
end
for i, name in ipairs(MATERIALS) do df.tiletype_material[name] = i - 1 end

local TT_BY_CHAR, BUILDING, n = {}, {}, 0
for ch, g in pairs(GLYPHS) do
  n = n + 1
  TT_BY_CHAR[ch] = n
  df.tiletype.attrs[n] = { shape = df.tiletype_shape[g[1]], material = df.tiletype_material[g[2]] }
  BUILDING[n] = g[3]
end

local function tile_id(x, y, z)
  if x < 0 or y < 0 or z < 0 then return nil end
  local layer = LAYERS[z + 1]
  if not layer then return nil end
  local row = layer[y + 1]
  if not row or x >= #row then return nil end
  return TT_BY_CHAR[row:sub(x + 1, x + 1)]
end

dfhack = { maps = {
  getTileType = function(x, y, z) return tile_id(x, y, z) end,
  getTileFlags = function(x, y, z)
    local id = tile_id(x, y, z)
    if not id then return nil, nil end
    return { hidden = false }, { building = BUILDING[id] }
  end,
} }
if GROUPS ~= nil then
  dfhack.maps.getWalkableGroup = function(pos)
    return GROUPS[pos.x .. ',' .. pos.y .. ',' .. pos.z] or 0
  end
  dfhack.maps.canWalkBetween = function(a, b)
    local ga, gb = dfhack.maps.getWalkableGroup(a), dfhack.maps.getWalkableGroup(b)
    return ga ~= 0 and ga == gb
  end
end

-- encoder JSON do harness (tabela vazia → [], como o do DFHack; chaves ordenadas)
local function q(s)
  return '"' .. s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n') .. '"'
end
local enc
enc = function(v)
  local t = type(v)
  if t == 'nil' then return 'null' end
  if t == 'boolean' then return v and 'true' or 'false' end
  if t == 'number' then
    if v == math.floor(v) then return string.format('%d', v) end
    return string.format('%.6f', v)
  end
  if t == 'string' then return q(v) end
  if t ~= 'table' then return 'null' end
  local count, is_arr = 0, true
  for k in pairs(v) do
    count = count + 1
    if type(k) ~= 'number' then is_arr = false end
  end
  if is_arr then
    local parts = {}
    for i = 1, count do parts[#parts + 1] = enc(v[i]) end
    return '[' .. table.concat(parts, ',') .. ']'
  end
  local keys = {}
  for k in pairs(v) do keys[#keys + 1] = tostring(k) end
  table.sort(keys)
  local parts = {}
  for _, k in ipairs(keys) do parts[#parts + 1] = q(k) .. ':' .. enc(v[k]) end
  return '{' .. table.concat(parts, ',') .. '}'
end

local UNIT, PRINTED = nil, {}
local COMMON = {
  json = { encode = enc },
  version_guard = function() end,
  adventurer = function() return UNIT end,
}
reqscript = function(name) return COMMON end
qerror = function(msg) error({ qerror = msg }, 0) end
print = function(s) PRINTED[#PRINTED + 1] = tostring(s) end

local chunk = assert(loadfile(SCRIPT))
for qi, query in ipairs(QUERIES) do
  UNIT = { pos = { x = query.start[1], y = query.start[2], z = query.start[3] } }
  PRINTED = {}
  local ok, err = pcall(chunk, unpack(query.argv))
  if ok then
    io.write('Q', qi, ':OUT:', table.concat(PRINTED, '\n'), '\n')
  elseif type(err) == 'table' and err.qerror then
    io.write('Q', qi, ':QERROR:', err.qerror, '\n')
  else
    io.write('Q', qi, ':LUAERR:', tostring(err):gsub('\n', ' '), '\n')
  end
end
"""


def run_nav(world, queries, groups=None, script=NAV, walkable_attrs=None):
    """Corre o `script` (dfb-nav.lua) uma vez por consulta, no mapa `world`.

    `queries` = [((sx, sy, sz), [argv...]), ...]. Devolve uma lista (uma por consulta)
    de dicts do JSON impresso, ou {"__qerror__": mensagem} quando o script faz qerror.
    `groups` (dict "x,y,z"→id) liga a API de grupos de caminhada; None = API ausente.
    `walkable_attrs` substitui a lista de formas com `walkable=true` nos atributos."""
    source = (HARNESS
              .replace("__LAYERS__", _lua_literal(world.layers))
              .replace("__GLYPHS__", _lua_literal({k: list(v) for k, v in GLYPHS.items()}))
              .replace("__SHAPES__", _lua_literal(SHAPE_ORDER))
              .replace("__MATERIALS__", _lua_literal(MATERIAL_ORDER))
              .replace("__ATTR_WALKABLE__",
                       _lua_literal(ATTR_WALKABLE if walkable_attrs is None else walkable_attrs))
              .replace("__GROUPS__", _lua_literal(groups))
              .replace("__QUERIES__", _lua_literal(
                  [{"start": list(start), "argv": [str(a) for a in argv]}
                   for start, argv in queries])))
    work = tempfile.mkdtemp(prefix="dfnav-")
    try:
        path = os.path.join(work, "harness.lua")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        proc = subprocess.run([LUA, path, script], capture_output=True, encoding="utf-8",
                              cwd=work, timeout=60)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if proc.returncode != 0:
        raise AssertionError("harness Lua falhou: %s" % proc.stderr)
    results = {}
    for line in proc.stdout.splitlines():
        head, _, rest = line.partition(":")
        kind, _, payload = rest.partition(":")
        index = int(head[1:])
        if kind == "OUT":
            results[index] = json.loads(payload)
        elif kind == "QERROR":
            results[index] = {"__qerror__": payload}
        else:
            raise AssertionError("erro Lua inesperado: %s" % line)
    return [results[i + 1] for i in range(len(queries))]


def nav(world, start, *argv, **kw):
    """Uma consulta: `nav(world, (1,1,0), "route3d", 7, 1, 1)` → dict."""
    return run_nav(world, [(start, list(argv))], **kw)[0]


def random_world(seed, w=9, h=7, depth=4):
    """Terreno com alturas 0..2, rampas naturais, escadas, portas, fortificações e lava."""
    rng = random.Random(seed)
    heights = [[rng.choice((0, 0, 1, 1, 2)) for _ in range(w)] for _ in range(h)]
    grid = [[[" "] * w for _ in range(h)] for _ in range(depth)]
    for y in range(h):
        for x in range(w):
            for z in range(depth):
                top = heights[y][x]
                grid[z][y][x] = "#" if z < top else ("." if z == top else " ")
    for y in range(h):                                  # rampas naturais
        for x in range(w):
            top = heights[y][x]
            higher = any(0 <= x + dx < w and 0 <= y + dy < h and heights[y + dy][x + dx] == top + 1
                         for _n, dx, dy in DIRS)
            if top + 1 < depth and higher and rng.random() < 0.6:
                grid[top][y][x] = "/"
                if rng.random() < 0.8:
                    grid[top + 1][y][x] = "^"
    for _ in range(rng.randint(0, 2)):                  # colunas de escada
        x, y = rng.randrange(w), rng.randrange(h)
        z0 = rng.randrange(0, depth - 1)
        z1 = rng.randrange(z0 + 1, depth)
        for z in range(z0, z1 + 1):
            grid[z][y][x] = "<" if z == z0 else (">" if z == z1 else "X")
    for _ in range(rng.randint(0, 4)):                  # portas, fortificações, lava
        x, y = rng.randrange(w), rng.randrange(h)
        z = heights[y][x]
        if grid[z][y][x] == ".":
            grid[z][y][x] = rng.choice("DD.")
        if z > 0 and grid[z - 1][y][x] == "#":
            grid[z - 1][y][x] = rng.choice("FL##")
    return World(["".join(row) for row in layer] for layer in grid)


# Cópia da v1 (planar) — referência para provar que o caminho padrão não regrediu.
V1_NAV_SOURCE = r"""
local C = reqscript('dfb-common')
local json = C.json
C.version_guard()

local args = {...}
if args[1] ~= 'route' then qerror('uso: dfb-nav route <x> <y> <z> [max_nos]') end
local tx, ty, tz = tonumber(args[2]), tonumber(args[3]), tonumber(args[4])
local maxn = tonumber(args[5]) or 25000
if not (tx and ty and tz) then qerror('coordenadas inválidas') end

local adv = C.adventurer()
local sx, sy, sz = adv.pos.x, adv.pos.y, adv.pos.z
if tz ~= sz then qerror('v1 só roteia no mesmo z (você z=' .. sz .. ', alvo z=' .. tz .. ')') end

local function walkable(x, y, z)
  local tt = dfhack.maps.getTileType(x, y, z)
  if not tt then return false end
  local sh = df.tiletype.attrs[tt].shape
  if df.tiletype_shape.attrs[sh].walkable then return true end
  local _, occ = dfhack.maps.getTileFlags(x, y, z)
  return occ and occ.building ~= 0          -- porta fechada: abre ao andar
end

local DIRS = { {0,-1,'n'}, {0,1,'s'}, {1,0,'e'}, {-1,0,'w'},
               {1,-1,'ne'}, {-1,-1,'nw'}, {1,1,'se'}, {-1,1,'sw'} }
local function key(x, y) return x * 100000 + y end

local prev, prevdir = {}, {}
local q, qi = { {sx, sy} }, 1
prev[key(sx, sy)] = 'start'
local found, n = false, 0

while qi <= #q and n < maxn do
  local cur = q[qi]; qi = qi + 1; n = n + 1
  local cx, cy = cur[1], cur[2]
  if cx == tx and cy == ty then found = true; break end
  for _, d in ipairs(DIRS) do
    local nx, ny = cx + d[1], cy + d[2]
    local k = key(nx, ny)
    if not prev[k] and math.abs(nx - sx) <= 70 and math.abs(ny - sy) <= 70
       and walkable(nx, ny, sz) then
      prev[k] = { cx, cy }
      prevdir[k] = d[3]
      q[#q + 1] = { nx, ny }
    end
  end
end

if not found then
  print(json.encode({ ok = false, explored = n,
                      hint = 'sem caminho no mesmo z dentro do raio 70' }))
  return
end

local steps = {}
local cx, cy = tx, ty
while not (cx == sx and cy == sy) do
  local k = key(cx, cy)
  steps[#steps + 1] = prevdir[k]
  local p = prev[k]
  cx, cy = p[1], p[2]
end
local rev = {}
for i = #steps, 1, -1 do rev[#rev + 1] = steps[i] end
print(json.encode({ ok = true, len = #rev, steps = rev }))
"""

_V1_DIR = None


def v1_script():
    """Caminho de um ficheiro com a cópia da v1 (criado 1x por processo)."""
    global _V1_DIR
    if _V1_DIR is None:
        _V1_DIR = tempfile.mkdtemp(prefix="dfnav-v1-")
    path = os.path.join(_V1_DIR, "dfb-nav-v1.lua")
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(V1_NAV_SOURCE)
    return path


def tearDownModule():
    if _V1_DIR:
        shutil.rmtree(_V1_DIR, ignore_errors=True)


# ====================================================================================
# 1. dfb-nav.lua (Lua real, stub do DFHack)
# ====================================================================================
@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class PlanarUnchangedTests(unittest.TestCase):
    """`route` com z igual ao do aventureiro tem de continuar IGUAL à v1."""

    def v1(self, world, start, argv):
        return run_nav(world, [(start, argv)], script=v1_script())[0]

    def test_same_z_route_equals_the_v1_script_on_fixed_maps(self):
        cases = [
            (MAP_STAIRS, (1, 1, 0), ["route", 7, 1, 0]),      # corredor
            (MAP_STAIRS, (1, 1, 1), ["route", 8, 1, 1]),
            (MAP_RAMP, (8, 1, 0), ["route", 5, 1, 0]),
            (MAP_COMBO, (1, 1, 0), ["route", 4, 1, 0]),
            (MAP_DETOUR, (1, 1, 0), ["route", 9, 1, 0]),      # sem caminho planar
            (MAP_STAIRS, (1, 1, 0), ["route", 1, 1, 0]),      # já lá: len 0
            (MAP_STAIRS, (1, 1, 0), ["route", 4, 0, 0]),      # alvo numa parede
            (MAP_STAIRS, (1, 1, 0), ["route", 99, 99, 0]),    # fora do mapa
            (MAP_STAIRS, (1, 1, 0), ["route", 9, 1, 0, 3]),   # max_nos curto
        ]
        for world, start, argv in cases:
            got = run_nav(world, [(start, argv)], script=NAV)[0]
            self.assertEqual(got, self.v1(world, start, argv), (start, argv))

    def test_same_z_route_equals_the_v1_script_on_random_maps(self):
        for seed in range(12):
            world = random_world(seed)
            tiles = world.walkable_tiles()
            rng = random.Random(1000 + seed)
            queries = []
            for _ in range(6):
                (sx, sy, sz), (tx, ty, _tz) = rng.choice(tiles), rng.choice(tiles)
                queries.append(((sx, sy, sz), ["route", tx, ty, sz]))
            mine = run_nav(world, queries, script=NAV)
            theirs = run_nav(world, queries, script=v1_script())
            self.assertEqual(mine, theirs, "semente %d" % seed)

    def test_planar_route_ignores_the_group_api(self):
        arena = World([["#######", "#.....#", "#.###.#", "#.....#", "#######"]])
        groups = arena.groups_table()
        groups["3,1,0"] = 999
        with_api = nav(arena, (1, 1, 0), "route", 5, 1, 0, groups=groups)
        self.assertEqual(with_api, self.v1(arena, (1, 1, 0), ["route", 5, 1, 0]))
        self.assertEqual(with_api["len"], 4)

    def test_planar_output_has_the_v1_shape_only(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route", 7, 1, 0)
        self.assertEqual(sorted(got), ["len", "ok", "steps"])
        self.assertEqual(got, {"ok": True, "len": 6, "steps": ["e"] * 6})
        fail = nav(MAP_DETOUR, (1, 1, 0), "route", 9, 1, 0)
        self.assertEqual(sorted(fail), ["explored", "hint", "ok"])
        self.assertFalse(fail["ok"])

    def test_door_counts_as_passable(self):
        door = World([["#####", "#.D.#", "#####"]])
        self.assertEqual(nav(door, (1, 1, 0), "route", 3, 1, 0)["steps"], ["e", "e"])
        self.assertEqual(nav(door, (1, 1, 0), "route3d", 3, 1, 0)["steps"], ["e", "e"])

    def test_v1_quirk_wall_with_building_is_passable_in_both_modes(self):
        quirk = World([["#####", "#.b.#", "#####"]])
        self.assertEqual(nav(quirk, (1, 1, 0), "route", 3, 1, 0)["steps"], ["e", "e"])
        self.assertEqual(nav(quirk, (1, 1, 0), "route3d", 3, 1, 0)["steps"], ["e", "e"])


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class Route3DTests(unittest.TestCase):
    def assertRoute(self, got, steps, z_changes=()):
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["mode"], "3d")
        self.assertEqual(got["steps"], steps)
        self.assertEqual(got["len"], len(steps))
        self.assertEqual(got["z_changes"], list(z_changes))

    # ----- escadas
    def test_up_stairs_route(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route", 7, 1, 1)         # tz ≠ sz → 3D
        self.assertRoute(got, ["e", "e", "e", "up", "e", "e", "e"],
                         [{"i": 3, "dz": 1, "kind": "stair", "step": "up", "x": 4, "y": 1, "z": 1}])
        self.assertEqual(got["origin"], {"x": 1, "y": 1, "z": 0})
        self.assertEqual(got["target"], {"x": 7, "y": 1, "z": 1})

    def test_down_stairs_route(self):
        got = nav(MAP_STAIRS, (7, 1, 1), "route3d", 1, 1, 0)
        self.assertRoute(got, ["w", "w", "w", "down", "w", "w", "w"],
                         [{"i": 3, "dz": -1, "kind": "stair", "step": "down", "x": 4, "y": 1, "z": 0}])

    def test_door_on_the_way_to_the_stairs(self):
        got = nav(MAP_DOOR, (1, 1, 0), "route", 8, 1, 1)
        self.assertRoute(got, ["e", "e", "e", "e", "e", "up", "e", "e"],
                         [{"i": 5, "dz": 1, "kind": "stair", "step": "up", "x": 6, "y": 1, "z": 1}])

    def test_updown_stair_chains_through_the_middle_level(self):
        tower = World([["#####", "#<..#", "#####"],
                       ["#####", "#X..#", "#####"],
                       ["#####", "#>..#", "#####"]])
        got = nav(tower, (3, 1, 0), "route", 3, 1, 2)
        self.assertRoute(got, ["w", "w", "up", "up", "e", "e"],
                         [{"i": 2, "dz": 1, "kind": "stair", "step": "up", "x": 1, "y": 1, "z": 1},
                          {"i": 3, "dz": 1, "kind": "stair", "step": "up", "x": 1, "y": 1, "z": 2}])

    def test_stair_needs_the_matching_half_above(self):
        broken = World([["##########", "#...<....#", "##########"],
                        ["##########", "#........#", "##########"]])   # sem STAIR_DOWN
        got = nav(broken, (1, 1, 0), "route", 7, 1, 1)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "no_path")

    def test_stair_down_needs_the_matching_half_below(self):
        broken = World([["##########", "#........#", "##########"],    # sem STAIR_UP
                        ["##########", "#...>....#", "##########"]])
        got = nav(broken, (7, 1, 1), "route", 1, 1, 0)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "no_path")

    # ----- rampas
    def test_down_ramp_route(self):
        got = nav(MAP_RAMP, (1, 1, 1), "route", 8, 1, 0)
        self.assertRoute(got, ["e", "e", "e", "e_down", "e", "e", "e"],
                         [{"i": 3, "dz": -1, "kind": "ramp", "step": "e_down", "x": 5, "y": 1, "z": 0}])

    def test_up_ramp_route(self):
        got = nav(MAP_RAMP, (8, 1, 0), "route", 1, 1, 1)
        self.assertRoute(got, ["w", "w", "w", "w_up", "w", "w", "w"],
                         [{"i": 3, "dz": 1, "kind": "ramp", "step": "w_up", "x": 4, "y": 1, "z": 1}])

    def test_ramp_without_a_solid_neighbour_is_unusable(self):
        flat = World([["##########", "#..../....", "##########"],    # (4,1,0) é chão
                      ["##########", "#....^    ", "##########"]])
        self.assertFalse(nav(flat, (8, 1, 0), "route", 1, 1, 1)["ok"])

    def test_ramp_with_a_floor_built_above_is_unusable(self):
        roofed = World([["##########", "#####/....", "##########"],
                        ["##########", "#.....    ", "##########"]])  # (5,1,1) é chão
        self.assertFalse(nav(roofed, (8, 1, 0), "route", 1, 1, 1)["ok"])

    def test_semi_molten_rock_is_not_a_ramp_support(self):
        lava = World([["##########", "####L/....", "##########"],
                      ["##########", "#....^    ", "##########"]])
        self.assertFalse(nav(lava, (8, 1, 0), "route", 1, 1, 1)["ok"])

    def test_fortification_is_a_ramp_support(self):
        fort = World([["##########", "####F/....", "##########"],
                      ["##########", "#....^    ", "##########"]])
        got = nav(fort, (8, 1, 0), "route", 1, 1, 1)
        self.assertTrue(got["ok"], got)
        self.assertIn("w_up", got["steps"])

    def test_diagonal_ramp_uses_the_diagonal_tokens(self):
        """R=(1,2,0) só se liga ao topo da parede NE (2,1,0) → (2,1,1): "ne_up"/"sw_down"."""
        diag = World([["#####", "###..", "#/...", "#####"],
                      ["#####", "##...", "#^###", "#####"]])
        up = nav(diag, (3, 2, 0), "route", 4, 1, 1)
        self.assertRoute(up, ["w", "w", "ne_up", "e", "e"],
                         [{"i": 2, "dz": 1, "kind": "ramp", "step": "ne_up", "x": 2, "y": 1, "z": 1}])
        down = nav(diag, (4, 1, 1), "route", 3, 2, 0)
        self.assertRoute(down, ["w", "w", "sw_down", "e", "e"],
                         [{"i": 2, "dz": -1, "kind": "ramp", "step": "sw_down", "x": 1, "y": 2, "z": 0}])

    def test_combo_stairs_then_ramp(self):
        planar = nav(MAP_COMBO, (1, 1, 0), "route", 10, 1, 0)       # tz == sz → BFS planar (v1)
        self.assertFalse(planar["ok"])
        self.assertNotIn("mode", planar)
        full = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0)
        self.assertRoute(full, ["e", "e", "up", "e", "e", "e", "e_down", "e", "e", "e"],
                         [{"i": 2, "dz": 1, "kind": "stair", "step": "up", "x": 3, "y": 1, "z": 1},
                          {"i": 6, "dz": -1, "kind": "ramp", "step": "e_down", "x": 7, "y": 1, "z": 0}])

    # ----- despacho / mesmo z
    def test_route_with_same_z_stays_planar_but_route3d_can_detour(self):
        planar = nav(MAP_DETOUR, (1, 1, 0), "route", 9, 1, 0)
        self.assertFalse(planar["ok"])                      # parede em x=5
        self.assertNotIn("mode", planar)                    # saída da v1
        detour = nav(MAP_DETOUR, (1, 1, 0), "route3d", 9, 1, 0)
        self.assertRoute(detour, ["e", "e", "up", "e", "e", "e", "e", "down", "e", "e"],
                         [{"i": 2, "dz": 1, "kind": "stair", "step": "up", "x": 3, "y": 1, "z": 1},
                          {"i": 7, "dz": -1, "kind": "stair", "step": "down", "x": 7, "y": 1, "z": 0}])

    def test_route3d_same_z_matches_planar_length(self):
        a = nav(MAP_STAIRS, (1, 1, 0), "route", 7, 1, 0)
        b = nav(MAP_STAIRS, (1, 1, 0), "route3d", 7, 1, 0)
        self.assertEqual(a["steps"], b["steps"])
        self.assertEqual(b["z_changes"], [])
        self.assertEqual(b["mode"], "3d")

    def test_start_equals_target_is_an_empty_route(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route3d", 1, 1, 0)
        self.assertRoute(got, [])

    def test_deterministic(self):
        a = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0)
        b = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0)
        self.assertEqual(a, b)

    def test_stairs_and_ramps_work_even_if_attrs_do_not_mark_them_walkable(self):
        """Robustez: o build do DFHack pode não marcar escadas/rampas como `walkable`."""
        got = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0, walkable_attrs=["FLOOR"])
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["len"], 10)

    # ----- falhas
    def test_unreachable_target_without_groups_is_no_path(self):
        isolated = World([["#####", "#.#.#", "#####"]])
        got = nav(isolated, (1, 1, 0), "route3d", 3, 1, 0)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "no_path")
        self.assertEqual(got["mode"], "3d")
        self.assertIn("hint", got)
        self.assertEqual(got["explored"], 1)               # só a própria origem

    def test_unreachable_target_with_groups_stops_before_the_bfs(self):
        isolated = World([["#####", "#.#.#", "#####"]])
        got = nav(isolated, (1, 1, 0), "route3d", 3, 1, 0, groups=isolated.groups_table())
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "different_walkable_group")
        self.assertEqual(got["explored"], 0)
        self.assertNotEqual(got["group_from"], got["group_to"])

    def test_target_in_a_wall_unloaded_or_out_of_radius(self):
        wall = nav(MAP_STAIRS, (1, 1, 0), "route3d", 4, 0, 0)
        self.assertEqual(wall["reason"], "target_not_walkable")
        gone = nav(MAP_STAIRS, (1, 1, 0), "route3d", 99, 99, 0)
        self.assertEqual(gone["reason"], "target_unloaded")
        above = nav(MAP_STAIRS, (1, 1, 0), "route", 4, 1, 9)
        self.assertEqual(above["reason"], "target_unloaded")
        corridor = World([["#" * 91, "." * 91, "#" * 91]])
        far = nav(corridor, (0, 1, 0), "route3d", 71, 1, 0)
        self.assertEqual(far["reason"], "target_out_of_radius")
        edge = nav(corridor, (0, 1, 0), "route3d", 70, 1, 0)
        self.assertTrue(edge["ok"], edge)
        self.assertEqual(edge["len"], 70)

    def test_node_cap(self):
        big = World([["." * 30] * 30])
        got = nav(big, (0, 0, 0), "route3d", 29, 29, 0, 5)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "node_cap")
        self.assertEqual(got["explored"], 5)
        full = nav(big, (0, 0, 0), "route3d", 29, 29, 0)
        self.assertTrue(full["ok"])
        self.assertEqual(full["len"], 29)                  # diagonal pura

    def test_search_is_bounded_on_a_big_open_map(self):
        """Alvo sem ligação num mundo enorme: a busca pára no teto de nós (tempo e
        memória limitados) em vez de varrer os 2 x 141^2 tiles do raio."""
        floor0, floor1 = ["." * 150] * 150, ["." * 150] * 150
        floor0[75] = "." * 75 + "<" + "." * 74
        floor1[75] = "." * 75 + ">" + "." * 74
        arena = World([floor0, floor1, ["." * 150] * 150])      # z=2 sem ligação
        got = nav(arena, (75, 75, 0), "route3d", 70, 70, 2)
        self.assertFalse(got["ok"])
        self.assertEqual(got["reason"], "node_cap")
        self.assertEqual(got["explored"], 25000)

    def test_bad_coordinates_and_usage_are_qerrors(self):
        for argv in (["route", "a", "2", "3"], ["route", "1", "2"], ["route", "1.5", "2", "0"],
                     ["route3d", "1", "", "0"], ["reach", "1", "2", "x"], ["route", "1e3", "2", "0"],
                     ["route", "0x10", "2", "0"], ["route", "1234567890", "2", "0"], ["bogus", "1", "2", "3"],
                     [], ["route", "-", "2", "0"]):
            got = nav(MAP_STAIRS, (1, 1, 0), *argv)
            self.assertIn("__qerror__", got, argv)
        self.assertEqual(nav(MAP_STAIRS, (1, 1, 0), "route", "x", 1, 0)["__qerror__"],
                         "coordenadas inválidas")

    def test_invalid_node_cap_falls_back_to_the_default(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route3d", 7, 1, 1, "abc")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["node_cap"], 25000)

    def test_node_cap_argument_is_clamped_to_the_hard_ceiling(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route3d", 7, 1, 1, 999999999)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["node_cap"], 200000)
        self.assertEqual(nav(MAP_STAIRS, (1, 1, 0), "route3d", 7, 1, 1, 50)["node_cap"], 50)

    def test_negative_target_is_unloaded_not_a_crash(self):
        got = nav(MAP_STAIRS, (1, 1, 0), "route3d", -3, -4, 0)
        self.assertEqual(got["reason"], "target_unloaded")

    def test_group_pruning_never_enters_a_tile_the_game_puts_in_another_group(self):
        arena = World([["#######", "#.....#", "#.###.#", "#.....#", "#######"]])
        groups = arena.groups_table()
        plain = nav(arena, (1, 1, 0), "route3d", 5, 1, 0, groups=groups)
        self.assertEqual(plain["len"], 4)                  # a direito pelo corredor de cima
        groups["3,1,0"] = 999                              # o DF põe (3,1) noutro grupo
        pruned = nav(arena, (1, 1, 0), "route3d", 5, 1, 0, groups=groups)
        self.assertTrue(pruned["ok"], pruned)
        self.assertEqual(pruned["len"], 6)                 # contorna por baixo
        self.assertNotIn((3, 1, 0), arena.trace((1, 1, 0), pruned["steps"]))
        groups["3,1,0"] = 0                                # grupo 0 NÃO poda (predicado da v1)
        zero = nav(arena, (1, 1, 0), "route3d", 5, 1, 0, groups=groups)
        self.assertEqual(zero["len"], 4)
        # sem a API de grupos o resultado é o do predicado (a direito)
        self.assertEqual(nav(arena, (1, 1, 0), "route3d", 5, 1, 0)["len"], 4)

    def test_walkable_groups_prune_the_search(self):
        """Origem e alvo no mesmo grupo: a rota sai igual com e sem a API."""
        plain = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0)
        pruned = nav(MAP_COMBO, (1, 1, 0), "route3d", 10, 1, 0, groups=MAP_COMBO.groups_table())
        self.assertEqual(plain["steps"], pruned["steps"])
        self.assertLessEqual(pruned["explored"], plain["explored"])


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class ReachTests(unittest.TestCase):
    def test_reach_uses_walkable_groups_when_the_api_exists(self):
        got = nav(MAP_COMBO, (1, 1, 0), "reach", 10, 1, 0, groups=MAP_COMBO.groups_table())
        self.assertEqual((got["ok"], got["reachable"], got["method"]), (True, True, "walkable_group"))
        self.assertEqual(got["group_from"], got["group_to"])
        self.assertTrue(got["within_radius"])
        self.assertEqual(got["dz"], 0)

    def test_reach_says_no_for_different_groups(self):
        isolated = World([["#####", "#.#.#", "#####"]])
        got = nav(isolated, (1, 1, 0), "reach", 3, 1, 0, groups=isolated.groups_table())
        self.assertEqual((got["ok"], got["reachable"], got["method"]), (True, False, "walkable_group"))
        self.assertNotEqual(got["group_from"], got["group_to"])

    def test_reach_falls_back_to_the_bfs_without_the_api(self):
        yes = nav(MAP_COMBO, (1, 1, 0), "reach", 10, 1, 0)
        self.assertEqual((yes["ok"], yes["reachable"], yes["method"]), (True, True, "bfs"))
        self.assertEqual(yes["len"], 10)
        no = nav(MAP_DETOUR, (1, 1, 0), "reach", 9, 1, 5)
        self.assertEqual((no["ok"], no["reachable"], no["method"]), (True, False, "bfs"))
        self.assertEqual(no["reason"], "target_unloaded")

    def test_reach_ignores_groups_equal_to_zero(self):
        groups = MAP_COMBO.groups_table()
        groups.pop("1,1,0")                                 # origem sem grupo (0)
        got = nav(MAP_COMBO, (1, 1, 0), "reach", 10, 1, 0, groups=groups)
        self.assertEqual(got["method"], "bfs")
        self.assertTrue(got["reachable"])

    def test_reach_reports_distance_and_radius(self):
        corridor = World([["#" * 91, "." * 91, "#" * 91]])
        got = nav(corridor, (0, 1, 0), "reach", 85, 1, 0, groups=corridor.groups_table())
        self.assertTrue(got["reachable"])
        self.assertFalse(got["within_radius"])              # route3d não chega lá
        self.assertEqual(got["distance"], 85)


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class OracleTests(unittest.TestCase):
    """Mapas aleatórios com semente: o Lua tem de concordar com o grafo do oráculo."""

    def check_world(self, seed, with_groups, walkable_attrs=None):
        world = random_world(seed)
        tiles = world.walkable_tiles()
        if len(tiles) < 2:
            return 0
        rng = random.Random(seed * 7919 + 3)
        pairs = [(rng.choice(tiles), rng.choice(tiles)) for _ in range(10)]
        groups = world.groups_table() if with_groups else None
        results = run_nav(world, [(s, ["route3d", t[0], t[1], t[2]]) for s, t in pairs],
                          groups=groups, walkable_attrs=walkable_attrs)
        checked = 0
        for (start, goal), got in zip(pairs, results):
            want = world.shortest(start, goal)
            label = "semente %d groups=%s %s→%s" % (seed, with_groups, start, goal)
            self.assertEqual(got["ok"], want is not None, "%s: %s" % (label, got))
            if want is not None:
                self.assertEqual(got["len"], want, label)
                self.assertEqual(world.replay(start, got["steps"]), goal, label)
                vertical = [t for t in got["steps"] if B.NAV_STEP_VECTORS[t][2]]
                self.assertEqual(len(got["z_changes"]), len(vertical), label)
            checked += 1
        return checked

    def test_random_maps_without_groups(self):
        total = sum(self.check_world(seed, False) for seed in range(30))
        self.assertGreater(total, 100)

    def test_random_maps_with_groups(self):
        total = sum(self.check_world(seed, True) for seed in range(30, 50))
        self.assertGreater(total, 100)

    def test_random_maps_when_attrs_do_not_mark_stairs_and_ramps_walkable(self):
        total = sum(self.check_world(seed, False, walkable_attrs=["FLOOR", "BOULDER", "PEBBLES"])
                    for seed in range(50, 62))
        self.assertGreater(total, 50)

    def test_random_maps_contain_ramps_and_stairs(self):
        """Salvaguarda do gerador: sem ligações em z o teste acima não provaria nada."""
        ramps = stairs = 0
        for seed in range(30):
            world = random_world(seed)
            for token in (t for edges in world.special_edges().values() for t, _ in edges):
                if token in ("up", "down"):
                    stairs += 1
                else:
                    ramps += 1
        self.assertGreater(ramps, 20)
        self.assertGreater(stairs, 5)

    def test_vertical_tokens_all_exist_in_the_bridge_vocabulary(self):
        """O que o oráculo/Lua emitem tem de ser aceite pela ponte (vetores e teclas)."""
        tokens = {name for name, _dx, _dy in DIRS} | {"up", "down"}
        tokens |= {n + "_up" for n, _x, _y in DIRS} | {n + "_down" for n, _x, _y in DIRS}
        self.assertEqual(tokens, set(B.NAV_STEP_VECTORS))
        slope = tokens - {name for name, _x, _y in DIRS} - {"up", "down"}
        self.assertEqual(slope, set(B.NAV_SLOPE_KEYS))
        for token, key in B.NAV_SLOPE_KEYS.items():
            self.assertEqual(key, "A_MOVE_" + token.upper())
            self.assertRegex(key, B._KEY_SHAPE)


# ====================================================================================
# 2. df_bridge.py — DSL
# ====================================================================================
class DslParseTests(unittest.TestCase):
    def test_two_integers_stay_a_planar_goto(self):
        self.assertEqual(B.parse_batch("goto 88,72"), [{"op": "goto", "x": 88, "y": 72}])
        self.assertEqual(B.parse_batch("goto 88 72"), [{"op": "goto", "x": 88, "y": 72}])
        self.assertEqual(B.parse_batch("goto 88,72 maxsteps=9"),
                         [{"op": "goto", "x": 88, "y": 72, "maxsteps": 9}])

    def test_three_integers_are_a_3d_goto(self):
        self.assertEqual(B.parse_batch("goto 88,72,5"),
                         [{"op": "goto", "x": 88, "y": 72, "z": 5}])
        self.assertEqual(B.parse_batch("goto 88 72 5"),
                         [{"op": "goto", "x": 88, "y": 72, "z": 5}])
        self.assertEqual(B.parse_batch("GOTO 88, 72, 5"),
                         [{"op": "goto", "x": 88, "y": 72, "z": 5}])
        self.assertEqual(B.parse_batch("goto 88,72,5 maxsteps=40"),
                         [{"op": "goto", "x": 88, "y": 72, "z": 5, "maxsteps": 40}])
        self.assertEqual(B.parse_batch("goto 88,72,0")[0]["z"], 0)

    def test_batch_mixes_planar_and_3d(self):
        steps = B.parse_batch("goto 1,2; goto 3,4,5; move n times=2; read screen")
        self.assertEqual([s["op"] for s in steps], ["goto", "goto", "move", "read"])
        self.assertNotIn("z", steps[0])
        self.assertEqual(steps[1]["z"], 5)

    def test_malformed_goto_forms_are_rejected(self):
        for bad in ("goto", "goto 1", "goto 1,2,x", "goto 1,2,3,4", "goto 1.5,2,3",
                    "goto 1,2,3.5", "goto a,b", "goto 1,2 fast",
                    "goto 1,2,3 fast", "goto 1,2 maxsteps=x", "goto 1,2,3 maxsteps=",
                    "goto 1_0,2", "goto 1,2,1_0", "goto 1,2,-1", "goto 1,2,1001",
                    "goto 1,2 3 4", "goto 1,2 maxsteps=5 3"):
            with self.assertRaises(B.BridgeError, msg=bad) as cm:
                B.parse_batch(bad)
            self.assertEqual(cm.exception.exit_code, B.EXIT_CMD_FAILED)

    def test_json_form_accepts_z_and_rejects_garbage(self):
        ok = B.parse_batch('[{"op":"goto","x":1,"y":2,"z":3},{"op":"goto","x":4,"y":5}]')
        self.assertEqual(ok[0], {"op": "goto", "x": 1, "y": 2, "z": 3})
        self.assertEqual(ok[1], {"op": "goto", "x": 4, "y": 5})
        self.assertEqual(B.parse_batch('[{"op":"goto","x":1,"y":2,"z":null}]')[0],
                         {"op": "goto", "x": 1, "y": 2})
        self.assertEqual(B.parse_batch('[{"op":"goto","x":"1","y":2.0,"z":"3"}]')[0],
                         {"op": "goto", "x": 1, "y": 2, "z": 3})
        for bad in ('[{"op":"goto","x":1,"y":2,"z":"a"}]', '[{"op":"goto","x":1,"y":2,"z":true}]',
                    '[{"op":"goto","x":1,"y":2,"z":1.5}]', '[{"op":"goto","x":1,"y":2,"z":-4}]',
                    '[{"op":"goto","x":1}]', '[{"op":"goto","x":null,"y":2}]',
                    '[{"op":"goto","x":1,"y":[2]}]', '[{"op":"goto","x":1,"y":2,"maxsteps":"x"}]'):
            with self.assertRaises(B.BridgeError, msg=bad):          # BridgeError, nunca KeyError
                B.parse_batch(bad)

    def test_existing_forms_still_parse(self):
        steps = B.parse_batch("goto 88,72; move n times=3; key A_TALK; click 26,9 right; "
                              "wait short; read screen; expect focus Conversation; sleep 100")
        self.assertEqual([s["op"] for s in steps],
                         ["goto", "move", "key", "click", "wait", "read", "expect", "sleep"])
        self.assertEqual(steps[0], {"op": "goto", "x": 88, "y": 72})

    def test_dry_run_echoes_the_3d_step_and_presses_nothing(self):
        srv = TB.FakeDFHack(TB.scripted_responder())
        srv.start()
        self.addCleanup(srv.stop)
        args = TB.make_args(action="batch", json=True, dry_run=True, port=srv.port,
                            args=["goto 88,72,5; goto 1,2"])
        rc, out = TB.capture(B.cmd_act_batch, args)
        self.assertEqual(rc, B.EXIT_OK)
        report = json.loads(out)
        self.assertEqual(report["steps"][0]["would"], {"op": "goto", "x": 88, "y": 72, "z": 5})
        self.assertEqual(report["steps"][1]["would"], {"op": "goto", "x": 1, "y": 2})
        self.assertEqual(srv.requests, [])

    def test_as_int_is_strict(self):
        self.assertEqual((B._as_int(5), B._as_int(5.0), B._as_int(-2)), (5, 5, -2))
        for bad in (True, False, None, 5.5, "5", [5], {"x": 5}, float("nan"), float("inf")):
            self.assertIsNone(B._as_int(bad), bad)

    def test_parse_goto_args_for_the_cli(self):
        self.assertEqual(B._parse_goto_args(["88", "72"]), (88, 72, None))
        self.assertEqual(B._parse_goto_args(["88", "72", "5"]), (88, 72, 5))
        self.assertEqual(B._parse_goto_args(["88,72,5"]), (88, 72, 5))
        for bad in ([], ["1"], ["1", "2", "3", "4"], ["1", "x"], ["1.5", "2"], ["1", "2", "-1"],
                    ["1", "2", "1001"]):
            with self.assertRaises(B.BridgeError, msg=bad):
                B._parse_goto_args(bad)


# ====================================================================================
# 3. df_bridge.py — _goto3d contra o servidor DFHack falso + simulador de jogo
# ====================================================================================
class GameSim:
    """Jogo simulado atrás do servidor falso: aplica as regras do `World` a cada tecla.

    `routes` (dict "route3d"/"route"/"reach" → payload) substitui o dfb-nav REAL por
    respostas fixas (testes sem Lua); sem `routes` o `dfb-nav.lua` corre de verdade."""

    def __init__(self, world, pos, groups=None, focus=("dungeonmode", "Default"),
                 blocked=(), drift=None, routes=None, slope_mode="diag", stair_mode="move"):
        # slope_mode: "diag" = só Shift/Ctrl+direção sobe/desce rampas; "walk" = só andar
        # na direção sobe; "both"; "none" = o jogo ignora todas as teclas de rampa.
        # stair_mode: "move" = A_MOVE_UP/DOWN e `<`/`>` (_AUX) funcionam; "aux" = só _AUX;
        # "none" = o jogo ignora as teclas de escada
        self.slope_mode, self.stair_mode = slope_mode, stair_mode
        self.world, self.pos = world, tuple(pos)
        self.groups, self.focus = groups, list(focus)
        self.blocked = set(blocked)               # tokens que o "jogo" ignora (NPC no caminho)
        self.drift = dict(drift or {})            # token → destino absoluto, 1 vez (desvio)
        self.routes = routes
        self._calls = {}
        self.log = []

    def presses(self):
        """[(comando, args...)] só das teclas/movimentos enviados ao jogo."""
        return [(c,) + tuple(a) for c, a in self.log
                if c == "dfb-act" and a and a[0] in ("move", "key")]

    def _apply(self, token):
        if token in self.drift:
            self.pos = tuple(self.drift.pop(token))
            return
        if token in self.blocked:
            return
        for name, dest in self.world.moves(self.pos):
            if name == token:
                self.pos = dest
                return

    def _press_key(self, key):
        if not key.startswith("A_MOVE_"):
            return
        name = key[len("A_MOVE_"):].lower()                  # "ne_up", "e", "up", ...
        if name.endswith("_up") or name.endswith("_down"):
            if self.slope_mode in ("diag", "both"):
                self._apply(name)
        elif name in ("up", "down"):
            if self.stair_mode == "move":
                self._apply(name)                            # escadas (A_MOVE_UP/DOWN)
        elif name in ("up_aux", "down_aux"):
            if self.stair_mode in ("move", "aux"):
                self._apply(name[:-4])                       # escadas (`<` / `>`)
        else:                                                # andar na direção
            before = self.pos
            self._apply(name)
            if self.pos == before and self.slope_mode in ("walk", "both"):
                self._apply(name + "_up")                    # a rampa sobe ao andar

    def _pos_json(self):
        return {"x": self.pos[0], "y": self.pos[1], "z": self.pos[2]}

    def respond(self, command, args):
        self.log.append((command, tuple(args)))
        sub = args[0] if args else ""
        if command == "dfb-state":
            if sub == "adventurer":
                return json.dumps({"pos": self._pos_json()}), 0
            if sub == "threats":
                return json.dumps({"radius": 10, "threats": []}), 0
            return json.dumps({"adventurer": {"pos": self._pos_json()}}), 0
        if command == "dfb-act":
            if sub == "focus":
                return json.dumps({"focus": self.focus}), 0
            if sub == "move":
                if args[1] not in B.MOVE_DIRS:
                    return "", 1
                before = self._pos_json()
                if args[1] not in ("up", "down") or self.stair_mode == "move":
                    self._apply(args[1])
                return json.dumps({"ok": True, "dir": args[1], "pos_before": before,
                                   "focus": self.focus}), 0
            if sub == "key":
                for key in args[1:]:
                    self._press_key(key)
                return json.dumps({"ok": True, "pressed": list(args[1:]),
                                   "focus": self.focus}), 0
        if command == "dfb-nav":
            if self.routes is not None:
                payload = self.routes.get(sub)
                if isinstance(payload, tuple):               # sequência: 1 resposta por pedido
                    i = self._calls.get(sub, 0)              # (a última repete-se)
                    self._calls[sub] = i + 1
                    payload = payload[min(i, len(payload) - 1)]
                return json.dumps(payload), 0
            out = run_nav(self.world, [(self.pos, list(args))], groups=self.groups)[0]
            if "__qerror__" in out:
                return out["__qerror__"], 1
            return json.dumps(out), 0
        return "ok", 0


class Bridge3DBase(unittest.TestCase):
    def serve(self, sim):
        srv = TB.FakeDFHack(sim.respond)
        srv.start()
        self.addCleanup(srv.stop)
        return srv

    def args(self, srv, **kw):
        defaults = dict(df_path=DF, port=srv.port, delay=0.0, max_steps=60, json=True)
        defaults.update(kw)
        return TB.make_args(**defaults)


class Goto3DCannedTests(Bridge3DBase):
    """`_goto3d` com o dfb-nav FALSO (respostas fixas): corre mesmo sem Lua."""

    def test_runs_stairs_and_ramp_steps_and_verifies_each_position(self):
        route = {"ok": True, "mode": "3d", "len": 10,
                 "steps": ["e", "e", "up", "e", "e", "e", "e_down", "e", "e", "e"]}
        sim = GameSim(MAP_COMBO, (1, 1, 0), routes={"route3d": route})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 10, 1, 0, 60)
        self.assertTrue(data["ok"], data)
        self.assertTrue(data["arrived"])
        self.assertEqual(data["mode"], "3d")
        self.assertEqual(data["z_steps"], 2)
        self.assertEqual(data["pos"], {"x": 9, "y": 1, "z": 0})     # a ≤ 1 tile: chegou
        self.assertEqual(data["steps"], 9)
        self.assertEqual(data["z_keys"], {"up": "A_MOVE_UP", "e_down": "A_MOVE_E_DOWN"})
        # up/down e planares por `dfb-act move`; a rampa por `dfb-act key` (+ guarda de foco)
        presses = sim.presses()
        self.assertEqual(presses[2], ("dfb-act", "move", "up"))
        self.assertIn(("dfb-act", "key", "A_MOVE_E_DOWN"), presses)
        order = [(c, a[0]) for c, a in sim.log if c == "dfb-act"]
        self.assertEqual(order[order.index(("dfb-act", "focus")) + 1], ("dfb-act", "key"))

    def test_no_route_and_other_z_reports_failure_and_presses_nothing(self):
        routes = {"route3d": {"ok": False, "mode": "3d", "reason": "no_path", "hint": "x"}}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes=routes)
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertFalse(data["ok"])
        self.assertIn("sem rota 3D", data["error"])
        self.assertEqual(data["detail"]["reason"], "no_path")
        self.assertEqual(data["steps"], 0)
        self.assertEqual(sim.presses(), [])                  # NUNCA mexe sem rota provada
        self.assertFalse(any(a[0] == "route" for c, a in sim.log if c == "dfb-nav"))  # sem planar

    def test_no_route_at_the_same_z_falls_back_to_planar(self):
        routes = {"route3d": {"ok": False, "reason": "no_path"},
                  "route": {"ok": True, "len": 2, "steps": ["e", "e"]}}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes=routes)
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 4, 1, 0, 60)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["mode"], "planar-fallback")
        self.assertEqual(data["detail3d"]["reason"], "no_path")
        calls = [a[0] for c, a in sim.log if c == "dfb-nav"]
        self.assertEqual(calls, ["route3d", "route"])

    def test_malformed_route_is_a_failure_without_pressing(self):
        for bad in ({"ok": True, "steps": ["e", "teleport"]}, {"ok": True, "steps": "e"},
                    {"ok": True}, {"ok": True, "steps": [1, 2]}, ["not", "a", "dict"]):
            sim = GameSim(MAP_STAIRS, (1, 1, 0), routes={"route3d": bad})
            srv = self.serve(sim)
            data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
            self.assertFalse(data["ok"], bad)
            self.assertEqual(sim.presses(), [], bad)

    def test_blocked_steps_give_up_after_five_dead_routes(self):
        route = {"ok": True, "steps": ["e", "e", "e", "up", "e", "e", "e"]}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), blocked={"e"}, routes={"route3d": route})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertFalse(data["ok"])
        self.assertIn("preso", data["error"])
        self.assertEqual(data["steps"], B.NAV_STUCK_ROUTES)  # 1 tecla por rota morta
        self.assertEqual(data["pos"], {"x": 1, "y": 1, "z": 0})

    def test_deviation_stops_the_stale_route_and_recomputes(self):
        first = {"ok": True, "steps": ["e", "e", "e", "up", "e", "e", "e"]}
        second = {"ok": True, "steps": ["e", "up", "e", "e", "e"]}
        # o 1.º `e` devia dar (2,1,0) mas o "jogo" desvia-o para (3,1,0): a rota antiga
        # deixa de valer e a ponte tem de pedir outra a partir da posição real
        sim = GameSim(MAP_STAIRS, (1, 1, 0), drift={"e": (3, 1, 0)},
                      routes={"route3d": (first, second)})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["recomputes"], 2)             # rota 1 (desvio) + rota 2 (chegou)
        nav_calls = [a for c, a in sim.log if c == "dfb-nav"]
        self.assertEqual(len(nav_calls), 2)

    def test_budget_exhausted_is_reported(self):
        route = {"ok": True, "steps": ["e", "e", "e", "up", "e", "e", "e"]}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes={"route3d": route})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 2)
        self.assertFalse(data["ok"])
        self.assertEqual(data["steps"], 2)
        self.assertIn("orçamento", data["note"])
        self.assertEqual(data["pos"], {"x": 3, "y": 1, "z": 0})

    def stairs_run(self, **sim_kw):
        route = {"ok": True, "steps": ["e", "e", "e", "up", "e", "e", "e"]}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes={"route3d": route}, **sim_kw)
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        return data, [p[1:] for p in sim.presses() if p[2] in ("up", "A_MOVE_UP_AUX")]

    def test_stairs_use_a_move_up_first(self):
        data, presses = self.stairs_run()
        self.assertTrue(data["ok"], data)
        self.assertEqual(presses, [("move", "up")])
        self.assertEqual(data["z_keys"], {"up": "A_MOVE_UP"})

    def test_stairs_fall_back_to_the_aux_key_when_move_up_does_nothing(self):
        data, presses = self.stairs_run(stair_mode="aux")
        self.assertTrue(data["ok"], data)
        self.assertEqual(presses, [("move", "up"), ("key", "A_MOVE_UP_AUX")])
        self.assertEqual(data["z_keys"], {"up": "A_MOVE_UP_AUX"})
        self.assertEqual(data["z_steps"], 1)

    def test_stairs_aux_fallback_can_be_disabled(self):
        with mock.patch.object(B, "NAV_STAIR_AUX_FALLBACK", False):
            data, presses = self.stairs_run(stair_mode="aux")
        self.assertFalse(data["ok"])
        self.assertEqual({p for p in presses}, {("move", "up")})
        self.assertIn("preso", data["error"])

    def test_stairs_down_use_the_matching_aux_key(self):
        route = {"ok": True, "steps": ["w", "w", "w", "down", "w", "w", "w"]}
        sim = GameSim(MAP_STAIRS, (7, 1, 1), stair_mode="aux", routes={"route3d": route})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 1, 1, 0, 60)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["z_keys"], {"down": "A_MOVE_DOWN_AUX"})
        self.assertIn(("dfb-act", "key", "A_MOVE_DOWN_AUX"), sim.presses())

    def slope_up_run(self, **sim_kw):
        route = {"ok": True, "steps": ["w", "w", "w", "w_up", "w", "w", "w"]}
        sim = GameSim(MAP_RAMP, (8, 1, 0), routes={"route3d": route}, **sim_kw)
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 1, 1, 1, 60)
        keys = [p[2] for p in sim.presses() if p[1] == "key"]
        return data, keys

    def test_slope_up_uses_the_documented_key_first(self):
        data, keys = self.slope_up_run(slope_mode="diag")
        self.assertTrue(data["ok"], data)
        self.assertEqual(keys, ["A_MOVE_W_UP"])
        self.assertEqual(data["z_keys"], {"w_up": "A_MOVE_W_UP"})
        self.assertEqual(data["steps"], 6)                       # 3 + rampa (1 tecla) + 2

    def test_slope_up_falls_back_to_walking_when_shift_direction_does_nothing(self):
        data, keys = self.slope_up_run(slope_mode="walk")
        self.assertTrue(data["ok"], data)
        self.assertEqual(keys, ["A_MOVE_W_UP", "A_MOVE_W"])      # 2.ª tentativa mexeu
        self.assertEqual(data["z_keys"], {"w_up": "A_MOVE_W"})
        self.assertEqual(data["steps"], 7)                       # o orçamento conta TECLAS
        self.assertEqual(data["pos"]["z"], 1)

    def test_slope_up_fallbacks_can_be_disabled(self):
        with mock.patch.object(B, "NAV_SLOPE_UP_FALLBACKS", False):
            data, keys = self.slope_up_run(slope_mode="walk")
        self.assertFalse(data["ok"])
        self.assertEqual(set(keys), {"A_MOVE_W_UP"})             # só a documentada
        self.assertIn("preso", data["error"])

    def test_slope_up_tries_every_key_before_giving_up(self):
        data, keys = self.slope_up_run(slope_mode="none")
        self.assertFalse(data["ok"])
        self.assertIn("preso", data["error"])
        self.assertEqual(keys[:3], ["A_MOVE_W_UP", "A_MOVE_W", "A_MOVE_UP"])
        self.assertEqual(data["z_keys"], {})
        self.assertEqual(data["pos"], {"x": 5, "y": 1, "z": 0})  # nunca saiu da rampa

    def slope_down_run(self, **sim_kw):
        route = {"ok": True, "steps": ["e", "e", "e", "e_down", "e", "e", "e"]}
        sim = GameSim(MAP_RAMP, (1, 1, 1), routes={"route3d": route}, **sim_kw)
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 8, 1, 0, 60)
        keys = [p[2] for p in sim.presses() if p[1] == "key"]
        return data, keys

    def test_slope_down_never_walks_off_a_ledge_by_default(self):
        data, keys = self.slope_down_run(slope_mode="walk")
        self.assertFalse(data["ok"])
        self.assertEqual(set(keys), {"A_MOVE_E_DOWN"})           # nunca A_MOVE_E para o ar
        self.assertEqual(data["pos"], {"x": 4, "y": 1, "z": 1})  # parado no rebordo

    def test_slope_down_walk_fallback_is_opt_in(self):
        with mock.patch.object(B, "NAV_SLOPE_DOWN_WALK", True):
            data, keys = self.slope_down_run(slope_mode="both")
        self.assertTrue(data["ok"], data)

    def test_menu_focus_blocks_slope_keys(self):
        route = {"ok": True, "steps": ["e_down"]}
        sim = GameSim(MAP_RAMP, (4, 1, 1), focus=("dungeonmode", "Conversation"),
                      routes={"route3d": route})
        srv = self.serve(sim)
        with self.assertRaises(B.BridgeError) as cm:
            B._goto3d(self.args(srv), DF, 8, 1, 0, 60)
        self.assertIn("não está no mapa", str(cm.exception))
        self.assertEqual([p for p in sim.presses() if p[1] == "key"], [])   # nenhuma tecla

    def test_already_there_does_not_ask_for_a_route(self):
        sim = GameSim(MAP_STAIRS, (6, 1, 1), routes={"route3d": {"ok": False}})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertTrue(data["ok"])
        self.assertEqual(data["steps"], 0)
        self.assertFalse(any(c == "dfb-nav" for c, _a in sim.log))

    def test_same_xy_other_z_is_not_arrival(self):
        """Adjacente em x,y mas em outro z NÃO conta como chegada (o planar ignora z)."""
        sim = GameSim(MAP_STAIRS, (7, 1, 0),
                      routes={"route3d": {"ok": False, "reason": "no_path"}})
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertFalse(data["ok"])
        self.assertIn("error", data)

    def test_unreadable_position_is_a_bridge_error(self):
        srv = TB.FakeDFHack(lambda c, a: (json.dumps({"pos": {"x": 1, "y": None}}), 0)
                            if c == "dfb-state" else ("{}", 0))
        srv.start()
        self.addCleanup(srv.stop)
        with self.assertRaises(B.BridgeError) as cm:
            B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertEqual(cm.exception.exit_code, B.EXIT_CMD_FAILED)


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class Goto3DEndToEndTests(Bridge3DBase):
    """Ponte Python ↔ dfb-nav.lua REAL ↔ mapa simulado (sem canned)."""

    def test_combo_route_reaches_the_target_through_stairs_and_ramp(self):
        sim = GameSim(MAP_COMBO, (1, 1, 0), groups=MAP_COMBO.groups_table())
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 10, 1, 0, 60)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["z_steps"], 2)
        self.assertEqual(data["recomputes"], 1)
        keys = [p[2] for p in sim.presses() if p[1] == "key"]
        self.assertEqual(keys, ["A_MOVE_E_DOWN"])

    def test_up_ramp_end_to_end(self):
        sim = GameSim(MAP_RAMP, (8, 1, 0))
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 1, 1, 1, 60)
        self.assertTrue(data["ok"], data)
        keys = [p[2] for p in sim.presses() if p[1] == "key"]
        self.assertEqual(keys, ["A_MOVE_W_UP"])
        self.assertEqual(data["pos"]["z"], 1)

    def test_unreachable_target_other_z_fails_cleanly(self):
        broken = World([["##########", "#...<....#", "##########"],
                        ["##########", "#........#", "##########"]])
        sim = GameSim(broken, (1, 1, 0))
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 7, 1, 1, 60)
        self.assertFalse(data["ok"])
        self.assertEqual(data["detail"]["reason"], "no_path")
        self.assertEqual(sim.presses(), [])

    def test_same_z_target_that_needs_a_detour_uses_the_3d_route(self):
        sim = GameSim(MAP_DETOUR, (1, 1, 0))
        srv = self.serve(sim)
        data = B._goto3d(self.args(srv), DF, 9, 1, 0, 60)
        self.assertTrue(data["ok"], data)
        self.assertEqual(data["mode"], "3d")
        self.assertEqual(data["z_steps"], 2)

    def test_planar_goto_never_asks_for_route3d(self):
        sim = GameSim(MAP_STAIRS, (1, 1, 0))
        srv = self.serve(sim)
        data = B._goto(self.args(srv), DF, 7, 1, 60)
        self.assertTrue(data["ok"], data)
        subs = [a[0] for c, a in sim.log if c == "dfb-nav"]
        self.assertTrue(subs and set(subs) == {"route"}, subs)

    def test_batch_runs_3d_and_planar_gotos(self):
        sim = GameSim(MAP_COMBO, (1, 1, 0))
        srv = self.serve(sim)
        args = self.args(srv, action="batch", args=["goto 10,1,0; goto 7,1"])
        rc, out = TB.capture(B.cmd_act_batch, args)
        report = json.loads(out)
        first, second = report["steps"][0], report["steps"][1]
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["mode"], "3d")
        self.assertEqual(first["z_steps"], 2)
        self.assertTrue(second["ok"], second)
        self.assertNotIn("mode", second)                    # goto X,Y: planar, como sempre
        subs = [a[0] for c, a in sim.log if c == "dfb-nav"]
        self.assertEqual(subs, ["route3d", "route"])
        self.assertEqual(report["summary"]["move_steps"], first["steps"] + second["steps"])

    def test_batch_failed_3d_goto_is_a_failed_step_not_a_success(self):
        broken = World([["##########", "#...<....#", "##########"],
                        ["##########", "#........#", "##########"]])
        sim = GameSim(broken, (1, 1, 0))
        srv = self.serve(sim)
        args = self.args(srv, action="batch", args=["goto 7,1,1"])
        rc, out = TB.capture(B.cmd_act_batch, args)
        report = json.loads(out)
        step = report["steps"][0]
        self.assertFalse(step["ok"])
        self.assertIn("error", step)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertEqual(report["summary"]["ok"], 0)


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


class CliTests(Bridge3DBase):
    def cli(self, srv, *words):
        df = tempfile.mkdtemp(prefix="dfmz-")
        self.addCleanup(shutil.rmtree, df, True)
        return run_cli(["act", *words, "--df-path", df, "--port", str(srv.port), "--via", "tcp",
                        "--json", "--delay", "0"])

    def test_act_goto_x_y_z_runs_the_3d_route(self):
        route = {"ok": True, "steps": ["e", "e", "e", "up", "e", "e", "e"]}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes={"route3d": route})
        srv = self.serve(sim)
        code, out = self.cli(srv, "goto", "7", "1", "1")
        self.assertEqual(code, B.EXIT_OK, out)
        body = json.loads(out)
        self.assertEqual(body["what"], "goto")
        self.assertEqual(body["data"]["mode"], "3d")
        self.assertTrue(body["data"]["ok"])

    def test_act_goto_x_y_stays_planar(self):
        sim = GameSim(MAP_STAIRS, (1, 1, 0),
                      routes={"route": {"ok": True, "steps": ["e", "e", "e"]}})
        srv = self.serve(sim)
        code, out = self.cli(srv, "goto", "5", "1")
        self.assertEqual(code, B.EXIT_OK, out)
        self.assertNotIn("mode", json.loads(out)["data"])
        self.assertEqual({a[0] for c, a in sim.log if c == "dfb-nav"}, {"route"})

    def test_act_goto_3d_failure_exits_13(self):
        sim = GameSim(MAP_STAIRS, (1, 1, 0),
                      routes={"route3d": {"ok": False, "reason": "no_path"}})
        srv = self.serve(sim)
        code, out = self.cli(srv, "goto", "7", "1", "1")
        self.assertEqual(code, B.EXIT_CMD_FAILED)
        self.assertEqual(sim.presses(), [])

    def test_act_goto_bad_args_are_exit_13_not_a_traceback(self):
        sim = GameSim(MAP_STAIRS, (1, 1, 0))
        srv = self.serve(sim)
        for words in (["goto", "7"], ["goto", "a", "b"], ["goto", "1", "2", "x"],
                      ["goto", "1", "2", "3", "4"], ["goto", "1", "2", "9999"]):
            code, out = self.cli(srv, *words)
            self.assertEqual(code, B.EXIT_CMD_FAILED, words)
            self.assertFalse(json.loads(out)["ok"], words)
        self.assertEqual(sim.presses(), [])

    def test_act_reach_prints_the_probe_verdict(self):
        payload = {"ok": True, "reachable": False, "method": "walkable_group",
                   "group_from": 1, "group_to": 2}
        sim = GameSim(MAP_STAIRS, (1, 1, 0), routes={"reach": payload})
        srv = self.serve(sim)
        code, out = self.cli(srv, "reach", "7", "1", "1")
        self.assertEqual(code, B.EXIT_OK)                    # a sonda correu
        body = json.loads(out)
        self.assertEqual(body["what"], "reach")
        self.assertEqual(body["data"], payload)
        called = [a for c, a in sim.log if c == "dfb-nav"]
        self.assertEqual(called, [("reach", "7", "1", "1")])

    def test_act_reach_requires_three_integers(self):
        sim = GameSim(MAP_STAIRS, (1, 1, 0))
        srv = self.serve(sim)
        code, out = self.cli(srv, "reach", "7", "1")
        self.assertEqual(code, B.EXIT_CMD_FAILED)

    @unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
    def test_act_reach_end_to_end_with_the_real_lua(self):
        sim = GameSim(MAP_COMBO, (1, 1, 0), groups=MAP_COMBO.groups_table())
        srv = self.serve(sim)
        code, out = self.cli(srv, "reach", "10", "1", "0")
        self.assertEqual(code, B.EXIT_OK, out)
        data = json.loads(out)["data"]
        self.assertTrue(data["reachable"])
        self.assertEqual(data["method"], "walkable_group")

    def test_parser_accepts_the_new_action(self):
        parser = B.build_parser()
        ns = parser.parse_args(["act", "reach", "1", "2", "3"])
        self.assertEqual((ns.action, ns.args), ("reach", ["1", "2", "3"]))
        ns = parser.parse_args(["act", "goto", "1", "2", "3", "--max-steps", "30"])
        self.assertEqual((ns.action, ns.args, ns.max_steps), ("goto", ["1", "2", "3"], 30))


if __name__ == "__main__":
    unittest.main(verbosity=2)
