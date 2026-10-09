import { useState } from 'react';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogContentText from '@mui/material/DialogContentText';
import DialogTitle from '@mui/material/DialogTitle';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import PowerSettingsNew from '@mui/icons-material/PowerSettingsNew';

export interface KillSwitchButtonProps {
  /** `true` quando o kill-switch está acionado. */
  active: boolean;
  /** Confirmação do utilizador (já com o novo valor pretendido). */
  onConfirm: (next: boolean) => void;
  busy?: boolean;
  /** `icon` (AppBar) ou `button` (painel de controlo). */
  variant?: 'icon' | 'button';
}

/**
 * Kill-switch com diálogo de confirmação.
 * Sem `disableEscapeKeyDown` (removido na v9): Esc fecha o diálogo, como deve.
 */
export default function KillSwitchButton({
  active,
  onConfirm,
  busy = false,
  variant = 'icon',
}: KillSwitchButtonProps) {
  const [open, setOpen] = useState(false);
  const dialogTitleId = 'kill-switch-titulo';
  const dialogTextId = 'kill-switch-texto';

  const label = active ? 'Reativar o agente (desligar kill-switch)' : 'Acionar kill-switch';

  return (
    <>
      {variant === 'icon' ? (
        <Tooltip title={label}>
          <IconButton
            aria-label={label}
            color={active ? 'error' : 'default'}
            onClick={() => setOpen(true)}
            disabled={busy}
          >
            <PowerSettingsNew />
          </IconButton>
        </Tooltip>
      ) : (
        <Button
          variant={active ? 'outlined' : 'contained'}
          color="error"
          startIcon={<PowerSettingsNew />}
          onClick={() => setOpen(true)}
          loading={busy}
          loadingPosition="start"
        >
          {label}
        </Button>
      )}

      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        maxWidth="xs"
        aria-labelledby={dialogTitleId}
        aria-describedby={dialogTextId}
        role="alertdialog"
      >
        <DialogTitle id={dialogTitleId}>
          {active ? 'Desligar o kill-switch?' : 'Acionar o kill-switch?'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText id={dialogTextId}>
            {active
              ? 'O agente volta a poder agir assim que o modo de autonomia o permitir.'
              : 'O agente para imediatamente de agir sobre o jogo. A memória e as métricas ficam intactas e podes desligar o kill-switch a qualquer momento.'}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancelar</Button>
          <Button
            color="error"
            variant="contained"
            autoFocus
            loading={busy}
            loadingPosition="start"
            startIcon={<PowerSettingsNew />}
            onClick={() => {
              onConfirm(!active);
              setOpen(false);
            }}
          >
            {active ? 'Desligar kill-switch' : 'Acionar agora'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
