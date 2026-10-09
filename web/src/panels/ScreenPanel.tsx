import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import Paper from '@mui/material/Paper';
import Snackbar from '@mui/material/Snackbar';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ContentCopy from '@mui/icons-material/ContentCopy';
import Pause from '@mui/icons-material/Pause';
import PlayArrow from '@mui/icons-material/PlayArrow';
import Refresh from '@mui/icons-material/Refresh';

import { errorMessage } from '../api/events';
import { formatRelative, formatTime } from '../api/format';
import { useScreen } from '../api/queries';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import SectionCard from '../components/SectionCard';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

/** Ecrã do jogo traduzido ao vivo — só PT (o original nunca é mostrado). */
export default function ScreenPanel() {
  const screenQuery = useScreen();
  const now = useNow(1000);
  const [frozen, setFrozen] = useState(false);
  const [frozenText, setFrozenText] = useState('');
  const [toast, setToast] = useState<string | null>(null);

  const screen = screenQuery.data;
  const text = frozen ? frozenText : (screen?.translated_pt ?? '');

  const toggleFreeze = () => {
    if (frozen) {
      setFrozen(false);
      return;
    }
    setFrozenText(screen?.translated_pt ?? '');
    setFrozen(true);
  };

  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setToast('Texto do ecrã copiado.');
    } catch (error) {
      setToast(`Não foi possível copiar: ${errorMessage(error)}`);
    }
  };

  return (
    <PanelShell
      title="Ecrã traduzido ao vivo"
      description="Texto do jogo traduzido para português (evento screen_translation). O original nunca é mostrado."
      actions={
        <>
          <Chip
            size="small"
            variant="outlined"
            color={frozen ? 'default' : 'success'}
            label={frozen ? 'congelado' : 'em direto'}
          />
          <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
            última atualização {formatTime(screen?.ts)} · {formatRelative(screen?.ts, now)}
          </Typography>
          <Tooltip title={frozen ? 'Retomar atualização automática' : 'Congelar o texto atual'}>
            <IconButton
              aria-label={frozen ? 'Retomar atualização automática do ecrã' : 'Congelar o texto do ecrã'}
              onClick={toggleFreeze}
            >
              {frozen ? <PlayArrow /> : <Pause />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Atualizar agora">
            <span>
              <IconButton
                aria-label="Atualizar o texto do ecrã agora"
                onClick={() => void screenQuery.refetch()}
                disabled={screenQuery.isFetching}
              >
                <Refresh />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Copiar texto">
            <span>
              <IconButton aria-label="Copiar o texto do ecrã" onClick={copyText} disabled={!text}>
                <ContentCopy />
              </IconButton>
            </span>
          </Tooltip>
        </>
      }
    >
      {screenQuery.isLoading ? <PanelLoading label="A carregar o ecrã…" /> : null}

      {screenQuery.isError ? (
        <PanelError
          message={errorMessage(screenQuery.error)}
          onRetry={() => void screenQuery.refetch()}
        />
      ) : null}

      <Stack spacing={2}>
        <SectionCard
          title="Texto do jogo (PT)"
          subtitle="Atualiza sozinho; congela para ler com calma"
        >
          <Paper
            variant="outlined"
            sx={{
              p: { xs: 1.5, md: 2.5 },
              minHeight: 220,
              bgcolor: 'background.default',
              borderColor: 'divider',
            }}
          >
            {text ? (
              <Typography
                variant="body1"
                component="p"
                sx={{
                  fontFamily: MONO_FONT,
                  fontSize: { xs: '0.8125rem', md: '0.9375rem' },
                  lineHeight: 1.75,
                  whiteSpace: 'pre-wrap',
                }}
              >
                {text}
              </Typography>
            ) : (
              <Box sx={{ color: 'text.secondary' }}>
                <Typography variant="body2">Sem texto de ecrã por agora.</Typography>
              </Box>
            )}
          </Paper>
        </SectionCard>

        <PanelNotice severity="info">
          Decisão de arquitetura: o ecrã é mostrado <strong>só em português</strong>. O texto
          original em inglês do jogo nunca é apresentado — a tradução é <em>fire-and-forget</em> e
          não bloqueia o loop de decisão.
        </PanelNotice>

        {frozen ? (
          <Alert severity="warning" role="status">
            Texto congelado: a UI continua a receber traduções novas, mas está a mostrar a cópia
            que congelaste.
          </Alert>
        ) : null}
      </Stack>

      <Snackbar
        open={toast !== null}
        autoHideDuration={4000}
        onClose={(_event, reason) => {
          if (reason === 'clickaway') return;
          setToast(null);
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          severity={toast?.startsWith('Não foi possível') ? 'error' : 'success'}
          variant="filled"
          role="status"
          onClose={() => setToast(null)}
        >
          {toast}
        </Alert>
      </Snackbar>
    </PanelShell>
  );
}
