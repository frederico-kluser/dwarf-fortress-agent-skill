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

import { errorMessage } from '../api/events';
import { useAnswerQuestion, useApiContext, useQuestions, useSetControl } from '../api/queries';
import type { AnswerQuestionRequest } from '../api/types';
import { PanelLoading } from '../components/PanelState';
import { QuestionsQueueDrawer } from '../components/QuestionsQueueDrawer';
import NavDrawer, { MobileNavDrawer } from './NavDrawer';
import PanelTabs from './PanelTabs';
import TopBar from './TopBar';
import {
  DEFAULT_PANEL_ID,
  PANELS,
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
  const { campaignsStatus, campaignsError, reloadCampaigns, campaign } = useApiContext();

  const [active, setActive] = useState<PanelId>(() => readHashPanel());
  const [navOpen, setNavOpen] = useState(false);
  const [queueOpen, setQueueOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

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

  const handleAnswer = (questionId: string, body: AnswerQuestionRequest) => {
    answerMutation.mutate({ qid: questionId, ...body });
  };

  const handleKillSwitch = (next: boolean) => {
    // Acionar o kill-switch implica pausar; desligá-lo devolve o modo anterior.
    const currentMode = campaign?.control.mode ?? 'autopilot';
    controlMutation.mutate({
      mode: next ? 'paused' : currentMode === 'paused' ? 'autopilot' : currentMode,
      kill_switch: next,
    });
  };

  const activePanel = PANELS.find((panel) => panel.id === active) ?? PANELS[0];

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
        onOpenNav={() => setNavOpen(true)}
        onKillSwitch={handleKillSwitch}
        controlBusy={controlMutation.isPending}
      />
      <Stack direction="row" sx={{ flexGrow: 1, minHeight: 0 }}>
        <NavDrawer active={active} onSelect={selectPanel} />
        <MobileNavDrawer
          open={navOpen}
          onClose={() => setNavOpen(false)}
          active={active}
          onSelect={selectPanel}
        />
        <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Toolbar variant="dense" />
          <PanelTabs active={active} onSelect={selectPanel} />
          <Container
            component="main"
            id="main"
            maxWidth={false}
            sx={{ py: { xs: 2, md: 3 }, px: { xs: 2, md: 3 }, flexGrow: 1 }}
          >
            <Box
              role="tabpanel"
              id={panelRegionId(active)}
              aria-labelledby={panelTabId(active)}
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
