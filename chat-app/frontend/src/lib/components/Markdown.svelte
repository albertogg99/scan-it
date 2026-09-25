<script lang="ts">
  import { browser } from '$app/environment';
  import { renderMarkdown } from './message-content';
  let { text }: { text: string } = $props();
  const html = $derived(browser ? renderMarkdown(text) : '');
</script>

<div class="markdown">{#if browser}{@html html}{:else}{text}{/if}</div>

<style>
  .markdown { min-width: 0; line-height: 1.6; overflow-wrap: anywhere; }
  .markdown :global(p) { margin: 0 0 12px; }
  .markdown :global(h1), .markdown :global(h2), .markdown :global(h3) { font-size: 1.15rem; line-height: 1.4; margin: 20px 0 10px; }
  .markdown :global(ul), .markdown :global(ol) { padding-left: 24px; margin: 12px 0; }
  .markdown :global(li) { margin: 4px 0; }
  .markdown :global(a) { color: inherit; text-decoration: underline; text-underline-offset: 3px; }
  .markdown :global(pre) { padding: 12px; background: rgb(127 127 127 / 10%); border-radius: 4px; }
  .markdown :global(code) { font-size: .9em; }
  .markdown :global(blockquote) { margin: 12px 0; padding-left: 16px; border-left: 3px solid #aaa; }
  .markdown :global(table) { display: block; max-width: 100%; overflow-x: auto; border-collapse: collapse; margin: 12px 0; }
  .markdown :global(th), .markdown :global(td) { padding: 8px 12px; border: 1px solid #aaa; text-align: left; }
  .markdown :global(> :last-child) { margin-bottom: 0; }
</style>