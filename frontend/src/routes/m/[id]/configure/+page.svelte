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
  onMount(() => { publicMcp(id).then(value => mcp = value).catch(reason => error = reason instanceof Error ? reason.message : t('error')); });
  function usable(cloud: boolean) { if (!mcp?.public_endpoint || mcp.requires_credentials) return false; if (!cloud) return true; try { const url = new URL(mcp.public_endpoint); return url.protocol === 'https:' && url.hostname.includes('.') && !/\.(local|localhost|internal|test|example)$/.test(url.hostname) && !/^(127\.|10\.|192\.168\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(url.hostname); } catch { return false; } }
  async function copy() { if (mcp?.public_endpoint) { try { await navigator.clipboard.writeText(mcp.public_endpoint); copied = true; } catch { copied = false; } } }
</script>
<section class="page-wrap setup"><a class="quiet-link" href="/m/{page.params.id}"><ArrowLeft size={16} />{t('back')}</a>
{#if error}<h1>{error}</h1>{:else if !mcp}<p role="status">{t('loading')}...</p>{:else}
  <p class="eyebrow">{mcp.title} / MCP</p><h1>{t('configure')}</h1>
  {#if mcp.requires_credentials}<p class="notice" role="status">{t('staticAuth')} {t('localOnly')}</p>{/if}
  {#if !mcp.public_endpoint}<p class="notice" role="status">{t('publicMissing')}</p>{:else}<div class="endpoint"><div><span class="eyebrow">{t('endpoint')}</span><code>{mcp.public_endpoint}</code></div><button class="icon" title={t('copy')} aria-label={t('copy')} onclick={copy}><Copy size={19} /></button></div>{#if copied}<p role="status">{t('copied')}</p>{/if}{/if}
  <p class="muted">{t('publicHttps')}</p>
  <div class="tabs" role="tablist" aria-label={t('clientSteps')}>{#each clientGuides as guide, index}<button role="tab" aria-selected={selected === index} class:active={selected === index} onclick={() => selected = index}>{guide.name}</button>{/each}</div>
  <section role="tabpanel" class="guide"><h2>{clientGuides[selected].name}</h2>{#if !usable(clientGuides[selected].cloud)}<p class="notice" role="status">{t('unsupported')}</p>{/if}<ol>{#each clientGuides[selected].steps[context.locale] as step}<li>{step}</li>{/each}</ol><a href={clientGuides[selected].documentation} target="_blank" rel="noopener noreferrer" class="quiet-link">{t('docs')} <ExternalLink size={16} /></a></section>
{/if}</section>