import { getContext } from 'svelte';
import type { Locale } from './i18n';
import { translate } from './i18n';
export const ui = () => getContext<{ readonly locale: Locale; t: (key: Parameters<typeof translate>[1]) => string }>('ui');