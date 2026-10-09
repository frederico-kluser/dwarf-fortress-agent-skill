/**
 * Chaves de query, hooks de leitura e mutações — tudo passa pelo cliente tipado.
 *
 * As leituras têm `refetchInterval` como rede de segurança: o caminho normal é o
 * WebSocket (`useCampaignEvents`) a escrever na cache do React Query.
 */

import { useContext, useMemo } from 'react';
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
  type InfiniteData,
  type UseMutationResult,
  type UseQueryResult,
} from '@tanstack/react-query';

import { ApiContext, type ApiContextValue } from './context';
import type { ApiClient, ConnectionStatus } from './client';
import type {
  AnswerQuestionRequest,
  AnswerQuestionResponse,
  Approval,
  ApprovalDecisionRequest,
  ApprovalStatusFilter,
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
  /** Prefixo das aprovações; a chave exata acrescenta o filtro de estado. */
  approvals: (id: string) => ['approvals', id] as const,
  screen: (id: string) => ['screen', id] as const,
  keys: (id: string) => ['keys', id] as const,
};

/** Filtro por omissão de `GET .../approvals` — só as pendentes. */
export const PENDING_APPROVALS: ApprovalStatusFilter = 'pending';

/** Chave da lista de aprovações pendentes (a que o WS mantém viva). */
export function pendingApprovalsKey(campaignId: string) {
  return [...queryKeys.approvals(campaignId), PENDING_APPROVALS] as const;
}

/** Insere/atualiza uma aprovação mantendo a ordem do contrato (ts crescente). */
function insertApproval(prev: Approval[] | undefined, approval: Approval): Approval[] {
  const rest = (prev ?? []).filter((item) => item.id !== approval.id);
  return [...rest, approval].sort((a, b) => (a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0));
}

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

/* ------------------------------------------------------------ paginação --- */

/** Tamanho de página do log de decisões e da fila de perguntas (contrato `?limit=`). */
export const PAGE_SIZE = 25;

/**
 * Resultado normalizado de uma lista paginada (`?limit=&before=`): itens já
 * carregados e o «carregar mais».
 */
export interface PagedQueryResult<T> {
  items: T[];
  isLoading: boolean;
  isFetching: boolean;
  isError: boolean;
  error: Error | null;
  dataUpdatedAt: number;
  refetch: () => void;
  /** Há mais registos no anel (`before` do registo mais antigo carregado). */
  hasMore: boolean;
  /** Pede a página seguinte com o cursor exclusivo do contrato. */
  loadMore: () => void;
  isLoadingMore: boolean;
}

/** Items de uma lista paginada — todos têm `ts` (cursor do contrato). */
interface TsItem {
  ts: string;
}

/**
 * Lista paginada ligada à campanha. A página inicial pede `PAGE_SIZE` registos
 * mais recentes; cada «carregar mais» pede os anteriores com `before=<ts do
 * registo mais antigo carregado>` (cursor **exclusivo**, docs/api.md).
 */
function usePagedQuery<T extends TsItem>(
  key: readonly unknown[],
  fetchPage: (client: ApiClient, campaignId: string, before?: string) => Promise<T[]>,
  options: CampaignQueryOptions = {},
): PagedQueryResult<T> {
  const client = useApi();
  const campaignId = useCampaignId();
  const query = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => fetchPage(client, campaignId as string, pageParam),
    enabled: Boolean(campaignId) && (options.enabled ?? true),
    refetchInterval: options.refetchInterval,
    staleTime: 1000,
    initialPageParam: undefined as string | undefined,
    // Páginas do contrato vêm mais antigo primeiro dentro da página; o cursor
    // seguinte é o `ts` do registo mais antigo da última página (exclusivo).
    getNextPageParam: (lastPage: T[]) =>
      lastPage.length < PAGE_SIZE ? undefined : (lastPage[0]?.ts ?? undefined),
  });

  const items = useMemo(() => (query.data?.pages ?? []).flat() as T[], [query.data]);

  return {
    items,
    isLoading: query.isLoading,
    isFetching: query.isFetching,
    isError: query.isError,
    error: query.error,
    dataUpdatedAt: query.dataUpdatedAt,
    refetch: () => void query.refetch(),
    hasMore: query.hasNextPage,
    loadMore: () => {
      if (query.hasNextPage && !query.isFetchingNextPage) void query.fetchNextPage();
    },
    isLoadingMore: query.isFetchingNextPage,
  };
}

/**
 * Insere/atualiza um item na primeira página de uma lista paginada (chegada por
 * WS). A primeira página é aparada para não crescer sem limite — o histórico
 * antigo continua acessível pela paginação (`?before=`).
 */
function upsertFirstPage<T extends { id: string }>(
  prev: InfiniteData<T[], string | undefined> | undefined,
  item: T,
): InfiniteData<T[], string | undefined> {
  const pages = prev?.pages ?? [];
  const first = pages[0] ?? [];
  const merged = [item, ...first.filter((entry) => entry.id !== item.id)].slice(0, 250);
  return {
    pages: [merged, ...pages.slice(1)],
    pageParams: prev?.pageParams ?? [undefined],
  };
}

/** Atualiza um item (por `id`) em qualquer página de uma lista paginada. */
function updateInPages<T extends { id: string }>(
  prev: InfiniteData<T[], string | undefined> | undefined,
  id: string,
  patch: (item: T) => T,
): InfiniteData<T[], string | undefined> | undefined {
  if (!prev) return prev;
  return {
    ...prev,
    pages: prev.pages.map((page) => page.map((item) => (item.id === id ? patch(item) : item))),
  };
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

/**
 * `GET .../decisions` paginado (`?limit=&before=`): página inicial de
 * `PAGE_SIZE` decisões mais recentes, «carregar mais» pelo cursor exclusivo.
 */
export function useDecisions(): PagedQueryResult<Decision> {
  const campaignId = useCampaignId() ?? '';
  return usePagedQuery<Decision>(
    queryKeys.decisions(campaignId),
    (c, id, before) => c.getDecisions(id, before ? { limit: PAGE_SIZE, before } : { limit: PAGE_SIZE }),
    { refetchInterval: 20000 },
  );
}

/**
 * `GET .../questions` paginado (`?limit=&before=`): a página inicial cobre a
 * fila viva; o histórico antigo carrega a pedido.
 */
export function useQuestions(): PagedQueryResult<Question> {
  const campaignId = useCampaignId() ?? '';
  return usePagedQuery<Question>(
    queryKeys.questions(campaignId),
    (c, id, before) => c.getQuestions(id, before ? { limit: PAGE_SIZE, before } : { limit: PAGE_SIZE }),
    { refetchInterval: 15000 },
  );
}

/**
 * `GET .../approvals` — superfície do modo `checkpoint` (achado #4).
 * Por omissão só as pendentes; `status` permite o histórico auditável.
 */
export function useApprovals(
  status: ApprovalStatusFilter = PENDING_APPROVALS,
): UseQueryResult<Approval[], Error> {
  const campaignId = useCampaignId() ?? '';
  return useCampaignQuery(
    [...queryKeys.approvals(campaignId), status],
    (c, id) => c.listApprovals(id, status),
    { refetchInterval: 15000 },
  );
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
      queryClient.setQueryData<InfiniteData<Decision[], string | undefined>>(
        queryKeys.decisions(campaignId),
        (prev) => upsertFirstPage(prev, event.payload),
      );
      break;
    case 'question_new':
      // O backend coalesce re-pedidos da mesma `action_id`: o mesmo id volta a
      // chegar com `attempts`/`last_seen_ts` atualizados — substitui, não duplica.
      queryClient.setQueryData<InfiniteData<Question[], string | undefined>>(
        queryKeys.questions(campaignId),
        (prev) => upsertFirstPage(prev, event.payload),
      );
      break;
    case 'question_answered':
      // Aditivo: a mesma resposta, venha ela do REST ou do WS.
      queryClient.setQueryData<InfiniteData<Question[], string | undefined>>(
        queryKeys.questions(campaignId),
        (prev) =>
          updateInPages(prev, event.payload.id, (question) => ({
            ...question,
            status: event.payload.status,
            answer_text: event.payload.answer_text,
            option_id: event.payload.option_id ?? null,
          })),
      );
      break;
    case 'approval_new':
      queryClient.setQueryData<Approval[]>(pendingApprovalsKey(campaignId), (prev) =>
        insertApproval(prev, event.payload),
      );
      break;
    case 'approval_updated':
      // Decidida deixa de ser pendente: sai da lista (fica no histórico do GET).
      queryClient.setQueryData<Approval[]>(pendingApprovalsKey(campaignId), (prev) =>
        event.payload.status === 'pending'
          ? insertApproval(prev, event.payload)
          : (prev ?? []).filter((item) => item.id !== event.payload.id),
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
    case 'error':
      // Erro recuperável do canal (JSON inválido, pergunta inexistente…): não é
      // fatal e o canal não fecha — a leitura seguinte volta a sincronizar.
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
      queryClient.setQueryData<InfiniteData<Question[], string | undefined>>(
        queryKeys.questions(campaignId),
        (prev) =>
          updateInPages(prev, response.id, (question) => ({
            ...question,
            status: response.status,
            answer_text: response.answer_text,
            option_id: response.option_id ?? variables.option_id ?? null,
          })),
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.memory(campaignId) });
      // Responder a uma pergunta de aprovação decide também a aprovação.
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals(campaignId) });
      options?.onSuccess?.(response);
    },
    onError: (error) => options?.onError?.(error),
  });
}

/**
 * `POST .../approvals/{id}/approve|reject` — decide uma ação retida do modo
 * `checkpoint`. Aprovar/rejeitar é responder à pergunta de aprovação: a resposta
 * entra na memória (EN) e a pergunta sai da fila.
 */
export function useDecideApproval(options?: {
  onSuccess?: (approval: Approval) => void;
  onError?: (error: unknown) => void;
}): UseMutationResult<
  Approval,
  Error,
  { approvalId: string; approve: boolean } & ApprovalDecisionRequest
> {
  const client = useApi();
  const campaignId = useCampaignId() ?? '';
  const queryClient = useQueryClient();
  return useMutation<
    Approval,
    Error,
    { approvalId: string; approve: boolean } & ApprovalDecisionRequest
  >({
    mutationFn: ({ approvalId, approve, note }) => {
      const body: ApprovalDecisionRequest | undefined = note ? { note } : undefined;
      return approve
        ? client.approve(campaignId, approvalId, body)
        : client.reject(campaignId, approvalId, body);
    },
    onSuccess: (approval) => {
      // Sai já da lista de pendentes (o WS `approval_updated` confirma-o).
      queryClient.setQueryData<Approval[]>(pendingApprovalsKey(campaignId), (prev) =>
        (prev ?? []).filter((item) => item.id !== approval.id),
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.approvals(campaignId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.questions(campaignId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.memory(campaignId) });
      options?.onSuccess?.(approval);
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
