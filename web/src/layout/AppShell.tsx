import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';
import Link from '@mui/material/Link';
import Stack from '@mui/material/Stack';
import Toolbar from '@mui/material/Toolbar';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';
import { motion } from 'motion/react';

import {
  Toast,
  ToastStack,
} from '@/components/motion-ui/toast-stack';
import {
  useMotionUITheme,
  useMotionUITransition,
} from '@/components/motion-ui/ui-theme';
import { errorMessage } from '../api/events';
import { useAnswerQuestion, useApiContext, useApprovals, useQuestions, useSetControl } from '../api/queries';
import type { AnswerQuestionRequest } from '../api/types';
import AutopilotConfirmDialog from '../components/AutopilotConfirmDialog';
import { PanelLoading } from '../components/PanelState';
import { QuestionsQueueDrawer } from '../components/QuestionsQueueDrawer';
import {
  controlPatchForKillSwitch,
  useAutonomyPersistence,
} from '../hooks/useAutonomyControl';
import NavDrawer from './NavDrawer';
import PanelTabs, { NavQuickAccess } from './PanelTabs';
import TopBar from './TopBar';
import { panelMobileTabId } from './navIds';
import {
  DEFAULT_PANEL_ID,
  PANELS,
  panelById,
  panelRegionId,
  type PanelId,
} from '../panels/registry';

const PANEL_IDS = new Set<string>(PANELS.map((panel) => panel.id));

/** Entrada na fila de toasts (Motion UI `toast-stack`). */
interface ToastEntry {
  id: number;
  text: string;
  tone: 'success' | 'error';
}

const TOAST_TIMEOUT_MS = 6000;

function ToastFace({
  entry,
  onDismiss,
}: {
  entry: ToastEntry;
  onDismiss: (id: number) => void;
}) {
  return (
    <div
      role="status"
      className={
        entry.tone === 'error'
          ? 'pointer-events-auto flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/10 p-3 text-sm text-foreground shadow-[var(--shadow-2)]'
          : 'pointer-events-auto flex items-start gap-2 rounded-lg border border-success/40 bg-success/10 p-3 text-sm text-foreground shadow-[var(--shadow-2)]'
      }
    >
      <p className="min-w-0 flex-1 text-pretty">{entry.text}</p>
      <button
        type="button"
        aria-label="Fechar notificação"
        className="grid h-10 w-10 shrink-0 place-items-center rounded-full text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
        onClick={() => onDismiss(entry.id)}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
          <path d="M18 6 6 18M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

/** Painel a partir do hash (`#/metricas`) — ligações diretas e refresh mantêm o painel. */
function readHashPanel(): PanelId {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return PANEL_IDS.has(hash) ? (hash as PanelId) : DEFAULT_PANEL_ID;
}

/** Esqueleto da aplicação: barra global, navegação, fila de perguntas e painel ativo. */
export default function AppShell() {
  const theme = useTheme();
  const uiTheme = useMotionUITheme();
  const uiTransition = useMotionUITransition('ui');
  const { campaignsStatus, campaignsError, reloadCampaigns, campaign, campaignId } = useApiContext();
  const [active, setActive] = useState<PanelId>(() => readHashPanel());
  const [queueOpen, setQueueOpen] = useState(false);
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const [autopilotConfirm, setAutopilotConfirm] = useState(false);
  const toastSeq = useRef(0);
  const toastTimers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  /** Descarta um toast da pilha e limpa o respetivo temporizador. */
  const dismissToast = useCallback((id: number) => {
    const timer = toastTimers.current.get(id);
    if (timer) clearTimeout(timer);
    toastTimers.current.delete(id);
    setToasts((prev) => prev.filter((entry) => entry.id !== id));
  }, []);

  /** Enfileira um toast (chegadas WS, resultados de ação) na pilha Motion UI. */
  const pushToast = useCallback(
    (text: string) => {
      const id = ++toastSeq.current;
      const entry: ToastEntry = {
        id,
        text,
        tone: text.startsWith('Não foi possível') ? 'error' : 'success',
      };
      setToasts((prev) => [entry, ...prev].slice(0, 6));
      toastTimers.current.set(
        id,
        setTimeout(() => dismissToast(id), TOAST_TIMEOUT_MS),
      );
    },
    [dismissToast],
  );

  useEffect(
    () => () => {
      for (const timer of toastTimers.current.values()) clearTimeout(timer);
      toastTimers.current.clear();
    },
    [],
  );

  /**
   * Uma só navegação por breakpoint (auditorias F.2.2.03/F.1.1.04): abaixo de
   * `md` a navegação primária é a tira de separadores agrupada por NAV_GROUPS;
   * a partir de `md` é a navegação lateral agrupada, com um «acesso rápido» de
   * dois atalhos («Perguntas»/«Controlo») — nunca uma lista paralela de 9.
   */
  const isTabbedNav = useMediaQuery(theme.breakpoints.down('md'));
  const showNavDrawer = !isTabbedNav;

  // Navegação por hash: botão "voltar" do browser e links diretos funcionam.
  useEffect(() => {
    const onHashChange = () => setActive(readHashPanel());
    window.addEventListener('hashchange', onHashChange);
    return () => window.removeEventListener('hashchange', onHashChange);
  }, []);

  const selectPanel = (id: PanelId) => {
    setActive(id);
    if (window.location.hash !== `#/${id}`) window.location.hash = `/${id}`;
  };

  const questionsQuery = useQuestions();
  const pending = useMemo(
    () => questionsQuery.items.filter((question) => question.status === 'pending'),
    [questionsQuery.items],
  );

  /**
   * Anúncio da chegada (auditoria v2, achado 5): uma pergunta nova ou um
   * re-pedido da mesma ação (`attempts` a subir) produz um toast `role=status`;
   * o primeiro carregamento não toasta — o resumo vive na região `aria-live`.
   */
  const seenQuestions = useRef<Map<string, number> | null>(null);
  useEffect(() => {
    const seen = seenQuestions.current;
    const next = new Map<string, number>();
    for (const question of pending) next.set(question.id, question.attempts ?? 1);
    if (seen) {
      const fresh = pending.filter((question) => !seen.has(question.id));
      const bumped = pending.filter((question) => {
        const previous = seen.get(question.id);
        return previous !== undefined && (question.attempts ?? 1) > previous;
      });
      if (fresh.length === 1) {
        pushToast(`Nova pergunta do agente: ${fresh[0]?.question ?? ''}`);
      } else if (fresh.length > 1) {
        pushToast(`${fresh.length} novas perguntas do agente — aguardam resposta`);
      } else if (bumped.length > 0) {
        const question = bumped[0];
        pushToast(
          `Re-pedido do agente: «${question?.question ?? ''}» (${question?.attempts ?? 1} vezes)`,
        );
      }
    }
    seenQuestions.current = next;
  }, [pending, pushToast]);

  // Ações retidas do modo `checkpoint` — badge próprio na barra superior.
  const approvalsQuery = useApprovals();
  const pendingApprovals = useMemo(
    () => (approvalsQuery.data ?? []).filter((approval) => approval.status === 'pending').length,
    [approvalsQuery.data],
  );

  const answerMutation = useAnswerQuestion({
    onSuccess: () => pushToast('Resposta enviada para a memória do agente.'),
    onError: (error) => pushToast(`Não foi possível responder: ${errorMessage(error)}`),
  });

  const controlMutation = useSetControl({
    onError: (error) => pushToast(`Não foi possível alterar o controlo: ${errorMessage(error)}`),
  });

  const mode = campaign?.control.mode ?? 'paused';
  const { restorableMode } = useAutonomyPersistence(campaignId, mode);

  const handleAnswer = (questionId: string, body: AnswerQuestionRequest) => {
    answerMutation.mutate({ qid: questionId, ...body });
  };

  /**
   * Acionar o kill-switch implica pausar; desligá-lo devolve o último modo
   * NÃO-pausado guardado (nunca `autopilot` por omissão — auditoria C.1.4.01).
   */
  const handleKillSwitch = (next: boolean) => {
    const patch = controlPatchForKillSwitch(next, mode, restorableMode);
    if (!next && patch.mode === 'autopilot') {
      setAutopilotConfirm(true);
      return;
    }
    controlMutation.mutate(patch);
  };

  const confirmAutopilot = () => {
    controlMutation.mutate({ mode: 'autopilot', kill_switch: false });
    setAutopilotConfirm(false);
  };

  const activePanel = panelById(active);

  const shell = (children: ReactNode) => (
    <Box sx={{ display: 'flex', minHeight: '100dvh', flexDirection: 'column' }}>
      <Link
        href="#main"
        sx={{
          position: 'absolute',
          left: -9999,
          top: 8,
          zIndex: (theme) => theme.zIndex.tooltip + 1,
          px: 2,
          py: 1,
          borderRadius: 1,
          bgcolor: 'background.paper',
          '&:focus': { left: 8 },
        }}
      >
        Saltar para o conteúdo principal
      </Link>
      {/*
        Região `aria-live` permanente (auditoria v2, achado 5): o resumo da fila
        de perguntas é anunciado a tecnologias de assistência, mesmo quando o
        utilizador está noutro painel.
      */}
      <Box
        role="status"
        aria-live="polite"
        sx={{
          position: 'absolute',
          width: '1px',
          height: '1px',
          p: 0,
          m: -1,
          overflow: 'hidden',
          clip: 'rect(0 0 0 0)',
          whiteSpace: 'nowrap',
          border: 0,
        }}
      >
        {pending.length === 0
          ? 'Sem perguntas pendentes.'
          : `${pending.length} pergunta(s) do agente à espera de resposta.`}
      </Box>
      <TopBar
        pendingQuestions={pending.length}
        onOpenQuestions={() => setQueueOpen(true)}
        pendingApprovals={pendingApprovals}
        onOpenApprovals={() => selectPanel('controlo')}
        onKillSwitch={handleKillSwitch}
        controlBusy={controlMutation.isPending}
      />
      <Stack direction="row" sx={{ flexGrow: 1, minHeight: 0 }}>
        {showNavDrawer ? (
          <NavDrawer
            active={active}
            onSelect={selectPanel}
            pendingQuestions={pending.length}
            pendingApprovals={pendingApprovals}
          />
        ) : null}
        <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Toolbar variant="dense" />
          <PanelTabs
            active={active}
            onSelect={selectPanel}
            pendingQuestions={pending.length}
            pendingApprovals={pendingApprovals}
          />
          <NavQuickAccess
            active={active}
            onSelect={selectPanel}
            pendingQuestions={pending.length}
            pendingApprovals={pendingApprovals}
          />
          <Container
            component="main"
            id="main"
            maxWidth={false}
            sx={{ py: { xs: 2, md: 3 }, px: { xs: 2, md: 3 }, flexGrow: 1 }}
          >
            {/*
              `role=tabpanel` só quando existe mesmo um tablist visível (mobile). Em
              desktop a navegação é `nav` + `aria-labelledby` para o separador mobile
              único (IDs sem duplicados, auditoria H.1.1.01) e o painel é uma região.
            */}
            <Box
              id={panelRegionId(active)}
              {...(isTabbedNav
                ? { role: 'tabpanel', 'aria-labelledby': panelMobileTabId(active) }
                : { role: 'region', 'aria-label': `${activePanel.title} — painel` })}
              tabIndex={-1}
            >
              {/*
                Transição painel→painel: entrada em fade + subida curta, só em
                `transform`/`opacity`, com os tokens do tema e postura `calm`/
                `off` para reduced motion.
              */}
              <motion.div
                key={active}
                initial={
                  uiTheme.motionMode === 'off'
                    ? false
                    : {
                        opacity: 0,
                        transform: `translateY(${
                          uiTheme.motionMode === 'calm' ? 0 : uiTheme.travel.enter
                        }px)`,
                      }
                }
                animate={{ opacity: 1, transform: 'translateY(0px)' }}
                transition={{ ...uiTransition }}
              >
                {children}
              </motion.div>
            </Box>
          </Container>
        </Box>
      </Stack>

      <QuestionsQueueDrawer
        open={queueOpen}
        onClose={() => setQueueOpen(false)}
        questions={pending}
        onAnswer={handleAnswer}
        busyId={answerMutation.isPending ? (answerMutation.variables?.qid ?? null) : null}
        onOpenPanel={() => {
          selectPanel('perguntas');
          setQueueOpen(false);
        }}
      />

      <AutopilotConfirmDialog
        open={autopilotConfirm}
        from={mode}
        reason="disarm"
        busy={controlMutation.isPending}
        onCancel={() => setAutopilotConfirm(false)}
        onConfirm={confirmAutopilot}
      />

      {/* z-index acima dos sheets (z-50) para o toast nunca ficar por baixo. */}
      <ToastStack className="z-[60]!">
        {toasts.map((entry) => (
          <Toast key={entry.id}>
            <ToastFace entry={entry} onDismiss={dismissToast} />
          </Toast>
        ))}
      </ToastStack>
    </Box>
  );

  if (campaignsStatus === 'loading') {
    return shell(<PanelLoading label="A carregar campanhas…" />);
  }

  if (campaignsStatus === 'error') {
    return shell(
      <Alert
        severity="error"
        action={
          <Button color="inherit" size="small" onClick={reloadCampaigns}>
            Tentar de novo
          </Button>
        }
      >
        <AlertTitle>Sem ligação ao backend</AlertTitle>
        {campaignsError ?? 'Erro desconhecido.'} — confirma que o FastAPI está a correr em{' '}
        <code>http://localhost:8000</code> e que <code>VITE_API_BASE</code> aponta para lá. Sem
        backend, a UI corre com dados simulados se <code>VITE_API_BASE</code> não estiver definida.
      </Alert>,
    );
  }

  if (!campaign) {
    return shell(
      <Alert severity="info" role="status">
        <AlertTitle>Nenhuma campanha</AlertTitle>
        Cria uma campanha no painel <strong>Controlo</strong> para começar.
      </Alert>,
    );
  }

  return shell(<activePanel.Component />);
}
