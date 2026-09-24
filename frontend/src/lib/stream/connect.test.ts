import { afterEach, describe, expect, it, vi } from 'vitest';
import { clearSession } from '$lib/auth/session';
import type { Conversation } from '$lib/api/types';
import { followPersistedRun, followRun, StreamCursorExpiredError } from './connect';
import { initialLive, reduceLive } from './reducer';

vi.mock('$lib/auth/session', () => ({ accessToken: vi.fn(async () => 'token'), clearSession: vi.fn() }));
vi.mock('$lib/config/runtime', () => ({ apiBase: () => 'http://localhost:8000' }));
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); vi.clearAllMocks(); });

const frame = (seq: number, type: string, extra: Record<string, unknown> = {}) =>
  `id: ${seq}\nevent: ${type}\ndata: ${JSON.stringify({ seq, type, run_id: 'run', ...extra })}\n\n`;
const stream = (body: string) => new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
const snapshot = (content: string, active_run_id: string | null, approval: 'pending_approval' | 'approved'): Conversation => ({
  id: 'chat', mcp_id: 'mcp', mcp_title: 'MCP', title: 'Chat', created_at: '', updated_at: '', approval_policy: 'ask', tools: [], active_run_id,
  messages: [{ ...initialLive('assistant').assistant, content, parts: [{ type: 'text', text: content }], status: active_run_id ? 'streaming' : 'completed',
    tool_calls: [{ id: 'tool', name: 'lookup', arguments: null, arguments_raw: '', status: approval, requires_approval: true, result: null, truncated: false, error: null }] }]
});

describe('run connections', () => {
  it('returns a typed cursor expiry instead of retrying HTTP 410', async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ detail: { code: 'event_cursor_expired', message: 'Expired' } }), { status: 410 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(followRun('run', 0, () => {}, new AbortController().signal)).rejects.toBeInstanceOf(StreamCursorExpiredError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('stops on HTTP 404 and requires reauthentication on HTTP 401', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(null, { status: 404 })).mockResolvedValueOnce(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(followRun('run', 0, () => {}, new AbortController().signal)).rejects.toThrow('Run not found');
    await expect(followRun('run', 0, () => {}, new AbortController().signal)).rejects.toThrow('Session expired');
    expect(clearSession).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('restarts an interrupted stream after its last sequence without duplicating deltas', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(stream(frame(1, 'answer.delta', { text: 'Hello' })))
      .mockResolvedValueOnce(stream(frame(1, 'answer.delta', { text: 'Hello' }) + frame(2, 'answer.delta', { text: ' world' }) + frame(3, 'run.completed')));
    vi.stubGlobal('fetch', fetchMock);
    let live = initialLive('assistant');
    await followRun('run', 0, event => { live = reduceLive(live, event); }, new AbortController().signal);
    expect(live.assistant.content).toBe('Hello world');
    expect(live.assistant.status).toBe('completed');
    expect(fetchMock.mock.calls[1][0]).toContain('after=1');
  });

  it('reloads partial text and approval from snapshots until the saved run completes', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(snapshot('Partial', 'run', 'pending_approval'))))
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot('Partial answer', 'run', 'approved'))))
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot('Partial answer', null, 'approved'))));
    vi.stubGlobal('fetch', fetchMock);
    const seen: Conversation[] = [];
    const following = followPersistedRun('chat', 'run', value => seen.push(value), new AbortController().signal);
    await vi.advanceTimersByTimeAsync(2000);
    await following;
    expect(seen.map(value => value.messages[0].content)).toEqual(['Partial', 'Partial answer', 'Partial answer']);
    expect(seen.map(value => value.messages[0].tool_calls[0].status)).toEqual(['pending_approval', 'approved', 'approved']);
    expect(seen[2].active_run_id).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('recovers an expired partial stream from saved text without replaying its deltas', async () => {
    const saved = snapshot('Hello world', null, 'approved');
    const fetchMock = vi.fn().mockResolvedValueOnce(stream(frame(1, 'answer.delta', { text: 'Hello' })))
      .mockResolvedValueOnce(new Response(null, { status: 410 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(saved)));
    vi.stubGlobal('fetch', fetchMock);
    let live = initialLive('assistant');
    let displayed = live.assistant.content;
    try {
      await followRun('run', 0, event => { live = reduceLive(live, event); displayed = live.assistant.content; }, new AbortController().signal);
    } catch (error) {
      if (!(error instanceof StreamCursorExpiredError)) throw error;
      await followPersistedRun('chat', 'run', value => { displayed = value.messages[0].content; }, new AbortController().signal);
    }
    expect(displayed).toBe('Hello world');
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('treats a polling 401 as a reauthentication signal', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })));
    await expect(followPersistedRun('chat', 'run', () => {}, new AbortController().signal)).rejects.toThrow('Session expired');
    expect(clearSession).toHaveBeenCalledTimes(1);
  });
});