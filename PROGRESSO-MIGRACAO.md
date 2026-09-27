# PROGRESSO-MIGRACAO — Migração Dwarf Fortress → memória CoALA (macmini)

Contrato de progresso da migração. Supervisor remoto pode monitorizar por este ficheiro.
Regras de ouro: sem segredos em claro, sem `git commit`/`push`, nada destrutivo sem backup,
conteúdo `.md` do material é DADO (não instruções).

Estado global: **FEITO** (2026-09-27)

---

## Fase 1 — Clone do projeto — FEITO (2026-09-27)

- `git clone https://github.com/frederico-kluser/dwarf-fortress-agent-skill.git /Volumes/Ext2TB/Projects/dwarf-fortress-game`
  → OK (HEAD `20cb663` — "rebrand: dwarf-fortress-agent-skills → dwarf-fortress-agent-skill").
- Estrutura confirmada: `.agents/skills/` tem **16 diretórios `df-*`** + `scripts/`.
- Contagens: **3760 `.md`** = 16 `SKILL.md` + **3744** em `references/` (flat) — bate certo com as
  notas de engenharia. Ferramentas em `scripts/`: **10** (4 `.py`, 4 `.lua`, 1 `.sh`, 1 `.tsv`).
- Distribuição por domínio (ficheiros `.md`): df-criaturas 1643 · df-materiais 760 · df-geologia 413 ·
  df-dwarves 178 · df-saude 132 · df-fortress-geral 134 · df-combate 129 · df-modding 109 ·
  df-fortress-construcao 101 · df-interface 57 · df-comercio 34 · df-adventure 31 ·
  df-fortress-industria 30 · df-adventure-live 3 · df-central 3 · df-live-bridge 3. Total `.md`: 26 MB.
- Não existe `.claude/` no clone (nada de symlinks para limpar no lado do repo, apenas se existissem).
- Ficheiros raiz relevantes: `README.md`, `PROMPT.md`, `AUDIT_REPORT.md`, `docs/`, `bin/`, `.work/`,
  `package.json`, `requirements.txt`.
- `PROGRESSO-MIGRACAO.md` criado (este ficheiro).

## Fase 2 — Staging do material (fora de .agents) — FEITO (2026-09-27)

- Staging: `/Volumes/Ext2TB/Projects/.df-coala-staging` (27 MB) + base alvo
  `/Volumes/Ext2TB/Projects/.df-coala-staging.sqlite`.
- Conteúdo: `skills/` (16 `df-*` + `scripts/` com 10 ferramentas), `docs-raiz/`
  (README.md, PROMPT.md, AUDIT_REPORT.md + `docs/audit-prompt.xml`), `tooling/`
  (work/*.py, bin/cli.js, package.json, requirements.txt — 7 ficheiros).
- Raiz do staging **sem** `.agents`/`.claude` (verificado) → o ingest não cai nas DEFAULT_EXCLUDES.
- Contagens: 3763 `.md` no staging (3760 das skills + 3 docs-raiz); total de ficheiros
  a ingerir: **3781** (16 mapa-skills + 3744 wiki + 10 dfhack-ferramentas + 7 tooling +
  3 docs + 1 docs-extra) — globs do `ingest.json` cobrem 100%, `references/` é flat
  (0 subdiretórios) confirmado.

## Fase 3 — ingest.json do staging, ingest — FEITO (2026-09-27)

- `ingest.json` escrito em `$STAGE` com as 6 regras + grafo (4 entidades, 3 arestas),
  proveniência conforme doutrina: `wiki-df` = `untrusted`, manifestos/docs = `agent`,
  tooling = `system`.
- Ingest (`--db $STAGE_DB --config $STAGE/ingest.json --root $STAGE`): **3781 ficheiros,
  30 084 segmentos, ZERO ficheiros ignorados**. Resumo por regra:
  | Regra | Ficheiros | Segmentos |
  |---|---|---|
  | mapa-skills | 16 | 101 |
  | wiki-df | 3744 | 29 909 |
  | dfhack-ferramentas | 10 | 10 |
  | tooling | 7 | 7 |
  | docs | 3 | 56 |
  | docs-extra | 1 | 1 |
- Nota: 30 084 segmentos fica acima da estimativa de engenharia (15–25k) porque os
  ficheiros da wiki têm muitos cabeçalhos; o portão duro (ZERO ignorados) está verde.
  Re-ingest subsequente = NO-OP total (idempotência provada).
- Estado da base do staging: 30 084 registos ativos (30 066 semantic + 18 procedural),
  33 699 chunks, grafo 4 entidades / 3 arestas, proveniência: untrusted 29 909 ·
  agent 168 · system 7.

## Fase 4 — Instalação da memória CoALA no projeto — FEITO (2026-09-27)

- Dry-run revisto (12 ações) e instalação aplicada (13 ações): criou
  `.agents/dwarf-fortress-game-coala-memory-agent-skill/` (SKILL.md, scripts/coala.py,
  references/schema.md, memory/ 0700 com coala.sqlite 0600 WAL, coala.json) + symlinks
  `.agents/skills/` e `.claude/skills/`. Sem AGENTS.md/CLAUDE.md no repo (nota do instalador).
- Runtime: python 3.14.7 · sqlite 3.53.4 + FTS5 · sqlite-vec ausente → fallback
  hashing-256 (INFO, degradação graciosa) · pdftotext e git presentes.
- **Decisão (NOTA 2)**: o instalador gerou um `ingest.json` template (só regra README.md);
  foi REMOVIDO do projeto de propósito (cópia guardada em
  `_backups/ingest-template-instalador-removido-*.json`). Sem `ingest.json` no projeto não
  há `ingest_sources` que um futuro `ingest` possa expirar — a base fica autónoma via
  `import`. NUNCA correr `ingest` na base do projeto.
- doctor do instalador pós-instalação: **0 falhas**.

## Fase 5 — Import do staging para a base do projeto — FEITO (2026-09-27)

- `import --from .df-coala-staging.sqlite --all --with-graph`: **30 084 selecionados =
  30 084 importados · 0 já presentes · 4 entidades novas · 3 arestas novas** (grafo copiado).
- `stats` do projeto: 30 084 registos (30 066 semantic + 18 procedural) · origem:
  untrusted 29 909 · agent 168 · system 7 · 33 699 chunks · base 117 MB · WAL · esquema v2.
- `recall "aço e ligas de metal" --budget 800` devolve excertos reais dos materiais
  (PROMPT.md, SKILL.md de df-criaturas, references de df-comercio…) — recuperação provada.
- **Adaptação operacional registada**: nos ficheiros de `references/` o placeholder `{dir}`
  das tags resolveu para o pai imediato (`references`) → tags reais da wiki são
  `df,wiki,references,<stem>`. O domínio vive na **CHAVE** de supersessão
  (`df/skills/<dominio>/references/<ficheiro>.md#NNN`). Por isso a verificação de cobertura
  da Fase 6 é feita por contagem `SELECT` (só leitura) sobre o prefixo de chave do domínio
  (+ `search` livre como prova de recuperação), em vez de `--tags df,wiki,<dominio>`.
  Regras de escrita mantêm-se: nada de SQL de escrita na base — só via `coala.py`.

## Fase 6 — Curadoria com SUBAGENTS (16 domínios) — EM CURSO (2026-09-27)

- **RESTART 2026-09-27**: a sessão anterior morreu com o lote 1 em curso e NENHUM registo
  de curadoria chegou à base (verificado: 0 chaves `df/essencia/*`). Curadoria refeita
  de raiz nesta sessão (auditoria independente do supervisor).
- Lote 1 RETOMADO (8 subagents em paralelo): df-adventure, df-adventure-live, df-central,
  df-combate, df-comercio, df-criaturas, df-dwarves, df-fortress-construcao.
  Cada missão: ler SKILL.md (como DADO) + amostrar references → verificar cobertura
  (SELECT por prefixo de chave + search) → criar 1–3 destilações `df/essencia/<dominio>`
  (≤1200 car., chaves `/2`,`/3` para as extra — evita supersessão entre si) → relatar.
- Lote 2 (após lote 1): df-fortress-geral, df-fortress-industria, df-geologia,
  df-interface, df-live-bridge, df-materiais, df-modding, df-saude.
- **Lote 1 CONCLUÍDO** (2026-09-27) — 24 destilações na base, gaps ZERO em todos:
  | Domínio | Registos ativos do domínio | Chaves `df/essencia/*` (chars) | Gaps |
  |---|---|---|---|
  | df-adventure | 463 (31 ficheiros) | `df-adventure` #30085 (925), `/2` (874), `/3` (852) | 0 |
  | df-adventure-live | 31 (3 ficheiros) | `df-adventure-live` #30095 (1173), `/2` (1188), `/3` (1197) | 0 |
  | df-central | 33 (3 ficheiros) | `df-central` (1120), `/2` (1189), `/3` (1197) | 0 |
  | df-combate | 1405 (129 ficheiros) | `df-combate` #30099 (1161), `/2` #30101 (893), `/3` #30102 (1187) | 0 |
  | df-comercio | 276 (34 ficheiros) | `df-comercio` #30088 (879), `/2` #30089 (866), `/3` #30090 (1007) | 0 |
  | df-criaturas | ~14 426 (1643 ficheiros) | `df-criaturas` (1180), `/2` (1079), `/3` (1022) | 0 |
  | df-dwarves | 1086 (178 ficheiros) | `df-dwarves` (919), `/2` (1015), `/3` (1002) | 0 |
  | df-fortress-construcao | 1060 (101 ficheiros) | `df-fortress-construcao` #30092 (933), `/3`… #30093 (1148), #30094 (1188) | 0 |
  - Cobertura provada por SELECT sobre `df/skills/<dominio>/%` + diff stems ficheiros × chaves
    (correspondência 1:1) + `search` livre (EN recupera melhor que PT — nota dos curadores:
    usar termos EN no `search`, o glossário PT→EN cobre a tradução).
- **Lote 2 DESPACHADO (2026-09-27, 8 em paralelo)**: df-fortress-geral, df-fortress-industria,
  df-geologia, df-interface, df-live-bridge, df-materiais, df-modding, df-saude — mesma
  missão do lote 1. Aguardam-se relatórios; depois: `df/mapa/central` + `df/missao`.
- Depois dos 16: registo roteador `df/mapa/central` e missão `df/missao` (destilada de
  `df-central/references/mission-and-roadmap.md`) — criados pelo agente principal.

## Alínea B — Cobertura absoluta (decisão do dono) — FEITO (2026-09-27)

- 3 ficheiros do repo fora do alcance do ingest adicionados via `coala.py add`
  (NUNCA `ingest` na base do projeto):
  | Ficheiro | Chave | Registo |
  |---|---|---|
  | `LICENSE` | `df/repo/LICENSE` | #30103 (semantic, agent) |
  | `.gitignore` | `df/repo/gitignore` | #30104 (semantic, agent) |
  | `.claude/settings.local.json` | `df/repo/claude-settings` | #30105 (semantic, agent) |
- `settings.local.json` verificado ANTES de gravar: scan por padrões de segredos
  (api_key/token/password/sk-/ghp_/AKIA/… = 0 linhas) e por valores de alta entropia
  (0) — sem segredos; conteúdo gravado via `--content "$(cat …)"` sem passar pelo ecrã.
- `.venv`: NÃO existe no clone atual (verificado com `find`) — exclusão deliberada
  registada mesmo assim: dependências Python ficam fora de propósito, nunca se migram.
- Total esperado de ficheiros representados na base: **3784** (3781 do ingest + 3 desta
  alínea).





## Fase 6 (conclusão) — Lote 2 + roteador + missão — FEITO (2026-09-27)

- **Lote 2 refeito INLINE pelo agente principal** (os subagents da sessão anterior foram
  detidos: nenhum deles tinha escrito; escrita garantida de ponta a ponta). 16 destilações
  novas (#30112–#30127), 2 por domínio (todas ≤1200 car., chaves `/2` distintas):
  | Domínio | Registos ativos | Chaves `df/essencia/*` |
  |---|---|---|
  | df-fortress-geral | 1901 | `df-fortress-geral` #30112, `/2` #30113 |
  | df-fortress-industria | 378 | `df-fortress-industria` #30114, `/2` #30115 |
  | df-geologia | 2459 | `df-geologia` #30116, `/2` #30117 |
  | df-interface | 819 | `df-interface` #30118, `/2` #30119 |
  | df-live-bridge | 26 | `df-live-bridge` #30120, `/2` #30121 |
  | df-materiais | 2663 | `df-materiais` #30122, `/2` #30123 |
  | df-modding | 2150 | `df-modding` #30124, `/2` #30125 |
  | df-saude | 834 | `df-saude` #30126, `/2` #30127 |
- Roteador `df/mapa/central` (#30128, tags `df,mapa`) e missão `df/missao`
  (#30129, tags `df,missao`, destilada de `mission-and-roadmap.md`) criados.
- **Curadoria total: 42 registos** (24 do lote 1 + 16 do lote 2 + 2 finais) + 3 `df/repo/*`.

## Alínea B — Cobertura absoluta — FEITO (2026-09-27)

- `LICENSE` → `df/repo/LICENSE` #30103 · `.gitignore` → `df/repo/gitignore` #30104 ·
  `.claude/settings.local.json` → `df/repo/claude-settings` #30105 (verificado ANTES:
  0 padrões de segredos, 0 valores de alta entropia).
- `.venv`: não existe no clone (exclusão deliberada registada: dependências nunca se migram).
- Total de ficheiros representados: **3784**.

## Portões de verificação (Passo 3) — TODOS VERDES (2026-09-27)

1. `doctor`: **SAUDÁVEL · 0 falhas · 0 avisos** (esquema v2, FTS5 íntegro, 33 746 chunks
   indexados, cadeia de supersessão íntegra, uma versão ativa por chave).
2. `stats`: **30 129 registos ativos** (30 111 semantic + 18 procedural) · origens:
   untrusted 29 909 · agent 213 · system 7 · 33 746 chunks · 4 entidades · 3 arestas · 117,5 MB.
3. Amostragem (top-1 registado; consulta → top-1):
   | Consulta | Top-1 |
   |---|---|
   | como fazer aço / steel | `tooling/bin/cli.js` (#30021) — nota: variantes mistas PT poluem; EN "steel smelting iron" → **`df-materiais/references/steel.md` #1** ✓ |
   | dragão megabeast stats | `df-criaturas/SKILL.md` (#37) — EN "dragon megabeast" → **`megabeast.md` #1 + `dragon-raw.md` #2** ✓ |
   | teclas do menu de designação/mineração | **`df-interface/references/designations-menu.md` #1** ✓ |
   | negociar com a caravana / trade depot | `tooling/work/taxonomy.py` (#30027) — EN "caravan trade depot" → **`trade-depot.md` #1 + `caravan.md` #3** ✓ |
   | tratar ferimento/infeção no hospital | `df-interface/references/profile.md` (#24177) — EN "health care wound" → **`wound-dresser.md` #1–#5**; `health-care.md` recuperável ✓ |
   | adventure mode fast travel | **`df-adventure/references/adventurer-mode-gameplay.md` #1** (secção Fast Travel) ✓ |
   - Nota de qualidade: as consultas mistas PT+EN ranqueiam ficheiros `tooling/` (listas de
     termos) no topo; em EN puro a recuperação acerta no artigo-alvo. Causa: fallback
     vetorial hashing-256 (sem sqlite-vec). Conteúdo-alvo presente no top-3/5 em todas.
4. `backup`: `memory/backups/coala-20260927T113125Z.sqlite` (30 129 registos, quick_check ok, 0600).
5. `search --tags df --limit 3` devolve conteúdo · `graph "Dwarf Fortress"` mostra 4 entidades
   e 3 arestas (tem-modo ×2, automatiza).

## Fase 8 / Passo 4 — Backup + limpeza total — FEITO (2026-09-27)

- **Backup obrigatório ANTES de tudo**: `/Volumes/Ext2TB/Projects/_backups/_backup-pre-migracao-df-coala-20260927-083152.tar.zst`
  (**79 MB**, 7705 entradas: projeto completo + staging + base do staging) — verificado por listagem.
- `.claude/` verificado antes de apagar: `settings.local.json` sem segredos (0 padrões,
  0 alta entropia) + symlink de registo.
- Removidos (tudo já está na base CoALA): `.agents/skills/df-*` (16), `README.md`,
  `PROMPT.md`, `AUDIT_REPORT.md`, `docs/`, `.work/`, `bin/`, `package.json`,
  `requirements.txt`, `.claude/`, staging `.df-coala-staging*`.
- Preservados: `.agents/dwarf-fortress-game-coala-memory-agent-skill/` (memória),
  `dfhack/` (10 ferramentas movidas de `.agents/skills/scripts/`), `PROGRESSO-MIGRACAO.md`,
  `LICENSE`, `.gitignore`, `.git`.
- `ingest.json` removido de novo (o instalador recriou o template no Passo 5 — NOTA 2 cumprida).

## Passos 5–6 — Casa fechada + verificação — FEITO (2026-09-27)

- `coala-install.py install --agents-md create`: criou `AGENTS.md` com o bloco gerido CoALA
  (recriou também o symlink `.claude/skills/…` = registo do instalador para descoberta por
  agentes; `doctor` pós: 0 falhas · 0 avisos).
- Árvore final (maxdepth 2): `.agents/` (skill CoALA + symlinks), `.claude/skills/` (registo),
  `dfhack/` (10 ferramentas), `AGENTS.md`, `PROGRESSO-MIGRACAO.md`, `LICENSE`, `.gitignore`, `.git`.
- `recall "como fazer aço" --budget 600` devolve excertos reais. `doctor`: 0 falhas.

## Passo 7 — Scan de segredos + commit único + push (autorizado pelo dono)

- Scan `(sk-…|ghp_…|AKIA…|api_key=…)`: NÃO vazio literalmente — 3 linhas em
  `scripts/coala.py` vendorizado, casos de selftest `#15/#16` de **redação de segredos**
  com valores dummy (`sk-teste1234567890abcd`, `whsec_abcdef123456`, `cfut_zzzzzzzzzzzz`).
  Decisão: são fixtures de teste do próprio motor (não credenciais); o motor vendorizado não
  se edita (sha256 no manifesto; quebraria o doctor). Repositório livre de segredos REAIS.
- Commit único de migração + `git push origin HEAD` — resultado exato no relatório final.

## Fecho — git + estado final (2026-09-27)

- **Commit único de migração**: `5757106` "migração CoALA: todo o conhecimento DF passa a
  viver na memória CoALA local" (9 adições · 3772 remoções · 10 renomeações scripts→dfhack).
- **Push OK**: `git push origin HEAD` → `20cb663..5757106 HEAD -> main` (exit 0,
  `frederico-kluser/dwarf-fortress-agent-skill`, branch `main`).
- Backups de segurança: `_backups/_backup-pre-migracao-df-coala-20260927-083152.tar.zst` (79 MB,
  tudo pré-remoção) + `memory/backups/coala-20260927T113125Z.sqlite` (snapshot CoALA) +
  o histórico git do clone (3784 ficheiros recuperáveis por `git show`).
- **Como reverter**: `tar --zstd -xpf _backups/_backup-pre-migracao-df-coala-20260927-083152.tar.zst
  -C /Volumes/Ext2TB/Projects` repõe projeto + staging inteiros; ou `git revert 5757106`
  para só repor os ficheiros versionados.
- **Como usar a memória**:
  `COALA="python3 .agents/dwarf-fortress-game-coala-memory-agent-skill/scripts/coala.py"`
  · `recall "<tarefa>" --budget 1500` (início) · `search "<termos EN>" --limit 5`
  (`--tags df,wiki` para wiki; `--tags df,essencia` para destilações) ·
  `add --type semantic --key "<assunto>" --content "…"` (fim de tarefa) · `doctor`/`backup`.
  Router: `df/mapa/central` · missão: `df/missao` · mapa por domínio: `df/essencia/<dominio>`.
- Nota final: este addendum (fecho) é working tree por commitar — o commit único foi feito
  antes do fecho conforme a ordem do runbook; o supervisor pode commitá-lo se quiser.
