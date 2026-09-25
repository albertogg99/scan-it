<script lang="ts">
  import { onMount } from 'svelte';
  import { attachmentUrl } from '$lib/api/client';
  import { ui } from '$lib/ui';
  let { id, mime } = $props<{ id: string; mime: string }>();
  const { t } = ui();
  let src = $state('');
  let error = $state(false);
  onMount(() => { let url = ''; let active = true; attachmentUrl(id).then(value => { url = value; if (active) src = value; else URL.revokeObjectURL(value); }).catch(() => error = true); return () => { active = false; if (url) URL.revokeObjectURL(url); }; });
</script>
  {#if error}<span role="status">{t(mime.startsWith('audio/') ? 'audioUnavailable' : 'imageUnavailable')}</span>{:else if src && mime.startsWith('audio/')}<audio class="attachment-audio" controls {src}></audio>{:else if src}<img class="attachment-image" {src} alt={t('upload')} />{:else}<span>{t('loading')}...</span>{/if}

<style>
  .attachment-audio { display: block; margin: 12px 0; max-width: min(100%, 340px); height: 40px; }
</style>