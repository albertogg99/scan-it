import { describe, expect, it } from 'vitest';
import { SseParser } from './sse';
import { initialLive, reduceLive } from './reducer';
import { groupParts } from '../components/message-content';

describe('resumable event stream', () => {
  it('joins saved token fragments without merging across tool activity or mutating history', () => {
    const parts = [{ type: 'text' as const, text: '**Hel' }, { type: 'text' as const, text: 'lo** world' },
      { type: 'tool_call' as const, tool_call_id: 'tool' }, { type: 'text' as const, text: 'Done' },
      { type: 'reasoning' as const, text: 'Think' }, { type: 'reasoning' as const, text: 'ing' }];
    expect(groupParts(parts)).toEqual([{ type: 'text', text: '**Hello** world' },
      { type: 'tool_call', tool_call_id: 'tool' }, { type: 'text', text: 'Done' }, { type: 'reasoning', text: 'Thinking' }]);
    expect(parts[0].text).toBe('**Hel');
  });
  it('parses split unicode, CRLF, comments and multiple frames', () => {
    const parser = new SseParser();
    const bytes = new TextEncoder().encode('id: 1\r\nevent: answer.delta\r\ndata: {"seq":1,"type":"answer.delta","run_id":"r","text":"Zürich"}\r\n\r\n: keepalive\r\n\r\nid: 2\nevent: run.completed\ndata: {"seq":2,"type":"run.completed","run_id":"r"}\n\n');
    const events = [];
    for (const byte of bytes) events.push(...parser.push(new Uint8Array([byte])));
    expect(events.map(event => event.event)).toEqual(['answer.delta', 'run.completed']);
    expect(events[0].data.text).toBe('Zürich');
  });
  it('assembles tool calls and ignores replayed sequence numbers', () => {
    let state = initialLive('assistant');
    const event = (id: number, type: string, data: Record<string, unknown>) => ({ id, event: type, data: { seq: id, type, run_id: 'r', ...data } });
    state = reduceLive(state, event(1, 'tool_call.started', { tool_call_id: 'tool', name: 'lookup' }));
    state = reduceLive(state, event(2, 'tool_call.arguments.delta', { tool_call_id: 'tool', delta: '{"stop":' }));
    state = reduceLive(state, event(3, 'tool_call.arguments.delta', { tool_call_id: 'tool', delta: '"HB"}' }));
    state = reduceLive(state, event(3, 'tool_call.arguments.delta', { tool_call_id: 'tool', delta: '"HB"}' }));
    state = reduceLive(state, event(4, 'approval.pending', { tool_call_id: 'tool' }));
    expect(state.assistant.tool_calls[0].arguments_raw).toBe('{"stop":"HB"}');
    expect(state.assistant.tool_calls[0].status).toBe('pending_approval');
    state = reduceLive(state, event(5, 'reasoning.unavailable', {}));
    expect(state.unavailable).toBe(true);
    expect(state.assistant.reasoning).toBeNull();
  });
});