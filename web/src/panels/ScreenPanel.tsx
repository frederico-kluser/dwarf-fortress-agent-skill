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

import { errorMessage } from '../api/events';
import { useScreen } from '../api/queries';
import FreshnessIndicator from '../components/FreshnessIndicator';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import SectionCard from '../components/SectionCard';
import { tintedChipSx } from '../components/chipSkin';
import { useNow } from '../hooks/useSparkSeries';

/** Alvo mínimo de ações recorrentes (invariante §5 / achado I.2.2.02): ≥ 40 px. */
const TARGET_SX = { width: 40, height: 40 };

/** Ecrã do jogo traduzido ao vivo — só PT (o original nunca é mostrado). */
export default function ScreenPanel() {
  const screenQuery = useScreen();
  const now = useNow(1000);
  const [frozen, setFrozen] = useState(false);
  const [frozenText, setFrozenText] = useState('');
  const [frozenTs, setFrozenTs] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  const screen = screenQuery.data;
  const text = frozen ? frozenText : (screen?.translated_pt ?? '');
  /**
   * Frescura honesta (C.1.1.01): com o texto congelado, a frescura mostrada é a
   * da cópia visível — nunca a da tradução mais recente que NÃO se está a ver.
   */
  const visibleTs = frozen ? frozenTs : (screen?.ts ?? null);

  const toggleFreeze = () => {
    if (frozen) {
      setFrozen(false);
      return;
    }
    setFrozenText(screen?.translated_pt ?? '');
    setFrozenTs(screen?.ts ?? null);
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
            icon={frozen ? <Pause /> : <PlayArrow />}
            sx={tintedChipSx(frozen ? 'warning' : 'success')}
            label={frozen ? 'congelado' : 'em direto'}
          />
          {/*
            C.1.1.01: a frescura é a do `FreshnessIndicator` partilhado (mesmo
            texto/alvo de métricas, perguntas, decisões, memória e gastos) — com
            o ecrã congelado mostra a frescura da cópia visível (frescura honesta).
          */}
          <FreshnessIndicator
            ts={visibleTs}
            now={now}
            showClock
            refreshing={screenQuery.isFetching}
            refreshDisabled={screenQuery.isFetching}
            onRefresh={() => void screenQuery.refetch()}
            refreshLabel="Atualizar o ecrã agora"
          />
          <Tooltip title={frozen ? 'Retomar atualização automática' : 'Congelar o texto atual'}>
            <IconButton
              aria-label={frozen ? 'Retomar atualização automática do ecrã' : 'Congelar o texto do ecrã'}
              onClick={toggleFreeze}
              sx={TARGET_SX}
            >
              {frozen ? <PlayArrow /> : <Pause />}
            </IconButton>
          </Tooltip>
          <Tooltip title="Copiar texto">
            <span>
              <IconButton aria-label="Copiar o texto do ecrã" onClick={copyText} disabled={!text} sx={TARGET_SX}>
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
                className="text-pretty"
                sx={{
                  // §8 `#/ecra`: sans para PT (mono só para EN/identificadores).
                  fontSize: '0.9375rem',
                  lineHeight: 1.55,
                  maxWidth: '72ch',
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
