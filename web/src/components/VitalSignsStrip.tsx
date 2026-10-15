import type { ReactNode } from 'react';

import { AnimatedNumber, type NumberFormat } from '@/components/motion-ui/animated-number';
import {
  USD_COUNTER_COMPACT_FORMAT,
  USD_PREFIX,
  formatConfidence,
  formatNumber,
  formatPercent,
  severityTone,
  valuePt,
} from '../api/format';
import type { Metrics } from '../api/types';
import {
  CharacterSheet,
  type CharacterSkill,
  type CharacterStat,
  type CharacterVital,
} from './rpg/CharacterSheet';
import Sparkline from './Sparkline';

type Tone = 'default' | 'success' | 'warning' | 'error';

/**
 * Tom de um sinal em texto (§2.3 regra 1): a etiqueta PT é sempre a leitura
 * primária — a cor do token de estado é só reforço, nunca o único sinal.
 */
const TONE_CLASS: Record<Tone, string> = {
  default: '',
  success: 'text-success',
  warning: 'text-warning',
  error: 'text-destructive',
};

export interface VitalSignsStripProps {
  metrics: Metrics;
  /**
   * Nome da campanha — identifica a ficha com um valor REAL do contrato
   * (§7.17: a metáfora veste, não renomeia; nada de atributos inventados).
   */
  name: string;
  /** Séries rolantes para as tendências (skills) — opcionais. */
  series?: {
    decisions?: number[];
    confidence?: number[];
    latency?: number[];
    cost?: number[];
  };
}

/** Pior tom entre vários valores do contrato (ex.: fome/sede/sono). */
function worstTone(values: string[]): Tone {
  const tones = values.map((value) => severityTone(value) as Tone);
  if (tones.includes('error')) return 'error';
  if (tones.includes('warning')) return 'warning';
  return 'success';
}

function needsDetail(metrics: Metrics): string {
  const entries: [string, string][] = [
    ['fome', metrics.needs.hunger],
    ['sede', metrics.needs.thirst],
    ['sono', metrics.needs.sleep],
  ];
  const raised = entries.filter(([, value]) => severityTone(value) !== 'success');
  if (raised.length === 0) return 'fome, sede e sono sem avisos';
  return raised.map(([label, value]) => `${label}: ${valuePt(value)}`).join(' · ');
}

/** Valor + detalhe: o contador é a leitura primária, o contexto fica por baixo. */
function statValue(value: ReactNode, tone: Tone, detail: string): ReactNode {
  return (
    <>
      <span className={TONE_CLASS[tone]}>{value}</span>
      <span className="block text-micro font-sans font-normal text-muted-foreground">{detail}</span>
    </>
  );
}

/**
 * Os 5 sinais vitais acima da dobra (auditoria F.1.1.02) na FICHA DE PERSONAGEM
 * (spec §5.4, componente partilhado `rpg/CharacterSheet`): stats em grid mono
 * `tabular-nums` (2–4 colunas), barras 8px com valor numérico sempre visível —
 * a barra nunca é a única leitura — e skills em linha com tendência
 * (`sparkline` do registry). Contadores `lively` só nos valores que mudam (§8).
 *
 * Sem inventar atributos: só campos do contrato (`docs/api.md`) — os enums de
 * saúde/necessidades ficam etiquetados; as barras exigem razões reais
 * (progresso 0..1, confiança 0..1), nunca escalas fabricadas.
 */
export default function VitalSignsStrip({ metrics, name, series }: VitalSignsStripProps) {
  const confidence = formatConfidence(metrics.agent.confidence_avg);
  const needsTone = worstTone([metrics.needs.hunger, metrics.needs.thirst, metrics.needs.sleep]);
  const healthTone = (severityTone(metrics.health.status) as Tone) ?? 'default';
  const threatTone = (severityTone(metrics.threats.level) as Tone) ?? 'default';
  const progress = metrics.objective_progress.percent;

  const stats: CharacterStat[] = [
    {
      label: 'Saúde',
      value: statValue(
        valuePt(metrics.health.status),
        healthTone,
        metrics.health.wounds.length === 0
          ? 'sem ferimentos registados'
          : `${metrics.health.wounds.length} ferimento(s) registado(s)`,
      ),
    },
    {
      label: 'Ameaças',
      value: statValue(
        valuePt(metrics.threats.level),
        metrics.threats.combat ? 'error' : threatTone,
        metrics.threats.combat
          ? `em combate · ${metrics.threats.hostiles.length} hostis`
          : `${metrics.threats.hostiles.length} hostis avistados`,
      ),
    },
    {
      label: 'Fome, sede e sono',
      value: statValue(
        needsTone === 'success' ? 'em ordem' : needsTone === 'warning' ? 'aviso' : 'crítico',
        needsTone,
        needsDetail(metrics),
      ),
    },
    {
      label: 'Progresso do objetivo',
      value: statValue(
        formatPercent(progress),
        'success',
        metrics.objective_progress.active_subgoal_id
          ? `sub-objetivo: ${metrics.objective_progress.active_subgoal_id}`
          : 'sem sub-objetivo ativo',
      ),
    },
    {
      label: 'Gastos e confiança',
      value: statValue(
        <AnimatedNumber
          value={metrics.agent.cost_usd}
          transition="lively"
          format={USD_COUNTER_COMPACT_FORMAT as NumberFormat}
          prefix={USD_PREFIX}
          locales="pt-PT"
        />,
        'default',
        `${formatNumber(metrics.agent.calls)} chamadas · confiança ${confidence ?? 'indisponível'}`,
      ),
    },
  ];

  // Barras (§5.4): só razões reais do contrato — sem escalas inventadas. A
  // confiança ausente NÃO vira barra a zero («indisponível» honesto).
  const vitals: CharacterVital[] = [
    { label: 'Progresso do objetivo (0–100)', current: Math.round(progress * 100), max: 100 },
  ];
  if (confidence !== null) {
    vitals.push({
      label: 'Confiança média (0–100)',
      current: Math.round(metrics.agent.confidence_avg * 100),
      max: 100,
    });
  }

  const skills: CharacterSkill[] = [
    {
      label: 'Decisões por minuto',
      value: (
        <AnimatedNumber
          value={metrics.agent.decisions_per_min}
          transition="lively"
          format={{ maximumFractionDigits: 1 } as NumberFormat}
          locales="pt-PT"
        />
      ),
      trend: series?.decisions ? (
        <Sparkline values={series.decisions} label="Decisões por minuto" />
      ) : undefined,
    },
    {
      label: 'Confiança média',
      value: confidence ?? 'indisponível',
      trend: series?.confidence ? (
        <Sparkline values={series.confidence} label="Confiança média" color="success.main" />
      ) : undefined,
    },
    {
      label: 'Latência média',
      value: `${formatNumber(metrics.agent.latency_ms)} ms`,
      trend: series?.latency ? (
        <Sparkline values={series.latency} label="Latência média" color="secondary.main" />
      ) : undefined,
    },
    {
      label: 'Custo acumulado',
      value: (
        <AnimatedNumber
          value={metrics.agent.cost_usd}
          transition="lively"
          format={USD_COUNTER_COMPACT_FORMAT as NumberFormat}
          prefix={USD_PREFIX}
          locales="pt-PT"
        />
      ),
      trend: series?.cost ? (
        <Sparkline values={series.cost} label="Custo acumulado" color="warning.main" />
      ) : undefined,
    },
    {
      label: 'Chamadas JEV',
      value: formatNumber(metrics.agent.calls),
    },
  ];

  return (
    <CharacterSheet title="Ficha de personagem" name={name} stats={stats} vitals={vitals} skills={skills} />
  );
}
