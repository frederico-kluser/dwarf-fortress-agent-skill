-- dfb-nav.lua — auto-rota do copiloto: BFS no mapa local (mesmo z, 8 direções;
-- tiles com edifício, ex.: portas, contam como passáveis). Saída: JSON.
-- Uso: dfb-nav route <x> <y> <z> [max_nos]   → {ok, steps:["s","se",...], len}
-- Licença: MIT (dwarf-fortress-agent-skill).

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
