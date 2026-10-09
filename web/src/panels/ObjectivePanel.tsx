import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Checkbox from '@mui/material/Checkbox';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import LinearProgress from '@mui/material/LinearProgress';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Add from '@mui/icons-material/Add';
import Delete from '@mui/icons-material/Delete';
import Save from '@mui/icons-material/Save';
import Undo from '@mui/icons-material/Undo';

import { errorMessage } from '../api/events';
import { formatPercent } from '../api/format';
import { useMetrics, useObjective, usePutObjective } from '../api/queries';
import type { Objective } from '../api/types';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading, PanelNotice } from '../components/PanelState';
import SectionCard from '../components/SectionCard';

/** Editor do objetivo: narrativa + sub-objetivos marcáveis (PUT completo). */
export default function ObjectivePanel() {
  const objectiveQuery = useObjective();
  const metricsQuery = useMetrics();
  const objective = objectiveQuery.data;

  // Edição derivada (sem efeitos): `base` é o objetivo do servidor quando a edição começou.
  const [edit, setEdit] = useState<{ base: Objective; value: Objective } | null>(null);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const draft: Objective | null = edit ? edit.value : (objective ?? null);
  const dirty = edit !== null && JSON.stringify(edit.value) !== JSON.stringify(edit.base);

  const saveMutation = usePutObjective({
    onSuccess: () => {
      setEdit(null);
      setFeedback({ tone: 'success', text: 'Objetivo guardado.' });
    },
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao guardar o objetivo: ${errorMessage(error)}` }),
  });

  const subgoals = draft?.subgoals ?? [];
  const hasEmptyText = subgoals.some((goal) => goal.text.trim().length === 0);
  const narrativeEmpty = (draft?.narrative ?? '').trim().length === 0;
  const canSave = Boolean(draft) && dirty && !hasEmptyText && !narrativeEmpty;

  const activeSubgoalId = metricsQuery.data?.objective_progress.active_subgoal_id ?? null;
  const percent = metricsQuery.data?.objective_progress.percent;

  const update = (next: Objective) => {
    setEdit({ base: edit?.base ?? objective ?? next, value: next });
    setFeedback(null);
  };

  const actions = (
    <>
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
        disabled={!canSave}
        onClick={() => draft && saveMutation.mutate(draft)}
      >
        Guardar objetivo
      </Button>
    </>
  );

  return (
    <PanelShell
      title="Objetivo e sub-objetivos"
      description="O que o agente deve cumprir. Escreve em português: o servidor traduz PT→EN antes de entrar nos prompts."
      actions={actions}
    >
      {objectiveQuery.isLoading ? <PanelLoading label="A carregar objetivo…" /> : null}

      {objectiveQuery.isError ? (
        <PanelError
          message={errorMessage(objectiveQuery.error)}
          onRetry={() => void objectiveQuery.refetch()}
        />
      ) : null}

      {draft ? (
        <Stack spacing={2}>
          {feedback ? (
            <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
              {feedback.text}
            </Alert>
          ) : null}

          {hasEmptyText ? (
            <Alert severity="warning" role="status">
              Há sub-objetivos sem texto. Escreve-os ou remove-os antes de guardar.
            </Alert>
          ) : null}

          <SectionCard
            title="Narrativa do objetivo"
            subtitle="Substituída por inteiro no PUT /objective"
            action={dirty ? <Chip size="small" color="warning" label="alterações por guardar" /> : null}
          >
            <TextField
              id="objetivo-narrativa"
              label="Objetivo (em português)"
              value={draft.narrative}
              onChange={(event) => update({ ...draft, narrative: event.target.value })}
              multiline
              minRows={3}
              fullWidth
              error={narrativeEmpty}
              helperText={
                narrativeEmpty
                  ? 'O objetivo não pode ficar vazio.'
                  : 'Ex.: «Chegar ao portão de pedra a nordeste, vivo e com água para dois dias.»'
              }
            />
          </SectionCard>

          <SectionCard
            title="Sub-objetivos"
            subtitle={`${subgoals.filter((goal) => goal.done).length} de ${subgoals.length} concluídos`}
            flush
            action={
              <Button
                size="small"
                startIcon={<Add />}
                onClick={() =>
                  update({
                    ...draft,
                    subgoals: [
                      ...draft.subgoals,
                      { id: `sg-${draft.subgoals.length + 1}-${Date.now().toString(36)}`, text: '', done: false },
                    ],
                  })
                }
              >
                Adicionar
              </Button>
            }
          >
            <Stack spacing={1.5} sx={{ px: 2, pt: 2 }}>
              <Stack spacing={0.5}>
                <LinearProgress
                  variant="determinate"
                  value={Math.round((percent ?? 0) * 100)}
                  aria-label="Progresso do objetivo"
                />
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  Progresso reportado pelo agente: {formatPercent(percent)}{' '}
                  {activeSubgoalId ? `· sub-objetivo ativo: ${activeSubgoalId}` : ''}
                </Typography>
              </Stack>
            </Stack>

            {subgoals.length === 0 ? (
              <Typography variant="body2" sx={{ p: 2, color: 'text.secondary' }}>
                Sem sub-objetivos. Adiciona o primeiro passo concreto.
              </Typography>
            ) : (
              <List dense disablePadding sx={{ pt: 1 }}>
                {subgoals.map((goal, index) => (
                  <ListItem
                    key={goal.id}
                    divider={index < subgoals.length - 1}
                    secondaryAction={
                      <Tooltip title="Remover sub-objetivo">
                        <IconButton
                          edge="end"
                          aria-label={`Remover sub-objetivo ${index + 1}`}
                          onClick={() =>
                            update({
                              ...draft,
                              subgoals: draft.subgoals.filter((item) => item.id !== goal.id),
                            })
                          }
                        >
                          <Delete />
                        </IconButton>
                      </Tooltip>
                    }
                  >
                    <Checkbox
                      checked={goal.done}
                      onChange={(event) =>
                        update({
                          ...draft,
                          subgoals: draft.subgoals.map((item) =>
                            item.id === goal.id ? { ...item, done: event.target.checked } : item,
                          ),
                        })
                      }
                      slotProps={{
                        input: { 'aria-label': `Marcar «${goal.text || `sub-objetivo ${index + 1}`}» como concluído` },
                      }}
                    />
                    <TextField
                      label={`Sub-objetivo ${index + 1}`}
                      value={goal.text}
                      onChange={(event) =>
                        update({
                          ...draft,
                          subgoals: draft.subgoals.map((item) =>
                            item.id === goal.id ? { ...item, text: event.target.value } : item,
                          ),
                        })
                      }
                      fullWidth
                      sx={{ mr: 6 }}
                    />
                    {activeSubgoalId === goal.id ? (
                      <Chip size="small" color="primary" variant="outlined" label="ativo" sx={{ ml: 1 }} />
                    ) : null}
                  </ListItem>
                ))}
              </List>
            )}
          </SectionCard>

          <PanelNotice severity="info">
            O contrato não devolve uma versão traduzida do objetivo (não existe{' '}
            <code>narrative_pt</code> em <code>/objective</code>): o texto mostrado é o que será
            traduzido e guardado pelo servidor.
          </PanelNotice>
        </Stack>
      ) : null}
    </PanelShell>
  );
}
