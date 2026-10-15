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
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';

import { errorMessage } from '../api/events';
import { formatDateTime } from '../api/format';
import { useApiContext, useAnswerQuestion, useQuestions } from '../api/queries';
import { tintedChipSx } from '../components/chipSkin';
import FreshnessIndicator from '../components/FreshnessIndicator';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelLoading } from '../components/PanelState';
import QuestionCard from '../components/QuestionCard';
import { EmptyState } from '../components/rpg/EmptyState';
import { groupQuestionsByAction } from '../components/questionGroups';
import SectionCard from '../components/SectionCard';
import { useNow } from '../hooks/useSparkSeries';

/** Fila de dúvidas: responder depressa, com as respondidas a colapsar no histórico. */
export default function QuestionsPanel() {
  const { campaign } = useApiContext();
  const questionsQuery = useQuestions();
  const now = useNow(1000);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const answerMutation = useAnswerQuestion({
    onSuccess: () => setFeedback({ tone: 'success', text: 'Resposta enviada e guardada na memória.' }),
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao responder: ${errorMessage(error)}` }),
  });

  const questions = questionsQuery.items;
  const pending = questions.filter((question) => question.status === 'pending');
  const answered = questions
    .filter((question) => question.status === 'answered')
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
  // Coalescência visual: pedidos da mesma `action_id` num só cartão (achado 1).
  const pendingGroups = groupQuestionsByAction(pending);
  const coalescedCount = pending.length - pendingGroups.length;

  return (
    <PanelShell
      title="Fila de perguntas"
      description="Dúvidas geradas nas bandas hitl e abstain. A resposta entra na memória do agente (traduzida PT→EN)."
      actions={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            size="small"
            variant="outlined"
            sx={tintedChipSx(pending.length > 0 ? 'warning' : 'default')}
            label={`${pending.length} pendentes`}
          />
          <Chip
            size="small"
            variant="outlined"
            sx={tintedChipSx('success')}
            label={`${answered.length} respondidas`}
          />
          <FreshnessIndicator
            ts={questionsQuery.dataUpdatedAt ? new Date(questionsQuery.dataUpdatedAt).toISOString() : null}
            now={now}
            refreshing={questionsQuery.isFetching}
            refreshDisabled={questionsQuery.isFetching}
            onRefresh={() => void questionsQuery.refetch()}
            refreshLabel="Atualizar perguntas agora"
          />
        </Stack>
      }
    >
      {questionsQuery.isLoading ? <PanelLoading label="A carregar perguntas…" /> : null}

      {questionsQuery.isError ? (
        <PanelError
          message={errorMessage(questionsQuery.error)}
          onRetry={() => void questionsQuery.refetch()}
        />
      ) : null}

      <Stack spacing={2}>
        {feedback ? (
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        ) : null}

        <SectionCard
          title="Pendentes"
          subtitle={
            coalescedCount > 0
              ? `Escolhe uma opção rápida ou escreve uma resposta livre — ${coalescedCount} pedido(s) da mesma ação agrupados`
              : 'Escolhe uma opção rápida ou escreve uma resposta livre'
          }
        >
          {!questionsQuery.isLoading && pending.length === 0 ? (
            /* §5.7 — mensagem por painel, nomeando o objeto real; a frase do
             * autopiloto só aparece quando ela é verdade (honestidade do estado). */
            <EmptyState
              glyph="hourglass"
              title={
                campaign?.control.mode === 'autopilot'
                  ? 'Nenhuma pergunta à espera — o agente segue em autopilot'
                  : 'Nenhuma pergunta à espera'
              }
              body="Quando o JEV decidir sem confiança suficiente (bandas hitl ou abstain), a dúvida aparece aqui e no badge da barra superior — e o agente fica à espera da tua resposta."
              action={{
                label: 'Atualizar perguntas',
                onClick: () => void questionsQuery.refetch(),
              }}
            />
          ) : (
            <Stack spacing={1.5}>
              {pendingGroups.map((group) => (
                <QuestionCard
                  key={group.key}
                  question={group.representative}
                  coalesced={group.coalesced}
                  busy={answerMutation.isPending && answerMutation.variables?.qid === group.representative.id}
                  onAnswer={(body) => answerMutation.mutate({ qid: group.representative.id, ...body })}
                />
              ))}
            </Stack>
          )}
        </SectionCard>

        <Accordion variant="outlined" disableGutters>
          <AccordionSummary
            expandIcon={<ExpandMore />}
            id="historico-perguntas-cabecalho"
            aria-controls="historico-perguntas-conteudo"
          >
            <Typography variant="h5" component="h2">
              Histórico de respostas ({answered.length})
            </Typography>
          </AccordionSummary>
          <AccordionDetails>
            {answered.length === 0 ? (
              <Typography variant="body2" sx={{ color: 'text.secondary' }}>
                Ainda não respondeste a nenhuma pergunta nesta campanha.
              </Typography>
            ) : (
              <List dense disablePadding>
                {answered.map((question, index) => (
                  <ListItem key={question.id} divider={index < answered.length - 1} alignItems="flex-start">
                    <ListItemText
                      primary={question.question}
                      secondary={`resposta: ${question.answer_text ?? '—'}${
                        question.option_id ? ` (${question.option_id})` : ''
                      } · ${formatDateTime(question.ts)}`}
                      slotProps={{
                        primary: { variant: 'body2' },
                        secondary: { variant: 'caption' },
                      }}
                    />
                  </ListItem>
                ))}
              </List>
            )}
          </AccordionDetails>
        </Accordion>

        {questionsQuery.hasMore ? (
          <Stack sx={{ alignItems: 'center' }}>
            <Button
              variant="outlined"
              onClick={questionsQuery.loadMore}
              loading={questionsQuery.isLoadingMore}
              disabled={questionsQuery.isLoadingMore}
              aria-label="Carregar mais perguntas do histórico (página anterior do anel)"
            >
              Carregar mais perguntas
            </Button>
            <Typography variant="caption" sx={{ color: 'text.secondary', mt: 0.5 }}>
              {questions.length} carregadas — o anel guarda até 2000 por campanha
            </Typography>
          </Stack>
        ) : null}
      </Stack>
    </PanelShell>
  );
}
