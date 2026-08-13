<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import Sortable from 'sortablejs';
	import {
		activeWorkspace,
		setActiveTab,
		closeTab,
		setActiveGroup,
		reorderTabs,
		openUntitledFileTab,
		openTerminalTab,
		openBrowserTab,
		openInSplit,
		closeGroup,
		moveTabToGroup,
		splitActive,
		splitCurrentTab,
		setSplitDirection,
		sidebarOpen,
		type EditorGroup,
		type Tab
	} from '$lib/stores';
	import { openChatTab } from '$lib/stores';
	import { chatEnabled, chatStatuses, isChatUnread, streamingChatTabs } from '$lib/stores/chat';
	import { voiceMemosEnabled, showVoiceMemo } from '$lib/stores/audio';
	import { keybindings, formatChord } from '$lib/stores/keybindings';
	import Icon from './Icon.svelte';
	import Spinner from './common/Spinner.svelte';
	import DropdownMenu from './DropdownMenu.svelte';
	import { tooltip } from '$lib/tooltip';
	import { t } from '$lib/i18n';
	import VoiceMemoModal from './VoiceMemoModal.svelte';
	import { TAB_DRAG_MIME } from '$lib/constants';

	interface Props {
		group: EditorGroup;
		home?: boolean;
		canClose?: boolean;
		isPrimary?: boolean;
		onTabDragOver?: () => void;
		onHomeSelect?: (tabId: string) => void;
		onHomeClose?: (tabId: string) => void;
		onHomeReorder?: (oldIndex: number, newIndex: number) => void;
		onHomeMove?: (tabId: string, fromGroupId: string) => void;
		onHomeNewChat?: () => void;
		onHomeNewTerminal?: () => void;
		onHomeNewBrowser?: () => void;
		onHomeSplit?: (direction: 'horizontal' | 'vertical') => void;
		onHomeCloseGroup?: () => void;
		homeSplitDirection?: 'horizontal' | 'vertical';
		homeSplitActive?: boolean;
		homeActive?: boolean;
	}

	let {
		group,
		home = false,
		canClose = false,
		isPrimary = false,
		onTabDragOver,
		onHomeSelect,
		onHomeClose,
		onHomeReorder,
		onHomeMove,
		onHomeNewChat,
		onHomeNewTerminal,
		onHomeNewBrowser,
		onHomeSplit,
		onHomeCloseGroup,
		homeSplitDirection = 'horizontal',
		homeSplitActive = false,
		homeActive = true
	}: Props = $props();

	let tabsEl: HTMLDivElement | undefined = $state();
	let sortable: Sortable | null = null;

	// Plus menu
	let plusBtnEl: HTMLButtonElement | undefined = $state();
	let showPlusMenu = $state(false);

	// Split menu
	let splitBtnEl: HTMLButtonElement | undefined = $state();
	let showSplitMenu = $state(false);

	// Context menu
	let contextMenu = $state<{ tab: Tab; x: number; y: number } | null>(null);

	let isWideScreen = $state(typeof window !== 'undefined' ? window.innerWidth >= 1024 : false);

	// Drop target highlight for cross-group drag
	let dropHighlight = $state(false);

	type TabDragPayload = { tabId: string; groupId: string };

	const displayTabs = $derived(group?.tabs ?? []);

	const isActiveGroup = $derived(home ? homeActive : $activeWorkspace?.activeGroupId === group?.id);

	function tabIconName(tab: Tab): string {
		switch (tab.type) {
			case 'home':
				return 'spark';
			case 'files':
				return 'folder';
			case 'terminal':
				return 'terminal';
			case 'file':
				return 'page';
			case 'chat':
				return 'chat-bubble';
			case 'preview':
				return 'monitor';
			case 'browser':
				return 'browser';
			default:
				return 'page';
		}
	}

	function handleTabClick(tab: Tab) {
		if (home) return onHomeSelect?.(tab.id);
		setActiveTab(tab.id, group.id);
	}

	function handleClose(e: Event, tabId: string) {
		e.stopPropagation();
		if (home) return onHomeClose?.(tabId);
		closeTab(tabId, group.id);
	}

	function handleCloseGroup(e: Event) {
		e.stopPropagation();
		if (home) return onHomeCloseGroup?.();
		closeGroup(group.id);
	}

	function handleContextMenu(e: MouseEvent, tab: Tab) {
		if (!isWideScreen || tab.permanent) return;
		e.preventDefault();
		contextMenu = { tab, x: e.clientX, y: e.clientY };
	}

	function handlePaneClick() {
		if (home) return;
		if (!isActiveGroup) {
			setActiveGroup(group.id);
		}
	}

	function toggleSidebar() {
		sidebarOpen.update((v) => !v);
	}

	// Native drag handlers are NOT needed. Sortable's setData handles it.
	// We keep the native drop handlers on the bar for cross-group drops.

	function hasTabDrag(dataTransfer: DataTransfer): boolean {
		return dataTransfer.types.includes(TAB_DRAG_MIME) || dataTransfer.types.includes('text/tab-id');
	}

	function readTabDragPayload(dataTransfer: DataTransfer): TabDragPayload | null {
		const raw = dataTransfer.getData(TAB_DRAG_MIME);
		if (raw) {
			try {
				const parsed = JSON.parse(raw) as Partial<TabDragPayload>;
				if (typeof parsed.tabId === 'string' && typeof parsed.groupId === 'string') {
					return { tabId: parsed.tabId, groupId: parsed.groupId };
				}
			} catch {
				// Fall through to the legacy payload below.
			}
		}

		const tabId = dataTransfer.getData('text/tab-id');
		const groupId = dataTransfer.getData('text/group-id');
		return tabId && groupId ? { tabId, groupId } : null;
	}

	function handleBarDragOver(e: DragEvent) {
		// Only accept tab drags (not file uploads)
		if (!e.dataTransfer || !hasTabDrag(e.dataTransfer)) return;
		e.preventDefault();
		e.stopPropagation();
		onTabDragOver?.();
		e.dataTransfer.dropEffect = 'move';
		dropHighlight = true;
	}

	function handleBarDragLeave() {
		dropHighlight = false;
	}

	function handleBarDrop(e: DragEvent) {
		dropHighlight = false;
		if (!e.dataTransfer) return;
		const payload = readTabDragPayload(e.dataTransfer);
		if (!payload) return;
		e.preventDefault();
		e.stopPropagation();
		if (payload.groupId === group.id) return; // same group, ignore
		if (home) onHomeMove?.(payload.tabId, payload.groupId);
		else moveTabToGroup(payload.tabId, payload.groupId, group.id);
	}

	const plusMenuItems = $derived(
		home
			? [
					...($chatEnabled
						? [
								{
									label: $t('bar.newChat'),
									icon: 'chat-bubble',
									shortcut: formatChord($keybindings.newChat),
									onclick: () => onHomeNewChat?.()
								}
							]
						: []),
					{
						label: $t('bar.newTerminal'),
						icon: 'terminal',
						shortcut: formatChord($keybindings.newTerminal),
						onclick: () => onHomeNewTerminal?.()
					},
					{
						label: $t('bar.newBrowser'),
						icon: 'browser',
						shortcut: formatChord($keybindings.newBrowser),
						onclick: () => onHomeNewBrowser?.()
					}
				]
			: [
					{
						label: $t('bar.newFile'),
						icon: 'page',
						shortcut: formatChord($keybindings.newFile),
						onclick: () => {
							openUntitledFileTab(group.id);
						}
					},
					...($chatEnabled
						? [
								{
									label: $t('bar.newChat'),
									icon: 'chat-bubble',
									shortcut: formatChord($keybindings.newChat),
									onclick: () => {
										openChatTab(undefined, group.id);
									}
								}
							]
						: []),
					{
						label: $t('bar.newTerminal'),
						icon: 'terminal',
						shortcut: formatChord($keybindings.newTerminal),
						onclick: () => {
							openTerminalTab(group.id);
						}
					},
					{
						label: $t('bar.newBrowser'),
						icon: 'browser',
						shortcut: formatChord($keybindings.newBrowser),
						onclick: () => openBrowserTab(group.id)
					},
					...($voiceMemosEnabled
						? [
								{
									label: $t('bar.voiceMemo'),
									icon: 'microphone',
									shortcut: formatChord($keybindings.voiceMemo),
									onclick: () => {
										showVoiceMemo.set(true);
									}
								}
							]
						: [])
				]
	);

	const contextMenuItems = $derived.by(() => {
		if (!contextMenu) return [];
		const tab = contextMenu.tab;
		const items: { label: string; icon?: string; onclick: () => void; divider?: boolean }[] = [];

		if (isWideScreen && tab.type === 'file' && tab.filePath) {
			items.push({
				label: $t('bar.splitRight'),
				icon: 'split-horizontal',
				onclick: () => openInSplit(tab.filePath!, 'horizontal')
			});
			items.push({
				label: $t('bar.splitDown'),
				icon: 'split-vertical',
				onclick: () => openInSplit(tab.filePath!, 'vertical')
			});
		}

		if (!tab.permanent) {
			if (items.length > 0) items.push({ label: '', onclick: () => {}, divider: true });
			items.push({
				label: $t('bar.closeTab'),
				icon: 'xmark',
				onclick: () => (home ? onHomeClose?.(tab.id) : closeTab(tab.id, group.id))
			});
		}

		return items;
	});

	const splitMenuItems = $derived.by(() => {
		const direction = home
			? homeSplitDirection
			: ($activeWorkspace?.splitDirection ?? 'horizontal');
		return [
			{
				label: $t('bar.splitRight'),
				icon: 'split-horizontal',
				active: direction === 'horizontal',
				onclick: () => {
					if (home) onHomeSplit?.('horizontal');
					else {
						setSplitDirection('horizontal');
						splitCurrentTab('horizontal');
					}
				}
			},
			{
				label: $t('bar.splitDown'),
				icon: 'split-vertical',
				active: direction === 'vertical',
				onclick: () => {
					if (home) onHomeSplit?.('vertical');
					else {
						setSplitDirection('vertical');
						splitCurrentTab('vertical');
					}
				}
			}
		];
	});

	function handleResize() {
		isWideScreen = window.innerWidth >= 1024;
	}

	onMount(() => {
		if (tabsEl) {
			sortable = Sortable.create(tabsEl, {
				animation: 150,
				ghostClass: 'tab-reorder-preview',
				dragClass: 'cursor-grabbing',
				direction: 'horizontal',
				delay: 200,
				delayOnTouchOnly: true,
				touchStartThreshold: 5,
				setData: (dataTransfer: DataTransfer, dragEl: HTMLElement) => {
					const tabId = dragEl.dataset.tabId ?? '';
					dataTransfer.setData(TAB_DRAG_MIME, JSON.stringify({ tabId, groupId: group.id }));
					dataTransfer.setData('text/tab-id', tabId);
					dataTransfer.setData('text/group-id', group.id);
				},
				onEnd: (evt) => {
					if (evt.oldIndex != null && evt.newIndex != null && evt.oldIndex !== evt.newIndex) {
						if (home) onHomeReorder?.(evt.oldIndex, evt.newIndex);
						else reorderTabs(evt.oldIndex, evt.newIndex, group.id);
					}
				}
			});
		}
		window.addEventListener('resize', handleResize);
	});

	onDestroy(() => {
		sortable?.destroy();
		if (typeof window !== 'undefined') window.removeEventListener('resize', handleResize);
	});
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	class="flex items-center h-9 px-1.5 gap-1 shrink-0 select-none border-b transition-colors duration-100
		{dropHighlight ? 'tab-reorder-drop-preview' : 'border-gray-200 dark:border-white/6'}
		{isActiveGroup ? '' : 'opacity-50'}"
	onclick={handlePaneClick}
	ondragover={handleBarDragOver}
	ondragleave={handleBarDragLeave}
	ondrop={handleBarDrop}
>
	<!-- Sidebar toggle (only in primary group when sidebar is closed) -->
	{#if isPrimary && !$sidebarOpen}
		<button
			class="flex items-center justify-center w-7 h-7 rounded-md text-gray-400 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-100"
			onclick={toggleSidebar}
			aria-label={$t('bar.toggleSidebar')}
			use:tooltip={$t('bar.sidebar')}
		>
			<Icon name="sidebar-expand" size={14} />
		</button>
	{/if}

	<!-- Tabs -->
	<div class="flex items-center gap-0.5 flex-1 min-w-0 overflow-x-auto group-tabs-row py-0.5">
		<div bind:this={tabsEl} class="flex items-center gap-0.5 shrink-0">
			{#each displayTabs as tab (tab.id)}
				{@const isActive = tab.id === group.activeTabId}
				{@const chatStatus =
					tab.type === 'chat' && tab.path ? $chatStatuses.get(tab.path) : undefined}
				<button
					class="flex items-center gap-1.5 px-2.5 h-7 rounded-lg text-xs font-medium whitespace-nowrap shrink-0 transition-all duration-100
						{isActive
						? 'bg-gray-200/50 text-gray-900 dark:bg-white/8 dark:text-white'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					data-tab-id={tab.id}
					onpointerdown={() => handleTabClick(tab)}
					onclick={() => handleTabClick(tab)}
					oncontextmenu={(e) => handleContextMenu(e, tab)}
				>
					{#if tab.type === 'chat' && (chatStatus?.active || $streamingChatTabs.has(tab.id))}
						<Spinner size={14} />
					{:else}
						<Icon name={tabIconName(tab)} size={14} />
					{/if}
					<span class="max-w-30 overflow-hidden text-ellipsis">
						{tab.type === 'files' ? ($activeWorkspace?.name ?? $t('bar.files')) : tab.label}
					</span>
					{#if tab.type === 'chat' && !isActive && isChatUnread(chatStatus)}
						<span class="size-1.5 shrink-0 rounded-full bg-sky-500" aria-hidden="true"></span>
					{/if}
					{#if tab.unsaved}<span class="w-1.5 h-1.5 rounded-full bg-gray-400 shrink-0"></span>{/if}
					{#if !tab.permanent}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<span
							class="flex items-center justify-center w-4 h-4 rounded text-gray-400 hover:bg-gray-200 hover:text-gray-700 dark:hover:bg-white/10 dark:hover:text-white"
							onpointerdown={(e) => e.stopPropagation()}
							onclick={(e) => handleClose(e, tab.id)}
							onkeydown={(e) => {
								if (e.key === 'Enter') handleClose(e, tab.id);
							}}
							role="button"
							tabindex="-1"
							aria-label={$t('bar.closeTab')}
						>
							<Icon name="xmark" size={12} />
						</span>
					{/if}
				</button>
			{/each}
		</div>

		<!-- Plus button -->
		<button
			bind:this={plusBtnEl}
			class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0"
			onclick={() => (showPlusMenu = !showPlusMenu)}
			aria-label={$t('bar.new')}
			use:tooltip={$t('bar.new')}
		>
			<Icon name="plus" size={14} />
		</button>
	</div>

	<!-- Right-side controls -->
	<div class="flex items-center gap-0.5 shrink-0">
		<!-- Split button (wide screens) -->
		{#if isWideScreen}
			<button
				bind:this={splitBtnEl}
				class="flex items-center justify-center w-7 h-7 rounded-lg transition-colors duration-100 shrink-0
					{(home ? homeSplitActive : $splitActive)
					? 'bg-gray-200/50 text-gray-900 dark:bg-white/8 dark:text-white'
					: 'text-gray-400 hover:text-gray-600 dark:hover:text-gray-300'}"
				onclick={() => (showSplitMenu = !showSplitMenu)}
				aria-label={$t('a11y.splitEditor')}
				use:tooltip={$t('a11y.splitEditor')}
			>
				<Icon
					name={(home ? homeSplitDirection : $activeWorkspace?.splitDirection) === 'vertical'
						? 'split-vertical'
						: 'split-horizontal'}
					size={14}
				/>
			</button>
		{/if}

		<!-- Close group button (when split is active) -->
		{#if canClose}
			<button
				class="flex items-center justify-center w-7 h-7 rounded-lg text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100 shrink-0"
				onclick={handleCloseGroup}
				aria-label={$t('a11y.closePane')}
				use:tooltip={$t('a11y.closePane')}
			>
				<Icon name="xmark" size={12} />
			</button>
		{/if}
	</div>
</div>

{#if showPlusMenu && plusBtnEl}
	<DropdownMenu
		items={plusMenuItems}
		anchor={plusBtnEl}
		onclose={() => (showPlusMenu = false)}
		className="min-w-52"
	/>
{/if}

{#if contextMenu}
	<DropdownMenu
		items={contextMenuItems}
		anchor={{ x: contextMenu.x, y: contextMenu.y }}
		onclose={() => (contextMenu = null)}
	/>
{/if}

{#if showSplitMenu && splitBtnEl}
	<DropdownMenu
		items={splitMenuItems}
		anchor={splitBtnEl}
		onclose={() => (showSplitMenu = false)}
	/>
{/if}

{#if !home && $showVoiceMemo}
	<VoiceMemoModal
		workspace={$activeWorkspace?.path ?? ''}
		directory={$activeWorkspace?.fileBrowserCwd ?? $activeWorkspace?.path ?? ''}
		onclose={() => showVoiceMemo.set(false)}
	/>
{/if}

<style>
	@reference "../../app.css";

	.group-tabs-row {
		scrollbar-width: none;
		-ms-overflow-style: none;
	}
	.group-tabs-row::-webkit-scrollbar {
		display: none;
	}

	.tab-reorder-preview {
		background: color-mix(in oklab, var(--app-fg) 6%, transparent) !important;
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--app-fg) 16%, transparent);
		opacity: 1 !important;
	}

	.tab-reorder-drop-preview {
		background: color-mix(in oklab, var(--app-fg) 6%, transparent);
		box-shadow: inset 0 0 0 1px color-mix(in oklab, var(--app-fg) 16%, transparent);
		border-color: transparent;
	}
</style>
