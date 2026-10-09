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
import AnswerConfirmDialog from './AnswerConfirmDialog';
import { QuestionStatusChip } from './Chips';
import { MONO_FONT } from '../theme';

export interface QuestionCardProps {
  question: Question;
  /** Envia a resposta (opção rápida ou texto livre). */
  onAnswer: (body: AnswerQuestionRequest) => void;
  busy?: boolean;
  /** Versão compacta para o drawer da fila. */
  dense?: boolean;
}

interface PendingAnswer {
  text: string;
  optionId: string | null;
}

/**
 * Pergunta/dúvida ao humano: texto em PT, opções rápidas e campo livre.
 * A resposta é traduzida PT→EN pelo servidor antes de entrar nos prompts.
 *
 * Fricção alinhada com a irreversibilidade (auditoria C.1.4.01): a resposta entra
 * na memória do agente e não há edição no contrato, logo nenhum caminho envia com
 * um clique acidental — opção rápida pede confirmação com o texto exato, texto
 * livre tem pré-visualização antes do envio.
 */
export default function QuestionCard({ question, onAnswer, busy = false, dense = false }: QuestionCardProps) {
  const [freeText, setFreeText] = useState('');
  const [reviewing, setReviewing] = useState(false);
  const [pending, setPending] = useState<PendingAnswer | null>(null);
  const answered = question.status === 'answered';
  const fieldId = `resposta-livre-${question.id}`;

  const resetDraft = () => {
    setFreeText('');
    setReviewing(false);
  };

  const confirm = () => {
    if (!pending) return;
    onAnswer(
      pending.optionId
        ? { answer_text: pending.text, option_id: pending.optionId }
        : { answer_text: pending.text },
    );
    setPending(null);
    resetDraft();
  };

  const cancel = () => {
    // Cancelar no texto livre volta ao rascunho, sem perder o que foi escrito.
    if (pending && !pending.optionId) setReviewing(true);
    setPending(null);
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
                    onClick={() => setPending({ text: option.text, optionId: option.id })}
                  >
                    {option.text}
                  </Button>
                ))}
              </Stack>
            ) : null}

            {question.free_text_allowed ? (
              <Stack spacing={1}>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={1}
                  sx={{ alignItems: 'flex-start' }}
                >
                  <TextField
                    id={fieldId}
                    label="Resposta livre"
                    value={freeText}
                    onChange={(event) => {
                      setFreeText(event.target.value);
                      setReviewing(false);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' && !event.shiftKey) {
                        event.preventDefault();
                        if (reviewing) {
                          setPending({ text: freeText.trim(), optionId: null });
                        } else if (freeText.trim()) {
                          setReviewing(true);
                        }
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
                    onClick={() => {
                      const text = freeText.trim();
                      if (!text) return;
                      setPending({ text, optionId: null });
                    }}
                    disabled={busy || freeText.trim().length === 0}
                    loading={busy}
                    loadingPosition="start"
                    startIcon={<Send />}
                    sx={{ flexShrink: 0, mt: { xs: 0, sm: 0.5 } }}
                  >
                    Rever e enviar
                  </Button>
                </Stack>

                {reviewing ? (
                  <Box
                    role="status"
                    sx={{
                      p: 1,
                      borderRadius: 1,
                      border: '1px dashed var(--mui-palette-divider)',
                      backgroundColor: 'var(--mui-palette-action-hover)',
                    }}
                  >
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
                      Vai ser enviado como resposta (passo 2 de 2):
                    </Typography>
                    <Typography variant="body2" sx={{ fontFamily: MONO_FONT, whiteSpace: 'pre-wrap' }}>
                      {freeText.trim()}
                    </Typography>
                  </Box>
                ) : null}
              </Stack>
            ) : (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Esta pergunta só aceita uma das opções acima.
              </Typography>
            )}
          </>
        )}
      </Stack>

      <AnswerConfirmDialog
        question={pending ? question : null}
        answerText={pending?.text ?? ''}
        optionId={pending?.optionId ?? null}
        busy={busy}
        onCancel={cancel}
        onConfirm={confirm}
      />
    </Paper>
  );
}
