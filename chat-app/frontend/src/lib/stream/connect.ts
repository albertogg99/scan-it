import { apiBase } from '$lib/config/runtime';
import { accessToken, clearSession } from '$lib/auth/session';
import { conversation, RequestError } from '$lib/api/client';
import type { Conversation } from '$lib/api/types';
import { SseParser, type WireEvent } from './sse';

export class StreamCursorExpiredError extends Error {
  constructor() { super('Stream cursor expired'); }
}

export async function followRun(runId: string, after: number, onEvent: (event: WireEvent) => void, signal: AbortSignal, onConnection: (retrying: boolean) => void = () => {}) {
  let cursor = after;
  let retry = 1000;
  while (!signal.aborted) {
    const token = await accessToken();
    if (!token) throw new Error('Session expired');
    try {
      const response = await fetch(`${apiBase()}/api/runs/${encodeURIComponent(runId)}/events?after=${cursor}`, { headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' }, signal });
      if (response.status === 401) { clearSession(); throw new Error('Session expired'); }
      if (response.status === 410) throw new StreamCursorExpiredError();
      if (response.status === 404) throw new Error('Run not found');
      if (!response.ok || !response.body) throw new Error(`Stream unavailable (${response.status})`);
      onConnection(false);
      const parser = new SseParser();
      const reader = response.body.getReader();
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const event of parser.push(value)) {
          if (event.id <= cursor || event.data.run_id !== runId) continue;
          cursor = event.id;
          onEvent(event);
          if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.event)) { await reader.cancel(); return; }
        }
      }
      retry = 1000;
    } catch (error) { if (signal.aborted) return; if (error instanceof StreamCursorExpiredError || error instanceof Error && ['Session expired', 'Run not found'].includes(error.message)) throw error; onConnection(true); }
    await new Promise<void>(resolve => { const timer = setTimeout(resolve, retry); signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true }); });
    retry = Math.min(retry * 2, 8000);
  }
}

export async function followPersistedRun(conversationId: string, runId: string, onSnapshot: (snapshot: Conversation) => void, signal: AbortSignal, onConnection: (retrying: boolean) => void = () => {}) {
  while (!signal.aborted) {
    try {
      const snapshot = await conversation(conversationId);
      if (signal.aborted) return;
      onConnection(false);
      onSnapshot(snapshot);
      if (snapshot.active_run_id !== runId) return;
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof RequestError && error.status === 401) throw new Error('Session expired');
      if (error instanceof RequestError && error.status === 404) throw error;
      onConnection(true);
    }
    await new Promise<void>(resolve => {
      const timer = setTimeout(resolve, 1000);
      signal.addEventListener('abort', () => { clearTimeout(timer); resolve(); }, { once: true });
    });
  }
}