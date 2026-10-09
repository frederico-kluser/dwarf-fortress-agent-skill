import type { ComponentType, ReactElement } from 'react';
import EditNote from '@mui/icons-material/EditNote';
import Explore from '@mui/icons-material/Explore';
import Flag from '@mui/icons-material/Flag';
import Insights from '@mui/icons-material/Insights';
import MenuBook from '@mui/icons-material/MenuBook';
import Monitor from '@mui/icons-material/Monitor';
import QuestionAnswer from '@mui/icons-material/QuestionAnswer';
import Rule from '@mui/icons-material/Rule';
import Tune from '@mui/icons-material/Tune';

import AventuraPanel from './AventuraPanel';
import ControlPanel from './ControlPanel';
import DecisionsPanel from './DecisionsPanel';
import InputsPanel from './InputsPanel';
import MemoryPanel from './MemoryPanel';
import MetricsPanel from './MetricsPanel';
import ObjectivePanel from './ObjectivePanel';
import QuestionsPanel from './QuestionsPanel';
import ScreenPanel from './ScreenPanel';

export type PanelId =
  | 'aventura'
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
 * Os 9 painéis: aventura primeiro (ciclo de vida da aventura) e, pela ordem da
 * arquitetura: memória · objetivo · inputs · métricas · perguntas · decisões ·
 * ecrã · controlo.
 */
export const PANELS: PanelDefinition[] = [
  {
    id: 'aventura',
    label: 'Aventura',
    title: 'Aventura',
    description:
      'Escolhe uma aventura existente ou cria um mundo e um personagem novos — com progresso ao vivo.',
    icon: <Explore fontSize="small" />,
    Component: AventuraPanel,
  },
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
    description: 'Autopiloto ⇄ checkpoints, aprovações retidas, pausa, kill-switch, chaves e gastos da campanha.',
    icon: <Tune fontSize="small" />,
    Component: ControlPanel,
  },
];

/**
 * Painel de entrada = primeiro da navegação (auditoria v3): «Aventura» está no
 * topo do grupo Supervisão e abre por omissão — escolher/criar a aventura é a
 * primeira decisão do humano e a porta de entrada de todo o resto (sem aventura
 * não há sessão, nem perguntas, nem métricas). «Perguntas» continua a ser o
 * primeiro item de trabalho humano logo a seguir.
 */
export const DEFAULT_PANEL_ID: PanelId = 'aventura';

/**
 * Grupo de navegação (auditoria F.2.2.03): itens planos davam o mesmo peso à
 * fila de perguntas — o trabalho do humano — e a métricas ambientais. Três
 * grupos ordenados pelo trabalho real, com «Aventura» e «Perguntas» à frente.
 */
export interface NavGroup {
  id: 'supervisao' | 'agente' | 'memoria';
  label: string;
  hint: string;
  panels: PanelId[];
}

export const NAV_GROUPS: NavGroup[] = [
  {
    id: 'supervisao',
    label: 'Supervisão',
    hint: 'O trabalho do humano: escolher a aventura, responder, decidir e parar',
    panels: ['aventura', 'perguntas', 'controlo', 'ecra'],
  },
  {
    id: 'agente',
    label: 'Agente',
    hint: 'O que o agente fez e como está a correr',
    panels: ['decisoes', 'metricas'],
  },
  {
    id: 'memoria',
    label: 'Memória',
    hint: 'Contexto que alimenta as decisões',
    panels: ['memoria', 'objetivo', 'inputs'],
  },
];

/** Painéis na ordem de navegação (grupos), com «Aventura» à frente. */
export const NAV_ORDER: PanelId[] = NAV_GROUPS.flatMap((group) => group.panels);

const BY_ID = new Map<PanelId, PanelDefinition>(PANELS.map((panel) => [panel.id, panel]));

/** Painel por id — evita repetir `PANELS.find` na navegação e nos separadores. */
export function panelById(id: PanelId): PanelDefinition {
  return BY_ID.get(id) ?? PANELS[0];
}

/** Todos os painéis, mas pela ordem dos grupos de navegação. */
export function panelsInNavOrder(): PanelDefinition[] {
  return NAV_ORDER.map(panelById);
}

export function panelTabId(id: PanelId) {
  return `painel-tab-${id}`;
}

export function panelRegionId(id: PanelId) {
  return `painel-${id}`;
}
