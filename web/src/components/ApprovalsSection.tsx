import { useState } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Badge from '@mui/material/Badge';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import IconButton from '@mui/material/IconButton';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import ApprovalIcon from '@mui/icons-material/Approval';
import Block from '@mui/icons-material/Block';
import ExpandMore from '@mui/icons-material/ExpandMore';
import TaskAlt from '@mui/icons-material/TaskAlt';

import {
  StaggerReveal,
  StaggerRevealHeadline,
  StaggerRevealItem,
} from '@/components/motion-ui/stagger-reveal';
import { errorMessage } from '../api/events';
import { formatDateTime, formatRelative } from '../api/format';
import { useApprovals, useDecideApproval } from '../api/queries';
import { isDecisionBand, type Approval, type ControlMode } from '../api/types';
import { useNow } from '../hooks/useSparkSeries';
import { MONO_FONT } from '../theme';
import ApprovalConfirmDialog from './ApprovalConfirmDialog';
import { AttemptsChip, BandChip } from './Chips';
import { tintedChipSx } from './chipSkin';
import FreshnessIndicator from './FreshnessIndicator';
import { PanelError, PanelLoading, PanelNotice } from './PanelState';
import { EmptyState } from './rpg/EmptyState';
import SectionCard from './SectionCard';
import { groupApprovalsByAction } from './questionGroups';

/**
 * Limite da fila de pendentes no contrato (`docs/api.md` §approvals): passado
 * isto, as mais antigas são descartadas pelo backend — a UI avisa quando a
 * janela está cheia para o descarte não ser uma surpresa (auditoria v2, achado 1).
 */
const PENDING_APPROVALS_CAP = 20;

/** Botão do AppBar com o contador de aprovações pendentes (modo `checkpoint`). */
export function ApprovalsBadgeButton({
  pendingCount,
  onClick,
}: {
  pendingCount: number;
  onClick: () => void;
}) {
  return (
    <Tooltip title="Aprovações pendentes (modo checkpoints)">
      <IconButton
        onClick={onClick}
        aria-label={
          pendingCount === 0
            ? 'Aprovações pendentes: nenhuma'
            : `Aprovações pendentes: ${pendingCount}`
        }
      >
        <Badge badgeContent={pendingCount} color="warning" max={99}>
          <ApprovalIcon />
        </Badge>
      </IconButton>
    </Tooltip>
  );
}

export interface ApprovalsSectionProps {
  /** Modo de autonomia da campanha — decide o estado vazio explicativo. */
  mode: ControlMode;
}

interface PendingDecision {
  approval: Approval;
  approve: boolean;
}

/**
 * Superfície de aprovação do modo `checkpoint` (achado #4 de
 * `docs/uxui-audit-ui.json`): o que o agente **quer** fazer e ainda não fez,
 * com Aprovar/Rejeitar — cada decisão passa por confirmação, porque a resposta
 * entra na memória do agente e não há caminho de edição.
 *
 * A lista é mantida viva pelos eventos `approval_new`/`approval_updated` do WS
 * (via cache do React Query) e pela leitura de `GET .../approvals`.
 */
export default function ApprovalsSection({ mode }: ApprovalsSectionProps) {
  const approvalsQuery = useApprovals();
  // Histórico auditável (`?status=all`): o contrato guarda a decisão humana.
  const historyQuery = useApprovals('all');
  const now = useNow(1000);
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [pending, setPending] = useState<PendingDecision | null>(null);

  const decideMutation = useDecideApproval({
    onSuccess: (approval) =>
      setFeedback({
        tone: 'success',
        text:
          approval.status === 'approved'
            ? `Ação aprovada: ${approval.action_name_en}.`
            : `Ação rejeitada: ${approval.action_name_en}.`,
      }),
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao decidir a aprovação: ${errorMessage(error)}` }),
  });

  const approvals = approvalsQuery.data ?? [];
  // Coalescência visual: pedidos da mesma `action_id` num só cartão (achado 1).
  const approvalGroups = groupApprovalsByAction(approvals);
  const coalescedCount = approvals.length - approvalGroups.length;
  const queueFull = approvals.length >= PENDING_APPROVALS_CAP;
  // Histórico decidido (mais recente primeiro) — a trilha de auditoria humana.
  const decided = (historyQuery.data ?? [])
    .filter((approval) => approval.status !== 'pending')
    .sort((a, b) => (a.ts < b.ts ? 1 : -1));
  const heldWhileAutopilot = mode === 'autopilot' && approvals.length > 0;

  const confirmDecision = () => {
    if (!pending) return;
    decideMutation.mutate({ approvalId: pending.approval.id, approve: pending.approve });
    setPending(null);
  };

  return (
    <SectionCard
      title="Aprovações retidas"
      subtitle={
        coalescedCount > 0
          ? `Em «Checkpoints» o agente retém cada ação aqui até a aprovares ou rejeitares — ${coalescedCount} pedido(s) da mesma ação agrupados`
          : 'Em «Checkpoints» o agente retém cada ação aqui até a aprovares ou rejeitares'
      }
      flush
      action={
        <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip
            size="small"
            variant="outlined"
            sx={tintedChipSx(approvals.length > 0 ? 'warning' : 'default')}
            label={`${approvals.length} pendentes`}
          />
          <FreshnessIndicator
            ts={
              approvalsQuery.dataUpdatedAt
                ? new Date(approvalsQuery.dataUpdatedAt).toISOString()
                : null
            }
            now={now}
            refreshing={approvalsQuery.isFetching}
            refreshDisabled={approvalsQuery.isFetching}
            onRefresh={() => void approvalsQuery.refetch()}
            refreshLabel="Atualizar aprovações agora"
          />
        </Stack>
      }
    >
      {approvalsQuery.isLoading ? <PanelLoading label="A carregar aprovações…" /> : null}

      {approvalsQuery.isError ? (
        <PanelError
          message={errorMessage(approvalsQuery.error)}
          onRetry={() => void approvalsQuery.refetch()}
        />
      ) : null}

      {feedback ? (
        <Stack sx={{ p: 2, pb: 0 }}>
          <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
            {feedback.text}
          </Alert>
        </Stack>
      ) : null}

      {heldWhileAutopilot ? (
        <Stack sx={{ p: 2, pb: 0 }}>
          <PanelNotice severity="warning">
            O agente está em <strong>autopiloto</strong>: estas ações ficaram retidas quando o modo
            era «Checkpoints» e continuam à espera de decisão.
          </PanelNotice>
        </Stack>
      ) : null}

      {queueFull ? (
        <Stack sx={{ p: 2, pb: 0 }}>
          <PanelNotice severity="warning">
            Fila no limite ({PENDING_APPROVALS_CAP} pendentes): o contrato descarta as entradas mais
            antigas quando chega mais uma — decide ou agrupa para não perderes pedidos.
          </PanelNotice>
        </Stack>
      ) : null}

      {!approvalsQuery.isLoading && approvals.length === 0 ? (
        /* §5.7 — a mensagem nomeia o objeto real e diz o que falta e porquê;
         * a frase do autopiloto só quando ela é verdade (honestidade). */
        mode === 'autopilot' ? (
          <EmptyState
            glyph="hourglass"
            title="Sem aprovações pendentes — o autopiloto decide sozinho"
            body="O agente só retém ações para aprovação no modo «Checkpoints». Muda o modo para reveres cada ação antes de ser executada."
          />
        ) : (
          <EmptyState
            glyph="shield"
            title="Sem aprovações pendentes"
            body="Assim que o agente quiser executar uma ação retida, ela aparece aqui e no badge da barra superior — para aprovares ou rejeitares."
          />
        )
      ) : null}

      {approvals.length > 0 ? (
        <List dense disablePadding>
          {approvalGroups.map(({ representative: approval, coalesced }, index) => {
            const groupAttempts = [approval, ...coalesced].reduce(
              (total, item) => total + (item.attempts ?? 1),
              0,
            );
            const lastSeen = [approval, ...coalesced]
              .map((item) => item.last_seen_ts)
              .filter((ts): ts is string => Boolean(ts))
              .sort()
              .at(-1);
            return (
            <ListItem
              key={approval.id}
              divider={index < approvalGroups.length - 1}
              sx={{ display: 'block', py: 1.5 }}
            >
              {/*
                Chegada do cartão de aprovação (primeira leitura ou evento
                WebSocket `approval_new`): `stagger-reveal` com o nome da ação
                por título e os detalhes em cascata por baixo.
              */}
              <StaggerReveal className="flex flex-wrap items-start gap-2">
                <div className="min-w-60 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <StaggerRevealHeadline
                      as="h3"
                      className="text-xs font-semibold text-foreground"
                    >
                      {approval.action_name_en}
                    </StaggerRevealHeadline>
                    {isDecisionBand(approval.band) ? <BandChip band={approval.band} /> : null}
                    <AttemptsChip attempts={groupAttempts} lastSeenTs={lastSeen} />
                  </div>
                  <StaggerRevealItem>
                    <div className="mt-1 flex flex-col gap-0.5">
                      {approval.question ? (
                        <Typography variant="body2" component="span">
                          {approval.question}
                        </Typography>
                      ) : null}
                      <Typography
                        variant="caption"
                        component="span"
                        sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}
                      >
                        {approval.id} · {approval.action_id} · {formatRelative(approval.ts, now)} ·{' '}
                        {formatDateTime(approval.ts)}
                      </Typography>
                      {approval.description_en ? (
                        <Typography variant="caption" component="span" sx={{ color: 'text.secondary' }}>
                          {approval.description_en}
                        </Typography>
                      ) : null}
                      {coalesced.length > 0 ? (
                        <Typography variant="caption" component="span" sx={{ color: 'text.secondary' }}>
                          Agrupados: {coalesced.length + 1} pedidos da mesma ação ({approval.action_id}) —
                          decidir esta decide o grupo (duplicados pendentes são descartados).
                        </Typography>
                      ) : null}
                    </div>
                  </StaggerRevealItem>
                </div>
                <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexShrink: 0 }}>
                  <Button
                    variant="contained"
                    size="small"
                    startIcon={<TaskAlt />}
                    disabled={decideMutation.isPending}
                    sx={{ minHeight: 40 }}
                    onClick={() => setPending({ approval, approve: true })}
                  >
                    Aprovar
                  </Button>
                  <Button
                    variant="outlined"
                    size="small"
                    color="error"
                    startIcon={<Block />}
                    disabled={decideMutation.isPending}
                    sx={{ minHeight: 40 }}
                    onClick={() => setPending({ approval, approve: false })}
                  >
                    Rejeitar
                  </Button>
                </Stack>
              </StaggerReveal>
            </ListItem>
            );
          })}
        </List>
      ) : null}

      <Accordion variant="outlined" disableGutters sx={{ m: 2, mt: 0 }}>
        <AccordionSummary
          expandIcon={<ExpandMore />}
          id="historico-aprovacoes-cabecalho"
          aria-controls="historico-aprovacoes-conteudo"
        >
          <Typography variant="h5" component="h2">
            Histórico de decisões ({decided.length})
          </Typography>
        </AccordionSummary>
        <AccordionDetails id="historico-aprovacoes-conteudo">
          {decided.length === 0 ? (
            <Typography variant="body2" sx={{ color: 'text.secondary' }}>
              Ainda não decidiste nenhuma ação retida nesta campanha.
            </Typography>
          ) : (
            <List dense disablePadding>
              {decided.map((approval, index) => (
                <ListItem key={approval.id} divider={index < decided.length - 1} alignItems="flex-start">
                  <ListItemText
                    primary={
                      <Stack direction="row" spacing={1} useFlexGap sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {approval.action_name_en}
                        </Typography>
                        <Chip
                          size="small"
                          variant="outlined"
                          icon={approval.status === 'approved' ? <TaskAlt /> : <Block />}
                          sx={tintedChipSx(approval.status === 'approved' ? 'success' : 'error')}
                          label={approval.status === 'approved' ? 'aprovada' : 'rejeitada'}
                        />
                        {isDecisionBand(approval.band) ? <BandChip band={approval.band} /> : null}
                      </Stack>
                    }
                    secondary={
                      <Stack spacing={0.25} sx={{ mt: 0.5 }}>
                        <Typography variant="caption" component="span" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
                          {approval.action_id} · decidida {approval.decided_at ? formatDateTime(approval.decided_at) : formatDateTime(approval.ts)}
                          {approval.attempts ? ` · pedido ${approval.attempts} vez(es)` : ''}
                        </Typography>
                        {approval.note ? (
                          <Typography variant="caption" component="span">
                            nota: {approval.note}
                          </Typography>
                        ) : null}
                      </Stack>
                    }
                  />
                </ListItem>
              ))}
            </List>
          )}
        </AccordionDetails>
      </Accordion>

      <ApprovalConfirmDialog
        approval={pending?.approval ?? null}
        approve={pending?.approve ?? true}
        busy={decideMutation.isPending}
        onCancel={() => setPending(null)}
        onConfirm={confirmDecision}
      />
    </SectionCard>
  );
}
