<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { Menu, Plus, Trash2, LogOut, Square, ArrowLeft } from 'lucide-svelte';
  import { accessToken, login, logout } from '$lib/auth/session';
  import { cancelRun, conversation, createConversation, deleteConversation, history, publicConfig, publicMcp, sendMessage, upload } from '$lib/api/client';
  import type { Conversation, ConversationSummary, Message, PublicConfig, PublicMcp } from '$lib/api/types';
  import { followPersistedRun, followRun, StreamCursorExpiredError } from '$lib/stream/connect';
  import { initialLive, reduceLive, type LiveState } from '$lib/stream/reducer';
  import Timeline from '$lib/components/Timeline.svelte';
  import Composer from '$lib/components/Composer.svelte';
  import { ui } from '$lib/ui';
  const { t } = ui();
  const id = $derived(page.params.id ?? '');
  let mcp = $state<PublicMcp | null>(null);
  let config = $state<PublicConfig | null>(null);
  let current = $state<Conversation | null>(null);
  let items = $state<ConversationSummary[]>([]);
  let live = $state<LiveState | null>(null);
  let loading = $state(true);
  let error = $state('');
  let busy = $state(false);
  let reconnecting = $state(false);
  let sidebar = $state(false);
  let streamController: AbortController | null = null;
  const messages = $derived(current ? current.messages.map(message => live && live.assistant.id === message.id ? live.assistant : message) : []);
  function path(id: string, conversationId?: string) { return `/m/${encodeURIComponent(id)}/chat${conversationId ? `?conversation=${encodeURIComponent(conversationId)}` : ''}`; }
  async function refresh() { if (current) { current = await conversation(current.id); items = await history(); } }
  async function pollPersisted(runId: string, controller: AbortController) {
    live = null;
    reconnecting = false;
    if (!current) return;
    await followPersistedRun(current.id, runId, snapshot => { if (!controller.signal.aborted) current = snapshot; }, controller.signal, retrying => reconnecting = retrying);
    if (!controller.signal.aborted) { busy = false; reconnecting = false; items = await history(); }
  }
  async function reopen(runId: string) {
    streamController?.abort(); streamController = new AbortController();
    const controller = streamController;
    busy = true;
    try { await pollPersisted(runId, controller); }
    catch (reason) { if (!controller.signal.aborted) { busy = false; reconnecting = false; error = reason instanceof Error ? reason.message : t('network'); } }
  }
  async function connect(runId: string, assistantId: string) {
    streamController?.abort(); streamController = new AbortController();
    const controller = streamController;
    live = initialLive(assistantId);
    try {
      await followRun(runId, 0, event => { if (controller.signal.aborted) return; live = reduceLive(live!, event); if (live.terminal) { busy = false; reconnecting = false; void refresh().catch(reason => error = String(reason)); } }, controller.signal, retrying => reconnecting = retrying);
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof StreamCursorExpiredError) {
        try { await pollPersisted(runId, controller); }
        catch (failure) { if (!controller.signal.aborted) { busy = false; reconnecting = false; error = failure instanceof Error ? failure.message : t('network'); } }
      } else { busy = false; reconnecting = false; error = reason instanceof Error ? reason.message : t('network'); }
    }
  }
  onMount(() => {
    let active = true;
    (async () => {
      try {
        mcp = await publicMcp(id);
        const token = await accessToken();
        if (!token) { await login(page.url.pathname + page.url.search); return; }
        config = await publicConfig();
        items = await history();
        const selected = page.url.searchParams.get('conversation');
        if (selected) {
          const loaded = await conversation(selected);
          if (loaded.mcp_id !== id) { await goto(path(loaded.mcp_id, loaded.id), { replaceState: true }); return; }
          current = loaded;
        } else {
          current = await createConversation(id);
          await goto(path(id, current.id), { replaceState: true, noScroll: true });
          items = await history();
        }
        if (!active || !current) return;
        if (current.active_run_id) void reopen(current.active_run_id);
      } catch (reason) { if (active) { error = reason instanceof Error ? reason.message : t('error'); if (String(error).includes('Session expired')) await login(page.url.pathname + page.url.search); } }
      finally { if (active) loading = false; }
    })();
    return () => { active = false; streamController?.abort(); };
  });
  async function send(text: string, file: File | null) {
    if (!current) return;
    busy = true; error = '';
    try {
      const attachments = file ? [(await upload(file)).id] : [];
      const run = await sendMessage(current.id, text, attachments);
      const user: Message = { id: run.user_message_id, role: 'user', content: text, reasoning: null, parts: null, status: 'completed', error: null, created_at: new Date().toISOString(), attachments: file ? [{ id: attachments[0], mime: file.type, size: file.size }] : [], tool_calls: [] };
      current = { ...current, messages: [...current.messages, user, initialLive(run.assistant_message_id).assistant], active_run_id: run.run_id };
      void connect(run.run_id, run.assistant_message_id);
      void history().then(value => items = value);
    } catch (reason) { busy = false; throw reason; }
  }
  async function remove(id: string) {
    if (!confirm(t('confirmDelete'))) return;
    try { await deleteConversation(id); items = items.filter(item => item.id !== id); if (current?.id === id) { streamController?.abort(); await goto(path(current.mcp_id), { invalidateAll: true }); location.reload(); } }
    catch (reason) { error = reason instanceof Error ? reason.message : t('error'); }
  }
  async function cancel() { if (current?.active_run_id) { try { await cancelRun(current.active_run_id); } catch (reason) { error = reason instanceof Error ? reason.message : t('error'); } } }
</script>
<div class="workspace">
  <aside class:shown={sidebar} class="sidebar"><div class="sidebar-title"><strong>{t('history')}</strong><button class="icon" aria-label={t('menu')} title={t('menu')} onclick={() => sidebar = false}><Menu size={18} /></button></div><a class="new-button" href={path(id)} data-sveltekit-reload><Plus size={18} />{t('newChat')}</a>
    <nav aria-label={t('history')}>{#if items.length === 0}<p class="muted">{t('noHistory')}</p>{/if}{#each items as item}<div class:active={item.id === current?.id} class="history-item"><a href={path(item.mcp_id, item.id)} data-sveltekit-reload><small>{item.mcp_title}</small><span>{item.title}</span></a><button class="icon" title={t('delete')} aria-label={t('delete')} onclick={() => remove(item.id)}><Trash2 size={16} /></button></div>{/each}</nav><button class="logout" onclick={logout}><LogOut size={17} />{t('logout')}</button>
  </aside>
  <section class="chat-area"><div class="chat-head"><button class="icon mobile-menu" title={t('menu')} aria-label={t('menu')} onclick={() => sidebar = !sidebar}><Menu size={20} /></button><a class="quiet-link" href="/m/{page.params.id}"><ArrowLeft size={16} /></a><div><strong>{current?.mcp_title || mcp?.title || t('loading')}</strong><small>MCP / {current?.mcp_id || page.params.id}</small></div><a class="quiet-link configure-link" href="/m/{current?.mcp_id || page.params.id}/configure">{t('configure')}</a></div>
    {#if error}<div class="notice" role="alert">{error} {#if error.includes('Session expired')}<button onclick={() => login(page.url.pathname + page.url.search)}>{t('signInAgain')}</button>{/if}</div>{/if}
    {#if loading}<p class="empty" role="status">{t('loading')}...</p>{:else if current}<Timeline {messages} unavailable={live?.unavailable || false} onChange={() => { void refresh(); }} />{#if busy}<div class="run-controls"><span role="status">{reconnecting ? t('network') : t('running')}</span><button onclick={cancel}><Square size={14} />{t('cancel')}</button></div>{/if}<Composer {config} {busy} onSend={send} />{/if}
  </section>
</div>