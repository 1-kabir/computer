<script lang="ts">
	import { goto } from '$app/navigation';
	import { sidebarOpen } from '$lib/stores';
	import { t } from '$lib/i18n';
	import { tooltip } from '$lib/tooltip';
	import Icon from './Icon.svelte';

	function goHome(e: MouseEvent) {
		e.preventDefault();
		goto('/');
		if (typeof window !== 'undefined' && window.innerWidth < 768) sidebarOpen.set(false);
	}
</script>

<div
	class="flex items-center justify-between h-9 pl-3.5 pr-1.5 shrink-0 border-b border-gray-200 dark:border-white/6"
	style="border-color: var(--app-border);"
>
	<a
		href="/"
		class="flex items-center gap-1.5 text-xs font-semibold tracking-tight text-gray-900 dark:text-white"
		onclick={goHome}><img src="/favicon.png" alt={$t('app.logoAlt')} class="w-4 h-4" />Computer</a
	>
	<button
		class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-300 hover:text-gray-500 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-100"
		onclick={() => sidebarOpen.set(false)}
		aria-label={$t('sidebar.collapse')}
		use:tooltip={$t('sidebar.collapse')}
	>
		<Icon name="sidebar-expand" size={14} />
	</button>
</div>
