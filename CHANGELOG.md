# Changelog

Todas as alterações notáveis deste projeto ficam neste ficheiro. Formato
[Keep a Changelog](https://keepachangelog.com/pt-PT/1.1.0/); versões
[SemVer](https://semver.org/lang/pt-BR/) — **0.y.z** enquanto o smoke E2E ao vivo
não estiver automatizado e estável (política e fluxo de release em `docs/ci.md`).
As entradas derivam dos Commits Convencionais (`feat` → *Added*, `fix` → *Fixed*,
`!`/`BREAKING CHANGE` → versão maior). O workflow `release.yml` publica como notas da
GitHub Release a secção cuja versão coincide com a tag.

> O histórico anterior ao rebrand (skill-pack `dwarf-fortress-agent-skills`, tags até
> `v3.6.0`) não faz parte deste changelog: a aplicação System One Dwarf versiona-se a
> partir de `0.1.0` (`pyproject.toml`, `GET /api/health`).

## [Unreleased]

Zona de preparação das notas da próxima release: as correções da `ARQUITETURA.md`
(§8 "Mudanças recomendadas", numeradas como na tabela; §9 "Riscos / limites
conhecidos"; otimizações H1–H8 de `PROBLEMAS-E-SOLUCOES.md`). Escrito a partir do
documento de arquitetura — confirmar cada entrada contra os commits antes de cortar a
release (o `release.yml` só exige que exista a secção da versão).

### Added

- **Memória conversacional (§8 #01)** — diálogos transcritos (quem, quando, vetor,
  importância) com recuperação por recência + importância + similaridade, espaço por
  interlocutor e cartão de perfil.
- **Skills de combate nas decisões (§8 #02)** — retrato de prontidão (~10 números:
  skill efetiva da arma, Fighter/Dodger/Shield/Armor, atributos, dor, exaustão) para
  ataque/fuga, mira neck/head e limiar de recuo.
- **Escalada de evidência (§8 #03)** — com confiança baixa colhe-se mais estado e
  re-decide-se uma vez antes de pedir ajuda ao humano.
- **Estado com histórico e anti-repetição (§8 #07)** — "últimas decisões + resultado"
  e "falhados — não repetir", deteção de ações repetidas e ordem U-curve do estado.
- **Eventos como diário de bordo (§8 #08)** — append-only, com etiqueta de importância
  e recuperação por recência + importância + relevância; reflexões periódicas.
- **Treinar antes de caçar (§8 #11)** — skill da arma abaixo de `Proficient` prefere
  alvos de baixo risco ou treino.
- **Regressão de prompts com golden set (§8 #12)** — casos versionados corrigidos
  offline em CI, com hold-out intocável (`scripts/golden.py check`, passo guardado).
- **CI no GitHub (§9 "Processo", H4)** — `ci.yml` (Python 3.12/3.14, pytest, testes da
  ponte e do snapshot, `luac -p`, web, shell, varredura de segredos) e `release.yml`
  (tag `vX.Y.Z` → mesmos testes → notas deste ficheiro → GitHub Release).
- **Smoke automatizado (§9 "Navegação", H7)** — `scripts/smoke/df_smoke.py`
  (`--offline` contra um DFHack falso em CI; `--live` com `df up --no-web` → snapshot →
  movida com prova → `df stop`), `scripts/smoke/patterns_check.py` (validação da
  biblioteca de padrões e alerta de deriva de UI) e `smoke.yml` (runner auto-hospedado).

### Changed

- **Movimento como macro-ação (§8 #04)** — o decisor escolhe destino + urgência e o
  código faz o pathing passo a passo (menos decisões de micro-movimento e abstenções).
- **Shuffle + voto nas opções (§8 #05)** — 2–3 permutações dos candidatos e voto nas
  decisões críticas; mede a estabilidade no mesmo passo.
- **Limiares calibrados por risco (§8 #06)** — um limiar por classe de ação, a partir
  das curvas risco-cobertura dos 66 cenários de `evals/`.
- **Planos com ciclo gerar → testar → criticar (§8 #09)** — condições de sucesso
  verificadas em código e limite de tentativas.
- **Planos com schema fixo e esforço dinâmico (§8 #10)** — campos fixos, waypoints (não
  teclas) e *effort* médio por omissão.
- **Riscos de §9 em curso** — navegação multi-z (H2), combate com políticas de recuo e
  gestão de armas (H5), persistência dos stores com watchdog de sessão (H3) e
  calibragem das bandas do JEV (H1).

### Security

- A varredura de segredos passa a correr em cada PR (`ci.yml`, job `secrets`) além do
  gate local antes de cada commit; o smoke retira credenciais do ambiente filho e
  redige o relatório.

## [0.1.0] - 2026-10-09

Primeiro MVP: o agente joga uma missão completa em Dwarf Fortress 53.16 (adventure
mode) via DFHack — corrida `caca-2`, mula abatida ao vivo.

### Added

- **Ponte DFHack** (`dfhack/df_bridge.py`, só stdlib): leitura do `gamelog.txt`, cliente
  RPC próprio, `state`, `snapshot` (8 grupos de métricas, contrato "nulls, nunca
  ausentes"), `act` (focus/screen/key/click/move/goto) e `act batch` (DSL com guardas
  `--stop-on-focus-change`/`--stop-on-threat` e journal JSONL); scripts Lua `dfb-*`.
- **Camada de estado do jogo** (`game/`): `GameState` tipado, `GameStateClient`, serviço
  de aventura (novo mundo, nova personagem, carregar save).
- **Frota JEV** (`jev/`): cliente `typesafe/jev-1.13` fixado, primitivas
  `noul`/`choice`/`score`, validação local e bandas `auto`/`hitl`/`abstain`
  (JEV-R/A/M/D).
- **Loop de decisão autónomo** (`agent/`): candidatos gerados por código, executor DSL
  com guardas, `StuckDetector`, narrador DeepSeek com *effort* dinâmico, formatter de
  perguntas PT e aprovações com modo `checkpoint`.
- **Memória**: CoALA/SQLite por campanha e base de conhecimento partilhada
  (`conhecimento/`, 30 129 registos); tradutor PT↔EN com glossário e cache.
- **Backend FastAPI + WebSocket** (contrato em `docs/api.md`): campanhas, chaves
  mascaradas, memória, objetivo, métricas, decisões, perguntas, aprovações, controlo
  (`autopilot`/`checkpoint`/`paused`/`kill_switch`), aventuras e controlo do sistema.
- **UI web** (React + Vite + MUI v9, em português): 8 painéis, wizard de aventura e
  superfícies motion-ui; auditoria UX de 40/100 para 100/100.
- **Operação**: comando global `df` (`up|stop|status|logs|install-link`), `install.sh`
  com guardas de SO e verificação SHA-256 do DF 53.16 e do DFHack 53.16-r2, som
  desativado por omissão; `sod` mantido como alias depreciado.
- **Biblioteca de padrões de navegação** (`docs/padroes-navegacao.json`, 21 padrões
  comprovados + cadeia de combate), arnês E2E (`scripts/e2e/`) e cenários de calibração
  (`evals/`, 66 cenários).
- **Documentação**: README, `docs/`, `PROBLEMAS-E-SOLUCOES.md` e `ARQUITETURA.md`.

### Fixed

- Aprovações: deduplicação, paginação e estado de execução; espelho de aprovações,
  libertação por aprovação e métricas anuláveis (500 determinístico do `metrics`).
- Personagem "parada" sem erro: o overlay de tutorial engolia todo o input — fecho do
  tutorial, `StuckDetector` anti-congelamento e prova de movimento.
- Chaves de teste inequivocamente falsas em todo o repositório.

[Unreleased]: https://github.com/frederico-kluser/dwarf-fortress-agent-skill/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/frederico-kluser/dwarf-fortress-agent-skill/releases/tag/v0.1.0
