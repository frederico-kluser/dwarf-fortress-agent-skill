/**
 * Provider da API: escolhe o cliente, resolve a campanha ativa e mantém aberto o
 * canal WebSocket da campanha (com reconexão automática feita pelo cliente).
 */

import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { createApiClient, type ConnectionStatus } from './client';
import { ApiContext, type ApiContextValue, type CampaignsStatus } from './context';
import { errorMessage } from './events';
import { applyServerEvent, queryKeys } from './queries';
import type { Campaign } from './types';

const SELECTED_CAMPAIGN_KEY = 'sod.campaign';

function readStoredCampaign(): string | null {
  try {
    return window.localStorage.getItem(SELECTED_CAMPAIGN_KEY);
  } catch {
    return null;
  }
}

export function ApiProvider({ children }: { children: ReactNode }) {
  // Um cliente por sessão de UI (mock quando `VITE_API_BASE` não está definida).
  const [client] = useState(() => createApiClient());
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(() => readStoredCampaign());
  const [wsStatus, setWsStatus] = useState<ConnectionStatus>('idle');

  const campaignsQuery = useQuery<Campaign[], Error>({
    queryKey: queryKeys.campaigns,
    queryFn: () => client.listCampaigns(),
    refetchInterval: 30000,
  });

  const campaigns = useMemo(() => campaignsQuery.data ?? [], [campaignsQuery.data]);

  // Sem escolha válida → primeira campanha da lista (derivado, sem efeito).
  const campaignId = useMemo(() => {
    if (selectedId && campaigns.some((campaign) => campaign.id === selectedId)) return selectedId;
    return campaigns[0]?.id ?? null;
  }, [campaigns, selectedId]);

  // Sem campanha não há canal: o estado é derivado, não guardado em efeito.
  const connection: ConnectionStatus = campaignId ? wsStatus : 'idle';

  useEffect(() => {
    if (!campaignId) return;
    try {
      window.localStorage.setItem(SELECTED_CAMPAIGN_KEY, campaignId);
    } catch {
      /* localStorage indisponível — a seleção fica só em memória */
    }
  }, [campaignId]);

  useEffect(() => {
    if (!campaignId) return;
    // O cliente anuncia 'connecting' ao abrir e 'reconnecting' no backoff.
    return client.subscribe(campaignId, {
      onEvent: (event) => applyServerEvent(queryClient, campaignId, event),
      onStatus: setWsStatus,
    });
  }, [client, campaignId, queryClient]);

  const reloadCampaigns = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.campaigns });
  }, [queryClient]);

  const campaign = useMemo(
    () => campaigns.find((item) => item.id === campaignId) ?? null,
    [campaigns, campaignId],
  );

  const campaignsStatus: CampaignsStatus = campaignsQuery.isError
    ? 'error'
    : campaignsQuery.isLoading
      ? 'loading'
      : 'ready';

  const value = useMemo<ApiContextValue>(
    () => ({
      client,
      isMock: client.kind === 'mock',
      campaigns,
      campaignsStatus,
      campaignsError: campaignsQuery.error ? errorMessage(campaignsQuery.error) : null,
      reloadCampaigns,
      campaignId,
      campaign,
      selectCampaign: setSelectedId,
      connection,
    }),
    [
      client,
      campaigns,
      campaignsStatus,
      campaignsQuery.error,
      reloadCampaigns,
      campaignId,
      campaign,
      connection,
    ],
  );

  return <ApiContext.Provider value={value}>{children}</ApiContext.Provider>;
}
