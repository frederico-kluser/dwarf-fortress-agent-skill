-- dfb-state.lua — leitura estruturada (JSON) do estado do jogo p/ agentes LLM.
-- Instalado automaticamente pelo df_bridge.py em <DF>/dfhack-config/scripts/.
-- Uso (console ou dfhack-run):
--   dfb-state <adventurer|threats|units|inventory|date|all|snapshot> [raio]
-- Saída: UMA linha/bloco JSON em stdout (consumida por `df_bridge.py state ...`).
--
-- `snapshot` alimenta `df_bridge.py snapshot`: {mode, adventurer, metrics}, com os
-- 8 grupos de métricas pedidos pela camada de GAME STATE (nomes de campo em INGLÊS):
--   health_injuries, needs_state, inventory, threats, location, game_clock,
--   units_nearby, events.
--
-- O bloco `adventurer` ganhou `combat` (prontidão de combate: skills efetivas, atributos,
-- dor/exaustão/sangue, armadura — ver "prontidão de combate" abaixo); as demais chaves
-- do `adventurer` ficam como estavam.
--
-- Regras desta camada:
--   * leitura DEFENSIVA (C.safe + pcall/guard): o script NUNCA estoura por causa de
--     um campo que mudou de nome/versão;
--   * o que o adventure mode / DFHack não expõe sai como `null` (ex.: hp_current,
--     hp_max, capacity_kg, travel_destination) — nenhum valor é inventado;
--   * unidades de peso: kg (massst.whole = kilograms; massst.fraction = milligrams).
-- Licença: MIT (dwarf-fortress-agent-skill).

local C = reqscript('dfb-common')
local utf = C.utf
C.version_guard()

local args = {...}
local what = args[1] or 'all'
local radius = tonumber(args[2]) or 25
local Z_RADIUS = 5                       -- ameaças: poucos níveis acima/abaixo importam

-- ---------- data do mundo ----------
local MONTHS = { 'Granite','Slate','Felsite','Hematite','Malachite','Galena',
                 'Limestone','Sandstone','Timber','Moonstone','Opal','Obsidian' }
local SEASONS = { 'spring','spring','spring','summer','summer','summer',
                  'autumn','autumn','autumn','winter','winter','winter' }
local TICKS_PER_DAY = 1200               -- 1 dia = 1200 ticks; 1 hora = 50 ticks

local function state_date()
  local tick = df.global.cur_year_tick
  local month = math.floor(tick / 33600) + 1       -- 28 dias x 1200 ticks
  local day = math.floor((tick % 33600) / 1200) + 1
  local hour = math.floor((tick % TICKS_PER_DAY) / 50)
  return {
    year = df.global.cur_year,
    tick = tick,
    month = MONTHS[month] or month,
    day = day,
    season = SEASONS[month] or 'unknown',
    hour = hour,
    date_text = ('%s %d, year %d'):format(MONTHS[month] or tostring(month), day,
                                          df.global.cur_year),
  }
end

-- ---------- helpers defensivos ----------
-- envolve uma leitura/grupo: se algo estourar, devolve o fallback em vez de derrubar
-- o script inteiro (structs do DF mudam de nome entre versões).
local function guard(fn, fallback)
  local ok, v = pcall(fn)
  if ok and v ~= nil then return v end
  return fallback
end

local function as_number(v)
  return type(v) == 'number' and v or nil
end

-- ---------- aventureiro ----------
local adventurer_unit = C.adventurer

-- timers sobem com o tempo; rótulos pelos limiares clássicos dos anúncios.
-- C.safe blinda contra campos que mudam de nome entre versões (counters2 já crashou).
local function needs_block(c2)
  local hunger = C.safe(c2, 'hunger_timer', 0)
  local thirst = C.safe(c2, 'thirst_timer', 0)
  local sleep = C.safe(c2, 'sleepiness_timer', 0)
  return {
    hunger_timer = hunger, hungry = hunger >= 75000, starving = hunger >= 150000,
    thirst_timer = thirst, thirsty = thirst >= 25000, dehydrated = thirst >= 50000,
    sleepiness_timer = sleep, drowsy = sleep >= 57600,
  }
end

-- ---------- prontidão de combate (ARQUITETURA §8, mudança 02) ----------
-- Retrato compacto (~30 números) do que o aventureiro consegue fazer AGORA: o decisor
-- (src/system_one_dwarf/game/readiness.py) pesa ataque/fuga/caça com a capacidade real.
--   * skills EFETIVAS (dfhack.units.getEffectiveSkill já desconta ferrugem, exaustão,
--     dor...), níveis 0..20+ (0 = Dabbling/sem skill; 5 = Proficient; 15 = Legendary);
--   * cada campo é lido à parte (guard/C.safe): o que o jogo ou esta versão do DFHack
--     não der SAI nil (a chave some do JSON). Nada é inventado — nem "desarmado" sem ter
--     conseguido ler o inventário;
--   * aqui só se MEDE; quem interpreta e decide é o Python.
-- Forma (secção ausente = nada legível; `skill` = nível efetivo, `skill_token` = df.job_skill):
--   combat = { weapon = { name, skill_token, skill_noun, skill, ranged, armed },
--              skills = { fighter, dodger, shield_user, armor_user, wrestler, striker, kicker },
--              attributes = { strength, agility, toughness, endurance, spatial_sense,
--                             kinesthetic_sense, willpower },
--              condition = { blood_ratio, pain, exhaustion, winded, stunned, nausea,
--                            dizziness, webbed, wound_count, bleeding },
--              armor = { pieces, slots, shield } }
local COMBAT_SKILLS = {                      -- chave do bloco -> token de df.job_skill
  fighter = 'MELEE_COMBAT', dodger = 'DODGING', shield_user = 'SHIELD',
  armor_user = 'ARMOR', wrestler = 'WRESTLING',
  striker = 'GRASP_STRIKE', kicker = 'STANCE_STRIKE',   -- soco (mãos) / chute (pés)
}
local UNARMED_SKILL = 'GRASP_STRIKE'         -- o soco é o ataque desarmado do aventureiro
local COMBAT_ATTRS = {                       -- { chave, 'physical'|'mental', token do enum }
  { 'strength', 'physical', 'STRENGTH' }, { 'agility', 'physical', 'AGILITY' },
  { 'toughness', 'physical', 'TOUGHNESS' }, { 'endurance', 'physical', 'ENDURANCE' },
  { 'spatial_sense', 'mental', 'SPATIAL_SENSE' },
  { 'kinesthetic_sense', 'mental', 'KINESTHETIC_SENSE' },
  { 'willpower', 'mental', 'WILLPOWER' },
}
-- tipos de item de proteção -> espaço do corpo coberto (armorlevel 0 = roupa, não conta)
local ARMOR_SLOTS = { ARMOR = 'body', PANTS = 'legs', HELM = 'head', GLOVES = 'hands',
                      SHOES = 'feet' }
local COMBAT_CAP_INVENTORY, COMBAT_CAP_WOUNDS = 60, 60

local function effective_skill(u, token)
  return guard(function()
    return as_number(dfhack.units.getEffectiveSkill(u, df.job_skill[token]))
  end, nil)
end

local function attribute_value(u, kind, token)
  return guard(function()
    if kind == 'physical' then
      return as_number(dfhack.units.getPhysicalAttrValue(u, df.physical_attribute_type[token]))
    end
    return as_number(dfhack.units.getMentalAttrValue(u, df.mental_attribute_type[token]))
  end, nil)
end

local function item_type_name(item)
  return guard(function()
    local name = df.item_type[item:getType()]
    return type(name) == 'string' and name or nil
  end, nil)
end

-- skill de combate da arma: a de tiro se for arma de longo alcance, senão a corpo a corpo
-- (itemdef_weaponst.skill_ranged / skill_melee; -1 = nenhuma). -> { id, ranged } | nil
local function weapon_skill(item)
  return guard(function()
    local def = dfhack.items.getSubtypeDef(item:getType(), item:getSubtype())
    local ranged = as_number(C.safe(def, 'skill_ranged'))
    if ranged and ranged >= 0 then return { id = ranged, ranged = true } end
    local melee = as_number(C.safe(def, 'skill_melee'))
    if melee and melee >= 0 then return { id = melee, ranged = false } end
    return nil
  end, nil)
end

local function job_skill_name(id)
  return guard(function()
    local name = df.job_skill[id]
    return type(name) == 'string' and name or nil
  end, nil)
end

local function job_skill_noun(id)       -- "Spearman", "Striker"... (caption_noun do jogo)
  return guard(function()
    local noun = df.job_skill.attrs[id].caption_noun
    return (type(noun) == 'string' and noun ~= '') and utf(noun) or nil
  end, nil)
end

-- inventário: arma na mão (item WEAPON em modo Weapon), escudo na mão e peças de armadura
-- vestidas (modo Worn, armorlevel > 0). nil = inventário ilegível; um item cujo tipo não
-- se lê torna `armed`/`armor` desconhecidos (nil) em vez de errados.
local function inventory_scan(u)
  local inv = C.safe(u, 'inventory')
  if inv == nil then return nil end
  local scan = { weapon = nil, shield = false, pieces = 0, slots = 0, covered = {},
                 unreadable_hand = false, unreadable_worn = false }
  local seen = 0
  for _, e in ipairs(inv) do
    seen = seen + 1
    if seen > COMBAT_CAP_INVENTORY then break end
    local item, mode = C.safe(e, 'item'), C.safe(e, 'mode')
    local mode_name = mode ~= nil and df.inv_item_role_type[mode] or nil
    if item ~= nil and (mode_name == 'Weapon' or mode_name == 'Worn') then
      local tname = item_type_name(item)
      if tname == nil then
        if mode_name == 'Weapon' then scan.unreadable_hand = true
        else scan.unreadable_worn = true end
      elseif mode_name == 'Weapon' then
        if tname == 'WEAPON' and scan.weapon == nil then
          scan.weapon = item
        elseif tname == 'SHIELD' then
          scan.shield = true
        end
      elseif ARMOR_SLOTS[tname] then
        -- armorlevel 0 = roupa (túnica, calças de pano); se não der p/ ler, conta a peça
        local level = as_number(C.safe(C.safe(item, 'subtype'), 'armorlevel'))
        if level == nil or level > 0 then
          scan.pieces = scan.pieces + 1
          local slot = ARMOR_SLOTS[tname]
          if not scan.covered[slot] then
            scan.covered[slot] = true
            scan.slots = scan.slots + 1
          end
        end
      end
    end
  end
  return scan
end

local function weapon_block(u, scan)
  local w = {}
  if scan == nil then return w end
  if scan.weapon ~= nil then
    w.armed = true
    w.name = guard(function()
      return utf(dfhack.items.getReadableDescription(scan.weapon))
    end, nil)
    local sk = weapon_skill(scan.weapon)
    if sk ~= nil then
      w.ranged = sk.ranged
      w.skill_token = job_skill_name(sk.id)
      w.skill_noun = job_skill_noun(sk.id)
      w.skill = guard(function()                    -- nível EFETIVO da skill da arma
        return as_number(dfhack.units.getEffectiveSkill(u, sk.id))
      end, nil)
    end
  elseif not scan.unreadable_hand then
    -- nada na mão: ataca com o punho (GRASP_STRIKE, "Striker")
    w.armed = false
    w.skill_token = UNARMED_SKILL
    w.skill_noun = guard(function() return job_skill_noun(df.job_skill[UNARMED_SKILL]) end, nil)
    w.skill = effective_skill(u, UNARMED_SKILL)
  end
  return w
end

local function armor_block(scan)
  if scan == nil or scan.unreadable_worn then return nil end
  local shield = nil
  if scan.shield then shield = true elseif not scan.unreadable_hand then shield = false end
  return { pieces = scan.pieces, slots = scan.slots, shield = shield }
end

-- sangue (razão), dor, exaustão (counters2), estados transitórios e ferimentos a sangrar
local function condition_block(body, c, c2)
  local blood = as_number(C.safe(body, 'blood_count'))
  local blood_max = as_number(C.safe(body, 'blood_max'))
  local ratio = nil
  if blood and blood_max and blood_max > 0 then
    ratio = math.max(0, math.min(1, blood / blood_max))
  end
  local wounds = C.safe(body, 'wounds')
  local wound_count, bleeding = nil, nil
  if wounds ~= nil then
    wound_count, bleeding = #wounds, 0
    local seen = 0
    for _, w in ipairs(wounds) do
      seen = seen + 1
      if seen > COMBAT_CAP_WOUNDS then break end
      for _, part in ipairs(C.safe(w, 'parts') or {}) do
        local bl = as_number(C.safe(part, 'bleeding'))
        if bl and bl > 0 then
          bleeding = bleeding + 1
          break
        end
      end
    end
  end
  return {
    blood_ratio = ratio,
    pain = as_number(C.safe(c, 'pain')),
    exhaustion = as_number(C.safe(c2, 'exhaustion')),
    winded = as_number(C.safe(c, 'winded')),
    stunned = as_number(C.safe(c, 'stunned')),
    nausea = as_number(C.safe(c, 'nausea')),
    dizziness = as_number(C.safe(c, 'dizziness')),
    webbed = as_number(C.safe(c, 'webbed')),
    wound_count = wound_count,
    bleeding = bleeding,
  }
end

local function nonempty(t)
  if t ~= nil and next(t) ~= nil then return t end
  return nil
end

local function combat_block(u)
  local scan = guard(function() return inventory_scan(u) end, nil)
  local skills, attributes = {}, {}
  for key, token in pairs(COMBAT_SKILLS) do skills[key] = effective_skill(u, token) end
  for _, spec in ipairs(COMBAT_ATTRS) do
    attributes[spec[1]] = attribute_value(u, spec[2], spec[3])
  end
  return nonempty({
    weapon = nonempty(guard(function() return weapon_block(u, scan) end, nil)),
    skills = nonempty(skills),
    attributes = nonempty(attributes),
    condition = nonempty(guard(function()
      return condition_block(C.safe(u, 'body'), C.safe(u, 'counters'), C.safe(u, 'counters2'))
    end, nil)),
    armor = nonempty(guard(function() return armor_block(scan) end, nil)),
  })
end

local function state_adventurer()
  local u = adventurer_unit()
  local c, c2, body = C.safe(u, 'counters'), C.safe(u, 'counters2'), C.safe(u, 'body')
  local wounds = C.safe(body, 'wounds')
  local pos = C.safe(u, 'pos')
  return {
    name = guard(function() return utf(dfhack.units.getReadableName(u)) end, nil),
    race = guard(function() return utf(dfhack.units.getRaceReadableName(u)) end, nil),
    profession = guard(function() return utf(dfhack.units.getProfessionName(u)) end, nil),
    pos = { x = as_number(C.safe(pos, 'x')), y = as_number(C.safe(pos, 'y')),
            z = as_number(C.safe(pos, 'z')) },
    health = {
      blood = C.safe(body, 'blood_count', 0), blood_max = C.safe(body, 'blood_max', 0),
      wounds = wounds and #wounds or 0,
      pain = C.safe(c, 'pain', 0), nausea = C.safe(c, 'nausea', 0),
      dizziness = C.safe(c, 'dizziness', 0), winded = C.safe(c, 'winded', 0),
      stunned = C.safe(c, 'stunned', 0), unconscious = C.safe(c, 'unconscious', 0),
      webbed = C.safe(c, 'webbed', 0), paralysis = C.safe(c2, 'paralysis', 0),
      fever = C.safe(c2, 'fever', 0), exhaustion = C.safe(c2, 'exhaustion', 0),
    },
    needs = needs_block(c2),
    kills = guard(function() return dfhack.units.getKillCount(u) end, nil),
    stress_category = guard(function() return dfhack.units.getStressCategory(u) end, nil),
    -- aditivo (mudança 02): prontidão de combate; nil se nada for legível
    combat = guard(function() return combat_block(u) end, nil),
  }
end

-- ---------- unidades / ameaças ao redor ----------
local function direction(dx, dy, dz)
  local d = ''
  if dy < 0 then d = 'N' elseif dy > 0 then d = 'S' end
  if dx > 0 then d = d .. 'E' elseif dx < 0 then d = d .. 'W' end
  if d == '' then d = 'here' end
  if dz > 0 then d = d .. ' +' .. dz .. 'z' elseif dz < 0 then d = d .. ' ' .. dz .. 'z' end
  return d
end

local function scan_units(only_threats)
  local adv = adventurer_unit()
  local ax, ay, az = adv.pos.x, adv.pos.y, adv.pos.z
  local out = {}
  for _, u in ipairs(df.global.world.units.active) do
    if u ~= adv and dfhack.units.isAlive(u) then
      local dx, dy, dz = u.pos.x - ax, u.pos.y - ay, u.pos.z - az
      local dist = math.max(math.abs(dx), math.abs(dy))
      if dist <= radius and math.abs(dz) <= Z_RADIUS then
        local danger = dfhack.units.isDanger(u)
        if not only_threats or danger then
          out[#out + 1] = {
            name = utf(dfhack.units.getReadableName(u)),
            race = utf(dfhack.units.getRaceReadableNameById(u.race)),
            dist = dist,
            dir = direction(dx, dy, dz),
            pos = { x = u.pos.x, y = u.pos.y, z = u.pos.z },
            danger = danger,
            great_danger = dfhack.units.isGreatDanger(u),
            invader = dfhack.units.isInvader(u),
            undead = dfhack.units.isUndead(u),
            agitated = dfhack.units.isAgitated(u),
            wildlife = dfhack.units.isWildlife(u),
            tame = dfhack.units.isTame(u),
            hidden = dfhack.units.isHidden(u),   -- info "armok": use com parcimônia
            crazed = dfhack.units.isCrazed(u),
          }
        end
      end
    end
  end
  table.sort(out, function(a, b) return a.dist < b.dist end)
  -- limita p/ não estourar o contexto do agente
  while #out > 20 do table.remove(out) end
  return out
end

-- ---------- inventário ----------
local function state_inventory()
  local u = adventurer_unit()
  local out = {}
  for _, e in ipairs(u.inventory) do
    out[#out + 1] = {
      mode = df.inv_item_role_type[e.mode] or tostring(e.mode),
      item = utf(dfhack.items.getReadableDescription(e.item)),
    }
  end
  return out
end

-- =====================================================================
-- SNAPSHOT: os 8 grupos de métricas (camada de GAME STATE)
-- =====================================================================
local CAP_ITEMS, CAP_HOSTILES, CAP_UNITS = 30, 12, 8

-- nome legível de uma parte do corpo (body_part_raw.name_singular[0] ou token)
local function body_part_name(u, bp_id)
  if type(bp_id) ~= 'number' or bp_id < 0 then return nil end
  local ok, name = pcall(function()
    local plan = u.body and u.body.body_plan
    local parts = plan and plan.body_parts
    local bp = parts and parts[bp_id]
    if not bp then return nil end
    local names = bp.name_singular
    if names and names[0] then return utf(names[0]) end
    return utf(bp.token)
  end)
  if ok and type(name) == 'string' and name ~= '' then return name end
  return nil
end

-- ferimentos: 1 entrada por unit_wound; severity = maior surface_perc das camadas
-- (0-100, área de contato atingida), bleeding = alguma camada sangrando.
local function wounds_list(u)
  local out = {}
  local body = C.safe(u, 'body')
  local wounds = C.safe(body, 'wounds')
  if not wounds then return out end
  for _, w in ipairs(wounds) do
    local severity, bleeding, bp_id = 0, false, nil
    local parts = C.safe(w, 'parts')
    if parts then
      for _, part in ipairs(parts) do
        if bp_id == nil then bp_id = as_number(C.safe(part, 'body_part_id')) end
        local surf = as_number(C.safe(part, 'surface_perc'))
        if surf and surf > severity then severity = surf end
        local bl = as_number(C.safe(part, 'bleeding'))
        if bl and bl > 0 then bleeding = true end
      end
    end
    local flags = C.safe(w, 'flags')
    local bits = C.safe(flags, 'bits')
    out[#out + 1] = {
      body_part = body_part_name(u, bp_id),
      severity = #(parts or {}) > 0 and severity or nil,
      bleeding = bleeding,
      severed = C.safe(bits, 'severed_part') and true or false,
      pain = as_number(C.safe(w, 'pain')),
    }
    if #out >= CAP_ITEMS then break end
  end
  return out
end

-- condições de estado (inglesas) lidas dos counters; só entra o que está ativo
local CONDITION_FIELDS = {
  { 'counters', 'stunned', 'stunned' },
  { 'counters', 'unconscious', 'unconscious' },
  { 'counters', 'winded', 'winded' },
  { 'counters', 'nausea', 'nauseous' },
  { 'counters', 'dizziness', 'dizzy' },
  { 'counters', 'webbed', 'webbed' },
  { 'counters2', 'paralysis', 'paralyzed' },
  { 'counters2', 'fever', 'feverish' },
  { 'counters2', 'exhaustion', 'exhausted' },
}

local function health_injuries_group(u, wounds)
  local conditions = {}
  for _, spec in ipairs(CONDITION_FIELDS) do
    local counter = C.safe(u, spec[1])
    local v = as_number(C.safe(counter, spec[2]))
    if v and v > 0 then conditions[#conditions + 1] = spec[3] end
  end
  local bleeding = false
  for _, w in ipairs(wounds) do
    if w.bleeding then bleeding = true end
  end
  if bleeding then conditions[#conditions + 1] = 'bleeding' end

  local alive = guard(function() return dfhack.units.isAlive(u) end, nil)
  local unconscious = as_number(C.safe(C.safe(u, 'counters'), 'unconscious'))
  local status
  if alive == false then
    status = 'dead'
  elseif unconscious and unconscious > 0 then
    status = 'unconscious'
  elseif bleeding then
    status = 'bleeding'
  elseif #wounds > 0 then
    status = 'wounded'
  else
    status = 'healthy'
  end

  local body = C.safe(u, 'body')
  local c = C.safe(u, 'counters')
  return {
    -- o adventure mode não tem número de HP: fica null em vez de inventar
    hp_current = nil,
    hp_max = nil,
    blood_current = as_number(C.safe(body, 'blood_count')),
    blood_max = as_number(C.safe(body, 'blood_max')),
    wound_count = #wounds,
    wounds = wounds,
    pain = as_number(C.safe(c, 'pain')),
    status = status,
    status_conditions = conditions,
  }
end

local function needs_state_group(u)
  local c2 = C.safe(u, 'counters2')
  return {
    hunger = (function(t)
      if not t then return nil end
      if t >= 150000 then return 'starving' end
      if t >= 75000 then return 'hungry' end
      return 'none'
    end)(as_number(C.safe(c2, 'hunger_timer'))),
    thirst = (function(t)
      if not t then return nil end
      if t >= 50000 then return 'dehydrated' end
      if t >= 25000 then return 'thirsty' end
      return 'none'
    end)(as_number(C.safe(c2, 'thirst_timer'))),
    sleep = (function(t)
      if not t then return nil end
      if t >= 57600 then return 'drowsy' end
      return 'rested'
    end)(as_number(C.safe(c2, 'sleepiness_timer'))),
    hunger_timer = as_number(C.safe(c2, 'hunger_timer')),
    thirst_timer = as_number(C.safe(c2, 'thirst_timer')),
    sleepiness_timer = as_number(C.safe(c2, 'sleepiness_timer')),
    -- getStressCategory devolve 0-6 (0 = mais stressado, 6 = menos); número, não rótulo
    stress_level = guard(function() return as_number(dfhack.units.getStressCategory(u)) end, nil),
  }
end

-- peso em kg: massst = { whole = kilograms, fraction = milligrams }
local function item_weight_kg(item)
  local ok, kg = pcall(function()
    local flags = item.flags
    local computed = flags and flags.bits and flags.bits.weight_computed
    if computed == false then return nil end
    local m = item.weight
    if not m then return nil end
    local whole = tonumber(m.whole) or 0
    local frac = tonumber(m.fraction) or 0
    return whole + frac / 1000000
  end)
  if ok and type(kg) == 'number' then return kg end
  return nil
end

local function item_material(item)
  local ok, name = pcall(function()
    local info = dfhack.matinfo.decode(item)
    return info and info:toString() or nil
  end)
  if ok and type(name) == 'string' and name ~= '' then return name end
  return nil
end

local WORN_MODES = { Worn = true, Piercing = true, Flask = true, WrappedAround = true,
                     Strapped = true, InMouth = true, SewnInto = true }

local function inventory_group(u)
  local items, armor = {}, {}
  local total_kg, known_kg = 0, false
  local weapon = nil
  for _, e in ipairs(C.safe(u, 'inventory') or {}) do
    local item = C.safe(e, 'item')
    local mode = C.safe(e, 'mode')
    local mode_name = mode ~= nil and (df.inv_item_role_type[mode] or tostring(mode)) or nil
    local kg = item and item_weight_kg(item) or nil
    if kg then total_kg, known_kg = total_kg + kg, true end
    local size = item and as_number(C.safe(item, 'stack_size')) or nil
    local name = item and utf(dfhack.items.getReadableDescription(item)) or nil
    local equipped = mode_name ~= nil and mode_name ~= 'NONE' and mode_name ~= 'Hauled'
    items[#items + 1] = {
      name = name,
      quantity = (size and size > 0) and size or 1,
      weight_kg = kg,
      equipped = equipped,
      slot = mode_name,
      material = item and item_material(item) or nil,
    }
    if mode_name == 'Weapon' and not weapon then weapon = name end
    if equipped and WORN_MODES[mode_name] and name then
      armor[#armor + 1] = name
    end
    if #items >= CAP_ITEMS then break end
  end
  while #armor > CAP_HOSTILES do table.remove(armor) end
  return {
    items = items,
    item_count = #items,
    total_weight_kg = known_kg and total_kg or nil,
    -- capacidade de carga do adventure mode não é exposta pelo DFHack: null
    capacity_kg = nil,
    equipped_weapon = weapon,
    equipped_armor = armor,
  }
end

-- varredura enxuta (só o que o prompt precisa): nome, distância, direção, hostil, visível
-- 'sapient' (humanoide inteligente: nunca alvo de caça sem aprovação humana) /
-- 'animal' (restantes criaturas: caçáveis) / nil (DFHack sem os helpers).
local function unit_kind(u)
  if type(dfhack.units.isSapient) == 'function' then
    return guard(function() return dfhack.units.isSapient(u) and 'sapient' or 'animal' end, nil)
  end
  if type(dfhack.units.isAnimal) == 'function' then
    return guard(function() return dfhack.units.isAnimal(u) and 'animal' or 'sapient' end, nil)
  end
  return nil
end

local function units_lean(only_hostile, cap)
  local adv = adventurer_unit()
  local ax, ay, az = adv.pos.x, adv.pos.y, adv.pos.z
  local out = {}
  for _, u in ipairs(df.global.world.units.active) do
    if u ~= adv and dfhack.units.isAlive(u) then
      local dx, dy, dz = u.pos.x - ax, u.pos.y - ay, u.pos.z - az
      local dist = math.max(math.abs(dx), math.abs(dy))
      if dist <= radius and math.abs(dz) <= Z_RADIUS then
        local hostile = dfhack.units.isDanger(u) and true or false
        if not only_hostile or hostile then
          out[#out + 1] = {
            name = utf(dfhack.units.getReadableName(u)),
            race = guard(function() return utf(dfhack.units.getRaceReadableNameById(u.race)) end, nil),
            distance = dist,
            direction = direction(dx, dy, dz),
            pos = { x = as_number(u.pos.x), y = as_number(u.pos.y), z = as_number(u.pos.z) },
            kind = unit_kind(u),
            hostile = hostile,
            visible = (dfhack.units.isVisible(u) and not dfhack.units.isHidden(u)) and true or false,
            great_danger = dfhack.units.isGreatDanger(u) and true or false,
          }
        end
      end
    end
  end
  table.sort(out, function(a, b) return a.distance < b.distance end)
  while #out > cap do table.remove(out) end
  return out
end

local function threats_group()
  local hostiles = units_lean(true, CAP_HOSTILES)
  -- nível derivado: none (nada) < low (hostil longe) < medium (hostil <= 10 tiles)
  -- < high (great danger <= 10 tiles). Sem "break": great danger ganha sempre.
  local close, great = false, false
  for _, h in ipairs(hostiles) do
    if h.distance <= 10 then
      close = true
      if h.great_danger then great = true end
    end
  end
  local level = 'none'
  if #hostiles > 0 then
    level = close and (great and 'high' or 'medium') or 'low'
  end
  return { count = #hostiles, level = level, hostiles = hostiles }
end

local function biome_name(pos)
  local ok, name = pcall(function()
    local rx, ry = dfhack.maps.getTileBiomeRgn(pos)
    local bt = dfhack.maps.getBiomeType(rx, ry)
    local n = df.biome_type[bt]
    if type(n) == 'string' then return n:lower():gsub('_', ' ') end
    return nil
  end)
  if ok and type(name) == 'string' and name ~= '' then return name end
  return nil
end

local function tile_name(pos)
  local ok, name = pcall(function()
    local tt = dfhack.maps.getTileType(pos)
    local n = tt ~= nil and df.tiletype[tt] or nil
    return type(n) == 'string' and n or nil
  end)
  if ok and type(name) == 'string' and name ~= '' then return name end
  return nil
end

local function name_to_string(n)
  if type(n) == 'string' then return utf(n) end
  if n == nil then return nil end
  local ok, s = pcall(function()
    return dfhack.translation.translateName(n, true)
  end)
  if ok and type(s) == 'string' and s ~= '' then return utf(s) end
  return nil
end

local function site_name()
  local ok, site = pcall(function()
    return dfhack.world.getCurrentSite and dfhack.world.getCurrentSite() or nil
  end)
  if not ok or not site then return nil, nil end
  local type_name = nil
  local ok2, t = pcall(function() return df.site_type[site.type] end)
  if ok2 and type(t) == 'string' then type_name = t end
  return name_to_string(C.safe(site, 'name')), type_name
end

local function location_group(u)
  local pos = u.pos
  local where, surroundings = nil, nil
  local ok = pcall(function()
    local desig = dfhack.maps.getTileFlags(pos)
    local bits = desig and desig.bits or nil
    if bits and bits.subterranean then
      where = 'underground'
    elseif bits and bits.outside then
      where = 'outdoors'
    elseif bits then
      where = 'indoors'
    end
  end)
  if not ok then where = nil end
  local biome = biome_name(pos)
  local parts = {}
  if where then parts[#parts + 1] = where end
  if biome then parts[#parts + 1] = biome end
  if #parts > 0 then surroundings = table.concat(parts, ', ') end
  local site, site_type = site_name()
  local world = name_to_string(C.safe(C.safe(df.global.world, 'world_data'), 'name'))
  return {
    x = pos.x, y = pos.y, z = pos.z,
    world = world,
    region = biome,
    site = site,
    site_type = site_type,
    surroundings = surroundings,
    tile = tile_name(pos),
    -- destino de viagem não é exposto pelas estruturas do adventure mode: null
    travel_destination = nil,
  }
end

local function snapshot_metrics()
  local u = adventurer_unit()
  return {
    health_injuries = guard(function()
      return health_injuries_group(u, wounds_list(u))
    end, { hp_current = nil, hp_max = nil, wounds = {}, wound_count = 0,
           pain = nil, status = nil, status_conditions = {} }),
    needs_state = guard(function()
      return needs_state_group(u)
    end, { hunger = nil, thirst = nil, sleep = nil, stress_level = nil }),
    inventory = guard(function()
      return inventory_group(u)
    end, { items = {}, item_count = 0, total_weight_kg = nil, capacity_kg = nil,
           equipped_weapon = nil, equipped_armor = {} }),
    threats = guard(function()
      return threats_group()
    end, { count = 0, level = nil, hostiles = {} }),
    location = guard(function()
      return location_group(u)
    end, { x = nil, y = nil, z = nil, world = nil, region = nil, site = nil,
           site_type = nil, surroundings = nil, tile = nil, travel_destination = nil }),
    game_clock = guard(function()
      return state_date()
    end, { year = nil, tick = nil, month = nil, day = nil, season = nil, hour = nil,
           date_text = nil }),
    units_nearby = guard(function()
      local units = units_lean(false, CAP_UNITS)
      return { count = #units, units = units }
    end, { count = 0, units = {} }),
    -- o jogo não escreve o gamelog: o df_bridge.py preenche este grupo com o
    -- tail do gamelog.txt (por isso sai vazio daqui)
    events = {},
  }
end

-- ---------- despacho ----------
local result
if what == 'adventurer' then
  result = state_adventurer()
elseif what == 'threats' then
  result = { radius = radius, threats = scan_units(true) }
elseif what == 'units' then
  result = { radius = radius, units = scan_units(false) }
elseif what == 'inventory' then
  result = state_inventory()
elseif what == 'date' then
  result = state_date()
elseif what == 'snapshot' then
  result = { mode = 'adventure', adventurer = state_adventurer(), metrics = snapshot_metrics() }
elseif what == 'all' then
  result = {
    date = state_date(),
    adventurer = state_adventurer(),
    threats = scan_units(true),
  }
else
  qerror('uso: dfb-state <adventurer|threats|units|inventory|date|all|snapshot> [raio]')
end

print(C.json.encode(result))
