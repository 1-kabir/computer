<script lang="ts">
	import Icon from './Icon.svelte';
	import { getWelcome } from '$lib/apis/state';
	import { listDir } from '$lib/apis/files';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';

	interface Props {
		defaultName?: string;
		initialDir?: string;
		onclose: () => void;
		onsave: (fullPath: string) => void;
	}

	interface DirEntry {
		name: string;
		type: 'directory' | 'file' | 'symlink';
		modified: string | null;
	}

	let { defaultName = 'Untitled', initialDir, onclose, onsave }: Props = $props();

	let currentPath = $state('~');
	let directories = $state<DirEntry[]>([]);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let selectedIndex = $state(-1);
	let showHidden = $state(false);
	let listEl: HTMLDivElement | undefined = $state();
	let history = $state<string[]>([]);
	let fileName = $state(defaultName);
	let fileNameInput: HTMLInputElement | undefined = $state();

	const filteredDirs = $derived.by(() => {
		if (showHidden) return directories;
		return directories.filter((d) => !d.name.startsWith('.'));
	});

	const savePath = $derived.by(() => {
		if (!fileName.trim()) return '';
		const name = fileName.trim();
		return currentPath === '/' ? `/${name}` : `${currentPath}/${name}`;
	});

	$effect(() => {
		selectedIndex = -1;
		history = [];
		if (initialDir) {
			fetchDirectories(initialDir);
		} else {
			getWelcome()
				.then((d) => {
					const home = d.suggestions?.[0]?.path || '/';
					fetchDirectories(home);
				})
				.catch(() => fetchDirectories('/'));
		}
	});

	$effect(() => {
		if (filteredDirs) selectedIndex = -1;
	});

	$effect(() => {
		if (fileNameInput) {
			requestAnimationFrame(() => {
				fileNameInput?.focus();
				fileNameInput?.select();
			});
		}
	});

	async function fetchDirectories(path: string) {
		loading = true;
		error = null;
		try {
			const data = await listDir(path);
			directories = data.entries.filter((e: DirEntry) => e.type === 'directory');
			currentPath = data.path;
		} catch (e: any) {
			error = e.message || $t('files.failedToLoad');
			directories = [];
		} finally {
			loading = false;
		}
	}

	function navigateTo(dirName: string) {
		history = [...history, currentPath];
		const newPath = currentPath === '/' ? `/${dirName}` : `${currentPath}/${dirName}`;
		fetchDirectories(newPath);
	}

	function goBack() {
		if (history.length === 0) return;
		const prev = history[history.length - 1];
		history = history.slice(0, -1);
		fetchDirectories(prev);
	}

	function doSave() {
		if (!fileName.trim()) return;
		onsave(savePath);
	}

	function handleKeydown(e: KeyboardEvent) {
		switch (e.key) {
			case 'Escape':
				onclose();
				break;
			case 'Enter':
				if (document.activeElement === fileNameInput) {
					e.preventDefault();
					doSave();
				}
				break;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="fixed inset-0 bg-black/50 z-[100] flex items-center justify-center"
	onmousedown={onclose}
	onkeydown={() => {}}
>
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="app-theme app-surface w-full max-w-[32.5rem] mx-4 border rounded-3xl overflow-hidden shadow-2xl max-h-[26.25rem] flex flex-col"
		onmousedown={(e) => e.stopPropagation()}
		onkeydown={() => {}}
	>
		<!-- Filename input: same pattern as QuickOpen search bar -->
		<div class="flex items-center px-4 py-3 gap-2.5 border-b border-gray-200 dark:border-white/6">
			<Icon name="empty-page" size={16} class="text-gray-400 shrink-0" />
			<input
				bind:this={fileNameInput}
				bind:value={fileName}
				type="text"
				class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white font-sans placeholder:text-gray-400"
				placeholder={$t('saveDialog.fileNamePlaceholder')}
				spellcheck="false"
				autocomplete="off"
			/>
			<span
				class="text-[0.625rem] font-mono font-medium px-1.5 py-0.5 rounded bg-gray-100 dark:bg-white/6 text-gray-400 shrink-0"
				>ESC</span
			>
		</div>

		<!-- Location breadcrumb -->
		<div
			class="flex items-center gap-2 px-4 h-8 border-b border-gray-200 dark:border-white/6 shrink-0"
		>
			{#if history.length > 0}
				<button
					class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-100"
					onclick={goBack}
					aria-label={$t('directory.goBack')}
				>
					<Icon name="chevron-left" size={13} />
				</button>
			{/if}
			<span class="flex-1 text-[0.6875rem] text-gray-400 dark:text-gray-500 font-mono truncate"
				>{currentPath}</span
			>
			<label class="flex items-center gap-1.5 cursor-pointer select-none shrink-0">
				<input type="checkbox" bind:checked={showHidden} class="w-3 h-3 rounded accent-gray-500" />
				<span class="text-[0.625rem] text-gray-400">{$t('saveDialog.hidden')}</span>
			</label>
		</div>

		<!-- Directory listing -->
		<div bind:this={listEl} class="flex-1 overflow-y-auto p-1 min-h-0">
			{#if loading}
				<div class="flex items-center justify-center py-8">
					<Spinner size={16} />
				</div>
			{:else if error}
				<div class="flex flex-col items-center justify-center gap-2 py-8 text-center">
					<p class="text-xs text-red-400">{error}</p>
					<button
						class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1 rounded-lg bg-gray-100 dark:bg-white/6 transition-colors duration-100"
						onclick={() => fetchDirectories(currentPath)}>{$t('saveDialog.retry')}</button
					>
				</div>
			{:else if filteredDirs.length === 0}
				<div class="flex flex-col items-center justify-center py-8">
					<p class="text-xs text-gray-400 dark:text-gray-600">{$t('saveDialog.emptyFolder')}</p>
				</div>
			{:else}
				{#each filteredDirs as dir, i (dir.name)}
					<button
						data-index={i}
						class="directory-row flex items-center gap-2 w-full h-7 px-2 rounded-lg text-left transition-colors duration-75
					{i === selectedIndex ? 'app-interactive-active' : ''}"
						onclick={() => navigateTo(dir.name)}
						onmouseenter={() => {
							selectedIndex = i;
						}}
					>
						<Icon name="folder" size={14} class="shrink-0 text-gray-400" />
						<span class="flex-1 truncate text-xs">{dir.name}</span>
						<Icon
							name="chevron-right"
							size={12}
							class="shrink-0 text-gray-300 dark:text-gray-700"
						/>
					</button>
				{/each}
			{/if}
		</div>

		<!-- Footer -->
		<div
			class="flex items-center gap-2 px-3 py-2 border-t border-gray-200 dark:border-white/6 shrink-0"
		>
			{#if fileName.trim()}
				<span
					class="flex-1 text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono truncate min-w-0"
					title={savePath}>{savePath}</span
				>
			{:else}
				<span class="flex-1"></span>
			{/if}
			<button
				class="text-xs font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1.5 rounded-lg transition-colors duration-100 shrink-0"
				onclick={onclose}>{$t('saveDialog.cancel')}</button
			>
			<button
				class="text-xs font-medium text-white dark:text-black bg-gray-900 dark:bg-white hover:bg-gray-800 dark:hover:bg-white/90 px-3 py-1.5 rounded-lg transition-colors duration-100 disabled:opacity-30 disabled:pointer-events-none shrink-0"
				onclick={doSave}
				disabled={!fileName.trim()}>{$t('saveDialog.save')}</button
			>
		</div>
	</div>
</div>

<style>
	.directory-row {
		color: color-mix(in oklab, var(--app-fg) 62%, var(--app-bg));
	}

	.directory-row:hover {
		background: color-mix(in oklab, var(--app-fg) 7%, transparent);
		color: var(--app-fg);
	}
</style>
