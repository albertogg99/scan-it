import { apiBase } from '$lib/config/runtime';
import { accessToken, clearSession } from '$lib/auth/session';
import type { AdminMcp, RegistryEntry, ApiError, Attachment, Conversation, ConversationSummary, PublicConfig, PublicMcp, Run, ToolCall } from './types';

export class RequestError extends Error {
  constructor(public detail: ApiError, public status: number) { super(detail.message); }
}
export async function request<T>(path: string, init: RequestInit = {}, auth = true): Promise<T> {
  const token = auth ? await accessToken() : null;
  if (auth && !token) throw new RequestError({ code: 'unauthorized', message: 'Session expired' }, 401);
  const response = await fetch(`${apiBase()}${path}`, { ...init, headers: { ...(init.body instanceof FormData ? {} : init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers, ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  if (!response.ok) {
    if (response.status === 401 && auth) clearSession();
    let detail: ApiError = { code: 'network_error', message: `Request failed (${response.status})` };
    try { detail = (await response.json() as { detail: ApiError }).detail || detail; } catch { detail = { code: 'network_error', message: `Request failed (${response.status})` }; }
    throw new RequestError(detail, response.status);
  }
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
export const publicMcps = () => request<PublicMcp[]>('/api/public/mcps', {}, false);
export const adminMcps = () => request<{ servers: AdminMcp[]; allowed_hosts: string[] }>('/api/admin/mcps');
export const saveMcp = (entry: RegistryEntry, existing: boolean) => request<AdminMcp>(`/api/admin/mcps${existing ? `/${encodeURIComponent(entry.id)}` : ''}`, { method: 'POST', body: JSON.stringify(entry) });
export const deleteMcp = (id: string) => request<void>(`/api/admin/mcps/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const uploadMcpIcon = (id: string, file: File) => { const body = new FormData(); body.append('file', file); return request<AdminMcp>(`/api/admin/mcps/${encodeURIComponent(id)}/icon`, { method: 'POST', body }); };
export const deleteMcpIcon = (id: string) => request<void>(`/api/admin/mcps/${encodeURIComponent(id)}/icon`, { method: 'DELETE' });
export const publicMcp = (id: string) => request<PublicMcp>(`/api/public/mcps/${encodeURIComponent(id)}`, {}, false);
export const publicHealth = (id: string) => request<{ reachable: boolean; checked_at: string }>(`/api/public/mcps/${encodeURIComponent(id)}/health`, {}, false);
export const publicConfig = () => request<PublicConfig>('/api/config/public', {}, false);
export const history = () => request<ConversationSummary[]>('/api/conversations');
export const conversation = (id: string) => request<Conversation>(`/api/conversations/${encodeURIComponent(id)}`);
export const createConversation = (mcp_id: string) => request<Conversation>('/api/conversations', { method: 'POST', body: JSON.stringify({ mcp_id }) });
export const deleteConversation = (id: string) => request<void>(`/api/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' });
export const sendMessage = (id: string, text: string, attachment_ids: string[]) => request<Run>(`/api/conversations/${encodeURIComponent(id)}/messages`, { method: 'POST', body: JSON.stringify({ text, attachment_ids }) });
export const cancelRun = (id: string) => request<Run>(`/api/runs/${encodeURIComponent(id)}/cancel`, { method: 'POST' });
export const decide = (id: string, approved: boolean) => request<ToolCall>(`/api/tool-calls/${encodeURIComponent(id)}/decision`, { method: 'POST', body: JSON.stringify({ approved }) });
export const upload = (file: File) => { const body = new FormData(); body.append('file', file); return request<Attachment>('/api/attachments', { method: 'POST', body }); };
export async function attachmentUrl(id: string): Promise<string> {
  const token = await accessToken();
  if (!token) throw new RequestError({ code: 'unauthorized', message: 'Session expired' }, 401);
  const response = await fetch(`${apiBase()}/api/attachments/${encodeURIComponent(id)}`, { headers: { Authorization: `Bearer ${token}` }, cache: 'no-store' });
  if (!response.ok) throw new RequestError({ code: 'attachment_unavailable', message: 'Image unavailable' }, response.status);
  return URL.createObjectURL(await response.blob());
}