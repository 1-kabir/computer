<script lang="ts">
	import { onMount, tick } from 'svelte';
	import { chatModels } from '$lib/stores/chat';
	import DropdownMenu from '../DropdownMenu.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		selectedModel: string | null;
		preferAbove?: boolean;
		align?: 'start' | 'end';
		nullable?: boolean;
		nullLabel?: string;
		onchange?: (model: string | null) => void;
	}
	let {
		selectedModel = $bindable(),
		preferAbove = true,
		align = 'end',
		nullable = false,
		nullLabel = 'Current model',
		onchange
	}: Props = $props();

	let btnEl: HTMLButtonElement | undefined = $state();
	let searchInputEl: HTMLInputElement | undefined = $state();
	let open = $state(false);
	let search = $state('');
	let isSmallViewport = $state(false);

	const selectorMaxHeight = $derived(isSmallViewport ? '7.5rem' : '15rem');

	const filtered = $derived(
		search.trim()
			? $chatModels.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
			: $chatModels
	);

	const menuItems = $derived([
		...(nullable
			? [
					{
						label: nullLabel,
						tooltip: nullLabel,
						active: selectedModel === null || selectedModel === '',
						check: true,
						onclick: () => {
							selectedModel = null;
							onchange?.(null);
						}
					}
				]
			: []),
		...filtered.map((m) => ({
			label: m.name,
			tooltip: m.name,
			active: m.id === selectedModel,
			check: true,
			onclick: () => {
				selectedModel = m.id;
				onchange?.(m.id);
			}
		}))
	]);

	function updateViewportSize() {
		isSmallViewport = (window.visualViewport?.width ?? window.innerWidth) < 640;
	}

	onMount(() => {
		updateViewportSize();
		window.addEventListener('resize', updateViewportSize);
		window.visualViewport?.addEventListener('resize', updateViewportSize);
		return () => {
			window.removeEventListener('resize', updateViewportSize);
			window.visualViewport?.removeEventListener('resize', updateViewportSize);
		};
	});

	async function toggle() {
		if ($chatModels.length === 0 && !nullable) return;
		if (open) {
			open = false;
			return;
		}
		open = true;
		search = '';
		await tick();
		// Focus search after DropdownMenu renders
		await tick();
		searchInputEl?.focus();
	}
</script>

<span class="relative inline-flex {open ? 'z-[1001]' : ''}">
	<button
		bind:this={btnEl}
		class="flex items-center gap-1 px-2 py-1 rounded-lg text-[0.6875rem] text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/5 transition-colors duration-100"
		onclick={toggle}
	>
		<span class="truncate max-w-[10rem]"
			>{selectedModel === null || selectedModel === ''
				? nullLabel
				: $chatModels.length === 0
					? $t('modelSelector.noModels')
					: $chatModels.find((m) => m.id === selectedModel)?.name ||
						$t('modelSelector.selectModel')}</span
		>
		{#if $chatModels.length > 0 || nullable}
			<svg
				class="w-3 h-3 opacity-50"
				viewBox="0 0 24 24"
				fill="none"
				stroke="currentColor"
				stroke-width="2.5"
				stroke-linecap="round"
				stroke-linejoin="round"
			>
				<polyline points="6 9 12 15 18 9" />
			</svg>
		{/if}
	</button>

	{#if open && btnEl && ($chatModels.length > 0 || nullable)}
		<DropdownMenu
			items={menuItems}
			anchor={btnEl}
			onclose={() => (open = false)}
			{preferAbove}
			forceAbove={preferAbove}
			maxHeight={selectorMaxHeight}
			className="w-52"
			{align}
		>
			{#snippet header()}
				<div class="flex items-center gap-1.5 h-6 px-2 mt-0.5">
					<svg
						class="w-3 h-3 shrink-0 text-gray-300 dark:text-gray-600"
						viewBox="0 0 24 24"
						fill="none"
						stroke="currentColor"
						stroke-width="2"
						stroke-linecap="round"
						stroke-linejoin="round"
					>
						<circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
					</svg>
					<input
						bind:this={searchInputEl}
						bind:value={search}
						placeholder={$t('modelSelector.search')}
						class="w-full bg-transparent text-[0.6875rem] text-gray-500 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
						onkeydown={(e) => {
							if (e.key === 'Escape') open = false;
						}}
					/>
				</div>
			{/snippet}
			{#snippet empty()}
				<div class="px-3 py-1.5 text-[0.6875rem] text-gray-400 dark:text-gray-500 text-center">
					{$t('modelSelector.noMatches')}
				</div>
			{/snippet}
		</DropdownMenu>
	{/if}
</span>
