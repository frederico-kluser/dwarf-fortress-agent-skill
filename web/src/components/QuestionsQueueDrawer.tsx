import Badge from '@mui/material/Badge';
import Button from '@mui/material/Button';
import IconButton from '@mui/material/IconButton';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import QuestionAnswer from '@mui/icons-material/QuestionAnswer';

import {
  Sheet,
  SheetBackdrop,
  SheetClose,
  SheetHandle,
  SheetPanel,
} from '@/components/motion-ui/sheet';
import type { AnswerQuestionRequest, Question } from '../api/types';
import QuestionCard from './QuestionCard';
import { EmptyState } from './rpg/EmptyState';
import { groupQuestionsByAction } from './questionGroups';

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
 * Fila persistente de perguntas pendentes, num `sheet` do registry Motion UI
 * (entrada com mola, arrastar para fechar, foco gerido pelo `<dialog>` nativo).
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
  // Coalescência visual: pedidos da mesma `action_id` num só cartão (achado 1).
  const groups = groupQuestionsByAction(questions);
  return (
    <Sheet
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
    >
      <SheetBackdrop label="Fechar a fila de perguntas" />
      <SheetPanel labelledBy={DRAWER_TITLE_ID} className="flex max-h-[85vh] flex-col">
        <SheetHandle />
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h2 id={DRAWER_TITLE_ID} className="text-sm font-bold text-foreground">
              Perguntas pendentes
            </h2>
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              {questions.length === 0
                ? 'Nada à espera de resposta'
                : groups.length < questions.length
                  ? `${questions.length} pedidos · ${groups.length} ações à espera de resposta`
                  : `${questions.length} à espera de resposta`}
            </Typography>
          </div>
          <SheetClose label="Fechar a fila de perguntas" />
        </div>

        <div className="mt-3 flex-1 overflow-y-auto">
          {questions.length === 0 ? (
            <EmptyState
              glyph="hourglass"
              title="Nenhuma pergunta à espera"
              body="As dúvidas aparecem aqui quando o JEV decide nas bandas hitl ou abstain — o agente fica à espera da tua resposta."
            />
          ) : (
            <Stack spacing={1.5}>
              {groups.map((group) => (
                <QuestionCard
                  key={group.key}
                  question={group.representative}
                  coalesced={group.coalesced}
                  dense
                  busy={busyId === group.representative.id}
                  onAnswer={(body) => onAnswer(group.representative.id, body)}
                />
              ))}
            </Stack>
          )}
        </div>

        <div className="mt-3 border-t border-border pt-3">
          <Button fullWidth variant="outlined" onClick={onOpenPanel}>
            Abrir painel de perguntas
          </Button>
        </div>
      </SheetPanel>
    </Sheet>
  );
}
