import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Paper from '@mui/material/Paper';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Send from '@mui/icons-material/Send';

import { formatRelative } from '../api/format';
import {
  isDecisionBand,
  isJevAgent,
  type AnswerQuestionRequest,
  type DecisionBand,
  type Question,
} from '../api/types';
import AnswerConfirmDialog from './AnswerConfirmDialog';
import { BandChip, QuestionStatusChip } from './Chips';
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
 * Explicação derivada quando o produtor não mandou `why_pt` (payload antigo com
 * banda): é a mesma frase que o loop publica (`_why_pt`), nunca conteúdo novo.
 */
const BAND_WHY_PT: Record<DecisionBand, string> = {
  auto: 'a decisão ficou na banda auto: confirmar antes de executar',
  hitl: 'a confiança ficou na banda hitl (0.50–0.89): confirmar antes de executar',
  abstain: 'a confiança ficou abaixo de 0.50: o agente não age sozinho',
};

/**
 * Proveniência da pergunta (achado #2 de `docs/uxui-audit-ui.json`): a fila tem
 * de dizer **porque** o agente pergunta, em que banda e com que ação retida.
 *
 * Os campos são aditivos: sem nenhum deles (produtor antigo) não se desenha nada;
 * com alguns, mostra-se só o que existe — a banda nunca depende só da cor (WCAG).
 */
function QuestionProvenance({ question, dense }: { question: Question; dense: boolean }) {
  const band = isDecisionBand(question.band) ? question.band : null;
  const role = isJevAgent(question.role) ? question.role : null;
  const actionId = question.action_id?.trim() ? question.action_id.trim() : null;
  const why = question.why_pt?.trim() ? question.why_pt.trim() : band ? BAND_WHY_PT[band] : null;

  if (!band && !role && !actionId && !why) return null;

  return (
    <Box
      sx={{
        p: dense ? 1 : 1.25,
        borderRadius: 1,
        borderLeft: '3px solid var(--mui-palette-info-main)',
        backgroundColor: 'var(--mui-palette-action-hover)',
      }}
    >
      <Stack spacing={0.5}>
        <Stack
          direction="row"
          spacing={0.75}
          useFlexGap
          sx={{ alignItems: 'center', flexWrap: 'wrap' }}
        >
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Porque pergunta
          </Typography>
          {band ? <BandChip band={band} /> : null}
        </Stack>

        {why ? <Typography variant="body2">{why}</Typography> : null}

        {role || actionId ? (
          <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
            {role ? `papel ${role}` : ''}
            {role && actionId ? ' · ' : ''}
            {actionId ? `ação ${actionId}` : ''}
          </Typography>
        ) : null}
      </Stack>
    </Box>
  );
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

        <QuestionProvenance question={question} dense={dense} />

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
