import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Refresh from '@mui/icons-material/Refresh';

import { formatRelative, formatTime } from '../api/format';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

export interface FreshnessIndicatorProps {
  /** `ts` do payload recebido (fonte canónica da frescura). */
  ts?: string | null;
  /** Relógio partilhado; sem ele o indicador cria o seu (1 s). */
  now?: number;
  /** Mostra a hora exata além do tempo relativo. */
  showClock?: boolean;
  /** Ação de leitura manual. */
  onRefresh?: () => void;
  refreshLabel?: string;
  refreshing?: boolean;
  refreshDisabled?: boolean;
  /** `icon` para cabeçalhos densos, `button` para painéis. */
  variant?: 'icon' | 'button';
}

/**
 * Indicador de frescura reutilizável (auditoria C.1.1.01): métricas, ecrã e gastos
 * mostravam «há X s» com atualização manual, enquanto perguntas, decisões e memória
 * não diziam quando tinham sido lidos. Um só componente, o mesmo texto em todo o lado.
 */
export default function FreshnessIndicator({
  ts,
  now,
  showClock = false,
  onRefresh,
  refreshLabel = 'Atualizar agora',
  refreshing = false,
  refreshDisabled = false,
  variant = 'icon',
}: FreshnessIndicatorProps) {
  // Hook sempre chamado: `now` externo só substitui o relógio local depois do render.
  const localNow = useNow(1000);
  const clock = now ?? localNow;
  const pending = !ts;

  return (
    <Box
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.5,
        flexWrap: 'nowrap',
        color: 'text.secondary',
      }}
    >
      <Typography
        variant="caption"
        component="span"
        sx={{ fontFamily: MONO_FONT, whiteSpace: 'nowrap' }}
      >
        {pending ? (
          'à espera da primeira leitura'
        ) : (
          <>
            última atualização {formatRelative(ts, clock)}
            {showClock ? <> · {formatTime(ts)}</> : null}
          </>
        )}
      </Typography>
      {onRefresh ? (
        variant === 'button' ? (
          <Button
            size="small"
            startIcon={<Refresh />}
            onClick={onRefresh}
            loading={refreshing}
            loadingPosition="start"
            disabled={refreshDisabled}
          >
            {refreshLabel}
          </Button>
        ) : (
          <Tooltip title={refreshLabel}>
            <span>
              <IconButton
                size="small"
                aria-label={refreshLabel}
                onClick={onRefresh}
                disabled={refreshDisabled}
              >
                <Refresh fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        )
      ) : null}
    </Box>
  );
}
