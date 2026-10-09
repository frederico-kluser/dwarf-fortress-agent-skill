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
| **Array** | `GET .../decisions`, `GET .../questions` | Cabeçalho de resposta **`X-Source: stub`** \| `"live"` (não há onde pôr um campo dentro de um array JSON) |

`X-Source` é um cabeçalho **de resposta** e está exposto por CORS
(`expose_headers`), para que a UI o possa ler. Os valores são exatamente `stub` e
`live`. O objeto `metrics`/`screen` traz **sempre** `source`, e o mesmo endpoint
pode devolver `stub` para uma campanha e `live` para outra.

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
{ "provider": "openrouter", "masked_key": "sk-or-v1-••••••••3f9a", "set": true }
```

### `GET /api/campaigns/{id}/keys`

Devolve **apenas as versões mascaradas** — nunca a chave em claro.

**200**

```json
[
  { "provider": "openrouter", "masked_key": "sk-or-v1-••••••••3f9a", "set": true },
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

**200**

```json
{
  "narrative": "Reach the fortress of Boatmurdered alive.",
  "subgoals": [
    { "id": "sg-1", "text": "Find water", "done": true },
    { "id": "sg-2", "text": "Cross the river", "done": false }
  ]
}
```

### `PUT /api/campaigns/{id}/objective`

Substitui o objetivo completo.

**Body**

```json
{
  "narrative": "Reach the fortress of Boatmurdered alive.",
  "subgoals": [{ "id": "sg-1", "text": "Find water", "done": true }]
}
```

**200** → o objetivo guardado.

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

**`confidence` pode ser `null` (UNAVAILABLE).** O schema alpha permite que a
confidence venha **ausente** (e os fallbacks devolvem `0`), pelo que *ausente* e
`0` são indistinguíveis de uma distribuição plana. Ambos significam
**indisponível** — não "0 % de confiança" e não "certo".

- A UI **nunca** pode renderizar `null` como um `0` real: mostre `—` / `indisponível`
  (um `0` desenhado como barra vazia é uma mentira visual).
- Sem confidence utilizável, a banda fica **limitada a `hitl`** — nunca `auto`
  (ver `docs/arquitetura.md`, "Bandas de ação").
- Para perguntas `noul` a certainty é `|2p − 1|` (não há confidence); `p = 0.5` é
  incerteza total.

---

### `GET /api/campaigns/{id}/questions`

Fila de perguntas/dúvidas ao humano (geradas nas bandas `hitl`/`abstain`).

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
    "status": "pending"
  }
]
```

### `POST /api/campaigns/{id}/questions/{qid}/answer`

**Body**

```json
{ "answer_text": "Procurar uma ponte a leste", "option_id": "opt-2" }
```

`option_id` é opcional (resposta por campo livre). A resposta **vai para a memória**.

**200**

```json
{ "id": "q-4", "status": "answered", "answer_text": "Look for a bridge to the east.", "option_id": "opt-2" }
```

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
| `question_new` | Uma entrada da fila de `GET /api/campaigns/{id}/questions` |
| `memory_update` | Objeto de `GET /api/campaigns/{id}/memory` |
| `spend_update` | Objeto de `GET /api/campaigns/{id}/spend` |
| `game_state` | Estado crítico estruturado em EN enviado ao JEV (o mesmo que alimenta o prompt) |
| `control_state` | `{ "mode": "autopilot", "kill_switch": false }` |
| `question_answered` | **Aditivo** — *mesmo corpo* da resposta de `POST .../questions/{qid}/answer` (`{ "id", "status", "answer_text", "option_id" }`); emitido após uma resposta vinda por REST **ou** por WS |
| `error` | **Aditivo** — `{ "detail": "<mensagem>" }`; erro recuperável do canal (JSON inválido, `question_id` em falta, pergunta inexistente). O canal **não** fecha |

Estes dois eventos são **aditivos** face ao contrato original: um cliente que ainda
não os conheça continua a funcionar. Regra geral: **o cliente ignora tipos de evento
que não conhece** (nunca são fatais).

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
