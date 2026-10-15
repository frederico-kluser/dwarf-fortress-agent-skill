# CI, releases e smoke

Como o System One Dwarf valida o que muda, publica versões e deteta regressões de
DF/DFHack. Resolve `PROBLEMAS-E-SOLUCOES.md` **H4** (CI + SemVer) e **H7** (smoke
automatizado) e os riscos de `ARQUITETURA.md` §9 "Processo" e "Navegação".

| Workflow | Quando corre | Onde | Precisa de segredos? | Para quê |
|---|---|---|---|---|
| `.github/workflows/ci.yml` | `push` a `main`, `pull_request`, manual, `workflow_call` | runners do GitHub | **não** (só `MOTION_TOKEN`, opcional — ver "Web") | testes, lint, **golden set**, segredos, smoke **offline** |
| `.github/workflows/release.yml` | tag `vX.Y.Z[-pre]` | runners do GitHub | **não** (`GITHUB_TOKEN` efémero) | mesmos testes + GitHub Release com as notas do `CHANGELOG.md` |
| `.github/workflows/smoke.yml` | manual e semanal (seg. 05:17 UTC) | **runner auto-hospedado** `dwarf-fortress` | **não** | smoke **ao vivo** contra o jogo real (opcional) |

Regras comuns: `permissions: contents: read` (a release eleva só o job que publica),
`concurrency` que cancela runs superados, `timeout-minutes` em todos os jobs, nada de
`${{ }}` dentro de `run:` (tudo passa por `env:`), actions fixadas por **tag major**
e `persist-credentials: false` no checkout. Nenhum workflow corre código de PR num
runner auto-hospedado.

## 1. CI (`ci.yml`)

| Job | Passos | Falha quando |
|---|---|---|
| `python` (3.12 e 3.14) | `pip install -r requirements.txt` · instala `lua5.4` · `compileall` · `pytest -q` · `dfhack/test_bridge.py` · `dfhack/test_snapshot.py` · `luac -p dfhack/*.lua` · `patterns_check.py` · `df_smoke.py --offline` · `scripts/golden.py check` (gate do Passo 15, obrigatório) | regressão de código/ponte/Lua, biblioteca de padrões inválida, fluxo do smoke partido, **deriva de prompt/modelo** |
| `web` | `npm ci --dry-run` · `oxlint` · (com `MOTION_TOKEN`) `npm ci` + `typecheck` + `build` | lock fora de sincronia, erros de lint/tipos/build |
| `shell` | `bash -n` (+ `shellcheck` **não bloqueante**) | erro de sintaxe nos `.sh` |
| `secrets` | `git grep` de padrões de chaves nos ficheiros versionados | possível segredo versionado (o valor nunca é impresso) |
| `gate` | agrega o estado dos jobs anteriores | qualquer job ≠ `success` — é o único check a exigir em rulesets |

### Equivalentes locais (da raiz do repositório)

| Passo do CI | Comando local |
|---|---|
| dependências | `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt` |
| sintaxe Python | `python3 -m compileall -q src scripts dfhack tests` |
| testes | `.venv/bin/python -m pytest -q -p no:cacheprovider` |
| ponte | `python3 dfhack/test_bridge.py` |
| snapshot | `python3 dfhack/test_snapshot.py` |
| sintaxe Lua | `luac5.4 -p dfhack/*.lua` (ou `luac`) |
| biblioteca de padrões | `python3 scripts/smoke/patterns_check.py` |
| smoke offline | `python3 scripts/smoke/df_smoke.py --offline --report smoke-offline.json` (o mesmo comando do CI: o JSON na raiz é o relatório transitório que o workflow faz upload como artefacto; `.runtime/` reserva-se ao runtime do `sod` — PIDs/logs) |
| golden set | `python3 scripts/golden.py check` |
| web: lock | `cd web && npm ci --dry-run --ignore-scripts` |
| web: lint | `cd web && npm run lint` |
| web: tipos + build | `cd web && npm ci && npm run typecheck && npm run build` (precisa de `MOTION_TOKEN`) |
| shell | `for f in install.sh scripts/df.sh scripts/sod.sh dfhack/install_dfhack_linux.sh; do bash -n "$f"; done` e `shellcheck -S warning …` |
| segredos | `PATTERN="$(sed -n "s/^ *PATTERN='\(.*\)'$/\1/p" .github/workflows/ci.yml)"; git grep -InE "$PATTERN" -- . ':(exclude)web/package-lock.json' \| cut -d: -f1,2` (sem saída = limpo) |

### Web e o token Motion+

A UI importa `motion-plus` (`@motionplus/core`), servido pelo registo privado
`api.motion.dev` — o `web/.npmrc` usa `${MOTION_TOKEN}` e, sem token, o `npm ci`
completo falha com 401. Para não exigir segredos em PRs, o job `web` é **graduado**:

* sempre: `npm ci --dry-run` (valida `package.json` ↔ lockfile sem rede de pacotes) e
  `oxlint` (autónomo, na versão do lockfile);
* só com o segredo de repositório `MOTION_TOKEN` (PRs do próprio repositório e
  `main`; PRs de forks nunca o recebem): `npm ci`, `npm run typecheck`, `npm run build`.

Sem o segredo o job passa com um `::notice::` a dizê-lo. Para ativar os gates
completos: *Settings → Secrets and variables → Actions → New repository secret*
`MOTION_TOKEN`. Recomendação de fundo: o `motion-plus` só tem 2 imports
(`AnimateNumber`, `splitText`); substituí-los por uma alternativa pública tornaria a
UI compilável por qualquer contribuidor e o CI completo sem segredos.

### Golden set (Passo 15): o gate e a disciplina

O passo `python3 scripts/golden.py check` é **gate obrigatório** do job `python`
(em PR, `main` e também na `release`, que reutiliza o `ci.yml`): offline,
determinístico, ~1 s, **sem chaves de API**. Falha (exit 1) com `prompt-drift`
(o pedido reconstruído pelos *builders* atuais já não dá o hash de
`prompts.lock.json`), `build-drift`/`seed-drift` (as fontes já não regeneram os
casos versionados), pin do hold-out adulterado (`HOLDOUT.sha256`) ou etiqueta
que já não corresponde à regra. Se o script faltar, o passo falha — um gate de
regressão de prompts não se pode saltar.

**Equivalentes locais** (da raiz, sem rede):

```bash
python3 scripts/golden.py check          # o mesmo gate do CI
python3 scripts/golden.py tune-view --role JEV-A --requests   # só casos dev
```

**Disciplina** (resumo da disciplina anti-overfit; o detalhe — composição,
regras e limiares — está em `docs/calibracao.md` §14–15):

1. Afina-se com `tune-view`/`show` — **só** casos `dev`; o hold-out nunca é
   aberto durante o tuning.
2. `check` depois de cada alteração; **`bless --reason "…"` só para mudanças
   *intencionais* de prompt** — re-calcula os hashes e regista uma linha em
   `evals/golden/CHANGELOG.md` (sem linha, a mudança não é legítima). O `bless`
   nunca "lava" etiquetas: recusa-se se houver qualquer outro problema.
3. Avaliação contra o modelo real (`run --online`, gasta tokens): `--split dev`
   enquanto se afina; **`--split holdout` só no fim**, quando já não há nada a
   afinar (o relatório do hold-out traz métricas agregadas, nunca linhas por caso).
4. Mudar o conjunto de casos ou o hold-out exige `build` + `freeze-holdout
   --reason` (decisão explícita, registada) — nunca `bless`.

O CI **nunca** corre `run --online`: os gates são todos offline por desenho, e
é por isso que o workflow não precisa de chaves de API.

## 2. Release (`release.yml`)

### Política SemVer

* **`0.y.z` enquanto o smoke E2E ao vivo não estiver automatizado e estável** (H7):
  mudanças incompatíveis sobem `y`, correções sobem `z`. A tag `v1.0.0` (ou superior)
  é bloqueada pelo workflow a menos que a variável de repositório
  `SOD_ALLOW_STABLE_RELEASE=true` exista — é uma decisão explícita, não um acidente.
* **Commits Convencionais** (`tipo(escopo)!: descrição`, ≤72 carateres) alimentam o
  `CHANGELOG.md` (Keep a Changelog): `feat` → *Added*, `fix` → *Fixed*, `perf`/`refactor`
  → *Changed*, `!`/`BREAKING CHANGE` → bump maior.
* **A versão é um cálculo, não um palpite**: `pyproject.toml`,
  `src/system_one_dwarf/__init__.py` (o `version` do `GET /api/health`) e
  `web/package.json` mudam **no PR da release**; a tag tem de coincidir (o workflow
  verifica).
* **Tag legada `v3.6.0`**: pertence ao skill-pack anterior ao rebrand (`git describe`
  devolve-a por ser a única tag). A aplicação começa em `v0.1.0`; depois de criada, o
  `git describe` e o `oss-version.py` da skill `opensource-project` partem dela.

### Cortar uma release

1. `main` verde no CI (o job `gate`).
2. PR `chore(release): vX.Y.Z`: atualizar as três versões; no `CHANGELOG.md` renomear o
   conteúdo de `## [Unreleased]` para `## [X.Y.Z] - AAAA-MM-DD`, abrir um
   `## [Unreleased]` novo e atualizar os links de rodapé. Os testes que afirmam a versão
   (`0.1.0`) têm de ser atualizados no mesmo PR.
3. Depois do merge: `git tag -a vX.Y.Z -m "chore(release): vX.Y.Z"` e
   `git push origin vX.Y.Z`.
4. O workflow corre os testes, valida tag ↔ versões ↔ política, extrai a secção
   `## [X.Y.Z]` como notas e cria a GitHub Release (`--prerelease` se a tag tiver
   sufixo `-rc.1`, …; um pré-release sem secção própria usa `[Unreleased]`). Voltar a
   empurrar a mesma tag atualiza as notas (idempotente).

Falhas típicas: *"CHANGELOG.md não tem a secção"* (esqueceu-se o passo 2) · *"tem
versão X mas a tag é Y"* (versões não bumpadas) · *"política 0.y.z"* (tag ≥ 1.0.0).

## 3. Smoke

`scripts/smoke/df_smoke.py` automatiza a prova mínima do H7: **ponte alcançável →
snapshot cumpre o contrato → biblioteca de padrões lê-se → movida com prova
(posição antes→depois)**. `scripts/smoke/patterns_check.py` é o alarme de deriva de UI.

### `df_smoke.py --offline` (CI; predefinido)

Corre o `dfhack/df_bridge.py` **real** (como subprocesso, o caminho do executor)
contra um servidor RPC falso em loopback e um jogo simulado — determinístico, ~2 s,
sem jogo, sem chaves.

| Verificação | O que prova |
|---|---|
| `bridge_status` | `status` alcança o RPC (handshake completo) |
| `dsl_parse` | `act batch --dry-run` valida os 8 verbos do DSL e rejeita um passo inválido |
| `snapshot_schema` | `schema_version`, `ts` ISO, 8 grupos de métricas com "nulls, nunca ausentes", `adventurer.pos`, coerência `location` ↔ `pos`, sem `warnings` |
| `snapshot_consumer` | `GameState.from_dict` (o consumidor real) lê a mesma posição |
| `pattern_library` | a biblioteca passa o `patterns_check` |
| `screen_patterns` | no mapa, `p-move-or-goto` casa com o ecrã |
| `movement_proof` | a posição (x, y) **muda** depois de `goto`/`move` |
| `proof_controls` | controlos negativos: parede em todas as direções, overlay `dungeonmode/Help` a engolir o input (D18) e "sem aventureiro" são **detetados** — prova que a prova pode falhar |

`--scenario blocked|frozen|no-adventurer` corre o fluxo contra esses estados (valida
o próprio smoke): sai `1`, `1` e `3` respetivamente.

### `df_smoke.py --live` (runner auto-hospedado, opcional)

`df up --no-web` → espera RPC (+ `/api/health` quando é preciso criar/carregar
aventura) → aventureiro → `snapshot` → `patterns` → fecho de overlays → movida com
prova → `df stop` (só pára o que arrancou; o jogo que já corria é deixado como está).

| Opção | Efeito |
|---|---|
| `--df-path DIR` | DF (predef.: `$DF_PATH` ou `~/Games/dwarf-fortress-53.16`) |
| `--adventure-id SAVE` | se não houver aventureiro, carrega-o pela API |
| `--new-adventure` | se não houver aventureiro, cria mundo `quick` + personagem pela API (minutos) |
| `--no-start` / `--keep-running` | não corre `df up`/`df stop` · não pára no fim |
| `--expect-pattern ID` | padrão(ões) que têm de casar no mapa (predef.: `p-move-or-goto`) |
| `--timeout S` / `--startup-timeout S` | orçamento global · espera pelo arranque |
| `--report FICHEIRO` / `--json` | relatório JSON (atómico) · JSON no stdout |

Notas: o smoke faz **movidas reais** (poucos tiles) — usa um save dedicado; sem
aventureiro e sem `--adventure-id`/`--new-adventure` sai `3`; a campanha
`smoke-df` é criada no backend isolado (`SOD_CAMPANHAS_DIR` temporário) e o runtime em
`DF_RUNTIME_DIR` temporário. Variáveis com `KEY`/`TOKEN`/`SECRET`/`PASSWORD` são
retiradas do ambiente dos processos filhos; o relatório redige segredos e usa `~`.

### Códigos de saída e relatório

| Código | Significado |
|---|---|
| `0` | tudo passou |
| `1` | regressão: alguma verificação falhou |
| `2` | uso inválido |
| `3` | pré-condição em falta (DF/DFHack em falta, sem aventureiro, não é Linux, RPC em baixo) |
| `4` | tempo esgotado (arranque ou orçamento global) |
| `130` | interrompido (Ctrl-C/SIGTERM; o jogo é parado) |

O relatório (`schema: 1`) tem `mode`, `ok`, `exit_code`, `environment`, `warnings`,
`checks[]` (`name`, `status` ∈ `pass|fail|skip|precondition|timeout`, `detail`,
`duration_ms`, `data`) e `summary`. Diagnósticos da prova de movimento
(`movement_proof.data.diagnosis`): `input_swallowed` (overlay a engolir o input — ver
`overlays.tutorial_help`), `modal_open` (menu aberto), `blocked_or_frozen` (parede ou
tempo congelado), `no_position`.

### Runner auto-hospedado

1. Linux x86_64 com DF 53.16 + DFHack 53.16-r2 (`bash install.sh`), `python3` com
   `venv`, `bash`, `xvfb-run` (ou uma sessão X real; o jogo precisa de `DISPLAY`).
2. Registar o runner com a etiqueta `dwarf-fortress` (`./config.sh --labels
   dwarf-fortress`) numa conta/máquina dedicada ao jogo.
3. Variáveis de repositório (não são segredos): `DF_PATH` (se não for o predefinido) e
   `SMOKE_ADVENTURE_ID` (save a reutilizar; sem ela cada corrida cria mundo e
   personagem novos e deixa saves em `data/save/`).
4. **Nunca** ligar este workflow a `pull_request`: um runner auto-hospedado executaria
   código de forks. Em *Settings → Actions* exigir aprovação para colaboradores
   externos.
5. Mudar a frequência: `cron` em `smoke.yml` (`"17 5 * * *"` = diário).

### Alerta de deriva de UI (`patterns_check.py`)

```bash
python3 scripts/smoke/patterns_check.py                       # só a biblioteca (CI)
python3 scripts/smoke/patterns_check.py --screen ecra.json    # `act screen --json`, snapshot ou texto
python3 scripts/smoke/patterns_check.py --live --expect p-title-create-world
```

Valida schema, ids únicos, `focus`/`text_markers`, `action`/`outcome_markers`, regexes,
proveniência (`proven`) e a coerência de `stats` com `patterns` e `stuck_events` (o
anel do `StuckDetector` guarda no máximo 200). Com um ecrã, classifica cada padrão:
`match` (o navegador ofereceria este padrão), `focus_only`/`text_only` (metade casa —
suspeito de deriva) e `miss`. `--expect` transforma-o num gate: num ecrã conhecido o
padrão esperado **tem** de casar (exit `1`, com os marcadores em falta).

## 4. Manutenção

* **Fixar actions por SHA** (OpenSSF Scorecard): `bash scripts/oss-pin-actions.sh`
  da skill `opensource-project` converte `owner/repo@vN` em `owner/repo@<sha> # vN`.
* **Dependabot**: acrescentar `.github/dependabot.yml` com o ecossistema
  `github-actions` (não é gerido por estes workflows).
* **Novo passo no CI**: adicionar ao job `python` (ou novo job + `needs` no `gate`),
  documentar o equivalente local na tabela acima e manter `${{ }}` fora de `run:`.
* **Recalibrar o smoke**: constantes nomeadas no topo de `df_smoke.py`
  (`MOVE_*`, `SETTLE_MAX_STEPS`, `STARTUP_TIMEOUT_S`, `ADVENTURE_TIMEOUT_S`, …).
* **Manter o DFHack falso alinhado**: `FakeGame.respond` (em `df_smoke.py`) espelha as
  formas de resposta dos `dfb-*.lua`. Se a ponte passar a chamar outro comando ou
  argumentos (ex.: `dfb-nav route3d`), atualizá-lo — até lá o smoke offline falha com
  `CR_NOT_FOUND`, que é o aviso certo: o contrato da ponte mudou.
* **Testes destes ficheiros**: `tests/e2e/test_smoke_scripts.py` cobre os dois scripts
  (offline, sem jogo), a estrutura/segurança dos workflows, os scripts embebidos do
  `release.yml`, a varredura de segredos, o `CHANGELOG.md` e os comandos deste documento.
