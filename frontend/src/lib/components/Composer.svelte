<script lang="ts">
  import { ImagePlus, Send, X } from 'lucide-svelte';
  import { onDestroy } from 'svelte';
  import type { PublicConfig } from '$lib/api/types';
  import { ui } from '$lib/ui';
  const { t } = ui();
  let { config, busy, onSend }: { config: PublicConfig | null; busy: boolean; onSend: (text: string, file: File | null) => Promise<void> } = $props();
  let text = $state('');
  let file = $state<File | null>(null);
  let preview = $state('');
  let error = $state('');
  let input: HTMLInputElement;
  function select(event: Event) {
    const selected = (event.currentTarget as HTMLInputElement).files?.[0];
    if (!selected) return;
    if (!['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(selected.type)) { error = t('imageType'); return; }
    if (selected.size > 5242880) { error = t('imageLarge'); return; }
    if (preview) URL.revokeObjectURL(preview);
    file = selected; preview = URL.createObjectURL(selected); error = '';
  }
  function remove() { if (preview) URL.revokeObjectURL(preview); preview = ''; file = null; if (input) input.value = ''; }
  onDestroy(() => { if (preview) URL.revokeObjectURL(preview); });
  async function submit() {
    if (busy || !text.trim() || !config?.llm_configured) return;
    try { await onSend(text.trim(), file); text = ''; remove(); error = ''; } catch (reason) { error = reason instanceof Error ? reason.message : t('error'); }
  }
</script>
<form class="composer" onsubmit={event => { event.preventDefault(); void submit(); }}>
  {#if preview}<div class="preview"><img src={preview} alt={file?.name || ''} /><button type="button" class="icon" title={t('remove')} aria-label={t('remove')} onclick={remove}><X size={18} /></button></div>{/if}
  <textarea aria-label={t('message')} placeholder={t('message')} bind:value={text} maxlength="8000" rows="2" disabled={busy} onkeydown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); } }}></textarea>
  <div class="composer-actions"><div><input class="sr-only" type="file" accept="image/png,image/jpeg,image/webp,image/gif" bind:this={input} onchange={select} disabled={config?.image_input !== 'supported' || !config?.llm_configured || busy} /><button type="button" class="icon" title={t('upload')} aria-label={t('upload')} disabled={config?.image_input !== 'supported' || !config?.llm_configured || busy} onclick={() => input.click()}><ImagePlus size={19} /></button><small>{config?.image_input === 'unknown' ? t('imageUnknown') : config?.image_input === 'unsupported' ? t('imageUnsupported') : ''}</small></div><button class="primary" type="submit" disabled={busy || !text.trim() || !config?.llm_configured}><Send size={17} />{t('send')}</button></div>
  {#if config && !config.llm_configured}<p role="status" class="notice">{t('llmMissing')}</p>{/if}
  {#if error}<p role="alert" class="notice">{error}</p>{/if}
</form>