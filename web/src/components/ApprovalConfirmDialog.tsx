import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import Block from '@mui/icons-material/Block';
import TaskAlt from '@mui/icons-material/TaskAlt';

import { formatDateTime } from '../api/format';
import { isDecisionBand, type Approval } from '../api/types';
import { BandChip } from './Chips';
import { MONO_FONT } from '../theme';

export interface ApprovalConfirmDialogProps {
  /** Aprovação em confirmação; `null` fecha o diálogo. */
  approval: Approval | null;
  /** `true` = aprovar (liberta a decisão); `false` = rejeitar (descarta a ação). */
  approve: boolean;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * Confirmação de decisão de uma aprovação (mesma fricção da resposta a uma
 * pergunta — `AnswerConfirmDialog`): aprovar/rejeitar **é responder** à pergunta
 * de aprovação, a resposta entra na memória do agente (EN) e não há caminho de
 * edição, por isso nenhuma decisão sai com um clique acidental.
 */
export default function ApprovalConfirmDialog({
  approval,
  approve,
  busy = false,
  onCancel,
  onConfirm,
}: ApprovalConfirmDialogProps) {
  const titleId = 'confirmar-aprovacao-titulo';
  const textId = 'confirmar-aprovacao-texto';
  const open = approval !== null;
  const band = approval && isDecisionBand(approval.band) ? approval.band : null;

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" aria-labelledby={titleId} aria-describedby={textId}>
      <DialogTitle id={titleId}>
        {approve ? 'Aprovar esta ação?' : 'Rejeitar esta ação?'}
      </DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography variant="body2" id={textId}>
            {approve
              ? 'A aprovação responde «sim» à pergunta de aprovação e é escrita na memória do agente (traduzida PT→EN). O agente lê-a na próxima recuperação de memória e volta a decidir.'
              : 'A rejeição responde «não» à pergunta de aprovação: a ação retida é descartada e a recusa fica registada na memória do agente. Não há caminho de edição.'}
          </Typography>

          <Stack spacing={0.5}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Ação retida ({approval?.id ?? '—'})
            </Typography>
            <Stack
              direction="row"
              spacing={1}
              useFlexGap
              sx={{ alignItems: 'center', flexWrap: 'wrap' }}
            >
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {approval?.action_name_en ?? '—'}
              </Typography>
              {band ? <BandChip band={band} /> : null}
            </Stack>
            <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
              {approval?.action_id ?? '—'} · {formatDateTime(approval?.ts)}
            </Typography>
          </Stack>

          {approval?.question ? (
            <Stack spacing={0.5}>
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Pergunta de aprovação
              </Typography>
              <Typography variant="body2">{approval.question}</Typography>
            </Stack>
          ) : null}

          <Alert severity={approve ? 'info' : 'warning'} variant="outlined">
            {approve
              ? 'O contrato não volta a despachar a ação retida: o agente decide de novo ao ler a resposta.'
              : 'Rejeitar descarta a ação — o agente terá de escolher outra.'}
          </Alert>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button sx={{ minHeight: 40 }} onClick={onCancel}>
          Cancelar
        </Button>
        <Button
          variant="contained"
          color={approve ? 'primary' : 'error'}
          autoFocus
          loading={busy}
          loadingPosition="start"
          startIcon={approve ? <TaskAlt /> : <Block />}
          sx={{ minHeight: 40 }}
          onClick={onConfirm}
        >
          {approve ? 'Confirmar e aprovar' : 'Confirmar e rejeitar'}
        </Button>
      </DialogActions>
    </Dialog>
  );
}
