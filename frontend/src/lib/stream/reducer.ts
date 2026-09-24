import type { Message, ToolCall } from '$lib/api/types';
import type { WireEvent } from './sse';

export type LiveState = { seq: number; assistant: Message; unavailable: boolean; terminal: boolean };
export function initialLive(id: string): LiveState {
  return { seq: 0, unavailable: false, terminal: false, assistant: { id, role: 'assistant', content: '', reasoning: null, parts: [], status: 'streaming', error: null, created_at: new Date().toISOString(), attachments: [], tool_calls: [] } };
}
export function reduceLive(state: LiveState, wire: WireEvent): LiveState {
  if (wire.id <= state.seq || state.terminal) return state;
  const data = wire.data;
  const assistant = { ...state.assistant, tool_calls: state.assistant.tool_calls.map(call => ({ ...call })), parts: (state.assistant.parts || []).map(part => ({ ...part })) };
  let unavailable = state.unavailable;
  let terminal = false;
  const append = (type: 'text' | 'reasoning', text: string) => {
    const last = assistant.parts!.at(-1);
    if (last && last.type === type) last.text += text;
    else assistant.parts!.push({ type, text });
  };
  const call = () => assistant.tool_calls.find(item => item.id === data.tool_call_id);
  switch (wire.event) {
    case 'reasoning.delta': assistant.reasoning = (assistant.reasoning || '') + String(data.text || ''); append('reasoning', String(data.text || '')); break;
    case 'reasoning.unavailable': unavailable = true; break;
    case 'answer.delta': assistant.content += String(data.text || ''); append('text', String(data.text || '')); break;
    case 'tool_call.started': assistant.tool_calls.push({ id: String(data.tool_call_id), name: String(data.name || ''), arguments: null, arguments_raw: '', status: 'running', requires_approval: false, result: null, truncated: false, error: null }); assistant.parts!.push({ type: 'tool_call', tool_call_id: String(data.tool_call_id) }); break;
    case 'tool_call.arguments.delta': if (call()) call()!.arguments_raw += String(data.delta || ''); break;
    case 'tool_call.ready': if (call()) Object.assign(call()!, { name: data.name, arguments: data.arguments, error: data.error, requires_approval: data.requires_approval }); break;
    case 'approval.pending': if (call()) call()!.status = 'pending_approval'; break;
    case 'approval.decided': if (call()) call()!.status = data.approved ? 'approved' : 'denied'; break;
    case 'tool.running': if (call()) call()!.status = 'running'; break;
    case 'tool.result': if (call()) Object.assign(call()!, { status: data.status as ToolCall['status'], result: data.content, truncated: data.truncated, error: data.error }); break;
    case 'run.completed': assistant.status = 'completed'; terminal = true; break;
    case 'run.failed': assistant.status = 'failed'; assistant.error = { code: String(data.code), message: String(data.message) }; terminal = true; break;
    case 'run.cancelled': assistant.status = 'cancelled'; terminal = true; break;
  }
  return { seq: wire.id, assistant, unavailable, terminal };
}