#!/usr/bin/env python3
"""Testes offline do bloco `combat` (prontidão de combate) do `dfb-state.lua`.

Sem o jogo: um stub do ambiente DFHack em Lua (mesma abordagem do `test_snapshot.py`) corre o
script REAL com um interpretador `lua`/`lua5.4`/`luajit` do PATH e salta limpo quando não há
nenhum. Cobre as contas do bloco (skills efetivas, arma/escudo/armadura, sangue, ferimentos),
a regra "campo ilegível = nil, nada inventado" (API em falta, API que levanta, tipos lixo) e
a aditividade (chaves antigas do `adventurer` e os 8 grupos de métricas ficam como estavam).
Fecha o circuito: o JSON fiel do Lua entra no `CombatReadiness.from_dict` do Python.

Rode:  python3 dfhack/test_readiness.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "src")
STATE_LUA = os.path.join(HERE, "dfb-state.lua")
LUA = shutil.which("lua5.4") or shutil.which("lua") or shutil.which("luajit")
LUAC = shutil.which("luac5.4") or shutil.which("luac")

if SRC not in sys.path:
    sys.path.insert(0, SRC)
try:
    from system_one_dwarf.game.readiness import CombatReadiness
except ImportError:                                            # pragma: no cover
    CombatReadiness = None

# --------------------------------------------------------------------------------------
# Harness: stub do ambiente DFHack + cenário + verificações (+ dump JSON fiel opcional).
# `df.job_skill`/`item_type`/... são enums bidirecionais como no DFHack (nome<->número).
# --------------------------------------------------------------------------------------
HARNESS = r"""
local CAPTURED = nil
local UNIT, ACTIVE, DEFS = nil, {}, {}

local JOB = { MELEE_COMBAT = 99, DODGING = 105, SHIELD = 45, ARMOR = 46, WRESTLING = 101,
              GRASP_STRIKE = 103, STANCE_STRIKE = 104, SPEAR = 43, SWORD = 39,
              CROSSBOW = 44, MISC_WEAPON = 106 }
local NOUNS = { SPEAR = 'Spearman', SWORD = 'Swordsman', CROSSBOW = 'Crossbowman',
                GRASP_STRIKE = 'Striker', MELEE_COMBAT = 'Fighter' }
local function enum(map)
  local e = {}
  for name, id in pairs(map) do e[name] = id; e[id] = name end
  return e
end

df = {
  global = { cur_year = 100, cur_year_tick = 300000,
             world = { units = { active = ACTIVE }, world_data = {} } },
  inv_item_role_type = { [0] = 'NONE', [1] = 'Hauled', [2] = 'Weapon', [3] = 'Worn' },
  biome_type = { [0] = 'FOREST' }, tiletype = { [0] = 'Grass' }, site_type = { [0] = 'DarkFortress' },
  job_skill = enum(JOB),
  physical_attribute_type = enum({ STRENGTH = 0, AGILITY = 1, TOUGHNESS = 2, ENDURANCE = 3 }),
  mental_attribute_type = enum({ WILLPOWER = 0, SPATIAL_SENSE = 1, KINESTHETIC_SENSE = 2 }),
  item_type = enum({ WEAPON = 1, SHIELD = 2, ARMOR = 3, PANTS = 4, HELM = 5, GLOVES = 6,
                     SHOES = 7, QUIVER = 8 }),
}
df.job_skill.attrs = {}
for token, id in pairs(JOB) do df.job_skill.attrs[id] = { caption_noun = NOUNS[token] } end

-- item do jogo: `item:getType()`/`item:getSubtype()` + `.subtype` = definição (itemdef)
local function item(desc, itype, subtype)
  local it = { desc = desc, itype = itype, subtype_id = subtype, material = 'iron',
               stack_size = 1, flags = { bits = { weight_computed = true } },
               weight = { whole = 1, fraction = 0 } }
  it.getType = function(self) return df.item_type[self.itype] end
  it.getSubtype = function(self) return self.subtype_id end
  it.subtype = DEFS[subtype]
  return it
end
local function worn(it) return { mode = 3, item = it } end
local function held(it) return { mode = 2, item = it } end

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
    getEffectiveSkill = function(u, id)
      local token = df.job_skill[id]
      if token == nil then error('skill invalida') end
      return (u.skills or {})[token] or 0
    end,
    getPhysicalAttrValue = function(u, id) return (u.attrs or {})[df.physical_attribute_type[id]] end,
    getMentalAttrValue = function(u, id) return (u.attrs or {})[df.mental_attribute_type[id]] end,
  },
  items = {
    getReadableDescription = function(i) return i.desc end,
    getSubtypeDef = function(item_type, subtype) return DEFS[subtype] end,
  },
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
chunk(arg[2] or 'adventurer', '25')
assert(CAPTURED ~= nil, 'o script nao produziu JSON')

-- ---------- verificações ----------
local ADV = CAPTURED.adventurer ~= nil and CAPTURED.adventurer or CAPTURED
__CHECKS__

__DUMP__
io.write('OK\n')
"""

# Encoder Lua->JSON fiel ao do DFHack no essencial: chave `nil` não existe e tabela vazia
# sai como `[]`. É o JSON que o df_bridge.py recebe do jogo.
LUA_DUMP = r"""
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

# Aventureiro competente: lança de ferro, escudo, armadura de couro + elmo + túnica (roupa).
RICH = r"""
DEFS[7] = { skill_melee = JOB.SPEAR, skill_ranged = -1 }
DEFS[11] = { armorlevel = 1 }                   -- armadura de couro
DEFS[12] = { armorlevel = 0 }                   -- túnica: roupa, não é armadura
DEFS[13] = { armorlevel = 2 }                   -- elmo
DEFS[20] = { }                                  -- escudo
UNIT = {
  pos = { x = 10, y = 20, z = 30 }, desc = 'Urist McTest',
  skills = { MELEE_COMBAT = 5, DODGING = 3, SHIELD = 2, ARMOR = 4, WRESTLING = 1,
             GRASP_STRIKE = 2, STANCE_STRIKE = 1, SPEAR = 4 },
  attrs = { STRENGTH = 1200, AGILITY = 900, TOUGHNESS = 1100, ENDURANCE = 1000,
            SPATIAL_SENSE = 950, KINESTHETIC_SENSE = 1000, WILLPOWER = 1100 },
  counters = { pain = 12, winded = 0, stunned = 0, nausea = 0, dizziness = 0, webbed = 0,
               unconscious = 0 },
  counters2 = { exhaustion = 100, hunger_timer = 80000, thirst_timer = 30000,
                sleepiness_timer = 60000 },
  body = {
    blood_count = 4100, blood_max = 5000,
    wounds = {
      { parts = { { body_part_id = 3, surface_perc = 40, bleeding = 12 } },
        flags = { bits = {} }, pain = 5 },
      { parts = { { body_part_id = 4, surface_perc = 10, bleeding = 0 } },
        flags = { bits = {} }, pain = 2 },
      { parts = { { body_part_id = 5, surface_perc = 20, bleeding = 0 },
                  { body_part_id = 6, surface_perc = 20, bleeding = 3 } },
        flags = { bits = {} }, pain = 3 },
    },
  },
  inventory = {
    held(item('iron spear', 'WEAPON', 7)),
    held(item('copper buckler', 'SHIELD', 20)),
    worn(item('leather armor', 'ARMOR', 11)),
    worn(item('cloth tunic', 'ARMOR', 12)),
    worn(item('iron helm', 'HELM', 13)),
  },
}
ACTIVE[1] = UNIT
"""

RICH_CHECKS = r"""
local a = ADV
local c = a.combat
assert(c ~= nil, 'bloco combat ausente')
assert(c.weapon.armed == true)
assert(c.weapon.name == 'iron spear')
assert(c.weapon.skill_token == 'SPEAR' and c.weapon.skill_noun == 'Spearman')
assert(c.weapon.skill == 4 and c.weapon.ranged == false)
assert(c.skills.fighter == 5 and c.skills.dodger == 3 and c.skills.shield_user == 2)
assert(c.skills.armor_user == 4 and c.skills.wrestler == 1)
assert(c.skills.striker == 2 and c.skills.kicker == 1)
assert(c.attributes.strength == 1200 and c.attributes.agility == 900)
assert(c.attributes.toughness == 1100 and c.attributes.endurance == 1000)
assert(c.attributes.spatial_sense == 950 and c.attributes.kinesthetic_sense == 1000)
assert(c.attributes.willpower == 1100)
assert(math.abs(c.condition.blood_ratio - 0.82) < 1e-9)
assert(c.condition.pain == 12 and c.condition.exhaustion == 100)
assert(c.condition.winded == 0 and c.condition.stunned == 0 and c.condition.nausea == 0)
assert(c.condition.dizziness == 0 and c.condition.webbed == 0)
assert(c.condition.wound_count == 3)
assert(c.condition.bleeding == 2, 'feridas a sangrar: ' .. tostring(c.condition.bleeding))
assert(c.armor.pieces == 2, 'tunica nao conta: ' .. tostring(c.armor.pieces))
assert(c.armor.slots == 2 and c.armor.shield == true)
"""

LEGACY_KEYS = ("name", "race", "profession", "pos", "health", "needs", "kills", "stress_category")


def _harness(scenario, checks, dump=False):
    return (HARNESS.replace("__SCENARIO__", scenario).replace("__CHECKS__", checks)
            .replace("__DUMP__", LUA_DUMP if dump else ""))


@unittest.skipUnless(LUA, "sem interpretador lua/lua5.4/luajit no PATH")
class LuaCombatBlockTests(unittest.TestCase):
    """O dfb-state.lua REAL contra um ambiente DFHack simulado (sem o jogo)."""

    def run_lua(self, scenario, checks, mode="adventurer", dump=False):
        work = tempfile.mkdtemp(prefix="dfcombat-")
        self.addCleanup(shutil.rmtree, work, True)
        path = os.path.join(work, "harness.lua")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(_harness(scenario, checks, dump))
        return subprocess.run([LUA, path, STATE_LUA, mode], capture_output=True, text=True,
                              cwd=work, timeout=30)

    def ok(self, scenario, checks, **kw):
        proc = self.run_lua(scenario, checks, **kw)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK", proc.stdout)
        return proc

    def payload(self, scenario, mode="adventurer"):
        proc = self.ok(scenario, "", mode=mode, dump=True)
        lines = [l for l in proc.stdout.splitlines() if l.startswith("JSON:")]
        self.assertTrue(lines, proc.stdout)
        data = json.loads(lines[0][len("JSON:"):])
        return data["adventurer"] if "adventurer" in data else data

    # ---------------------------------------------------------------- caminho feliz
    def test_rich_unit_full_block(self):
        self.ok(RICH, RICH_CHECKS)

    def test_snapshot_mode_carries_the_block_and_the_eight_groups(self):
        checks = RICH_CHECKS + r"""
for _, k in ipairs({'health_injuries', 'needs_state', 'inventory', 'threats', 'location',
                    'game_clock', 'units_nearby', 'events'}) do
  assert(CAPTURED.metrics[k] ~= nil, 'grupo ausente: ' .. k)
end
local n = 0
for _ in pairs(CAPTURED.metrics) do n = n + 1 end
assert(n == 8, 'o contrato tem exatamente 8 grupos, nao ' .. n)
assert(CAPTURED.metrics.inventory.equipped_weapon == 'iron spear')
"""
        self.ok(RICH, checks, mode="snapshot")

    def test_legacy_adventurer_keys_are_unchanged(self):
        checks = r"""
local a = ADV
assert(a.name == 'Urist McTest' and a.race == 'Dwarf' and a.profession == 'Adventurer')
assert(a.pos.x == 10 and a.pos.y == 20 and a.pos.z == 30)
assert(a.health.blood == 4100 and a.health.blood_max == 5000 and a.health.wounds == 3)
assert(a.health.pain == 12 and a.health.exhaustion == 100 and a.health.winded == 0)
assert(a.needs.hunger_timer == 80000 and a.needs.hungry == true and a.needs.drowsy == true)
assert(a.kills == 7 and a.stress_category == 5)
"""
        self.ok(RICH, checks)
        keys = set(self.payload(RICH))
        self.assertEqual(keys, set(LEGACY_KEYS) | {"combat"})

    def test_modes_all_and_adventurer_dispatch(self):
        self.ok(RICH, "assert(CAPTURED.adventurer.combat.weapon.name == 'iron spear')", mode="all")
        self.ok(RICH, "assert(CAPTURED.adventurer == nil and CAPTURED.combat.weapon.name == 'iron spear')")

    # ---------------------------------------------------------------- arma / mãos
    def test_unarmed_uses_the_punch_skill(self):
        scenario = RICH.replace("    held(item('iron spear', 'WEAPON', 7)),\n", "")
        scenario = scenario.replace("    held(item('copper buckler', 'SHIELD', 20)),\n", "")
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.weapon.armed == false)
assert(c.weapon.name == nil)
assert(c.weapon.skill_token == 'GRASP_STRIKE' and c.weapon.skill_noun == 'Striker')
assert(c.weapon.skill == 2, 'nivel do soco: ' .. tostring(c.weapon.skill))
assert(c.armor.shield == false)
""")

    def test_shield_in_hand_is_not_mistaken_for_the_weapon(self):
        scenario = RICH.replace(
            "    held(item('iron spear', 'WEAPON', 7)),\n    held(item('copper buckler', 'SHIELD', 20)),\n",
            "    held(item('copper buckler', 'SHIELD', 20)),\n    held(item('iron spear', 'WEAPON', 7)),\n")
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.weapon.name == 'iron spear' and c.weapon.armed == true)
assert(c.armor.shield == true)
""")

    def test_only_a_shield_in_hand_means_unarmed(self):
        scenario = RICH.replace("    held(item('iron spear', 'WEAPON', 7)),\n", "")
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.weapon.armed == false and c.armor.shield == true)
""")

    def test_ranged_weapon_reports_the_ranged_skill(self):
        scenario = RICH.replace("DEFS[7] = { skill_melee = JOB.SPEAR, skill_ranged = -1 }",
                                "DEFS[7] = { skill_melee = JOB.MISC_WEAPON, skill_ranged = JOB.CROSSBOW }")
        scenario = scenario.replace("SPEAR = 4 }", "SPEAR = 4, CROSSBOW = 9, MISC_WEAPON = 1 }")
        self.ok(scenario, r"""
local w = ADV.combat.weapon
assert(w.ranged == true and w.skill_token == 'CROSSBOW' and w.skill_noun == 'Crossbowman')
assert(w.skill == 9)
""")

    def test_weapon_without_a_skill_still_reports_the_name(self):
        scenario = RICH.replace("DEFS[7] = { skill_melee = JOB.SPEAR, skill_ranged = -1 }",
                                "DEFS[7] = { skill_melee = -1, skill_ranged = -1 }")
        self.ok(scenario, r"""
local w = ADV.combat.weapon
assert(w.armed == true and w.name == 'iron spear')
assert(w.skill_token == nil and w.skill == nil and w.ranged == nil)
""")

    # ---------------------------------------------------------------- armadura
    def test_clothing_does_not_count_and_slots_are_distinct(self):
        scenario = RICH.replace(
            "    worn(item('iron helm', 'HELM', 13)),\n",
            "    worn(item('iron helm', 'HELM', 13)),\n    worn(item('iron mail shirt', 'ARMOR', 11)),\n"
            "    worn(item('cloth trousers', 'PANTS', 12)),\n    worn(item('quiver', 'QUIVER', 12)),\n")
        self.ok(scenario, r"""
local a = ADV.combat.armor
assert(a.pieces == 3, 'pecas: ' .. tostring(a.pieces))      -- couro, elmo, camisa de malha
assert(a.slots == 2, 'espacos: ' .. tostring(a.slots))      -- body + head (corpo repetido)
""")

    def test_unreadable_armorlevel_still_counts_the_piece(self):
        scenario = RICH.replace("DEFS[11] = { armorlevel = 1 }", "DEFS[11] = { }")
        self.ok(scenario, "assert(ADV.combat.armor.pieces == 2)")

    # ---------------------------------------------------------------- nada inventado
    def test_unit_with_only_a_position_does_not_crash_and_invents_nothing(self):
        scenario = "UNIT = { pos = { x = 88, y = 72, z = 5 } }\nACTIVE[1] = UNIT\n"
        self.ok(scenario, r"""
local a = ADV
assert(a.pos.x == 88 and a.pos.z == 5 and a.kills == 7)
local c = a.combat
if c ~= nil then
  assert(c.weapon == nil or c.weapon.armed == nil, 'armed inventado sem inventario')
  assert(c.armor == nil, 'armadura inventada sem inventario')
  assert(c.condition == nil or (c.condition.blood_ratio == nil and c.condition.pain == nil))
  assert(c.condition == nil or c.condition.wound_count == nil)
end
""")
        c = self.payload(scenario).get("combat") or {}
        self.assertFalse(CombatReadiness.from_dict(c).armed is False)

    def test_missing_skill_api_keeps_everything_else(self):
        scenario = RICH + "dfhack.units.getEffectiveSkill = nil\n"
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.skills == nil, 'skills inventadas')
assert(c.weapon.name == 'iron spear' and c.weapon.skill_token == 'SPEAR' and c.weapon.skill == nil)
assert(c.attributes.strength == 1200 and c.condition.pain == 12 and c.armor.pieces == 2)
""")

    def test_raising_skill_api_is_contained(self):
        scenario = RICH + "dfhack.units.getEffectiveSkill = function() error('boom') end\n"
        self.ok(scenario, r"""
local c = ADV.combat
assert(c ~= nil and c.skills == nil and c.weapon.skill == nil)
assert(c.condition.blood_ratio ~= nil and c.attributes.agility == 900)
""")

    def test_missing_attribute_api(self):
        scenario = RICH + ("dfhack.units.getPhysicalAttrValue = nil\n"
                           "dfhack.units.getMentalAttrValue = nil\n")
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.attributes == nil and c.skills.fighter == 5)
""")

    def test_unreadable_item_types_make_armed_and_armor_unknown_not_false(self):
        scenario = RICH + "df.item_type = nil\n"
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.weapon == nil or c.weapon.armed == nil, 'armed adivinhado: ' .. tostring(c.weapon and c.weapon.armed))
assert(c.armor == nil, 'armadura adivinhada')
assert(c.skills.fighter == 5 and c.condition.pain == 12)
""")

    def test_a_single_untyped_hand_item_keeps_armed_unknown(self):
        scenario = RICH.replace("    held(item('iron spear', 'WEAPON', 7)),\n",
                                "    held({ desc = 'mystery', mode = 2 }),\n")
        self.ok(scenario, r"""
local c = ADV.combat
assert(c.weapon == nil or c.weapon.armed == nil)
assert(c.armor == nil or c.armor.shield == true)
""")

    def test_garbage_counter_values_become_nil(self):
        scenario = RICH.replace(
            "counters = { pain = 12, winded = 0, stunned = 0, nausea = 0,",
            "counters = { pain = 'lots', winded = {}, stunned = true, nausea = 0,").replace(
            "counters2 = { exhaustion = 100,", "counters2 = { exhaustion = 'tired',")
        self.ok(scenario, r"""
local k = ADV.combat.condition
assert(k.pain == nil and k.winded == nil and k.stunned == nil and k.exhaustion == nil)
assert(k.nausea == 0 and k.blood_ratio ~= nil)
""")

    def test_blood_ratio_edges(self):
        zero_max = RICH.replace("blood_count = 4100, blood_max = 5000,", "blood_count = 10, blood_max = 0,")
        self.ok(zero_max, "assert(ADV.combat.condition.blood_ratio == nil)")
        over = RICH.replace("blood_count = 4100, blood_max = 5000,", "blood_count = 9000, blood_max = 5000,")
        self.ok(over, "assert(ADV.combat.condition.blood_ratio == 1)")
        empty = RICH.replace("blood_count = 4100, blood_max = 5000,", "blood_count = 0, blood_max = 5000,")
        self.ok(empty, "assert(ADV.combat.condition.blood_ratio == 0)")

    def test_no_wounds_table_means_unknown_wounds_not_zero(self):
        scenario = RICH.replace("    wounds = {", "    wounds_off = {")
        self.ok(scenario, r"""
local k = ADV.combat.condition
assert(k.wound_count == nil and k.bleeding == nil)
""")

    def test_healthy_unit_has_zero_wounds_and_zero_bleeding(self):
        scenario = RICH.replace("    wounds = {", "    wounds = {} , _ignored = {")
        self.ok(scenario, r"""
local k = ADV.combat.condition
assert(k.wound_count == 0 and k.bleeding == 0)
""")

    def test_inventory_scan_is_capped(self):
        extra = "".join("    worn(item('rag %d', 'ARMOR', 11)),\n" % i for i in range(200))
        scenario = RICH.replace("    worn(item('iron helm', 'HELM', 13)),\n",
                                "    worn(item('iron helm', 'HELM', 13)),\n" + extra)
        self.ok(scenario, r"""
local a = ADV.combat.armor
assert(a.pieces <= 60, 'o varrimento do inventario tem de ter teto: ' .. a.pieces)
""")

    # ---------------------------------------------------------------- ponta a ponta
    def test_faithful_json_round_trips_into_the_python_readiness(self):
        if CombatReadiness is None:
            self.skipTest("pacote system_one_dwarf indisponivel")
        payload = self.payload(RICH)
        combat = payload["combat"]
        self.assertEqual(sorted(combat), ["armor", "attributes", "condition", "skills", "weapon"])
        ready = CombatReadiness.from_dict(combat)
        self.assertEqual((ready.weapon_name, ready.weapon_skill_token, ready.weapon_skill, ready.armed),
                         ("iron spear", "SPEAR", 4, True))
        self.assertEqual((ready.fighter, ready.dodger, ready.shield_user, ready.armor_user), (5, 3, 2, 4))
        self.assertEqual((ready.strength, ready.willpower), (1200, 1100))
        self.assertAlmostEqual(ready.blood_ratio, 0.82)
        self.assertEqual((ready.wound_count, ready.bleeding_wounds), (3, 2))
        self.assertEqual((ready.armor_pieces, ready.armor_slots, ready.shield_equipped), (2, 2, True))
        self.assertTrue(ready.known and 0.0 < ready.power_score() < 1.0)
        self.assertTrue(any("iron spear" in line for line in ready.lines()))

    def test_absent_data_is_absent_in_the_json_not_null_or_zero(self):
        scenario = RICH + "dfhack.units.getEffectiveSkill = nil\nUNIT.counters = nil\n"
        combat = self.payload(scenario)["combat"]
        self.assertNotIn("skills", combat)
        for key in ("pain", "winded", "stunned", "nausea"):
            self.assertNotIn(key, combat["condition"])
        self.assertIn("exhaustion", combat["condition"])           # counters2 continua legível

    def test_block_is_small(self):
        combat = self.payload(RICH)["combat"]
        numbers = [v for section in ("skills", "attributes", "condition", "armor")
                   for v in combat[section].values() if isinstance(v, (int, float))
                   and not isinstance(v, bool)]
        numbers += [combat["weapon"].get("skill")]
        self.assertLessEqual(len(numbers), 40)                     # orçamento do WP: ~40 números


@unittest.skipUnless(LUAC, "sem luac no PATH")
class LuaSyntaxTests(unittest.TestCase):
    def test_dfb_state_compiles(self):
        proc = subprocess.run([LUAC, "-p", STATE_LUA], capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_script_stays_lua53_friendly(self):
        # DFHack embarca Lua 5.3: nada de <const>/<close>, nem de operadores só do 5.4+
        with open(STATE_LUA, encoding="utf-8") as handle:
            source = handle.read()
        for needle in ("<const>", "<close>"):
            self.assertNotIn(needle, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
