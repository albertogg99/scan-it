export type ApiError = { code: string; message: string };
export type PublicMcp = { id: string; title: string; description: string; enabled: boolean; transport: string; public_endpoint: string | null; requires_credentials: boolean; icon_url?: string | null };
export type RegistryEntry = {
	id: string; title: string; description: string; backend_url: string; public_url: string | null;
	enabled: boolean; approval_policy: 'auto' | 'ask_always' | 'ask_destructive';
	auth: { type: 'none' | 'bearer' | 'header'; header_name: string; credential_env: string };
};
export type AdminMcp = RegistryEntry & { icon_url: string | null };
export type PublicConfig = { app_name: string; llm_configured: boolean; image_input: 'supported' | 'unsupported' | 'unknown' };
export type Attachment = { id: string; mime: string; size: number };
export type ToolCall = { id: string; name: string; arguments: Record<string, unknown> | null; arguments_raw: string; status: 'pending_approval' | 'approved' | 'denied' | 'running' | 'succeeded' | 'failed'; requires_approval: boolean; result: string | null; truncated: boolean; error: string | null };
export type Part = { type: 'reasoning'; text: string } | { type: 'text'; text: string } | { type: 'tool_call'; tool_call_id: string };
export type Message = { id: string; role: 'user' | 'assistant'; content: string; reasoning: string | null; parts: Part[] | null; status: 'streaming' | 'completed' | 'failed' | 'cancelled'; error: ApiError | null; created_at: string; attachments: Attachment[]; tool_calls: ToolCall[] };
export type ConversationSummary = { id: string; mcp_id: string; mcp_title: string; title: string; created_at: string; updated_at: string };
export type Conversation = ConversationSummary & { approval_policy: string; tools: { name: string; description: string; requires_approval: boolean }[]; messages: Message[]; active_run_id: string | null };
export type Run = { run_id: string; user_message_id: string; assistant_message_id: string };