import { useEffect, useRef, useState } from 'react';
import Accordion from '@mui/material/Accordion';
import AccordionDetails from '@mui/material/AccordionDetails';
import AccordionSummary from '@mui/material/AccordionSummary';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';
import FormControlLabel from '@mui/material/FormControlLabel';
import MenuItem from '@mui/material/MenuItem';
import Stack from '@mui/material/Stack';
import Switch from '@mui/material/Switch';
import TextField from '@mui/material/TextField';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';
import Tooltip from '@mui/material/Tooltip';
import Typography from '@mui/material/Typography';
import Add from '@mui/icons-material/Add';
import CheckCircle from '@mui/icons-material/CheckCircle';
import ExpandMore from '@mui/icons-material/ExpandMore';
import PlayArrow from '@mui/icons-material/PlayArrow';
import Stop from '@mui/icons-material/Stop';
import Tune from '@mui/icons-material/Tune';
import Warning from '@mui/icons-material/Warning';

import {
  StaggerReveal,
  StaggerRevealHeadline,
  StaggerRevealItem,
} from '@/components/motion-ui/stagger-reveal';
import { errorMessage } from '../api/events';
import {
  useAdventureStatus,
  useCancelAdventureTask,
  useStartNewCharacter,
  useStartNewWorld,
  useSystemStatus,
} from '../api/queries';
import type { NewCharacterRequest, NewWorldRequest, WorldParams } from '../api/types';
import AdventureTaskCard from './AdventureTaskCard';
import { PanelNotice } from './PanelState';
import WizardStepper from './WizardStepper';

/** Raças do contrato (`Select <race>`) — valores EN, rótulos PT. */
const RACES: { value: string; label: string }[] = [
  { value: 'human', label: 'Humano' },
  { value: 'dwarf', label: 'Anão' },
  { value: 'elf', label: 'Elfo' },
  { value: 'goblin', label: 'Goblin' },
];

/** Passos lógicos por modo — `create` inclui o mundo; `character` compõe num save `world`. */
type StepKey = 'mundo' | 'personagem' | 'progresso' | 'pronto';

const STEPS_CREATE: StepKey[] = ['mundo', 'personagem', 'progresso', 'pronto'];
const STEPS_CHARACTER: StepKey[] = ['personagem', 'progresso', 'pronto'];

const STEP_LABELS: Record<StepKey, string> = {
  mundo: 'Mundo',
  personagem: 'Personagem',
  progresso: 'Progresso',
  pronto: 'Pronto',
};

export interface AdventureWizardProps {
  open: boolean;
  /**
   * `create` (por omissão) = mundo + personagem (4 passos). `character` = só o
   * personagem (3 passos) — usado para compor depois de carregar um save `world`.
   */
  mode?: 'create' | 'character';
  /** Fecha o wizard (já sem tarefa a correr, ou por decisão confirmada). */
  onClose: () => void;
}

/**
 * Wizard «Criar nova aventura» (Mundo → Personagem → Progresso → Pronto) ou
 * «Criar personagem» (Personagem → Progresso → Pronto). O passo de progresso
 * mostra o progresso ao vivo da cadeia de tarefas (WS `adventure_task` + `GET
 * .../adventures/status`), com cancelamento sempre disponível.
 */
export default function AdventureWizard({ open, mode = 'create', onClose }: AdventureWizardProps) {
  const statusQuery = useAdventureStatus();
  const systemQuery = useSystemStatus();
  const task = statusQuery.data?.task ?? null;
  const gameOff = systemQuery.data ? !systemQuery.data.game.running : false;

  const steps: StepKey[] = mode === 'create' ? STEPS_CREATE : STEPS_CHARACTER;
  const [step, setStep] = useState(0);
  const current: StepKey = steps[Math.min(step, steps.length - 1)] ?? 'mundo';

  const [worldName, setWorldName] = useState('Terras de Valdram');
  const [params, setParams] = useState<WorldParams>('quick');
  const [randomize, setRandomize] = useState(true);
  const [charName, setCharName] = useState('Urist');
  const [race, setRace] = useState('human');
  const [origin, setOrigin] = useState('');
  const [phase, setPhase] = useState<'world' | 'character'>('world');
  const [closeConfirm, setCloseConfirm] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  const startedRef = useRef(false);
  const handledTaskRef = useRef<string | null>(null);

  const worldBody: NewWorldRequest = {
    world_name: worldName.trim() || undefined,
    params,
  };
  const characterBody: NewCharacterRequest = randomize
    ? { randomize: true }
    : {
        name: charName.trim() || undefined,
        race: race || undefined,
        origin: origin.trim() || undefined,
        randomize: false,
      };

  const startWorld = useStartNewWorld({
    onError: (error) => setFeedback(`Não foi possível arrancar a criação do mundo: ${errorMessage(error)}`),
  });
  const startCharacter = useStartNewCharacter({
    onError: (error) =>
      setFeedback(`Não foi possível arrancar a criação do personagem: ${errorMessage(error)}`),
  });
  const cancelTask = useCancelAdventureTask({
    onSuccess: () => setFeedback(null),
    onError: (error) => setFeedback(`Não foi possível cancelar: ${errorMessage(error)}`),
  });

  // Abertura limpa: cada invocação recomeça do primeiro passo do modo.
  useEffect(() => {
    if (!open) return;
    setStep(0);
    setPhase(mode === 'create' ? 'world' : 'character');
    startedRef.current = false;
    handledTaskRef.current = null;
    setFeedback(null);
    setCloseConfirm(false);
  }, [open, mode]);

  // Entrada no passo «Progresso»: arranca a cadeia (mundo → personagem, ou só personagem).
  useEffect(() => {
    if (!open || current !== 'progresso' || startedRef.current) return;
    startedRef.current = true;
    if (mode === 'create') {
      setPhase('world');
      startWorld.mutate(worldBody);
    } else {
      setPhase('character');
      startCharacter.mutate(characterBody);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- arranque único por abertura
  }, [open, current]);

  // Cadeia automática: mundo concluído → personagem; personagem concluído → pronto.
  // Só reage às tarefas DESTE wizard e da fase atual: uma tarefa concluída que
  // estava na cache ao abrir nunca pode saltar passos.
  useEffect(() => {
    if (!open || current !== 'progresso' || !task || task.state !== 'done') return;
    if (handledTaskRef.current === task.id) return;
    if (phase === 'world' && task.kind !== 'new_world') return;
    if (phase === 'character' && task.kind !== 'new_character') return;
    handledTaskRef.current = task.id;
    if (phase === 'world') {
      setPhase('character');
      startCharacter.mutate(characterBody);
    } else {
      setStep(steps.indexOf('pronto'));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- depende do id da tarefa
  }, [open, current, phase, task]);

  const running = task?.state === 'running';
  const retry = () => {
    handledTaskRef.current = null;
    setFeedback(null);
    if (phase === 'world') startWorld.mutate(worldBody);
    else startCharacter.mutate(characterBody);
  };

  const requestClose = () => {
    if (running) {
      setCloseConfirm(true);
      return;
    }
    onClose();
  };

  /**
   * Entrar em sessão exige o jogo a correr (auditoria v3, achado 2): com o jogo
   * desligado, o destino certo é o Controlo — onde está o botão «Ligar o jogo».
   */
  const enterSession = () => {
    onClose();
    if (window.location.hash !== (gameOff ? '#/controlo' : '#/perguntas')) {
      window.location.hash = gameOff ? '/controlo' : '/perguntas';
    }
  };

  const enterControl = () => {
    onClose();
    if (window.location.hash !== '#/controlo') window.location.hash = '/controlo';
  };

  const summary =
    mode === 'create'
      ? `Vai criar o mundo «${worldName.trim() || 'nome gerado pelo jogo'}» (${
          params === 'quick' ? 'parâmetros rápidos' : 'parâmetros padrão'
        })${
          randomize
            ? ' com um personagem gerado ao acaso.'
            : ` com o personagem «${charName.trim() || 'sem nome (o jogo escolhe)'}».`
        }`
      : randomize
        ? 'Vai criar um personagem gerado ao acaso no mundo carregado.'
        : `Vai criar o personagem «${charName.trim() || 'sem nome (o jogo escolhe)'}» no mundo carregado.`;

  return (
    <>
      <Dialog
        open={open}
        onClose={requestClose}
        maxWidth="sm"
        fullWidth
        aria-labelledby="aventura-wizard-titulo"
        aria-describedby="aventura-wizard-descricao"
      >
        <DialogTitle id="aventura-wizard-titulo">
          {mode === 'create' ? 'Criar nova aventura' : 'Criar personagem'}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ pt: 1 }}>
            <Typography id="aventura-wizard-descricao" variant="caption" sx={{ color: 'text.secondary' }}>
              {mode === 'create'
                ? 'Quatro passos: escolhe o mundo, define o personagem, acompanha o progresso e entra em sessão. Tudo a partir do site — sem terminal.'
                : 'Três passos: define o personagem, acompanha o progresso e entra em sessão — o mundo carregado fica pronto a jogar.'}
            </Typography>

            <WizardStepper
              steps={steps.map((key) => STEP_LABELS[key])}
              active={step}
              label="Passos da criação de aventura"
            />

            {feedback ? (
              <Alert severity="error" role="alert" onClose={() => setFeedback(null)}>
                {feedback}
              </Alert>
            ) : null}

            {current === 'mundo' ? (
              <StaggerReveal className="flex flex-col gap-4">
                <StaggerRevealHeadline as="h2" className="text-sm font-bold text-foreground">
                  1 · Mundo
                </StaggerRevealHeadline>
                <StaggerRevealItem>
                  <TextField
                    label="Nome do mundo"
                    value={worldName}
                    onChange={(event) => setWorldName(event.target.value)}
                    fullWidth
                    helperText="É escrito no campo de nome do jogo. É um passo suave: nunca aborta a criação do mundo."
                  />
                </StaggerRevealItem>
                <StaggerRevealItem>
                  <Box>
                    <Typography variant="subtitle2" id="parametros-mundo-rotulo">
                      Parâmetros do mundo
                    </Typography>
                    <ToggleButtonGroup
                      exclusive
                      value={params}
                      onChange={(_event, next: WorldParams | null) => {
                        if (next !== null) setParams(next);
                      }}
                      aria-labelledby="parametros-mundo-rotulo"
                      sx={{ mt: 1 }}
                    >
                      <Tooltip title="Tenta encurtar a geração do mundo clicando em tamanho/história curtos quando visíveis">
                        <ToggleButton value="quick">Rápido</ToggleButton>
                      </Tooltip>
                      <Tooltip title="Aceita os parâmetros do ecrã tal como estão (o fluxo provado)">
                        <ToggleButton value="default">Padrão</ToggleButton>
                      </Tooltip>
                    </ToggleButtonGroup>
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 1 }}>
                      {params === 'quick'
                        ? 'Rápido: tenta encurtar a geração do mundo clicando nas opções curtas do ecrã (quando existem).'
                        : 'Padrão: aceita exatamente os parâmetros que o jogo mostra no ecrã.'}
                    </Typography>
                  </Box>
                </StaggerRevealItem>
                <StaggerRevealItem>
                  <PanelNotice severity="info">
                    A geração do mundo pode demorar <strong>minutos</strong>. No passo seguinte vês o
                    progresso ao vivo e podes <strong>cancelar</strong> a qualquer momento.
                  </PanelNotice>
                </StaggerRevealItem>
              </StaggerReveal>
            ) : null}

            {current === 'personagem' ? (
              <StaggerReveal className="flex flex-col gap-4">
                <StaggerRevealHeadline as="h2" className="text-sm font-bold text-foreground">
                  {mode === 'create' ? '2 · Personagem' : '1 · Personagem'}
                </StaggerRevealHeadline>
                <StaggerRevealItem>
                  <Box>
                    <FormControlLabel
                      control={
                        <Switch
                          checked={randomize}
                          onChange={(event) => setRandomize(event.target.checked)}
                          slotProps={{
                            input: {
                              'aria-label': 'Gerar personagem ao acaso',
                              'aria-describedby': 'aleatorio-descricao',
                            },
                          }}
                        />
                      }
                      label="Aleatório"
                    />
                    <Typography
                      id="aleatorio-descricao"
                      variant="caption"
                      sx={{ color: 'text.secondary', display: 'block' }}
                    >
                      {randomize
                        ? 'O jogo escolhe nome, raça, origem, aspeto e personalidade ao acaso.'
                        : 'Define tu o nome; raça e origem ficam em opções avançadas.'}
                    </Typography>
                  </Box>
                </StaggerRevealItem>

                {!randomize ? (
                  <StaggerRevealItem>
                    <Stack spacing={2}>
                      <TextField
                        label="Nome do personagem"
                        value={charName}
                        onChange={(event) => setCharName(event.target.value)}
                        fullWidth
                        helperText="Passo suave: o jogo pode ignorar o nome se o campo não estiver visível."
                      />
                      <Accordion>
                        <AccordionSummary expandIcon={<ExpandMore aria-hidden="true" />}>
                          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                            <Tune fontSize="small" aria-hidden="true" />
                            <Typography variant="subtitle2">Opções avançadas</Typography>
                          </Stack>
                        </AccordionSummary>
                        <AccordionDetails>
                          <Stack spacing={2}>
                            <TextField
                              select
                              label="Raça"
                              value={race}
                              onChange={(event) => setRace(event.target.value)}
                              fullWidth
                              helperText="Guia o passo de escolha de raça no jogo (predefinição: humano)."
                            >
                              {RACES.map((item) => (
                                <MenuItem key={item.value} value={item.value}>
                                  {item.label}
                                </MenuItem>
                              ))}
                            </TextField>
                            <TextField
                              label="Origem"
                              value={origin}
                              onChange={(event) => setOrigin(event.target.value)}
                              fullWidth
                              helperText="Deixa vazio para o jogo usar a primeira origem visível."
                            />
                          </Stack>
                        </AccordionDetails>
                      </Accordion>
                    </Stack>
                  </StaggerRevealItem>
                ) : null}

                <StaggerRevealItem>
                  <Alert severity="info" variant="outlined" role="note">
                    {summary}
                  </Alert>
                </StaggerRevealItem>
              </StaggerReveal>
            ) : null}

            {current === 'progresso' ? (
              <StaggerReveal className="flex flex-col gap-4">
                <StaggerRevealHeadline as="h2" className="text-sm font-bold text-foreground">
                  {mode === 'create' ? '3 · Progresso' : '2 · Progresso'}
                </StaggerRevealHeadline>
                <StaggerRevealItem>
                  <Typography variant="body2" sx={{ color: 'text.secondary' }} role="status">
                    {phase === 'world'
                      ? 'Etapa 1 de 2 — criação do mundo.'
                      : mode === 'create'
                        ? 'Etapa 2 de 2 — criação do personagem.'
                        : 'Etapa única — criação do personagem.'}
                  </Typography>
                </StaggerRevealItem>
                <StaggerRevealItem>
                  <AdventureTaskCard
                    task={task}
                    heading={phase === 'world' ? 'A criar o mundo' : 'A criar o personagem'}
                    contextLine={
                      phase === 'world'
                        ? `«${worldName.trim() || 'nome gerado pelo jogo'}»`
                        : randomize
                          ? 'personagem ao acaso'
                          : `«${charName.trim() || 'sem nome'}»`
                    }
                    updatedAt={
                      statusQuery.dataUpdatedAt ? new Date(statusQuery.dataUpdatedAt).toISOString() : null
                    }
                    refreshing={statusQuery.isFetching}
                    onRefresh={() => void statusQuery.refetch()}
                    cancelBusy={cancelTask.isPending}
                    onConfirmCancel={() => cancelTask.mutate()}
                    onRetry={retry}
                  />
                </StaggerRevealItem>
              </StaggerReveal>
            ) : null}

            {current === 'pronto' ? (
              <StaggerReveal className="flex flex-col gap-4">
                <StaggerRevealHeadline as="h2" className="text-sm font-bold text-foreground">
                  {mode === 'create' ? '4 · Pronto' : '3 · Pronto'}
                </StaggerRevealHeadline>
                <StaggerRevealItem>
                  <Alert severity="success" icon={<CheckCircle fontSize="inherit" />} role="status">
                    {mode === 'create'
                      ? 'Aventura criada — o mundo e o personagem estão prontos. A sessão pode começar.'
                      : 'Personagem criado — o mundo carregado está pronto a jogar.'}
                  </Alert>
                </StaggerRevealItem>
                {gameOff ? (
                  <StaggerRevealItem>
                    <Alert severity="warning" role="status">
                      O jogo está desligado — «Entrar em sessão» leva-te à secção <strong>Sistema</strong>{' '}
                      do Controlo para o ligares; a sessão começa assim que o processo estiver a correr.
                    </Alert>
                  </StaggerRevealItem>
                ) : null}
                <StaggerRevealItem>
                  <Box component="dl" sx={{ m: 0, display: 'grid', gap: 0.5 }}>
                    {mode === 'create' ? (
                      <>
                        <Typography component="dt" variant="caption" sx={{ color: 'text.secondary' }}>
                          Mundo
                        </Typography>
                        <Typography component="dd" variant="body2" sx={{ m: 0, fontWeight: 600 }}>
                          {worldName.trim() || 'nome gerado pelo jogo'}
                        </Typography>
                      </>
                    ) : null}
                    <Typography component="dt" variant="caption" sx={{ color: 'text.secondary' }}>
                      Personagem
                    </Typography>
                    <Typography component="dd" variant="body2" sx={{ m: 0, fontWeight: 600 }}>
                      {statusQuery.data?.current?.adventurer_name ??
                        (randomize ? 'gerado ao acaso' : charName.trim() || 'sem nome')}
                    </Typography>
                  </Box>
                </StaggerRevealItem>
                <StaggerRevealItem>
                  <Stack direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
                    <Button variant="contained" startIcon={<PlayArrow />} onClick={enterSession}>
                      Entrar em sessão
                    </Button>
                    <Button variant="outlined" onClick={enterControl}>
                      Ver controlo do sistema
                    </Button>
                  </Stack>
                </StaggerRevealItem>
              </StaggerReveal>
            ) : null}
          </Stack>
        </DialogContent>
        <DialogActions>
          {current === 'personagem' && mode === 'create' ? (
            <Button onClick={() => setStep(steps.indexOf('mundo'))} disabled={running}>
              Voltar
            </Button>
          ) : null}
          {current === 'progresso' ? (
            <Button onClick={() => setStep(steps.indexOf('personagem'))} disabled={running}>
              Voltar
            </Button>
          ) : null}
          <Box sx={{ flexGrow: 1 }} />
          {current === 'pronto' ? (
            <Button variant="contained" onClick={onClose}>
              Concluir
            </Button>
          ) : (
            <Button onClick={requestClose}>{running ? 'Fechar' : 'Cancelar'}</Button>
          )}
          {current === 'mundo' ? (
            <Button variant="contained" endIcon={<PlayArrow />} onClick={() => setStep(steps.indexOf('personagem'))}>
              Avançar
            </Button>
          ) : null}
          {current === 'personagem' ? (
            <Button
              variant="contained"
              startIcon={<Add />}
              disabled={running}
              onClick={() => setStep(steps.indexOf('progresso'))}
            >
              {mode === 'create' ? 'Criar aventura' : 'Criar personagem'}
            </Button>
          ) : null}
        </DialogActions>
      </Dialog>

      <Dialog
        open={closeConfirm}
        onClose={() => setCloseConfirm(false)}
        maxWidth="xs"
        aria-labelledby="fechar-wizard-titulo"
        aria-describedby="fechar-wizard-descricao"
      >
        <DialogTitle id="fechar-wizard-titulo">
          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
            <Warning fontSize="small" color="warning" aria-hidden="true" />
            <span>Fechar com uma tarefa a correr?</span>
          </Stack>
        </DialogTitle>
        <DialogContent>
          <Typography id="fechar-wizard-descricao" variant="body2">
            Há uma tarefa de aventura em curso. Podes deixá-la correr e acompanhá-la no painel
            (continua com o mesmo progresso e cancelamento), fechar o wizard, ou cancelá-la agora —
            nesse caso o progresso desta tarefa é perdido.
          </Typography>
        </DialogContent>
        <DialogActions>
          <Button variant="outlined" onClick={() => setCloseConfirm(false)}>
            Continuar a tarefa
          </Button>
          <Button
            variant="outlined"
            onClick={() => {
              setCloseConfirm(false);
              onClose();
            }}
          >
            Fechar e deixar correr
          </Button>
          <Button
            color="error"
            variant="contained"
            startIcon={<Stop />}
            onClick={() => {
              setCloseConfirm(false);
              cancelTask.mutate();
              onClose();
            }}
          >
            Cancelar tarefa e fechar
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
