/**
 * Implementação MOCK do cliente — usada quando `VITE_API_BASE` não está definida.
 *
 * Dados realistas de adventure mode + simulação de eventos WebSocket a cada
 * poucos segundos (`metrics_update`, `screen_translation`, `decision`,
 * `spend_update`, `question_new`, `memory_update`, `control_state`) para que
 * todos os painéis pareçam vivos sem backend nenhum.
 */

import { parseServerEvent } from './events';
import type { ApiClient, ConnectionStatus, SubscriptionHandlers } from './client';
import type {
  AnswerQuestionRequest,
  AnswerQuestionResponse,
  Campaign,
  CombatEntry,
  ControlRequest,
  ControlState,
  CreateCampaignRequest,
  CreateInputRequest,
  Decision,
  HealthResponse,
  InputHistoryEntry,
  InputInjection,
  JevAgent,
  KeyInfo,
  Memory,
  Metrics,
  Objective,
  PatchMemoryRequest,
  Question,
  ScreenTranslation,
  ServerEvent,
  SetKeyRequest,
  Spend,
} from './types';

/* --------------------------------------------------------------- helpers --- */

const iso = (date: Date) => date.toISOString();
const secondsAgo = (s: number) => iso(new Date(Date.now() - s * 1000));
const minutesAgo = (m: number) => secondsAgo(m * 60);

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

function randomBetween(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function round(value: number, decimals: number): number {
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

function pick<T>(items: readonly T[], index: number): T {
  return items[((index % items.length) + items.length) % items.length] as T;
}

/* ----------------------------------------------------------------- seeds --- */

const SCREEN_TEXTS_PT = [
  'Encontra-se numa clareira rodeada de árvores. A norte, o terreno sobe para uma colina rochosa.',
  'O rio corre de leste para oeste. A água é rasa aqui, mas a corrente é forte.',
  'Vê uma caverna escura na encosta. Há sinais de pegadas recentes na lama à entrada.',
  'Um javali selvagem pasta a poucos metros. Não parece ter notado a sua presença.',
  'A chuva começa a cair. O chão de terra transforma-se rapidamente em lama.',
  'Encontra um acampamento abandonado: restos de uma fogueira e uma mochila rasgada.',
  'Um caminho de pedra antigo segue para nordeste, em direção a um portão de pedra.',
  'Sente fome. A mochila tem carne seca suficiente para mais uma refeição.',
  'Dois goblins armados com lanças aproximam-se pelo lado sul, a correr.',
  'O sol põe-se. A visibilidade cai e ouve-se o uivo distante de um lobo.',
  'Uma ponte de madeira atravessa o desfiladeiro. Algumas tábuas parecem podres.',
  'O ar cheira a enxofre. Mais à frente, um fio de fumo sobe entre as rochas.',
];

const REGIONS = [
  'The Hills',
  'The Hills',
  'River Bank',
  'Cave Mouth',
  'The Hills',
  'Old Road',
  'Stone Gate',
  'Deep Forest',
];

const HOSTILES = [
  'goblin spearman',
  'goblin lasher',
  'wild boar',
  'wolf pack',
  'kobold thief',
  'giant cave spider',
];

const ITEMS = [
  'mochila de couro',
  'carne seca (3)',
  'odre de água',
  'espada curta de bronze',
  'tocha (5)',
  'corda de cânhamo',
  'pedra de amolar',
  'mapa rasgado',
];

const ACTION_CANDIDATES = [
  'goto:river_bank',
  'move:north',
  'goto:cave_mouth',
  'goto:stone_gate',
  'key:look',
  'wait:60',
  'other',
] as const;

const REQUEST_SUMMARIES = [
  'choice(6 action candidates) + score(urgency) + noul(needs_human)',
  'choice(7 action candidates) + score(urgency) + noul(needs_human)',
  'noul(12 memory candidates)',
  'noul(update narrative now) + choice(narrator effort) + choice(record type)',
  'noul(ask human) + choice(question type)',
  'choice(5 action candidates) + score(urgency) + noul(needs_human)',
];

const JEV_AGENTS: readonly JevAgent[] = ['JEV-A', 'JEV-R', 'JEV-M', 'JEV-D'];

const NARRATIVE_EN =
  'The adventurer wakes in a clearing north of the river, bruised but alive. ' +
  'Supplies are low, the road to the stone gate is still unexplored, and the ' +
  'first goblin scouts have been sighted near the cave mouth.';

const NARRATIVE_PT =
  'O aventureiro acorda numa clareira a norte do rio, com nódoas mas vivo. ' +
  'Os mantimentos estão a acabar, a estrada para o portão de pedra continua por ' +
  'explorar e os primeiros batedores goblins foram avistados junto à entrada da caverna.';

const WORKING_MEMORY_EN =
  'Open threads: find drinking water before dusk; avoid the goblin patrol near the ' +
  'cave mouth; decide between swimming the river and searching for a bridge east.';

interface EpisodeSeed {
  kind: string;
  content: string;
  content_pt: string;
}

const EPISODE_SEEDS: EpisodeSeed[] = [
  {
    kind: 'episodic',
    content: 'Climbed the hill without incident; spotted the river to the east.',
    content_pt: 'Subiu a colina sem incidentes; avistou o rio a leste.',
  },
  {
    kind: 'episodic',
    content: 'Drank from the stream and refilled the waterskin.',
    content_pt: 'Bebeu do ribeiro e voltou a encher o odre de água.',
  },
  {
    kind: 'episodic',
    content: 'Fought off a wild boar near the trail; took a light wound to the left arm.',
    content_pt: 'Rechaçou um javali junto ao trilho; sofreu um ferimento ligeiro no braço esquerdo.',
  },
  {
    kind: 'semantic',
    content: 'The stone gate to the northeast is guarded by at least two goblin spearmen.',
    content_pt: 'O portão de pedra a nordeste é guardado por pelo menos dois lanceiros goblins.',
  },
  {
    kind: 'procedural',
    content: 'Crossing a river: prefer a bridge; swimming costs stamina and risks dropping items.',
    content_pt: 'Atravessar um rio: preferir uma ponte; nadar custa estamina e arrisca perder itens.',
  },
  {
    kind: 'episodic',
    content: 'Found an abandoned camp with a torn backpack and a rope.',
    content_pt: 'Encontrou um acampamento abandonado com uma mochila rasgada e uma corda.',
  },
];

const INPUT_SEEDS: { text_pt: string; text: string; minutes: number }[] = [
  {
    text_pt: 'Há uma caverna a norte que dá água.',
    text: 'There is a cave to the north with water.',
    minutes: 42,
  },
  {
    text_pt: 'Evita combater à noite; procura abrigo primeiro.',
    text: 'Avoid fighting at night; look for shelter first.',
    minutes: 27,
  },
  {
    text_pt: 'O portão de pedra é o objetivo principal, não te distraias com loot.',
    text: 'The stone gate is the main objective; do not get distracted by loot.',
    minutes: 13,
  },
];

const QUESTION_SEEDS: {
  question: string;
  options: { id: string; text: string }[];
  status: 'pending' | 'answered';
  minutes: number;
  answer_text_pt?: string;
}[] = [
  {
    question: 'Atravessar o rio a nadar ou procurar uma ponte?',
    options: [
      { id: 'opt-1', text: 'Nadar' },
      { id: 'opt-2', text: 'Procurar ponte' },
    ],
    status: 'pending',
    minutes: 1,
  },
  {
    question: 'Os goblins junto à caverna: contornar ou enfrentar?',
    options: [
      { id: 'opt-3', text: 'Contornar pelo norte' },
      { id: 'opt-4', text: 'Enfrentar' },
      { id: 'opt-5', text: 'Recuar e observar' },
    ],
    status: 'pending',
    minutes: 4,
  },
  {
    question: 'A mochila está quase cheia. Largar a corda de cânhamo?',
    options: [
      { id: 'opt-6', text: 'Largar a corda' },
      { id: 'opt-7', text: 'Manter a corda' },
    ],
    status: 'pending',
    minutes: 9,
  },
  {
    question: 'Vale a pena investigar o acampamento abandonado?',
    options: [
      { id: 'opt-8', text: 'Investigar' },
      { id: 'opt-9', text: 'Ignorar e seguir' },
    ],
    status: 'answered',
    minutes: 22,
    answer_text_pt: 'Investigar rapidamente e seguir viagem.',
  },
  {
    question: 'Que arma levar para a expedição ao portão de pedra?',
    options: [
      { id: 'opt-10', text: 'Espada curta' },
      { id: 'opt-11', text: 'Lança' },
    ],
    status: 'answered',
    minutes: 55,
    answer_text_pt: 'Levar a espada curta e a tocha.',
  },
];

/* ------------------------------------------------------------ campanhas --- */

interface CampaignSeed {
  id: string;
  name: string;
  is_new_game: boolean;
  mode: ControlState['mode'];
  kill_switch: boolean;
  ageHours: number;
}

const CAMPAIGN_SEEDS: CampaignSeed[] = [
  {
    id: '8f2c1e40-1d6a-4a1f-9c3b-5b2f0a7d41c9',
    name: 'Urist McAdventurer',
    is_new_game: true,
    mode: 'autopilot',
    kill_switch: false,
    ageHours: 3,
  },
  {
    id: 'b41d7a93-2f88-4c50-8e17-6a9c3d5e0b22',
    name: 'Boatmurdered Reborn',
    is_new_game: false,
    mode: 'checkpoint',
    kill_switch: false,
    ageHours: 29,
  },
  {
    id: 'd7e05c16-9a34-4b72-b0f8-1c4e8a26d5f3',
    name: 'Adilatír, a Expedição',
    is_new_game: false,
    mode: 'paused',
    kill_switch: true,
    ageHours: 76,
  },
];

/* ------------------------------------------------------------- o cliente --- */

const TICK_MS = 2600;

export class MockApiClient implements ApiClient {
  readonly kind = 'mock' as const;
  readonly baseUrl = '';

  private readonly states = new Map<string, MockState>();

  constructor() {
    for (const seed of CAMPAIGN_SEEDS) {
      this.states.set(seed.id, buildState(seed));
    }
  }

  private delay<T>(value: T, ms = randomBetween(140, 340)): Promise<T> {
    return new Promise((resolve) => {
      window.setTimeout(() => resolve(value), Math.round(ms));
    });
  }

  private state(id: string): MockState {
    const state = this.states.get(id);
    if (!state) throw new Error(`campanha desconhecida no mock: ${id}`);
    return state;
  }

  /* ------------------------------------------------------------- REST --- */

  health() {
    return this.delay<HealthResponse>({ status: 'ok', version: '0.1.0-mock' }, 90);
  }

  listCampaigns() {
    return this.delay([...this.states.values()].map((s) => clone(s.campaign)));
  }

  async createCampaign(body: CreateCampaignRequest) {
    const slug = body.name
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '');
    const index = this.states.size;
    const seed: CampaignSeed = {
      id: `${slug || 'campanha'}-${Math.random().toString(16).slice(2, 10)}`,
      name: body.name,
      is_new_game: body.is_new_game,
      mode: 'autopilot',
      kill_switch: false,
      ageHours: 0,
    };
    const state = buildState(seed, index);
    if (body.is_new_game) {
      state.memory.narrative = '';
      state.memory.narrative_pt = '';
      state.memory.episodes = [];
      state.decisions = [];
      state.questions = [];
    }
    this.states.set(seed.id, state);
    return clone(state.campaign);
  }

  getCampaign(id: string) {
    return this.delay(clone(this.state(id).campaign));
  }

  listKeys(id: string) {
    return this.delay(clone(this.state(id).keys));
  }

  async setKey(id: string, body: SetKeyRequest) {
    const state = this.state(id);
    const masked = `${body.key.slice(0, Math.min(9, body.key.length))}${'•'.repeat(8)}${body.key.slice(-4)}`;
    const entry: KeyInfo = { provider: body.provider, masked_key: masked, set: true };
    state.keys = state.keys.map((k) => (k.provider === body.provider ? entry : k));
    return this.delay(clone(entry));
  }

  getMemory(id: string) {
    return this.delay(clone(this.state(id).memory));
  }

  async patchMemory(id: string, body: PatchMemoryRequest) {
    const state = this.state(id);
    if (typeof body.narrative === 'string') {
      // O servidor real traduz PT→EN. No mock a "tradução" é marcada.
      state.memory.narrative_pt = body.narrative;
      state.memory.narrative = `[EN] ${body.narrative}`;
      state.memory.updated_at = iso(new Date());
      this.emit(id, { type: 'memory_update', ts: state.memory.updated_at, payload: clone(state.memory) });
    }
    if (body.episodes) state.memory.episodes = clone(body.episodes);
    return this.delay(clone(state.memory));
  }

  getObjective(id: string) {
    return this.delay(clone(this.state(id).objective));
  }

  async putObjective(id: string, body: Objective) {
    const state = this.state(id);
    state.objective = clone(body);
    state.metrics.objective_progress.percent = progressOf(state);
    return this.delay(clone(state.objective));
  }

  async submitInput(id: string, body: CreateInputRequest) {
    const state = this.state(id);
    const created: InputInjection = {
      id: `in-${state.inputs.length + 1}`,
      text: `[EN] ${body.text}`,
      ts: iso(new Date()),
    };
    state.inputs = [{ ...created, text_pt: body.text }, ...state.inputs].slice(0, 100);
    return this.delay(clone(created));
  }

  listInputs(id: string) {
    return this.delay(clone(this.state(id).inputs));
  }

  getMetrics(id: string) {
    return this.delay(clone(this.state(id).metrics));
  }

  getSpend(id: string) {
    return this.delay(clone(this.state(id).spend));
  }

  getDecisions(id: string) {
    return this.delay(clone(this.state(id).decisions));
  }

  getQuestions(id: string) {
    return this.delay(clone(this.state(id).questions));
  }

  async answerQuestion(id: string, qid: string, body: AnswerQuestionRequest) {
    const state = this.state(id);
    const question = state.questions.find((q) => q.id === qid);
    const answerEn = `[EN] ${body.answer_text}`;
    if (question) {
      question.status = 'answered';
      question.answer_text = answerEn;
      question.option_id = body.option_id ?? null;
    }
    const response: AnswerQuestionResponse = {
      id: qid,
      status: 'answered',
      answer_text: answerEn,
      option_id: body.option_id ?? null,
    };
    const episode = {
      id: `ep-${state.memory.episodes.length + 1}`,
      ts: iso(new Date()),
      kind: 'episodic',
      content: `Human answered "${body.answer_text}".`,
      content_pt: `O humano respondeu «${body.answer_text}».`,
    };
    state.memory.episodes = [episode, ...state.memory.episodes];
    return this.delay(response);
  }

  async setControl(id: string, body: ControlRequest) {
    const state = this.state(id);
    state.campaign.control = { mode: body.mode, kill_switch: body.kill_switch };
    state.campaign.updated_at = iso(new Date());
    const control: ControlState = {
      mode: body.mode,
      kill_switch: body.kill_switch,
      ts: state.campaign.updated_at,
    };
    this.emit(id, { type: 'control_state', ts: control.ts ?? iso(new Date()), payload: control });
    return this.delay(control);
  }

  getScreen(id: string) {
    return this.delay(clone(this.state(id).screen));
  }

  /* -------------------------------------------------------- websocket --- */

  subscribe(id: string, handlers: SubscriptionHandlers): () => void {
    const state = this.state(id);
    let disposed = false;
    const timers: number[] = [];

    const setStatus = (status: ConnectionStatus) => handlers.onStatus?.(status);

    // Registo de subscritores: mutações (controlo, memória) também emitem eventos.
    const registry = this.listeners.get(id) ?? new Set<(event: ServerEvent) => void>();
    const listener = (event: ServerEvent) => handlers.onEvent(event);
    registry.add(listener);
    this.listeners.set(id, registry);

    const send = (raw: unknown) => {
      if (disposed) return;
      const event = parseServerEvent(raw);
      // Eventos desconhecidos (ex.: `heartbeat`) são ignorados — como no cliente real.
      if (event) handlers.onEvent(event);
    };

    setStatus('connecting');

    timers.push(
      window.setTimeout(() => {
        setStatus('open');
        send(burst(state, 'metrics_update'));
        timers.push(window.setTimeout(() => send(burst(state, 'screen_translation')), 700));
      }, 350),
    );

    const interval = window.setInterval(() => {
      if (disposed) return;
      state.tick += 1;
      const tick = state.tick;

      drift(state);
      send(burst(state, 'metrics_update'));

      if (tick % 2 === 0) send(burst(state, 'screen_translation'));
      if (tick % 3 === 0) send(burst(state, 'decision'));
      if (tick % 4 === 0) send(burst(state, 'spend_update'));
      if (tick % 5 === 0) {
        // Ruído deliberado: o cliente tem de ignorar tipos desconhecidos (não fatais).
        send({ type: 'heartbeat', ts: iso(new Date()), payload: {} });
      }
      if (tick % 7 === 0) send(burst(state, 'question_new'));
      if (tick % 11 === 0) send(burst(state, 'memory_update'));
      if (tick % 13 === 0) send(burst(state, 'control_state'));
    }, TICK_MS);

    return () => {
      disposed = true;
      window.clearInterval(interval);
      for (const timer of timers) window.clearTimeout(timer);
      registry.delete(listener);
      if (registry.size === 0) this.listeners.delete(id);
    };
  }

  /** Difunde um evento a todos os subscritores ativos da campanha. */
  private emit(id: string, event: ServerEvent) {
    for (const listener of this.listeners.get(id) ?? []) listener(event);
  }

  private readonly listeners = new Map<string, Set<(event: ServerEvent) => void>>();
}

/* --------------------------------------------------------- estado mock --- */

interface MockState {
  campaign: Campaign;
  memory: Memory;
  objective: Objective;
  metrics: Metrics;
  spend: Spend;
  decisions: Decision[];
  questions: Question[];
  screen: ScreenTranslation;
  inputs: InputHistoryEntry[];
  keys: KeyInfo[];
  tick: number;
}

function progressOf(state: MockState): number {
  const goals = state.objective.subgoals;
  if (goals.length === 0) return 0;
  return round(goals.filter((g) => g.done).length / goals.length, 2);
}

function buildState(seed: CampaignSeed, index = 0): MockState {
  const created = new Date(Date.now() - seed.ageHours * 3600 * 1000);
  const campaign: Campaign = {
    id: seed.id,
    name: seed.name,
    is_new_game: seed.is_new_game,
    created_at: iso(created),
    updated_at: secondsAgo(4),
    control: { mode: seed.mode, kill_switch: seed.kill_switch },
  };

  const episodes = EPISODE_SEEDS.map((episode, i) => ({
    id: `ep-${String(i + 1).padStart(3, '0')}`,
    ts: minutesAgo(90 - i * 13),
    kind: episode.kind,
    content: episode.content,
    content_pt: episode.content_pt,
  }));

  const memory: Memory = {
    narrative: NARRATIVE_EN,
    narrative_pt: NARRATIVE_PT,
    episodes,
    working_memory: WORKING_MEMORY_EN,
    updated_at: minutesAgo(2),
  };

  const objective: Objective = {
    narrative:
      'Chegar ao portão de pedra a nordeste, vivo e com água suficiente para dois dias.',
    subgoals: [
      { id: 'sg-1', text: 'Encontrar água potável', done: true },
      { id: 'sg-2', text: 'Atravessar o rio em segurança', done: false },
      { id: 'sg-3', text: 'Evitar a patrulha goblin junto à caverna', done: false },
      { id: 'sg-4', text: 'Chegar ao portão de pedra', done: false },
    ],
  };

  const state: MockState = {
    campaign,
    memory,
    objective,
    metrics: buildMetrics(seed.mode),
    spend: buildSpend(),
    decisions: buildDecisions(),
    questions: buildQuestions(),
    screen: { translated_pt: pick(SCREEN_TEXTS_PT, index), ts: secondsAgo(1) },
    inputs: INPUT_SEEDS.map((entry, i) => ({
      id: `in-${i + 1}`,
      text: entry.text,
      text_pt: entry.text_pt,
      ts: minutesAgo(entry.minutes),
    })),
    keys: [
      { provider: 'openrouter', masked_key: 'sk-or-v1••••••••3f9a', set: true },
      { provider: 'typesafe', masked_key: 'ts_live_••••••••b710', set: true },
      { provider: 'deepseek', masked_key: null, set: false },
    ],
    tick: 0,
  };

  state.metrics.objective_progress.percent = progressOf(state);
  return state;
}

function buildMetrics(mode: ControlState['mode']): Metrics {
  const combat = mode === 'paused' ? false : Math.random() > 0.7;
  return {
    ts: secondsAgo(2),
    health: {
      wounds:
        mode === 'paused'
          ? [{ part: 'braço esquerdo', severity: 'ligeiro', ts: minutesAgo(31) }]
          : [],
      status: mode === 'paused' ? 'wounded' : 'healthy',
    },
    needs: {
      hunger: mode === 'paused' ? 'hungry' : 'none',
      thirst: 'none',
      sleep: mode === 'paused' ? 'drowsy' : 'rested',
    },
    inventory: { weight_kg: 42.5, items: ITEMS.slice(0, 6) },
    threats: { level: combat ? 'high' : 'low', combat, hostiles: combat ? HOSTILES.slice(0, 2) : [] },
    location: { region: 'The Hills', path: REGIONS.slice(0, 3) },
    objective_progress: { percent: 0.25, active_subgoal_id: 'sg-2' },
    combat_history: buildCombatHistory(combat),
    agent: {
      decisions_per_min: 12.4,
      confidence_avg: 0.87,
      calls: 318,
      cost_usd: 0.042318,
      latency_ms: 410,
    },
  };
}

function buildCombatHistory(active: boolean): CombatEntry[] {
  const history: CombatEntry[] = [
    { ts: minutesAgo(38), opponent: 'wild boar', outcome: 'victory', damage: 'corte ligeiro no braço' },
    { ts: minutesAgo(64), opponent: 'kobold thief', outcome: 'fled', damage: 'nenhum' },
    { ts: minutesAgo(121), opponent: 'goblin spearman', outcome: 'victory', damage: 'contusão nas costas' },
  ];
  if (active) {
    history.unshift({ ts: secondsAgo(12), opponent: 'goblin lasher', outcome: 'ongoing', damage: '—' });
  }
  return history;
}

function buildSpend(): Spend {
  return {
    total_usd: 0.042318,
    providers: [
      {
        provider: 'typesafe',
        models: [
          {
            model: 'typesafe/jev-1.13',
            calls: 318,
            input_tokens: 1007714,
            output_tokens: 0,
            cost_usd: 0.042318,
          },
        ],
      },
      {
        provider: 'openrouter',
        models: [
          {
            model: 'deepseek/deepseek-v4.1-flash',
            calls: 12,
            input_tokens: 8000,
            output_tokens: 2100,
            cost_usd: 0,
          },
        ],
      },
    ],
    ts: secondsAgo(3),
  };
}

function buildDecisions(): Decision[] {
  const bands: Decision['band'][] = [
    'auto',
    'auto',
    'hitl',
    'auto',
    'abstain',
    'auto',
    'hitl',
    'auto',
    'auto',
    'hitl',
    'auto',
    'abstain',
    'auto',
    'auto',
  ];
  const decisions: Decision[] = [];
  for (let i = 0; i < bands.length; i += 1) {
    const band = bands[i] as Decision['band'];
    const agent = pick(JEV_AGENTS, i);
    // Uma decisão com confidence ausente: "indisponível", não "certo".
    const confidence =
      i === 4 ? null : band === 'auto' ? round(randomBetween(0.9, 0.99), 2) : round(randomBetween(0.42, 0.79), 2);
    decisions.push(makeDecision(i, agent, band, confidence, minutesAgo(1 + i * 2.5)));
  }
  return decisions.reverse();
}

function makeDecision(
  index: number,
  agent: JevAgent,
  band: Decision['band'],
  confidence: number | null,
  ts: string,
): Decision {
  const winner = pick(ACTION_CANDIDATES, index);
  const probabilities: Record<string, number> = {};
  const winnerProb = confidence ?? 0.5;
  probabilities[winner] = round(Math.min(0.98, Math.max(0.2, winnerProb)), 4);
  const rest = round((1 - probabilities[winner]!) / 2, 4);
  probabilities[pick(ACTION_CANDIDATES, index + 2)] = rest;
  probabilities.other = round(1 - probabilities[winner]! - rest, 4);

  const answers: Record<string, unknown> =
    agent === 'JEV-A'
      ? { choice: winner, score: pick(['risco menor', 'urgente', 'rotina'], index), noul: round(randomBetween(0.03, 0.4), 2) }
      : agent === 'JEV-R'
        ? { noul_memories: [index % 6, (index + 3) % 6] }
        : agent === 'JEV-M'
          ? { update_now: index % 3 !== 0, effort: pick(['medium', 'high', 'low'], index), record: 'episódio' }
          : { ask_human: band !== 'auto', question_type: pick(['binária', 'múltipla', 'aberta'], index) };

  return {
    id: `d-${100 + index}`,
    ts,
    agent,
    request_summary: pick(REQUEST_SUMMARIES, index),
    answers,
    probabilities,
    confidence,
    band,
    latency_ms: Math.round(randomBetween(180, 720)),
    cost_usd: round(randomBetween(0.00008, 0.00042), 6),
  };
}

function buildQuestions(): Question[] {
  return QUESTION_SEEDS.map((seed, i) => ({
    id: `q-${i + 1}`,
    ts: minutesAgo(seed.minutes),
    question: seed.question,
    options: seed.options,
    free_text_allowed: true,
    status: seed.status,
    answer_text: seed.answer_text_pt ? `[EN] ${seed.answer_text_pt}` : null,
    option_id: seed.status === 'answered' ? (seed.options[0]?.id ?? null) : null,
  }));
}

/* --------------------------------------------------------- simulação --- */

function burst(state: MockState, type: string): unknown {
  const ts = iso(new Date());
  switch (type) {
    case 'metrics_update': {
      state.metrics.ts = ts;
      return { type: 'metrics_update', ts, payload: clone(state.metrics) };
    }
    case 'screen_translation': {
      const text = pick(SCREEN_TEXTS_PT, state.tick + state.decisions.length);
      state.screen = { translated_pt: text, ts };
      return { type: 'screen_translation', ts, payload: { translated_pt: text } };
    }
    case 'decision': {
      const index = state.decisions.length;
      const band = pick<Decision['band']>(['auto', 'auto', 'hitl', 'auto', 'abstain'], index);
      const confidence = band === 'auto' ? round(randomBetween(0.9, 0.99), 2) : round(randomBetween(0.4, 0.8), 2);
      const decision = makeDecision(index, pick(JEV_AGENTS, index), band, confidence, ts);
      state.decisions = [decision, ...state.decisions].slice(0, 120);
      state.metrics.agent.calls += 1;
      state.metrics.agent.cost_usd = round(state.metrics.agent.cost_usd + decision.cost_usd, 6);
      return { type: 'decision', ts, payload: clone(decision) };
    }
    case 'spend_update': {
      state.spend.ts = ts;
      return { type: 'spend_update', ts, payload: clone(state.spend) };
    }
    case 'question_new': {
      const index = state.questions.length;
      const question: Question = {
        id: `q-${index + 1}`,
        ts,
        question: pick(
          [
            'O caminho a norte está bloqueado por rochas. Contornar ou voltar atrás?',
            'Há um mercador no trilho. Trocar a corda por rações?',
            'A tempestade está a piorar. Acampar ou continuar?',
            'Encontrámos um artefacto estranho. Recolher ou deixar?',
          ],
          index,
        ),
        options: [
          { id: `opt-${index * 2 + 20}`, text: 'Opção A' },
          { id: `opt-${index * 2 + 21}`, text: 'Opção B' },
        ],
        free_text_allowed: true,
        status: 'pending',
        answer_text: null,
        option_id: null,
      };
      state.questions = [question, ...state.questions];
      return { type: 'question_new', ts, payload: clone(question) };
    }
    case 'memory_update': {
      state.memory.updated_at = ts;
      const episode = EPISODE_SEEDS[state.tick % EPISODE_SEEDS.length] as EpisodeSeed;
      state.memory.episodes = [
        {
          id: `ep-${String(state.memory.episodes.length + 1).padStart(3, '0')}`,
          ts,
          kind: episode.kind,
          content: episode.content,
          content_pt: episode.content_pt,
        },
        ...state.memory.episodes,
      ].slice(0, 40);
      return { type: 'memory_update', ts, payload: clone(state.memory) };
    }
    case 'control_state': {
      return { type: 'control_state', ts, payload: clone(state.campaign.control) };
    }
    default:
      return { type, ts, payload: {} };
  }
}

/** Deriva as métricas como se o agente estivesse a jogar. */
function drift(state: MockState) {
  const { metrics, campaign } = state;
  const paused = campaign.control.mode === 'paused' || campaign.control.kill_switch;
  const agent = metrics.agent;

  agent.decisions_per_min = Math.max(0, round(agent.decisions_per_min + randomBetween(-0.7, 0.7), 1));
  agent.confidence_avg = round(Math.min(0.99, Math.max(0.35, agent.confidence_avg + randomBetween(-0.03, 0.03))), 2);
  agent.latency_ms = Math.max(90, Math.round(agent.latency_ms + randomBetween(-60, 60)));

  if (paused) {
    agent.decisions_per_min = 0;
    return;
  }

  if (state.tick % 6 === 0) {
    const region = pick(REGIONS, Math.floor(state.tick / 6));
    metrics.location.region = region;
    metrics.location.path = [...metrics.location.path, region].slice(-5);
  }

  if (state.tick % 8 === 0) {
    const next = round(Math.min(1, metrics.objective_progress.percent + randomBetween(0, 0.04)), 2);
    metrics.objective_progress.percent = next;
  }

  if (state.tick % 10 === 0) {
    const levels = ['low', 'medium', 'high'];
    const current = levels.indexOf(metrics.threats.level);
    metrics.threats.level = pick(levels, Math.max(0, current + (Math.random() > 0.55 ? 1 : -1)));
    metrics.threats.combat = metrics.threats.level === 'high';
    metrics.threats.hostiles = metrics.threats.combat ? [pick(HOSTILES, state.tick)] : [];
  }

  if (state.tick % 12 === 0) {
    const needs = ['none', 'hungry', 'thirsty', 'drowsy'];
    const key = pick(['hunger', 'thirst', 'sleep'] as const, state.tick);
    metrics.needs[key] = pick(needs, state.tick + 1);
  }

  if (state.tick % 9 === 0 && metrics.threats.combat) {
    metrics.combat_history = [
      {
        ts: iso(new Date()),
        opponent: pick(HOSTILES, state.tick),
        outcome: pick(['ongoing', 'victory', 'fled'], state.tick),
        damage: pick(['nenhum', 'corte ligeiro', 'contusão'], state.tick),
      },
      ...metrics.combat_history,
    ].slice(0, 12);
  }
}
