<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { ArrowUpRight, Blocks, Search, X } from 'lucide-svelte';
  import { publicMcps } from '$lib/api/client';
  import { apiBase } from '$lib/config/runtime';
  import { ui } from '$lib/ui';
  const { t } = ui();
  type App = { id: string; title: string; description: string; icon_url?: string | null };
  let apps = $state<App[]>([]);
  let status = $state<'loading' | 'ready' | 'error'>('loading');
  let query = $state('');
  let active = $state(0);
  let input = $state<HTMLInputElement>();

  const normalize = (value: string) => value.normalize('NFD').replace(/\p{Diacritic}/gu, '').toLowerCase();
  const words = (value: string) => value.split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  const subsequence = (needle: string, hay: string) => { let i = 0; for (const char of hay) if (char === needle[i]) i++; return i === needle.length; };
  function tokenScore(token: string, title: string, id: string, description: string): number | null {
    if (title.startsWith(token)) return 100;
    if (words(title).some((word) => word.startsWith(token))) return 80;
    if (title.includes(token)) return 60;
    if (id.includes(token)) return 45;
    if (words(description).some((word) => word.startsWith(token))) return 30;
    if (description.includes(token)) return 20;
    if (token.length > 1 && subsequence(token, title)) return 10;
    return null;
  }
  const ranked = $derived.by(() => {
    const tokens = words(normalize(query));
    if (!tokens.length) return apps;
    return apps
      .map((app) => {
        const [title, id, description] = [normalize(app.title), app.id, normalize(app.description)];
        let score = 0;
        for (const token of tokens) { const s = tokenScore(token, title, id, description); if (s === null) return null; score += s; }
        return { app, score };
      })
      .filter((entry): entry is { app: App; score: number } => entry !== null)
      .sort((a, b) => b.score - a.score || a.app.title.length - b.app.title.length)
      .map((entry) => entry.app);
  });

  $effect(() => { void query; active = 0; });
  function onKeydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') { event.preventDefault(); active = Math.min(active + 1, ranked.length - 1); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); active = Math.max(active - 1, 0); }
    else if (event.key === 'Enter' && ranked[active]) void goto(`/m/${ranked[active].id}`);
    else if (event.key === 'Escape') query = '';
  }
  function onGlobalKeydown(event: KeyboardEvent) {
    const target = event.target as HTMLElement;
    if (event.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) && !target.isContentEditable) { event.preventDefault(); input?.focus(); }
  }
  onMount(async () => { try { apps = await publicMcps(); status = 'ready'; } catch { status = 'error'; } });
</script>
<svelte:window onkeydown={onGlobalKeydown} />
<section class="page-wrap">
  <p class="eyebrow">01 / {t('explore')}</p><h1>{t('explore')}</h1>
  <label class="app-search">
    <Search size={19} strokeWidth={1.5} />
    <span class="sr-only">{t('search')}</span>
    <input bind:this={input} bind:value={query} onkeydown={onKeydown} type="search" placeholder={t('search')} autocomplete="off" spellcheck="false" aria-controls="app-results" />
    {#if query}<button type="button" class="app-search-clear" aria-label={t('clearSearch')} onclick={() => { query = ''; input?.focus(); }}><X size={17} /></button>{:else}<kbd>/</kbd>{/if}
  </label>
  <div class="demo-list" id="app-results" aria-live="polite">
    {#each ranked as app, index (app.id)}
      {@const Icon = Blocks}
      <a href="/m/{app.id}" class="demo-row" class:active={query && index === active}>{#if app.icon_url}<img class="mcp-icon" src={`${apiBase()}${app.icon_url}`} alt="" width="32" height="32" />{:else}<Icon size={27} strokeWidth={1.5} />{/if}<div class="demo-text"><strong>{app.title}</strong>{#if app.description}<small>{app.description}</small>{/if}</div><span>{t('open')} <ArrowUpRight size={19} /></span></a>
    {:else}
      <p class="demo-empty muted">{t(status === 'loading' ? 'loading' : status === 'error' ? 'unavailable' : 'noResults')}</p>
    {/each}
  </div>
</section>

<style>
  .mcp-icon { object-fit: contain; flex: 0 0 32px; }
</style>