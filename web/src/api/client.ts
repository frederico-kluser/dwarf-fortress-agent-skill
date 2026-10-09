/**
 * Cliente tipado único da API — REST + WebSocket.
 *
 * Escolha da implementação: se `VITE_API_BASE` **não** estiver definida, o
 * cliente é o MOCK (`./mock`) e a UI corre com zero backend. Se estiver definida
 * (ex.: `http://localhost:8000`), fala com o FastAPI real.
 */

import { MockApiClient } from './mock';
import { errorMessage, parseServerEvent, safeJsonParse } from './events';
import {
  API_PATHS,
  type AdventureCancelResponse,
  type AdventuresResponse,
  type AdventureStatusResponse,
  type AdventureTaskStart,
  type AnswerQuestionRequest,
  type AnswerQuestionResponse,
  type ApiErrorBody,
  type Approval,
  type ApprovalDecisionRequest,
  type ApprovalStatusFilter,
  type Campaign,
  type ControlRequest,
  type ControlState,
  type CreateCampaignRequest,
  type CreateInputRequest,
  type Decision,
  type GameActionRequest,
  type GameActionResponse,
  type HealthResponse,
  type InputHistoryEntry,
  type InputInjection,
  type KeyInfo,
  type ListPageQuery,
  type LoadAdventureRequest,
  type Memory,
  type Metrics,
  type NewCharacterRequest,
  type NewWorldRequest,
  type Objective,
  type PatchMemoryRequest,
  type Question,
  type ScreenTranslation,
  type ServerEvent,
  type SetKeyRequest,
  type Spend,
  type SystemStatus,
} from './types';

/** Estado da ligação WebSocket, tal como a UI o mostra. */
export type ConnectionStatus = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

export interface SubscriptionHandlers {
  onEvent: (event: ServerEvent) => void;
  onStatus?: (status: ConnectionStatus) => void;
}

/** Contrato que as duas implementações (HTTP e mock) cumprem. */
export interface ApiClient {
  /** `'http'` fala com o backend; `'mock'` gera dados localmente. */
  readonly kind: 'http' | 'mock';
  /** Base REST efetiva (vazia no mock). */
  readonly baseUrl: string;

  health(): Promise<HealthResponse>;

  listCampaigns(): Promise<Campaign[]>;
  createCampaign(body: CreateCampaignRequest): Promise<Campaign>;
  getCampaign(id: string): Promise<Campaign>;

  listKeys(id: string): Promise<KeyInfo[]>;
  setKey(id: string, body: SetKeyRequest): Promise<KeyInfo>;

  getMemory(id: string): Promise<Memory>;
  patchMemory(id: string, body: PatchMemoryRequest): Promise<Memory>;

  getObjective(id: string): Promise<Objective>;
  putObjective(id: string, body: Objective): Promise<Objective>;

  /** `POST .../inputs` — o servidor traduz PT→EN antes de entrar em prompts. */
  submitInput(id: string, body: CreateInputRequest): Promise<InputInjection>;
  /** Histórico de inputs mantido pelo cliente (não há `GET` no contrato). */
  listInputs(id: string): Promise<InputHistoryEntry[]>;

  getMetrics(id: string): Promise<Metrics>;
  getSpend(id: string): Promise<Spend>;
  /** `GET .../decisions` — `page` pagina o log (`?limit=&before=`, aditivo). */
  getDecisions(id: string, page?: ListPageQuery): Promise<Decision[]>;
  /** `GET .../questions` — `page` pagina a fila (`?limit=&before=`, aditivo). */
  getQuestions(id: string, page?: ListPageQuery): Promise<Question[]>;
  answerQuestion(
    id: string,
    qid: string,
    body: AnswerQuestionRequest,
  ): Promise<AnswerQuestionResponse>;

  /** `GET .../approvals` — por omissão só as pendentes (filtro opcional). */
  listApprovals(id: string, status?: ApprovalStatusFilter): Promise<Approval[]>;
  /** `POST .../approvals/{id}/approve` — liberta a decisão retida. */
  approve(id: string, approvalId: string, body?: ApprovalDecisionRequest): Promise<Approval>;
  /** `POST .../approvals/{id}/reject` — descarta a ação e regista a recusa. */
  reject(id: string, approvalId: string, body?: ApprovalDecisionRequest): Promise<Approval>;

  setControl(id: string, body: ControlRequest): Promise<ControlState>;
  getScreen(id: string): Promise<ScreenTranslation>;

  /** `GET .../adventures` — saves do DF + aventura atual. */
  listAdventures(id: string): Promise<AdventuresResponse>;
  /** `POST .../adventures/new-world` — `202` com a tarefa arrancada (pode demorar minutos). */
  startNewWorld(id: string, body: NewWorldRequest): Promise<AdventureTaskStart>;
  /** `POST .../adventures/new-character` — `202`; compõe com `new-world`/`load`. */
  startNewCharacter(id: string, body: NewCharacterRequest): Promise<AdventureTaskStart>;
  /** `POST .../adventures/load` — `202`; `422` quando o save não existe. */
  loadAdventure(id: string, body: LoadAdventureRequest): Promise<AdventureTaskStart>;
  /** `GET .../adventures/status` — progresso ao vivo da tarefa. */
  getAdventureStatus(id: string): Promise<AdventureStatusResponse>;
  /** `POST .../adventures/cancel` — o motor para de enviar teclas. */
  cancelAdventureTask(id: string): Promise<AdventureCancelResponse>;

  /** `GET /api/system/status` — estado global (jogo, RPC, frontend, loop). */
  getSystemStatus(): Promise<SystemStatus>;
  /** `POST /api/system/game` — arranca/pára o processo do jogo. */
  setGameAction(body: GameActionRequest): Promise<GameActionResponse>;

  /** Abre o canal de tempo real; devolve a função de cancelamento. */
  subscribe(id: string, handlers: SubscriptionHandlers): () => void;
}

/** `VITE_API_BASE` — vazio ⇒ modo mock. */
export function readApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE;
  return typeof raw === 'string' ? raw.trim().replace(/\/+$/, '') : '';
}

/** Cria o cliente certo para o ambiente atual. */
export function createApiClient(): ApiClient {
  const baseUrl = readApiBase();
  if (!baseUrl) return new MockApiClient();
  return new HttpApiClient(baseUrl);
}

/** Erro de API com o `detail` do FastAPI e o código HTTP. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = 'ApiError';
    this.status = status;
  }
}

const INPUTS_STORAGE_PREFIX = 'sod.inputs.';

/* ------------------------------------------------------------------ HTTP --- */

export class HttpApiClient implements ApiClient {
  readonly kind = 'http' as const;
  readonly baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        headers: {
          'Content-Type': 'application/json',
          ...(init?.headers ?? {}),
        },
      });
    } catch (cause) {
      throw new ApiError(0, `sem ligação ao backend (${errorMessage(cause)})`);
    }

    if (!response.ok) {
      const body = safeJsonParse(await response.text().catch(() => ''));
      const detail =
        body && typeof body === 'object' && 'detail' in body
          ? String((body as ApiErrorBody).detail)
          : `HTTP ${response.status}`;
      throw new ApiError(response.status, detail);
    }

    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
  }

  health() {
    return this.request<HealthResponse>(API_PATHS.health);
  }

  listCampaigns() {
    return this.request<Campaign[]>(API_PATHS.campaigns);
  }

  createCampaign(body: CreateCampaignRequest) {
    return this.request<Campaign>(API_PATHS.campaigns, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  getCampaign(id: string) {
    return this.request<Campaign>(API_PATHS.campaign(id));
  }

  listKeys(id: string) {
    return this.request<KeyInfo[]>(API_PATHS.keys(id));
  }

  setKey(id: string, body: SetKeyRequest) {
    return this.request<KeyInfo>(API_PATHS.keys(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  getMemory(id: string) {
    return this.request<Memory>(API_PATHS.memory(id));
  }

  patchMemory(id: string, body: PatchMemoryRequest) {
    return this.request<Memory>(API_PATHS.memory(id), {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
  }

  getObjective(id: string) {
    return this.request<Objective>(API_PATHS.objective(id));
  }

  putObjective(id: string, body: Objective) {
    return this.request<Objective>(API_PATHS.objective(id), {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  }

  async submitInput(id: string, body: CreateInputRequest) {
    const created = await this.request<InputInjection>(API_PATHS.inputs(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
    this.appendLocalInput(id, { ...created, text_pt: body.text });
    return created;
  }

  listInputs(id: string) {
    return Promise.resolve(this.readLocalInputs(id));
  }

  getMetrics(id: string) {
    return this.request<Metrics>(API_PATHS.metrics(id));
  }

  getSpend(id: string) {
    return this.request<Spend>(API_PATHS.spend(id));
  }

  getDecisions(id: string, page?: ListPageQuery) {
    return this.request<Decision[]>(API_PATHS.decisions(id, page));
  }

  getQuestions(id: string, page?: ListPageQuery) {
    return this.request<Question[]>(API_PATHS.questions(id, page));
  }

  answerQuestion(id: string, qid: string, body: AnswerQuestionRequest) {
    return this.request<AnswerQuestionResponse>(API_PATHS.answer(id, qid), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  listApprovals(id: string, status?: ApprovalStatusFilter) {
    return this.request<Approval[]>(API_PATHS.approvals(id, status));
  }

  approve(id: string, approvalId: string, body?: ApprovalDecisionRequest) {
    return this.request<Approval>(API_PATHS.approve(id, approvalId), {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    });
  }

  reject(id: string, approvalId: string, body?: ApprovalDecisionRequest) {
    return this.request<Approval>(API_PATHS.reject(id, approvalId), {
      method: 'POST',
      body: JSON.stringify(body ?? {}),
    });
  }

  setControl(id: string, body: ControlRequest) {
    return this.request<ControlState>(API_PATHS.control(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  getScreen(id: string) {
    return this.request<ScreenTranslation>(API_PATHS.screen(id));
  }

  /* ---------------------------------------------------------- aventuras --- */

  listAdventures(id: string) {
    return this.request<AdventuresResponse>(API_PATHS.adventures(id));
  }

  startNewWorld(id: string, body: NewWorldRequest) {
    return this.request<AdventureTaskStart>(API_PATHS.newWorld(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  startNewCharacter(id: string, body: NewCharacterRequest) {
    return this.request<AdventureTaskStart>(API_PATHS.newCharacter(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  loadAdventure(id: string, body: LoadAdventureRequest) {
    return this.request<AdventureTaskStart>(API_PATHS.loadAdventure(id), {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  getAdventureStatus(id: string) {
    return this.request<AdventureStatusResponse>(API_PATHS.adventuresStatus(id));
  }

  cancelAdventureTask(id: string) {
    return this.request<AdventureCancelResponse>(API_PATHS.adventuresCancel(id), {
      method: 'POST',
      body: JSON.stringify({}),
    });
  }

  /* ------------------------------------------------------------- sistema --- */

  getSystemStatus() {
    return this.request<SystemStatus>(API_PATHS.systemStatus);
  }

  setGameAction(body: GameActionRequest) {
    return this.request<GameActionResponse>(API_PATHS.systemGame, {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  /* ------------------------------------------- histórico local de inputs --- */

  private storageKey(id: string) {
    return `${INPUTS_STORAGE_PREFIX}${id}`;
  }

  private readLocalInputs(id: string): InputHistoryEntry[] {
    try {
      const raw = window.localStorage.getItem(this.storageKey(id));
      const parsed = raw ? safeJsonParse(raw) : null;
      return Array.isArray(parsed) ? (parsed as InputHistoryEntry[]) : [];
    } catch {
      return [];
    }
  }

  private appendLocalInput(id: string, entry: InputHistoryEntry) {
    try {
      const next = [entry, ...this.readLocalInputs(id)].slice(0, 100);
      window.localStorage.setItem(this.storageKey(id), JSON.stringify(next));
    } catch {
      /* localStorage indisponível (modo privado) — o histórico fica só em memória */
    }
  }

  /* -------------------------------------------------------- websocket --- */

  subscribe(id: string, handlers: SubscriptionHandlers): () => void {
    const url = `${this.baseUrl.replace(/^http/, 'ws')}${API_PATHS.ws(id)}`;
    let socket: WebSocket | null = null;
    let disposed = false;
    let attempt = 0;
    let timer: number | undefined;

    const setStatus = (status: ConnectionStatus) => handlers.onStatus?.(status);

    const open = () => {
      if (disposed) return;
      setStatus(attempt === 0 ? 'connecting' : 'reconnecting');
      socket = new WebSocket(url);

      socket.onopen = () => {
        attempt = 0;
        setStatus('open');
      };

      socket.onmessage = (message) => {
        const event = parseServerEvent(safeJsonParse(String(message.data)));
        if (!event) return; // eventos desconhecidos: ignorados, não fatais
        handlers.onEvent(event);
      };

      socket.onclose = () => {
        if (disposed) return;
        setStatus('closed');
        attempt += 1;
        // backoff exponencial limitado: 1s, 2s, 4s, 8s, 15s, 15s, …
        const delay = Math.min(1000 * 2 ** (attempt - 1), 15000);
        setStatus('reconnecting');
        timer = window.setTimeout(open, delay);
      };

      socket.onerror = () => socket?.close();
    };

    open();

    return () => {
      disposed = true;
      if (timer !== undefined) window.clearTimeout(timer);
      socket?.close();
      setStatus('idle');
    };
  }
}
