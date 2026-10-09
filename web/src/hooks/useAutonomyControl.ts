import { useCallback, useSyncExternalStore } from 'react';

import { CONTROL_MODE_LABEL, formatRelative } from '../api/format';
import type { ControlMode } from '../api/types';

/**
 * Modo de autonomia restaurável.
 *
 * O contrato (`POST /control`) só transporta `mode` + `kill_switch`: o backend não
 * guarda o modo anterior a uma pausa. Sem isto, retomar de «Em pausa» ou desligar o
 * kill-switch devolvia SEMPRE `autopilot`, ignorando o `checkpoint` escolhido
 * (auditoria C.1.4.01). A escolha de segurança é portanto persistida aqui, por
 * campanha, e qualquer transição para autopilot passa a exigir confirmação.
 */
export type RestorableMode = Exclude<ControlMode, 'paused'>;

export const DEFAULT_RESTORABLE_MODE: RestorableMode = 'checkpoint';

const STORAGE_PREFIX = 'system-one-dwarf:autonomy:';

function isRestorableMode(value: string | null): value is RestorableMode {
  return value === 'autopilot' || value === 'checkpoint';
}

function storageKey(campaignId: string): string {
  return `${STORAGE_PREFIX}${campaignId}`;
}

const STORE_EVENT = 'system-one-dwarf:autonomy-change';

/** Notifica os assinantes da mesma aba (o evento `storage` só cruza abas). */
function notifyStore(): void {
  window.dispatchEvent(new Event(STORE_EVENT));
}

function subscribeStore(onChange: () => void): () => void {
  window.addEventListener('storage', onChange);
  window.addEventListener(STORE_EVENT, onChange);
  return () => {
    window.removeEventListener('storage', onChange);
    window.removeEventListener(STORE_EVENT, onChange);
  };
}

function writeStored(campaignId: string | null | undefined, mode: RestorableMode): void {
  if (!campaignId) return;
  try {
    window.localStorage.setItem(storageKey(campaignId), mode);
  } catch {
    // Sem persistência a UI continua a funcionar dentro da sessão.
  }
  notifyStore();
}

/** Etiqueta PT de um modo (evita duplicar o dicionário em cada ecrã). */
export function modeLabel(mode: ControlMode): string {
  return CONTROL_MODE_LABEL[mode] ?? mode;
}

export interface AutonomyPersistence {
  /**
   * Melhor estimativa do modo que fica ativo ao sair de «Em pausa»/kill-switch.
   * Vem do armazenamento local (última escolha não-pausada do utilizador) e, na
   * sua falta, do modo que o backend reporta quando não está pausado.
   */
  restorableMode: RestorableMode;
  /** Grava explicitamente um modo não-pausado como o modo a restaurar. */
  rememberMode: (mode: ControlMode) => void;
  /** `true` quando já há uma escolha gravada para esta campanha (para tooltips). */
  persisted: boolean;
}

export function useAutonomyPersistence(
  campaignId: string | null,
  currentMode: ControlMode,
): AutonomyPersistence {
  // `localStorage` é o sistema externo: ler por `useSyncExternalStore` mantém a
  // escolha sincronizada entre componentes e abas sem `setState` dentro de efeitos.
  const storedRaw = useSyncExternalStore(
    subscribeStore,
    () => {
      if (!campaignId) return null;
      try {
        return window.localStorage.getItem(storageKey(campaignId));
      } catch {
        return null;
      }
    },
    () => null,
  );
  const stored = isRestorableMode(storedRaw) ? storedRaw : null;

  // Só uma escolha EXPLÍCITA no ecrã é persistida: um `autopilot` vindo do backend
  // (ex.: reinício do agente) nunca apaga o `checkpoint` que o utilizador escolheu.
  const rememberMode = useCallback(
    (mode: ControlMode) => {
      if (mode === 'paused') return;
      writeStored(campaignId, mode);
    },
    [campaignId],
  );

  const fallback: RestorableMode = currentMode === 'paused' ? DEFAULT_RESTORABLE_MODE : currentMode;

  return { restorableMode: stored ?? fallback, rememberMode, persisted: stored !== null };
}

/**
 * Decide o que enviar em `POST /control` ao mexer no kill-switch.
 * Desligar (desarmar) restaura o modo guardado em vez de forçar `autopilot`.
 */
export function controlPatchForKillSwitch(
  next: boolean,
  currentMode: ControlMode,
  restorableMode: RestorableMode,
): { mode: ControlMode; kill_switch: boolean } {
  if (next) return { mode: 'paused', kill_switch: true };
  return { mode: currentMode === 'paused' ? restorableMode : currentMode, kill_switch: false };
}

/** Versão legível de um controlo aplicado, para o aviso de sucesso. */
export function controlOutcomeText(control: { mode: ControlMode; kill_switch: boolean }): string {
  if (control.kill_switch) return 'Kill-switch ativo · o agente fica em pausa.';
  return `Modo: ${modeLabel(control.mode)}.`;
}

/** Pressão do kill-switch no ecrã (chip compacto do AppBar). */
export function autonomyChipLabel(
  mode: ControlMode,
  killSwitch: boolean,
  pendingQuestions: number,
): string {
  const base = killSwitch ? 'Kill-switch ativo' : modeLabel(mode);
  if (pendingQuestions === 0) return base;
  return `${base} · ${pendingQuestions} à espera de resposta`;
}

/** Texto de frescura do modo de autonomia (o backend não manda `ts` deste estado). */
export function controlFreshnessLabel(campaignTs: string | null | undefined, now: number): string {
  if (!campaignTs) return 'sem leitura';
  return `lido ${formatRelative(campaignTs, now)}`;
}
