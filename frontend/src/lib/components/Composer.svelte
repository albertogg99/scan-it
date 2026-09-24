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
  let input = $state<HTMLInputElement>();
  let textarea = $state<HTMLTextAreaElement>();
  $effect(() => {
    void text;
    if (!textarea) return;
    textarea.style.height = 'auto';
    textarea.style.height = `${textarea.scrollHeight}px`;
  });
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
  <div class="message-input">
    <textarea aria-label={t('message')} placeholder={t('message')} bind:value={text} bind:this={textarea} maxlength="8000" rows="1" disabled={busy} onkeydown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); } }}></textarea>
    <button class="icon send-button" type="submit" title={t('send')} aria-label={t('send')} disabled={busy || !text.trim() || !config?.llm_configured}><Send size={19} /></button>
  </div>
  {#if config?.image_input === 'supported'}<div class="composer-actions"><input class="sr-only" type="file" accept="image/png,image/jpeg,image/webp,image/gif" bind:this={input} onchange={select} disabled={!config.llm_configured || busy} /><button type="button" class="icon" title={t('upload')} aria-label={t('upload')} disabled={!config.llm_configured || busy} onclick={() => input?.click()}><ImagePlus size={19} /></button></div>{/if}
  {#if config && !config.llm_configured}<p role="status" class="notice">{t('unavailable')}</p>{/if}
  {#if error}<p role="alert" class="notice">{error}</p>{/if}
</form>

<style>
  .message-input { display: flex; align-items: flex-end; gap: 8px; padding: 8px 8px 8px 16px; border: 1px solid #c6c2bb; border-radius: 14px; transition: border-color .15s; }
  .message-input:focus-within { border-color: #be272b; }
  :global(html[data-theme='dark']) .message-input { border-color: #45413d; }
  :global(html[data-theme='dark']) .message-input:focus-within { border-color: #be272b; }
  .message-input textarea { flex: 1; display: block; min-height: 0; max-height: min(45vh, 360px); margin: 0; padding: 7px 0; border: 0; border-radius: 0; resize: none; overflow-y: auto; line-height: 1.5; outline: none; }
  .send-button { flex: none; width: 34px; height: 34px; padding: 7px; border-radius: 8px; }
  .send-button:hover { background: rgb(127 127 127 / 12%); }
</style>