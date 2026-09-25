<script lang="ts">
  import { Mic, Plus, Send, Square, X } from 'lucide-svelte';
  import { onDestroy } from 'svelte';
  import type { PublicConfig } from '$lib/api/types';
  import { toWav } from '$lib/audio/wav';
  import { ui } from '$lib/ui';
  const { t } = ui();
  const MAX_UPLOAD = 5242880;
  const MAX_SECONDS = 120;
  let { config, busy, onSend }: { config: PublicConfig | null; busy: boolean; onSend: (text: string, files: File[]) => Promise<void> } = $props();
  let text = $state('');
  let file = $state<File | null>(null);
  let preview = $state('');
  let audio = $state<File | null>(null);
  let audioPreview = $state('');
  let recording = $state(false);
  let converting = $state(false);
  let seconds = $state(0);
  let error = $state('');
  let input = $state<HTMLInputElement>();
  let textarea = $state<HTMLTextAreaElement>();
  let recorder: MediaRecorder | null = null;
  let microphone: MediaStream | null = null;
  let timer: ReturnType<typeof setInterval> | undefined;
  const imageOn = $derived(config?.image_input === 'supported');
  const audioOn = $derived(config?.audio_input === 'supported');
  const canSend = $derived(!busy && !recording && !converting && !!config?.llm_configured && (!!text.trim() || !!audio));
  const clock = $derived(`${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`);
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
    if (selected.size > MAX_UPLOAD) { error = t('imageLarge'); return; }
    if (preview) URL.revokeObjectURL(preview);
    file = selected; preview = URL.createObjectURL(selected); error = '';
  }
  function remove() { if (preview) URL.revokeObjectURL(preview); preview = ''; file = null; if (input) input.value = ''; }
  function removeAudio() { if (audioPreview) URL.revokeObjectURL(audioPreview); audioPreview = ''; audio = null; }
  function release() { clearInterval(timer); microphone?.getTracks().forEach(track => track.stop()); microphone = null; recorder = null; recording = false; }
  async function record() {
    if (recording) { recorder?.stop(); return; }
    error = '';
    try { microphone = await navigator.mediaDevices.getUserMedia({ audio: true }); } catch { error = t('micDenied'); return; }
    const chunks: Blob[] = [];
    recorder = new MediaRecorder(microphone);
    recorder.ondataavailable = event => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = async () => {
      release();
      converting = true;
      try {
        const wav = await toWav(new Blob(chunks, { type: chunks[0]?.type }));
        if (wav.size > MAX_UPLOAD) { error = t('audioLong'); return; }
        removeAudio(); audio = wav; audioPreview = URL.createObjectURL(wav);
      } catch { error = t('audioFailed'); }
      finally { converting = false; }
    };
    recorder.start();
    recording = true; seconds = 0;
    timer = setInterval(() => { seconds += 1; if (seconds >= MAX_SECONDS) recorder?.stop(); }, 1000);
  }
  onDestroy(() => {
    if (recorder) recorder.onstop = null;
    recorder?.stop(); release();
    if (preview) URL.revokeObjectURL(preview);
    if (audioPreview) URL.revokeObjectURL(audioPreview);
  });
  async function submit() {
    if (!canSend) return;
    try { await onSend(text.trim(), [file, audio].filter((item): item is File => !!item)); text = ''; remove(); removeAudio(); error = ''; } catch (reason) { error = reason instanceof Error ? reason.message : t('error'); }
  }
</script>
<form class="composer" onsubmit={event => { event.preventDefault(); void submit(); }}>
  {#if preview || audioPreview}<div class="attachments">
    {#if preview}<div class="preview"><img src={preview} alt={file?.name || ''} /><button type="button" class="icon" title={t('remove')} aria-label={t('remove')} onclick={remove}><X size={18} /></button></div>{/if}
    {#if audioPreview}<div class="preview audio-preview"><audio controls src={audioPreview}></audio><button type="button" class="icon" title={t('removeAudio')} aria-label={t('removeAudio')} onclick={removeAudio}><X size={18} /></button></div>{/if}
  </div>{/if}
  <div class="message-input">
    {#if imageOn}<input class="sr-only" type="file" accept="image/png,image/jpeg,image/webp,image/gif" bind:this={input} onchange={select} disabled={!config?.llm_configured || busy} /><button type="button" class="icon side-button" title={t('upload')} aria-label={t('upload')} disabled={!config?.llm_configured || busy} onclick={() => input?.click()}><Plus size={19} /></button>{/if}
    <textarea class:padded={!imageOn} aria-label={t('message')} placeholder={recording ? t('recording') : t('message')} bind:value={text} bind:this={textarea} maxlength="8000" rows="1" disabled={busy} onkeydown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); void submit(); } }}></textarea>
    {#if recording}<span class="recording-clock" role="status"><span class="dot"></span>{clock}</span>{/if}
    {#if audioOn}<button type="button" class="icon side-button" class:recording title={recording ? t('stopRecording') : t('record')} aria-label={recording ? t('stopRecording') : t('record')} aria-pressed={recording} disabled={!config?.llm_configured || busy || converting} onclick={() => void record()}>{#if recording}<Square size={16} />{:else}<Mic size={19} />{/if}</button>{/if}
    <button class="icon send-button" type="submit" title={t('send')} aria-label={t('send')} disabled={!canSend}><Send size={19} /></button>
  </div>
  {#if config && !config.llm_configured}<p role="status" class="notice">{t('unavailable')}</p>{/if}
  {#if error}<p role="alert" class="notice">{error}</p>{/if}
</form>

<style>
  .message-input { display: flex; align-items: flex-end; gap: 4px; padding: 8px; border: 1px solid #c6c2bb; border-radius: 14px; transition: border-color .15s; }
  .message-input:focus-within { border-color: #be272b; }
  :global(html[data-theme='dark']) .message-input { border-color: #45413d; }
  :global(html[data-theme='dark']) .message-input:focus-within { border-color: #be272b; }
  .message-input textarea { flex: 1; display: block; min-height: 0; max-height: min(45vh, 360px); margin: 0; padding: 7px 4px; border: 0; border-radius: 0; resize: none; overflow-y: auto; line-height: 1.5; outline: none; }
  .message-input textarea.padded { padding-left: 8px; }
  .send-button, .side-button { flex: none; width: 34px; height: 34px; padding: 7px; border-radius: 8px; display: inline-flex; align-items: center; justify-content: center; }
  .send-button:hover, .side-button:hover { background: rgb(127 127 127 / 12%); }
  .side-button.recording { background: #be272b; color: #fff; }
  .side-button.recording:hover { background: #a32025; }
  .recording-clock { flex: none; display: inline-flex; align-items: center; gap: 6px; height: 34px; font-size: .8rem; font-variant-numeric: tabular-nums; color: #be272b; }
  .dot { width: 8px; height: 8px; border-radius: 50%; background: #be272b; animation: pulse 1s ease-in-out infinite alternate; }
  .attachments { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 8px; }
  .audio-preview { align-items: center; }
  .audio-preview audio { height: 36px; max-width: min(100%, 300px); }
  @keyframes pulse { to { opacity: .3; } }
  @media (prefers-reduced-motion: reduce) { .dot { animation: none; } }
</style>