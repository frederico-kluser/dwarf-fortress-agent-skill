/**
 * Implementação MOCK do cliente — usada quando `VITE_API_BASE` não está definida.
 *
 * Dados realistas de adventure mode + simulação de eventos WebSocket a cada
 * poucos segundos (`metrics_update`, `screen_translation`, `decision`,
 * `spend_update`, `question_new`, `question_answered`, `memory_update`,
 * `control_state`, `approval_new`, `adventure_task`) para que todos os painéis
 * pareçam vivos sem backend nenhum. As tarefas de aventura
 * (`new_world`/`new_character`/`load`) são simuladas passo a passo, com o mesmo
 * envelope `task` do contrato e cancelamento a meio.
 */

import { parseServerEvent } from './events';
import type { ApiClient, ConnectionStatus, SubscriptionHandlers } from './client';
import type {
  AdventureCancelResponse,
  AdventuresResponse,
  AdventureSave,
  AdventureStatusResponse,
  AdventureTask,
  AdventureTaskKind,
  AdventureTaskStart,
  AnswerQuestionRequest,
  AnswerQuestionResponse,
  Approval,
  ApprovalDecisionRequest,
  ApprovalStatusFilter,
  Campaign,
  CombatEntry,
  ControlRequest,
  ControlState,
  CreateCampaignRequest,
  CreateInputRequest,
  CurrentAdventure,
  Decision,
  DecisionBand,
  DecisionStatus,
  GameActionRequest,
  GameActionResponse,
  HealthResponse,
  InputHistoryEntry,
  InputInjection,
  JevAgent,
  KeyInfo,
  ListPageQuery,
  LoadAdventureRequest,
  Memory,
  Metrics,
  NewCharacterRequest,
  NewWorldRequest,
  Objective,
  PatchMemoryRequest,
  Question,
  ScreenTranslation,
  ServerEvent,
  SetKeyRequest,
  Spend,
  SystemStatus,
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

/** Slug de nome (campanha, save) — minúsculas, sem acentos, separado por `-`. */
function slugify(value: string): string {
  return (
    value
      .toLowerCase()
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/(^-|-$)/g, '') || 'mundo'
  );
}

function pick<T>(items: readonly T[], index: number): T {
  return items[((index % items.length) + items.length) % items.length] as T;
}

/**
 * Paginação do contrato (`?limit=&before=`, docs/api.md): `limit` devolve os
 * registos **mais recentes** mantendo a ordem antiga (mais antigo primeiro
 * dentro da página) e `before` é um cursor **exclusivo** por `ts`.
 */
function pageSlice<T extends { ts: string }>(items: T[], page?: ListPageQuery): T[] {
  const ascending = [...items].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
  const before = page?.before;
  const eligible = before ? ascending.filter((item) => item.ts < before) : ascending;
  const limit = typeof page?.limit === 'number' && page.limit > 0 ? page.limit : undefined;
  return limit ? eligible.slice(-limit) : eligible;
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

const OBJECTIVE_NARRATIVE_EN =
  'Reach the stone gate to the northeast alive, with water enough for two days.';

const OBJECTIVE_NARRATIVE_PT =
  'Chegar ao portão de pedra a nordeste, vivo e com água suficiente para dois dias.';

/** Sub-objetivos: canónico EN + exibição PT (o contrato só omite o `*_pt` sem tradução). */
const SUBGOAL_SEEDS: { id: string; text: string; text_pt: string; done: boolean }[] = [
  { id: 'sg-1', text: 'Find drinking water', text_pt: 'Encontrar água potável', done: true },
  { id: 'sg-2', text: 'Cross the river safely', text_pt: 'Atravessar o rio em segurança', done: false },
  {
    id: 'sg-3',
    text: 'Avoid the goblin patrol near the cave',
    text_pt: 'Evitar a patrulha goblin junto à caverna',
    done: false,
  },
  { id: 'sg-4', text: 'Reach the stone gate', text_pt: 'Chegar ao portão de pedra', done: false },
];

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

interface QuestionSeed {
  question: string;
  options: { id: string; text: string }[];
  status: 'pending' | 'answered';
  minutes: number;
  answer_text_pt?: string;
  /* Proveniência (aditiva) — o último seed fica sem ela de propósito: é o
     payload antigo (6 campos) que a UI tem de continuar a mostrar. */
  band?: DecisionBand;
  role?: JevAgent;
  action_id?: string;
  why_pt?: string;
  source_data?: Record<string, unknown>;
  /** Dedupe (aditivo): quantas vezes o loop voltou a pedir a MESMA ação. */
  attempts?: number;
}

const QUESTION_SEEDS: QuestionSeed[] = [
  {
    question: 'Atravessar o rio a nadar ou procurar uma ponte?',
    options: [
      { id: 'opt-1', text: 'Nadar' },
      { id: 'opt-2', text: 'Procurar ponte a leste' },
    ],
    status: 'pending',
    minutes: 1,
    band: 'hitl',
    role: 'JEV-A',
    action_id: 'goto:river_bank',
    why_pt: 'a confiança ficou na banda hitl (0,50–0,89): confirmar antes de executar',
    source_data: {
      reason: 'band_hitl',
      band: 'hitl',
      action_id: 'goto:river_bank',
      action_en: 'Go to the river bank',
      confidence_available: true,
      action_confidence: 0.62,
    },
    // O checkpoint volta a pedir a MESMA ação a cada passo do loop (dedupe).
    attempts: 7,
  },
  {
    // Pilha pré-dedupe: outra entrada pendente da MESMA `action_id` — a UI tem
    // de coalescer as duas num só cartão (auditoria v2, achado 1).
    question: 'A margem do rio está lamacenta. Manter o plano de ir à margem do rio?',
    options: [
      { id: 'opt-20', text: 'Manter o plano' },
      { id: 'opt-21', text: 'Contornar por rocha seca' },
    ],
    status: 'pending',
    minutes: 2,
    band: 'hitl',
    role: 'JEV-A',
    action_id: 'goto:river_bank',
    why_pt: 'o modo checkpoint retém a ação até confirmação humana (banda hitl)',
    source_data: {
      reason: 'checkpoint_mode',
      band: 'hitl',
      action_id: 'goto:river_bank',
      answer_mode: 'approval',
    },
    attempts: 2,
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
    band: 'abstain',
    role: 'JEV-D',
    why_pt:
      'a confiança ficou na banda abstain (< 0,50): o agente prefere não decidir sozinho e escala a dúvida',
    source_data: { reason: 'band_abstain', band: 'abstain', confidence_available: false },
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

/* ------------------------------------------------------------ aprovações --- */

/**
 * Ações retidas pelo modo `checkpoint` (`docs/api.md` §approvals).
 * A pergunta de aprovação tem o mesmo id da aprovação: aprovar/rejeitar é
 * responder a essa pergunta, pelo que o mock mantém as duas em sincronia.
 */
interface ApprovalSeed {
  qid: string;
  action_id: string;
  action_name_en: string;
  description_en: string;
  band: DecisionBand;
  minutes: number;
  /** Dedupe (aditivo): quantas vezes o loop voltou a pedir a MESMA ação. */
  attempts?: number;
}

const APPROVAL_SEEDS: ApprovalSeed[] = [
  {
    qid: 'q-3-1',
    action_id: 'goto:river_bank',
    action_name_en: 'Go to the river bank',
    description_en: 'Travel to the river bank and look for a crossing',
    band: 'auto',
    minutes: 2,
    // Re-pedido da mesma ação: o backend não cria outra entrada, incrementa.
    attempts: 5,
  },
  {
    qid: 'q-3-2',
    action_id: 'goto:stone_gate',
    action_name_en: 'Go to the stone gate',
    description_en: 'Follow the old road northeast towards the stone gate',
    band: 'hitl',
    minutes: 6,
  },
  {
    qid: 'q-3-3',
    action_id: 'wait:60',
    action_name_en: 'Wait one minute',
    description_en: 'Hold position and let the goblin patrol move away',
    band: 'auto',
    minutes: 11,
  },
];

/** `why_pt` determinístico — a mesma frase que o loop publica. */
function approvalWhyPt(seed: ApprovalSeed): string {
  return `o modo checkpoint exige aprovação humana para todas as ações (banda ${seed.band})`;
}

function optionYes(): { id: string; text: string } {
  return { id: 'yes', text: 'Sim, executar' };
}

function optionNo(): { id: string; text: string } {
  return { id: 'no', text: 'Não, escolher outra ação' };
}

function optionOther(): { id: string; text: string } {
  return { id: 'other', text: 'Outra resposta (escrever abaixo)' };
}

function makeApproval(seed: ApprovalSeed): Approval {
  return {
    id: seed.qid,
    question_id: seed.qid,
    ts: minutesAgo(seed.minutes),
    action_id: seed.action_id,
    action_name_en: seed.action_name_en,
    question: `Posso executar «${seed.action_id}»?`,
    band: seed.band,
    status: 'pending',
    note: null,
    decided_at: null,
    description_en: seed.description_en,
    ...(seed.attempts
      ? { attempts: seed.attempts, last_seen_ts: minutesAgo(Math.max(0, seed.minutes - 1)) }
      : {}),
  };
}

/** A pergunta de aprovação que o backend espelha na fila de perguntas. */
function approvalQuestion(seed: ApprovalSeed): Question {
  return {
    id: seed.qid,
    ts: minutesAgo(seed.minutes),
    question: `Posso executar «${seed.action_id}»?`,
    options: [optionYes(), optionNo(), optionOther()],
    free_text_allowed: true,
    status: 'pending',
    answer_text: null,
    option_id: null,
    band: seed.band,
    role: 'JEV-A',
    action_id: seed.action_id,
    why_pt: approvalWhyPt(seed),
    source_data: {
      action_id: seed.action_id,
      action_en: seed.action_name_en,
      description_en: seed.description_en,
      band: seed.band,
      reason: 'checkpoint_mode',
      answer_mode: 'approval',
    },
    ...(seed.attempts
      ? { attempts: seed.attempts, last_seen_ts: minutesAgo(Math.max(0, seed.minutes - 1)) }
      : {}),
  };
}

/**
 * Em `autopilot` não há ações retidas (lista vazia, `X-Source: stub`); em
 * `checkpoint` ficam todas; em `pausa` fica o que estava retido antes de parar.
 */
function retainedApprovalSeeds(mode: ControlState['mode']): ApprovalSeed[] {
  if (mode === 'autopilot') return [];
  if (mode === 'checkpoint') return APPROVAL_SEEDS;
  return APPROVAL_SEEDS.slice(0, 1);
}

/** Resposta que *recusa* explicitamente (o resto aprova) — `docs/api.md`. */
function isRefusal(answerText: string, optionId?: string | null): boolean {
  if (optionId === 'no') return true;
  return /^\s*(no|não|nao|reject|rejeitar|recusar)\b/i.test(answerText);
}

/** Perguntas novas da simulação — opções reais (nunca «Opção A»/«Opção B»). */
const QUESTION_BURSTS: { question: string; options: string[] }[] = [
  {
    question: 'O caminho a norte está bloqueado por rochas. Contornar ou voltar atrás?',
    options: ['Contornar por leste', 'Voltar ao acampamento'],
  },
  {
    question: 'Há um mercador no trilho. Trocar a corda de cânhamo por rações?',
    options: ['Trocar a corda por rações', 'Não trocar'],
  },
  {
    question: 'A tempestade está a piorar. Acampar ou continuar para o portão?',
    options: ['Acampar sob a rocha', 'Continuar para o portão'],
  },
  {
    question: 'Encontrámos um artefacto estranho. Recolher ou deixar?',
    options: ['Recolher', 'Deixar onde está'],
  },
];

/** Frase PT que explica a origem — a mesma forma que o loop publica em `why_pt`. */
function whyPtFor(band: DecisionBand): string {
  if (band === 'abstain') {
    return 'a confiança ficou na banda abstain (< 0,50): o agente prefere não decidir sozinho';
  }
  if (band === 'hitl') {
    return 'a confiança ficou na banda hitl (0,50–0,89): confirmar antes de executar';
  }
  return 'a ação é reversível e a confiança ficou na banda auto: confirmar antes de executar';
}

/* ------------------------------------------------------------ aventuras --- */

/** Passos totais por tipo de tarefa — os mesmos números do exemplo do contrato. */
const TASK_STEPS: Record<AdventureTaskKind, number> = {
  new_world: 6,
  new_character: 13,
  load: 5,
};

/** Duração de um passo por tipo (o worldgen "demora mais" que os menus). */
const TASK_STEP_MS: Record<AdventureTaskKind, number> = {
  new_world: 1100,
  new_character: 420,
  load: 800,
};

/** Ecrãs traduzidos (PT) vistos em cada passo — uma tradução por passo. */
const TASK_SCREENS: Record<AdventureTaskKind, string[]> = {
  new_world: [
    'Ecrã de título — a abrir a criação de mundo…',
    'A escrever o nome do mundo…',
    'Parâmetros do mundo — a aceitar o ecrã…',
    'A gerar o mundo — este passo pode demorar minutos…',
    'A exportar o mundo gerado…',
    'Mundo criado — ecrã de tipo de jogo.',
  ],
  new_character: [
    'Ecrã de tipo de jogo — a escolher «Aventura»…',
    'A criar o personagem…',
    'A escolher o destino…',
    'Destino escolhido — a avançar…',
    'A escolher a raça…',
    'A confirmar a raça…',
    'A escolher a origem…',
    'A aceitar o aspeto…',
    'A aceitar a personalidade…',
    '«Pronto para a aventura!» — quase pronto…',
    'A confirmar o personagem…',
    '«Ir!» — a entrar no mundo…',
    'Personagem criado — sessão pronta.',
  ],
  load: [
    'A localizar o jogo guardado na pasta do jogo…',
    'A ler o mundo guardado…',
    'A carregar o jogo guardado…',
    'A preparar a sessão…',
    'Aventura carregada.',
  ],
};

/** Nomes de aventureiro usados quando o personagem é aleatório. */
const ADVENTURER_NAMES = ['Omba Ziltacathi', 'Urist McShield', 'Dastot Lustrousfrosts', 'Sibrek Goldencurls'];

/** Contexto da tarefa em curso (só mock — o contrato não o transporta). */
interface TaskContext {
  worldName?: string;
  characterName?: string;
  adventureId?: string;
}

const SAVE_ROOT = '/home/ondokai/Games/dwarf-fortress-53.16/data/save';

function saveOf(id: string, name: string, kind: AdventureSave['kind'], minutes: number): AdventureSave {
  return {
    id,
    name,
    kind,
    folder: `${SAVE_ROOT}/${id}`,
    saved_at: minutesAgo(minutes),
  };
}

/* ------------------------------------------------------------ campanhas --- */

interface CampaignSeed {
  id: string;
  name: string;
  is_new_game: boolean;
  mode: ControlState['mode'];
  kill_switch: boolean;
  ageHours: number;
  /** Saves da pasta do DF (best-effort, como o `GET .../adventures`). */
  adventures: AdventureSave[];
  /** Aventura atual do último snapshot (`null` sem aventureiro). */
  current: CurrentAdventure | null;
}

const CAMPAIGN_SEEDS: CampaignSeed[] = [
  {
    id: '8f2c1e40-1d6a-4a1f-9c3b-5b2f0a7d41c9',
    name: 'Urist McAdventurer',
    is_new_game: true,
    mode: 'autopilot',
    kill_switch: false,
    ageHours: 3,
    // Sem aventureiro ativo: o cartão de estado mostra o empty state convidativo.
    adventures: [
      saveOf('region1', 'region1', 'active', 60 * 26),
      saveOf('region2-00202-01-01', 'region2', 'world', 60 * 49),
      saveOf('vale-negro-00311-02-14', 'Vale Negro', 'world', 60 * 80),
    ],
    current: null,
  },
  {
    id: 'b41d7a93-2f88-4c50-8e17-6a9c3d5e0b22',
    name: 'Boatmurdered Reborn',
    is_new_game: false,
    mode: 'checkpoint',
    kill_switch: false,
    ageHours: 29,
    adventures: [
      saveOf('region1', 'region1', 'active', 60 * 5),
      saveOf('colinas-de-vardan-00180-03-02', 'Colinas de Vardan', 'world', 60 * 52),
    ],
    current: {
      adventure_id: 'region1',
      adventurer_name: 'Omba Ziltacathi',
      world_name: 'As Profecias dos Planetas',
      alive: true,
    },
  },
  {
    id: 'd7e05c16-9a34-4b72-b0f8-1c4e8a26d5f3',
    name: 'Adilatír, a Expedição',
    is_new_game: false,
    mode: 'paused',
    kill_switch: true,
    ageHours: 76,
    // Nenhum save nesta instala — o escolhedor mostra o seu estado vazio.
    adventures: [],
    current: null,
  },
];

/* ------------------------------------------------------------- o cliente --- */

const TICK_MS = 2600;

export class MockApiClient implements ApiClient {
  readonly kind = 'mock' as const;
  readonly baseUrl = '';

  private readonly states = new Map<string, MockState>();
  /** Timers das tarefas de aventura em curso (uma por campanha). */
  private readonly taskTimers = new Map<string, number>();

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
      adventures: [],
      current: null,
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
    // O servidor real traduz PT→EN antes de guardar (o objetivo é material de
    // prompt) e devolve o canónico EN + os `*_pt` de exibição — o mock marca a
    // "tradução" em vez de a inventar.
    state.objective = {
      narrative: `[EN] ${body.narrative}`,
      narrative_pt: body.narrative,
      subgoals: body.subgoals.map((goal) => ({
        id: goal.id,
        text: `[EN] ${goal.text_pt ?? goal.text}`,
        text_pt: goal.text_pt ?? goal.text,
        done: goal.done,
      })),
    };
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

  getDecisions(id: string, page?: ListPageQuery) {
    return this.delay(clone(pageSlice(this.state(id).decisions, page)));
  }

  getQuestions(id: string, page?: ListPageQuery) {
    return this.delay(clone(pageSlice(this.state(id).questions, page)));
  }

  listApprovals(id: string, status?: ApprovalStatusFilter) {
    const wanted = status ?? 'pending';
    const all = this.state(id).approvals;
    const list = wanted === 'all' ? all : all.filter((approval) => approval.status === wanted);
    return this.delay(clone(list));
  }

  async approve(id: string, approvalId: string, body?: ApprovalDecisionRequest) {
    return this.decideApproval(id, approvalId, true, body?.note);
  }

  async reject(id: string, approvalId: string, body?: ApprovalDecisionRequest) {
    return this.decideApproval(id, approvalId, false, body?.note);
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
    // Se a pergunta for uma aprovação de checkpoint, a resposta decide-a
    // (`docs/api.md`): recusa explícita rejeita, qualquer outra resposta aprova.
    const approval = state.approvals.find((item) => item.id === qid && item.status === 'pending');
    if (approval) {
      this.markApproval(id, state, approval, !isRefusal(body.answer_text, body.option_id));
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
    // Aditivo (`docs/api.md`): emitido depois de uma resposta vinda por REST.
    this.emit(id, { type: 'question_answered', ts: episode.ts, payload: response });
    return this.delay(response);
  }

  /* --------------------------------------------------------- aprovações --- */

  /** Decide uma aprovação pendente, como o `POST .../approve|reject` do backend. */
  private decideApproval(
    id: string,
    approvalId: string,
    approveIt: boolean,
    note?: string,
  ): Promise<Approval> {
    const state = this.state(id);
    const approval = state.approvals.find((item) => item.id === approvalId);
    if (!approval) return Promise.reject(new Error('aprovação não encontrada no mock'));
    if (approval.status !== 'pending') {
      return Promise.reject(new Error(`aprovação já ${approval.status === 'approved' ? 'aprovada' : 'rejeitada'}`));
    }
    const decided = this.markApproval(id, state, approval, approveIt, note);
    return this.delay(clone(decided));
  }

  /**
   * Marca a aprovação (e a pergunta que a originou) e difunde `approval_updated`.
   * Devolve a aprovação já decidida.
   */
  private markApproval(
    id: string,
    state: MockState,
    approval: Approval,
    approveIt: boolean,
    note?: string,
  ): Approval {
    const ts = iso(new Date());
    approval.status = approveIt ? 'approved' : 'rejected';
    approval.decided_at = ts;
    if (note) approval.note = note;
    state.approvals = state.approvals.map((item) => (item.id === approval.id ? approval : item));

    // Aprovar/rejeitar é responder à pergunta de aprovação: a fila reflete-o.
    const question = state.questions.find((item) => item.id === approval.id);
    if (question) {
      question.status = 'answered';
      question.answer_text = approveIt ? 'yes' : 'no';
      question.option_id = approveIt ? 'yes' : 'no';
    }
    this.emit(id, { type: 'approval_updated', ts, payload: clone(approval) });
    return approval;
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

  /* ---------------------------------------------------------- aventuras --- */

  listAdventures(id: string) {
    const state = this.state(id);
    return this.delay<AdventuresResponse>({
      adventures: clone(state.adventures),
      current: state.current ? clone(state.current) : null,
    });
  }

  getAdventureStatus(id: string) {
    const state = this.state(id);
    return this.delay<AdventureStatusResponse>({
      task: state.task ? clone(state.task) : null,
      current: state.current ? clone(state.current) : null,
    });
  }

  startNewWorld(id: string, body: NewWorldRequest) {
    return this.beginTask(id, 'new_world', {
      worldName: body.world_name?.trim() || undefined,
    });
  }

  startNewCharacter(id: string, body: NewCharacterRequest) {
    // `randomize` clica os labels `Randomize` do jogo: sem `name`, o jogo escolhe.
    const name =
      body.name?.trim() ||
      (body.randomize ? pick(ADVENTURER_NAMES, Math.round(Date.now() / 1000)) : undefined);
    return this.beginTask(id, 'new_character', { characterName: name });
  }

  loadAdventure(id: string, body: LoadAdventureRequest) {
    const state = this.state(id);
    const save = state.adventures.find((item) => item.id === body.adventure_id);
    if (!save) {
      return Promise.reject(new Error(`aventura desconhecida: ${body.adventure_id}`));
    }
    return this.beginTask(id, 'load', { adventureId: save.id });
  }

  cancelAdventureTask(id: string) {
    const state = this.state(id);
    const task = state.task;
    if (!task || task.state !== 'running') {
      // Sem tarefa em curso: `cancelled: false` e a última tarefa (se houver).
      return this.delay<AdventureCancelResponse>({
        cancelled: false,
        task: task ? clone(task) : null,
      });
    }
    this.stopTaskTimer(id);
    task.state = 'error';
    task.error = 'cancelled';
    this.emitTask(id, state);
    return this.delay<AdventureCancelResponse>({ cancelled: true, task: clone(task) });
  }

  /**
   * Arranca uma tarefa assíncrona (uma de cada vez por campanha — a segunda
   * devolve o equivalente ao `409` do contrato). O WS `adventure_task` é emitido
   * no arranque, em cada passo e no fim.
   */
  private beginTask(
    id: string,
    kind: AdventureTaskKind,
    context: TaskContext,
  ): Promise<AdventureTaskStart> {
    const state = this.state(id);
    if (state.task && state.task.state === 'running') {
      return Promise.reject(new Error('já existe uma tarefa de aventura em curso nesta campanha'));
    }
    const task: AdventureTask = {
      id: `t-${Math.random().toString(16).slice(2, 14)}`,
      kind,
      state: 'running',
      step: 0,
      steps_total: TASK_STEPS[kind],
      last_screen_pt: TASK_SCREENS[kind][0],
    };
    state.task = task;
    // Composição do contrato: `new-character` herda o mundo/save da tarefa
    // anterior (`new-world` ou `load`) quando não traz o seu próprio alvo.
    state.taskContext = { ...state.taskContext, ...context };
    this.emitTask(id, state);
    this.scheduleTaskStep(id);
    return this.delay<AdventureTaskStart>({ task_id: task.id, kind }, 180);
  }

  /** Avança um passo da tarefa em curso e agenda o seguinte. */
  private scheduleTaskStep(id: string) {
    const kind = this.state(id).task?.kind ?? 'load';
    this.stopTaskTimer(id);
    const timer = window.setTimeout(() => {
      const state = this.state(id);
      const task = state.task;
      if (!task || task.state !== 'running') return;
      task.step += 1;
      const screens = TASK_SCREENS[task.kind];
      task.last_screen_pt = screens[Math.min(task.step, screens.length) - 1] ?? task.last_screen_pt;
      if (task.step >= task.steps_total) {
        task.state = 'done';
        this.finishTask(state, task);
      }
      this.emitTask(id, state);
      if (task.state === 'running') this.scheduleTaskStep(id);
    }, TASK_STEP_MS[kind]);
    this.taskTimers.set(id, timer);
  }

  private stopTaskTimer(id: string) {
    const timer = this.taskTimers.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      this.taskTimers.delete(id);
    }
  }

  /** Efeitos da tarefa terminada: saves, aventura atual (como o backend real). */
  private finishTask(state: MockState, task: AdventureTask) {
    const context = state.taskContext;
    const ts = iso(new Date());
    if (task.kind === 'new_world') {
      const name = context.worldName ?? 'Mundo novo';
      const saveId = slugify(name);
      const existing = state.adventures.find((item) => item.id === saveId);
      if (existing) {
        existing.kind = 'world';
        existing.saved_at = ts;
      } else {
        state.adventures = [saveOf(saveId, name, 'world', 0), ...state.adventures];
      }
      state.taskContext = { ...context, worldName: name, adventureId: saveId };
      return;
    }
    const saveId = context.adventureId ?? state.adventures[0]?.id ?? 'region1';
    const save = state.adventures.find((item) => item.id === saveId);
    if (save) {
      if (task.kind === 'new_character') save.kind = 'active';
      save.saved_at = ts;
    }
    if (task.kind === 'load' && save?.kind === 'world') {
      // Contrato: `current` é null sem aventureiro — um save `world` só tem o
      // mundo gerado; o `new-character` compõe depois e só ele cria o aventureiro.
      state.current = null;
      return;
    }
    state.current = {
      adventure_id: saveId,
      adventurer_name: context.characterName ?? state.current?.adventurer_name ?? 'Aventureiro',
      world_name: save?.name ?? context.worldName ?? 'Mundo guardado',
      alive: true,
    };
  }

  /** Difunde o progresso da tarefa (objeto `task` do contrato, `task_id` no WS). */
  private emitTask(id: string, state: MockState) {
    const task = state.task;
    if (!task) return;
    this.emit(id, {
      type: 'adventure_task',
      ts: iso(new Date()),
      payload: {
        task_id: task.id,
        kind: task.kind,
        state: task.state,
        step: task.step,
        steps_total: task.steps_total,
        ...(task.last_screen_pt ? { last_screen_pt: task.last_screen_pt } : {}),
        ...(task.error ? { error: task.error } : {}),
      },
    });
  }

  /* ------------------------------------------------------------- sistema --- */

  private readonly system: SystemStatus = {
    game: { running: true, pid: 41236, rpc: true, path: '/home/ondokai/Games/dwarf-fortress-53.16' },
    backend: { version: '0.1.0-mock' },
    frontend: { running: true, port: 5173 },
    loop: { campaigns: [] },
  };

  getSystemStatus() {
    const campaigns = [...this.states.values()].map((state) => ({
      id: state.campaign.id,
      mode: state.campaign.control.mode,
      running: state.campaign.control.mode !== 'paused' && !state.campaign.control.kill_switch,
    }));
    return this.delay<SystemStatus>(clone({ ...this.system, loop: { campaigns } }));
  }

  setGameAction(body: GameActionRequest) {
    const game = this.system.game;
    if (body.action === 'start') {
      if (game.running) {
        return this.delay<GameActionResponse>(
          { action: 'start', running: true, pid: game.pid, already_running: true },
          250,
        );
      }
      game.running = true;
      game.pid = 41236;
      game.rpc = true;
      return this.delay<GameActionResponse>(
        { action: 'start', running: true, pid: game.pid, already_running: false },
        500,
      );
    }
    game.running = false;
    game.rpc = false;
    delete game.pid;
    return this.delay<GameActionResponse>({ action: 'stop', running: false }, 900);
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
      // Ações retidas: só o modo `checkpoint` as produz (em autopilot a lista
      // de aprovações fica vazia). A pergunta espelhada segue na fila.
      if (tick % 6 === 0 && state.campaign.control.mode === 'checkpoint') {
        const approvalEvent = burst(state, 'approval_new');
        if (approvalEvent) send(approvalEvent);
        const mirrored = state.questions[0];
        if (mirrored) {
          send({ type: 'question_new', ts: mirrored.ts, payload: clone(mirrored) });
        }
      }
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
  approvals: Approval[];
  screen: ScreenTranslation;
  inputs: InputHistoryEntry[];
  keys: KeyInfo[];
  /** Saves do DF + aventura atual (`docs/api.md` §aventuras). */
  adventures: AdventureSave[];
  current: CurrentAdventure | null;
  /** Última tarefa de aventura (terminada ou em curso) — `null` antes da primeira. */
  task: AdventureTask | null;
  /** Contexto mock da tarefa em curso (nome do mundo/personagem/save alvo). */
  taskContext: TaskContext;
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
    narrative: OBJECTIVE_NARRATIVE_EN,
    narrative_pt: OBJECTIVE_NARRATIVE_PT,
    subgoals: SUBGOAL_SEEDS.map((goal) => ({ ...goal })),
  };

  const state: MockState = {
    campaign,
    memory,
    objective,
    metrics: buildMetrics(seed.mode),
    spend: buildSpend(),
    decisions: buildDecisions(),
    questions: buildQuestions(seed.mode),
    approvals: retainedApprovalSeeds(seed.mode).map(makeApproval),
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
    adventures: seed.adventures.map((save) => ({ ...save })),
    current: seed.current ? { ...seed.current } : null,
    task: null,
    taskContext: {},
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
  const bandCycle: Decision['band'][] = [
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
  // 63 decisões: mais do que 2 páginas de 25 — o «carregar mais» aparece sempre.
  const bands = Array.from(
    { length: 63 },
    (_, i) => bandCycle[i % bandCycle.length] as Decision['band'],
  );
  const decisions: Decision[] = [];
  for (let i = 0; i < bands.length; i += 1) {
    const band = bands[i] as Decision['band'];
    const agent = pick(JEV_AGENTS, i);
    // Uma decisão com confidence ausente: "indisponível", não "certo".
    const confidence =
      i === 4 ? null : band === 'auto' ? round(randomBetween(0.9, 0.99), 2) : round(randomBetween(0.42, 0.79), 2);
    /*
     * Desfecho (aditivo): só o JEV-A (que escolhe ação) o tem; auxiliares
     * JEV-R/M/D ficam sem `status` — «desconhecido», nunca «executada». Os
     * `issues` são as marcas de auditoria do contrato; os índices escolhidos
     * (8/12) são recentes (i=0 é o mais novo) para caírem na primeira página.
     */
    const status: DecisionStatus | undefined =
      agent === 'JEV-A' ? (i === 8 ? 'error' : band === 'auto' ? 'executed' : 'question') : undefined;
    const issues: string[] | undefined =
      i === 12 ? ['released_by_approval'] : i === 8 ? ['step_failed_before_dispatch'] : undefined;
    decisions.push(
      makeDecision(i, agent, band, confidence, minutesAgo(1 + i * 2.5), status, issues),
    );
  }
  return decisions.reverse();
}

function makeDecision(
  index: number,
  agent: JevAgent,
  band: Decision['band'],
  confidence: number | null,
  ts: string,
  status?: DecisionStatus,
  issues?: string[],
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
    // Aditivos: omitidos quando desconhecidos/vazios (semântica do contrato).
    ...(status ? { status } : {}),
    ...(issues && issues.length > 0 ? { issues } : {}),
  };
}

function buildQuestions(mode: ControlState['mode']): Question[] {
  return [
    // As perguntas de aprovação do modo `checkpoint` estão também na fila.
    ...retainedApprovalSeeds(mode).map(approvalQuestion),
    ...QUESTION_SEEDS.map((seed, i) => ({
      id: `q-${i + 1}`,
      ts: minutesAgo(seed.minutes),
      question: seed.question,
      options: seed.options,
      free_text_allowed: true,
      status: seed.status,
      answer_text: seed.answer_text_pt ? `[EN] ${seed.answer_text_pt}` : null,
      option_id: seed.status === 'answered' ? (seed.options[0]?.id ?? null) : null,
      /*
       * Proveniência ADITIVA: um seed sem estes campos produz exatamente o
       * payload antigo de 6 campos (é o caso do 3.º pendente, de propósito).
       */
      ...(seed.band ? { band: seed.band } : {}),
      ...(seed.role ? { role: seed.role } : {}),
      ...(seed.action_id ? { action_id: seed.action_id } : {}),
      ...(seed.why_pt ? { why_pt: seed.why_pt } : {}),
      ...(seed.source_data ? { source_data: seed.source_data } : {}),
      // Dedupe: `attempts`/`last_seen_ts` só quando o loop voltou a pedir.
      ...(seed.attempts
        ? { attempts: seed.attempts, last_seen_ts: minutesAgo(Math.max(0, seed.minutes - 1)) }
        : {}),
    })),
  ];
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
      const agent = pick(JEV_AGENTS, index);
      // Só o JEV-A tem desfecho; auxiliares ficam sem `status` (desconhecido).
      const status: DecisionStatus | undefined =
        agent === 'JEV-A' ? (band === 'auto' ? 'executed' : 'question') : undefined;
      const decision = makeDecision(index, agent, band, confidence, ts, status);
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
      /*
       * Dedupe anti-flood: de vez em quando o loop VOLTA a pedir uma ação já
       * pendente — o backend não cria entrada nova, a pendente ganha
       * `attempts`/`last_seen_ts` (docs/api.md §questions).
       */
      const reask =
        index % 3 === 2
          ? state.questions.find((item) => item.status === 'pending' && item.action_id)
          : undefined;
      if (reask) {
        reask.attempts = (reask.attempts ?? 1) + 1;
        reask.last_seen_ts = ts;
        return { type: 'question_new', ts, payload: clone(reask) };
      }
      const seed = pick(QUESTION_BURSTS, index);
      // Proveniência realista: banda, ação retida (JEV-A) ou escalada de dúvida
      // sem ação (JEV-D) — a mesma imputação que o backend faz.
      const band = pick<DecisionBand>(['hitl', 'abstain', 'hitl', 'auto'], index);
      const actionId = band === 'abstain' ? undefined : pick(ACTION_CANDIDATES, index + 1);
      const question: Question = {
        id: `q-${index + 1}`,
        ts,
        question: seed.question,
        options: seed.options.map((text, option) => ({ id: `opt-${index * 2 + 20 + option}`, text })),
        free_text_allowed: true,
        status: 'pending',
        answer_text: null,
        option_id: null,
        band,
        role: actionId ? 'JEV-A' : 'JEV-D',
        ...(actionId ? { action_id: actionId } : {}),
        why_pt: whyPtFor(band),
        source_data: {
          reason: `band_${band}`,
          band,
          action_id: actionId ?? null,
          confidence_available: band !== 'abstain',
        },
      };
      state.questions = [question, ...state.questions];
      return { type: 'question_new', ts, payload: clone(question) };
    }
    case 'approval_new': {
      const seed = pick(APPROVAL_SEEDS, state.tick + state.approvals.length);
      const approval = makeApproval({ ...seed, qid: `q-live-${state.approvals.length + 1}`, minutes: 0 });
      approval.ts = ts;
      state.approvals = [...state.approvals, approval];
      // O backend espelha a pergunta de aprovação na fila de perguntas.
      state.questions = [approvalQuestion({ ...seed, qid: approval.id, minutes: 0 }), ...state.questions];
      return { type: 'approval_new', ts, payload: clone(approval) };
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
