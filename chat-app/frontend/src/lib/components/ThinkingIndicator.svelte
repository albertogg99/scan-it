<script lang="ts">
  import { onMount } from 'svelte';
  import { ui } from '$lib/ui';
  const context = ui();
  const words = {
    en: ['Thinking', 'Refining', 'Generating', 'Polishing', 'Considering', 'Composing'],
    de: ['Nachdenken', 'Verfeinern', 'Generieren', 'Ausarbeiten', 'Abwagen', 'Formulieren'],
    fr: ['Reflexion', 'Affinage', 'Generation', 'Peaufinage', 'Analyse', 'Redaction'],
    it: ['Riflessione', 'Perfezionamento', 'Generazione', 'Rifinitura', 'Analisi', 'Composizione']
  };
  let wordIndex = $state(0);
  onMount(() => { wordIndex = Math.floor(Math.random() * words.en.length); });
</script>

<span class="thinking" role="status">
  <span>{words[context.locale][wordIndex]}</span><span class="dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>
</span>

<style>
  .thinking { display: inline-flex; align-items: baseline; min-width: 14ch; }
  .dots { display: inline-flex; }
  .dots span { animation: pulse 1.2s ease-in-out infinite; }
  .dots span:nth-child(2) { animation-delay: 160ms; }
  .dots span:nth-child(3) { animation-delay: 320ms; }
  @keyframes pulse { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .dots span { animation: none; } }
</style>