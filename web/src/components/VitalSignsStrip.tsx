import type { ReactNode } from 'react';
import Box from '@mui/material/Box';
import Grid from '@mui/material/Grid';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import HealthAndSafety from '@mui/icons-material/HealthAndSafety';
import Payments from '@mui/icons-material/Payments';
import Restaurant from '@mui/icons-material/Restaurant';
import Shield from '@mui/icons-material/Shield';
import TrackChanges from '@mui/icons-material/TrackChanges';

import {
  formatConfidence,
  formatNumber,
  formatPercent,
  formatUsd,
  severityTone,
  valuePt,
} from '../api/format';
import type { Metrics } from '../api/types';
import { MONO_FONT } from '../theme';

type Tone = 'default' | 'success' | 'warning' | 'error' | 'info' | 'primary';

const TONE_COLOR: Record<Tone, string> = {
  default: 'text.primary',
  success: 'success.main',
  warning: 'warning.main',
  error: 'error.main',
  info: 'info.main',
  primary: 'primary.main',
};

export interface VitalSignal {
  key: string;
  label: string;
  value: string;
  /** Segunda linha: contexto curto. */
  detail: string;
  tone: Tone;
  icon: ReactNode;
  /** Destaque quando o sinal pede atenção (borda + fundo). */
  attention?: boolean;
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

/**
 * Os 5 sinais vitais acima da dobra (auditoria F.1.1.02): responde a «está tudo
 * bem?» sem scroll e sem abrir nenhum cartão. Os restantes grupos ficam nos
 * acordeões abaixo, ordenados por severidade.
 */
function buildVitalSignals(metrics: Metrics): VitalSignal[] {
  const confidence = formatConfidence(metrics.agent.confidence_avg);
  const needsTone = worstTone([metrics.needs.hunger, metrics.needs.thirst, metrics.needs.sleep]);
  const healthTone = (severityTone(metrics.health.status) as Tone) ?? 'default';
  const threatTone = (severityTone(metrics.threats.level) as Tone) ?? 'default';
  const progress = metrics.objective_progress.percent;

  return [
    {
      key: 'saude',
      label: 'Saúde',
      value: valuePt(metrics.health.status),
      detail:
        metrics.health.wounds.length === 0
          ? 'sem ferimentos registados'
          : `${metrics.health.wounds.length} ferimento(s) registado(s)`,
      tone: healthTone,
      icon: <HealthAndSafety fontSize="small" />,
      attention: healthTone === 'error',
    },
    {
      key: 'ameacas',
      label: 'Ameaças',
      value: valuePt(metrics.threats.level),
      detail: metrics.threats.combat
        ? `em combate · ${metrics.threats.hostiles.length} hostis`
        : `${metrics.threats.hostiles.length} hostis avistados`,
      tone: metrics.threats.combat ? 'error' : threatTone,
      icon: <Shield fontSize="small" />,
      attention: metrics.threats.combat || threatTone === 'error',
    },
    {
      key: 'necessidades',
      label: 'Fome, sede e sono',
      value: needsTone === 'success' ? 'em ordem' : needsTone === 'warning' ? 'aviso' : 'crítico',
      detail: needsDetail(metrics),
      tone: needsTone,
      icon: <Restaurant fontSize="small" />,
      attention: needsTone === 'error',
    },
    {
      key: 'objetivo',
      label: 'Progresso do objetivo',
      value: formatPercent(progress),
      detail: metrics.objective_progress.active_subgoal_id
        ? `sub-objetivo: ${metrics.objective_progress.active_subgoal_id}`
        : 'sem sub-objetivo ativo',
      tone: 'success',
      icon: <TrackChanges fontSize="small" />,
    },
    {
      key: 'agente',
      label: 'Gastos e confiança',
      value: formatUsd(metrics.agent.cost_usd, { compact: true }),
      detail: `${formatNumber(metrics.agent.calls)} chamadas · confiança ${
        confidence ?? 'indisponível'
      }`,
      tone: confidence === null ? 'default' : 'info',
      icon: <Payments fontSize="small" />,
    },
  ];
}

/** Tira de sinais vitais do painel de métricas. */
export default function VitalSignsStrip({ metrics }: { metrics: Metrics }) {
  const signals = buildVitalSignals(metrics);

  return (
    <Box component="section" aria-label="Resumo: sinais vitais">
      <Grid container spacing={1.5} sx={{ width: '100%' }}>
        {signals.map((signal) => (
          <Grid key={signal.key} size={{ xs: 12, sm: 6, md: 4, xl: 2.4 }}>
            <Paper
              variant="outlined"
              sx={{
                p: 1.5,
                height: '100%',
                borderColor: signal.attention ? 'error.main' : 'divider',
                backgroundColor: signal.attention ? 'error.main' : 'background.paper',
                ...(signal.attention ? { color: 'error.contrastText' } : null),
              }}
            >
              <Stack spacing={0.5}>
                <Stack direction="row" spacing={0.75} sx={{ alignItems: 'center' }}>
                  <Box
                    sx={{
                      display: 'inline-flex',
                      color: signal.attention ? 'inherit' : TONE_COLOR[signal.tone],
                    }}
                  >
                    {signal.icon}
                  </Box>
                  <Typography
                    variant="overline"
                    sx={{ color: signal.attention ? 'inherit' : 'text.secondary' }}
                  >
                    {signal.label}
                  </Typography>
                </Stack>
                <Tooltip title={signal.detail}>
                  <Typography
                    variant="h4"
                    component="p"
                    sx={{ fontFamily: MONO_FONT, color: signal.attention ? 'inherit' : undefined }}
                  >
                    {signal.value}
                  </Typography>
                </Tooltip>
                <Typography
                  variant="caption"
                  sx={{
                    color: signal.attention ? 'inherit' : 'text.secondary',
                    display: 'block',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap',
                  }}
                >
                  {signal.detail}
                </Typography>
              </Stack>
            </Paper>
          </Grid>
        ))}
      </Grid>
    </Box>
  );
}
