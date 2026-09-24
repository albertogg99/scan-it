<script lang="ts">
  import { ArrowUpRight, TrainFront, Landmark } from 'lucide-svelte';
  import { ui } from '$lib/ui';
  const { t } = ui();
  const demos = [{ id: 'zurich-transit', name: 'Zurich Transit Live', icon: TrainFront }, { id: 'museum-guide', name: 'Museum Guide (demo)', icon: Landmark }];
</script>
<section class="page-wrap"><p class="eyebrow">01 / {t('explore')}</p><h1>{t('explore')}</h1><div class="demo-list">{#each demos as demo}<a href="/m/{demo.id}" class="demo-row"><demo.icon size={27} strokeWidth={1.5} /><strong>{demo.name}</strong><span>{t('open')} <ArrowUpRight size={19} /></span></a>{/each}</div></section>