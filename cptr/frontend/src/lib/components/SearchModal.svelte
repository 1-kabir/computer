<script lang="ts">
	import {
		workspaceList,
		currentWorkspace,
		showSearch,
		openFileTab,
		setFileBrowserCwd
	} from '$lib/stores';
	import { getPathDisplayName } from '$lib/utils/paths';
	import { fileIconName } from '$lib/utils/fileIcon';
	import {
		unifiedSearch,
		getRecentChats,
		type ChatSearchResult,
		type FileSearchResult
	} from '$lib/apis/search';
	import { getFileMatches, type ContentMatch, type FileMatch } from '$lib/apis/files';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';
	import Modal from './Modal.svelte';
	import Icon from './Icon.svelte';
	import KeyPill from './KeyPill.svelte';
	import Spinner from './common/Spinner.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		onclose: () => void;
	}

	let { onclose }: Props = $props();

	let query = $state('');
	let chatResults = $state<ChatSearchResult[]>([]);
	let fileResults = $state<FileSearchResult[]>([]);
	let fileMatches = $state<FileMatch[]>([]);
	let recentChats = $state<ChatSearchResult[]>([]);
	let loading = $state(false);
	let fileSearchLoading = $state(false);
	let fileSearchLoadingMore = $state(false);
	let fileSearchError = $state<string | null>(null);
	let fileSearchLoadMoreError = $state(false);
	let fileSearchNextOffset = $state<number | null>(null);
	let selectedIndex = $state(0);
	let inputEl: HTMLInputElement | undefined = $state();
	let debounceTimer: ReturnType<typeof setTimeout> | null = null;
	let resultsEl: HTMLDivElement | undefined = $state();
	let fileSearchController: AbortController | null = null;
	let searchRequestId = 0;
	let searchTargetRequestId = 0;
	let lastWorkspaceScope: string | null | undefined;

	type SelectableItem =
		| { kind: 'chat'; chat: ChatSearchResult }
		| { kind: 'global-file'; file: FileSearchResult }
		| { kind: 'file-match'; match: FileMatch }
		| { kind: 'content-match'; match: FileMatch; content: ContentMatch };

	// Determine active workspace from the URL, not the store.
	// When on /scheduled or other non-workspace pages, $currentWorkspace
	// retains the last workspace but we should search across all.
	const urlWorkspacePath = $derived($page.url.searchParams.get('workspace'));
	const effectiveWorkspace = $derived(urlWorkspacePath ? $currentWorkspace : null);

	// Show recents when no query, search results when query exists
	const showingRecents = $derived(!query.trim());
	const filteredRecents = $derived(
		effectiveWorkspace
			? recentChats.filter((c) => c.workspace === effectiveWorkspace!.path)
			: recentChats
	);
	const displayedChats = $derived(showingRecents ? filteredRecents : chatResults);
	const filenameMatches = $derived(fileMatches.filter((match) => match.name_match));
	const contentOnlyMatches = $derived(fileMatches.filter((match) => !match.name_match));
	const usingRichFileSearch = $derived(Boolean(effectiveWorkspace && !showingRecents));
	const selectableItems = $derived.by(() => {
		const items: SelectableItem[] = displayedChats.map((chat) => ({ kind: 'chat', chat }));
		if (usingRichFileSearch) {
			for (const match of [...filenameMatches, ...contentOnlyMatches]) {
				items.push({ kind: 'file-match', match });
				const content = match.content_matches[0];
				if (content) {
					items.push({
						kind: 'content-match' as const,
						match,
						content
					});
				}
			}
		} else {
			items.push(...fileResults.map((file) => ({ kind: 'global-file' as const, file })));
		}
		return items;
	});
	const totalResults = $derived(selectableItems.length);

	$effect(() => {
		if ($showSearch) requestAnimationFrame(() => inputEl?.focus());
	});

	$effect(() => {
		if ($showSearch) loadRecents();
	});

	$effect(() => {
		const workspaceScope = effectiveWorkspace?.path ?? null;
		if (lastWorkspaceScope === undefined) {
			lastWorkspaceScope = workspaceScope;
			if ($showSearch && !workspaceScope) resetSearchState();
			return;
		}
		if (workspaceScope !== lastWorkspaceScope) {
			lastWorkspaceScope = workspaceScope;
			resetSearchState();
			return;
		}
		if ($showSearch && !workspaceScope) resetSearchState();
	});

	async function loadRecents() {
		try {
			const data = await getRecentChats(9, effectiveWorkspace?.path);
			recentChats = data.chats;
		} catch {
			// ignore
		}
	}

	function resetSearchState() {
		searchRequestId += 1;
		if (debounceTimer) clearTimeout(debounceTimer);
		debounceTimer = null;
		fileSearchController?.abort();
		fileSearchController = null;
		query = '';
		chatResults = [];
		fileResults = [];
		fileMatches = [];
		loading = false;
		fileSearchLoading = false;
		fileSearchLoadingMore = false;
		fileSearchError = null;
		fileSearchLoadMoreError = false;
		fileSearchNextOffset = null;
		selectedIndex = 0;
	}

	$effect(() => {
		if (debounceTimer) clearTimeout(debounceTimer);
		if (!query.trim()) {
			chatResults = [];
			fileResults = [];
			fileMatches = [];
			loading = false;
			fileSearchLoading = false;
			fileSearchLoadingMore = false;
			fileSearchError = null;
			fileSearchLoadMoreError = false;
			fileSearchNextOffset = null;
			fileSearchController?.abort();
			fileSearchController = null;
			selectedIndex = 0;
			return;
		}
		loading = true;
		debounceTimer = setTimeout(() => doSearch(query), 250);
	});

	// When a workspace is explicitly active via URL, scope to files from that workspace only
	const isWorkspaceScoped = $derived(!!effectiveWorkspace);

	async function doSearch(q: string) {
		const requestId = ++searchRequestId;
		const activeWs = effectiveWorkspace;
		fileSearchController?.abort();

		if (activeWs) {
			// Workspace active: search chats + files scoped to this workspace
			const controller = new AbortController();
			fileSearchController = controller;
			fileResults = [];
			fileMatches = [];
			fileSearchError = null;
			fileSearchLoadMoreError = false;
			fileSearchNextOffset = null;
			fileSearchLoading = true;
			try {
				const [chatData, matchData] = await Promise.all([
					unifiedSearch(q, [], activeWs.path, 10, 0),
					getFileMatches(q, activeWs.path, false, 0, controller.signal)
				]);
				if (requestId === searchRequestId) {
					chatResults = chatData.chats;
					fileMatches = matchData.results;
					fileSearchNextOffset = matchData.next_offset;
					selectedIndex = 0;
				}
			} catch (e: any) {
				if (e.name !== 'AbortError' && requestId === searchRequestId) {
					fileSearchError = e.message || $t('files.failedToSearch');
					fileMatches = [];
				}
			} finally {
				if (requestId === searchRequestId) {
					loading = false;
					fileSearchLoading = false;
				}
			}
		} else {
			// No workspace: full search across all workspaces
			fileMatches = [];
			fileSearchLoading = false;
			fileSearchError = null;
			fileSearchNextOffset = null;
			const wsPaths = $workspaceList.map((w) => w.path);
			if (wsPaths.length === 0) {
				chatResults = [];
				fileResults = [];
				loading = false;
				return;
			}
			try {
				const data = await unifiedSearch(q, wsPaths);
				if (requestId === searchRequestId) {
					chatResults = data.chats;
					fileResults = data.files;
					selectedIndex = 0;
				}
			} catch {
				// ignore
			} finally {
				if (requestId === searchRequestId) loading = false;
			}
		}
	}

	async function loadMoreFileMatches() {
		const activeWs = effectiveWorkspace;
		const offset = fileSearchNextOffset;
		const q = query.trim();
		if (!activeWs || offset === null || !q || fileSearchLoadingMore || fileSearchLoadMoreError)
			return;

		fileSearchLoadingMore = true;
		const requestId = searchRequestId;
		try {
			const data = await getFileMatches(q, activeWs.path, false, offset);
			if (requestId === searchRequestId) {
				fileMatches = [...fileMatches, ...data.results];
				fileSearchNextOffset = data.next_offset;
			}
		} catch {
			if (requestId === searchRequestId) fileSearchLoadMoreError = true;
		} finally {
			if (requestId === searchRequestId) fileSearchLoadingMore = false;
		}
	}

	function retryMoreFileMatches() {
		fileSearchLoadMoreError = false;
		void loadMoreFileMatches();
	}

	function loadMoreOnVisible(node: HTMLElement) {
		const observer = new IntersectionObserver(
			([entry]) => {
				if (entry.isIntersecting) void loadMoreFileMatches();
			},
			{ root: resultsEl, rootMargin: '160px' }
		);
		observer.observe(node);
		return { destroy: () => observer.disconnect() };
	}

	function getWorkspaceDisplayName(workspacePath: string): string {
		const workspace = $workspaceList.find((item) => item.path === workspacePath);
		if (workspace) return workspace.name;
		return getPathDisplayName(workspacePath, workspacePath);
	}

	function selectChat(chat: ChatSearchResult) {
		onclose();
		goto(`/?workspace=${encodeURIComponent(chat.workspace)}&chatId=${encodeURIComponent(chat.id)}`);
	}

	function selectFile(file: FileSearchResult) {
		onclose();
		if (file.type === 'file') {
			goto(
				`/?workspace=${encodeURIComponent(file.workspace)}&file=${encodeURIComponent(file.path)}`
			);
		} else if (file.type === 'directory') {
			goto(
				`/?workspace=${encodeURIComponent(file.workspace)}&dir=${encodeURIComponent(file.path)}`
			);
		}
	}

	function selectFileMatch(match: FileMatch) {
		onclose();
		if (match.type === 'directory') {
			setFileBrowserCwd(match.path);
		} else {
			openFileTab(match.path);
		}
	}

	function selectContentMatch(match: FileMatch, content: ContentMatch) {
		onclose();
		openFileTab(match.path, undefined, {
			searchTarget: {
				line: content.line,
				column: content.column,
				length: query.trim().length,
				requestId: ++searchTargetRequestId
			}
		});
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!$showSearch) return;
		// Cmd+1-9: jump to recent chat
		if ((e.metaKey || e.ctrlKey) && e.key >= '1' && e.key <= '9' && showingRecents) {
			const idx = parseInt(e.key) - 1;
			if (idx < recentChats.length) {
				e.preventDefault();
				selectChat(recentChats[idx]);
				return;
			}
		}
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			selectedIndex = Math.min(selectedIndex + 1, totalResults - 1);
			scrollSelectedIntoView();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			selectedIndex = Math.max(selectedIndex - 1, 0);
			scrollSelectedIntoView();
		} else if (e.key === 'Enter' && totalResults > 0) {
			e.preventDefault();
			activateSelected();
		}
	}

	function scrollSelectedIntoView() {
		requestAnimationFrame(() => {
			const el = resultsEl?.querySelector(`[data-idx="${selectedIndex}"]`);
			el?.scrollIntoView({ block: 'nearest', behavior: 'instant' });
		});
	}

	function activateSelected() {
		const item = selectableItems[selectedIndex];
		if (!item) return;
		if (item.kind === 'chat') selectChat(item.chat);
		else if (item.kind === 'global-file') selectFile(item.file);
		else if (item.kind === 'file-match') selectFileMatch(item.match);
		else selectContentMatch(item.match, item.content);
	}

	function selectedItemIndex(kind: SelectableItem['kind'], id: string): number {
		return selectableItems.findIndex((item) => {
			if (item.kind !== kind) return false;
			if (item.kind === 'chat') return item.chat.id === id;
			if (item.kind === 'global-file') return item.file.path === id;
			return item.match.path === id;
		});
	}

	function contentItemIndex(match: FileMatch, content: ContentMatch): number {
		return selectableItems.findIndex(
			(item) =>
				item.kind === 'content-match' &&
				item.match.path === match.path &&
				item.content.line === content.line &&
				item.content.column === content.column
		);
	}

	function parentPath(relativePath: string): string {
		const slash = relativePath.lastIndexOf('/');
		return slash === -1 ? '' : relativePath.slice(0, slash);
	}

	function displayRelativePath(path: string, workspacePath: string): string {
		const relative = relPath(path, workspacePath);
		return parentPath(relative);
	}

	function setSelected(index: number) {
		if (index >= 0) selectedIndex = index;
	}

	function fileMatchKey(match: FileMatch): string {
		return `${match.path}:file`;
	}

	function searchStatusEmpty(): boolean {
		if (loading || fileSearchLoading) return false;
		if (usingRichFileSearch) return displayedChats.length === 0 && fileMatches.length === 0;
		return totalResults === 0;
	}

	function resultButtonClass(idx: number, extra = ''): string {
		return `${extra} ${
			idx === selectedIndex
				? 'bg-gray-200/50 text-gray-900 dark:bg-white/6 dark:text-white'
				: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'
		}`;
	}

	function relPath(fullPath: string, wsPath: string): string {
		return fullPath.startsWith(wsPath) ? fullPath.slice(wsPath.length + 1) : fullPath;
	}

	function relativeTime(ts: number): string {
		const now = Date.now();
		const diffMs = now - ts;
		const diffSec = Math.floor(diffMs / 1000);
		if (diffSec < 60) return 'now';
		const diffMin = Math.floor(diffSec / 60);
		if (diffMin < 60) return `${diffMin}m`;
		const diffHr = Math.floor(diffMin / 60);
		if (diffHr < 24) return `${diffHr}h`;
		const diffDay = Math.floor(diffHr / 24);
		if (diffDay < 30) return `${diffDay}d`;
		const diffMonth = Math.floor(diffDay / 30);
		return `${diffMonth}mo`;
	}
</script>

<svelte:window onkeydown={handleKeydown} />

{#if $showSearch}
	<Modal
		{onclose}
		class="w-full max-w-[35rem] mx-4 max-md:mx-0 max-md:rounded-none max-h-[30rem] max-md:max-h-dvh flex flex-col mb-[6vh] max-md:mb-0"
	>
		<!-- Search input -->
		<div class="flex items-center px-3.5 py-3 gap-2">
			<Icon name="search" size={14} class="text-gray-400 shrink-0" />
			<input
				bind:this={inputEl}
				type="text"
				class="flex-1 border-none outline-none bg-transparent text-xs text-gray-900 dark:text-white font-sans placeholder:text-gray-400"
				placeholder={$t('search.placeholder')}
				bind:value={query}
			/>
		</div>

		<!-- Results -->
		<div bind:this={resultsEl} class="overflow-y-auto px-1.5 pb-1.5 flex-1 min-h-0">
			{#if showingRecents && filteredRecents.length > 0}
				<!-- Recent chats (empty query) -->
				<div
					class="text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600 px-2 pt-1 pb-0.5"
				>
					{$t('search.recentChats')}
				</div>
				{#each filteredRecents as chat, i (chat.id)}
					<button
						data-idx={i}
						class="group flex items-center gap-2 w-full h-7 px-2 rounded-xl text-left transition-colors duration-75
						{i === selectedIndex
							? 'bg-gray-200/50 text-gray-900 dark:bg-white/6 dark:text-white'
							: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'}"
						onclick={() => selectChat(chat)}
						onmouseenter={() => (selectedIndex = i)}
					>
						<span class="text-xs font-medium truncate flex-1">{chat.title}</span>
						{#if i < 9 && !isWorkspaceScoped}
							<KeyPill
								text={`⌘${i + 1}`}
								class="shrink-0 opacity-0 group-hover:opacity-100 transition-opacity duration-100"
							/>
						{/if}
						<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0"
							>{getWorkspaceDisplayName(chat.workspace)}</span
						>
					</button>
				{/each}
			{:else if showingRecents}
				<div class="py-6 text-center text-xs text-gray-400 dark:text-gray-600">
					{$t('search.noRecentChats')}
				</div>
			{:else if !showingRecents}
				{#if displayedChats.length > 0}
					<!-- Chat search results -->
					<div
						class="text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600 px-2 pt-1 pb-0.5"
					>
						{$t('search.chats')}
					</div>
					{#each displayedChats as chat, i (chat.id)}
						{@const idx = i}
						<button
							data-idx={idx}
							class="flex items-start gap-2 w-full px-2 py-1.5 rounded-xl text-left transition-colors duration-75
							{idx === selectedIndex
								? 'bg-gray-200/50 text-gray-900 dark:bg-white/6 dark:text-white'
								: 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-white/4'}"
							onclick={() => selectChat(chat)}
							onmouseenter={() => (selectedIndex = idx)}
						>
							<div class="flex-1 min-w-0">
								<div class="flex items-center gap-2">
									<span class="text-xs font-medium truncate">{chat.title}</span>
									<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0"
										>{relativeTime(chat.updated_at)}</span
									>
								</div>
								{#if chat.snippet}
									<div class="text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate mt-0.5">
										{chat.snippet}
									</div>
								{/if}
							</div>
							<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0"
								>{getWorkspaceDisplayName(chat.workspace)}</span
							>
						</button>
					{/each}
				{/if}

				{#if usingRichFileSearch && filenameMatches.length > 0}
					<div
						class="text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600 px-2 pt-1 pb-0.5 {displayedChats.length >
						0
							? 'mt-1'
							: ''}"
					>
						{$t('files.filenameMatches')}
					</div>
					{#each filenameMatches as match (fileMatchKey(match))}
						{@const idx = selectedItemIndex('file-match', match.path)}
						<button
							data-idx={idx}
							class={resultButtonClass(
								idx,
								'flex items-center gap-2 w-full h-7 px-2 rounded-xl text-left transition-colors duration-75'
							)}
							onclick={() => selectFileMatch(match)}
							onmouseenter={() => setSelected(idx)}
						>
							<Icon
								name={fileIconName(match.name, match.type)}
								size={14}
								strokeWidth={1.4}
								class="shrink-0 {match.type === 'directory'
									? 'text-gray-500 dark:text-gray-400'
									: 'text-gray-400 dark:text-gray-500'}"
							/>
							<span class="text-xs font-medium shrink-0">{match.name}</span>
							<span
								class="text-[0.6875rem] text-gray-400 overflow-hidden text-ellipsis whitespace-nowrap"
								>{parentPath(match.relative_path)}</span
							>
							<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0 ml-auto"
								>{getWorkspaceDisplayName(effectiveWorkspace!.path)}</span
							>
						</button>
						{#if match.content_matches.length > 0}
							{@const content = match.content_matches[0]}
							{@const contentIdx = contentItemIndex(match, content)}
							<button
								data-idx={contentIdx}
								class={resultButtonClass(
									contentIdx,
									'flex items-center gap-2 w-full h-6 pl-8 pr-2 rounded-xl text-left transition-colors duration-75'
								)}
								onclick={() => selectContentMatch(match, content)}
								onmouseenter={() => setSelected(contentIdx)}
							>
								<span
									class="w-6 shrink-0 text-right text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
									>{content.line}</span
								>
								<span
									class="min-w-0 flex-1 truncate text-[0.6875rem] font-mono text-gray-500 dark:text-gray-500"
									>{content.text}</span
								>
								{#if match.content_matches.length > 1}
									<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600"
										>+{match.content_matches.length - 1}</span
									>
								{/if}
							</button>
						{/if}
					{/each}
				{/if}

				{#if usingRichFileSearch && contentOnlyMatches.length > 0}
					<div
						class="text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600 px-2 pt-2 pb-0.5 {displayedChats.length >
							0 || filenameMatches.length > 0
							? 'mt-1'
							: ''}"
					>
						{$t('files.contentMatches')}
					</div>
					{#each contentOnlyMatches as match (fileMatchKey(match))}
						{@const idx = selectedItemIndex('file-match', match.path)}
						<button
							data-idx={idx}
							class={resultButtonClass(
								idx,
								'flex items-center gap-2 w-full h-7 px-2 rounded-xl text-left transition-colors duration-75'
							)}
							onclick={() => selectFileMatch(match)}
							onmouseenter={() => setSelected(idx)}
						>
							<Icon
								name={fileIconName(match.name, match.type)}
								size={14}
								strokeWidth={1.4}
								class="shrink-0 text-gray-400 dark:text-gray-500"
							/>
							<span class="text-xs font-medium shrink-0">{match.name}</span>
							<span
								class="text-[0.6875rem] text-gray-400 overflow-hidden text-ellipsis whitespace-nowrap"
								>{displayRelativePath(match.path, effectiveWorkspace!.path)}</span
							>
							<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0 ml-auto"
								>{getWorkspaceDisplayName(effectiveWorkspace!.path)}</span
							>
						</button>
						{#if match.content_matches.length > 0}
							{@const content = match.content_matches[0]}
							{@const contentIdx = contentItemIndex(match, content)}
							<button
								data-idx={contentIdx}
								class={resultButtonClass(
									contentIdx,
									'flex items-center gap-2 w-full h-6 pl-8 pr-2 rounded-xl text-left transition-colors duration-75'
								)}
								onclick={() => selectContentMatch(match, content)}
								onmouseenter={() => setSelected(contentIdx)}
							>
								<span
									class="w-6 shrink-0 text-right text-[0.625rem] font-mono text-gray-400 dark:text-gray-600"
									>{content.line}</span
								>
								<span
									class="min-w-0 flex-1 truncate text-[0.6875rem] font-mono text-gray-500 dark:text-gray-500"
									>{content.text}</span
								>
								{#if match.content_matches.length > 1}
									<span class="shrink-0 text-[0.625rem] text-gray-400 dark:text-gray-600"
										>+{match.content_matches.length - 1}</span
									>
								{/if}
							</button>
						{/if}
					{/each}
				{/if}

				{#if !usingRichFileSearch && fileResults.length > 0}
					<!-- File search results -->
					<div
						class="text-[0.625rem] font-medium uppercase tracking-wide text-gray-400 dark:text-gray-600 px-2 pt-1 pb-0.5 {displayedChats.length >
						0
							? 'mt-1'
							: ''}"
					>
						{$t('search.files')}
					</div>
					{#each fileResults as file (file.path)}
						{@const idx = selectedItemIndex('global-file', file.path)}
						<button
							data-idx={idx}
							class={resultButtonClass(
								idx,
								'flex items-center gap-2 w-full h-7 px-2 rounded-xl text-left transition-colors duration-75'
							)}
							onclick={() => selectFile(file)}
							onmouseenter={() => setSelected(idx)}
						>
							<Icon
								name={file.type === 'directory' ? 'folder' : 'empty-page'}
								size={14}
								class="shrink-0 text-gray-400"
							/>
							<span class="text-xs font-medium shrink-0">{file.name}</span>
							<span
								class="text-[0.6875rem] text-gray-400 overflow-hidden text-ellipsis whitespace-nowrap"
								>{relPath(file.path, file.workspace)}</span
							>
							<span class="text-[0.625rem] text-gray-400 dark:text-gray-600 shrink-0 ml-auto"
								>{getWorkspaceDisplayName(file.workspace)}</span
							>
						</button>
					{/each}
				{/if}

				{#if usingRichFileSearch && fileSearchNextOffset !== null}
					<div use:loadMoreOnVisible class="flex h-8 items-center justify-center">
						{#if fileSearchLoadingMore}
							<Spinner size={12} />
						{:else if fileSearchLoadMoreError}
							<button
								class="text-[0.6875rem] text-gray-400 hover:text-gray-600 dark:text-gray-600 dark:hover:text-gray-400 transition-colors duration-75"
								onclick={retryMoreFileMatches}>{$t('files.retry')}</button
							>
						{/if}
					</div>
				{/if}

				{#if loading || fileSearchLoading}
					<div class="flex items-center justify-center py-8">
						<Spinner size={16} />
					</div>
				{:else if fileSearchError}
					<div class="p-6 text-center text-xs text-red-400">{fileSearchError}</div>
				{:else if query && searchStatusEmpty()}
					<div class="p-6 text-center text-xs text-gray-400">{$t('search.noResults')}</div>
				{/if}
			{/if}
		</div>
	</Modal>
{/if}
