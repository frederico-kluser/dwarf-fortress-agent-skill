import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';

import { BAND_LABEL, JEV_ROLE_LABEL, QUESTION_STATUS_LABEL, valuePt } from '../api/format';
import type { ControlMode, DecisionBand, JevAgent, QuestionStatus } from '../api/types';

const BAND_COLOR: Record<DecisionBand, 'success' | 'warning' | 'error'> = {
  auto: 'success',
  hitl: 'warning',
  abstain: 'error',
};

/**
 * Banda de ação do JEV. A cor nunca é o único sinal: a etiqueta diz o que é
 * (WCAG 2.2 — não depender só da cor).
 */
export function BandChip({ band, size = 'small' }: { band: DecisionBand; size?: 'small' | 'medium' }) {
  return (
    <Chip
      size={size}
      variant="outlined"
      color={BAND_COLOR[band] ?? 'default'}
      label={BAND_LABEL[band] ?? band}
    />
  );
}

/** Papel JEV (R/A/M/D) com legenda PT no tooltip. */
export function AgentChip({ agent }: { agent: JevAgent }) {
  const color =
    agent === 'JEV-A' ? 'primary' : agent === 'JEV-R' ? 'info' : agent === 'JEV-M' ? 'secondary' : 'warning';
  return (
    <Tooltip title={JEV_ROLE_LABEL[agent] ?? agent}>
      <Chip size="small" variant="outlined" color={color} label={agent.replace('JEV-', '')} />
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

/** Estado de uma pergunta da fila. */
export function QuestionStatusChip({ status }: { status: QuestionStatus }) {
  return (
    <Chip
      size="small"
      variant="outlined"
      color={status === 'pending' ? 'warning' : 'success'}
      label={QUESTION_STATUS_LABEL[status] ?? status}
    />
  );
}

/**
 * Valor enumerado do contrato (EN) mostrado em PT.
 * `tone` mapeia o valor para uma cor sem depender dela para o significado.
 */
export function ValueChip({
  value,
  tone = 'default',
  label,
}: {
  value: unknown;
  tone?: 'default' | 'success' | 'warning' | 'error' | 'info' | 'primary';
  label?: string;
}) {
  return <Chip size="small" variant="outlined" color={tone} label={label ?? valuePt(value)} />;
}
