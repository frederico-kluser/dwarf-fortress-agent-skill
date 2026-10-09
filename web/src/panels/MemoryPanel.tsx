import { useState } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';
import Save from '@mui/icons-material/Save';
import Undo from '@mui/icons-material/Undo';

import { errorMessage } from '../api/events';
import { formatDateTime, valuePt } from '../api/format';
import { useMemory, usePatchMemory } from '../api/queries';
import type { MemoryEpisode } from '../api/types';
import FreshnessIndicator from '../components/FreshnessIndicator';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import SectionCard from '../components/SectionCard';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';

function sortEpisodes(episodes: MemoryEpisode[]): MemoryEpisode[] {
  return [...episodes].sort((a, b) => (a.ts < b.ts ? 1 : -1));
}

/** Editor da memória narrativa: mostra PT, guarda através do PATCH em PT. */
export default function MemoryPanel() {
  const memoryQuery = useMemory();
  const now = useNow(1000);
  const memory = memoryQuery.data;

  // Rascunho derivado (sem efeitos): `base` é o texto do servidor quando começou a edição.
  const [edit, setEdit] = useState<{ base: string; text: string } | null>(null);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const serverText = memory?.narrative_pt ?? '';
  const draft = edit ? edit.text : serverText;
  const dirty = edit !== null && edit.text !== edit.base;

  const saveMutation = usePatchMemory({
    onSuccess: () => {
      setEdit(null);
      setFeedback({ tone: 'success', text: 'Memória guardada (traduzida para inglês no servidor).' });
    },
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao guardar: ${errorMessage(error)}` }),
  });

  const actions = (
    <>
      <FreshnessIndicator
        ts={memoryQuery.dataUpdatedAt ? new Date(memoryQuery.dataUpdatedAt).toISOString() : null}
        now={now}
        refreshing={memoryQuery.isFetching}
        refreshDisabled={memoryQuery.isFetching}
        onRefresh={() => void memoryQuery.refetch()}
        refreshLabel="Atualizar memória agora"
      />
      <Button
        variant="outlined"
        startIcon={<Undo />}
        disabled={!dirty || saveMutation.isPending}
        onClick={() => {
          setEdit(null);
          setFeedback(null);
        }}
      >
        Reverter
      </Button>
      <Button
        variant="contained"
        startIcon={<Save />}
        loading={saveMutation.isPending}
        loadingPosition="start"
        disabled={!dirty || draft.trim().length === 0}
        onClick={() => saveMutation.mutate({ narrative: draft })}
      >
        Guardar
      </Button>
    </>
  );

  return (
    <PanelShell
      title="Memória narrativa"
      description="A memória canónica é guardada em inglês; aqui vês e editas a versão em português."
      actions={actions}
    >
      {memoryQuery.isLoading ? <PanelLoading label="A carregar memória…" /> : null}

      {memoryQuery.isError ? (
        <PanelError
          message={errorMessage(memoryQuery.error)}
          onRetry={() => void memoryQuery.refetch()}
        />
      ) : null}

      {memory ? (
        <Stack spacing={2}>
          {feedback ? (
            <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
              {feedback.text}
            </Alert>
          ) : null}

          <SectionCard
            title="Narrativa (PT)"
            subtitle={`Atualizada em ${formatDateTime(memory.updated_at)}`}
            headingLevel="h2"
            action={dirty ? <Chip size="small" color="warning" label="alterações por guardar" /> : null}
          >
            <TextField
              id="memoria-narrativa"
              label="Narrativa em português"
              value={draft}
              onChange={(event) => setEdit({ base: edit?.base ?? serverText, text: event.target.value })}
              multiline
              minRows={6}
              fullWidth
              helperText="Escreve em português. Ao guardar, o servidor traduz PT→EN antes de gravar a memória canónica."
            />
          </SectionCard>

          <SectionCard
            title="Episódios"
            subtitle={`${memory.episodes.length} registos · do mais recente para o mais antigo`}
            headingLevel="h2"
            flush
          >
            {memory.episodes.length === 0 ? (
              <Typography variant="body2" sx={{ p: 2, color: 'text.secondary' }}>
                Ainda não há episódios nesta campanha.
              </Typography>
            ) : (
              <List dense disablePadding>
                {sortEpisodes(memory.episodes).map((episode) => (
                  <ListItem key={episode.id} divider alignItems="flex-start">
                    <ListItemText
                      primary={episode.content_pt}
                      secondary={`${formatDateTime(episode.ts)} · ${valuePt(episode.kind)} · ${episode.id}`}
                      slotProps={{
                        primary: { variant: 'body2' },
                        secondary: { variant: 'caption', sx: { fontFamily: MONO_FONT } },
                      }}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </SectionCard>

          <Accordion variant="outlined" disableGutters>
            <AccordionSummary expandIcon={<ExpandMore />} aria-controls="memoria-trabalho-conteudo" id="memoria-trabalho-cabecalho">
              <Typography variant="h5" component="h2">
                Memória de trabalho (canónica, em inglês)
              </Typography>
            </AccordionSummary>
            <AccordionDetails>
              <PanelNotice severity="info">
                O contrato (<code>docs/api.md</code>) define <code>working_memory</code> apenas em
                inglês — não existe variante <code>working_memory_pt</code>. É mostrada tal como o
                agente a usa nos prompts.
              </PanelNotice>
              <Typography
                variant="body2"
                sx={{ mt: 1.5, fontFamily: MONO_FONT, whiteSpace: 'pre-wrap' }}
              >
                {memory.working_memory || '—'}
              </Typography>
            </AccordionDetails>
          </Accordion>
        </Stack>
      ) : null}
    </PanelShell>
  );
}
