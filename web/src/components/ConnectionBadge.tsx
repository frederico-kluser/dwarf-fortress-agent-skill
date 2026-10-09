import Chip from '@mui/material/Chip';
import Tooltip from '@mui/material/Tooltip';
import Circle from '@mui/icons-material/Circle';

import type { ConnectionStatus } from '../api/client';

const LABEL: Record<ConnectionStatus, string> = {
  idle: 'sem canal',
  connecting: 'a ligar',
  open: 'em direto',
  reconnecting: 'a reconectar',
  closed: 'desligado',
};

const COLOR: Record<ConnectionStatus, 'default' | 'success' | 'warning' | 'error'> = {
  idle: 'default',
  connecting: 'warning',
  open: 'success',
  reconnecting: 'warning',
  closed: 'error',
};

/** Estado do canal em tempo real (WS) + aviso de que os dados são simulados. */
export default function ConnectionBadge({
  connection,
  isMock,
}: {
  connection: ConnectionStatus;
  isMock: boolean;
}) {
  return (
    <Tooltip
      title={
        isMock
          ? 'Sem VITE_API_BASE: a UI está a correr com dados simulados (mock).'
          : 'Canal WebSocket com o backend FastAPI.'
      }
    >
      <Chip
        size="small"
        variant="outlined"
        color={isMock ? 'info' : COLOR[connection]}
        label={isMock ? `mock · ${LABEL[connection]}` : LABEL[connection]}
        icon={
          <Circle
            aria-hidden="true"
            sx={{
              fontSize: 9,
              ml: 1,
              opacity: connection === 'open' || isMock ? 1 : 0.45,
            }}
          />
        }
      />
    </Tooltip>
  );
}
