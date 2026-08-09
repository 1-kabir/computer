<script lang="ts">
	import { sidebarOpen, sidebarWidth } from '$lib/stores';
	import DirectoryPicker from './DirectoryPicker.svelte';
	import SidebarFooter from './SidebarFooter.svelte';
	import SidebarHeader from './SidebarHeader.svelte';
	import SidebarNav from './SidebarNav.svelte';
	import SidebarWorkspaceList from './SidebarWorkspaceList.svelte';
	import SettingsModal from './SettingsModal.svelte';
	import SystemInfoModal from './SystemInfoModal.svelte';
	import { t } from '$lib/i18n';

	interface Props {
		gitSettingsAvailable?: boolean;
	}

	let { gitSettingsAvailable = false }: Props = $props();
	let showPicker = $state(false);
	let showSettings = $state(false);
	let showSystemInfo = $state(false);
	let settingsTab = $state<string>('general');

	const MIN_WIDTH = 160;
	const MAX_WIDTH = 400;
	let isResizing = $state(false);

	function startResize(e: PointerEvent) {
		// Only allow resize on md+ screens
		if (typeof window !== 'undefined' && window.innerWidth < 768) return;
		e.preventDefault();
		isResizing = true;
		const startX = e.clientX;
		const startWidth = $sidebarWidth;

		function onMove(ev: PointerEvent) {
			const delta = ev.clientX - startX;
			const newWidth = Math.round(Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth + delta)));
			sidebarWidth.set(newWidth);
		}

		function onUp() {
			isResizing = false;
			window.removeEventListener('pointermove', onMove);
			window.removeEventListener('pointerup', onUp);
			document.body.style.cursor = '';
			document.body.style.userSelect = '';
		}

		window.addEventListener('pointermove', onMove);
		window.addEventListener('pointerup', onUp);
		document.body.style.cursor = 'col-resize';
		document.body.style.userSelect = 'none';
	}

	function closeSidebar() {
		sidebarOpen.set(false);
	}

	function openSettings(tab = 'general') {
		settingsTab = tab;
		showSettings = true;
	}

	function openSystemInfo() {
		showSystemInfo = true;
	}
</script>

{#if $sidebarOpen}
	<button
		class="fixed inset-0 bg-black/50 z-40 cursor-default md:hidden"
		onclick={closeSidebar}
		aria-label={$t('sidebar.closeSidebar')}
	></button>

	<aside class="sidebar" style="--sw: {$sidebarWidth}px;">
		<!-- Resize handle (md+ only) -->
		<div
			class="resize-handle"
			class:active={isResizing}
			role="separator"
			aria-orientation="vertical"
			onpointerdown={startResize}
			ondblclick={() => sidebarWidth.set(220)}
		></div>
		<SidebarHeader />
		<SidebarNav />
		<SidebarWorkspaceList onaddworkspace={() => (showPicker = true)} />
		<SidebarFooter onsettings={openSettings} onsysteminfo={openSystemInfo} />
	</aside>
{/if}

{#if showPicker}
	<DirectoryPicker onclose={() => (showPicker = false)} />
{/if}

{#if showSettings}
	<SettingsModal
		{gitSettingsAvailable}
		initialTab={settingsTab}
		onclose={() => {
			showSettings = false;
			settingsTab = 'general';
		}}
	/>
{/if}

{#if showSystemInfo}
	<SystemInfoModal onclose={() => (showSystemInfo = false)} />
{/if}

<style>
	@reference "../../app.css";

	.sidebar {
		position: fixed;
		left: 0;
		top: 0;
		bottom: 0;
		width: 13.75rem;
		z-index: 50;
		display: flex;
		flex-direction: column;
		background: var(--app-bg);
		color: var(--app-fg);
		border-right: 1px solid var(--app-border);
		padding-top: env(safe-area-inset-top, 0);
	}

	:global(.dark) .sidebar {
		border-right-color: var(--app-border);
	}

	@media (min-width: 768px) {
		.sidebar {
			position: relative;
			z-index: auto;
			width: var(--sw, 13.75rem);
		}
	}

	.resize-handle {
		display: none;
	}

	@media (min-width: 768px) {
		.resize-handle {
			display: block;
			position: absolute;
			right: -0.1875rem;
			top: 0;
			bottom: 0;
			width: 0.375rem;
			cursor: col-resize;
			z-index: 10;
			transition: background 0.15s;
		}

		.resize-handle:hover,
		.resize-handle.active {
			background: var(--app-active);
		}
	}
</style>
