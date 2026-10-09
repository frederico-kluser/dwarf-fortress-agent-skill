import Alert from '@mui/material/Alert';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import IconButton from '@mui/material/IconButton';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Backpack from '@mui/icons-material/Backpack';
import Bolt from '@mui/icons-material/Bolt';
import HealthAndSafety from '@mui/icons-material/HealthAndSafety';
import Payments from '@mui/icons-material/Payments';
import Percent from '@mui/icons-material/Percent';
import Place from '@mui/icons-material/Place';
import Refresh from '@mui/icons-material/Refresh';
import Restaurant from '@mui/icons-material/Restaurant';
import Shield from '@mui/icons-material/Shield';
import Timeline from '@mui/icons-material/Timeline';
import Timer from '@mui/icons-material/Timer';
import TrackChanges from '@mui/icons-material/TrackChanges';
import Api from '@mui/icons-material/Api';

import { errorMessage } from '../api/events';
import {
  asText,
  formatConfidence,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatRelative,
  formatTime,
  formatUsd,
  severityTone,
  valuePt,
} from '../api/format';
import { useMetrics } from '../api/queries';
import type { CombatEntry, Metrics, WoundEntry } from '../api/types';
import InfoCard from '../components/InfoCard';
import MetricCard from '../components/MetricCard';
import PanelShell from '../components/PanelShell';
import { PanelEmpty, PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import { ValueChip } from '../components/Chips';
import { useNow, useSparkSeries } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

function woundLabel(wound: WoundEntry, index: number): string {
  const part = typeof wound.part === 'string' ? valuePt(wound.part) : `ferimento ${index + 1}`;
  const severity = typeof wound.severity === 'string' ? ` (${valuePt(wound.severity)})` : '';
  const ts = typeof wound.ts === 'string' ? ` · ${formatDateTime(wound.ts)}` : '';
  return `${part}${severity}${ts}`;
}

function combatLabel(entry: CombatEntry): string {
  const opponent = typeof entry.opponent === 'string' ? valuePt(entry.opponent) : 'oponente desconhecido';
  const outcome = typeof entry.outcome === 'string' ? valuePt(entry.outcome) : '—';
  const damage = typeof entry.damage === 'string' ? ` · ${entry.damage}` : '';
  return `${formatDateTime(entry.ts)} · ${opponent} — ${outcome}${damage}`;
}

function NeedsRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack
      direction="row"
      spacing={1}
      sx={{ alignItems: 'center', justifyContent: 'space-between' }}
    >
      <Typography variant="body2" sx={{ color: 'text.secondary' }}>
        {label}
      </Typography>
      <ValueChip value={value} tone={severityTone(value)} />
    </Stack>
  );
}

/** Painel de métricas vivas — exatamente os 8 grupos do contrato. */
export default function MetricsPanel() {
  const metricsQuery = useMetrics();
  const metrics: Metrics | undefined = metricsQuery.data;
  const now = useNow(1000);

  const decisionsSeries = useSparkSeries(metrics?.ts, metrics?.agent.decisions_per_min);
  const confidenceSeries = useSparkSeries(metrics?.ts, metrics?.agent.confidence_avg);
  const latencySeries = useSparkSeries(metrics?.ts, metrics?.agent.latency_ms);
  const costSeries = useSparkSeries(metrics?.ts, metrics?.agent.cost_usd);
  const progressSeries = useSparkSeries(metrics?.ts, metrics?.objective_progress.percent);

  const confidence = formatConfidence(metrics?.agent.confidence_avg);

  return (
    <PanelShell
      title="Métricas vivas"
      description="Os 8 grupos do contrato, atualizados por WebSocket (metrics_update) com recurso periódico."
      actions={
        <>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
            última leitura {formatTime(metrics?.ts)} · {formatRelative(metrics?.ts, now)}
          </Typography>
          <Tooltip title="Atualizar agora">
            <span>
              <IconButton
                aria-label="Atualizar métricas agora"
                onClick={() => void metricsQuery.refetch()}
                disabled={metricsQuery.isFetching}
              >
                <Refresh />
              </IconButton>
            </span>
          </Tooltip>
        </>
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

          <Grid container spacing={2} sx={{ width: '100%' }}>
            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Saúde e ferimentos"
                contractKey="health"
                icon={<HealthAndSafety fontSize="small" />}
                color={metrics.health.status === 'healthy' ? 'success.main' : 'warning.main'}
                action={<ValueChip value={metrics.health.status} tone={severityTone(metrics.health.status)} />}
              >
                {metrics.health.wounds.length === 0 ? (
                  <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                    Sem ferimentos registados.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {metrics.health.wounds.map((wound, index) => (
                      <ListItem key={`${wound.part ?? 'w'}-${index}`} disableGutters>
                        <ListItemText
                          primary={woundLabel(wound, index)}
                          slotProps={{ primary: { variant: 'body2' } }}
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Fome, sede e sono"
                contractKey="needs"
                icon={<Restaurant fontSize="small" />}
                color="warning.main"
              >
                <Stack spacing={1}>
                  <NeedsRow label="Fome" value={metrics.needs.hunger} />
                  <NeedsRow label="Sede" value={metrics.needs.thirst} />
                  <NeedsRow label="Sono" value={metrics.needs.sleep} />
                </Stack>
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Inventário e peso"
                contractKey="inventory"
                icon={<Backpack fontSize="small" />}
                color="info.main"
                action={
                  <Chip
                    size="small"
                    variant="outlined"
                    label={`${formatNumber(metrics.inventory.weight_kg, 1)} kg`}
                  />
                }
              >
                {metrics.inventory.items.length === 0 ? (
                  <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                    Inventário vazio.
                  </Typography>
                ) : (
                  <Stack
                    direction="row"
                    spacing={0.5}
                    useFlexGap
                    sx={{ flexWrap: 'wrap' }}
                  >
                    {metrics.inventory.items.map((item) => (
                      <Chip key={asText(item)} size="small" variant="outlined" label={asText(item)} />
                    ))}
                  </Stack>
                )}
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Ameaças e combate"
                contractKey="threats"
                icon={<Shield fontSize="small" />}
                color={metrics.threats.combat ? 'error.main' : 'success.main'}
                action={<ValueChip value={metrics.threats.level} tone={severityTone(metrics.threats.level)} />}
              >
                <Stack spacing={1}>
                  <Stack
                    direction="row"
                    spacing={0.5}
                    useFlexGap
                    sx={{ flexWrap: 'wrap' }}
                  >
                    <Chip
                      size="small"
                      variant={metrics.threats.combat ? 'filled' : 'outlined'}
                      color={metrics.threats.combat ? 'error' : 'default'}
                      label={metrics.threats.combat ? 'em combate' : 'sem combate'}
                    />
                    {metrics.threats.hostiles.map((hostile) => (
                      <Chip key={hostile} size="small" variant="outlined" color="error" label={valuePt(hostile)} />
                    ))}
                  </Stack>
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    {metrics.threats.hostiles.length} hostis avistados
                  </Typography>
                </Stack>
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Localização e trajeto"
                contractKey="location"
                icon={<Place fontSize="small" />}
                color="primary.main"
                action={<Chip size="small" variant="outlined" label={metrics.location.region} />}
              >
                <Stack
                  direction="row"
                  spacing={0.5}
                  useFlexGap
                  sx={{ flexWrap: 'wrap', alignItems: 'center' }}
                >
                  {metrics.location.path.length === 0 ? (
                    <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                      Trajeto ainda sem pontos.
                    </Typography>
                  ) : (
                    metrics.location.path.map((step, index) => (
                      <Stack
                        key={`${step}-${index}`}
                        direction="row"
                        spacing={0.5}
                        sx={{ alignItems: 'center' }}
                      >
                        {index > 0 ? (
                          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                            ›
                          </Typography>
                        ) : null}
                        <Chip size="small" variant="outlined" label={step} />
                      </Stack>
                    ))
                  )}
                </Stack>
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12, md: 6, lg: 4 }}>
              <InfoCard
                title="Progresso do objetivo"
                contractKey="objective_progress"
                icon={<TrackChanges fontSize="small" />}
                color="success.main"
                action={
                  <Chip size="small" variant="outlined" label={formatPercent(metrics.objective_progress.percent)} />
                }
              >
                <Stack spacing={1}>
                  <LinearProgress
                    variant="determinate"
                    value={Math.round(metrics.objective_progress.percent * 100)}
                    aria-label="Progresso do objetivo"
                    color="success"
                  />
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    sub-objetivo ativo:{' '}
                    {metrics.objective_progress.active_subgoal_id ?? 'nenhum'}
                  </Typography>
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    amostras de progresso: {progressSeries.length}
                  </Typography>
                </Stack>
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12 }}>
              <InfoCard
                title="Histórico de combate"
                contractKey="combat_history"
                icon={<Timeline fontSize="small" />}
                color="error.main"
                action={
                  <Chip size="small" variant="outlined" label={`${metrics.combat_history.length} entradas`} />
                }
              >
                {metrics.combat_history.length === 0 ? (
                  <PanelEmpty message="Sem combates registados." />
                ) : (
                  <List dense disablePadding>
                    {metrics.combat_history.map((entry, index) => (
                      <ListItem key={`${entry.ts ?? 'c'}-${index}`} divider={index < metrics.combat_history.length - 1}>
                        <ListItemText
                          primary={combatLabel(entry)}
                          slotProps={{
                            primary: { variant: 'body2', sx: { fontFamily: MONO_FONT } },
                          }}
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </InfoCard>
            </Grid>

            <Grid size={{ xs: 12 }}>
              <InfoCard
                title="Métricas do agente"
                contractKey="agent"
                icon={<Bolt fontSize="small" />}
                color="primary.main"
                action={
                  <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                    {formatNumber(metrics.agent.calls)} chamadas acumuladas
                  </Typography>
                }
              >
                <Grid container spacing={2} sx={{ width: '100%' }}>
                  <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
                    <MetricCard
                      title="Decisões por minuto"
                      value={formatNumber(metrics.agent.decisions_per_min, 1)}
                      unit="dec/min"
                      icon={<Bolt fontSize="small" />}
                      series={decisionsSeries}
                      color="primary.main"
                    />
                  </Grid>
                  <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
                    <MetricCard
                      title="Confiança média"
                      value={confidence ?? 'indisponível'}
                      hint={
                        confidence
                          ? 'média das decisões com confidence válida'
                          : 'confidence ausente ou 0 = indisponível, nunca «certo»'
                      }
                      icon={<Percent fontSize="small" />}
                      series={confidenceSeries}
                      color="success.main"
                      unavailable={confidence === null}
                    />
                  </Grid>
                  <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
                    <MetricCard
                      title="Chamadas JEV"
                      value={formatNumber(metrics.agent.calls)}
                      unit="chamadas"
                      icon={<Api fontSize="small" />}
                      color="info.main"
                    />
                  </Grid>
                  <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
                    <MetricCard
                      title="Custo acumulado"
                      value={formatUsd(metrics.agent.cost_usd, { compact: true })}
                      hint="sem teto de orçamento — só visibilidade"
                      icon={<Payments fontSize="small" />}
                      series={costSeries}
                      color="warning.main"
                    />
                  </Grid>
                  <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
                    <MetricCard
                      title="Latência média"
                      value={formatNumber(metrics.agent.latency_ms)}
                      unit="ms"
                      icon={<Timer fontSize="small" />}
                      series={latencySeries}
                      color="secondary.main"
                    />
                  </Grid>
                </Grid>
              </InfoCard>
            </Grid>
          </Grid>

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
