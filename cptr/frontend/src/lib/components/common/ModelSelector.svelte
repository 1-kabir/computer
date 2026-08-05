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
		onclose?: () => void;
	}
	let {
		selectedModel = $bindable(),
		preferAbove = true,
		align = 'end',
		nullable = false,
		nullLabel = 'Current model',
		onchange,
		onclose
	}: Props = $props();

	let btnEl: HTMLButtonElement | undefined = $state();
	let searchInputEl: HTMLInputElement | undefined = $state();
	let open = $state(false);
	let search = $state('');
	let highlightedIndex = $state(0);
	let isSmallViewport = $state(false);

	const selectorMaxHeight = $derived(isSmallViewport ? '7.5rem' : '15rem');

	const filtered = $derived(
		search.trim()
			? $chatModels.filter((m) => m.name.toLowerCase().includes(search.toLowerCase()))
			: $chatModels
	);

	const menuItems = $derived.by(() => {
		const items = [
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
		];

		const highlighted = Math.min(highlightedIndex, Math.max(items.length - 1, 0));
		return items.map((item, index) => ({ ...item, highlighted: index === highlighted }));
	});

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

	async function focusSearchInput() {
		await tick();
		await tick();
		searchInputEl?.focus();
		searchInputEl?.select();
	}

	function selectedIndex() {
		if (nullable && (selectedModel === null || selectedModel === '')) return 0;
		const index = filtered.findIndex((m) => m.id === selectedModel);
		return index >= 0 ? index + (nullable ? 1 : 0) : 0;
	}

	function resetHighlightedIndex() {
		const total = filtered.length + (nullable ? 1 : 0);
		highlightedIndex = total > 0 ? Math.min(selectedIndex(), total - 1) : 0;
	}

	function moveHighlightedIndex(delta: number) {
		const total = menuItems.length;
		if (total === 0) return;
		highlightedIndex = (highlightedIndex + delta + total) % total;
	}

	export async function openSelector() {
		if ($chatModels.length === 0 && !nullable) return;
		open = true;
		search = '';
		resetHighlightedIndex();
		await focusSearchInput();
	}

	async function toggle() {
		if (open) {
			open = false;
			onclose?.();
			return;
		}
		await openSelector();
	}

	function closeSelector() {
		open = false;
		onclose?.();
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
			onclose={closeSelector}
			{preferAbove}
			forceAbove={preferAbove}
			maxHeight={selectorMaxHeight}
			className="w-52"
			scrollActiveIntoView
			scrollActiveBlock="center"
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
						value={search}
						placeholder={$t('modelSelector.search')}
						class="w-full bg-transparent text-[0.6875rem] text-gray-500 dark:text-gray-400 placeholder:text-gray-300 dark:placeholder:text-gray-600 outline-none"
						oninput={(e) => {
							search = e.currentTarget.value;
							resetHighlightedIndex();
						}}
						onkeydown={(e) => {
							if (e.key === 'Escape') {
								closeSelector();
							} else if (e.key === 'ArrowDown') {
								e.preventDefault();
								moveHighlightedIndex(1);
							} else if (e.key === 'ArrowUp') {
								e.preventDefault();
								moveHighlightedIndex(-1);
							} else if (e.key === 'Enter') {
								e.preventDefault();
								menuItems[Math.min(highlightedIndex, menuItems.length - 1)]?.onclick();
								closeSelector();
							}
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
