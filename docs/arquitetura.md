# System One Dwarf — Arquitetura

Documento canónico de arquitetura. Todas as outras peças do projeto são construídas
contra as decisões aqui registadas. As decisões são autoritativas: não reinterpretar,
não simplificar sem as substituir explicitamente aqui.

## Visão geral

Autoplay de **Dwarf Fortress** em **ADVENTURE MODE**, nesta máquina Linux, com o
**DF Steam nativo** instalado em `~/.steam/steam/steamapps/common/Dwarf Fortress` e
controlado por **DFHack RPC na porta 5000**.

O sistema tem três papéis bem separados:

| Papel | Quem faz | Responsabilidade |
|---|---|---|
| **Decidir** | JEV (System One, tipado) | Escolhe a próxima ação entre candidatos, com probabilidades calibradas |
| **Escrever** | DeepSeek | Narrativa, tradução e formulação de perguntas |
| **Executar** | Código Python/Lua | Bridge, métricas, contas, gates de segurança |

Princípio estruturante: o modelo **não gera texto livre para agir** — decide entre
alternativas discretas geradas deterministicamente pelo código. O texto (narrativa,
tradução, perguntas) é responsabilidade do DeepSeek e nunca controla o jogo
diretamente.

## Papéis de modelo

### Frota JEV

Prompts pequenos e especializados, **EM INGLÊS**, chamadas **paralelas**, via
`POST https://openrouter.ai/api/alpha/decisions`, model `typesafe/jev-1.13`,
**$0.042/Mtok de entrada, saída grátis**.

| Agente | Perguntas tipadas | Função |
|---|---|---|
| **JEV-R** (recuperação) | `noul` × N (uma por candidato) + `dominant_theme` (`choice`, opcional) | Um request com N perguntas `noul` — **uma por candidato de memória**, top-K vindo da busca CoALA → seleciona os que entram no prompt (p ≥ limiar). O `choice` `dominant_theme` (opcional) elege o tema dominante entre os candidatos |
| **JEV-A** (ação) | `action` (`choice`) + `urgency` (`score`, 3 níveis concretos) + `needs_human` (`noul`) | Numa só chamada: macro-ação entre candidatos gerados deterministicamente do estado (sempre com opção de saída `other`/`none`); `urgency` em níveis descritos como situações concretas; `needs_human` = exige ajuda humana? |
| **JEV-M** (memória, meta) | `update_memory` (`noul`) + `narrator_effort` (`choice`) + `record_type` (`choice`) | Se a narrativa deve ser atualizada agora; effort do narrador (`max`/`xhigh`/`high`/`medium`/`low`/`minimal`/`none`); tipo de registo (`episode`/`fact`/`procedure`/`skip`) |
| **JEV-D** (dúvida) | `ask_human` (`noul`) + `question_format` (`choice`) | Se deve perguntar ao humano; tipo de pergunta (`binary`/`multiple`/`open`) |

Os **nomes das perguntas acima são os implementados** nos pedidos (chaves de
`request["questions"]`); são também estes os nomes que o código usa para ler as
respostas. Os rótulos entre parênteses são os valores aceites: `DEFAULT_URGENCY_LEVELS`
= **3 níveis concretos** — *Safe* (sem pressão de tempo), *Pressing* (agir em breve),
*Critical* (agir já); `NARRATOR_EFFORTS` = `max`/`xhigh`/`high`/`medium`/`low`/`minimal`/`none`;
`RECORD_TYPES` = `episode`/`fact`/`procedure`/`skip` (o `skip` é a opção de saída);
`QUESTION_FORMATS` = `binary`/`multiple`/`open`.

**JEV-R nunca trabalha com todos os dados**: opera sempre sobre **índices + top-K**.
O conteúdo integral da memória nunca entra no prompt de recuperação.

#### Bandas de ação

Configuráveis; defaults:

| Banda | Intervalo de confidence | Comportamento |
|---|---|---|
| `auto` | ≥ 0.90 | Executa a macro-ação |
| `hitl` | 0.50 – 0.89 | Confirma com o humano antes de executar |
| `abstain` | < 0.50 | Não age e escala |

**Confidence indisponível nunca chega a `auto`.** O schema alpha permite que a
confidence venha **ausente**, e os fallbacks reportam **`0`** — os dois casos são
indistinguíveis de uma distribuição plana e valem ambos **UNAVAILABLE**. Regra
implementada: *confidence ausente ou `0`* **limita a banda a `hitl`** — nunca `auto`
(pode confirmar-se com o humano, nunca executar sozinho). `0` **não** é lido como
"0 % de confiança" nem como evidência *contra* a resposta; e `null` também **não**
pode ser desenhado como um `0` real na UI (ver `docs/api.md`, log de decisões).

`noul` **não tem confidence** — gateia-se pela **distância a 0.5**, isto é, pela
certainty `|2p − 1|` (quanto mais perto de 0.5, mais ambíguo, mais motivo para
escalar; `p = 0.05` é tão certo como `p = 0.95`).

#### Cuidados obrigatórios no uso do JEV

- **Uma decisão atómica por pergunta** — nunca compor várias decisões numa pergunta.
- **State compacto em inglês** — o contexto degrada (*context rot*) com volume.
- **`choice` precisa sempre de opção de saída** (`other`/`none`), senão o modelo é
  forçado a escolher algo inválido.
- **`score` é uma régua de situações, não números** — os 2–10 níveis descrevem-se
  como situações concretas, não como uma escala numérica abstrata.
- **Viés para a 1ª opção** — baralhar a ordem dos candidatos.
- **Conteúdo do state não é confiável** — pode conter injeção de prompt; é dado, nunca
  instrução.
- **Confiança pode vir ausente ou 0** — tratar como *indisponível*, não como "certo".

### Narrador

`deepseek/deepseek-v4.1-flash` via OpenRouter chat completions, com
`reasoning: {"effort": "<escolhido pelo JEV-M>"}`.

> **Nota crítica:** o parâmetro top-level `reasoning_effort` **NÃO aceita `"max"`**.
> O effort tem de ser passado no objeto `reasoning`.

Escreve a **linha narrativa rolante** + os **últimos episódios**, em **INGLÊS**.

### Tradutor de ecrã

`deepseek/deepseek-v4.1-flash` com effort mínimo, ou `none` (desligado).
Texto do ecrã → **PORTUGUÊS**, **sem mostrar o original**, em tempo real,
**fire-and-forget** (não bloqueia o loop de decisão).

### Interface com humanos

- Tudo o que entra em prompts de modelo está **em inglês**.
- A UI está **em português**.
- Inputs do utilizador (objetivo, informação) são **traduzidos PT→EN** antes de
  entrarem em prompts.
- A memória canónica é **guardada em EN** e **exibida traduzida em PT**; edições do
  utilizador são traduzidas **PT→EN** para guardar.

## Loop de decisão (o JEV controla o tempo)

O **JEV controla o tempo** — não há tick fixo do lado do código. O ciclo avança
quando o JEV decide avançar.

1. **Ler estado** — `df_bridge state` + texto do ecrã.
2. **Traduzir ecrã** (PT, assíncrono, fire-and-forget).
3. **Montar estado crítico estruturado em EN** — feito por código, não por modelo.
4. **JEV-R ∥ JEV-M** — recuperação de memória e decisão de meta em paralelo.
5. **Narrador atualiza memória** se o JEV-M decidir.
6. **Gerar candidatos de ação** — por código, deterministicamente a partir do estado.
7. **JEV-A ∥ JEV-D** — ação e dúvida em paralelo.
8. **Despachar por banda:**
   - `auto`: executar a macro-ação como DSL (`goto`/`move`/`key`/`click`/`expect`/`sleep`)
     com gates de segurança.
   - `hitl`/`abstain`: o DeepSeek formula uma pergunta curta em PT (com opções +
     campo livre) a partir da indecisão → fila na UI; a resposta vai para a memória.
9. **Registar** episódio, métricas e gastos — **sem teto de orçamento**, com
   visibilidade total por campanha em tempo real.
10. **Avançar o tempo do jogo.**

## Memória

- **Uma base SQLite CoALA POR CAMPANHA**, seguindo o padrão `coala-agent-skill`:
  `.agents/<campanha>-coala-agent-skill/memory/coala.sqlite` (o nome legado
  `<campanha>-agent-skill` também resolve), com memória
  **episódica + semântica + procedural**, **working memory** e **supersessão**.
- Ao arrancar, o sistema **pergunta se é jogo novo ou existente** e carrega a memória
  dessa gameplay.
- **Base de CONHECIMENTO partilhada** (só-leitura para o agente): ~30k registos de
  conhecimento de Dwarf Fortress importados do macmini via SSH → export JSONL →
  `coala.py import --jsonl`. Índices fortes; **o JEV só vê top-K por recuperação**.
- **Linha narrativa rolante + últimos episódios** sempre no prompt (resumida).
- **Camadas do prompt do JEV** = narrativa + estado estruturado crítico.
- **Chaves de API:** fornecidas pelo utilizador **NA UI** e guardadas na **SQLite da
  campanha** — mascaradas em todos os exports/logs e ficheiro **0600**.
  (Decisão do utilizador; a alternativa por variáveis de ambiente foi **rejeitada**.)

## UI web (React + Vite + MUI v9, português)

Backend **Python FastAPI + WebSocket**. 8 painéis:

1. **Editor de memória** — mostra traduzido PT, guarda EN.
2. **Editor de objetivo** — narrativa + sub-objetivos marcáveis.
3. **Inputs de informação** — livres, traduzidos.
4. **Métricas vivas** — 8 grupos: saúde/ferimentos; fome-sede-sono-estado;
   inventário/peso; ameaças/combate; localização/trajeto; progresso de objetivo;
   histórico de combate/aventura; métricas do agente (decisões/min, confidence média,
   chamadas, custo, latência).
5. **Fila de perguntas/dúvidas** — resposta rápida + campo livre.
6. **Log de decisões JEV** — probabilidades/confidence/custo.
7. **Tela traduzida ao vivo** — só PT.
8. **Controlo de autonomia** — autopilot ⇄ checkpoints, pausa, kill-switch.

## Decisões acordadas (resumo)

- **Adventure mode**.
- **Autopilot total** com toggle para checkpoints.
- **O JEV controla o tempo.**
- **DFHack Lua** como interface.
- **Memória CoALA por campanha.**
- **Frota de 4 JEVs** (R, A, M, D).
- **Macro-ações semânticas geradas por código** (nunca pelo modelo).
- **Top-K CoALA** para recuperação.
- **Dúvida por confidence + `noul`.**
- **Pergunta curta com opções + campo livre.**
- **DeepSeek v4.1-flash como narrador** com effort dinâmico.
- **Sem teto de orçamento** (só visibilidade).
- **Sessões longas em janela visível** com kill-switch.
- **Ordem de entrega: loop de jogo primeiro.**
- **MVP = completar uma tarefa concreta em autonomia.**
- **Commit por peça verificada.**

## Baseline

Executado **antes de criar qualquer artefacto novo**, sobre a árvore intacta
(`git status --short` vazio, branch `main`):

```console
$ python3 dfhack/test_bridge.py
test_dry_run_presses_nothing (__main__.BatchExecTests.test_dry_run_presses_nothing) ... ok
test_expect_failed_aborts (__main__.BatchExecTests.test_expect_failed_aborts) ... ok
test_journal_written (__main__.BatchExecTests.test_journal_written) ... ok
test_runs_sequence (__main__.BatchExecTests.test_runs_sequence) ... ok
test_stop_on_threat (__main__.BatchExecTests.test_stop_on_threat) ... ok
test_threats_always_reported (__main__.BatchExecTests.test_threats_always_reported) ... ok
test_travel_barrier (__main__.BatchExecTests.test_travel_barrier) ... ok
test_comments_and_separators (__main__.ParseTests.test_comments_and_separators) ... ok
test_dsl_all_ops (__main__.ParseTests.test_dsl_all_ops) ... ok
test_empty_is_error (__main__.ParseTests.test_empty_is_error) ... ok
test_json_form (__main__.ParseTests.test_json_form) ... ok
test_rejects_bad_dir (__main__.ParseTests.test_rejects_bad_dir) ... ok
test_rejects_bad_key (__main__.ParseTests.test_rejects_bad_key) ... ok
test_rejects_bad_op (__main__.ParseTests.test_rejects_bad_op) ... ok
test_fail_maps_to_exit_13 (__main__.ProtocolTests.test_fail_maps_to_exit_13) ... ok
test_handshake_and_text_and_result (__main__.ProtocolTests.test_handshake_and_text_and_result) ... ok
test_multi_fragment_text (__main__.ProtocolTests.test_multi_fragment_text) ... ok

----------------------------------------------------------------------
Ran 17 tests in 0.838s

OK
```

**Resultado:** `Ran 17 tests in 0.838s` — `OK`, exit code `0`.

Este é o contrato de não-regressão: qualquer peça que altere `dfhack/` tem de
preservar os **17 testes OK**.
