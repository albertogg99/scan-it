<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { ArrowRight, MessageSquare, Settings2, ArrowLeft } from 'lucide-svelte';
  import { publicMcp, publicHealth, RequestError } from '$lib/api/client';
  import type { PublicMcp } from '$lib/api/types';
  import { ui } from '$lib/ui';
  const { t } = ui();
  const id = $derived(page.params.id ?? '');
  let mcp = $state<PublicMcp | null>(null);
  let reachable = $state<boolean | null>(null);
  let error = $state('');
  async function load() {
    error = ''; reachable = null;
    try { mcp = await publicMcp(id); try { reachable = (await publicHealth(id)).reachable; } catch { reachable = false; } }
    catch (reason) { error = reason instanceof RequestError && reason.status === 404 ? t('notFound') : reason instanceof Error ? reason.message : t('error'); }
  }
  onMount(() => { void load(); });
</script>
<section class="page-wrap identity"><a class="quiet-link" href="/"><ArrowLeft size={16} />{t('back')}</a>
  {#if error}<h1>{error}</h1><button onclick={load}>{t('retry')}</button>
  {:else if !mcp}<p role="status">{t('loading')}...</p>
  {:else}<p class="eyebrow">MCP / {mcp.id}</p><h1>{mcp.title}</h1><p class="lead">{mcp.description}</p>
    {#if reachable === false}<p role="alert" class="notice">{t('disconnected')}</p>{/if}
    <div class="choices"><a class:disabled={reachable === false} aria-disabled={reachable === false} href={reachable === false ? undefined : `/m/${encodeURIComponent(mcp.id)}/chat`}><MessageSquare size={23} /><span><strong>{t('chat')}</strong><small>{t('choose')}</small></span><ArrowRight size={18} /></a><a href="/m/{encodeURIComponent(mcp.id)}/configure"><Settings2 size={23} /><span><strong>{t('configure')}</strong><small>{t('clientSteps')}</small></span><ArrowRight size={18} /></a></div>
  {/if}
</section>