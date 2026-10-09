import { useEffect, useMemo, useState, type ReactNode } from 'react';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';
import Link from '@mui/material/Link';
import Snackbar from '@mui/material/Snackbar';
import Stack from '@mui/material/Stack';
import Toolbar from '@mui/material/Toolbar';
import useMediaQuery from '@mui/material/useMediaQuery';
import { useTheme } from '@mui/material/styles';

import { errorMessage } from '../api/events';
import { useAnswerQuestion, useApiContext, useQuestions, useSetControl } from '../api/queries';
import type { AnswerQuestionRequest } from '../api/types';
import AutopilotConfirmDialog from '../components/AutopilotConfirmDialog';
import { PanelLoading } from '../components/PanelState';
import { QuestionsQueueDrawer } from '../components/QuestionsQueueDrawer';
import {
  controlPatchForKillSwitch,
  useAutonomyPersistence,
} from '../hooks/useAutonomyControl';
import NavDrawer from './NavDrawer';
import PanelTabs, { PanelIconTabs } from './PanelTabs';
import TopBar from './TopBar';
import {
  DEFAULT_PANEL_ID,
  PANELS,
  panelById,
  panelRegionId,
  panelTabId,
  type PanelId,
} from '../panels/registry';

const PANEL_IDS = new Set<string>(PANELS.map((panel) => panel.id));

/** Painel a partir do hash (`#/metricas`) — ligações diretas e refresh mantêm o painel. */
function readHashPanel(): PanelId {
  const hash = window.location.hash.replace(/^#\/?/, '');
  return PANEL_IDS.has(hash) ? (hash as PanelId) : DEFAULT_PANEL_ID;
}

/** Esqueleto da aplicação: barra global, navegação, fila de perguntas e painel ativo. */
export default function AppShell() {
  const theme = useTheme();
  const { campaignsStatus, campaignsError, reloadCampaigns, campaign, campaignId } = useApiContext();
  const [active, setActive] = useState<PanelId>(() => readHashPanel());
  const [queueOpen, setQueueOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [autopilotConfirm, setAutopilotConfirm] = useState(false);

  /**
   * Uma só navegação em ecrãs estreitos (auditoria F.2.2.03): abaixo de `md` a
   * navegação é a barra de separadores com rótulo; a partir de `md` é a navegação
   * lateral agrupada, com uma barra só-ícone como acesso rápido à fila de perguntas.
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
    () => (questionsQuery.data ?? []).filter((question) => question.status === 'pending'),
    [questionsQuery.data],
  );

  const answerMutation = useAnswerQuestion({
    onSuccess: () => setToast('Resposta enviada para a memória do agente.'),
    onError: (error) => setToast(`Não foi possível responder: ${errorMessage(error)}`),
  });

  const controlMutation = useSetControl({
    onError: (error) => setToast(`Não foi possível alterar o controlo: ${errorMessage(error)}`),
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
      <TopBar
        pendingQuestions={pending.length}
        onOpenQuestions={() => setQueueOpen(true)}
        onKillSwitch={handleKillSwitch}
        controlBusy={controlMutation.isPending}
      />
      <Stack direction="row" sx={{ flexGrow: 1, minHeight: 0 }}>
        {showNavDrawer ? (
          <NavDrawer active={active} onSelect={selectPanel} pendingQuestions={pending.length} />
        ) : null}
        <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Toolbar variant="dense" />
          <PanelTabs active={active} onSelect={selectPanel} />
          <PanelIconTabs active={active} onSelect={selectPanel} pendingQuestions={pending.length} />
          <Container
            component="main"
            id="main"
            maxWidth={false}
            sx={{ py: { xs: 2, md: 3 }, px: { xs: 2, md: 3 }, flexGrow: 1 }}
          >
            {/*
              `role=tabpanel` só quando existe mesmo um tablist visível (mobile). Em
              desktop a navegação é `nav` + `aria-current` e o painel é uma região.
            */}
            <Box
              id={panelRegionId(active)}
              {...(isTabbedNav
                ? { role: 'tabpanel', 'aria-labelledby': panelTabId(active) }
                : { role: 'region', 'aria-label': `${activePanel.title} — painel` })}
              tabIndex={-1}
            >
              {children}
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

      <Snackbar
        open={toast !== null}
        autoHideDuration={6000}
        onClose={(_event, reason) => {
          if (reason === 'clickaway') return;
          setToast(null);
        }}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert
          severity={toast?.startsWith('Não foi possível') ? 'error' : 'success'}
          variant="filled"
          role="status"
          onClose={() => setToast(null)}
          sx={{ width: '100%' }}
        >
          {toast}
        </Alert>
      </Snackbar>
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
