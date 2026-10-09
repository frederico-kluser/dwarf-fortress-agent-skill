/** Contexto da API — separado do provider para evitar ciclos de importação. */

import { createContext } from 'react';
import type { ApiClient, ConnectionStatus } from './client';
import type { Campaign } from './types';

/** Estado da lista de campanhas (o backend pode estar desligado). */
export type CampaignsStatus = 'loading' | 'ready' | 'error';

export interface ApiContextValue {
  /** Cliente tipado único (HTTP ou mock, conforme `VITE_API_BASE`). */
  client: ApiClient;
  /** `true` quando não há `VITE_API_BASE` e os dados são gerados localmente. */
  isMock: boolean;
  /** Campanhas conhecidas (memórias CoALA por campanha). */
  campaigns: Campaign[];
  campaignsStatus: CampaignsStatus;
  campaignsError: string | null;
  reloadCampaigns: () => void;
  /** Campanha selecionada (persistida em localStorage). */
  campaignId: string | null;
  /** Campanha selecionada, já resolvida. */
  campaign: Campaign | null;
  selectCampaign: (id: string) => void;
  /** Estado da ligação em tempo real. */
  connection: ConnectionStatus;
}

export const ApiContext = createContext<ApiContextValue | null>(null);
