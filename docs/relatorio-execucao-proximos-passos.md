# Relatório de execução — «PROXIMOS-PASSOS.md do início ao fim + site RPG medieval»

**Data do relatório:** 2026-10-12 21:40 (-03) · **Projecto:** dwarf-fortress-game (`dwarf-fortress-agent-skill`) · **HEAD de referência:** `cdf0c18` («fix(campaign,web): CoALA v3, nomes de skill e peers do motion-plus (#1)»).
**Âmbito:** registar o que está **FEITO**, **VERIFICADO**, **EM CURSO** e **PENDENTE** no esforço que percorre o `PROXIMOS-PASSOS.md` (Passos 1–19) e constrói o site RPG medieval da web UI, mais a auditoria UX/UI e a verificação independente.

**Como ler as marcas:**

- **[V]** — **VERIFICADO no disco** durante a escrita deste relatório, com comando citado na evidência (leituras apenas: `ls`, `git status/diff`, `python3 -m json.tool`, `grep`, `wc`). Nenhuma suíte de testes foi executada.
- **[A]** — **AFIRMADO POR UMA PEÇA** (relato do esforço ou documento do projecto), sem verificação independente refeita aqui.
- **[G]** — **GUESS/por confirmar**: não foi possível confirmar no disco; reportado como «verificação concluída, sem achados críticos» por indicação do esforço.

**Aviso de snapshot:** a árvore está a ser trabalhada **em paralelo** por várias frentes enquanto este relatório é escrito (o `git status` cresceu de 26 para 40 entradas entre duas leituras no mesmo minuto). Os factos marcados [V] são um instantâneo a 2026-10-12 21:40; a baseline é **limpa em `cdf0c18`** e o trabalho do esforço aparece como alterações por confirmar (working tree suja).

---

## Tabela-resumo — O que foi pedido vs estado

| # | Item pedido | Estado | Marca |
|---|---|---|---|
| 1 | Passo 1 — Gold ≥20 casos/papel (MIMO + JEV) | **Parcial**: parte MIMO FEITA (72→112 cenários); parte JEV (≥20/pergunta JEV-A/M/D/R) EM CURSO | [V] cenários · [A] resto |
| 2 | Passo 2 — Re-rodar arena 3+ iterações, decisão por significância | EM CURSO (preparação: política de promoção, fix do timeout do datagen, configs 3+/3+, estimativas `--dry-run`) | [A] |
| 3 | Passo 3 — A/B shadow em produção | PENDENTE (precisa de sessão real do jogo) | [A] |
| 4 | Passo 4 — Skills de combate (prontidão, vantagem, fuga <60% HP) | EM CURSO | [V] módulo existe · [A] estado |
| 5 | Passo 5 — Movimento como macro-ação (MacroExecutor + StuckDetector) | EM CURSO (já parcialmente ligado ao `loop.py`) | [V] |
| 6 | Passo 6 — Evidência antes de abstain | PENDENTE (wiring; depende de libertação de donos) | [V] módulo escrito, não ligado |
| 7 | Passo 7 — Diálogos retrieváveis | EM CURSO (Onda 2 memória viva) | [V] módulos em edição |
| 8 | Passo 8 — Estado com histórico e anti-repetição | PENDENTE (wiring; donos) | [V] módulo escrito, não ligado |
| 9 | Passo 9 — Eventos como diário de bordo (append-only) | EM CURSO (Onda 2 memória viva) | [V] módulos em edição |
| 10 | Passo 10 — Shuffle + voto nas opções (JEV) | PENDENTE (wiring; donos) | [V] módulo escrito, não ligado |
| 11 | Passo 11 — Calibragem de limiares por risco | EM CURSO (tabela por classe, Wilson 95%, preliminar) | [A] |
| 12 | Passo 12 — Ciclo gerar → testar → criticar nos planos | PENDENTE (wiring; donos) | [V] módulos escritos, não ligados |
| 13 | Passo 13 — Planos com schema fixo, waypoints, effort dinâmico | PENDENTE (wiring; donos) | [V] módulos escritos, não ligados |
| 14 | Passo 14 — Treinar antes de caçar | PENDENTE (wiring; donos) | [A] |
| 15 | Passo 15 — Golden set em CI como gate | **FEITO** (gate obrigatório; guarda `if [ -f … ]` removida) | [V] |
| 16 | Passo 16 — Persistir stores em SQLite | **Quase FEITO**: grosso implementado; migração de arranque dos anéis em memória EM CURSO | [V] |
| 17 | Passo 17 — CI no GitHub (suíte + lint + web) | **FEITO** (existia desde `732c801`; lacunas de badge + gate fechadas); reparo menor EM CURSO | [V] |
| 18 | Passo 18 — Watchdog global de sessão | **FEITO** (módulo novo `agent/session_watchdog.py`, 793 linhas; 113 testes verdes = 47 novos + 66 H3); ressalvas: wiring pendente (snippet p/ `agent/loop._step_inner`) + coexistência com `agent/watchdog.py` H3 | [V] |
| 19 | Passo 19 — Cache de planos e tradução | EM CURSO (`agent/plan_cache.py`, `translate/cache.py`, `cache.py` novos/em edição) | [V] |
| 20 | Site RPG medieval (web UI) | EM CURSO em 4 frentes (Web-A fundação/tema, Web-B shell+nav+fixes UX, Web-C painéis aventura/objetivo/memória/inputs, Web-D painéis métricas/perguntas/decisões/ecrã/controlo + fix F.1.1.03) | [V] parcial (deps + `index.css`) · [A] frentes |
| 21 | Auditoria UX/UI de referência pré-redesign (skill `uxui-evaluator`) | **FEITA** (`docs/uxui-audit-ui-preredesign.json`) | [V] |
| 22 | Verificação independente (gold MIMO, spec design, gates vs baseline) | EM CURSO / parcial (resultados não arquivados em `docs/`) | [G] |

---

## FEITO E VERIFICADO

### 1. Gold MIMO (Passo 1, parte MIMO) — **[V]** contagens; **[G]** verificação independente

- `bench/cenarios/` passou de **72 → 112 cenários** (+40). **[V]**: contagem por ficheiro (`narracao` 20, `perguntas-pt` 20, `plano-caca` 20, `traducao` 20, `acao` 14, `memoria` 8, `recuperacao` 10 = **112**).
- Os 40 novos são `narr-11..20`, `perg-13..20`, `plano-11..20`, `trad-09..20` — **20 exatos por papel** nos 4 papéis MIMO. **[V]**: ids inspecionados nos JSON (ex.: `narr-11-oportunismo-presa`…`narr-20-injecao-nos-eventos`, `plano-11-presa-tame-unica`…`plano-20-injecao-nas-hints`).
- **Só adições, 0 removidas.** **[V]**: `git diff --numstat -- bench/cenarios/` → `70 0`, `56 0`, `70 0`, `84 0` (narracao/perguntas-pt/plano-caca/traducao; segunda coluna = 0 remoções em todas).
- **Verificação por verificador independente:** não há relatório/arquivo do verificador em `docs/` (procura por «verificador»/«sem achados» vazia). Reportado como **«verificação concluída, sem achados críticos» [G]** — sem artefacto no disco para confirmar.
- **Pendente de revisão humana (rótulos do gold):** regra de rótulo para gado tame (`plano-11`), rota curta por ferida (`plano-13`), direção **pt→en sem cenários**, paráfrases vs strings reais de `_band_description`. **[A]**

### 2. Especificação de design RPG medieval — **[V]** estrutura; **[A]** números internos

- `docs/design-rpg-medieval.md` — **326 linhas [V]** (`wc -l`). Especificação normativa para a web UI (shell + 9 painéis do `web/src/panels/registry.tsx` + overlays).
- Conceito **«A Fortaleza-Vigia»** (pedra/forja/pergaminho/runas) — **[V]** (§1 no cabeçalho do ficheiro).
- Conteúdo declarado **[A]** (mencionado no documento; não recalculado aqui): **14 tokens semânticos** por modo em **OKLCH + hex** (29 ocorrências de «oklch» **[V]**), rácios WCAG calculados (pior caso de texto **4.52:1**), tipografia **Cinzel / Inter / IBM Plex Mono** self-host (`@fontsource`), **10 glifos runa** (§4.3, SVG inline), **11 padrões de superfície** (§5.x, níveis 0–2), **5 tokens de motion** (`snap/ui/gentle/lively/ambient`, §6, com apoio `stagger/travel/inView`), **18 anti-padrões**, e mapeamento superfície→componente do **registry Motion UI** (tabela shadcn/Motion UI → tokens, §2).
- Princípio reitor registado: «pele sobre um dashboard de operação» — densidade operacional preservada. **[V]** (citação direta do ficheiro).

### 3. Auditoria UX/UI de referência PRÉ-redesign — **[V]**

- `docs/uxui-audit-ui-preredesign.json` — JSON válido (`python3 -m json.tool` OK). Skill `uxui-evaluator` **sem API key** → `api_enriched: false` **[V]** (`api_note` presente).
- **Score 68/100, banda `good`** **[V]**; **8 findings** **[V]**: **0 critical · 2 warning · 6 sugestões**, exatamente como registado:
  - **F.1.1.03** (warning, *Mental Model*): Enter na «Resposta livre» multiline — `event.preventDefault()` em `QuestionCard.tsx:355-363`, helper text `QuestionCard.tsx:369` não documenta o comportamento (2º Enter abre confirmação).
  - **F.2.2.03** (warning, *Hick's Law*): navegação dupla `md+` — `PanelTabs.tsx:60-63`/`91-114` + `AppShell.tsx:294-295`.
  - Sugestões: **I.2.2.02** (Fitts, alvos ~30px), **F.1.1.04** (Miller, `NAV_GROUPS` em mobile), **H.1.1.01 ×3** (Perceivable), **C.1.1.01** (Consistency, freshness manual).
- **5 strengths** **[V]** (ex.: C.1.4.01 *Error Prevention* — estados de erro/empty tratados a sério).

### 4. CI (Passos 17 + 15) — **[V]** lacunas fechadas; **[A]** resultado do verificador externo

- O CI já existia **completo** desde o commit `732c801`: `.github/workflows/{ci,release,smoke}.yml` **[V]** (3 ficheiros) + `docs/ci.md` **[V]**.
- **Badge CI no `README.md:3`** — **[V]** (linha lida: badge `frederico-kluser/dwarf-fortress-agent-skill/actions/workflows/ci.yml/badge.svg`). O resultado «HTTP 200 *CI - passing*» é **[A]** (a verificação remota do badge expirou durante este relatório; não reconfirmada).
- **Gate `python3 scripts/golden.py check` obrigatório:** **[V]** — o passo «Golden set (offline)» está em `.github/workflows/ci.yml:97` **sem** a guarda `if [ -f scripts/golden.py ]` (procura negativa confirmada); comentário de disciplina em `ci.yml:90-96` (bless --reason, hold-out só no fim).
- **Asserções anti-continue-on-error** no contrato do CI: `tests/e2e/test_smoke_scripts.py:2056-2058` **[V]** — `assert "python3 scripts/golden.py check" in runs` e `assert "[ -f scripts/golden.py ]" not in runs`. **Reparo em curso** (alinhamento `docs/ci.md:40` vs `ci.yml:92`, e o teste de contrato em revisão) — **[A]**.
- Disciplina golden documentada em `docs/ci.md` (tabela de comandos inclui `python3 scripts/golden.py check`) **[V]**; verificador independente do gate «PASS 6/6» é **[A]** (sem artefacto arquivado).
- `docs/ci.md` regista ainda que o `MOTION_TOKEN` é **opcional** e único segredo do job web — coerente com `ci.yml:6,132,142-148` **[V]**.

### 5. Baseline e recon — **[V]** descobertas estruturais

- **Baseline pré-mudança registada:** pytest 3599 passed/2 skipped · `golden.py check` OK (123 casos: dev 101, hold-out 22 pinado) · `dfhack/test_bridge.py` 17 OK · `dfhack/test_snapshot.py` 42 OK · web lint/typecheck/build OK · Python 3.14.7, node 24, prompt-builder v0.1.1 — **[A]** (estado do esforço; não re-executado aqui por ordem de não correr suítes).
- Ambiente: `~/.secrets` com `MOTION_TOKEN`/`MOTION_API_KEY`/`OPENROUTER_API_KEY` (só nomes); `UXUI_API_KEY` e `SOD_*` ausentes; campanhas reais `caca-2`, `campanha-caca`, `campanha-e2e` — campanhas **[V]** (`ls campanhas/`), segredos **[A]** (não inspecionados por princípio).
- **Descoberta central [V]:** os módulos dos Passos 4–14 **já estão escritos** desde `732c801` — `game/readiness.py`, `agent/combat_policy.py`, `agent/evidence.py`, `agent/movement.py`, `agent/history.py`, `jev/voting.py`, `campaign/dialogue.py`, `campaign/logbook.py` (todos presentes no disco, dezenas de KB) — mas **não ligados ao loop**: `agent/loop.py` e `agent/planning.py` não importam `combat_policy`/`evidence`/`readiness`/`history`/`voting` (grep vazio nesses nomes; só `_combat_history` local em `loop.py:2126`). **O trabalho real = wiring + extensão.**
- Mapa de propriedade passo→ficheiros com **10 grupos de conflito** — **[A]**.
- Exceção parcial **[V]**: `loop.py` já importa `MacroExecutor` (`agent/executor.py`) e `StuckDetector` (`agent/stuck.py`) — Passo 5 em pleno wiring.

### 6. Passo 16 (stores) — **[V]** o grosso

- `src/system_one_dwarf/app/persist.py` implementa `campanhas/<slug>/runtime.sqlite` **[V]** (`RUNTIME_DB_NAME = "runtime.sqlite"`), **write-through** (cada mutação do runtime é persistida; dict com *hook* por edição in-place) **[V]**, **scrub de segredos** (`scrub_payload` com máscara de `campaign/keys.mask_key`) **[V]**, ficheiro corrompido → `runtime.sqlite.corrupt-N` **[V]**. Permissões 0700/0600 e **70 testes** são **[A]**.
- Lacuna real em implementação: **migração de arranque dos anéis em memória** — **[A]** (em curso).

### 7. Passo 18 — Watchdog global de sessão — **[V]** (concluído DEPOIS do snapshot deste relatório)

- **Módulo novo `src/system_one_dwarf/agent/session_watchdog.py`** (793 linhas): `GlobalSessionWatchdog` (:486) com escada `replan → ask_human → kill_switch` (`LADDER` :93), janela N (default 15 min) + janela de graça (2 min); **nunca dispara em `paused`/kill-switch** (`_suppressed` :738); evento estruturado por callback (`SessionStallEvent.as_stuck_event` :444) para a biblioteca de padrões por API; config `SOD_WATCHDOG_*` em `agent/config.py` (`WATCHDOG_ENV_KEYS` :265, `watchdog_settings` + meta da campanha :317).
- **Testes [V]:** `.venv/bin/python -m pytest -q tests/session_watchdog` → **47 passed**; com `tests/agent/test_session_watchdog.py` → **113 passed** (47 novos + 66 do módulo H3 antigo; regressão limpa). Os **4 critérios da definição de pronto** cobertos: deteta em ≤N min · zero disparos em `paused`/kill-switch · escada gradual · janela de graça. (Corridas de teste executadas na conclusão do Passo 18, pós-snapshot — exceção pontual à regra «só leituras» do cabeçalho, aplicável só a esta entrada.)
- **Ressalva — WIRING pendente:** o `GlobalSessionWatchdog` ainda não está ligado ao loop. Snippet pronto (donos ativos) para `agent/loop._step_inner` (~`loop.py:902`): `GlobalSessionWatchdog(...)` com `on_event` → `publish("watchdog_alert")` + `append_pattern_event`, `activity_source` → `SessionActivity.from_sources`, `wd.observe(state, control)`; graça re-armada por `reset()`/`acknowledge()`/`set_answer_listener(cid, wd.note_human_answer)` (hook já existe em `app/state.py:202`).
- **Nota de reconciliação:** coexistem DOIS módulos de watchdog em `agent/` — `agent/watchdog.py` (H3: `SessionWatchdog`+`LoopHealth`, escada `replan→force_explore→pause_and_ask`, 66 testes, ligado a `app/state.py`, **INTACTO por decisão**) e o novo `agent/session_watchdog.py` (Passo 18). Consolidar as duas escadas num só módulo num PR próprio é **decisão do dono**.

---

## EM CURSO (à data do snapshot; confirmado no disco onde marcado [V])

- **Gold JEV ≥20 casos/pergunta** (JEV-A/M/D/R) — **[A]**; indício [V]: `evals/{jev-a-action,jev-d-doubt,jev-m-memory,jev-r-retrieval,injection-evals}.json` estão modificados na working tree (espelho/evals em movimento).
- **Preparação da arena (Passo 2):** política de promoção p<0,05 (`docs/prompt-builder-evolucao.md` §8) **[V]** (secção existe), fix do timeout de 180s do datagen, configs 3+ iterações/repetições (§9) **[V]**, estimativas `--dry-run` (§10) **[V]**; execução em si **[A]**.
- **Passo 4** — retrato de prontidão + candidatos por vantagem + fuga <60% HP: `agent/combat_policy.py` e `game/readiness.py` escritos **[V]**; ligação/em curso **[A]**.
- **Passo 5** — `MacroExecutor` + `StuckDetector` já importados por `loop.py` **[V]**; `agent/movement_actions.py` (312 linhas, novo) **[V]**; `agent/executor.py`, `agent/candidates.py`, `agent/stuck.py` modificados **[V]**.
- **Onda 2 memória viva (Passos 7+9):** `campaign/dialogue.py`, `campaign/logbook.py`, `campaign/recall.py` (769 linhas), `campaign/memory.py`, `campaign/extraction.py` (novo) em criação/edição **[V]**.
- **Passo 11 calibragem preliminar:** `scripts/calibrate.py` presente **[V]**; tabela por classe + Wilson 95% **[A]**.
- **Passo 19 cache:** `agent/plan_cache.py` (215 linhas), `translate/cache.py` (106), `src/system_one_dwarf/cache.py` (398, novo) **[V]**.
- **Site RPG medieval, 4 frentes:** observado no disco **[V]** — `web/package.json` troca fontes para `@fontsource/cinzel` + `@fontsource-variable/inter` + `@fontsource/ibm-plex-mono` (coerente com o spec) e `web/src/index.css` (+386/-linhas em diff — tokens `@theme`/`:root`/`.dark` em curso, Web-A). Web-B/C/D (shell+nav, painéis, fix F.1.1.03 em `QuestionCard.tsx`) **[A]** — sem alterações ainda visíveis nesses ficheiros neste snapshot.
- **Verificadores** (gold MIMO, spec design) e **verificação intermédia da árvore** — **[A]/[G]**.

---

## PENDENTE (por dependência / prazo / dinheiro)

| Item | Razão da pendência |
|---|---|
| **Espelho golden** (`bench/gerar.py --sync-evals` → `build` → `freeze-holdout` → `bless --reason` → `check`) | Depende do **gold JEV** estar fechado; fazer antes é desperdiçar bless. `evals/*.json` já em movimento — atenção a não blessar cedo. |
| **Corrida REAL da arena** com decisão por significância (p<0,05 / flipRate) | **Dinheiro:** teto diário US$20; estimativas `--dry-run` primeiro (orçamento já calculado em `docs/prompt-builder-evolucao.md` §10). |
| **A/B shadow em produção** (Passo 3) | Precisa de **sessão real do jogo** (DFHack/live); indisponível em CI. |
| **Passos 6/8/10/12/13/14** (wiring em `loop.py`/`planning.py`) | Dependem de **libertação de donos** (grupos de conflito do mapa de propriedade). |
| **Polish/motion transversal + auditoria `uxui-evaluator` PÓS-redesign + ronda de reparos** | Depende do site estar montado (frentes Web-A..D) e do fix de F.1.1.03. |
| **Verificador global final** (pytest + golden + dfhack + web gates vs baseline) | Último gate antes de fechar; não correr enquanto as frentes estiverem a mexer na árvore. |
| **Tag SemVer / changelog / commit / push** | Ficam para **aprovação do dono** (não executados neste esforço). |
| **Revisão humana dos rótulos do gold** (gado tame `plano-11`, rota curta por ferida `plano-13`, direção pt→en, paráfrases vs `_band_description`) | Requer **juízo humano** sobre semântica de jogo; não automatizável com segurança. |

---

## RISCOS / NOTAS

1. **Corridas pagas:** a arena e o JEV consomem orçamento real (teto diário **US$20**; `OPENROUTER_API_KEY` presente em `~/.secrets`). Toda a estimativa deve sair primeiro por `--dry-run` (`estimate.high`); promover só com p<0,05 ou flipRate — regra escrita em `docs/prompt-builder-evolucao.md` §8. Um `bless` prematuro do golden invalida o pin do hold-out (22 casos).
2. **PII em diálogos do Passo 7:** os diálogos retrieváveis (`campaign/dialogue.py`/`recall.py`) podem capturar fala de NPCs/estados do utilizador; aplicar o mesmo `scrub_payload`/máscara de segredos do `persist.py` aos registos de memória e confirmar que `~/.secrets` nunca chega aos dumps (o scrub já existe para o store — estender aos anéis em memória na migração de arranque).
3. **`MOTION_TOKEN` opcional no job web do CI:** sem ele o `npm ci` completo falha (401 do registo Motion+); por desenho o job degrada e é o **único** segredo aceite no workflow (contrato verificado no teste: `set(re.findall(r"secrets\.([A-Z_]+)") == {"MOTION_TOKEN"}`). Não acrescentar outros segredos ao CI.
4. **`UXUI_API_KEY` ausente:** a auditoria sai `api_enriched: false` (sem citações/impacto de negócio enriquecidos); a auditoria PÓS-redesign terá a mesma limitação a menos que a chave exista.
5. **Árvore volátil / conflitos:** 10 grupos de conflito passo→ficheiros e 4 frentes web em paralelo; o `git status` mudou durante a escrita deste relatório (26→40 entradas). O verificador global final só é válido com a árvore **quieta**; diffs de `evals/` durante o gold JEV não devem ser misturados com mudanças de código.
6. **Gate golden é obrigatório e sensível a prompt-drift:** qualquer alteração intencional de prompt exige `bless --reason` registado; mudanças não intencionais devem falhar o CI (é exatamente o comportamento desejado).
7. **Pendências de revisão humana do gold** (rótulos) podem alterar `expected`/`rubric` e forçar re-bless — fazer a revisão **antes** do espelho golden, não depois.

---

## Comandos de evidência usados (leituras apenas)

```text
git status --short · git diff --numstat -- bench/cenarios/ · git diff --stat · git log --oneline -8
wc -l docs/design-rpg-medieval.md · python3 -m json.tool docs/uxui-audit-ui-preredesign.json
grep (cabeçalhos, ids de cenários, imports de loop.py/planning.py, MOTION_TOKEN, golden gate)
sed (README.md:1-6, ci.yml:85-100, tests/e2e/test_smoke_scripts.py:2050-2065, docs/ci.md:35-45, PROXIMOS-PASSOS.md)
ls (docs/, bench/cenarios/, campanhas/, .github/workflows/, src/system_one_dwarf/agent/)
```

Nenhuma suíte de testes foi executada; nenhum ficheiro do projecto foi alterado para além deste relatório.
