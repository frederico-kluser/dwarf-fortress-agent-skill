import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';
import Add from '@mui/icons-material/Add';
import PlayArrow from '@mui/icons-material/PlayArrow';

import {
  StaggerReveal,
  StaggerRevealHeadline,
  StaggerRevealItem,
} from '@/components/motion-ui/stagger-reveal';
import { errorMessage } from '../api/events';
import { formatDateTime, formatRelative } from '../api/format';
import {
  useAdventureStatus,
  useAdventures,
  useCancelAdventureTask,
  useLoadAdventure,
  useSystemStatus,
} from '../api/queries';
import type { AdventureKind, AdventureSave, AdventureTask } from '../api/types';
import AdventureTaskCard from '../components/AdventureTaskCard';
import AdventureWizard from '../components/AdventureWizard';
import PanelShell from '../components/PanelShell';
import { PanelError } from '../components/PanelState';
import {
  EmptyState,
  RunaGlyph,
  RunaRule,
  StoneTexture,
} from '../components/rpg';
import {
  RunaHover,
  RunaLoading,
  RunaSection,
  TOKEN,
  runaType,
} from '../components/RunaKit';
import { useNow } from '../hooks/useSparkSeries';

/** Rótulo PT do tipo de save (`active` = jogo iniciado; `world` = só mundo). */
const KIND_LABEL: Record<AdventureKind, string> = {
  active: 'jogo iniciado',
  world: 'mundo gerado',
};

/** Alvo tátil das ações primárias destas superfícies (auditoria v3, achado 6). */
const TARGET_SX = { minHeight: 40 } as const;

/** Cabeçalho do progresso conforme o tipo de tarefa. */
function taskHeading(task: AdventureTask): string {
  if (task.kind === 'new_world') return 'A criar o mundo';
  if (task.kind === 'new_character') return 'A criar o personagem';
  return 'A carregar a aventura';
}

/**
 * Painel «Aventura»: o estado da aventura atual, o escolhedor de saves existentes
 * e o wizard «Criar nova aventura» (mundo → personagem → progresso → pronto).
 * É o ponto de entrada da UI — sem aventura ativa, o estado vazio convida a
 * escolher ou criar.
 *
 * Tema «RPG medieval» (spec §5.1): o portão de entrada (hero) abre o painel com
 * a textura de pedra, o glifo `map`, o título em Cinzel `--text-hero` e as duas
 * saídas (criar · escolher); a régua runa separa o portão do conteúdo.
 */
export default function AventuraPanel() {
  const adventuresQuery = useAdventures();
  const statusQuery = useAdventureStatus();
  const systemQuery = useSystemStatus();
  const now = useNow(1000);
  const [wizardOpen, setWizardOpen] = useState(false);
  const [wizardMode, setWizardMode] = useState<'create' | 'character'>('create');
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error' | 'info'; text: string } | null>(null);
  const [dismissedTaskId, setDismissedTaskId] = useState<string | null>(null);
  const [lastLoad, setLastLoad] = useState<{ id: string; kind: AdventureKind } | null>(null);

  const loadMutation = useLoadAdventure({
    onSuccess: () => setFeedback(null),
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Não foi possível carregar: ${errorMessage(error)}` }),
  });
  const cancelTask = useCancelAdventureTask({
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Não foi possível cancelar: ${errorMessage(error)}` }),
  });

  const task = statusQuery.data?.task ?? null;
  const current = statusQuery.data?.current ?? adventuresQuery.data?.current ?? null;
  const running = task?.state === 'running';
  const taskVisible = task !== null && task.id !== dismissedTaskId && !wizardOpen;
  const saves = adventuresQuery.data?.adventures ?? [];
  /** O processo do jogo precisa de estar a correr para haver sessão (achado 2). */
  const gameOff = systemQuery.data ? !systemQuery.data.game.running : false;

  const continueAdventure = (save: AdventureSave) => {
    setLastLoad({ id: save.id, kind: save.kind });
    setDismissedTaskId(null);
    setFeedback(null);
    loadMutation.mutate({ adventure_id: save.id });
  };

  /**
   * «Entrar em sessão» só faz sentido com o jogo a correr: com o processo
   * desligado, o destino é o Controlo (secção Sistema) para o ligar — o aviso
   * junto do CTA e o aviso da secção Sistema explicam o porquê.
   */
  const enterSession = () => {
    if (window.location.hash !== (gameOff ? '#/controlo' : '#/perguntas')) {
      window.location.hash = gameOff ? '/controlo' : '/perguntas';
    }
  };

  /** Carregar um save `world` deixa o jogo sem personagem → compor com `new-character`. */
  const openCharacterWizard = () => {
    setWizardMode('character');
    setWizardOpen(true);
  };

  const openCreateWizard = () => {
    setWizardMode('create');
    setWizardOpen(true);
  };

  const scrollToChooser = () => {
    document.getElementById('aventuras-existentes')?.scrollIntoView({ behavior: 'smooth' });
  };

  /**
   * Tarefa terminada com mundo pronto mas SEM personagem (worldgen concluído ou
   * save `world` carregado): a composição do contrato é `new-character` a seguir.
   */
  const needsCharacter =
    task?.state === 'done' &&
    (task.kind === 'new_world' || (task.kind === 'load' && lastLoad?.kind === 'world'));

  return (
    <PanelShell
      title="Aventura"
      description="Escolhe uma aventura existente ou cria um mundo e um personagem novos — tudo pelo site, sem terminal."
      actions={
        <>
          <Chip
            size="small"
            variant="outlined"
            label={
              adventuresQuery.isLoading
                ? 'a carregar jogos guardados…'
                : `${saves.length} jogos guardados`
            }
          />
          <Button
            variant="contained"
            startIcon={<Add />}
            disabled={running}
            onClick={openCreateWizard}
          >
            Criar nova aventura
          </Button>
        </>
      }
    >
      {/* Portão de entrada (spec §5.1): textura de pedra, glifo `map` 48px,
          título Cinzel `--text-hero`, subtítulo ≤ 60ch e as duas saídas. */}
      <Box
        component="section"
        aria-labelledby="aventura-hero-titulo"
        sx={{
          position: 'relative',
          overflow: 'hidden',
          borderRadius: 'var(--radius-xl)',
          border: '1px solid',
          borderColor: TOKEN.border,
          backgroundColor: TOKEN.background,
        }}
      >
        <StoneTexture />
        <i className="canto-runa" data-canto="tl" aria-hidden="true" />
        <i className="canto-runa" data-canto="tr" aria-hidden="true" />
        <i className="canto-runa" data-canto="bl" aria-hidden="true" />
        <i className="canto-runa" data-canto="br" aria-hidden="true" />
        <StaggerReveal
          as="section"
          className="relative flex flex-col gap-4 px-6 py-8 max-sm:px-4 max-sm:py-6"
        >
          <StaggerRevealItem>
            <span style={{ color: TOKEN.accent }}>
              <RunaGlyph name="map" size={48} />
            </span>
          </StaggerRevealItem>
          <div style={runaType('hero')}>
            <StaggerRevealHeadline
              as="h2"
              ariaLabel={`Portão de entrada: ${current?.world_name ?? 'A Fortaleza-Vigia'}`}
              className="text-balance"
              id="aventura-hero-titulo"
            >
              {current?.world_name ?? 'A Fortaleza-Vigia'}
            </StaggerRevealHeadline>
          </div>
          <StaggerRevealItem as="p">
            <span
              className="block max-w-[60ch] text-pretty"
              style={{ ...runaType('body'), color: TOKEN.mutedForeground }}
            >
              Escolhe uma aventura existente ou cria um mundo e um personagem novos — com
              progresso ao vivo.
            </span>
          </StaggerRevealItem>
          <StaggerRevealItem>
            <Stack
              direction={{ xs: 'column', sm: 'row' }}
              spacing={1.5}
              useFlexGap
              sx={{ alignItems: { xs: 'stretch', sm: 'center' } }}
            >
              <Button
                variant="contained"
                startIcon={<Add />}
                disabled={running}
                sx={TARGET_SX}
                onClick={openCreateWizard}
              >
                Criar nova aventura
              </Button>
              <Button variant="outlined" sx={TARGET_SX} onClick={scrollToChooser}>
                Escolher existente
              </Button>
            </Stack>
          </StaggerRevealItem>
        </StaggerReveal>
      </Box>

      <RunaRule />

      <Stack spacing={2}>
        {feedback ? (
          <Alert
            severity={feedback.tone === 'info' ? 'info' : feedback.tone}
            role="status"
            onClose={() => setFeedback(null)}
          >
            {feedback.text}
          </Alert>
        ) : null}

        {running ? (
          <Alert severity="info" role="status">
            Há uma tarefa de aventura em curso — o resto das ações fica bloqueado até terminar ou
            ser cancelada (o contrato permite só uma tarefa de cada vez).
          </Alert>
        ) : null}

        {/* Cartão de estado: a aventura atual ou o estado vazio convidativo. */}
        <RunaSection
          title="Aventura atual"
          subtitle="O que está a correr agora neste mundo"
          glyph="map"
          action={
            current ? (
              <Button
                variant="outlined"
                startIcon={<PlayArrow />}
                sx={TARGET_SX}
                onClick={enterSession}
              >
                Entrar em sessão
              </Button>
            ) : undefined
          }
        >
          {adventuresQuery.isLoading || statusQuery.isLoading ? (
            <RunaLoading label="A carregar a aventura…" />
          ) : adventuresQuery.isError ? (
            <PanelError
              message={errorMessage(adventuresQuery.error)}
              onRetry={() => void adventuresQuery.refetch()}
            />
          ) : current ? (
            <Stack spacing={1}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <Typography variant="h3" component="p" sx={{ fontWeight: 700 }}>
                  {current.world_name}
                </Typography>
                <Chip
                  size="small"
                  color={current.alive ? 'success' : 'error'}
                  variant="outlined"
                  icon={
                    <RunaGlyph name={current.alive ? 'shield' : 'alert'} size={14} />
                  }
                  label={current.alive ? 'aventureiro vivo' : 'aventureiro morto'}
                />
              </Stack>
              <Typography variant="body1">
                Aventureiro: <strong>{current.adventurer_name}</strong>
                {current.adventure_id ? (
                  <>
                    {' · jogo guardado '}
                    <span style={runaType('data')}>«{current.adventure_id}»</span>
                  </>
                ) : null}
              </Typography>
              {gameOff ? (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  O jogo está desligado — «Entrar em sessão» leva-te ao Controlo para o ligar.
                </Typography>
              ) : null}
              {!current.alive ? (
                <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                  O registo do jogo marca a morte do aventureiro (leitura best-effort). Podes
                  carregar outro jogo guardado ou criar uma aventura nova.
                </Typography>
              ) : null}
            </Stack>
          ) : (
            /* Estado vazio «a masmorra ainda não foi explorada» (kit `rpg/`,
               §5.7): mensagem do painel + 1 CTA concreta → wizard. */
            <div role="status">
              <EmptyState
                glyph="map"
                title="Nenhuma aventura ativa — escolhe uma existente ou cria uma nova"
                body="Uma aventura é um mundo + um personagem. Continua um jogo guardado que já tens ou deixa o agente conduzir a criação de um mundo novo, com o progresso a acontecer aqui. «Escolher existente» está no portão acima."
                action={{ label: 'Criar nova aventura', onClick: openCreateWizard }}
              />
            </div>
          )}
        </RunaSection>

        {/* Progresso de tarefas lançadas fora do wizard (ex.: continuar um jogo guardado). */}
        {taskVisible ? (
          <AdventureTaskCard
            task={task}
            heading={taskHeading(task)}
            contextLine={lastLoad ? `jogo guardado «${lastLoad.id}»` : undefined}
            updatedAt={statusQuery.dataUpdatedAt ? new Date(statusQuery.dataUpdatedAt).toISOString() : null}
            refreshing={statusQuery.isFetching}
            onRefresh={() => void statusQuery.refetch()}
            cancelBusy={cancelTask.isPending}
            onConfirmCancel={() => cancelTask.mutate()}
            onRetry={
              lastLoad
                ? () => {
                    setDismissedTaskId(null);
                    loadMutation.mutate({ adventure_id: lastLoad.id });
                  }
                : undefined
            }
            onDismiss={() => setDismissedTaskId(task.id)}
            doneActions={
              needsCharacter ? (
                <>
                  <Button
                    color="primary"
                    variant="contained"
                    startIcon={<Add />}
                    sx={TARGET_SX}
                    onClick={openCharacterWizard}
                  >
                    Criar personagem
                  </Button>
                  <Typography variant="caption" sx={{ color: 'text.secondary', maxWidth: 220 }}>
                    O mundo está pronto — falta o personagem para entrar em sessão.
                  </Typography>
                </>
              ) : (
                <Button
                  color="success"
                  variant="contained"
                  startIcon={<PlayArrow />}
                  sx={TARGET_SX}
                  onClick={enterSession}
                >
                  Entrar em sessão
                </Button>
              )
            }
          />
        ) : null}

        {/* Escolhedor: os jogos guardados da pasta do DF, com a ação primária por linha. */}
        <Box id="aventuras-existentes">
          <RunaSection
            title="Aventuras existentes"
            subtitle="Jogos guardados na pasta do jogo (data/save) — «Continuar aventura» carrega o escolhido"
            glyph="gem"
            flush
          >
            {adventuresQuery.isLoading ? (
              <Box sx={{ p: 2 }}>
                <RunaLoading label="A carregar jogos guardados…" />
              </Box>
            ) : adventuresQuery.isError ? (
              <Box sx={{ p: 2 }}>
                <PanelError
                  message={errorMessage(adventuresQuery.error)}
                  onRetry={() => void adventuresQuery.refetch()}
                />
              </Box>
            ) : saves.length === 0 ? (
              <EmptyState
                glyph="map"
                title="Nenhum jogo guardado encontrado na pasta do jogo."
                body="Cria uma nova aventura para gerar o primeiro mundo — ele aparece aqui assim que terminar."
                action={{ label: 'Criar nova aventura', onClick: openCreateWizard }}
              />
            ) : (
              <List disablePadding aria-label="Aventuras guardadas">
                {saves.map((save, index) => {
                  const inUse = current?.adventure_id === save.id;
                  return (
                    <ListItem
                      key={save.id}
                      divider={index < saves.length - 1}
                      sx={{ display: 'block', py: 1.5 }}
                    >
                      {/*
                        Cartão de jogo guardado: cada entrada revela-se em
                        cascata na chegada (Motion UI `stagger-reveal`) e
                        levanta 2px no hover (token `snap`, §6).
                      */}
                      <RunaHover className="rounded-md">
                        <StaggerReveal className="flex flex-wrap items-center justify-between gap-2">
                          <div className="min-w-0 flex-1">
                            <div className="flex flex-wrap items-center gap-2">
                              <span style={{ color: TOKEN.accent }}>
                                <RunaGlyph name="map" size={16} />
                              </span>
                              <StaggerRevealHeadline
                                as="h3"
                                className="text-xs font-semibold"
                                ariaLabel={`Jogo guardado ${save.name}`}
                              >
                                {save.name}
                              </StaggerRevealHeadline>
                              <Chip
                                size="small"
                                variant="outlined"
                                color={save.kind === 'active' ? 'success' : 'default'}
                                icon={
                                  <RunaGlyph
                                    name={save.kind === 'active' ? 'flame' : 'map'}
                                    size={14}
                                  />
                                }
                                label={KIND_LABEL[save.kind]}
                              />
                            </div>
                            <StaggerRevealItem>
                              <Typography
                                variant="caption"
                                component="p"
                                sx={{ color: 'text.secondary', display: 'block', mt: 0.5 }}
                              >
                                <span style={runaType('data')}>
                                  id: {save.id} · guardado {formatDateTime(save.saved_at)} (
                                  {formatRelative(save.saved_at, now)})
                                </span>
                              </Typography>
                            </StaggerRevealItem>
                          </div>
                          <Button
                            variant={inUse ? 'outlined' : 'contained'}
                            disabled={running || inUse || loadMutation.isPending}
                            startIcon={<PlayArrow />}
                            sx={TARGET_SX}
                            aria-label={`Continuar aventura ${save.name}`}
                            onClick={() => continueAdventure(save)}
                          >
                            {inUse ? 'Em uso' : 'Continuar aventura'}
                          </Button>
                        </StaggerReveal>
                      </RunaHover>
                    </ListItem>
                  );
                })}
              </List>
            )}
          </RunaSection>
        </Box>

        <Typography variant="caption" sx={{ color: 'text.secondary' }}>
          Leitura de GET /api/campaigns/{'{id}'}/adventures · o progresso das tarefas vem de
          GET .../adventures/status + eventos WebSocket adventure_task.
        </Typography>
      </Stack>

      <AdventureWizard
        key={wizardMode}
        open={wizardOpen}
        mode={wizardMode}
        onClose={() => {
          setWizardOpen(false);
          // O wizard já mostrou o desfecho: não repetimos o cartão da tarefa
          // terminada no painel (o cartão de estado passa a ser a fonte da verdade).
          if (task && task.state !== 'running') setDismissedTaskId(task.id);
        }}
      />
    </PanelShell>
  );
}
