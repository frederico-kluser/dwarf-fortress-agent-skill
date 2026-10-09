import type { Approval, Question } from '../api/types';

/**
 * Coalescência visual por `action_id` (auditoria v2, achado 1): o backend já
 * dedupeia re-pedidos da mesma ação (`attempts`/`last_seen_ts`), mas podem
 * coexistir várias entradas da mesma ação (pilha criada antes do dedupe, ou
 * pedidos após resposta). A UI mostra o grupo como UM cartão — o mais recente é
 * o representante e os restantes ficam listados dentro dele.
 */
export interface ActionGroup<T> {
  /** `action_id` comum (ou o próprio id quando a entrada não traz ação). */
  key: string;
  /** Entrada mais recente do grupo — a que recebe as ações. */
  representative: T;
  /** Restantes entradas, mais recentes primeiro. */
  coalesced: T[];
}

function groupByAction<T extends { id: string; ts: string; action_id?: string | null }>(
  items: T[],
): ActionGroup<T>[] {
  const byKey = new Map<string, T[]>();
  for (const item of items) {
    const key = item.action_id?.trim() ? item.action_id.trim() : item.id;
    const bucket = byKey.get(key);
    if (bucket) bucket.push(item);
    else byKey.set(key, [item]);
  }
  return [...byKey.entries()]
    .map(([key, bucket]) => {
      const sorted = [...bucket].sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
      return {
        key,
        representative: sorted[0] as T,
        coalesced: sorted.slice(1),
      };
    })
    .sort((a, b) => (a.representative.ts < b.representative.ts ? 1 : -1));
}

/** Perguntas pendentes agrupadas por ação retida (mais recente primeiro). */
export function groupQuestionsByAction(questions: Question[]): ActionGroup<Question>[] {
  return groupByAction(questions);
}

/** Aprovações pendentes agrupadas por ação retida (mais recente primeiro). */
export function groupApprovalsByAction(approvals: Approval[]): ActionGroup<Approval>[] {
  return groupByAction(approvals);
}
