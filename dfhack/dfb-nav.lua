-- dfb-nav.lua — auto-rota do copiloto: BFS no mapa local (8 direções; tiles com
-- edifício, ex.: portas, contam como passáveis). Saída: JSON.
-- Uso:
--   dfb-nav route   <x> <y> <z> [max_nos]  → {ok, steps:["s","se",...], len}
--       z == z do aventureiro → BFS PLANAR (a v1, provada em jogo; saída inalterada)
--       z ~= z do aventureiro → BFS 3D (o mesmo que route3d)
--   dfb-nav route3d <x> <y> <z> [max_nos]  → {ok, mode:"3d", len, steps, z_changes, ...}
--   dfb-nav reach   <x> <y> <z> [max_nos]  → {ok, reachable, method:"walkable_group"|"bfs"}
-- Licença: MIT (dwarf-fortress-agent-skill).
--
-- ---------- multi-z (ARQUITETURA §9 / PROBLEMAS H2) — EXPERIMENTAL: verify live ----------
-- A BFS 3D vem SÓ da documentação (formas de tile da wiki "Ramp"/"Stairs" e os
-- grupos de caminhada do DFHack: dfhack.maps.getWalkableGroup, "Lua API.txt").
-- Nunca foi corrida contra o jogo vivo. Por isso: devolve dados explícitos (ok /
-- reason / hint), nunca "adivinha" e o df_bridge só mexe no personagem com uma
-- rota PROVADA. A BFS planar continua a ser o caminho padrão (route com z igual).
--
-- Passos emitidos em `steps` (cada um = uma tecla = um turno do jogo):
--   n s e w ne nw se sw        planar              → A_MOVE_<DIR>
--   up | down                  escada, mesmo (x,y) → A_MOVE_UP / A_MOVE_DOWN
--   <dir>_up | <dir>_down      rampa, em diagonal  → A_MOVE_<DIR>_UP / A_MOVE_<DIR>_DOWN
--                              (bindings de adventure mode em data/init/interface.txt)
-- `z_changes` = [{i, dz, kind:"stair"|"ramp", step, x, y, z}]: i = índice (0-based) do
-- passo em `steps` que muda o z; (x,y,z) = o tile onde o passo aterra.
-- Falha (route3d/route com z diferente): {ok:false, mode:"3d", reason, hint, explored,
-- node_cap, group_from, group_to}; reason ∈ target_unloaded | target_out_of_radius |
-- target_not_walkable | different_walkable_group | node_cap | no_path | shape_enum_missing.
-- reach: {ok, reachable, method, group_from, group_to, distance, dz, within_radius}
-- (+ reason/len/explored quando method == "bfs"); ok = a sonda correu.
--
-- Regras de transição (fonte: wiki DF "Ramp" e "Stairs"):
--   * escada: STAIR_UP/STAIR_UPDOWN em (x,y,z) liga a STAIR_DOWN/STAIR_UPDOWN em
--     (x,y,z+1) — "up" para cima, "down" no sentido inverso.
--   * rampa: uma RAMP em R liga-se ao TOPO das paredes adjacentes (8 vizinhos):
--       (1) R é RAMP;  (2) o tile logo acima de R é espaço aberto (EMPTY/RAMP_TOP);
--       (3) o vizinho no mesmo z é sólido (WALL/FORTIFICATION; "semi-molten rock" =
--           LAVA_STONE não conta);  (4) o tile acima desse vizinho é caminhável.
--     A ligação é simétrica: subir R → vizinho(z+1), descer vizinho(z+1) → R.
--   * grupos de caminhada: se a API existir, origem e alvo em grupos NÃO nulos e
--     diferentes = o DF não os liga (aborta sem BFS) e nenhum nó fora do grupo da
--     origem entra na busca (as regras acima nunca "inventam" uma ligação).

local C = reqscript('dfb-common')
local json = C.json
C.version_guard()

-- ---------- parâmetros (nomes, nunca números mágicos inline) ----------
local SEARCH_RADIUS = 70          -- raio planar da busca, em tiles (igual à v1)
local NODE_CAP_DEFAULT = 25000    -- nós expandidos por busca (igual à v1)
local NODE_CAP_HARD = 200000      -- teto do argumento max_nos (corre na thread do jogo)
local Z_MARGIN = 8                -- folga em z para lá do intervalo origem..alvo (só 3D)
local USE_WALKABLE_GROUPS = true  -- usa getWalkableGroup (poda a BFS 3D e responde ao reach)

local USAGE = 'uso: dfb-nav route|route3d|reach <x> <y> <z> [max_nos]'
local args = {...}
local cmd = args[1]
if cmd ~= 'route' and cmd ~= 'route3d' and cmd ~= 'reach' then qerror(USAGE) end

-- inteiros decimais curtos: nada de 1.5, 1e3, 0x10 (a ponte só envia ints)
local function parse_int(s)
  if type(s) == 'number' then
    if s == math.floor(s) and math.abs(s) < 1e9 then return math.floor(s) end
    return nil
  end
  if type(s) ~= 'string' or #s > 9 or not s:match('^%-?%d+$') then return nil end
  return tonumber(s)
end

local tx, ty, tz = parse_int(args[2]), parse_int(args[3]), parse_int(args[4])
if not (tx and ty and tz) then qerror('coordenadas inválidas') end
local maxn = math.min(parse_int(args[5]) or NODE_CAP_DEFAULT, NODE_CAP_HARD)

local adv = C.adventurer()
local sx, sy, sz = adv.pos.x, adv.pos.y, adv.pos.z

local DIRS = { {0,-1,'n'}, {0,1,'s'}, {1,0,'e'}, {-1,0,'w'},
               {1,-1,'ne'}, {-1,-1,'nw'}, {1,1,'se'}, {-1,1,'sw'} }

local function emit(t) print(json.encode(t)) end

-- ======================================================================
-- BFS PLANAR (v1) — NÃO MEXER: é o caminho padrão, provado em jogo.
-- ======================================================================
local function route_planar()
  local function walkable(x, y, z)
    local tt = dfhack.maps.getTileType(x, y, z)
    if not tt then return false end
    local sh = df.tiletype.attrs[tt].shape
    if df.tiletype_shape.attrs[sh].walkable then return true end
    local _, occ = dfhack.maps.getTileFlags(x, y, z)
    return occ and occ.building ~= 0          -- porta fechada: abre ao andar
  end

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
      if not prev[k] and math.abs(nx - sx) <= SEARCH_RADIUS
         and math.abs(ny - sy) <= SEARCH_RADIUS and walkable(nx, ny, sz) then
        prev[k] = { cx, cy }
        prevdir[k] = d[3]
        q[#q + 1] = { nx, ny }
      end
    end
  end

  if not found then
    emit({ ok = false, explored = n,
           hint = 'sem caminho no mesmo z dentro do raio ' .. SEARCH_RADIUS })
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
  emit({ ok = true, len = #rev, steps = rev })
end

-- ======================================================================
-- BFS 3D (EXPERIMENTAL: verify live) — tudo dentro de run_3d(): o caminho
-- planar nunca toca neste código (nem no carregamento do script).
-- ======================================================================
local function run_3d(kind)          -- kind: 'route' | 'reach'
  local SH = df.tiletype_shape
  local S = {
    EMPTY = SH.EMPTY, RAMP_TOP = SH.RAMP_TOP, WALL = SH.WALL,
    FORTIFICATION = SH.FORTIFICATION, RAMP = SH.RAMP, STAIR_UP = SH.STAIR_UP,
    STAIR_DOWN = SH.STAIR_DOWN, STAIR_UPDOWN = SH.STAIR_UPDOWN,
  }
  local MAT_LAVA = df.tiletype_material and df.tiletype_material.LAVA_STONE or nil

  local missing_shape = nil               -- nome do 1.º enum em falta (nunca esperado)
  for _, name in ipairs({ 'EMPTY', 'RAMP_TOP', 'WALL', 'FORTIFICATION', 'RAMP',
                          'STAIR_UP', 'STAIR_DOWN', 'STAIR_UPDOWN' }) do
    if S[name] == nil and missing_shape == nil then missing_shape = name end
  end

  -- formas que contam como "pisáveis" mesmo que o atributo `walkable` do build
  -- do DFHack não as marque (são sempre caminháveis; só alarga a BFS 3D)
  local TRAVERSABLE = {}
  for _, name in ipairs({ 'RAMP', 'STAIR_UP', 'STAIR_DOWN', 'STAIR_UPDOWN' }) do
    if S[name] ~= nil then TRAVERSABLE[S[name]] = true end
  end

  local OFF, KY, KZ = 1024, 100000, 10000000000
  -- chave inteira do tile; OFF evita chaves negativas nos vizinhos fora do mapa
  local function key3(x, y, z) return (x + OFF) + (y + OFF) * KY + (z + OFF) * KZ end

  local SHAPE, MATERIAL, WALK, GROUP = {}, {}, {}, {}   -- caches só desta corrida

  -- (shape, material) do tile; shape == false se o tile não existe (bloco não alocado)
  local function tile_attrs(x, y, z)
    local k = key3(x, y, z)
    local sh = SHAPE[k]
    if sh == nil then
      local tt = dfhack.maps.getTileType(x, y, z)
      local a = (tt ~= nil) and df.tiletype.attrs[tt] or nil
      SHAPE[k] = a and a.shape or false
      MATERIAL[k] = a and a.material or false
      sh = SHAPE[k]
    end
    return sh, MATERIAL[k]
  end

  -- o predicado `walkable` da v1 (porta/edifício conta como passável) + escadas/rampas
  local function is_walkable(x, y, z)
    local k = key3(x, y, z)
    local w = WALK[k]
    if w == nil then
      w = false
      local sh = tile_attrs(x, y, z)
      if sh then
        local attrs = df.tiletype_shape.attrs[sh]
        if TRAVERSABLE[sh] or (attrs and attrs.walkable) then
          w = true
        else
          local _, occ = dfhack.maps.getTileFlags(x, y, z)
          w = (occ and occ.building ~= 0) and true or false
        end
      end
      WALK[k] = w
    end
    return w
  end

  -- parede/fortificação sólida (suporte de rampa); "semi-molten rock" não conta
  local function is_solid(x, y, z)
    local sh, mat = tile_attrs(x, y, z)
    if not sh then return false end
    if sh ~= S.WALL and sh ~= S.FORTIFICATION then return false end
    return MAT_LAVA == nil or mat ~= MAT_LAVA
  end

  -- espaço aberto (o "topo de rampa" que o DF escreve por cima de uma RAMP)
  local function is_open(x, y, z)
    local sh = tile_attrs(x, y, z)
    return sh ~= false and (sh == S.EMPTY or sh == S.RAMP_TOP)
  end

  local group_api = USE_WALKABLE_GROUPS and type(dfhack.maps.getWalkableGroup) == 'function'

  -- grupo de caminhada do DF (0 = não caminhável); nil = API indisponível/falhou
  local function group_of(x, y, z)
    if not group_api then return nil end
    local k = key3(x, y, z)
    local g = GROUP[k]
    if g == nil then
      local ok, v = pcall(dfhack.maps.getWalkableGroup, { x = x, y = y, z = z })
      g = (ok and type(v) == 'number') and v or false
      GROUP[k] = g
    end
    return g or nil
  end

  -- Verificações baratas antes da BFS → reason, hint (ou nil se vale a pena procurar).
  local function precheck()
    if missing_shape then
      return 'shape_enum_missing',
             'df.tiletype_shape.' .. missing_shape .. ' não existe nesta versão do DFHack'
    end
    if not tile_attrs(tx, ty, tz) then
      return 'target_unloaded', 'alvo fora do mapa carregado'
    end
    if math.abs(tx - sx) > SEARCH_RADIUS or math.abs(ty - sy) > SEARCH_RADIUS then
      return 'target_out_of_radius', 'alvo além do raio de busca (' .. SEARCH_RADIUS .. ')'
    end
    if not is_walkable(tx, ty, tz) then
      return 'target_not_walkable', 'o tile do alvo não é caminhável'
    end
    local gs, gd = group_of(sx, sy, sz), group_of(tx, ty, tz)
    if gs and gd and gs ~= 0 and gd ~= 0 and gs ~= gd then
      return 'different_walkable_group',
             'origem e alvo estão em grupos de caminhada diferentes (o DF não os liga)'
    end
    return nil
  end

  -- BFS 3D de custo uniforme (cada tecla = 1). → { found, explored, steps, z_changes }
  local function search3d()
    local out = { found = false, explored = 0 }
    local skey = key3(sx, sy, sz)
    local seen = { [skey] = 1 }
    local qx, qy, qz = { sx }, { sy }, { sz }       -- fila por id de nó (1 = origem)
    local parent, token, kind = {}, {}, {}
    local qn, qi, n = 1, 1, 0
    local zlo, zhi = math.min(sz, tz) - Z_MARGIN, math.max(sz, tz) + Z_MARGIN
    local src_group = group_of(sx, sy, sz) or 0

    local function push(from, nx, ny, nz, tok, knd)
      local nk = key3(nx, ny, nz)
      if seen[nk] then return end
      if math.abs(nx - sx) > SEARCH_RADIUS or math.abs(ny - sy) > SEARCH_RADIUS
         or nz < zlo or nz > zhi then return end
      if src_group ~= 0 then
        local g = group_of(nx, ny, nz)
        if g and g ~= 0 and g ~= src_group then return end   -- o DF diz que não liga
      end
      qn = qn + 1
      seen[nk] = qn
      qx[qn], qy[qn], qz[qn] = nx, ny, nz
      parent[qn], token[qn], kind[qn] = from, tok, knd or false
    end

    local found_id = nil
    while qi <= qn and n < maxn do
      local id = qi; qi = qi + 1; n = n + 1
      local cx, cy, cz = qx[id], qy[id], qz[id]
      if cx == tx and cy == ty and cz == tz then found_id = id; break end

      -- 1) planar, no mesmo z (o mesmo predicado da v1)
      for _, d in ipairs(DIRS) do
        local nx, ny = cx + d[1], cy + d[2]
        if is_walkable(nx, ny, cz) then push(id, nx, ny, cz, d[3], nil) end
      end

      local sh = tile_attrs(cx, cy, cz)

      -- 2) escadas: o par STAIR_UP/UPDOWN (z) ↔ STAIR_DOWN/UPDOWN (z+1), mesmo (x,y)
      if sh == S.STAIR_UP or sh == S.STAIR_UPDOWN then
        local above = tile_attrs(cx, cy, cz + 1)
        if above == S.STAIR_DOWN or above == S.STAIR_UPDOWN then
          push(id, cx, cy, cz + 1, 'up', 'stair')
        end
      end
      if sh == S.STAIR_DOWN or sh == S.STAIR_UPDOWN then
        local below = tile_attrs(cx, cy, cz - 1)
        if below == S.STAIR_UP or below == S.STAIR_UPDOWN then
          push(id, cx, cy, cz - 1, 'down', 'stair')
        end
      end

      -- 3) rampa SOBE: de R (RAMP) para o topo da parede vizinha, em z+1
      if sh == S.RAMP and is_open(cx, cy, cz + 1) then
        for _, d in ipairs(DIRS) do
          local ux, uy = cx + d[1], cy + d[2]
          if is_solid(ux, uy, cz) and is_walkable(ux, uy, cz + 1) then
            push(id, ux, uy, cz + 1, d[3] .. '_up', 'ramp')
          end
        end
      end

      -- 4) rampa DESCE: do topo de uma parede (estou em cima dela) para a RAMP vizinha
      if is_solid(cx, cy, cz - 1) then
        for _, d in ipairs(DIRS) do
          local rx, ry = cx + d[1], cy + d[2]
          if tile_attrs(rx, ry, cz - 1) == S.RAMP and is_open(rx, ry, cz) then
            push(id, rx, ry, cz - 1, d[3] .. '_down', 'ramp')
          end
        end
      end
    end
    out.explored = n
    if not found_id then return out end

    out.found = true
    local ids, id = {}, found_id
    while id ~= 1 do ids[#ids + 1] = id; id = parent[id] end
    local steps, zc = {}, {}
    for i = #ids, 1, -1 do
      local nid = ids[i]
      steps[#steps + 1] = token[nid]
      if kind[nid] then
        zc[#zc + 1] = { i = #steps - 1, dz = qz[nid] - qz[parent[nid]], kind = kind[nid],
                        step = token[nid], x = qx[nid], y = qy[nid], z = qz[nid] }
      end
    end
    out.steps, out.z_changes = steps, zc
    return out
  end

  -- Roda precheck + BFS → tabela JSON-ável de route3d (ok/steps ou ok=false/reason).
  local function route_3d()
    local gs, gd = group_of(sx, sy, sz), group_of(tx, ty, tz)
    local reason, hint = precheck()
    if reason then
      return { ok = false, mode = '3d', reason = reason, hint = hint, explored = 0,
               node_cap = maxn, group_from = gs, group_to = gd }
    end
    local r = search3d()
    if r.found then
      return { ok = true, mode = '3d', len = #r.steps, steps = r.steps, z_changes = r.z_changes,
               explored = r.explored, node_cap = maxn,
               origin = { x = sx, y = sy, z = sz }, target = { x = tx, y = ty, z = tz } }
    end
    if r.explored >= maxn then
      return { ok = false, mode = '3d', reason = 'node_cap', explored = r.explored,
               hint = 'orçamento de nós esgotado (' .. maxn .. ')', node_cap = maxn,
               group_from = gs, group_to = gd }
    end
    return { ok = false, mode = '3d', reason = 'no_path', explored = r.explored,
             hint = 'sem caminho 3D (escadas/rampas) dentro do raio ' .. SEARCH_RADIUS,
             node_cap = maxn, group_from = gs, group_to = gd }
  end

  -- Sonda barata: grupos de caminhada do DF quando existem (e são não nulos); senão BFS.
  local function reach()
    local dist = math.max(math.abs(tx - sx), math.abs(ty - sy))
    local gs, gd = group_of(sx, sy, sz), group_of(tx, ty, tz)
    if gs and gd and gs ~= 0 and gd ~= 0 then
      emit({ ok = true, reachable = (gs == gd), method = 'walkable_group',
             group_from = gs, group_to = gd, distance = dist, dz = tz - sz,
             within_radius = dist <= SEARCH_RADIUS })
      return
    end
    local r = route_3d()
    emit({ ok = true, reachable = r.ok, method = 'bfs', reason = r.reason, len = r.len,
           explored = r.explored, group_from = gs, group_to = gd, distance = dist,
           dz = tz - sz, within_radius = dist <= SEARCH_RADIUS })
  end

  if kind == 'reach' then
    reach()
  else
    emit(route_3d())
  end
end

-- ---------- despacho ----------
if cmd == 'route' and tz == sz then
  route_planar()
else
  run_3d(cmd == 'reach' and 'reach' or 'route')
end
