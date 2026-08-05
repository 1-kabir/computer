<script lang="ts">
	import { goto } from '$app/navigation';
	import { addWorkspace } from '$lib/stores';
	import { getWelcome } from '$lib/apis/state';
	import { createEntry, listDir } from '$lib/apis/files';
	import Icon from './Icon.svelte';
	import Modal from './Modal.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import { tooltip } from '$lib/tooltip';

	interface Props {
		onclose: () => void;
		onselect?: (path: string) => void;
	}

	interface DirEntry {
		name: string;
		type: 'directory' | 'file' | 'symlink';
		modified: string | null;
	}

	let { onclose, onselect }: Props = $props();

	let currentPath = $state('~');
	let directories = $state<DirEntry[]>([]);
	let loading = $state(false);
	let error = $state<string | null>(null);
	let selectedIndex = $state(-1);
	let showHidden = $state(false);
	let actionsMenuOpen = $state(false);
	let actionsBtnEl: HTMLButtonElement | undefined = $state();
	let listEl: HTMLDivElement | undefined = $state();
	let history = $state<string[]>([]);
	let creatingFolder = $state(false);
	let folderName = $state('');

	// ── Editable path bar ───────────────────────────────────────
	let editingPath = $state(false);
	let pathInputValue = $state('');
	let pathInputEl: HTMLInputElement | undefined = $state();
	let pathValid = $state<'idle' | 'checking' | 'valid' | 'invalid'>('idle');
	let validateTimer: ReturnType<typeof setTimeout> | null = null;
	let tabHint = $state('');

	const filteredDirs = $derived.by(() => {
		if (showHidden) return directories;
		return directories.filter((d) => !d.name.startsWith('.'));
	});

	const breadcrumbs = $derived.by(() => {
		if (!currentPath || currentPath === '/') return [{ name: '/', path: '/' }];
		const parts = currentPath.split('/').filter(Boolean);
		const segs: { name: string; path: string }[] = [{ name: '/', path: '/' }];
		let built = '';
		for (const p of parts) {
			built += '/' + p;
			segs.push({ name: p, path: built });
		}
		return segs;
	});

	$effect(() => {
		selectedIndex = -1;
		history = [];
		getWelcome()
			.then((d) => {
				const home = d.suggestions?.[0]?.path || '/';
				fetchDirectories(home);
			})
			.catch(() => fetchDirectories('/'));
	});

	$effect(() => {
		if (filteredDirs) selectedIndex = -1;
	});

	$effect(() => {
		if (editingPath && pathInputEl) {
			requestAnimationFrame(() => {
				pathInputEl?.focus();
				pathInputEl?.select();
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

	function navigateToPath(path: string) {
		history = [...history, currentPath];
		fetchDirectories(path);
	}

	function goBack() {
		if (history.length === 0) return;
		const prev = history[history.length - 1];
		history = history.slice(0, -1);
		fetchDirectories(prev);
	}

	function selectCurrent() {
		if (onselect) {
			onselect(currentPath);
			onclose();
			return;
		}
		addWorkspace(currentPath);
		goto(`/?workspace=${encodeURIComponent(currentPath)}`);
		onclose();
	}

	// ── Path editing ────────────────────────────────────────────
	function startEditing() {
		pathInputValue = currentPath;
		editingPath = true;
		pathValid = 'idle';
		tabHint = '';
	}

	function cancelEditing() {
		editingPath = false;
		pathInputValue = '';
		pathValid = 'idle';
		tabHint = '';
		if (validateTimer) {
			clearTimeout(validateTimer);
			validateTimer = null;
		}
	}

	function confirmPath() {
		const val = pathInputValue.trim();
		if (!val) {
			cancelEditing();
			return;
		}
		editingPath = false;
		pathValid = 'idle';
		tabHint = '';
		navigateToPath(val);
	}

	function onPathInput() {
		tabHint = '';
		if (validateTimer) clearTimeout(validateTimer);
		const val = pathInputValue.trim();
		if (!val) {
			pathValid = 'idle';
			return;
		}
		pathValid = 'checking';
		validateTimer = setTimeout(async () => {
			try {
				await listDir(val);
				pathValid = 'valid';
			} catch {
				pathValid = 'invalid';
			}
		}, 300);
	}

	async function tabComplete() {
		const val = pathInputValue.trim();
		if (!val) return;
		const lastSlash = val.lastIndexOf('/');
		if (lastSlash < 0) return;
		const parent = val.substring(0, lastSlash) || '/';
		const partial = val.substring(lastSlash + 1).toLowerCase();
		try {
			const data = await listDir(parent);
			const dirs = data.entries
				.filter((e: DirEntry) => e.type === 'directory')
				.map((e: DirEntry) => e.name);
			if (partial) {
				const matches = dirs.filter((n: string) => n.toLowerCase().startsWith(partial));
				if (matches.length === 1) {
					pathInputValue = parent === '/' ? `/${matches[0]}` : `${parent}/${matches[0]}`;
					tabHint = '';
					onPathInput();
				} else if (matches.length > 1) {
					const cp = commonPrefix(matches);
					if (cp.length > partial.length) {
						pathInputValue = parent === '/' ? `/${cp}` : `${parent}/${cp}`;
					}
					tabHint = `${matches.length} matches`;
					onPathInput();
				}
			} else if (dirs.length > 0) {
				tabHint = `${dirs.length} dirs`;
			}
		} catch {}
	}

	function commonPrefix(strs: string[]): string {
		if (strs.length === 0) return '';
		let p = strs[0];
		for (let i = 1; i < strs.length; i++) {
			while (!strs[i].toLowerCase().startsWith(p.toLowerCase())) {
				p = p.slice(0, -1);
				if (!p) return '';
			}
		}
		return p;
	}

	function handlePathKeydown(e: KeyboardEvent) {
		if (e.key === 'Enter') {
			e.preventDefault();
			e.stopPropagation();
			confirmPath();
		} else if (e.key === 'Escape') {
			e.preventDefault();
			e.stopPropagation();
			cancelEditing();
		} else if (e.key === 'Tab') {
			e.preventDefault();
			e.stopPropagation();
			tabComplete();
		}
	}

	function handleKeydown(e: KeyboardEvent) {
		if (editingPath) return;
		switch (e.key) {
			case 'Escape':
				onclose();
				break;
			case 'ArrowDown':
				e.preventDefault();
				selectedIndex = Math.min(selectedIndex + 1, filteredDirs.length - 1);
				scrollSelectedIntoView();
				break;
			case 'ArrowUp':
				e.preventDefault();
				selectedIndex = Math.max(selectedIndex - 1, -1);
				scrollSelectedIntoView();
				break;
			case 'Enter':
				e.preventDefault();
				if (selectedIndex >= 0 && selectedIndex < filteredDirs.length) {
					navigateTo(filteredDirs[selectedIndex].name);
				}
				break;
			case 'Backspace':
				if (history.length > 0) {
					e.preventDefault();
					goBack();
				}
				break;
		}
	}

	function scrollSelectedIntoView() {
		requestAnimationFrame(() => {
			const el = listEl?.querySelector(`[data-index="${selectedIndex}"]`);
			el?.scrollIntoView({ block: 'nearest' });
		});
	}

	const currentFolderName = $derived.by(() => {
		const parts = currentPath.split('/').filter(Boolean);
		return parts.length > 0 ? parts[parts.length - 1] : '/';
	});

	function toggleHidden() {
		showHidden = !showHidden;
	}

	function startCreatingFolder() {
		creatingFolder = true;
		folderName = '';
		actionsMenuOpen = false;
	}

	async function createFolder() {
		const name = folderName.trim();
		if (!name) return;
		const path = currentPath === '/' ? `/${name}` : `${currentPath}/${name}`;
		try {
			await createEntry(path, 'directory');
			await fetchDirectories(currentPath);
		} catch (e: any) {
			error = e.message || $t('files.failedToLoad');
		} finally {
			creatingFolder = false;
			folderName = '';
		}
	}

	function cancelCreatingFolder() {
		creatingFolder = false;
		folderName = '';
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<Modal
	{onclose}
	class="w-full max-w-[35rem] mx-4 max-md:mx-0 max-md:rounded-none max-h-[30rem] max-md:max-h-dvh flex flex-col mb-[6vh] max-md:mb-0"
>
	<!-- Path bar -->
	<div class="flex items-center px-3.5 pt-2.5 pb-2 gap-2 shrink-0">
		{#if history.length > 0}
			<button
				class="flex items-center justify-center w-6 h-6 rounded text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-100 shrink-0"
				onclick={goBack}
				aria-label={$t('directory.goBack')}
			>
				<Icon name="chevron-left" size={14} />
			</button>
		{/if}

		{#if editingPath}
			<div class="flex items-center gap-2 flex-1 min-w-0">
				<input
					bind:this={pathInputEl}
					bind:value={pathInputValue}
					type="text"
					class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white font-mono placeholder:text-gray-400"
					oninput={onPathInput}
					onkeydown={handlePathKeydown}
					onblur={() => {
						requestAnimationFrame(() => {
							if (editingPath) cancelEditing();
						});
					}}
					spellcheck="false"
					autocomplete="off"
					placeholder={$t('directory.typePath')}
				/>
				{#if pathValid === 'checking'}
					<Spinner size={12} />
				{:else if pathValid === 'valid'}
					<span class="text-green-500 shrink-0 flex"><Icon name="check" size={12} /></span>
				{:else if pathValid === 'invalid'}
					<span class="text-red-400 shrink-0 flex"><Icon name="xmark" size={12} /></span>
				{/if}
				{#if tabHint}
					<span class="text-[0.625rem] text-gray-400 whitespace-nowrap shrink-0">{tabHint}</span>
				{/if}
				<span
					class="text-[0.625rem] font-mono px-1.5 py-0.5 rounded bg-gray-100 dark:bg-white/6 text-gray-400 shrink-0"
					>TAB</span
				>
			</div>
		{:else}
			<!-- svelte-ignore a11y_no_static_element_interactions -->
			<div
				class="group flex items-center gap-0.5 flex-1 min-w-0 cursor-text px-1 -mx-1"
				role="button"
				tabindex="0"
				onclick={startEditing}
				onkeydown={(e) => {
					if (e.key === 'Enter') startEditing();
				}}
			>
				{#each breadcrumbs as seg, i (seg.path)}
					{#if i > 1}<span class="text-gray-300 dark:text-gray-600 text-[0.6875rem] font-mono"
							>/</span
						>{/if}
					{#if i === breadcrumbs.length - 1}
						<span
							class="text-[0.6875rem] font-mono text-gray-900 dark:text-white whitespace-nowrap overflow-hidden text-ellipsis"
							>{seg.name}</span
						>
					{:else}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<span
							class="text-[0.6875rem] font-mono text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 whitespace-nowrap shrink-0 cursor-pointer px-0.5 transition-colors duration-75"
							role="button"
							tabindex="-1"
							onclick={(e) => {
								e.stopPropagation();
								navigateToPath(seg.path);
							}}
							onkeydown={() => {}}>{seg.name}</span
						>
					{/if}
				{/each}
				<span
					class="ml-1 text-gray-300 dark:text-gray-700 opacity-0 group-hover:opacity-100 transition-opacity duration-150 shrink-0 flex items-center"
				>
					<Icon name="pencil" size={10} />
				</span>
			</div>
		{/if}

		<button
			bind:this={actionsBtnEl}
			class="flex items-center justify-center w-6 h-6 rounded text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 hover:bg-gray-200/70 dark:hover:bg-white/8 transition-colors duration-100 shrink-0"
			onclick={() => {
				actionsMenuOpen = !actionsMenuOpen;
			}}
			use:tooltip={$t('files.actions')}
			aria-label={$t('files.actions')}
		>
			<Icon name="three-dots" size={12} />
		</button>
	</div>

	<!-- Directory listing -->
	<div bind:this={listEl} class="overflow-y-auto px-1.5 pb-1.5 flex-1 min-h-0">
		{#if creatingFolder}
			<div class="flex items-center gap-2 h-7 px-2">
				<Icon name="folder" size={14} class="text-gray-400 shrink-0" />
				<input
					type="text"
					class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white"
					placeholder={$t('files.folderNamePlaceholder')}
					bind:value={folderName}
					onkeydown={(e) => {
						if (e.key === 'Enter') createFolder();
						if (e.key === 'Escape') cancelCreatingFolder();
					}}
					autofocus
				/>
				<button
					class="flex items-center justify-center w-5 h-5 rounded text-green-500 hover:bg-green-50 dark:hover:bg-green-500/10 transition-colors duration-75"
					onclick={createFolder}
					aria-label={$t('files.newFolder')}
				>
					<Icon name="check" size={12} />
				</button>
				<button
					class="flex items-center justify-center w-5 h-5 rounded text-gray-400 hover:bg-gray-100 dark:hover:bg-white/6 transition-colors duration-75"
					onclick={cancelCreatingFolder}
					aria-label={$t('directory.cancel')}
				>
					<Icon name="xmark" size={12} />
				</button>
			</div>
		{/if}

		{#if loading}
			<div class="flex items-center justify-center py-8">
				<Spinner size={16} />
			</div>
		{:else if error}
			<div class="flex flex-col items-center justify-center gap-2 py-8 text-center">
				<p class="text-xs text-red-400">{error}</p>
				<button
					class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1 rounded-lg bg-gray-100 dark:bg-white/6 transition-colors duration-100"
					onclick={() => fetchDirectories(currentPath)}>{$t('directory.retry')}</button
				>
			</div>
		{:else if filteredDirs.length === 0}
			<div class="flex flex-col items-center justify-center py-8">
				<p class="text-xs text-gray-400 dark:text-gray-600">{$t('directory.noSubdirectories')}</p>
			</div>
		{:else}
			{#each filteredDirs as dir, i (dir.name)}
				<button
					data-index={i}
					class="flex items-center gap-2 w-full h-7 px-2 rounded-xl text-left transition-colors duration-75
							{i === selectedIndex
						? 'bg-gray-200/50 text-gray-900 dark:bg-white/6 dark:text-white'
						: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'}"
					onclick={() => navigateTo(dir.name)}
					ondblclick={(e) => {
						e.stopPropagation();
						const fullPath = currentPath === '/' ? `/${dir.name}` : `${currentPath}/${dir.name}`;
						addWorkspace(fullPath);
						goto(`/?workspace=${encodeURIComponent(fullPath)}`);
						onclose();
					}}
					onmouseenter={() => {
						selectedIndex = i;
					}}
				>
					<Icon name="folder" size={14} class="shrink-0 text-gray-400" />
					<span class="flex-1 truncate text-xs">{dir.name}</span>
					<Icon name="chevron-right" size={12} class="shrink-0 text-gray-300 dark:text-gray-700" />
				</button>
			{/each}
		{/if}
	</div>

	<!-- Footer -->
	<div class="flex items-center gap-2 px-3.5 pb-3 pt-1 shrink-0">
		<span
			class="flex-1 text-[0.6875rem] text-gray-400 dark:text-gray-600 font-mono truncate min-w-0"
			title={currentPath}>{currentPath}</span
		>
		<button
			class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 px-3 py-1.5 rounded-lg transition-colors duration-100 shrink-0"
			onclick={onclose}>{$t('directory.cancel')}</button
		>
		<button
			class="text-xs text-white dark:text-black bg-gray-900 dark:bg-white hover:bg-gray-800 dark:hover:bg-white/90 px-3 py-1.5 rounded-lg transition-colors duration-100 shrink-0"
			onclick={selectCurrent}>{$t('directory.open', { name: currentFolderName })}</button
		>
	</div>
</Modal>

{#if actionsMenuOpen && actionsBtnEl}
	<DropdownMenu
		anchor={actionsBtnEl}
		align="end"
		items={[
			{
				label: $t('files.newFolder'),
				icon: 'folder',
				onclick: () => startCreatingFolder()
			},
			{
				label: showHidden ? $t('files.hideHidden') : $t('files.showHidden'),
				icon: 'eye',
				onclick: () => toggleHidden()
			}
		]}
		onclose={() => (actionsMenuOpen = false)}
	/>
{/if}
