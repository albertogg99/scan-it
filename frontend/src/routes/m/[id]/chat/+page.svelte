<script lang="ts">
  import { onDestroy } from 'svelte';
  import { afterNavigate, goto } from '$app/navigation';
  import { page } from '$app/state';
  import { PanelLeftClose, PanelLeftOpen, Plus, Trash2, LogOut, Square, ArrowLeft } from 'lucide-svelte';
  import { accessToken, login, logout } from '$lib/auth/session';
  import { cancelRun, conversation, createConversation, deleteConversation, history, publicConfig, publicMcp, sendMessage, upload } from '$lib/api/client';
  import type { Conversation, ConversationSummary, Message, PublicConfig, PublicMcp } from '$lib/api/types';
  import { followPersistedRun, followRun, StreamCursorExpiredError } from '$lib/stream/connect';
  import { initialLive, reduceLive, type LiveState } from '$lib/stream/reducer';
  import Timeline from '$lib/components/Timeline.svelte';
  import Composer from '$lib/components/Composer.svelte';
  import ThinkingIndicator from '$lib/components/ThinkingIndicator.svelte';
  import { scrollViewport } from '$lib/components/scroll-viewport';
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
  let sidebarCollapsed = $state(false);
  let streamController: AbortController | null = null;
  let navigationVersion = 0;
  const messages = $derived(current ? current.messages.map(message => live && live.assistant.id === message.id ? live.assistant : message) : []);
  function path(id: string, conversationId?: string) { return `/m/${encodeURIComponent(id)}/chat${conversationId ? `?conversation=${encodeURIComponent(conversationId)}` : ''}`; }
  async function refresh() {
    if (!current) return;
    const version = navigationVersion;
    const snapshot = await conversation(current.id);
    if (version !== navigationVersion) return;
    current = snapshot;
    const summaries = await history();
    if (version === navigationVersion) items = summaries;
  }
  async function pollPersisted(runId: string, controller: AbortController) {
    live = null;
    reconnecting = false;
    if (!current) return;
    await followPersistedRun(current.id, runId, snapshot => { if (!controller.signal.aborted) current = snapshot; }, controller.signal, retrying => { if (!controller.signal.aborted) reconnecting = retrying; });
    if (!controller.signal.aborted) {
      busy = false; reconnecting = false;
      const summaries = await history();
      if (!controller.signal.aborted) items = summaries;
    }
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
      await followRun(runId, 0, event => { if (controller.signal.aborted) return; live = reduceLive(live!, event); if (live.terminal) { busy = false; reconnecting = false; void refresh().catch(reason => { if (!controller.signal.aborted) error = String(reason); }); } }, controller.signal, retrying => { if (!controller.signal.aborted) reconnecting = retrying; });
    } catch (reason) {
      if (controller.signal.aborted) return;
      if (reason instanceof StreamCursorExpiredError) {
        try { await pollPersisted(runId, controller); }
        catch (failure) { if (!controller.signal.aborted) { busy = false; reconnecting = false; error = failure instanceof Error ? failure.message : t('network'); } }
      } else { busy = false; reconnecting = false; error = reason instanceof Error ? reason.message : t('network'); }
    }
  }
  afterNavigate(() => {
    const serverId = id;
    const selected = page.url.searchParams.get('conversation');
    if (selected && current?.id === selected && current.mcp_id === serverId) return;
    const version = ++navigationVersion;
    streamController?.abort();
    live = null; current = null; busy = false; reconnecting = false;
    loading = true; error = ''; sidebar = false;
    void (async () => {
      try {
        const server = await publicMcp(serverId);
        if (version !== navigationVersion) return;
        mcp = server;
        const token = await accessToken();
        if (version !== navigationVersion) return;
        if (!token) { await login(page.url.pathname + page.url.search); return; }
        const [settings, summaries] = await Promise.all([config ? Promise.resolve(config) : publicConfig(), history()]);
        if (version !== navigationVersion) return;
        config = settings; items = summaries;
        if (selected) {
          const loaded = await conversation(selected);
          if (version !== navigationVersion) return;
          if (loaded.mcp_id !== serverId) { await goto(path(loaded.mcp_id, loaded.id), { replaceState: true }); return; }
          current = loaded;
        } else {
          const created = await createConversation(serverId);
          if (version !== navigationVersion) return;
          current = created;
          await goto(path(serverId, created.id), { replaceState: true, noScroll: true });
          if (version !== navigationVersion) return;
          const summaries = await history();
          if (version !== navigationVersion) return;
          items = summaries;
        }
        if (version !== navigationVersion || !current) return;
        if (current.active_run_id) void reopen(current.active_run_id);
      } catch (reason) { if (version === navigationVersion) { error = reason instanceof Error ? reason.message : t('error'); if (String(error).includes('Session expired')) await login(page.url.pathname + page.url.search); } }
      finally { if (version === navigationVersion) loading = false; }
    })();
  });
  onDestroy(() => { navigationVersion++; streamController?.abort(); });
  async function send(text: string, file: File | null) {
    if (!current) return;
    const version = navigationVersion;
    const conversationId = current.id;
    busy = true; error = '';
    try {
      const attachments = file ? [(await upload(file)).id] : [];
      if (version !== navigationVersion) return;
      const run = await sendMessage(conversationId, text, attachments);
      if (version !== navigationVersion || !current) return;
      const user: Message = { id: run.user_message_id, role: 'user', content: text, reasoning: null, parts: null, status: 'completed', error: null, created_at: new Date().toISOString(), attachments: file ? [{ id: attachments[0], mime: file.type, size: file.size }] : [], tool_calls: [] };
      current = { ...current, messages: [...current.messages, user, initialLive(run.assistant_message_id).assistant], active_run_id: run.run_id };
      void connect(run.run_id, run.assistant_message_id);
      void history().then(value => { if (version === navigationVersion) items = value; }).catch(() => {});
    } catch (reason) { if (version === navigationVersion) { busy = false; throw reason; } }
  }
  async function remove(id: string) {
    if (!confirm(t('confirmDelete'))) return;
    try { await deleteConversation(id); items = items.filter(item => item.id !== id); if (current?.id === id) { streamController?.abort(); await goto(path(current.mcp_id)); } }
    catch (reason) { error = reason instanceof Error ? reason.message : t('error'); }
  }
  async function cancel() { if (current?.active_run_id) { try { await cancelRun(current.active_run_id); } catch (reason) { error = reason instanceof Error ? reason.message : t('error'); } } }
</script>
<div class="workspace">
  <aside id="conversation-sidebar" class:shown={sidebar} class:collapsed={sidebarCollapsed} class="sidebar"><div class="sidebar-title"><strong>{t('history')}</strong><button class="icon" aria-label={t('collapseSidebar')} title={t('collapseSidebar')} aria-controls="conversation-sidebar" onclick={() => { sidebar = false; sidebarCollapsed = true; }}><PanelLeftClose size={18} /></button></div><a class="new-button" href={path(id)}><Plus size={18} />{t('newChat')}</a>
    <nav aria-label={t('history')} use:scrollViewport>{#if items.length === 0}<p class="muted">{t('noHistory')}</p>{/if}{#each items as item}<div class:active={item.id === current?.id} class="history-item"><a href={path(item.mcp_id, item.id)} aria-current={item.id === current?.id ? 'page' : undefined} title={item.title}>{#if item.mcp_id !== id}<small>{item.mcp_title}</small>{/if}<span>{item.title}</span></a><button class="icon" title={t('delete')} aria-label={t('delete')} onclick={() => remove(item.id)}><Trash2 size={16} /></button></div>{/each}</nav><button class="logout" onclick={logout}><LogOut size={17} />{t('logout')}</button>
  </aside>
  <section class="chat-area"><div class="chat-head"><button class="icon mobile-menu" title={t('expandSidebar')} aria-label={t('expandSidebar')} aria-controls="conversation-sidebar" aria-expanded={sidebar} onclick={() => sidebar = !sidebar}><PanelLeftOpen size={20} /></button>{#if sidebarCollapsed}<button class="icon desktop-menu" title={t('expandSidebar')} aria-label={t('expandSidebar')} aria-controls="conversation-sidebar" aria-expanded="false" onclick={() => sidebarCollapsed = false}><PanelLeftOpen size={20} /></button>{/if}<a class="quiet-link" href="/m/{page.params.id}"><ArrowLeft size={16} /></a><div class="chat-identity"><strong>Apertus</strong><small>{current?.mcp_title || mcp?.title || t('loading')}</small></div><a class="quiet-link configure-link" href="/m/{current?.mcp_id || page.params.id}/configure">{t('configure')}</a></div>
    {#if error}<div class="notice" role="alert">{error} {#if error.includes('Session expired')}<button onclick={() => login(page.url.pathname + page.url.search)}>{t('signInAgain')}</button>{/if}</div>{/if}
    {#if loading}<p class="empty" role="status">{t('loading')}...</p>{:else if current}<Timeline {messages} onChange={() => { void refresh(); }} />{#if busy}<div class="run-controls">{#if reconnecting}<span role="status">{t('network')}</span>{:else}<ThinkingIndicator />{/if}<button onclick={cancel}><Square size={14} />{t('cancel')}</button></div>{/if}<Composer {config} {busy} onSend={send} />{/if}
  </section>
</div>

<style>
  .sidebar { background: #f0efec; border-right: 1px solid #dedcd7; }
  :global(html[data-theme='dark']) .sidebar { background: #222221; border-right-color: #383735; }
  .sidebar-title .icon { display: inline-flex; }
  .chat-head { flex-wrap: wrap; height: auto; min-height: 64px; }
  .chat-identity { min-width: 0; overflow-wrap: anywhere; }
  .configure-link { max-width: 100%; white-space: normal; }
  .sidebar-title strong { font-size: .78rem; color: #888178; }
  .new-button { border: 0; border-radius: 5px; margin: 4px 12px 16px; padding: 10px; }
  .new-button:hover, .history-item:hover { background: rgb(127 127 127 / 7%); }
  .history-item { border: 0; border-radius: 5px; margin: 3px 4px; }
  .history-item.active, :global(html[data-theme='dark']) .history-item.active { background: rgb(127 127 127 / 12%); }
  .history-item a { padding: 10px; }
  .history-item .icon { opacity: 0; transition: opacity 150ms ease; }
  .history-item:hover .icon, .history-item:focus-within .icon { opacity: .7; }
  .logout { border: 0; font-size: .8rem; opacity: .7; }
  :global(.quiet-scroll) { scrollbar-width: thin; scrollbar-gutter: stable; scrollbar-color: transparent transparent; transition: scrollbar-color 250ms ease; }
  :global(.quiet-scroll.is-scrolling) { scrollbar-color: rgb(127 127 127 / 45%) transparent; }
  :global(.quiet-scroll::-webkit-scrollbar) { width: 6px; height: 6px; }
  :global(.quiet-scroll::-webkit-scrollbar-track) { background: transparent; }
  :global(.quiet-scroll::-webkit-scrollbar-thumb) { background: transparent; border-radius: 3px; }
  :global(.quiet-scroll.is-scrolling::-webkit-scrollbar-thumb) { background: rgb(127 127 127 / 45%); }
  @media (hover: none) { .history-item .icon { opacity: .6; } }
  @media (max-width: 700px) {
    .desktop-menu { display: none; }
    .sidebar:not(.shown) { visibility: hidden; }
  }
  @media (min-width: 701px) { .sidebar.collapsed { display: none; } }
  @media (prefers-reduced-motion: reduce) {
    .history-item .icon, :global(.quiet-scroll) { transition: none; }
  }
</style>