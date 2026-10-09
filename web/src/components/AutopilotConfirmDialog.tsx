import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import PlayArrow from '@mui/icons-material/PlayArrow';

import type { ControlMode } from '../api/types';

/** Porque é que o autopilot está a ser pedido (o diálogo explica o caso concreto). */
export type AutopilotReason = 'resume' | 'disarm' | 'switch';

export interface AutopilotConfirmDialogProps {
  open: boolean;
  /** Modo de onde se está a sair (para o texto). */
  from: ControlMode;
  /** Autopilot é o modo guardado a restaurar, ou uma escolha nova. */
  reason: AutopilotReason;
  /** `true` enquanto o `POST /control` está em curso. */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

function bodyText(reason: AutopilotReason, from: ControlMode): string {
  switch (reason) {
    case 'resume':
      return 'Estavas em pausa e o último modo de autonomia guardado nesta campanha é Autopiloto. Retomar devolve o agente ao autopiloto: ele volta a agir sozinho sobre o jogo, sem confirmação humana decisão a decisão.';
    case 'disarm':
      return 'Estavas em pausa e o último modo de autonomia guardado nesta campanha é Autopiloto. Desligar o kill-switch devolve o agente ao autopiloto: ele volta a agir sozinho sobre o jogo, sem confirmação humana decisão a decisão.';
    case 'switch':
    default:
      return `Vais passar de ${from === 'paused' ? '«Em pausa»' : '«Checkpoints»'} para Autopiloto: o agente decide e age sozinho, sem parar para confirmação. Podes voltar a Checkpoints ou pausar a qualquer momento.`;
  }
}

function titleText(reason: AutopilotReason): string {
  switch (reason) {
    case 'resume':
      return 'Retomar em autopiloto?';
    case 'disarm':
      return 'Desligar o kill-switch e voltar ao autopiloto?';
    case 'switch':
    default:
      return 'Passar para autopiloto?';
  }
}

/**
 * Confirmação de qualquer transição PARA autopilot (auditoria C.1.4.01).
 * O kill-switch usa `alertdialog`; esta é uma escolha normal de modo, logo `Dialog`.
 */
export default function AutopilotConfirmDialog({
  open,
  from,
  reason,
  busy = false,
  onCancel,
  onConfirm,
}: AutopilotConfirmDialogProps) {
  const titleId = 'autopilot-titulo';
  const textId = 'autopilot-texto';

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="xs" aria-labelledby={titleId} aria-describedby={textId}>
      <DialogTitle id={titleId}>{titleText(reason)}</DialogTitle>
      <DialogContent>
        <DialogContentText id={textId}>{bodyText(reason, from)}</DialogContentText>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancelar</Button>
        <Button
          variant="contained"
          color="warning"
          autoFocus
          loading={busy}
          loadingPosition="start"
          startIcon={<PlayArrow />}
          onClick={onConfirm}
        >
          Sim, ativar autopiloto
        </Button>
      </DialogActions>
    </Dialog>
  );
}
