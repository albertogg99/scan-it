<script lang="ts">
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { finishLogin, login, pendingReturnTo } from '$lib/auth/session';
  import { ui } from '$lib/ui';
  const { t } = ui();
  const retryKey = 'sd.loginRetry';
  let error = $state('');
  onMount(() => {
    const params = new URLSearchParams(location.search);
    const returnTo = pendingReturnTo(params);
    finishLogin(params).then(path => { sessionStorage.removeItem(retryKey); return goto(path, { replaceState: true }); }).catch(reason => {
      // Retry sign-in once automatically; a second failure is shown to avoid redirect loops.
      if (!sessionStorage.getItem(retryKey)) { sessionStorage.setItem(retryKey, '1'); login(returnTo).catch(() => { error = t('error'); }); return; }
      sessionStorage.removeItem(retryKey);
      error = reason instanceof Error ? reason.message : t('error');
    });
  });
</script>
<section class="page-wrap">{#if error}<h1>{t('error')}</h1><p role="alert">{error}</p><a class="button" href="/">{t('back')}</a>{:else}<p role="status">{t('loading')}...</p>{/if}</section>