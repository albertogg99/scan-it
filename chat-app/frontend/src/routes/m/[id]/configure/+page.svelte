<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { ArrowLeft, Copy, ExternalLink } from 'lucide-svelte';
  import { publicMcp } from '$lib/api/client';
  import type { PublicMcp } from '$lib/api/types';
  import { clientGuides } from '$lib/config/clients';
  import { ui } from '$lib/ui';
  const context = ui();
  const { t } = context;
  const id = $derived(page.params.id ?? '');
  let mcp = $state<PublicMcp | null>(null);
  let error = $state('');
  let selected = $state(0);
  let copied = $state(false);
  let endpointInput = $state<HTMLInputElement>();
  onMount(() => { publicMcp(id).then(value => mcp = value).catch(reason => error = reason instanceof Error ? reason.message : t('error')); });
  async function copy() {
    if (!mcp?.public_endpoint) return;
    try {
      if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(mcp.public_endpoint); copied = true; }
      else { endpointInput?.select(); copied = document.execCommand('copy'); }
    } catch { endpointInput?.select(); copied = false; }
  }
</script>
<section class="page-wrap setup"><a class="quiet-link" href="/m/{page.params.id}"><ArrowLeft size={16} />{t('back')}</a>
{#if error}<h1>{error}</h1>{:else if !mcp}<p role="status">{t('loading')}...</p>{:else}
  <p class="eyebrow">{mcp.title} / MCP</p><h1>{t('configure')}</h1>
  {#if mcp.requires_credentials}<p class="notice" role="status">{t('staticAuth')} {t('localOnly')}</p>{/if}
  {#if mcp.public_endpoint}<div class="endpoint"><div><label class="eyebrow" for="mcp-endpoint">{t('endpoint')}</label><input id="mcp-endpoint" bind:this={endpointInput} readonly value={mcp.public_endpoint} onclick={event => event.currentTarget.select()} /></div><button class="icon" title={t('copy')} aria-label={t('copy')} onclick={copy}><Copy size={19} /></button></div>{#if copied}<p role="status">{t('copied')}</p>{/if}{/if}
  <div class="tabs" role="tablist" aria-label={t('clientSteps')}>{#each clientGuides as guide, index}<button role="tab" aria-selected={selected === index} class:active={selected === index} onclick={() => selected = index}>{guide.name}</button>{/each}</div>
  <section role="tabpanel" class="guide"><h2>{clientGuides[selected].name}</h2><ol>{#each clientGuides[selected].steps[context.locale] as step}<li>{#each step.split('{endpoint}') as segment, index}{#if index > 0}<code>{mcp.public_endpoint || t('endpoint')}</code>{/if}{segment.replaceAll('{name}', mcp.title)}{/each}</li>{/each}</ol><a href={clientGuides[selected].documentation} target="_blank" rel="noopener noreferrer" class="quiet-link">{t('docs')} <ExternalLink size={16} /></a></section>
{/if}</section>

<style>
  h1 { font-size: 2rem; letter-spacing: 0; overflow-wrap: anywhere; }
  .endpoint > div { flex: 1; min-width: 0; }
  .endpoint input { display: block; width: 100%; border: 0; background: transparent; color: inherit; padding: 8px 0; font-family: ui-monospace, monospace; font-size: .9rem; }
  .endpoint button { flex: 0 0 auto; }
  .guide li { margin-bottom: 14px; line-height: 1.65; overflow-wrap: anywhere; }
  .guide code { color: inherit; user-select: all; }
  .tabs { flex-wrap: wrap; }
</style>