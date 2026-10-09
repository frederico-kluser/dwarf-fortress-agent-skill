/**
 * Tipos do contrato REST + WebSocket — espelho EXATO de `docs/api.md`.
 *
 * Convenções do contrato (docs/api.md §Convenções):
 *  - erros: `{ "detail": "<mensagem>" }` com o código HTTP adequado;
 *  - timestamps: ISO-8601 em UTC com sufixo `Z` (ex.: `2026-09-27T19:44:03.512Z`);
 *  - dinheiro: `number` em USD com 6 casas decimais (ex.: `0.042318`);
 *  - texto: campos em EN, exceto os marcados `*_pt` (traduzidos para português);
 *  - ids: string opaca (UUID4) — o cliente nunca a interpreta;
 *  - chaves de API: nunca devolvidas em claro (só mascaradas).
 *
 * Nada aqui deve divergir de `docs/api.md`; o backend é a outra metade do contrato.
 */

/* ------------------------------------------------------------------ base --- */

/** ISO-8601 UTC com sufixo `Z`. */
export type Iso8601 = string;

/** USD com 6 casas decimais. */
export type Usd = number;

/** `POST /api/campaigns/{id}/control` — `mode` ∈ autopilot | checkpoint | paused. */
export type ControlMode = 'autopilot' | 'checkpoint' | 'paused';

/** `POST /api/campaigns/{id}/keys` — `provider` ∈ openrouter | typesafe | deepseek. */
export type Provider = 'openrouter' | 'typesafe' | 'deepseek';

/** `agent` do log de decisões JEV. */
export type JevAgent = 'JEV-R' | 'JEV-A' | 'JEV-M' | 'JEV-D';

/** Papéis JEV do contrato — guard para payloads que podem trazer lixo. */
export const JEV_AGENT_ROLES: readonly JevAgent[] = ['JEV-R', 'JEV-A', 'JEV-M', 'JEV-D'];

/** Banda de ação: auto ≥ 0.90 · hitl 0.50–0.89 · abstain < 0.50. */
export type DecisionBand = 'auto' | 'hitl' | 'abstain';

/** Bandas do contrato — guard para campos aditivos de proveniência. */
export const DECISION_BANDS: readonly DecisionBand[] = ['auto', 'hitl', 'abstain'];

/** Estado da pergunta na fila. */
export type QuestionStatus = 'pending' | 'answered';

/**
 * Desfecho da ação escolhida numa decisão (`Decision.status`, ADITIVO).
 * `docs/api.md` §decisions: `executed` quando a ação despachou (mesmo sem
 * aprovação) ou quando uma aprovação humana a libertou; `question` quando a
 * decisão virou pergunta ao humano; `error` quando o passo falhou antes de
 * executar. **Omitido quando desconhecido** (decisões auxiliares JEV-R/M/D).
 */
export type DecisionStatus = 'executed' | 'question' | 'error';

/** Desfechos do contrato — guard para payloads que podem trazer lixo. */
export const DECISION_STATUSES: readonly DecisionStatus[] = ['executed', 'question', 'error'];

/** `true` se o valor for um desfecho de decisão conhecido. */
export function isDecisionStatus(value: unknown): value is DecisionStatus {
  return typeof value === 'string' && (DECISION_STATUSES as readonly string[]).includes(value);
}

/** `true` se o valor for um papel JEV conhecido (`meta.role` do pedido). */
export function isJevAgent(value: unknown): value is JevAgent {
  return typeof value === 'string' && (JEV_AGENT_ROLES as readonly string[]).includes(value);
}

/** `true` se o valor for uma banda de ação do contrato. */
export function isDecisionBand(value: unknown): value is DecisionBand {
  return typeof value === 'string' && (DECISION_BANDS as readonly string[]).includes(value);
}

/**
 * Estado de uma aprovação do modo `checkpoint`.
 * `docs/api.md` §`GET .../approvals`.
 */
export type ApprovalStatus = 'pending' | 'approved' | 'rejected';

/** Filtro de `GET .../approvals?status=…` (por omissão só as pendentes). */
export type ApprovalStatusFilter = ApprovalStatus | 'all';

/**
 * Paginação aditiva de `GET .../decisions` e `GET .../questions`
 * (`?limit=<n>&before=<ts ISO-8601>`): `limit` devolve os registos mais
 * recentes (pela ordem antiga dentro da página) e `before` é um cursor
 * **exclusivo** por `ts` — só registos estritamente anteriores.
 * Sem parâmetros o comportamento antigo está intacto.
 */
export interface ListPageQuery {
  limit?: number;
  before?: Iso8601;
}

/** Sufixo de query da paginação — vazio quando não há parâmetros. */
function pageQuerySuffix(page?: ListPageQuery): string {
  const params = new URLSearchParams();
  if (typeof page?.limit === 'number') params.set('limit', String(page.limit));
  if (page?.before) params.set('before', page.before);
  const query = params.toString();
  return query ? `?${query}` : '';
}

/** Caminhos canónicos — um só sítio para todos os endpoints do contrato. */
export const API_PATHS = {
  health: '/api/health',
  campaigns: '/api/campaigns',
  campaign: (id: string) => `/api/campaigns/${encodeURIComponent(id)}`,
  keys: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/keys`,
  memory: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/memory`,
  objective: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/objective`,
  inputs: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/inputs`,
  metrics: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/metrics`,
  spend: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/spend`,
  decisions: (id: string, page?: ListPageQuery) =>
    `/api/campaigns/${encodeURIComponent(id)}/decisions${pageQuerySuffix(page)}`,
  questions: (id: string, page?: ListPageQuery) =>
    `/api/campaigns/${encodeURIComponent(id)}/questions${pageQuerySuffix(page)}`,
  answer: (id: string, qid: string) =>
    `/api/campaigns/${encodeURIComponent(id)}/questions/${encodeURIComponent(qid)}/answer`,
  control: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/control`,
  screen: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/screen`,
  /** `status` omitido ⇒ o servidor devolve só as pendentes. */
  approvals: (id: string, status?: ApprovalStatusFilter) =>
    `/api/campaigns/${encodeURIComponent(id)}/approvals${status ? `?status=${encodeURIComponent(status)}` : ''}`,
  approve: (id: string, approvalId: string) =>
    `/api/campaigns/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/approve`,
  reject: (id: string, approvalId: string) =>
    `/api/campaigns/${encodeURIComponent(id)}/approvals/${encodeURIComponent(approvalId)}/reject`,
  /** Saves do DF + aventura atual (`docs/api.md` §aventuras). */
  adventures: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures`,
  newWorld: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures/new-world`,
  newCharacter: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures/new-character`,
  loadAdventure: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures/load`,
  adventuresStatus: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures/status`,
  adventuresCancel: (id: string) => `/api/campaigns/${encodeURIComponent(id)}/adventures/cancel`,
  /** Sistema global (`docs/api.md` §sistema) — fora do âmbito da campanha. */
  systemStatus: '/api/system/status',
  systemGame: '/api/system/game',
  ws: (id: string) => `/ws/campaigns/${encodeURIComponent(id)}`,
} as const;

/** Erro devolvido pelo backend (formato FastAPI `HTTPException`). */
export interface ApiErrorBody {
  detail: string;
}

/* -------------------------------------------------------------- campanhas --- */

/** `GET /api/health` */
export interface HealthResponse {
  status: string;
  version: string;
}

/** Controlo de autonomia guardado na campanha. */
export interface CampaignControl {
  mode: ControlMode;
  kill_switch: boolean;
}

/** `GET /api/campaigns` → item · `GET /api/campaigns/{id}` → objeto. */
export interface Campaign {
  id: string;
  name: string;
  is_new_game: boolean;
  created_at: Iso8601;
  updated_at: Iso8601;
  control: CampaignControl;
}

/** `POST /api/campaigns` body */
export interface CreateCampaignRequest {
  /** Nome da campanha; define o caminho da memória CoALA. */
  name: string;
  /** `true` = jogo novo (memória vazia); `false` = gameplay existente. */
  is_new_game: boolean;
}

/** `POST /api/campaigns/{id}/keys` body */
export interface SetKeyRequest {
  provider: Provider;
  key: string;
}

/** `POST|GET /api/campaigns/{id}/keys` — sempre mascarada. */
export interface KeyInfo {
  provider: Provider;
  masked_key: string | null;
  set: boolean;
}

/* ---------------------------------------------------------------- memória --- */

export interface MemoryEpisode {
  id: string;
  ts: Iso8601;
  /** episodic | semantic | procedural | … */
  kind: string;
  /** Conteúdo canónico em EN. */
  content: string;
  /** Exibição traduzida em PT. */
  content_pt: string;
}

/** `GET /api/campaigns/{id}/memory` */
export interface Memory {
  /** Linha narrativa canónica (EN). */
  narrative: string;
  /** Linha narrativa traduzida (PT) — o que a UI mostra. */
  narrative_pt: string;
  episodes: MemoryEpisode[];
  /** Memória de trabalho (EN; o contrato não define variante `_pt`). */
  working_memory: string;
  updated_at: Iso8601;
}

/**
 * `PATCH /api/campaigns/{id}/memory` body (parcial — enviar só o que muda).
 * O servidor traduz PT→EN antes de guardar. A UI envia apenas `narrative`.
 */
export interface PatchMemoryRequest {
  narrative?: string;
  episodes?: MemoryEpisode[];
}

/* --------------------------------------------------------------- objetivo --- */

export interface Subgoal {
  id: string;
  /** Texto canónico (EN). */
  text: string;
  /** Exibição traduzida (PT) — aditivo, só vem quando difere do canónico. */
  text_pt?: string;
  done: boolean;
}

/** `GET|PUT /api/campaigns/{id}/objective` */
export interface Objective {
  /** Objetivo canónico (EN). */
  narrative: string;
  /** Exibição traduzida (PT) — aditivo, só vem quando difere do canónico. */
  narrative_pt?: string;
  subgoals: Subgoal[];
}

/* ----------------------------------------------------------------- inputs --- */

/** `POST /api/campaigns/{id}/inputs` body — texto livre do utilizador (PT). */
export interface CreateInputRequest {
  text: string;
}

/** `201` do `POST /api/campaigns/{id}/inputs` — `text` já traduzido para EN. */
export interface InputInjection {
  id: string;
  text: string;
  ts: Iso8601;
}

/**
 * Entrada do histórico de inputs.
 *
 * `docs/api.md` define apenas o `POST .../inputs` (não há `GET`), por isso o
 * histórico é mantido **localmente pelo cliente** (localStorage por campanha) e
 * junta o texto em PT que o utilizador escreveu ao texto em EN devolvido pelo
 * servidor. Não é um endpoint novo: se o backend passar a expor `GET .../inputs`,
 * basta trocar a implementação de `listInputs`.
 */
export interface InputHistoryEntry extends InputInjection {
  /** Texto tal como o utilizador o escreveu (PT). */
  text_pt: string;
}

/* --------------------------------------------------------------- métricas --- */

export interface MetricsHealth {
  /**
   * Forma não fixada em `docs/api.md` (o exemplo traz `[]`): a UI mostra o que
   * existir e ignora o resto. Campos esperados: parte do corpo, gravidade, ts.
   */
  wounds: WoundEntry[];
  /** healthy | wounded | … */
  status: string;
}

export interface WoundEntry {
  part?: string;
  severity?: string;
  ts?: Iso8601;
  [key: string]: unknown;
}

export interface MetricsNeeds {
  /** none | … */
  hunger: string;
  /** none | … */
  thirst: string;
  /** rested | drowsy | … */
  sleep: string;
}

export interface MetricsInventory {
  weight_kg: number;
  /** Lista de itens (o exemplo traz `[]`). */
  items: string[];
}

export interface MetricsThreats {
  /** low | medium | high | … */
  level: string;
  combat: boolean;
  hostiles: string[];
}

export interface MetricsLocation {
  region: string;
  path: string[];
}

export interface MetricsObjectiveProgress {
  /** 0..1 */
  percent: number;
  active_subgoal_id: string | null;
}

/**
 * `combat_history` — forma não fixada em `docs/api.md` (o exemplo traz `[]`).
 * Renderização defensiva: os campos são opcionais.
 */
export interface CombatEntry {
  ts?: Iso8601;
  opponent?: string;
  outcome?: string;
  damage?: string;
  notes?: string;
  [key: string]: unknown;
}

/** Métricas do agente (decisões/min, confidence média, chamadas, custo, latência). */
export interface MetricsAgent {
  decisions_per_min: number;
  /** 0..1 */
  confidence_avg: number;
  calls: number;
  cost_usd: Usd;
  latency_ms: number;
}

/** `GET /api/campaigns/{id}/metrics` — os 8 grupos exatos do contrato. */
export interface Metrics {
  ts: Iso8601;
  health: MetricsHealth;
  needs: MetricsNeeds;
  inventory: MetricsInventory;
  threats: MetricsThreats;
  location: MetricsLocation;
  objective_progress: MetricsObjectiveProgress;
  combat_history: CombatEntry[];
  agent: MetricsAgent;
}

/* ---------------------------------------------------------------- gastos --- */

export interface SpendModel {
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: Usd;
}

export interface SpendProvider {
  provider: Provider;
  models: SpendModel[];
}

/** `GET /api/campaigns/{id}/spend` — sem teto de orçamento, só visibilidade. */
export interface Spend {
  total_usd: Usd;
  providers: SpendProvider[];
  ts: Iso8601;
}

/* -------------------------------------------------------------- decisões --- */

/** `GET /api/campaigns/{id}/decisions` → item do log JEV. */
export interface Decision {
  id: string;
  ts: Iso8601;
  agent: JevAgent;
  /** Resumo do pedido (tipos de pergunta + nº de candidatos). */
  request_summary: string;
  /** Respostas tipadas devolvidas pelo JEV. */
  answers: Record<string, unknown>;
  /**
   * Distribuição por opção (`choice`). Pode vir **`null`**: decisões que só
   * fazem perguntas `noul`/`score` (JEV-R/M/D) não têm distribuição — `null`
   * nunca pode ser convertido em `{}` (um objeto vazio afirmaria uma
   * distribuição que não existe).
   */
  probabilities: Record<string, number> | null;
  /**
   * Pode vir **ausente ou 0** = indisponível — NUNCA ler como "certo".
   * A UI mostra "indisponível" nesses casos.
   */
  confidence?: number | null;
  band: DecisionBand;
  latency_ms: number;
  cost_usd: Usd;
  /**
   * Notas de validação/qualidade do JEV e marcas de auditoria (ex.
   * `released_by_approval`) — **ADITIVO**, omitido quando vazio.
   */
  issues?: string[];
  /**
   * Desfecho da ação escolhida — **ADITIVO**, omitido quando desconhecido
   * (decisões auxiliares JEV-R/M/D).
   */
  status?: DecisionStatus;
}

/* -------------------------------------------------------------- perguntas --- */

export interface QuestionOption {
  id: string;
  text: string;
}

/**
 * Diagnóstico opcional que acompanha uma pergunta com proveniência.
 * `docs/api.md`: `reason`, `band`, `action_mode`/`answer_mode`, `action_en`,
 * `description_en`, confiança, candidatos… — a forma não é fixa, pode vir `{}`.
 */
export type QuestionSourceData = Record<string, unknown>;

/** `GET /api/campaigns/{id}/questions` → item da fila (hitl/abstain). */
export interface Question {
  id: string;
  ts: Iso8601;
  /** Pergunta em PT. */
  question: string;
  options: QuestionOption[];
  free_text_allowed: boolean;
  status: QuestionStatus;
  /**
   * Presentes na resposta do `POST .../answer`; o `GET` do contrato não os
   * garante, por isso a UI mantém a resposta localmente quando faltarem.
   */
  answer_text?: string | null;
  option_id?: string | null;

  /*
   * Proveniência (achado #2 de `docs/uxui-audit-ui.json`) — campos ADITIVOS: só
   * vêm quando são conhecidos, pelo que a UI degrada em silêncio quando faltam
   * (um produtor antigo devolve exatamente os 6 campos originais).
   */
  /** Banda que originou a pergunta. */
  band?: DecisionBand;
  /** Papel JEV que a originou (`JEV-A` quando há ação retida, `JEV-D` na dúvida). */
  role?: JevAgent;
  /** A ação retida à espera de resposta (ex.: `goto:river_bank`). */
  action_id?: string;
  /** **PT** (camada human-facing, como `question`): porque o agente pergunta. */
  why_pt?: string;
  /** Diagnóstico opcional do pedido que a gerou. */
  source_data?: QuestionSourceData;

  /*
   * Dedupe (anti-flood) — campos ADITIVOS: o modo `checkpoint` volta a pedir a
   * mesma `action_id` a cada passo do loop; enquanto houver pendente daquela
   * ação, o re-pedido não cria outra entrada — a pendente ganha estes campos.
   * **Omitidos na primeira vez** (produtor antigo não os manda).
   */
  /** Quantas vezes o loop voltou a pedir a **mesma** ação. */
  attempts?: number;
  /** Ts do último re-pedido da mesma ação. */
  last_seen_ts?: Iso8601;
}

/** `POST /api/campaigns/{id}/questions/{qid}/answer` body. */
export interface AnswerQuestionRequest {
  answer_text: string;
  option_id?: string;
}

/** `200` do `POST .../answer` (a resposta vai para a memória). */
export interface AnswerQuestionResponse {
  id: string;
  status: QuestionStatus;
  /** Já traduzida para EN pelo servidor. */
  answer_text: string;
  option_id?: string | null;
}

/* -------------------------------------------------------------- aprovações --- */

/**
 * `GET /api/campaigns/{id}/approvals` → item (modo `checkpoint`).
 *
 * Superfície de aprovação do achado #4 de `docs/uxui-audit-ui.json`: o loop retém
 * **todas** as ações e levanta uma pergunta de aprovação; o backend espelha-a
 * aqui. Em `autopilot` a lista nasce vazia (não há ações retidas).
 */
export interface Approval {
  /** Id da pergunta de aprovação que a originou — chave de `approve`/`reject`. */
  id: string;
  /** O mesmo id (o contrato mantém os dois nomes). */
  question_id?: string | null;
  ts: Iso8601;
  /** A ação retida (id de macro-ação). */
  action_id: string;
  /** Nome da ação em EN (cai no `action_id` quando o loop não traz o nome). */
  action_name_en: string;
  /** A pergunta em PT que o humano leu (pode faltar). */
  question?: string | null;
  /** Banda da decisão retida. */
  band: DecisionBand;
  status: ApprovalStatus;
  /** Só depois de decidida — a nota humana (omitida quando não há). */
  note?: string | null;
  /** Só depois de decidida. */
  decided_at?: Iso8601 | null;
  /** Opcional (quando o loop a publica). */
  description_en?: string | null;
  /**
   * Dedupe — **ADITIVO**: quantas vezes o loop voltou a pedir a **mesma** ação
   * (omitido na primeira vez). Enquanto houver pendente desta `action_id`, o
   * re-pedido não cria outra entrada; decidir uma entrada descarta duplicados
   * pendentes da mesma ação. A fila de pendentes está limitada a 20.
   */
  attempts?: number;
  /** Ts do último re-pedido da mesma ação (omitido na primeira vez). */
  last_seen_ts?: Iso8601;
}

/** Corpo opcional de `POST .../approvals/{id}/approve|reject`. */
export interface ApprovalDecisionRequest {
  note?: string;
}

/* --------------------------------------------------------------- controlo --- */

/** `POST /api/campaigns/{id}/control` body. */
export interface ControlRequest {
  mode: ControlMode;
  kill_switch: boolean;
}

/** `200` do `POST .../control` (o `control_state` do WS não traz `ts`). */
export interface ControlState {
  mode: ControlMode;
  kill_switch: boolean;
  ts?: Iso8601;
}

/* ------------------------------------------------------------------ ecrã --- */

/** `GET /api/campaigns/{id}/screen` — só PT; o original nunca é mostrado. */
export interface ScreenTranslation {
  translated_pt: string;
  ts: Iso8601;
}

/* -------------------------------------------------------------- aventuras --- */

/**
 * Tipo de save derivado da pasta de saves do DF (`docs/api.md` §aventuras):
 * `active` = `world.sav` presente (jogo já iniciado, carregável com `load-save`);
 * `world` = só `world.dat` (mundo gerado, nenhum jogo iniciado).
 */
export type AdventureKind = 'active' | 'world';

/** Tipos de save do contrato — guard para payloads que podem trazer lixo. */
export const ADVENTURE_KINDS: readonly AdventureKind[] = ['active', 'world'];

/** `true` se o valor for um tipo de save conhecido. */
export function isAdventureKind(value: unknown): value is AdventureKind {
  return typeof value === 'string' && (ADVENTURE_KINDS as readonly string[]).includes(value);
}

/**
 * Tipo de tarefa de aventura (`docs/api.md` §tarefas de aventura):
 * `new_world` (worldgen) · `new_character` (menus de personagem) · `load`.
 */
export type AdventureTaskKind = 'new_world' | 'new_character' | 'load';

/** Tipos de tarefa do contrato — guard para payloads que podem trazer lixo. */
export const ADVENTURE_TASK_KINDS: readonly AdventureTaskKind[] = [
  'new_world',
  'new_character',
  'load',
];

/** `true` se o valor for um tipo de tarefa de aventura conhecido. */
export function isAdventureTaskKind(value: unknown): value is AdventureTaskKind {
  return typeof value === 'string' && (ADVENTURE_TASK_KINDS as readonly string[]).includes(value);
}

/** Estados do contrato (fixos): `running` | `done` | `error`. */
export type AdventureTaskState = 'running' | 'done' | 'error';

/** Estados de tarefa do contrato — guard para payloads que podem trazer lixo. */
export const ADVENTURE_TASK_STATES: readonly AdventureTaskState[] = ['running', 'done', 'error'];

/** `true` se o valor for um estado de tarefa de aventura conhecido. */
export function isAdventureTaskState(value: unknown): value is AdventureTaskState {
  return typeof value === 'string' && (ADVENTURE_TASK_STATES as readonly string[]).includes(value);
}

/**
 * Parâmetros de worldgen (`POST .../adventures/new-world`):
 * `quick` = tenta encurtar o worldgen (passo *soft*); `default` = aceita o ecrã.
 */
export type WorldParams = 'quick' | 'default';

/** Parâmetros de worldgen do contrato — guard para payloads que podem trazer lixo. */
export const WORLD_PARAMS: readonly WorldParams[] = ['quick', 'default'];

/** Um save da pasta `<df>/data/save/` (parsing best-effort do backend). */
export interface AdventureSave {
  /** Nome da pasta do save — é o argumento do `load-save` do DFHack. */
  id: string;
  /** Nome da pasta sem o sufixo sazonal `region#-AAAA-MM-DD` (best-effort). */
  name: string;
  kind: AdventureKind;
  /** Caminho absoluto da pasta do save. */
  folder: string;
  /** `mtime` de `world.sav`/`world.dat` (ou da pasta), ISO-8601 UTC. */
  saved_at: Iso8601;
  /** Omitido hoje — nomes de personagens não são parseáveis offline. */
  characters?: string[];
}

/** Aventura atual, do último snapshot do jogo (`null` sem aventureiro). */
export interface CurrentAdventure {
  /** Preenchido quando é conhecido (última carga ou pasta com o nome do mundo). */
  adventure_id?: string;
  adventurer_name: string;
  world_name: string;
  /** Best-effort: `false` quando o gamelog marca a morte do aventureiro. */
  alive: boolean;
}

/** `GET .../adventures` — lista de saves + aventura atual. */
export interface AdventuresResponse {
  adventures: AdventureSave[];
  current: CurrentAdventure | null;
}

/**
 * Tarefa assíncrona de aventura (`docs/api.md` §tarefas de aventura). Uma de
 * cada vez por campanha; cada passo corre com ≤ 5 tentativas e, na falha final,
 * `error` traz o relatório PT (passo, tentativas, ações, telas).
 */
export interface AdventureTask {
  id: string;
  kind: AdventureTaskKind;
  state: AdventureTaskState;
  step: number;
  steps_total: number;
  /** Último ecrã traduzido, calculado dentro da tarefa (uma tradução por passo). */
  last_screen_pt?: string;
  /** Relatório PT do erro, ou `"cancelled"` quando cancelada pelo humano. */
  error?: string;
}

/** `payload` do evento WS `adventure_task` (aditivo). */
export interface AdventureTaskEvent {
  task_id: string;
  kind: AdventureTaskKind;
  state: AdventureTaskState;
  step: number;
  steps_total: number;
  last_screen_pt?: string;
  error?: string;
}

/** Evento WS `adventure_task` → objeto `task` do `GET .../adventures/status`. */
export function adventureTaskFromEvent(event: AdventureTaskEvent): AdventureTask {
  return {
    id: event.task_id,
    kind: event.kind,
    state: event.state,
    step: event.step,
    steps_total: event.steps_total,
    ...(event.last_screen_pt ? { last_screen_pt: event.last_screen_pt } : {}),
    ...(event.error ? { error: event.error } : {}),
  };
}

/** `202` dos três `POST` (`new-world`, `new-character`, `load`). */
export interface AdventureTaskStart {
  task_id: string;
  kind: AdventureTaskKind;
}

/** `GET .../adventures/status` — progresso ao vivo (nunca bloqueia). */
export interface AdventureStatusResponse {
  /** `null` quando esta campanha ainda não correu nenhuma tarefa. */
  task: AdventureTask | null;
  current: CurrentAdventure | null;
}

/** `POST .../adventures/new-world` — `world_name` é *soft* (nunca aborta). */
export interface NewWorldRequest {
  world_name?: string;
  params: WorldParams;
}

/** `POST .../adventures/new-character` — sem parâmetros aceita tudo. */
export interface NewCharacterRequest {
  /** *Soft*: clica o campo de nome e escreve o texto. */
  name?: string;
  /** Guia o label `Select <race>` (predefinição: `human`). */
  race?: string;
  /** Guia o label `Select <origin>` (predefinição: a primeira origem visível). */
  origin?: string;
  /** *Soft*: clica labels `Randomize` antes de aceitar aparência/personalidade. */
  randomize: boolean;
}

/** `POST .../adventures/load` — `adventure_id` é o nome da pasta do save. */
export interface LoadAdventureRequest {
  adventure_id: string;
}

/** `POST .../adventures/cancel` — o motor para de enviar teclas. */
export interface AdventureCancelResponse {
  cancelled: boolean;
  task: AdventureTask | null;
}

/* ---------------------------------------------------------------- sistema --- */

/** Processo do jogo — só no caminho configurado (nunca há caminho no pedido). */
export interface SystemGameStatus {
  running: boolean;
  /** Opcional: falta quando o jogo está em baixo. */
  pid?: number;
  /** Sonda TCP best-effort da porta RPC do DFHack. */
  rpc: boolean;
  path: string;
}

export interface SystemBackendStatus {
  version: string;
}

export interface SystemFrontendStatus {
  running: boolean;
  port: number;
}

/** Loop de decisão de uma campanha (id, modo de controlo e se está a correr). */
export interface SystemLoopCampaign {
  id: string;
  mode: ControlMode;
  running: boolean;
}

export interface SystemLoopStatus {
  campaigns: SystemLoopCampaign[];
}

/** `GET /api/system/status` — estado global do sistema. */
export interface SystemStatus {
  game: SystemGameStatus;
  backend: SystemBackendStatus;
  frontend: SystemFrontendStatus;
  loop: SystemLoopStatus;
}

/** `POST /api/system/game` — arranca/pára o processo do jogo. */
export type GameAction = 'start' | 'stop';

export interface GameActionRequest {
  action: GameAction;
}

/** `200` do `POST /api/system/game`. */
export interface GameActionResponse {
  action: GameAction;
  running: boolean;
  pid?: number;
  /** `true` quando o jogo já estava a correr no `start` (não lança outro). */
  already_running?: boolean;
}

/* ------------------------------------------------------------- websocket --- */

/**
 * Estado crítico estruturado em EN enviado ao JEV (o mesmo que alimenta o
 * prompt). `docs/api.md` não fixa a forma — o cliente ignora-o.
 */
export type GameState = Record<string, unknown>;

/** `payload` por tipo de evento servidor → cliente. */
export interface ServerEventPayloads {
  metrics_update: Metrics;
  screen_translation: { translated_pt: string };
  decision: Decision;
  question_new: Question;
  memory_update: Memory;
  spend_update: Spend;
  game_state: GameState;
  control_state: ControlState;
  /** Aditivo — mesmo corpo do `POST .../questions/{qid}/answer`. */
  question_answered: AnswerQuestionResponse;
  /** Aditivo — aprovação criada em modo `checkpoint`. */
  approval_new: Approval;
  /** Aditivo — a mesma aprovação depois de decidida. */
  approval_updated: Approval;
  /** Aditivo — progresso de uma tarefa de aventura (cada transição de estado/passo). */
  adventure_task: AdventureTaskEvent;
  /** Aditivo — erro recuperável do canal (`{"detail": …}`); o canal não fecha. */
  error: ApiErrorBody;
}

export type ServerEventType = keyof ServerEventPayloads;

/** Envelope comum: `{ "type": …, "ts": …, "payload": { … } }`. */
export type ServerEvent = {
  [K in ServerEventType]: {
    type: K;
    ts: Iso8601;
    payload: ServerEventPayloads[K];
  };
}[ServerEventType];

export const SERVER_EVENT_TYPES: readonly ServerEventType[] = [
  'metrics_update',
  'screen_translation',
  'decision',
  'question_new',
  'memory_update',
  'spend_update',
  'game_state',
  'control_state',
  'question_answered',
  'approval_new',
  'approval_updated',
  'adventure_task',
  'error',
];

/** Mensagens cliente → servidor. */
export interface AnswerQuestionMessage {
  type: 'answer_question';
  question_id: string;
  answer_text: string;
  option_id?: string;
}

export interface SetControlMessage {
  type: 'set_control';
  mode: ControlMode;
  kill_switch: boolean;
}

export type ClientMessage = AnswerQuestionMessage | SetControlMessage;
