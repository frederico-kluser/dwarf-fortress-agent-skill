import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';
import ReportProblemOutlined from '@mui/icons-material/ReportProblemOutlined';
import HelpOutlined from '@mui/icons-material/HelpOutlined';
import HourglassEmpty from '@mui/icons-material/HourglassEmpty';
import Replay from '@mui/icons-material/Replay';
import TaskAlt from '@mui/icons-material/TaskAlt';

import {
  DECISION_STATUS_DESCRIPTION,
  DECISION_STATUS_LABEL,
  JEV_ROLE_LABEL,
  QUESTION_STATUS_LABEL,
  formatDateTime,
  valuePt,
} from '../api/format';
import type {
  ControlMode,
  DecisionBand,
  DecisionStatus,
  JevAgent,
  QuestionStatus,
} from '../api/types';
import { tintedChipSx, type BadgeTone } from './chipSkin';
import { BandBadge } from './rpg/BandBadge';

/**
 * Banda de ação do JEV — badge partilhado `rpg/BandBadge` (§5.10): glifo runa +
 * etiqueta PT + tooltip com os limiares (auto ≥ 0,90 · hitl 0,50–0,89 ·
 * abstain < 0,50); a cor nunca é o único sinal (WCAG 2.2).
 */
export function BandChip({ band }: { band: DecisionBand }) {
  return <BandBadge band={band} />;
}

/**
 * Desfecho da decisão (`Decision.status`, aditivo) — executada | virou pergunta
 * | erro. Quando o contrato omite o estado (decisão auxiliar), desenha-se
 * «desconhecido» em vez de fingir um valor (auditoria v2, achado 2).
 */
export function DecisionStatusChip({
  status,
  size = 'small',
}: {
  status: DecisionStatus | null | undefined;
  size?: 'small' | 'medium';
}) {
  if (!status) {
    return (
      <Tooltip title="O contrato não traz desfecho para decisões auxiliares (JEV-R/M/D) — desconhecido, não «executada».">
        <Chip
          size={size}
          variant="outlined"
          icon={<HelpOutlined />}
          sx={tintedChipSx('default')}
          label="desconhecido"
        />
      </Tooltip>
    );
  }
  const tone: BadgeTone = status === 'executed' ? 'success' : status === 'question' ? 'warning' : 'error';
  const icon = status === 'executed' ? <TaskAlt /> : status === 'question' ? <HourglassEmpty /> : <ReportProblemOutlined />;
  return (
    <Tooltip title={DECISION_STATUS_DESCRIPTION[status] ?? status}>
      <Chip
        size={size}
        variant="outlined"
        icon={icon}
        sx={tintedChipSx(tone)}
        label={DECISION_STATUS_LABEL[status] ?? status}
      />
    </Tooltip>
  );
}

/**
 * Dedupe anti-flood do contrato: quantas vezes o loop voltou a pedir a MESMA
 * ação. A cor não é o único sinal; o tooltip traz o último re-pedido.
 */
export function AttemptsChip({
  attempts,
  lastSeenTs,
  size = 'small',
}: {
  attempts?: number | null;
  lastSeenTs?: string | null;
  size?: 'small' | 'medium';
}) {
  const count = typeof attempts === 'number' && attempts > 1 ? attempts : null;
  if (!count) return null;
  return (
    <Tooltip
      title={`O agente pediu esta mesma ação ${count} vezes (dedupe: aparece como um só pedido)${
        lastSeenTs ? `; último re-pedido: ${formatDateTime(lastSeenTs)}` : ''
      }`}
    >
      <Chip
        size={size}
        variant="outlined"
        icon={<Replay />}
        sx={tintedChipSx('warning')}
        label={`pedido ${count} vezes`}
      />
    </Tooltip>
  );
}

/**
 * Papel JEV (R/A/M/D) com legenda PT no tooltip. O papel NÃO é um estado: fica
 * neutro (§2.3 regra 3 — `warning`/`danger`/`success` nunca decorativos) e a
 * letra do contrato identifica-o.
 */
export function AgentChip({ agent }: { agent: JevAgent }) {
  return (
    <Tooltip title={JEV_ROLE_LABEL[agent] ?? agent}>
      <Chip
        size="small"
        variant="outlined"
        sx={{ ...tintedChipSx('default'), fontFamily: 'inherit', fontWeight: 600 }}
        label={agent.replace('JEV-', '')}
      />
    </Tooltip>
  );
}

const MODE_COLOR: Record<ControlMode, 'success' | 'info' | 'warning'> = {
  autopilot: 'success',
  checkpoint: 'info',
  paused: 'warning',
};

const MODE_LABEL: Record<ControlMode, string> = {
  autopilot: 'Autopiloto',
  checkpoint: 'Checkpoints',
  paused: 'Em pausa',
};

/** Forma curta para ecrãs estreitos — a mesma informação, sem truncar. */
const MODE_COMPACT_LABEL: Record<ControlMode, string> = {
  autopilot: 'Autop.',
  checkpoint: 'Checkpt.',
  paused: 'Pausa',
};

/**
 * Indicador global do modo de autonomia (também no AppBar).
 * `compact` mantém o chip legível em ecrãs estreitos (auditoria C.1.1.01: o chip
 * desaparecia abaixo de `sm`, deixando o kill-switch visível sem o modo).
 */
export function ControlModeChip({
  mode,
  blocked = false,
  compact = false,
  pendingQuestions = 0,
}: {
  mode: ControlMode;
  blocked?: boolean;
  compact?: boolean;
  pendingQuestions?: number;
}) {
  if (blocked) {
    return (
      <Chip
        size="small"
        color="error"
        label={compact ? 'Kill-switch' : 'Kill-switch ativo'}
        title={compact ? 'Kill-switch ativo' : undefined}
      />
    );
  }
  return (
    <Chip
      size="small"
      variant="outlined"
      color={MODE_COLOR[mode]}
      label={compact ? MODE_COMPACT_LABEL[mode] : MODE_LABEL[mode]}
      title={
        compact && pendingQuestions > 0
          ? `${MODE_LABEL[mode]} · ${pendingQuestions} perguntas à espera de resposta`
          : undefined
      }
    />
  );
}

/** Estado de uma pergunta da fila. Glifo + etiqueta: cor nunca sozinha (§2.3). */
export function QuestionStatusChip({ status }: { status: QuestionStatus }) {
  const pending = status === 'pending';
  return (
    <Chip
      size="small"
      variant="outlined"
      icon={pending ? <HourglassEmpty /> : <TaskAlt />}
      sx={tintedChipSx(pending ? 'warning' : 'success')}
      label={QUESTION_STATUS_LABEL[status] ?? status}
    />
  );
}

/**
 * Valor enumerado do contrato (EN) mostrado em PT.
 * `tone` mapeia o valor para uma cor sem depender dela para o significado —
 * a etiqueta PT é sempre visível (§2.3 regra 1).
 */
export function ValueChip({
  value,
  tone = 'default',
  label,
}: {
  value: unknown;
  tone?: BadgeTone;
  label?: string;
}) {
  return (
    <Chip size="small" variant="outlined" sx={tintedChipSx(tone)} label={label ?? valuePt(value)} />
  );
}
