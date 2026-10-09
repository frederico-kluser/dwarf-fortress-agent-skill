# System One Dwarf — API (REST + WebSocket)

Contrato canónico entre o backend (Python/FastAPI) e a UI (React + Vite + MUI v9).
Todas as peças do projeto implementam **exatamente** estes caminhos e formas de dados.

Documento em português; **nomes de campos em inglês** (entram em prompts de modelo).

## Convenções

| Aspeto | Regra |
|---|---|
| **Erros** | Corpo `{"detail": "<mensagem>"}` (formato FastAPI `HTTPException`) com o código HTTP adequado |
| **Timestamps** | ISO-8601 em **UTC** com sufixo `Z` — ex.: `2026-09-27T19:44:03.512Z` |
| **Dinheiro** | `float` em **USD** com **6 casas decimais** — ex.: `0.042318` |
| **Texto** | Todos os campos de texto em **EN**, exceto (a) os marcados `*_pt` e (b) os payloads de pergunta *human-facing* — ver nota abaixo |
| **IDs** | `campaign_id` e `qid`: string opaca (UUID4) — o cliente nunca a interpreta |
| **Chaves de API** | Nunca devolvidas em claro por nenhum endpoint. `GET .../keys` devolve **apenas mascaradas** |
| **Content-Type** | `application/json` em todos os pedidos e respostas REST |

Convenção de nomes para tradução: um campo traduzido tem sufixo **`_pt`** e é
**só-leitura** para o cliente, exceto onde o `PATCH` explicitamente aceita PT e o
servidor traduz PT→EN antes de guardar.

**Exceção (b) — payloads de pergunta (PT por desenho).** A exceção (a) são os campos
`*_pt`. A exceção (b) são os campos que o humano lê diretamente na fila de perguntas,
que são **em português**, não em inglês: `question` e o
`text`/`label` das opções em `GET /api/campaigns/{id}/questions`, e os eventos WS
`question_new` / `question_answered`. São a **camada human-facing** (formulada pelo
DeepSeek em PT), pelo que a regra "tudo em EN" **não** se lhes aplica. Os exemplos
abaixo mostram-nos já em PT e **não devem ser traduzidos**.

### Marcador stub/live (dados reais vs. placeholder)

Enquanto o loop de jogo não está ligado, os endpoints respondem com a **forma
completa e correta** mas com dados placeholder — nunca `501`, nunca uma forma
quebrada. O cliente precisa de distinguir os dois casos:

| Tipo de payload | Endpoints | Onde vem o marcador |
|---|---|---|
| **Objeto** | `GET .../metrics`, `GET .../screen` | Campo **inline** `"source": "stub"` \| `"live"` dentro do próprio objeto |
| **Array** | `GET .../decisions`, `GET .../questions`, `GET .../approvals` | Cabeçalho de resposta **`X-Source: stub`** \| `"live"` (não há onde pôr um campo dentro de um array JSON) |

`X-Source` é um cabeçalho **de resposta** e está exposto por CORS
(`expose_headers`), para que a UI o possa ler. Os valores são exatamente `stub` e
`live`. O objeto `metrics`/`screen` traz **sempre** `source`, e o mesmo endpoint
pode devolver `stub` para uma campanha e `live` para outra.

---

## Loop de decisão: quando corre e o que precisa

O loop de decisão (`AgentLoop`, com JEV + narrador + executor) é **um por
campanha**, criado e gerido pelo backend. Regras (comportamento observável pelo
cliente, não detalhe interno):

| Situação | O loop | O que o app serve |
|---|---|---|
| `mode: "autopilot"` ou `"checkpoint"`, com **chaves** guardadas | corre | dados `live` |
| `mode: "paused"` | parado (e nenhuma chamada a API é feita) | última leitura `live`, sem atualizações novas |
| `kill_switch: true` | paragem imediata | idem |
| **Falta a chave `typesafe` (JEV) ou `openrouter` (LLM)** | **nunca arranca** | os stubs documentados acima |

O modo é gravado por `POST .../control` (e pela mensagem WS `set_control`); é
essa chamada que arranca/para o loop — não há endpoint separado de arranque.
`autopilot` e `checkpoint` correm ambos o loop: a diferença é que em
`checkpoint` **todas** as ações ficam retidas para aprovação (ver
`GET .../approvals`). Sem chaves, `POST .../control` continua a responder `200`
com o modo gravado — só o loop é que não arranca.

O **tradutor** PT↔EN segue a mesma regra de chaves: é construído a partir da
chave `openrouter` **da campanha**; sem chave, o comportamento é o *identity*
documentado (o texto é guardado como foi escrito e os campos `*_pt` repetem o
canónico EN).

---

## REST

### `GET /api/health`

Liveness + versão do backend.

**200**

```json
{ "status": "ok", "version": "0.1.0" }
```

---

### `GET /api/campaigns`

Lista as campanhas conhecidas (memórias CoALA por campanha).

**200**

```json
[
  {
    "id": "8f2c1e40-...",
    "name": "Urist McAdventurer",
    "is_new_game": false,
    "created_at": "2026-09-27T19:44:03.512Z",
    "updated_at": "2026-09-27T20:02:11.004Z",
    "control": { "mode": "autopilot", "kill_switch": false }
  }
]
```

### `POST /api/campaigns`

Cria (ou abre) a campanha, a que corresponde **uma base SQLite CoALA própria**
(`.agents/<campanha>-agent-skill/memory/coala.sqlite`).

**Body**

```json
{ "name": "Urist McAdventurer", "is_new_game": true }
```

| Campo | Tipo | Notas |
|---|---|---|
| `name` | string | Nome da campanha; define o caminho da memória |
| `is_new_game` | bool | `true` = jogo novo (memória vazia); `false` = gameplay existente (carrega a memória dessa gameplay) |

**201** → o objeto campanha (mesma forma do `GET /api/campaigns`).

---

### `GET /api/campaigns/{id}`

Detalhe de uma campanha.

**200** → objeto campanha. **404** → `{"detail": "campaign not found"}`.

---

### `POST /api/campaigns/{id}/keys`

Guarda uma chave de API. A chave é persistida **na SQLite da campanha**, com
**ficheiro 0600** e **mascarada em todos os exports/logs** (decisão do utilizador;
variáveis de ambiente foram rejeitadas).

**Body**

```json
{ "provider": "openrouter", "key": "sk-or-v1-..." }
```

`provider` ∈ `"openrouter"` | `"typesafe"` | `"deepseek"`.

**200**

```json
{ "provider": "openrouter", "masked_key": "TEST-KEY-••••••••0001", "set": true }
```

### `GET /api/campaigns/{id}/keys`

Devolve **apenas as versões mascaradas** — nunca a chave em claro.

**200**

```json
[
  { "provider": "openrouter", "masked_key": "TEST-KEY-••••••••0001", "set": true },
  { "provider": "typesafe",   "masked_key": null,                    "set": false },
  { "provider": "deepseek",   "masked_key": null,                    "set": false }
]
```

---

### `GET /api/campaigns/{id}/memory`

Estado da memória CoALA da campanha.
Os campos `*_pt` são a **exibição traduzida**; o canónico é guardado em **EN**.

**200**

```json
{
  "narrative": "The adventurer wakes in a clearing...",
  "narrative_pt": "O aventureiro acorda numa clareira...",
  "episodes": [
    {
      "id": "ep-001",
      "ts": "2026-09-27T19:50:00.000Z",
      "kind": "episodic",
      "content": "Climbed the hill without incident.",
      "content_pt": "Subiu a colina sem incidentes."
    }
  ],
  "working_memory": "…",
  "updated_at": "2026-09-27T20:02:11.004Z"
}
```

### `PATCH /api/campaigns/{id}/memory`

Edita narrativa e/ou episódios. **O servidor traduz PT→EN** antes de guardar: o
utilizador escreve em português, a memória canónica fica em inglês.

**Body** (parcial — enviar só o que muda)

```json
{ "narrative": "O aventureiro acorda numa clareira..." }
```

**200** → o objeto memória atualizado (já com `narrative` em EN e `narrative_pt` em PT).

---

### `GET /api/campaigns/{id}/objective`

Os campos `narrative_pt` e `subgoals[].text_pt` são **aditivos** (exibição PT);
o canónico continua a ser o texto EN. Regra: o campo `*_pt` só aparece quando há
uma tradução de exibição **diferente** do canónico — sem chave (identity) o
payload é exatamente o do contrato original, por isso a UI tem de tolerar a
ausência do campo e cair no texto canónico.

**200**

```json
{
  "narrative": "Reach the fortress of Boatmurdered alive.",
  "narrative_pt": "Chegar vivo à fortaleza de Boatmurdered.",
  "subgoals": [
    { "id": "sg-1", "text": "Find water", "done": true, "text_pt": "Encontrar água" },
    { "id": "sg-2", "text": "Cross the river", "done": false, "text_pt": "Atravessar o rio" }
  ]
}
```

### `PUT /api/campaigns/{id}/objective`

Substitui o objetivo completo. **O servidor traduz PT→EN** antes de guardar (o
objetivo é material de prompt) e devolve o mesmo objeto do `GET`, já com os
campos `*_pt` calculados a partir do canónico.

**Body**

```json
{
  "narrative": "Chegar vivo à fortaleza.",
  "subgoals": [{ "id": "sg-1", "text": "Encontrar água", "done": true }]
}
```

**200** → o objetivo guardado (canónico EN + `*_pt` quando existir tradução).

---

### `POST /api/campaigns/{id}/inputs`

Injeção de informação livre pelo utilizador (traduzida PT→EN antes de entrar em prompts).

**Body**

```json
{ "text": "Há uma caverna a norte que dá água." }
```

**201**

```json
{ "id": "in-7", "text": "There is a cave to the north with water.", "ts": "2026-09-27T20:05:00.000Z" }
```

---

### `GET /api/campaigns/{id}/metrics`

Métricas vivas, em **8 grupos**.

**200**

```json
{
  "ts": "2026-09-27T20:05:00.000Z",
  "health":            { "wounds": [], "status": "healthy" },
  "needs":             { "hunger": "none", "thirst": "none", "sleep": "rested" },
  "inventory":         { "weight_kg": 42.5, "items": [] },
  "threats":           { "level": "low", "combat": false, "hostiles": [] },
  "location":          { "region": "The Hills", "path": [] },
  "objective_progress":{ "percent": 0.35, "active_subgoal_id": "sg-2" },
  "combat_history":    [],
  "agent": {
    "decisions_per_min": 12.4,
    "confidence_avg": 0.87,
    "calls": 318,
    "cost_usd": 0.042318,
    "latency_ms": 410
  }
}
```

Os 8 grupos são exatamente: **`health`** (saúde/ferimentos), **`needs`**
(fome-sede-sono-estado), **`inventory`** (inventário/peso), **`threats`**
(ameaças/combate), **`location`** (localização/trajeto), **`objective_progress`**
(progresso de objetivo), **`combat_history`** (histórico de combate/aventura),
**`agent`** (métricas do agente: decisões/min, confidence média, chamadas, custo,
latência).

---

### `GET /api/campaigns/{id}/spend`

Gastos **sem teto** — apenas visibilidade total, por campanha, em tempo real.

**200**

```json
{
  "total_usd": 0.042318,
  "providers": [
    {
      "provider": "typesafe",
      "models": [
        { "model": "typesafe/jev-1.13", "calls": 318, "input_tokens": 1007714, "output_tokens": 0, "cost_usd": 0.042318 }
      ]
    },
    {
      "provider": "openrouter",
      "models": [
        { "model": "deepseek/deepseek-v4.1-flash", "calls": 12, "input_tokens": 8000, "output_tokens": 2100, "cost_usd": 0.000000 }
      ]
    }
  ],
  "ts": "2026-09-27T20:05:00.000Z"
}
```

---

### `GET /api/campaigns/{id}/decisions`

Log das decisões JEV.

**Query (aditivo)** — `?limit=<n>&before=<ts ISO-8601>` pagina o log:
`limit` devolve os registos **mais recentes** (a lista mantém a ordem antiga →
mais antigo primeiro dentro da página) e `before` é um cursor **exclusivo** —
só registos com `ts` estritamente anterior. `before` inválido → **422**. Sem
parâmetros, o comportamento antigo está intacto (o anel guarda **5000**
registos por campanha).

**200**

```json
[
  {
    "id": "d-91",
    "ts": "2026-09-27T20:04:58.000Z",
    "agent": "JEV-A",
    "request_summary": "choice(6 action candidates) + score(urgency) + noul(needs_human)",
    "answers": { "choice": "goto:river_bank", "score": "minor risk", "noul": 0.12 },
    "probabilities": { "goto:river_bank": 0.94, "move:north": 0.04, "other": 0.02 },
    "confidence": 0.94,
    "band": "auto",
    "latency_ms": 410,
    "cost_usd": 0.000133
  }
]
```

| Campo | Notas |
|---|---|
| `agent` | `JEV-R` \| `JEV-A` \| `JEV-M` \| `JEV-D` — vem do `meta.role` do **pedido** ao JEV (é o pedido que se identifica, não o cliente nem o endpoint) |
| `request_summary` | Resumo do pedido (tipos de pergunta + nº de candidatos) |
| `answers` | Respostas tipadas devolvidas |
| `probabilities` | Distribuição por opção (`choice`) |
| `confidence` | `float` **ou `null`** — `null` = **UNAVAILABLE**, ver nota abaixo |
| `band` | `auto` \| `hitl` \| `abstain` |
| `latency_ms` | Latência da chamada |
| `cost_usd` | Custo da chamada |
| `issues` | **ADITIVO** — array de strings (notas de validação/qualidade do JEV e marcas de auditoria), **omitido quando vazio**. Quando a execução veio de uma aprovação humana já registada, o registo do JEV-A traz a marca `released_by_approval` |
| `status` | **ADITIVO** — `executed` \| `question` \| `error`: o que aconteceu à ação escolhida. `executed` quando a ação despachou (evento `action_executed`, mesmo sem aprovação) ou quando uma aprovação humana a libertou (`approval_released`); `question` quando a decisão virou pergunta ao humano (a `action_id` retida); `error` quando o passo falhou antes de executar. **Omitido quando desconhecido** (decisões auxiliares JEV-R/M/D) |

**`confidence` pode ser `null` (UNAVAILABLE).** O schema alpha permite que a
confidence venha **ausente** (e os fallbacks devolvem `0`), pelo que *ausente* e
`0` são indistinguíveis de uma distribuição plana. Ambos significam
**indisponível** — não "0 % de confiança" e não "certo".

**`probabilities` também pode ser `null`**: as decisões que só fazem perguntas
`noul`/`score` (JEV-R, JEV-M, JEV-D) não têm distribuição por opção. `null` nunca
pode ser convertido em `{}` — um objeto vazio afirmaria uma distribuição que não
existe. (Só o JEV-A, que responde `choice`, traz `probabilities` preenchido.)

- A UI **nunca** pode renderizar `null` como um `0` real: mostre `—` / `indisponível`
  (um `0` desenhado como barra vazia é uma mentira visual).
- Sem confidence utilizável, a banda fica **limitada a `hitl`** — nunca `auto`
  (ver `docs/arquitetura.md`, "Bandas de ação").
- Para perguntas `noul` a certainty é `|2p − 1|` (não há confidence); `p = 0.5` é
  incerteza total.

---

### `GET /api/campaigns/{id}/questions`

Fila de perguntas/dúvidas ao humano (geradas nas bandas `hitl`/`abstain`, nas
ações retidas pelo modo `checkpoint` e nas escaladas do JEV-D).

**Query (aditivo)** — `?limit=<n>&before=<ts ISO-8601>` pagina a fila (mesma
semântica de `.../decisions`: `limit` devolve as entradas **mais recentes**, na
ordem antiga; `before` é um cursor **exclusivo** por `ts`). `before` inválido →
**422**. Sem parâmetros, o comportamento antigo está intacto (o anel guarda
**2000** entradas por campanha — a evicção começa pelas **respondidas**; as
pendentes só caem em último caso).

**Dedupe (anti-flood).** O modo `checkpoint` volta a pedir a **mesma** ação
retida a cada passo do loop. Enquanto existir uma pergunta por responder daquela
`action_id`, o re-pedido **não** cria outra entrada: a pendente ganha os campos
aditivos `attempts` (quantas vezes o loop pediu) e `last_seen_ts` (ts do último
re-pedido). Depois de respondida, um novo pedido pode voltar a entrar.

**200**

```json
[
  {
    "id": "q-4",
    "ts": "2026-09-27T20:04:59.000Z",
    "question": "Atravessar o rio a nadar ou procurar uma ponte?",
    "options": [
      { "id": "opt-1", "text": "Nadar" },
      { "id": "opt-2", "text": "Procurar ponte" }
    ],
    "free_text_allowed": true,
    "status": "pending",
    "band": "hitl",
    "role": "JEV-A",
    "action_id": "goto:river_bank",
    "why_pt": "a confiança ficou na banda hitl (0.50–0.89): confirmar antes de executar (banda hitl)",
    "source_data": { "answer_mode": "approval", "reason": "checkpoint_mode", "band": "auto" }
  }
]
```

Os cinco últimos campos são **ADITIVOS** (achado #2 de
`docs/uxui-audit-ui.json`: a fila tem de dizer *porque* o agente pergunta). A UI
**tem de tolerar a ausência** de qualquer um deles, porque só vêm quando são
conhecidos:

| Campo | Tipo | Notas |
|---|---|---|
| `band` | `"auto"` \| `"hitl"` \| `"abstain"` | Banda que originou a pergunta (presente quando o loop a publicou ou quando vem no `source_data`) |
| `role` | `"JEV-A"` \| `"JEV-D"` (roles do log) | Papel JEV que originou a pergunta: `JEV-A` quando há uma ação retida, `JEV-D` na escalada de dúvida. Calculado pelo backend a partir da proveniência disponível |
| `action_id` | string | A ação retida à espera de resposta (id de macro-ação, ex. `goto:river_bank`) |
| `why_pt` | string | **PT** (camada human-facing, como `question`): uma frase a explicar o motivo |
| `source_data` | object | Diagnóstico opcional: `reason`, `band`, `action_mode`/`answer_mode`, `action_en`, `description_en`, confiança, candidatos… Pode vir `{}` |
| `attempts` | integer | **ADITIVO** — quantas vezes o loop voltou a pedir a **mesma** ação (dedupe). **Omitido na primeira vez** |
| `last_seen_ts` | string | **ADITIVO** — ts do último re-pedido da mesma ação (omitido na primeira vez) |

Uma pergunta sem proveniência (um produtor antigo) continua a devolver
**exatamente** os 6 campos originais — os campos aditivos só aparecem quando
existem (o servidor omite os nulos).

### `POST /api/campaigns/{id}/questions/{qid}/answer`

**Body**

```json
{ "answer_text": "Procurar uma ponte a leste", "option_id": "opt-2" }
```

`option_id` é opcional (resposta por campo livre). A resposta **vai para a
memória**. Se a pergunta for uma **aprovação** de checkpoint (ver
`GET .../approvals`), a resposta decide também essa aprovação (uma recusa
explícita — `no`/`reject` — rejeita; qualquer outra resposta aprova).

**200**

```json
{ "id": "q-4", "status": "answered", "answer_text": "Look for a bridge to the east.", "option_id": "opt-2" }
```

---

### `GET /api/campaigns/{id}/approvals`

**Aditivo** (achado #4 de `docs/uxui-audit-ui.json`): a superfície de aprovação
do modo `checkpoint`. No modo `checkpoint` o loop retém **todas** as ações e
levanta uma pergunta de aprovação; o backend espelha essas perguntas aqui. Em
`autopilot` não existem ações retidas para aprovar, por isso a lista nasce
**vazia** (e `X-Source: stub`).

Por omissão devolve só as **pendentes**; `?status=pending|approved|rejected|all`
filtra (com `all` obtém-se o histórico auditável). Ordem por `ts` crescente.
`X-Source: live` quando existe pelo menos uma aprovação.

**Dedupe + limite (aditivo).** Enquanto existir uma aprovação **pendente** da
mesma `action_id`, o re-pedido do loop **não** cria outra: a pendente ganha
`attempts`/`last_seen_ts`. A fila de pendentes está **limitada a 20** — passado
isso, as **mais antigas** são descartadas. Decidir uma aprovação (approve ou
reject) também descarta duplicados pendentes da mesma `action_id` (limpeza de
uma pilha criada antes do dedupe: decidir uma entrada de 391 colapsa a pilha).

**200**

```json
[
  {
    "id": "q-3-1",
    "question_id": "q-3-1",
    "ts": "2026-09-27T20:04:59.000Z",
    "action_id": "goto:river_bank",
    "action_name_en": "Go to the river bank",
    "question": "Posso executar «goto:river_bank»?",
    "band": "auto",
    "status": "pending"
  }
]
```

| Campo | Notas |
|---|---|
| `id` / `question_id` | O id da pergunta de aprovação que originou a entrada (são o mesmo; o id é a chave para aprovar/rejeitar) |
| `ts` | ISO-8601 UTC |
| `action_id` | A ação retida |
| `action_name_en` | Nome da ação em EN (cai no `action_id` quando o payload do loop não traz o nome) |
| `question` | A pergunta em PT que o humano leu (pode faltar) |
| `band` | Banda da decisão retida |
| `status` | `pending` \| `approved` \| `rejected` |
| `note` | **Só depois de decidida** — a nota humana (omitida quando não há) |
| `decided_at` | **Só depois de decidida** — ISO-8601 UTC |
| `description_en` | Opcional (quando o loop a publica) |
| `attempts` | **ADITIVO** — quantas vezes o loop voltou a pedir a **mesma** ação (dedupe; omitido na primeira vez) |
| `last_seen_ts` | **ADITIVO** — ts do último re-pedido (omitido na primeira vez) |

### `POST /api/campaigns/{id}/approvals/{approval_id}/approve`

Liberta a decisão. O corpo é opcional: `{ "note": "vai com cuidado" }`.

**200** → a aprovação com `status: "approved"`, `note` e `decided_at`.
**404** → `{"detail": "approval not found"}`.
**409** → `{"detail": "approval already approved"}` (ou `already rejected`).

### `POST /api/campaigns/{id}/approvals/{approval_id}/reject`

Descarta a ação e regista a recusa (mesmo corpo opcional). **200** → a aprovação
com `status: "rejected"`; **404**/**409** como acima.

**Semântica (honesta).** Aprovar/rejeitar é **responder à pergunta** de
aprovação (a resposta segue o caminho de resposta já existente — fica gravada na
memória da campanha em EN, com o registo de auditoria da aprovação). Além disso,
a aprovação decide a ação retida diretamente:

- **Aprovar liberta a ação retida exatamente UMA vez.** O `approval_checker` do
  loop consome a aprovação **mais antiga aprovada** dessa ação exata quando a vai
  despachar — fica marcada com `released_at`/`released_action_id` e **nunca**
  volta a libertar nada — e a ação executa de imediato como se a banda fosse
  `auto`. O registo de decisão do JEV-A fica sinalizado em `issues` com
  `released_by_approval` e é emitido o evento aditivo `approval_released`. Uma
  aprovação explícita também satisfaz o `needs_human` do JEV-A (quem aprova,
  decide — decisão documentada, não omissão).
- **Rejeitar nunca liberta.** Recusas (e aprovações pendentes) nunca satisfazem
  o gate: a ação fica retida e o loop mantém a pergunta. Responder `no`/`reject`
  à pergunta (fila ou WS) rejeita a aprovação espelhada; qualquer outra resposta
  aprova-a (e liberta, pelas mesmas regras).
- **kill_switch/paused ganham sempre**: com o kill-switch ativo ou o modo
  `paused` o loop nem chega a correr, pelo que nada é libertado.

---

### `POST /api/campaigns/{id}/control`

Controlo de autonomia e kill-switch.

**Body**

```json
{ "mode": "autopilot", "kill_switch": false }
```

`mode` ∈ `"autopilot"` | `"checkpoint"` | `"paused"`.

**200**

```json
{ "mode": "autopilot", "kill_switch": false, "ts": "2026-09-27T20:05:00.000Z" }
```

Esta é a chamada que **arranca/para o loop** de decisão (ver "Loop de decisão:
quando corre e o que precisa", acima): `autopilot`/`checkpoint` → corre,
`paused`/kill-switch → parado. O corpo da resposta é e continua a ser exatamente
`{mode, kill_switch, ts}` — o estado do loop não entra aqui (é visível pelos
dados `live`/`stub` e por `GET .../approvals`).

---

### `GET /api/campaigns/{id}/screen`

Texto do ecrã **já traduzido** para português (só PT — o original nunca é mostrado).

**200**

```json
{ "translated_pt": "Encontra-se numa clareira rodeada de árvores.", "ts": "2026-09-27T20:05:00.000Z" }
```

---

## WebSocket

### `WS /ws/campaigns/{id}`

Canal de tempo real por campanha. Todas as mensagens são **JSON** com envelope comum.

**Envelope (servidor → cliente)**

```json
{ "type": "decision", "ts": "2026-09-27T20:04:58.000Z", "payload": { } }
```

| Campo | Tipo | Notas |
|---|---|---|
| `type` | string | Tipo do evento (tabela abaixo) |
| `ts` | string | ISO-8601 UTC |
| `payload` | object | Carga específica do evento |

**Eventos servidor → cliente**

| `type` | `payload` |
|---|---|
| `metrics_update` | Objeto completo de `GET /api/campaigns/{id}/metrics` (8 grupos) |
| `screen_translation` | `{ "translated_pt": "..." }` |
| `decision` | Uma entrada do log de `GET /api/campaigns/{id}/decisions` |
| `question_new` | Uma entrada da fila de `GET /api/campaigns/{id}/questions` — **inclui** os campos aditivos de proveniência (`band`, `role`, `action_id`, `why_pt`, `source_data`) quando existem |
| `memory_update` | Objeto de `GET /api/campaigns/{id}/memory` |
| `spend_update` | Objeto de `GET /api/campaigns/{id}/spend` |
| `game_state` | Estado crítico estruturado em EN enviado ao JEV (o mesmo que alimenta o prompt) |
| `control_state` | `{ "mode": "autopilot", "kill_switch": false }` |
| `question_answered` | **Aditivo** — *mesmo corpo* da resposta de `POST .../questions/{qid}/answer` (`{ "id", "status", "answer_text", "option_id" }`); emitido após uma resposta vinda por REST **ou** por WS |
| `approval_new` | **Aditivo** — uma entrada de `GET .../approvals` (aprovação criada em modo `checkpoint`) |
| `approval_updated` | **Aditivo** — a mesma entrada depois de decidida (`status` `approved`/`rejected`), venha a decisão de `.../approvals/{id}/approve|reject` ou da resposta da pergunta |
| `approval_released` | **Aditivo** — `{ "action_id", "ts", "decision" }`; emitido quando uma aprovação humana é **consumida** para libertar a ação retida (execução única). `decision` é o registo do JEV-A já sinalizado com `released_by_approval` em `issues` |
| `action_executed` | **Aditivo** — `{ "action_id", "ts", "decision" }`; emitido quando a ação é **mesmo despachada** (com ou sem libertação humana). É o que marca o registo do JEV-A com `status: "executed"` no log de decisões |
| `error` | **Aditivo** — `{ "detail": "<mensagem>" }`; erro recuperável do canal (JSON inválido, `question_id` em falta, pergunta inexistente). O canal **não** fecha |

Estes eventos aditivos mantêm-se **aditivos** face ao contrato original: um
cliente que ainda não os conheça continua a funcionar. Regra geral: **o cliente
ignora tipos de evento que não conhece** (nunca são fatais).

**Mensagens cliente → servidor**

```json
{ "type": "answer_question", "question_id": "q-4", "answer_text": "Procurar uma ponte a leste", "option_id": "opt-2" }
```

```json
{ "type": "set_control", "mode": "checkpoint", "kill_switch": false }
```

| `type` | Campos | Equivalente REST |
|---|---|---|
| `answer_question` | `question_id`, `answer_text`, `option_id?` | `POST .../questions/{qid}/answer` |
| `set_control` | `mode`, `kill_switch` | `POST .../control` |

Eventos desconhecidos devem ser ignorados (não fatais) do lado do cliente.
