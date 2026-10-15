import type { PanelId } from '../panels/registry';

/**
 * IDs de DOM da navegação mobile (auditoria H.1.1.01): cada superfície de
 * navegação tem os seus próprios IDs — `painel-tab-<id>` já não existe em
 * duplicado no DOM, e o `aria-labelledby` do painel aponta para a instância
 * visível por desenho, não por ordem de render.
 */
export const panelMobileTabId = (id: PanelId) => `painel-tab-mobile-${id}`;
