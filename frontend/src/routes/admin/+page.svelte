<script lang="ts">
  import { onMount } from 'svelte';
  import { ArrowLeft, Blocks, Check, LogIn, LogOut, Plus, RefreshCw, Save, ShieldCheck, Trash2, X } from 'lucide-svelte';
  import { adminMcps, deleteMcp, deleteMcpIcon, RequestError, saveMcp, uploadMcpIcon } from '$lib/api/client';
  import type { AdminMcp, RegistryEntry } from '$lib/api/types';
  import { login, logout } from '$lib/auth/session';
  import { apiBase } from '$lib/config/runtime';
  import { APP_NAME } from '$lib/config/brand';

  const blank = (): RegistryEntry => ({ id: '', title: '', description: '', backend_url: '', public_url: '', enabled: true, approval_policy: 'ask_destructive', auth: { type: 'none', header_name: '', credential_env: '' } });
  let pageStatus = $state<'loading' | 'ready' | 'signed-out' | 'forbidden' | 'error'>('loading');
  let servers = $state<AdminMcp[]>([]);
  let form = $state<RegistryEntry>(blank());
  let existing = $state(false);
  let iconUrl = $state<string | null>(null);
  let iconFile = $state<File | null>(null);
  let fileInput = $state<HTMLInputElement>();
  let busy = $state(false);
  let error = $state('');
  let notice = $state('');
  let confirmDelete = $state(false);

  function failure(reason: unknown) {
    error = reason instanceof Error ? reason.message : 'The request failed.';
    if (reason instanceof RequestError && reason.status === 401) pageStatus = 'signed-out';
    if (reason instanceof RequestError && reason.status === 403) pageStatus = 'forbidden';
  }
  async function refresh() {
    const result = await adminMcps();
    servers = result.servers;
  }
  async function load() {
    pageStatus = 'loading'; error = '';
    try { await refresh(); pageStatus = 'ready'; }
    catch (reason) {
      pageStatus = 'error'; failure(reason);
      if (reason instanceof RequestError && [401, 403].includes(reason.status)) error = '';
    }
  }
  function edit(server?: AdminMcp) {
    form = server ? { id: server.id, title: server.title, description: server.description, backend_url: server.backend_url, public_url: server.public_url ?? '', enabled: server.enabled, approval_policy: server.approval_policy, auth: { ...server.auth } } : blank();
    existing = !!server; iconUrl = server?.icon_url ?? null; iconFile = null;
    if (fileInput) fileInput.value = '';
    error = ''; notice = ''; confirmDelete = false;
  }
  async function save(event: SubmitEvent) {
    event.preventDefault(); busy = true; error = ''; notice = '';
    try {
      let saved = await saveMcp(form, existing);
      existing = true;
      iconUrl = saved.icon_url;
      notice = 'Registry entry saved.';
      if (iconFile) {
        saved = await uploadMcpIcon(form.id, iconFile);
        iconUrl = saved.icon_url; iconFile = null;
        if (fileInput) fileInput.value = '';
        notice = 'Registry entry and icon saved.';
      }
    } catch (reason) { failure(reason); }
    finally {
      try { await refresh(); } catch (reason) { failure(reason); }
      busy = false;
    }
  }
  async function removeIcon() {
    busy = true; error = ''; notice = '';
    try { await deleteMcpIcon(form.id); iconUrl = null; await refresh(); notice = 'Icon removed.'; }
    catch (reason) { failure(reason); } finally { busy = false; }
  }
  async function remove() {
    busy = true; error = ''; notice = '';
    try { await deleteMcp(form.id); edit(); await refresh(); notice = 'Registry entry deleted.'; }
    catch (reason) { failure(reason); } finally { busy = false; confirmDelete = false; }
  }
  async function signIn() {
    busy = true; error = '';
    try { await login('/admin'); } catch (reason) { failure(reason); busy = false; }
  }
  onMount(() => { void load(); });
</script>

<svelte:head><title>MCP Registry | {APP_NAME}</title></svelte:head>
<section class="admin-page">
  <a class="quiet-link" href="/"><ArrowLeft size={16} /> Explore</a>
  <div class="admin-heading"><div><p class="eyebrow">Administration</p><h1>MCP Registry</h1></div>
    {#if pageStatus === 'ready' || pageStatus === 'forbidden'}<button onclick={() => logout()} title="Sign out"><LogOut size={17} /> Sign out</button>{/if}
  </div>
  {#if error}<p class="admin-error" role="alert">{error}</p>{/if}
  {#if notice}<p class="admin-notice" role="status"><Check size={17} /> {notice}</p>{/if}
  {#if pageStatus === 'loading'}
    <p role="status" class="muted">Loading registry...</p>
  {:else if pageStatus === 'signed-out'}
    <div class="access-state"><ShieldCheck size={32} strokeWidth={1.5} /><h2>Administrator sign-in</h2><button class="primary" onclick={signIn} disabled={busy}><LogIn size={17} /> Sign in</button></div>
  {:else if pageStatus === 'forbidden'}
    <div class="access-state"><ShieldCheck size={32} strokeWidth={1.5} /><h2>Admin access required</h2><p class="muted">This account is not a member of the admin group.</p></div>
  {:else if pageStatus === 'error'}
    <button onclick={load}><RefreshCw size={17} /> Retry</button>
  {:else}
    <div class="registry-layout">
      <aside aria-label="Registry entries">
        <div class="list-heading"><h2>Servers <span class="muted">{servers.length}</span></h2><button class="icon" title="Add MCP" aria-label="Add MCP" disabled={busy} onclick={() => edit()}><Plus size={20} /></button></div>
        {#each servers as server (server.id)}
          <button class="server-row" class:selected={existing && form.id === server.id} disabled={busy} onclick={() => edit(server)}>
            {#if server.icon_url}<img src={`${apiBase()}${server.icon_url}`} alt="" width="28" height="28" />{:else}<Blocks size={24} strokeWidth={1.5} />{/if}
            <span class="server-name"><strong>{server.title}</strong><small>{server.enabled ? server.id : 'Disabled'}</small></span>
            <span class="status-dot" class:enabled={server.enabled} aria-label={server.enabled ? 'Enabled' : 'Disabled'}></span>
          </button>
        {:else}<p class="muted">No MCPs registered.</p>{/each}
      </aside>
      <form onsubmit={save}>
        <div class="form-heading"><h2>{existing ? 'Edit MCP' : 'New MCP'}</h2><span class="muted">{existing ? form.id : 'Streamable HTTP'}</span></div>
        <fieldset disabled={busy}>
          <div class="field-pair">
            <label>Registry ID<input bind:value={form.id} required pattern={'[a-z0-9][a-z0-9\\-]{1,62}'} maxlength="63" disabled={existing} placeholder="my-mcp" /></label>
            <label>Title<input bind:value={form.title} required maxlength="200" /></label>
          </div>
          <label>Description<textarea bind:value={form.description} rows="3" maxlength="4000"></textarea></label>
          <label>Backend endpoint<input bind:value={form.backend_url} type="url" required maxlength="2048" placeholder="http://museum-guide:8000/mcp" /></label>
          <label>Public endpoint <span class="muted">(optional)</span><input bind:value={form.public_url} type="url" maxlength="2048" placeholder="https://example.com/mcp" /></label>
          <div class="field-pair">
            <label>Authentication<select bind:value={form.auth.type}><option value="none">None</option><option value="bearer">Bearer token</option><option value="header">Custom header</option></select></label>
            <label>Tool approval<select bind:value={form.approval_policy}><option value="ask_destructive">Ask for destructive tools</option><option value="ask_always">Always ask</option><option value="auto">Automatic</option></select></label>
          </div>
          {#if form.auth.type !== 'none'}<label>Credential environment variable<input bind:value={form.auth.credential_env} required pattern="[A-Za-z_][A-Za-z0-9_]*" maxlength="128" placeholder="MY_MCP_TOKEN" /></label>{/if}
          {#if form.auth.type === 'header'}<label>Header name<input bind:value={form.auth.header_name} required maxlength="128" placeholder="X-API-Key" /></label>{/if}
          <div class="icon-field">
            {#if iconUrl}<img src={`${apiBase()}${iconUrl}`} alt="Current MCP icon" width="48" height="48" />{:else}<span class="icon-placeholder"><Blocks size={26} strokeWidth={1.5} /></span>{/if}
            <label>Icon <span class="muted">(SVG or PNG, up to 1 MB)</span><input bind:this={fileInput} type="file" accept="image/svg+xml,image/png,.svg,.png" onchange={event => { const file = event.currentTarget.files?.[0] ?? null; if (file && file.size > 1024 * 1024) { error = 'Choose an icon no larger than 1 MB.'; event.currentTarget.value = ''; iconFile = null; } else { iconFile = file; error = ''; } }} /></label>
            {#if iconUrl}<button type="button" class="icon" title="Remove current icon" aria-label="Remove current icon" onclick={removeIcon}><X size={18} /></button>{/if}
          </div>
          <label class="enabled-field"><input type="checkbox" bind:checked={form.enabled} /> Enabled</label>
          <div class="form-actions"><button type="submit" class="primary"><Save size={17} /> {busy ? 'Saving...' : 'Save MCP'}</button>
            {#if existing}<button type="button" class="icon delete-action" title="Delete MCP" aria-label="Delete MCP" onclick={() => { confirmDelete = true; }}><Trash2 size={18} /></button>{/if}
          </div>
          {#if confirmDelete}<div class="delete-confirm" role="alert"><p>Delete {form.title}?</p><button type="button" onclick={remove}><Trash2 size={16} /> Delete</button><button type="button" onclick={() => { confirmDelete = false; }}>Cancel</button></div>{/if}
        </fieldset>
      </form>
    </div>
  {/if}
</section>

<style>
  .admin-page { --line: #dcd9d2; --surface: #fff; max-width: 1140px; margin: 0 auto; padding: 36px 28px 72px; letter-spacing: 0; }
  :global(html[data-theme='dark']) .admin-page { --line: #45413d; --surface: #252320; }
  .admin-heading, .list-heading, .form-heading, .form-actions { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
  .admin-heading { margin: 24px 0 32px; flex-wrap: wrap; }
  .admin-heading h1 { font-size: 2rem; margin: 6px 0 0; }
  .eyebrow { letter-spacing: 0; margin: 0; }
  .registry-layout { display: grid; grid-template-columns: minmax(200px, 280px) minmax(0, 1fr); border-top: 1px solid var(--line); }
  aside { padding: 16px 24px 0 0; border-right: 1px solid var(--line); min-width: 0; }
  h2 { font-size: 1.1rem; margin: 0; }
  .list-heading { margin-bottom: 16px; }
  .server-row { width: 100%; text-align: left; justify-content: flex-start; padding: 14px 8px; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; gap: 12px; }
  .server-row.selected { background: var(--surface); box-shadow: inset 3px 0 #c52d30; }
  .server-name { display: flex; flex-direction: column; gap: 4px; flex: 1; min-width: 0; overflow-wrap: anywhere; }
  .server-name strong { font-size: .9rem; font-weight: 500; }
  .server-name small { color: #77736c; }
  img { object-fit: contain; flex-shrink: 0; }
  .server-row :global(svg) { flex-shrink: 0; }
  .status-dot { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #92908a; }
  .status-dot.enabled { background: #368069; }
  form { min-width: 0; padding: 24px 0 0 32px; }
  .form-heading { margin-bottom: 24px; flex-wrap: wrap; font-size: .8rem; overflow-wrap: anywhere; }
  fieldset { padding: 0; margin: 0; border: 0; min-width: 0; }
  label { display: flex; flex-wrap: wrap; gap: 7px; font-size: .85rem; margin-bottom: 18px; min-width: 0; }
  input:not([type='checkbox']), textarea, select { width: 100%; min-width: 0; border: 1px solid var(--line); background: var(--surface); color: inherit; padding: 10px 12px; border-radius: 3px; }
  input:focus, textarea:focus, select:focus { outline: 2px solid #c52d30; outline-offset: 2px; }
  input:disabled { opacity: .6; }
  textarea { resize: vertical; }
  .field-pair { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
  .icon-field { display: flex; align-items: center; gap: 14px; margin: 4px 0 20px; }
  .icon-field label { flex: 1; margin: 0; }
  .icon-placeholder { display: grid; place-items: center; width: 48px; height: 48px; flex: 0 0 48px; border: 1px solid var(--line); }
  input[type='file'] { padding: 7px; font-size: .78rem; }
  .enabled-field { align-items: center; gap: 10px; }
  input[type='checkbox'] { width: 17px; height: 17px; accent-color: #c52d30; }
  .form-actions { border-top: 1px solid var(--line); padding-top: 20px; }
  .admin-error, .admin-notice { padding: 12px 0; overflow-wrap: anywhere; }
  .admin-error { color: #bd3434; }
  .admin-notice { display: flex; align-items: center; gap: 8px; color: #368069; }
  .access-state { padding: 32px 0; border-top: 1px solid var(--line); }
  .access-state h2 { margin: 18px 0; }
  .delete-action { color: #bd3434; }
  .delete-confirm { border-top: 1px solid var(--line); margin-top: 20px; }
  .delete-confirm button { margin-right: 8px; }
  @media (max-width: 720px) {
    .admin-page { padding: 24px 18px 48px; }
    .registry-layout { grid-template-columns: minmax(0, 1fr); }
    aside { border-right: 0; border-bottom: 1px solid var(--line); padding: 16px 0 24px; }
    form { padding: 24px 0 0; }
    .field-pair { grid-template-columns: minmax(0, 1fr); gap: 0; }
  }
</style>