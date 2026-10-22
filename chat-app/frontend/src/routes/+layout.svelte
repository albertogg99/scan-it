<script lang="ts">
  import '../styles.css';
  import { onMount, setContext } from 'svelte';
  import { Sun, Moon, ShieldCheck } from 'lucide-svelte';
  import { APP_NAME } from '$lib/config/brand';
  import { initialLocale, locales, translate, type Locale } from '$lib/i18n';
  let { children } = $props();
  let locale = $state<Locale>('es');
  let theme = $state<'light' | 'dark'>('light');
  setContext('ui', { get locale() { return locale; }, t: (key: Parameters<typeof translate>[1]) => translate(locale, key) });
  onMount(() => { locale = initialLocale(); document.documentElement.lang = locale; theme = localStorage.getItem('sd.theme') === 'dark' ? 'dark' : 'light'; document.documentElement.dataset.theme = theme; });
  function changeTheme() { theme = theme === 'light' ? 'dark' : 'light'; document.documentElement.dataset.theme = theme; localStorage.setItem('sd.theme', theme); }
</script>
<svelte:head><title>{APP_NAME}</title></svelte:head>
<header class="site-header"><a class="brand" href="/" aria-label="{APP_NAME} · EMT Madrid"><img class="brand-logo" src="/emt-logo.svg" alt="EMT Madrid" />{APP_NAME}<span class="brand-sep" aria-hidden="true">·</span><span class="brand-partner">EMT Madrid</span></a><div class="header-controls"><a class="icon" href="/admin" title="Administration" aria-label="Administration"><ShieldCheck size={18} /></a><label class="language"><span class="sr-only">{translate(locale, 'language')}</span><select aria-label={translate(locale, 'language')} value={locale} onchange={event => { locale = event.currentTarget.value as Locale; localStorage.setItem('sd.locale', locale); document.documentElement.lang = locale; }}>{#each locales as language}<option value={language}>{language.toUpperCase()}</option>{/each}</select></label><button class="icon" title={translate(locale, 'theme')} aria-label={translate(locale, 'theme')} onclick={changeTheme}>{#if theme === 'light'}<Moon size={18} />{:else}<Sun size={18} />{/if}</button></div></header>
<main>{@render children()}</main>
