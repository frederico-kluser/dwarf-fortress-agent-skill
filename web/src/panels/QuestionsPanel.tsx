import { useState } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Chip from '@mui/material/Chip';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';

import { errorMessage } from '../api/events';
import { formatDateTime } from '../api/format';
import { useAnswerQuestion, useQuestions } from '../api/queries';
import FreshnessIndicator from '../components/FreshnessIndicator';
import PanelShell from '../components/PanelShell';
import { PanelEmpty, PanelError, PanelLoading } from '../components/PanelState';
import QuestionCard from '../components/QuestionCard';
import SectionCard from '../components/SectionCard';
import { useNow } from '../hooks/useSparkSeries';

/** Fila de dúvidas: responder depressa, com as respondidas a colapsar no histórico. */
export default function QuestionsPanel() {
  const questionsQuery = useQuestions();
  const now = useNow(1000);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const answerMutation = useAnswerQuestion({
    onSuccess: () => setFeedback({ tone: 'success', text: 'Resposta enviada e guardada na memória.' }),
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao responder: ${errorMessage(error)}` }),
  });

  const questions = questionsQuery.data ?? [];
  const pending = questions.filter((question) => question.status === 'pending');
  const answered = questions
    .filter((question) => question.status === 'answered')
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));

  return (
    <PanelShell
      title="Fila de perguntas"
      description="Dúvidas geradas nas bandas hitl e abstain. A resposta entra na memória do agente (traduzida PT→EN)."
      actions={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip size="small" color="warning" variant="outlined" label={`${pending.length} pendentes`} />
          <Chip size="small" color="success" variant="outlined" label={`${answered.length} respondidas`} />
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
          subtitle="Escolhe uma opção rápida ou escreve uma resposta livre"
        >
          {!questionsQuery.isLoading && pending.length === 0 ? (
            <PanelEmpty
              message="Sem perguntas pendentes."
              hint="Quando o JEV não tiver confiança suficiente, a pergunta aparece aqui e no badge da barra superior."
            />
          ) : (
            <Stack spacing={1.5}>
              {pending.map((question) => (
                <QuestionCard
                  key={question.id}
                  question={question}
                  busy={answerMutation.isPending && answerMutation.variables?.qid === question.id}
                  onAnswer={(body) => answerMutation.mutate({ qid: question.id, ...body })}
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
      </Stack>
    </PanelShell>
  );
}
