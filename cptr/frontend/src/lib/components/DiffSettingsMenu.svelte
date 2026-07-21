<script lang="ts">
	import { diffDisplayMode, hideWhitespaceChanges } from '$lib/stores/gitDiffSettings';
	import { t } from '$lib/i18n';
	import DropdownMenu from './DropdownMenu.svelte';
	import Icon from './Icon.svelte';

	interface Props {
		anchor: HTMLElement;
		onclose: () => void;
		preferAbove?: boolean;
		forceAbove?: boolean;
	}

	let { anchor, onclose, preferAbove = false, forceAbove = false }: Props = $props();

	function toggleWhitespace() {
		hideWhitespaceChanges.set(!$hideWhitespaceChanges);
	}
</script>

<DropdownMenu
	items={[]}
	{anchor}
	{onclose}
	{preferAbove}
	{forceAbove}
	align="end"
	className="w-48"
	headerDivider={false}
	footerDivider={false}
>
	<div class="py-0.5 text-xs font-normal">
		<button
			type="button"
			class="app-interactive app-muted flex h-6 w-full items-center gap-2 rounded-xl px-2 text-left text-xs font-normal transition-colors duration-75"
			onclick={toggleWhitespace}
			aria-pressed={$hideWhitespaceChanges}
		>
			<Icon name="code" size={13} class="app-icon-muted shrink-0" />
			<span class="min-w-0 flex-1 truncate">{$t('diff.hideWhitespace')}</span>
			<span
				class="relative h-3.5 w-6 shrink-0 rounded-full transition-colors duration-150 {$hideWhitespaceChanges
					? 'bg-gray-900 dark:bg-white'
					: 'bg-gray-300 dark:bg-gray-700'}"
			>
				<span
					class="absolute top-[0.125rem] h-2.5 w-2.5 rounded-full transition-all duration-150 {$hideWhitespaceChanges
						? 'left-[0.75rem] bg-white dark:bg-black'
						: 'left-[0.125rem] bg-white dark:bg-gray-500'}"
				></span>
			</span>
		</button>

		<div class="app-muted px-2 pb-0.5 pt-1 text-[0.625rem] leading-none uppercase">
			{$t('diff.display')}
		</div>

		<button
			type="button"
			class="flex h-6 w-full items-center gap-2 rounded-xl px-2 text-left text-xs font-normal transition-colors duration-75 {$diffDisplayMode ===
			'unified'
				? 'app-interactive-active'
				: 'app-interactive app-muted'}"
			onclick={() => diffDisplayMode.set('unified')}
		>
			<Icon name="list" size={13} class="app-icon-muted shrink-0" />
			<span class="min-w-0 flex-1 truncate text-left">{$t('diff.unified')}</span>
			{#if $diffDisplayMode === 'unified'}
				<Icon name="check" size={11} class="app-icon-muted shrink-0" />
			{/if}
		</button>
		<button
			type="button"
			class="flex h-6 w-full items-center gap-2 rounded-xl px-2 text-left text-xs font-normal transition-colors duration-75 {$diffDisplayMode ===
			'split'
				? 'app-interactive-active'
				: 'app-interactive app-muted'}"
			onclick={() => diffDisplayMode.set('split')}
		>
			<Icon name="split-view" size={13} class="app-icon-muted shrink-0" />
			<span class="min-w-0 flex-1 truncate">{$t('diff.split')}</span>
			{#if $diffDisplayMode === 'split'}
				<Icon name="check" size={11} class="app-icon-muted shrink-0" />
			{/if}
		</button>
	</div>
</DropdownMenu>
