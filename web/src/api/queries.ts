/**
 * Chaves de query, hooks de leitura e mutações — tudo passa pelo cliente tipado.
 *
 * As leituras têm `refetchInterval` como rede de segurança: o caminho normal é o
 * WebSocket (`useCampaignEvents`) a escrever na cache do React Query.
 */

import { useContext } from 'react';
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { ApiContext, type ApiContextValue } from './context';
import type { ApiClient, ConnectionStatus } from './client';
import type {
  AnswerQuestionRequest,
  AnswerQuestionResponse,
  Campaign,
  ControlRequest,
  ControlState,
  CreateInputRequest,
  Decision,
  InputHistoryEntry,
  InputInjection,
  KeyInfo,
  Memory,
  Metrics,
  Objective,
  PatchMemoryRequest,
  Question,
  ScreenTranslation,
  ServerEvent,
  SetKeyRequest,
  Spend,
} from './types';

export const queryKeys = {
  campaigns: ['campaigns'] as const,
  memory: (id: string) => ['memory', id] as const,
  objective: (id: string) => ['objective', id] as const,
  inputs: (id: string) => ['inputs', id] as const,
  metrics: (id: string) => ['metrics', id] as const,
  spend: (id: string) => ['spend', id] as const,
  decisions: (id: string) => ['decisions', id] as const,
  questions: (id: string) => ['questions', id] as const,
  screen: (id: string) => ['screen', id] as const,
  keys: (id: string) => ['keys', id] as const,
};

/* --------------------------------------------------------------- acesso --- */

export function useApiContext(): ApiContextValue {
  const value = useContext(ApiContext);
  if (!value) throw new Error('useApiContext requer <ApiProvider> acima na árvore');
  return value;
}

export function useApi(): ApiClient {
  return useApiContext().client;
}

export function useCampaignId(): string | null {
  return useApiContext().campaignId;
}

export function useCampaign(): Campaign | null {
  return useApiContext().campaign;
}

export function useConnection(): ConnectionStatus {
  return useApiContext().connection;
}

export function useIsMock(): boolean {
  return useApiContext().isMock;
}

/* ------------------------------------------------------------- leitura --- */

interface CampaignQueryOptions {
  /** Atualização periódica de recurso (ms) — rede de segurança do WS. */
  refetchInterval?: number;
  enabled?: boolean;
}

/** `useQuery` ligado à campanha selecionada (só corre quando há campanha). */
export function useCampaignQuery<T>(
  key: readonly unknown[],
  fetcher: (client: ApiClient, campaignId: string) => Promise<T>,
  options: CampaignQueryOptions = {},
): UseQueryResult<T, Error> {
  const client = useApi();
  const campaignId = useCampaignId();
  return useQuery<T, Error>({
    queryKey: key,
    queryFn: () => fetcher(client, campaignId as string),
    enabled: Boolean(campaignId) && (options.enabled ?? true),
    refetchInterval: options.refetchInterval,
    staleTime: 1000,
  });
}

export function useMemory(): UseQueryResult<Memory, Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.memory(campaignId), (c, id) => c.getMemory(id));
}

export function useObjective(): UseQueryResult<Objective, Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.objective(campaignId), (c, id) => c.getObjective(id));
}

export function useInputs(): UseQueryResult<InputHistoryEntry[], Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.inputs(campaignId), (c, id) => c.listInputs(id));
}

export function useMetrics(): UseQueryResult<Metrics, Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.metrics(campaignId), (c, id) => c.getMetrics(id), {
    refetchInterval: 5000,
  });
}

export function useSpend(): UseQueryResult<Spend, Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.spend(campaignId), (c, id) => c.getSpend(id), {
    refetchInterval: 20000,
  });
}

export function useDecisions(): UseQueryResult<Decision[], Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.decisions(campaignId), (c, id) => c.getDecisions(id), {
    refetchInterval: 20000,
  });
}

export function useQuestions(): UseQueryResult<Question[], Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.questions(campaignId), (c, id) => c.getQuestions(id), {
    refetchInterval: 15000,
  });
}

export function useScreen(): UseQueryResult<ScreenTranslation, Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.screen(campaignId), (c, id) => c.getScreen(id), {
    refetchInterval: 4000,
  });
}

export function useKeys(): UseQueryResult<KeyInfo[], Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(queryKeys.keys(campaignId), (c, id) => c.listKeys(id));
}

/* ------------------------------------------------- eventos em tempo real --- */

/**
 * Escreve um evento do WebSocket na cache do React Query.
 * Eventos desconhecidos nunca chegam aqui (`parseServerEvent` filtra-os).
 */
export function applyServerEvent(
  queryClient: ReturnType<typeof useQueryClient>,
  campaignId: string,
  event: ServerEvent,
) {
  switch (event.type) {
    case 'metrics_update':
      queryClient.setQueryData(queryKeys.metrics(campaignId), event.payload);
      break;
    case 'screen_translation':
      queryClient.setQueryData<ScreenTranslation>(queryKeys.screen(campaignId), {
        translated_pt: event.payload.translated_pt,
        ts: event.ts,
      });
      break;
    case 'decision':
      queryClient.setQueryData<Decision[]>(queryKeys.decisions(campaignId), (prev) =>
        [event.payload, ...(prev ?? []).filter((d) => d.id !== event.payload.id)].slice(0, 250),
      );
      break;
    case 'question_new':
      queryClient.setQueryData<Question[]>(queryKeys.questions(campaignId), (prev) =>
        [event.payload, ...(prev ?? []).filter((q) => q.id !== event.payload.id)],
      );
      break;
    case 'memory_update':
      queryClient.setQueryData(queryKeys.memory(campaignId), event.payload);
      break;
    case 'spend_update':
      queryClient.setQueryData(queryKeys.spend(campaignId), event.payload);
      break;
    case 'control_state':
      queryClient.setQueryData<Campaign[]>(queryKeys.campaigns, (prev) =>
        (prev ?? []).map((campaign) =>
          campaign.id === campaignId
            ? {
                ...campaign,
                control: { mode: event.payload.mode, kill_switch: event.payload.kill_switch },
                updated_at: event.ts,
              }
            : campaign,
        ),
      );
      break;
    case 'game_state':
      // Estado crítico em EN enviado ao JEV — não é apresentado na UI (só PT).
      break;
    default:
      break;
  }
}

/* ------------------------------------------------------------ mutações --- */

export function usePatchMemory(options?: {
  onSuccess?: (memory: Memory) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<Memory, Error, PatchMemoryRequest> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<Memory, Error, PatchMemoryRequest>({
    mutationFn: (body) => client.patchMemory(campaignId, body),
    onSuccess: (memory) => {
      queryClient.setQueryData(queryKeys.memory(campaignId), memory);
      options?.onSuccess?.(memory);
    },
    onError: (error) => options?.onError?.(error),
  });
}

export function usePutObjective(options?: {
  onSuccess?: (objective: Objective) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<Objective, Error, Objective> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<Objective, Error, Objective>({
    mutationFn: (body) => client.putObjective(campaignId, body),
    onSuccess: (objective) => {
      queryClient.setQueryData(queryKeys.objective(campaignId), objective);
      void queryClient.invalidateQueries({ queryKey: queryKeys.metrics(campaignId) });
      options?.onSuccess?.(objective);
    },
    onError: (error) => options?.onError?.(error),
  });
}

export function useSubmitInput(options?: {
  onSuccess?: (input: InputInjection) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<InputInjection, Error, CreateInputRequest> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<InputInjection, Error, CreateInputRequest>({
    mutationFn: (body) => client.submitInput(campaignId, body),
    onSuccess: (input) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.inputs(campaignId) });
      options?.onSuccess?.(input);
    },
    onError: (error) => options?.onError?.(error),
  });
}

export function useAnswerQuestion(options?: {
  onSuccess?: (response: AnswerQuestionResponse) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<
  AnswerQuestionResponse,
  Error,
  { qid: string } & AnswerQuestionRequest
> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<AnswerQuestionResponse, Error, { qid: string } & AnswerQuestionRequest>({
    mutationFn: ({ qid, ...body }) => client.answerQuestion(campaignId, qid, body),
    onSuccess: (response, variables) => {
      queryClient.setQueryData<Question[]>(queryKeys.questions(campaignId), (prev) =>
        (prev ?? []).map((question) =>
          question.id === response.id
            ? {
                ...question,
                status: response.status,
                answer_text: response.answer_text,
                option_id: response.option_id ?? variables.option_id ?? null,
              }
            : question,
        ),
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.memory(campaignId) });
      options?.onSuccess?.(response);
    },
    onError: (error) => options?.onError?.(error),
  });
}

export function useSetControl(options?: {
  onSuccess?: (control: ControlState) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<ControlState, Error, ControlRequest> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<ControlState, Error, ControlRequest>({
    mutationFn: (body) => client.setControl(campaignId, body),
    onSuccess: (control, variables) => {
      queryClient.setQueryData<Campaign[]>(queryKeys.campaigns, (prev) =>
        (prev ?? []).map((campaign) =>
          campaign.id === campaignId
            ? {
                ...campaign,
                control: { mode: variables.mode, kill_switch: variables.kill_switch },
              }
            : campaign,
        ),
      );
      options?.onSuccess?.(control);
    },
    onError: (error) => options?.onError?.(error),
  });
}

export function useSetKey(options?: {
  onSuccess?: (key: KeyInfo) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<KeyInfo, Error, SetKeyRequest> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<KeyInfo, Error, SetKeyRequest>({
    mutationFn: (body) => client.setKey(campaignId, body),
    onSuccess: (key) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.keys(campaignId) });
      options?.onSuccess?.(key);
    },
    onError: (error) => options?.onError?.(error),
  });
}
