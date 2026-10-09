import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Send from '@mui/icons-material/Send';

import { formatRelative } from '../api/format';
import type { AnswerQuestionRequest, Question } from '../api/types';
import { QuestionStatusChip } from './Chips';

export interface QuestionCardProps {
  question: Question;
  /** Envia a resposta (opção rápida ou texto livre). */
  onAnswer: (body: AnswerQuestionRequest) => void;
  busy?: boolean;
  /** Versão compacta para o drawer da fila. */
  dense?: boolean;
}

/**
 * Pergunta/dúvida ao humano: texto em PT, opções rápidas e campo livre.
 * A resposta é traduzida PT→EN pelo servidor antes de entrar nos prompts.
 */
export default function QuestionCard({ question, onAnswer, busy = false, dense = false }: QuestionCardProps) {
  const [freeText, setFreeText] = useState('');
  const answered = question.status === 'answered';
  const fieldId = `resposta-livre-${question.id}`;

  const submitFreeText = () => {
    const text = freeText.trim();
    if (!text) return;
    onAnswer({ answer_text: text });
    setFreeText('');
  };

  return (
    <Paper variant="outlined" sx={{ p: dense ? 1.5 : 2 }}>
      <Stack spacing={1.5}>
        <Stack
          direction="row"
          spacing={1}
          sx={{ alignItems: 'flex-start', justifyContent: 'space-between' }}
        >
          <Typography variant="body1" component="h3" sx={{ fontWeight: 600 }}>
            {question.question}
          </Typography>
          <Stack
            direction="row"
            spacing={0.5}
            sx={{ alignItems: 'center', flexShrink: 0 }}
          >
            <QuestionStatusChip status={question.status} />
          </Stack>
        </Stack>

        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          {question.id} · {formatRelative(question.ts)}
        </Typography>

        {answered ? (
          <Box>
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              Resposta (canónica em EN)
            </Typography>
            <Typography variant="body2">{question.answer_text ?? '—'}</Typography>
            {question.option_id ? (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                opção: {question.option_id}
              </Typography>
            ) : null}
          </Box>
        ) : (
          <>
            {question.options.length > 0 ? (
              <Stack
                direction="row"
                spacing={1}
                useFlexGap
                role="group"
                aria-label={`Opções rápidas para ${question.id}`}
                sx={{ flexWrap: 'wrap' }}
              >
                {question.options.map((option) => (
                  <Button
                    key={option.id}
                    variant="outlined"
                    size="small"
                    disabled={busy}
                    onClick={() => onAnswer({ answer_text: option.text, option_id: option.id })}
                  >
                    {option.text}
                  </Button>
                ))}
              </Stack>
            ) : null}

            {question.free_text_allowed ? (
              <Stack
                direction={{ xs: 'column', sm: 'row' }}
                spacing={1}
                sx={{ alignItems: 'flex-start' }}
              >
                <TextField
                  id={fieldId}
                  label="Resposta livre"
                  value={freeText}
                  onChange={(event) => setFreeText(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      submitFreeText();
                    }
                  }}
                  multiline
                  minRows={1}
                  maxRows={4}
                  fullWidth
                  helperText="Escreve em português — o servidor traduz antes de guardar."
                />
                <Button
                  variant="contained"
                  onClick={submitFreeText}
                  disabled={busy || freeText.trim().length === 0}
                  loading={busy}
                  loadingPosition="start"
                  startIcon={<Send />}
                  sx={{ flexShrink: 0, mt: { xs: 0, sm: 0.5 } }}
                >
                  Responder
                </Button>
              </Stack>
            ) : (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Esta pergunta só aceita uma das opções acima.
              </Typography>
            )}
          </>
        )}
      </Stack>
    </Paper>
  );
}
