-- dfb-act.lua — camada de OBSERVAÇÃO/AÇÃO do copiloto (instalada pelo df_bridge.py
-- em <DF>/dfhack-config/scripts/). Saída: JSON em stdout.
-- Uso:
--   dfb-act focus                     → {focus:[...]}            que tela está aberta
--   dfb-act screen [y1] [y2]          → {focus, lines:[...]}     texto visível na tela
--   dfb-act key KEY [KEY...]          → simula teclas (nomes de df.interface_key)
--   dfb-act click X Y [left|right]    → clique de mouse sintético no tile (x,y) da tela
--   dfb-act move <n|s|e|w|ne|nw|se|sw|up|down|wait>  → 1 passo do aventureiro
-- Licença: MIT (dwarf-fortress-agent-skill).

local C = reqscript('dfb-common')
local json = C.json
local gui = require('gui')
local focus_strings = C.focus_strings
C.version_guard()

local args = {...}
local what = args[1] or 'focus'

local function read_screen(y1, y2)
  local w, h = dfhack.screen.getWindowSize()
  y1 = math.max(0, y1 or 0)
  y2 = math.min(h - 1, y2 or (h - 1))
  local lines = {}
  for y = y1, y2 do
    local row = {}
    for x = 0, w - 1 do
      local p = dfhack.screen.readTile(x, y)
      local c = p and p.ch or 32
      -- df2utf POR LINHA (0x0A em CP437 é o glifo ◙, não newline)
      row[#row + 1] = (c > 31 and c < 256) and string.char(c) or ' '
    end
    local s = dfhack.df2utf(table.concat(row)):gsub('%s+$', '')
    lines[#lines + 1] = s
  end
  return lines
end

local function press(keys)
  gui.simulateInput(dfhack.gui.getCurViewscreen(), keys)
end

local MOVE_KEYS = {
  n = 'A_MOVE_N', s = 'A_MOVE_S', e = 'A_MOVE_E', w = 'A_MOVE_W',
  ne = 'A_MOVE_NE', nw = 'A_MOVE_NW', se = 'A_MOVE_SE', sw = 'A_MOVE_SW',
  up = 'A_MOVE_UP', down = 'A_MOVE_DOWN', wait = 'A_SHORT_WAIT',
}

local result
if what == 'focus' then
  result = { focus = focus_strings() }

elseif what == 'screen' then
  result = { focus = focus_strings(),
             lines = read_screen(tonumber(args[2]), tonumber(args[3])) }

elseif what == 'key' then
  local keys = {}
  for i = 2, #args do
    if not df.interface_key[args[i]] then qerror('tecla desconhecida: ' .. args[i]) end
    keys[#keys + 1] = args[i]
  end
  if #keys == 0 then qerror('uso: dfb-act key NOME_DA_TECLA ...') end
  press(keys)
  result = { ok = true, pressed = keys, focus = focus_strings() }

elseif what == 'click' then
  -- mouse sintético: posiciona em coords de TILE da tela (mesmas de `screen`) e clica.
  local x, y = tonumber(args[2]), tonumber(args[3])
  local btn = (args[4] or 'left'):lower()
  if not (x and y) then qerror('uso: dfb-act click <x> <y> [left|right]') end
  local w, h = dfhack.screen.getWindowSize()
  if x < 0 or y < 0 or x >= w or y >= h then
    qerror(('clique fora da tela %dx%d: %d,%d'):format(w, h, x, y))
  end
  local gps = df.global.gps
  gps.mouse_x, gps.mouse_y = x, y
  gps.precise_mouse_x = x * gps.tile_pixel_x + gps.tile_pixel_x // 2
  gps.precise_mouse_y = y * gps.tile_pixel_y + gps.tile_pixel_y // 2
  press({ btn == 'right' and '_MOUSE_R' or '_MOUSE_L' })
  result = { ok = true, clicked = { x = x, y = y }, button = btn, focus = focus_strings() }

elseif what == 'move' then
  local key = MOVE_KEYS[(args[2] or ''):lower()]
  if not key then qerror('uso: dfb-act move n|s|e|w|ne|nw|se|sw|up|down|wait') end
  local f = table.concat(focus_strings(), '/')
  -- só anda com o MAPA em primeiro plano (sem menus abertos por cima)
  if not f:find('dungeonmode') or f:find('Announcements') or f:find('Sheet')
     or f:find('Conversation') or f:find('Travel') then
    qerror('não está no mapa de aventura (focus: ' .. f .. ') — feche o menu antes (dfb-act key LEAVESCREEN)')
  end
  local adv = dfhack.world.getAdventurer()
  local before = adv and { x = adv.pos.x, y = adv.pos.y, z = adv.pos.z } or nil
  press({ key })
  -- o passo processa no próximo frame do jogo: pos aqui ainda é a ANTERIOR
  result = { ok = true, dir = args[2], key = key, pos_before = before,
             focus = focus_strings() }

else
  qerror('uso: dfb-act <focus|screen|key|click|move> ...')
end

print(json.encode(result))
