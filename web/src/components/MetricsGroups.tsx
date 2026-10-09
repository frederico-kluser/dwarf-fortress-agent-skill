import type { ReactNode } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import Grid from '@mui/material/Grid';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';
import Backpack from '@mui/icons-material/Backpack';
import Bolt from '@mui/icons-material/Bolt';
import HealthAndSafety from '@mui/icons-material/HealthAndSafety';
import Payments from '@mui/icons-material/Payments';
import Percent from '@mui/icons-material/Percent';
import Place from '@mui/icons-material/Place';
import Restaurant from '@mui/icons-material/Restaurant';
import Shield from '@mui/icons-material/Shield';
import Timeline from '@mui/icons-material/Timeline';
import Timer from '@mui/icons-material/Timer';
import TrackChanges from '@mui/icons-material/TrackChanges';
import Api from '@mui/icons-material/Api';

import {
  USD_COUNTER_COMPACT_FORMAT,
  USD_PREFIX,
  asText,
  formatConfidence,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatUsd,
  severityTone,
  valuePt,
} from '../api/format';
import type { NumberFormat } from '@/components/motion-ui/animated-number';
import type { CombatEntry, Metrics, WoundEntry } from '../api/types';
import { ValueChip } from './Chips';
import MetricCard from './MetricCard';
import {
  SEVERITY_COLOR,
  SEVERITY_LABEL,
  severityFromTone,
  worstSeverity,
  type Severity,
} from './metricsSeverity';
import { PanelEmpty } from './PanelState';
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

export interface MetricsGroupProps {
  title: string;
  /** Chave do grupo no contrato — usada nos ids de a11y. */
  contractKey: string;
  icon: ReactNode;
  severity: Severity;
  /** Resumo de uma linha no cabeçalho: o que se vê com o grupo fechado. */
  summary: string;
  statusChip?: ReactNode;
  children: ReactNode;
}

/**
 * Grupo de métricas em divulgação progressiva: fechado por omissão, com a
 * severidade e o resumo à vista — o cartão só ocupa espaço quando é aberto, mas o
 * sinal continua legível no cabeçalho.
 */
export function MetricsGroup({
  title,
  contractKey,
  icon,
  severity,
  summary,
  statusChip,
  children,
}: MetricsGroupProps) {
  const regionId = `metrica-${contractKey}-conteudo`;
  return (
    <Accordion variant="outlined" disableGutters sx={{ borderColor: SEVERITY_COLOR[severity] }}>
      <AccordionSummary
        expandIcon={<ExpandMore />}
        id={`metrica-${contractKey}-cabecalho`}
        aria-controls={regionId}
      >
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: 'center', flexGrow: 1, minWidth: 0, flexWrap: 'wrap' }}
        >
          <Box sx={{ display: 'inline-flex', color: SEVERITY_COLOR[severity] }}>{icon}</Box>
          <Typography variant="h5" component="h2">
            {title}
          </Typography>
          <Chip
            size="small"
            variant="outlined"
            color={severity === 'critico' ? 'error' : severity === 'aviso' ? 'warning' : 'default'}
            label={SEVERITY_LABEL[severity]}
          />
          {statusChip}
          <Typography
            variant="caption"
            sx={{
              color: 'text.secondary',
              minWidth: 0,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {summary}
          </Typography>
        </Stack>
      </AccordionSummary>
      <AccordionDetails id={regionId} sx={{ pt: 0 }}>
        {children}
      </AccordionDetails>
    </Accordion>
  );
}

/* ----------------------------------------------- grupos do contrato (8) --- */

export function HealthGroup({ metrics }: { metrics: Metrics }) {
  const severity = severityFromTone(severityTone(metrics.health.status));
  return (
    <MetricsGroup
      title="Saúde e ferimentos"
      contractKey="health"
      icon={<HealthAndSafety fontSize="small" />}
      severity={severity}
      summary={
        metrics.health.wounds.length === 0
          ? 'sem ferimentos'
          : `${metrics.health.wounds.length} ferimento(s)`
      }
      statusChip={<ValueChip value={metrics.health.status} tone={severityTone(metrics.health.status)} />}
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
    </MetricsGroup>
  );
}

export function NeedsGroup({ metrics }: { metrics: Metrics }) {
  const severity = worstSeverity([metrics.needs.hunger, metrics.needs.thirst, metrics.needs.sleep]);
  return (
    <MetricsGroup
      title="Fome, sede e sono"
      contractKey="needs"
      icon={<Restaurant fontSize="small" />}
      severity={severity}
      summary={`fome ${valuePt(metrics.needs.hunger)} · sede ${valuePt(metrics.needs.thirst)} · sono ${valuePt(metrics.needs.sleep)}`}
    >
      <Stack spacing={1}>
        <NeedsRow label="Fome" value={metrics.needs.hunger} />
        <NeedsRow label="Sede" value={metrics.needs.thirst} />
        <NeedsRow label="Sono" value={metrics.needs.sleep} />
      </Stack>
    </MetricsGroup>
  );
}

export function ThreatsGroup({ metrics }: { metrics: Metrics }) {
  const severity = metrics.threats.combat
    ? 'critico'
    : severityFromTone(severityTone(metrics.threats.level));
  return (
    <MetricsGroup
      title="Ameaças e combate"
      contractKey="threats"
      icon={<Shield fontSize="small" />}
      severity={severity}
      summary={`${valuePt(metrics.threats.level)} · ${metrics.threats.hostiles.length} hostis`}
      statusChip={
        <Chip
          size="small"
          variant={metrics.threats.combat ? 'filled' : 'outlined'}
          color={metrics.threats.combat ? 'error' : 'default'}
          label={metrics.threats.combat ? 'em combate' : 'sem combate'}
        />
      }
    >
      {metrics.threats.hostiles.length === 0 ? (
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Sem hostis avistados.
        </Typography>
      ) : (
        <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
          {metrics.threats.hostiles.map((hostile) => (
            <Chip key={hostile} size="small" variant="outlined" color="error" label={valuePt(hostile)} />
          ))}
        </Stack>
      )}
    </MetricsGroup>
  );
}

export function ObjectiveProgressGroup({
  metrics,
  progressSamples,
}: {
  metrics: Metrics;
  progressSamples: number;
}) {
  return (
    <MetricsGroup
      title="Progresso do objetivo"
      contractKey="objective_progress"
      icon={<TrackChanges fontSize="small" />}
      severity="atencao"
      summary={`sub-objetivo ativo: ${metrics.objective_progress.active_subgoal_id ?? 'nenhum'}`}
      statusChip={
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
        <Tooltip title={`${progressSamples} amostras de progresso nesta sessão`}>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            leitura de GET /api/campaigns/{'{id}'}/metrics
          </Typography>
        </Tooltip>
      </Stack>
    </MetricsGroup>
  );
}

export function LocationGroup({ metrics }: { metrics: Metrics }) {
  return (
    <MetricsGroup
      title="Localização e trajeto"
      contractKey="location"
      icon={<Place fontSize="small" />}
      severity="atencao"
      summary={`região: ${metrics.location.region} · ${metrics.location.path.length} pontos no trajeto`}
    >
      <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap', alignItems: 'center' }}>
        {metrics.location.path.length === 0 ? (
          <Typography variant="body2" sx={{ color: 'text.secondary' }}>
            Trajeto ainda sem pontos.
          </Typography>
        ) : (
          metrics.location.path.map((step, index) => (
            <Stack key={`${step}-${index}`} direction="row" spacing={0.5} sx={{ alignItems: 'center' }}>
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
    </MetricsGroup>
  );
}

export function InventoryGroup({ metrics }: { metrics: Metrics }) {
  return (
    <MetricsGroup
      title="Inventário e peso"
      contractKey="inventory"
      icon={<Backpack fontSize="small" />}
      severity="informativo"
      summary={`${metrics.inventory.items.length} item(ns) · ${formatNumber(metrics.inventory.weight_kg, 1)} kg`}
    >
      {metrics.inventory.items.length === 0 ? (
        <Typography variant="body2" sx={{ color: 'text.secondary' }}>
          Inventário vazio.
        </Typography>
      ) : (
        <Stack direction="row" spacing={0.5} useFlexGap sx={{ flexWrap: 'wrap' }}>
          {metrics.inventory.items.map((item) => (
            <Chip key={asText(item)} size="small" variant="outlined" label={asText(item)} />
          ))}
        </Stack>
      )}
    </MetricsGroup>
  );
}

export function CombatHistoryGroup({ metrics }: { metrics: Metrics }) {
  return (
    <MetricsGroup
      title="Histórico de combate"
      contractKey="combat_history"
      icon={<Timeline fontSize="small" />}
      severity="informativo"
      summary={`${metrics.combat_history.length} entrada(s)`}
    >
      {metrics.combat_history.length === 0 ? (
        <PanelEmpty message="Sem combates registados." />
      ) : (
        <List dense disablePadding>
          {metrics.combat_history.map((entry, index) => (
            <ListItem key={`${entry.ts ?? 'c'}-${index}`} divider={index < metrics.combat_history.length - 1}>
              <ListItemText
                primary={combatLabel(entry)}
                slotProps={{ primary: { variant: 'body2', sx: { fontFamily: MONO_FONT } } }}
              />
            </ListItem>
          ))}
        </List>
      )}
    </MetricsGroup>
  );
}

export interface AgentSeries {
  decisions: number[];
  confidence: number[];
  latency: number[];
  cost: number[];
}

export function AgentMetricsGroup({
  metrics,
  series,
}: {
  metrics: Metrics;
  series: AgentSeries;
}) {
  const confidence = formatConfidence(metrics.agent.confidence_avg);
  return (
    <MetricsGroup
      title="Métricas do agente"
      contractKey="agent"
      icon={<Bolt fontSize="small" />}
      severity="informativo"
      summary={`${formatNumber(metrics.agent.calls)} chamadas acumuladas`}
    >
      <Grid container spacing={2} sx={{ width: '100%' }}>
        <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
          <MetricCard
            title="Decisões por minuto"
            value={formatNumber(metrics.agent.decisions_per_min, 1)}
            unit="dec/min"
            icon={<Bolt fontSize="small" />}
            series={series.decisions}
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
            series={series.confidence}
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
            numericValue={metrics.agent.cost_usd}
            numericFormat={USD_COUNTER_COMPACT_FORMAT as NumberFormat}
            numericPrefix={USD_PREFIX}
            hint="sem teto de orçamento — só visibilidade"
            icon={<Payments fontSize="small" />}
            series={series.cost}
            color="warning.main"
          />
        </Grid>
        <Grid size={{ xs: 12, sm: 6, lg: 4, xl: 2 }}>
          <MetricCard
            title="Latência média"
            value={formatNumber(metrics.agent.latency_ms)}
            unit="ms"
            icon={<Timer fontSize="small" />}
            series={series.latency}
            color="secondary.main"
          />
        </Grid>
      </Grid>
    </MetricsGroup>
  );
}
