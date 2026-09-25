<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { finishLogin } from '$lib/auth/session';
  import { ui } from '$lib/ui';
  const { t } = ui();
  let error = $state('');
  onMount(() => { finishLogin(new URLSearchParams(location.search)).then(path => goto(path, { replaceState: true })).catch(reason => { error = reason instanceof Error ? reason.message : t('error'); }); });
</script>
<section class="page-wrap">{#if error}<h1>{t('error')}</h1><p role="alert">{error}</p><a class="button" href="/">{t('back')}</a>{:else}<p role="status">{t('loading')}...</p>{/if}</section>