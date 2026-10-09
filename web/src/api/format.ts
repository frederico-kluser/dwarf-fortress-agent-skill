/**
 * Formatação para a UI em português: horas, dinheiro (USD) e valores diversos.
 * Dinheiro é sempre USD (contrato) — só a apresentação é pt-PT.
 */

import type { ControlMode, DecisionBand, DecisionStatus, JevAgent, QuestionStatus } from './types';

const timeFmt = new Intl.DateTimeFormat('pt-PT', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

const dateTimeFmt = new Intl.DateTimeFormat('pt-PT', {
  day: '2-digit',
  month: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
});

const number1Fmt = new Intl.NumberFormat('pt-PT', {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});

const intFmt = new Intl.NumberFormat('pt-PT');

function toDate(ts?: string | null): Date | null {
  if (!ts) return null;
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** `HH:MM:SS` local; `—` se o timestamp faltar ou for inválido. */
export function formatTime(ts?: string | null): string {
  const d = toDate(ts);
  return d ? timeFmt.format(d) : '—';
}

/** `dd/mm HH:MM:SS` local. */
export function formatDateTime(ts?: string | null): string {
  const d = toDate(ts);
  return d ? dateTimeFmt.format(d) : '—';
}

/** `há 12 s` / `há 3 min` — usado nos painéis com dados vivos. */
export function formatRelative(ts?: string | null, now: number = Date.now()): string {
  const d = toDate(ts);
  if (!d) return '—';
  const seconds = Math.max(0, Math.round((now - d.getTime()) / 1000));
  if (seconds < 60) return `há ${seconds} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `há ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return `há ${hours} h`;
}

/**
 * USD com 6 casas decimais (convenção do contrato): `US$ 0,042318`.
 * `compact` corta para 4 casas — só para chips e tabelas densas.
 */
export function formatUsd(value?: number | null, options?: { compact?: boolean }): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  const decimals = options?.compact ? 4 : 6;
  const formatted = new Intl.NumberFormat('pt-PT', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
  return `US$ ${formatted}`;
}

/**
 * Espelhos `Intl` de `formatUsd` para os contadores animados (Motion UI
 * `AnimatedNumber`): mesma convenção do contrato, `US$ 0,042318`. O símbolo
 * entra como `prefix` — `Intl` em pt-PT põe o símbolo depois do número e isso
 * quebrava a convenção da app.
 */
export const USD_PREFIX = 'US$ ';

export const USD_COUNTER_FORMAT: Intl.NumberFormatOptions = {
  minimumFractionDigits: 6,
  maximumFractionDigits: 6,
};

/** Variante compacta (4 casas) — chips e cartões densos. */
export const USD_COUNTER_COMPACT_FORMAT: Intl.NumberFormatOptions = {
  minimumFractionDigits: 4,
  maximumFractionDigits: 4,
};

export function formatNumber(value?: number | null, decimals = 0): string {
  if (typeof value !== 'number' || Number.isNaN(value)) return '—';
  if (decimals === 0) return intFmt.format(value);
  return new Intl.NumberFormat('pt-PT', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value);
}

export function formatPercent(value01?: number | null, decimals = 0): string {
  if (typeof value01 !== 'number' || Number.isNaN(value01)) return '—';
  const formatted = new Intl.NumberFormat('pt-PT', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  }).format(value01 * 100);
  return `${formatted} %`;
}

/**
 * Confidence do JEV: **ausente ou 0 = indisponível** (nunca "certo").
 * Devolve `null` nesses casos para que a UI mostre "indisponível".
 */
export function formatConfidence(value?: number | null): string | null {
  if (typeof value !== 'number' || Number.isNaN(value) || value <= 0) return null;
  return `${number1Fmt.format(value * 100)} %`;
}

/* ------------------------------------------------------ dicionários PT --- */

export const CONTROL_MODE_LABEL: Record<ControlMode, string> = {
  autopilot: 'Autopiloto',
  checkpoint: 'Checkpoints',
  paused: 'Em pausa',
};

export const BAND_LABEL: Record<DecisionBand, string> = {
  auto: 'auto',
  hitl: 'hitl · confirmação',
  abstain: 'abstenção',
};

/**
 * Vocabulário das bandas — o modelo mental do produto (auditoria v2, achado 4):
 * cada chip de banda leva esta frase no tooltip, e as opções de filtro também.
 * Intervalos conforme `docs/arquitetura.md` §Bandas de ação.
 */
export const BAND_DESCRIPTION: Record<DecisionBand, string> = {
  auto: 'auto · confiança ≥ 0,90 — a ação executa sozinha, sem pedir confirmação',
  hitl: 'hitl · confiança 0,50–0,89 — a ação fica retida até um humano confirmar',
  abstain: 'abstain · confiança < 0,50 — o agente não age e escala a dúvida ao humano',
};

/** Desfecho da ação escolhida (`Decision.status`, ADITIVO). */
export const DECISION_STATUS_LABEL: Record<DecisionStatus, string> = {
  executed: 'executada',
  question: 'virou pergunta',
  error: 'erro',
};

export const DECISION_STATUS_DESCRIPTION: Record<DecisionStatus, string> = {
  executed: 'A ação escolhida foi executada (despachada ou libertada por aprovação humana)',
  question: 'A decisão virou pergunta ao humano — a ação ficou retida à espera de resposta',
  error: 'O passo falhou antes de executar a ação',
};

export const JEV_ROLE_LABEL: Record<JevAgent, string> = {
  'JEV-R': 'R · recuperação',
  'JEV-A': 'A · ação',
  'JEV-M': 'M · memória',
  'JEV-D': 'D · dúvida',
};

export const QUESTION_STATUS_LABEL: Record<QuestionStatus, string> = {
  pending: 'pendente',
  answered: 'respondida',
};

/** Vocabulário EN do contrato → PT (fallback: o valor original). */
const VALUE_PT: Record<string, string> = {
  // needs
  none: 'nenhuma',
  rested: 'descansado',
  drowsy: 'sonolento',
  tired: 'cansado',
  exhausted: 'exausto',
  hungry: 'com fome',
  starving: 'a morrer de fome',
  thirsty: 'com sede',
  dehydrated: 'desidratado',
  // health
  healthy: 'saudável',
  wounded: 'ferido',
  critical: 'crítico',
  dead: 'morto',
  // threats
  low: 'baixa',
  medium: 'média',
  high: 'alta',
  extreme: 'extrema',
  // combat outcome
  victory: 'vitória',
  defeat: 'derrota',
  fled: 'fugiu',
  ongoing: 'em curso',
  // criaturas e entidades do jogo (nomes canónicos em EN no contrato)
  'goblin spearman': 'lanceiro goblin',
  'goblin lasher': 'açoitador goblin',
  'goblin': 'goblin',
  'wild boar': 'javali selvagem',
  'wolf pack': 'alcateia de lobos',
  'wolf': 'lobo',
  'kobold thief': 'ladrão kobold',
  'giant cave spider': 'aranha gigante da caverna',
  'cave spider': 'aranha da caverna',
  'merchant': 'mercador',
  // kinds
  episodic: 'episódico',
  semantic: 'semântico',
  procedural: 'procedimental',
  working: 'trabalho',
};

/** Traduz um valor enumerado do contrato para PT; devolve o original se desconhecido. */
export function valuePt(value: unknown): string {
  if (value === null || value === undefined) return '—';
  const raw = typeof value === 'string' ? value : String(value);
  return VALUE_PT[raw.toLowerCase()] ?? raw;
}

/** Texto legível para valores arbitrários (objetos/arrays vêm do contrato sem forma fixa). */
export function asText(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(asText).join(', ');
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

/** Mapeia o vocabulário EN de severidade para um tom de chip. */
export function severityTone(value: string): 'default' | 'success' | 'warning' | 'error' {
  const normalized = value.toLowerCase();
  if (['none', 'rested', 'healthy', 'low', 'nenhuma'].includes(normalized)) return 'success';
  if (['medium', 'drowsy', 'hungry', 'thirsty', 'wounded', 'média'].includes(normalized)) return 'warning';
  if (['high', 'extreme', 'starving', 'dehydrated', 'exhausted', 'critical', 'dead', 'alta', 'extrema'].includes(normalized)) {
    return 'error';
  }
  return 'default';
}
