<script lang="ts">
	import { appVersion, showChangelog } from '$lib/stores';
	import { session, clearSession } from '$lib/session';
	import { t } from '$lib/i18n';
	import { keybindings, formatChord } from '$lib/stores/keybindings';
	import DropdownMenu from './DropdownMenu.svelte';
	import RunningAgentsModal from './RunningAgentsModal.svelte';

	interface Props {
		onsettings: (tab?: string) => void;
		onsysteminfo: () => void;
	}

	let { onsettings, onsysteminfo }: Props = $props();
	let showMenu = $state(false);
	let showRunningAgents = $state(false);
	let menuButtonEl: HTMLButtonElement | undefined = $state();
	const FEATURE_REQUEST_URL = 'https://github.com/open-webui/computer/issues/new/choose';

	function openSettings(tab?: string) {
		showMenu = false;
		onsettings(tab);
	}

	function openRunningAgents() {
		showMenu = false;
		showRunningAgents = true;
	}

	function openSystemInfo() {
		showMenu = false;
		onsysteminfo();
	}
</script>

<div class="relative px-1 pb-0.5 shrink-0">
	<button
		bind:this={menuButtonEl}
		class="flex items-center gap-2 w-full h-8 px-2 rounded-lg text-xs font-medium text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-100"
		onclick={() => (showMenu = !showMenu)}
	>
		<img
			src={$session?.profile_image_url || '/user.png'}
			alt="Avatar"
			class="w-5 h-5 rounded-full object-cover shrink-0"
		/>
		<span class="truncate"
			>{$session?.display_name || $session?.username || $t('sidebar.settings')}</span
		>
		{#if $appVersion}
			<button
				onclick={(e) => {
					e.stopPropagation();
					showChangelog.set(true);
				}}
				class="ml-auto text-[0.625rem] text-gray-400 dark:text-gray-600 hover:text-gray-500 dark:hover:text-gray-400 font-mono hover:underline cursor-pointer"
			>
				v{$appVersion}
			</button>
		{/if}
	</button>
</div>

{#if showMenu && menuButtonEl}
	<DropdownMenu
		anchor={menuButtonEl}
		matchWidth
		items={[
			...($session
				? [
						{
							label: $session.display_name || $session.username,
							image: $session.profile_image_url || '/user.png',
							onclick: () => openSettings('account')
						}
					]
				: []),
			...($session ? [{ divider: true, label: '', onclick: () => {} }] : []),
			{
				label: $t('sidebar.settings'),
				icon: 'settings',
				shortcut: formatChord($keybindings.openSettings),
				onclick: () => openSettings()
			},
			{
				label: $t('system.infoTitle'),
				icon: 'info',
				onclick: openSystemInfo
			},
			{
				label: $t('agents.runningTitle'),
				icon: 'spark',
				onclick: openRunningAgents
			},
			{
				label: $t('sidebar.suggestFeature'),
				icon: 'external-link',
				onclick: () => window.open(FEATURE_REQUEST_URL, '_blank', 'noopener,noreferrer')
			},
			{ divider: true, label: '', onclick: () => {} },
			{ label: $t('sidebar.logOut'), icon: 'log-out', onclick: clearSession }
		]}
		onclose={() => (showMenu = false)}
	/>
{/if}

{#if showRunningAgents}
	<RunningAgentsModal onclose={() => (showRunningAgents = false)} />
{/if}
