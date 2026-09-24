<script lang="ts">
  import type { Message, ToolCall } from '$lib/api/types';
  import { decide } from '$lib/api/client';
  import ImageAttachment from './ImageAttachment.svelte';
  import Markdown from './Markdown.svelte';
  import { groupParts } from './message-content';
  import { ui } from '$lib/ui';
  const { t } = ui();
  let { messages, onChange }: { messages: Message[]; onChange: () => void } = $props();
  let deciding = $state<string | null>(null);
  let decisionError = $state('');
  async function decision(call: ToolCall, approved: boolean) {
    deciding = call.id; decisionError = '';
    try { await decide(call.id, approved); onChange(); } catch (error) { decisionError = error instanceof Error ? error.message : t('error'); }
    finally { deciding = null; }
  }
</script>
<div class="timeline" role="log" aria-live="polite">
  {#if messages.length === 0}<p class="empty">{t('empty')}</p>{/if}
  {#each messages as message (message.id)}
    <article class:user-message={message.role === 'user'} class="message">
      <div class="message-meta">{message.role === 'user' ? t('message') : message.status === 'streaming' ? t('running') : message.status === 'failed' ? t('failed') : message.status === 'cancelled' ? t('cancelled') : ''}</div>
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
{#snippet tool(call: ToolCall)}
  <section class="tool"><div class="tool-head"><strong>{call.name}</strong><span>{t(call.status === 'pending_approval' ? 'pending' : call.status === 'approved' ? 'approved' : call.status === 'denied' ? 'denied' : call.status === 'running' ? 'running' : call.status === 'succeeded' ? 'succeeded' : 'failed')}</span></div>
    <details><summary>{t('arguments')}</summary><pre>{call.arguments ? JSON.stringify(call.arguments, null, 2) : call.arguments_raw}</pre></details>
    {#if call.status === 'pending_approval'}<div class="approval"><span>{t('approval')}</span><button disabled={deciding === call.id} onclick={() => decision(call, false)}>{t('deny')}</button><button class="primary" disabled={deciding === call.id} onclick={() => decision(call, true)}>{t('approve')}</button></div>{/if}
    {#if call.result !== null}<details><summary>{t('result')}{call.truncated ? ` / ${t('truncated')}` : ''}</summary><pre>{call.result}</pre></details>{/if}
    {#if call.error}<p role="alert">{call.error}</p>{/if}
    {#if decisionError}<p role="alert">{decisionError}</p>{/if}
  </section>
{/snippet}

<style>
  .message { min-width: 0; }
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