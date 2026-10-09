import { useMemo } from 'react';
import Alert from '@mui/material/Alert';
import Grid from '@mui/material/Grid';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';

import { errorMessage } from '../api/events';
import { formatRelative, severityTone, valuePt } from '../api/format';
import { useMetrics } from '../api/queries';
import type { Metrics } from '../api/types';
import FreshnessIndicator from '../components/FreshnessIndicator';
import {
  AgentMetricsGroup,
  CombatHistoryGroup,
  HealthGroup,
  InventoryGroup,
  LocationGroup,
  NeedsGroup,
  ObjectiveProgressGroup,
  ThreatsGroup,
} from '../components/MetricsGroups';
import { SEVERITY_RANK, worstSeverity, type Severity } from '../components/metricsSeverity';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import VitalSignsStrip from '../components/VitalSignsStrip';
import { useNow, useSparkSeries } from '../hooks/useSparkSeries';

/**
 * Painel de métricas vivas.
 *
 * Auditoria F.1.1.02: era o painel de entrada e o mais denso — 13 blocos num só
 * scroll, todos com o mesmo peso visual. Agora: (1) tira de 5 sinais vitais acima
 * da dobra; (2) os 8 grupos do contrato em acordeões fechados, ORDENADOS POR
 * SEVERIDADE (crítico → aviso → atenção → informativo), com o resumo legível no
 * cabeçalho mesmo fechado.
 */
export default function MetricsPanel() {
  const metricsQuery = useMetrics();
  const metrics: Metrics | undefined = metricsQuery.data;
  const now = useNow(1000);

  const decisionsSeries = useSparkSeries(metrics?.ts, metrics?.agent.decisions_per_min);
  const confidenceSeries = useSparkSeries(metrics?.ts, metrics?.agent.confidence_avg);
  const latencySeries = useSparkSeries(metrics?.ts, metrics?.agent.latency_ms);
  const costSeries = useSparkSeries(metrics?.ts, metrics?.agent.cost_usd);
  const progressSeries = useSparkSeries(metrics?.ts, metrics?.objective_progress.percent);

  /** Grupos com a severidade calculada, prontos a ordenar. */
  const groups = useMemo(() => {
    if (!metrics) return [];
    const health = severityTone(metrics.health.status);
    const threats = metrics.threats.combat ? 'error' : severityTone(metrics.threats.level);
    const needs = worstSeverity([
      metrics.needs.hunger,
      metrics.needs.thirst,
      metrics.needs.sleep,
    ]);
    return [
      {
        key: 'health',
        severity: (health === 'error' ? 'critico' : health === 'warning' ? 'aviso' : 'informativo') as Severity,
        node: <HealthGroup metrics={metrics} />,
      },
      {
        key: 'threats',
        severity: (threats === 'error' ? 'critico' : threats === 'warning' ? 'aviso' : 'informativo') as Severity,
        node: <ThreatsGroup metrics={metrics} />,
      },
      { key: 'needs', severity: needs, node: <NeedsGroup metrics={metrics} /> },
      {
        key: 'objective_progress',
        severity: 'atencao' as Severity,
        node: <ObjectiveProgressGroup metrics={metrics} progressSamples={progressSeries.length} />,
      },
      { key: 'location', severity: 'atencao' as Severity, node: <LocationGroup metrics={metrics} /> },
      { key: 'inventory', severity: 'informativo' as Severity, node: <InventoryGroup metrics={metrics} /> },
      {
        key: 'combat_history',
        severity: 'informativo' as Severity,
        node: <CombatHistoryGroup metrics={metrics} />,
      },
      {
        key: 'agent',
        severity: 'informativo' as Severity,
        node: (
          <AgentMetricsGroup
            metrics={metrics}
            series={{
              decisions: decisionsSeries,
              confidence: confidenceSeries,
              latency: latencySeries,
              cost: costSeries,
            }}
          />
        ),
      },
    ].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
    // As séries mudam a cada amostra; os grupos são reconstruídos com elas.
  }, [metrics, decisionsSeries, confidenceSeries, latencySeries, costSeries, progressSeries.length]);

  return (
    <PanelShell
      title="Métricas vivas"
      description="Os 8 grupos do contrato, atualizados por WebSocket (metrics_update) com recurso periódico."
      actions={
        <FreshnessIndicator
          ts={metrics?.ts}
          now={now}
          showClock
          refreshing={metricsQuery.isFetching}
          refreshDisabled={metricsQuery.isFetching}
          onRefresh={() => void metricsQuery.refetch()}
          refreshLabel="Atualizar métricas agora"
        />
      }
    >
      {metricsQuery.isLoading ? <PanelLoading label="A carregar métricas…" /> : null}

      {metricsQuery.isError ? (
        <PanelError
          message={errorMessage(metricsQuery.error)}
          onRetry={() => void metricsQuery.refetch()}
        />
      ) : null}

      {metrics ? (
        <Stack spacing={2}>
          {metrics.threats.combat ? (
            <Alert severity="error" role="alert">
              Combate em curso — {valuePt(metrics.threats.level)} ·{' '}
              {metrics.threats.hostiles.map((hostile) => valuePt(hostile)).join(', ') || 'hostis por identificar'}
            </Alert>
          ) : null}

          {/* Acima da dobra: os 5 sinais que respondem a «está tudo bem?». */}
          <VitalSignsStrip metrics={metrics} />

          <Stack spacing={1}>
            <Stack
              direction="row"
              spacing={1}
              sx={{ alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap' }}
            >
              <Typography variant="h4" component="h2">
                Detalhe dos 8 grupos do contrato
              </Typography>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                por severidade · mostra mais em cada grupo · {formatRelative(metrics.ts, now)}
              </Typography>
            </Stack>

            <Grid container spacing={1.5} sx={{ width: '100%' }}>
              {groups.map((group) => (
                <Grid key={group.key} size={{ xs: 12, lg: group.key === 'agent' ? 12 : 4 }}>
                  {group.node}
                </Grid>
              ))}
            </Grid>
          </Stack>

          <PanelNotice severity="info">
            Chaves de grupos vindas de <code>docs/api.md</code>. Campos que o contrato deixa em
            aberto (<code>wounds</code>, <code>combat_history</code>) são mostrados de forma
            defensiva: o que não existir é ignorado.
          </PanelNotice>
        </Stack>
      ) : null}
    </PanelShell>
  );
}
