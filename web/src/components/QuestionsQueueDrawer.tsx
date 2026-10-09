import Badge from '@mui/material/Badge';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Divider from '@mui/material/Divider';
import Drawer from '@mui/material/Drawer';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Close from '@mui/icons-material/Close';
import QuestionAnswer from '@mui/icons-material/QuestionAnswer';

import type { AnswerQuestionRequest, Question } from '../api/types';
import { PanelEmpty } from './PanelState';
import QuestionCard from './QuestionCard';

const DRAWER_TITLE_ID = 'perguntas-pendentes-titulo';

/** Botão do AppBar com o contador de perguntas pendentes. */
export function QuestionsBadgeButton({
  pendingCount,
  onClick,
}: {
  pendingCount: number;
  onClick: () => void;
}) {
  return (
    <Tooltip title="Perguntas pendentes">
      <IconButton
        onClick={onClick}
        aria-label={
          pendingCount === 0
            ? 'Perguntas pendentes: nenhuma'
            : `Perguntas pendentes: ${pendingCount}`
        }
      >
        <Badge badgeContent={pendingCount} color="warning" max={99}>
          <QuestionAnswer />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}

export interface QuestionsQueueDrawerProps {
  open: boolean;
  onClose: () => void;
  /** Só as pendentes. */
  questions: Question[];
  onAnswer: (questionId: string, body: AnswerQuestionRequest) => void;
  busyId?: string | null;
  onOpenPanel: () => void;
}

/**
 * Fila persistente de perguntas pendentes (drawer lateral).
 * Acessível a partir de qualquer painel pelo badge do AppBar.
 */
export function QuestionsQueueDrawer({
  open,
  onClose,
  questions,
  onAnswer,
  busyId = null,
  onOpenPanel,
}: QuestionsQueueDrawerProps) {
  return (
    <Drawer
      anchor="right"
      open={open}
      onClose={onClose}
      slotProps={{
        paper: {
          role: 'dialog',
          'aria-labelledby': DRAWER_TITLE_ID,
          sx: { width: { xs: '100%', sm: 420 }, maxWidth: '100%' },
        },
      }}
    >
      <Stack
        direction="row"
        sx={{ alignItems: 'center', justifyContent: 'space-between', px: 2, py: 1.5 }}
      >
        <Box>
          <Typography variant="h4" component="h2" id={DRAWER_TITLE_ID}>
            Perguntas pendentes
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {questions.length === 0
              ? 'Nada à espera de resposta'
              : `${questions.length} à espera de resposta`}
          </Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="Fechar a fila de perguntas">
          <Close />
        </IconButton>
      </Stack>
      <Divider />

      <Box sx={{ p: 2, overflowY: 'auto', flexGrow: 1 }}>
        {questions.length === 0 ? (
          <PanelEmpty
            message="Sem perguntas pendentes."
            hint="As dúvidas aparecem aqui quando o JEV decide nas bandas hitl ou abstain."
          />
        ) : (
          <Stack spacing={1.5}>
            {questions.map((question) => (
              <QuestionCard
                key={question.id}
                question={question}
                dense
                busy={busyId === question.id}
                onAnswer={(body) => onAnswer(question.id, body)}
              />
            ))}
          </Stack>
        )}
      </Box>

      <Divider />
      <Box sx={{ p: 2 }}>
        <Button fullWidth variant="outlined" onClick={onOpenPanel}>
          Abrir painel de perguntas
        </Button>
      </Box>
    </Drawer>
  );
}
