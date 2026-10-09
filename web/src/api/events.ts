/**
 * Leitura defensiva de eventos do WebSocket.
 *
 * `docs/api.md`: "Eventos desconhecidos devem ser ignorados (não fatais) do lado
 * do cliente." — este módulo é o único sítio que decide o que é um evento válido.
 */

import { SERVER_EVENT_TYPES, type ServerEvent, type ServerEventType } from './types';

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isServerEventType(value: unknown): value is ServerEventType {
  return typeof value === 'string' && (SERVER_EVENT_TYPES as readonly string[]).includes(value);
}

/**
 * Valida o envelope `{ type, ts, payload }`. Devolve `null` para tudo o que não
 * seja um evento conhecido com envelope válido (o chamador ignora em silêncio).
 */
export function parseServerEvent(raw: unknown): ServerEvent | null {
  if (!isRecord(raw)) return null;
  const { type, ts, payload } = raw;
  if (!isServerEventType(type)) return null;
  if (typeof ts !== 'string' || ts.length === 0) return null;
  if (!isRecord(payload)) return null;
  return { type, ts, payload } as ServerEvent;
}

/** `JSON.parse` tolerante: devolve `null` em vez de lançar. */
export function safeJsonParse(text: string): unknown {
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return null;
  }
}

/** Mensagem de erro legível para qualquer falha do cliente. */
export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'erro desconhecido';
}
