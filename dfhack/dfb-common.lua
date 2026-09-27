-- dfb-common.lua — helpers compartilhados dos scripts dfb-* do copiloto.
--@ module = true
-- Carregue com:  local C = reqscript('dfb-common')   (as funções globais viram exports)
-- NÃO imprime nada — os scripts que o usam é que imprimem o JSON único que o
-- df_bridge.py (run_game_script) extrai.
-- Licença: MIT (dwarf-fortress-agent-skill).

json = require('json')

-- CP437/strings do jogo → UTF-8 (nomes, descrições)
function utf(s)
  return s and dfhack.df2utf(s) or nil
end

-- pilha de foco da tela atual, como lista de strings
function focus_strings()
  local t = {}
  for _, f in ipairs(dfhack.gui.getFocusStrings(dfhack.gui.getCurViewscreen())) do
    t[#t + 1] = f
  end
  return t
end

-- leitura DEFENSIVA de campo: structs mudam entre versões do DF (counters2 já
-- crashou um probe). Retorna `default` (nil por padrão) em vez de estourar.
function safe(obj, field, default)
  if obj == nil then return default end
  local ok, v = pcall(function() return obj[field] end)
  if ok and v ~= nil then return v end
  return default
end

-- aborta com mensagem clara se o DFHack foi compilado p/ outra versão do DF.
-- getDFVersion() = "v0.53.14 linux64 STEAM"; getCompiledDFVersion() = "53.14"
-- (formatos diferentes — comparar por SUBSTRING, nunca por igualdade).
function version_guard()
  local run = dfhack.getDFVersion() or ''
  local built = dfhack.getCompiledDFVersion and dfhack.getCompiledDFVersion() or nil
  if built and built ~= '' and not run:find(built, 1, true) then
    qerror(('versao incompativel: DF "%s" vs DFHack compilado p/ "%s"')
           :format(run, built))
  end
end

-- aventureiro atual ou erro amigável (adventure mode obrigatório)
function adventurer()
  local u = dfhack.world.getAdventurer and dfhack.world.getAdventurer()
  if not u then qerror('sem aventureiro (o jogo esta em adventure mode?)') end
  return u
end
