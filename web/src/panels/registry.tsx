import type { ComponentType, ReactElement } from 'react';
import EditNote from '@mui/icons-material/EditNote';
import Flag from '@mui/icons-material/Flag';
import Insights from '@mui/icons-material/Insights';
import MenuBook from '@mui/icons-material/MenuBook';
import Monitor from '@mui/icons-material/Monitor';
import QuestionAnswer from '@mui/icons-material/QuestionAnswer';
import Rule from '@mui/icons-material/Rule';
import Tune from '@mui/icons-material/Tune';

import ControlPanel from './ControlPanel';
import DecisionsPanel from './DecisionsPanel';
import InputsPanel from './InputsPanel';
import MemoryPanel from './MemoryPanel';
import MetricsPanel from './MetricsPanel';
import ObjectivePanel from './ObjectivePanel';
import QuestionsPanel from './QuestionsPanel';
import ScreenPanel from './ScreenPanel';

export type PanelId =
  | 'memoria'
  | 'objetivo'
  | 'inputs'
  | 'metricas'
  | 'perguntas'
  | 'decisoes'
  | 'ecra'
  | 'controlo';

export interface PanelDefinition {
  id: PanelId;
  /** Etiqueta curta da navegação. */
  label: string;
  /** Título completo no topo do painel. */
  title: string;
  description: string;
  icon: ReactElement;
  Component: ComponentType;
}

/**
 * Os 8 painéis, pela ordem da arquitetura:
 * memória · objetivo · inputs · métricas · perguntas · decisões · ecrã · controlo.
 */
export const PANELS: PanelDefinition[] = [
  {
    id: 'memoria',
    label: 'Memória',
    title: 'Memória narrativa',
    description:
      'A memória canónica é guardada em inglês; aqui vês e editas a versão em português.',
    icon: <MenuBook fontSize="small" />,
    Component: MemoryPanel,
  },
  {
    id: 'objetivo',
    label: 'Objetivo',
    title: 'Objetivo e sub-objetivos',
    description: 'Narrativa do objetivo e sub-objetivos marcáveis — escrito em português.',
    icon: <Flag fontSize="small" />,
    Component: ObjectivePanel,
  },
  {
    id: 'inputs',
    label: 'Inputs',
    title: 'Injeção de informação',
    description: 'Informação livre para o agente. É traduzida PT→EN antes de entrar nos prompts.',
    icon: <EditNote fontSize="small" />,
    Component: InputsPanel,
  },
  {
    id: 'metricas',
    label: 'Métricas',
    title: 'Métricas vivas',
    description: 'Os 8 grupos de métricas do contrato, mais as métricas do agente.',
    icon: <Insights fontSize="small" />,
    Component: MetricsPanel,
  },
  {
    id: 'perguntas',
    label: 'Perguntas',
    title: 'Fila de perguntas',
    description: 'Dúvidas do agente (bandas hitl/abstain): resposta rápida ou campo livre.',
    icon: <QuestionAnswer fontSize="small" />,
    Component: QuestionsPanel,
  },
  {
    id: 'decisoes',
    label: 'Decisões',
    title: 'Log de decisões JEV',
    description: 'Cada chamada ao JEV: papel, respostas, probabilidades, confiança, banda, custo.',
    icon: <Rule fontSize="small" />,
    Component: DecisionsPanel,
  },
  {
    id: 'ecra',
    label: 'Ecrã',
    title: 'Ecrã traduzido ao vivo',
    description: 'Texto do jogo traduzido para português — o original nunca é mostrado.',
    icon: <Monitor fontSize="small" />,
    Component: ScreenPanel,
  },
  {
    id: 'controlo',
    label: 'Controlo',
    title: 'Controlo de autonomia',
    description: 'Autopiloto ⇄ checkpoints, pausa, kill-switch, chaves e gastos da campanha.',
    icon: <Tune fontSize="small" />,
    Component: ControlPanel,
  },
];

export const DEFAULT_PANEL_ID: PanelId = 'metricas';

export function panelTabId(id: PanelId) {
  return `painel-tab-${id}`;
}

export function panelRegionId(id: PanelId) {
  return `painel-${id}`;
}
