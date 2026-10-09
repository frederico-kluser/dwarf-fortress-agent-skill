import Alert from '@mui/material/Alert';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import Send from '@mui/icons-material/Send';

import type { Question } from '../api/types';
import { MONO_FONT } from '../theme';

export interface AnswerConfirmDialogProps {
  /** Pergunta em confirmação; `null` fecha o diálogo. */
  question: Question | null;
  /** Texto exato que vai para a memória do agente. */
  answerText: string;
  /** Id da opção rápida escolhida (respostas livres não têm). */
  optionId?: string | null;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}

/**
 * Confirmação de envio de resposta (auditoria C.1.4.01: a fricção estava invertida).
 * Responder é irreversível — a resposta entra na memória do agente e não há caminho
 * de edição no contrato —, por isso o clique numa opção rápida mostra o que vai ser
 * escrito antes de enviar. Continua a ser um passo leve (2 cliques, sem escrever nada).
 */
export default function AnswerConfirmDialog({
  question,
  answerText,
  optionId = null,
  busy = false,
  onCancel,
  onConfirm,
}: AnswerConfirmDialogProps) {
  const titleId = 'confirmar-resposta-titulo';
  const textId = 'confirmar-resposta-texto';
  const open = question !== null;

  return (
    <Dialog open={open} onClose={onCancel} maxWidth="sm" aria-labelledby={titleId} aria-describedby={textId}>
      <DialogTitle id={titleId}>Enviar esta resposta?</DialogTitle>
      <DialogContent>
        <Stack spacing={1.5}>
          <Typography variant="body2" id={textId}>
            A resposta é traduzida PT→EN e escrita na memória do agente. Não há caminho de edição:
            só uma nova resposta corrige esta.
          </Typography>

          <Stack spacing={0.5}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Pergunta ({question?.id ?? '—'})
            </Typography>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>
              {question?.question ?? '—'}
            </Typography>
          </Stack>

          <Stack spacing={0.5}>
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              Resposta a enviar
            </Typography>
            <Typography variant="body2" sx={{ fontFamily: MONO_FONT, whiteSpace: 'pre-wrap' }}>
              {answerText}
            </Typography>
            {optionId ? (
              <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
                opção rápida: {optionId}
              </Typography>
            ) : null}
          </Stack>

          <Alert severity="info" variant="outlined">
            Em caso de dúvida, cancela: o agente fica à espera e a pergunta continua na fila.
          </Alert>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onCancel}>Cancelar</Button>
        <Button
          variant="contained"
          autoFocus
          loading={busy}
          loadingPosition="start"
          startIcon={<Send />}
          onClick={onConfirm}
        >
          Confirmar e enviar
        </Button>
      </DialogActions>
    </Dialog>
  );
}
