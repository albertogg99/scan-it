<script lang="ts">
  import { ChevronRight } from 'lucide-svelte';
  import type { Message, ToolCall } from '$lib/api/types';
  import { decide } from '$lib/api/client';
  import ImageAttachment from './ImageAttachment.svelte';
  import Markdown from './Markdown.svelte';
  import { groupParts } from './message-content';
  import { scrollViewport } from './scroll-viewport';
  import { ui } from '$lib/ui';
  const { t } = ui();
  let { messages, onChange }: { messages: Message[]; onChange: () => void } = $props();
  let deciding = $state<string | null>(null);
  let decisionError = $state('');
  const lastUserMessage = $derived(messages.filter(message => message.role === 'user').at(-1)?.id);
  async function decision(call: ToolCall, approved: boolean) {
    deciding = call.id; decisionError = '';
    try { await decide(call.id, approved); onChange(); } catch (error) { decisionError = error instanceof Error ? error.message : t('error'); }
    finally { deciding = null; }
  }
</script>
<div class="timeline" role="log" aria-live="polite" use:scrollViewport={{ follow: true, resetKey: lastUserMessage }}>
  <div class="timeline-content">
  {#if messages.length === 0}<p class="empty">{t('empty')}</p>{/if}
  {#each messages as message (message.id)}
    <article class:user-message={message.role === 'user'} class="message">
      <div class="message-meta">{message.role === 'user' ? t('message') : message.status === 'failed' ? t('failed') : message.status === 'cancelled' ? t('cancelled') : ''}</div>
      {#if message.role === 'user'}
        <p class="message-text">{message.content}</p>
      {:else if message.parts?.length}
        {#each groupParts(message.parts) as part}
          {#if part.type === 'text'}<Markdown text={part.text} />
          {:else if part.type === 'reasoning'}<details class="reasoning"><summary>{t('reasoning')}</summary><Markdown text={part.text} /></details>
          {:else}{@const call = message.tool_calls.find(item => item.id === part.tool_call_id)}{#if call}{@render tool(call)}{/if}{/if}
        {/each}
      {:else}
        {#if message.content}<Markdown text={message.content} />{/if}
        {#if message.reasoning}<details class="reasoning"><summary>{t('reasoning')}</summary><Markdown text={message.reasoning} /></details>{/if}
        {#each message.tool_calls as call}{@render tool(call)}{/each}
      {/if}
      {#each message.attachments as attachment}<ImageAttachment id={attachment.id} mime={attachment.mime} />{/each}
      {#if message.error}<p role="alert" class="notice">{message.error.message}</p>{/if}
    </article>
  {/each}
  </div>
</div>
{#snippet tool(call: ToolCall)}
  <section class="tool-call">
    <details class="tool-disclosure">
      <summary><span class="tool-chevron"><ChevronRight size={14} /></span><span>{call.name}</span></summary>
      <div class="tool-content">
        <span class="tool-status">{t(call.status === 'pending_approval' ? 'pending' : call.status === 'approved' ? 'approved' : call.status === 'denied' ? 'denied' : call.status === 'running' ? 'running' : call.status === 'succeeded' ? 'succeeded' : 'failed')}</span>
        <h3>{t('arguments')}</h3><pre>{call.arguments ? JSON.stringify(call.arguments, null, 2) : call.arguments_raw}</pre>
        {#if call.result !== null}<h3>{t('result')}{call.truncated ? ` / ${t('truncated')}` : ''}</h3><pre>{call.result}</pre>{/if}
      </div>
    </details>
    {#if call.status === 'pending_approval'}<div class="approval"><span>{t('approval')}</span><button disabled={deciding === call.id} onclick={() => decision(call, false)}>{t('deny')}</button><button class="primary" disabled={deciding === call.id} onclick={() => decision(call, true)}>{t('approve')}</button></div>{/if}
    {#if call.status === 'failed' || call.status === 'denied'}<p class="tool-status">{t(call.status)}</p>{/if}
    {#if call.error}<p role="alert">{call.error}</p>{/if}
    {#if decisionError}<p role="alert">{decisionError}</p>{/if}
  </section>
{/snippet}

<style>
  .timeline-content { display: flow-root; }
  .message { min-width: 0; border-bottom: 0; }
  .message + .user-message { margin-top: 20px; }
  .tool-call { margin: 14px 0; }
  .tool-disclosure summary { display: flex; align-items: center; gap: 7px; list-style: none; width: fit-content; max-width: 100%; padding: 4px 0; }
  .tool-disclosure summary::-webkit-details-marker { display: none; }
  .tool-chevron { display: inline-flex; flex: 0 0 auto; transition: transform 180ms ease; }
  .tool-disclosure[open] .tool-chevron { transform: rotate(90deg); }
  .tool-content { padding: 8px 0 4px 21px; }
  .tool-content h3 { font-size: .78rem; font-weight: 500; margin: 14px 0 4px; }
  .tool-status { font-size: .75rem; color: #888178; }
  @media (prefers-reduced-motion: reduce) { .tool-chevron { transition: none; } }
  .message.user-message {
    width: fit-content;
    max-width: 80%;
    margin-left: auto;
    margin-right: 0;
    padding: 14px 18px;
    border: 1px solid rgb(127 127 127 / 22%);
    border-radius: 8px;
    background: rgb(127 127 127 / 8%);
    overflow-wrap: anywhere;
  }
  .user-message .message-meta { display: none; }
  .user-message .message-text { margin: 0; }
  @media (max-width: 600px) { .message.user-message { max-width: 92%; } }
</style>