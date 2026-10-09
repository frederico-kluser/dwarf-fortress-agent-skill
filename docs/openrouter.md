# OpenRouter no System One Dwarf

Como este projeto integra a API do OpenRouter, alinhado com a
`openrouter-agent-skill`. Três clientes HTTP partilham a mesma convenção
(attribution, retries, contabilidade de custo, mapeamento de erros) e correm
100% offline em testes via transport injectável:

| Cliente | Módulo | Papel |
|---|---|---|
| `JevClient` | `src/system_one_dwarf/jev/client.py` | decisões tipadas (System One) |
| `DeepSeekLLM` | `src/system_one_dwarf/agent/llm.py` | narrador + perguntas ao humano |
| `DeepSeekTranslator` | `src/system_one_dwarf/translate/translator.py` | tradução de ecrã EN↔PT |

## Endpoints usados

| Método + URL | Quem | Para quê |
|---|---|---|
| `POST https://openrouter.ai/api/alpha/decisions` | `JevClient` | schema alpha do Jev: `state` + perguntas tipadas (`noul`/`choice`/`score`) → respostas com probabilidades calibradas |
| `POST https://openrouter.ai/api/v1/chat/completions` | `DeepSeekLLM`, `DeepSeekTranslator` | chat completions (formato OpenAI) para prosa e tradução |

Autenticação em ambos: `Authorization: Bearer <OPENROUTER_API_KEY>`.

## Modelos

| id (pinned) | Finalidade | Preço (USD/Mtok) | Contexto | Endpoint |
|---|---|---|---|---|
| `typesafe/jev-1.13` | decisões discretas calibradas (JEV-R/A/M/D) | **$0.042 entrada · saída grátis** | 64K tokens/pedido (janela do modelo); o validador local impõe 32K tokens estimados (`jev/validate.py`) | `/api/alpha/decisions` |
| `deepseek/deepseek-v4.1-flash` | narrador, perguntas, tradução | **$0.30 entrada · $1.20 saída** | **1M tokens** (o clamp de `max_tokens` usa este teto) | `/api/v1/chat/completions` |

Os preços são os do cartão do modelo; **o valor cobrado é o do provider
selecionado** e sai em `usage.cost` na resposta (ver gotcha 19 abaixo). Os
defaults estão fixados nos três clientes (`DEFAULT_MODEL` / `DEFAULT_ENDPOINT`),
cobertos por testes.

### Aliases e porquê pinado

O OpenRouter tem alias dinâmicos, ex. `~typesafe/jev-latest`, que resolvem para
o snapshot mais novo da família (o campo `model` da resposta devolve o concreto,
ex. `typesafe/jev-1.13-20260917`). **Nunca calibramos contra um alias**: as
probabilidades do Jev são calibradas por snapshot (ver `docs/calibracao.md`) e
os limiares `auto`/`hitl` não são transferíveis entre snapshots — por isso os
defaults são `typesafe/jev-1.13` e `deepseek/deepseek-v4.1-flash`, fixos. Quando
o `model` resolvido numa resposta mudar, é sinal para repetir a calibração
(`evals/` mede accuracy + ECE por bin).

## Headers de attribution (gotcha 11)

Toda a request de saída leva, além do `Authorization`:

```
HTTP-Referer: https://github.com/frederico-kluser/dwarf-fortress-agent-skill
X-OpenRouter-Title: System One Dwarf
```

O header `X-OpenRouter-App` **não existe** para attribution e nunca é enviado
(alias oficial: `X-Title`). Os testes de cada cliente (fake transport) afirmam
os dois headers e a ausência do errado.

## Chaves de API

* Formato `sk-or-v1-…`, criadas em https://openrouter.ai/keys (definir credit
  limit); gestão/revogação em https://openrouter.ai/settings/keys.
* **Armazenamento**: `KeyStore` por campanha
  (`src/system_one_dwarf/campaign/keys.py` — SQLite em diretório `0700`,
  ficheiro forçado a `0600`), acedida via `KeyStore.get_key("openrouter")` e
  injectada nos clientes pelo callable `get_key`. A API
  `GET /api/campaigns/{id}/keys` devolve **apenas chaves mascaradas**.
* Para scripts/CLI fora do backend: `OPENROUTER_API_KEY` no ambiente (`.env`
  fora do git); a skill `openrouter-agent-skill` tem
  `scripts/elevenlabs.sh check` para localizar credenciais já guardadas
  (ambiente → `./.env` → `~/.secrets` → `~/.zshenv` → `~/.dsh/.credentials.yaml`).
* **Higiene**: a chave nunca aparece em logs, mensagens de erro ou `repr` dos
  clientes (coberto por testes) e nunca é commitada.

## Roteamento

* **Default (sem `provider`)**: load balancing por preço (peso ∝ 1/preço²,
  priorizando providers sem outage) com fallback automático
  (`allow_fallbacks: true`). É o que usamos hoje em todas as chamadas.
* Para forçar um provider: acrescentar ao body
  `"provider": {"only": ["<tag>"]}` (o campo é `tag`, ex. `openai` — nunca
  `provider_id`; gotcha 1). Nenhum provider compatível → **404**.
* Teto de custo estrito: `"provider": {"max_price": {"prompt": 1, "completion": 2}}`
  — USD por **milhão** de tokens; a request não corre se o preço não estiver
  disponível/dentro do teto.
* Ordem sem fallback: `"provider": {"order": ["a", "b"], "allow_fallbacks": false}`.
* Fallback de modelo: `"models": ["<id-1>", "<id-2>"]` (cobra-se o modelo
  efetivamente usado, campo `model` da resposta).
* Sem provider disponível → **503** ("no available model provider that meets
  your routing requirements"): relaxar `provider`/`max_price` ou adicionar
  fallbacks. Variantes (`:free`, `:nitro`, …), plugins e cache:
  `references/routing.md` da skill.

## Conformidade com as gotchas da skill

| Gotcha | Como cumprimos |
|---|---|
| 1 — `tag` identifica o provider | nas notas de roteamento acima; `provider.only`/`order` usam `tag` |
| 11 — attribution não é `X-OpenRouter-App` | `HTTP-Referer` + `X-OpenRouter-Title` em **todas** as requests dos 3 clientes; testes afirmam ambos e a ausência do errado |
| 13 — enum de `reasoning.effort` | só o objeto **nested** `reasoning: {"effort": …}` / `{"enabled": false}`; enum `max/xhigh/high/medium/low/minimal/none` (sem `"min"`); nunca o campo top-level `reasoning_effort` — testes parametrizados por nível afirmam o shape e a ausência do top-level |
| 14 — `reasoning_tokens` | nunca lidos do topo de `usage`; os tokens de raciocínio vêm já contados em `completion_tokens` (cobrados como saída) e é esse o total que contabilizamos |
| 17 — `max_tokens` vs contexto | `clamp_max_tokens(requested, prompt_tokens)` = `min(requested, context_length − prompt_tokens)` (contexto 1M do deepseek-v4.1-flash), um helper partilhado por `agent/llm.py` e `translate/translator.py`; testes cobrem os dois call sites |
| 18 — SDKs não honram `Retry-After` | backoff manual nos 3 clientes: `Retry-After` (segundos ou HTTP-date) honrado com cap de **30 s**, exponencial quando ausente, **máx. 3 tentativas**; 401/402 terminais, 429/5xx retentáveis |
| 19 — preço não é fixo | custo = `usage.cost` quando presente (valor do provider), senão estimativa por token pricing (`$0.042/Mtok` Jev entrada, saída grátis; `$0.30/$1.20/Mtok` deepseek), sempre no contrato de dinheiro de **6 decimais** (`money()`); `raw` guarda o valor original |
| 20 — erros com HTTP 200 | os 3 clientes inspecionam o body: um `{"error": …}` com status 200 é mapeado (code → status; mensagens classificadas: rate limit → 429 retentado, créditos → 402, auth → 401, resto → 400) e tratado como o erro que é |
| 21 — modelos `:free` | não usamos variantes `:free`; os defaults são pagos e os limites free-tier não se aplicam |

## Monitorização de spend

* **Lado OpenRouter**:
  * `GET https://openrouter.ai/api/v1/key` → `limit`, `limit_remaining`,
    `usage_monthly`, `is_free_tier`;
  * `GET https://openrouter.ai/api/v1/credits` → `total_credits`/`total_usage`
    (requer management key);
  * custo exato por request: `usage.cost` na resposta; detalhe (tokens, custo,
    latência) via header `X-Generation-Id` → `GET /api/v1/generation?id=…`;
  * decisão de roteamento: header `X-OpenRouter-Metadata: enabled` → campo
    `openrouter_metadata` na resposta.
* **CLI da skill** (existe em
  `~/.agents/skills/openrouter-agent-skill/scripts/openrouter.sh`):
  `./openrouter.sh key` (limites da chave atual) e `./openrouter.sh credits`
  (créditos comprados/usados; requer management key) — úteis para um check
  rápido sem escrever curls.
* **Lado projeto**: cada chamada bem-sucedida alimenta o `SpendLedger`
  (`campaign/spend.py`, 6 decimais) via `usage_callback`/`spend_recorder`; o
  total por campanha sai em `GET /api/campaigns/{id}/spend`. Um callback de
  accounting que falha é engolido — nunca parte o game loop.
