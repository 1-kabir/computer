<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { get } from 'svelte/store';
	import Icon from './Icon.svelte';
	import Modal from './Modal.svelte';
	import General from './Settings/General.svelte';
	import Notifications from './Settings/Notifications.svelte';
	import Appearance from './Settings/Appearance.svelte';
	import Usage from './Settings/Usage.svelte';
	import Memory from './Settings/Memory.svelte';
	import PWA from './Settings/PWA.svelte';
	import Account from './Settings/Account.svelte';
	import Keyboard from './Settings/Keyboard.svelte';
	import Users from './Admin/Users.svelte';
	import Connections from './Admin/Connections.svelte';
	import Agents from './Admin/Agents.svelte';
	import Models from './Admin/Models.svelte';
	import Chat from './Admin/Chat.svelte';
	import Tools from './Admin/Tools.svelte';
	import Git from './Settings/Git.svelte';
	import Skills from './Admin/Skills.svelte';
	import Messaging from './Admin/Messaging.svelte';
	import Gateway from './Admin/Gateway.svelte';
	import AudioSettings from './Admin/AudioSettings.svelte';
	import Images from './Admin/Images.svelte';
	import AdminWeb from './Admin/Web.svelte';
	import ToolServers from './Admin/ToolServers.svelte';
	import Subagents from './Admin/Subagents.svelte';
	import Workspace from './Admin/Workspace.svelte';
	import { session } from '$lib/session';
	import { t } from '$lib/i18n';

	type Tab =
		| 'general'
		| 'notifications'
		| 'appearance'
		| 'usage'
		| 'memory'
		| 'pwa'
		| 'keyboard'
		| 'account'
		| 'users'
		| 'connections'
		| 'agents'
		| 'models'
		| 'chat'
		| 'tools'
		| 'git'
		| 'skills'
		| 'messaging'
		| 'gateway'
		| 'audio'
		| 'images'
		| 'web'
		| 'toolservers'
		| 'subagents'
		| 'workspace';

	interface Props {
		onclose: () => void;
		initialTab?: string;
		gitSettingsAvailable?: boolean;
	}

	let { onclose, initialTab = 'general', gitSettingsAvailable = false }: Props = $props();

	function normalizeTab(tab: string): Tab {
		const validTabs: Tab[] = [
			'general',
			'notifications',
			'appearance',
			'usage',
			'memory',
			'pwa',
			'keyboard',
			'account',
			'users',
			'connections',
			'agents',
			'models',
			'chat',
			'tools',
			'git',
			'skills',
			'messaging',
			'gateway',
			'audio',
			'images',
			'web',
			'toolservers',
			'subagents',
			'workspace'
		];
		return validTabs.includes(tab as Tab) ? (tab as Tab) : 'general';
	}

	let activeTab = $state<Tab>(
		untrack(() =>
			initialTab === 'pwa' || (initialTab === 'git' && !gitSettingsAvailable)
				? 'general'
				: normalizeTab(initialTab)
		)
	);
	let showPwaSettings = $state(false);

	const isAdmin = $derived($session?.role === 'admin');
	const tr = (key: string) => (get(t) as (key: string) => string)(key);

	type SettingsTab = { id: Tab; label: string; icon: string };

	const adminTabIds: Tab[] = [
		'users',
		'connections',
		'agents',
		'models',
		'chat',
		'tools',
		'messaging',
		'gateway',
		'audio',
		'images',
		'web',
		'toolservers',
		'subagents',
		'workspace',
		'memory',
		'skills'
	];

	const personalTabs: SettingsTab[] = $derived.by(() => {
		const tabs: SettingsTab[] = [
			{ id: 'general', label: tr('settings.general'), icon: 'settings' },
			{ id: 'appearance', label: tr('settings.appearance'), icon: 'sun-light' },
			{ id: 'usage', label: 'Usage', icon: 'usage' },
			{ id: 'notifications', label: tr('general.notifications'), icon: 'chat-bubble' },
			{ id: 'keyboard', label: tr('settings.keyboard'), icon: 'terminal' },
			{ id: 'account', label: tr('settings.account'), icon: 'user' }
		];
		if (gitSettingsAvailable)
			tabs.splice(tabs.length - 1, 0, { id: 'git', label: tr('admin.git'), icon: 'git-branch' });
		if (showPwaSettings) tabs.push({ id: 'pwa', label: 'PWA', icon: 'phone' });
		return tabs;
	});

	const adminTabs: { id: Tab; label: string; icon: string }[] = $derived([
		{ id: 'users', label: tr('admin.users'), icon: 'user' },
		{ id: 'connections', label: tr('admin.connections'), icon: 'plug' },
		{ id: 'agents', label: tr('admin.agents'), icon: 'terminal' },
		{ id: 'models', label: tr('admin.models'), icon: 'cube' },
		{ id: 'chat', label: tr('admin.chat'), icon: 'chat-bubble' },
		{ id: 'tools', label: tr('admin.tools'), icon: 'tools' },
		{ id: 'messaging', label: tr('admin.messaging'), icon: 'chat-bubble' },
		{ id: 'gateway', label: tr('admin.gateway.tab'), icon: 'gateway' },
		{ id: 'audio', label: tr('admin.audio.title'), icon: 'microphone' },
		{ id: 'images', label: tr('admin.images.title'), icon: 'image' },
		{ id: 'web', label: tr('admin.web'), icon: 'globe' },
		{ id: 'toolservers', label: tr('admin.toolServers'), icon: 'plug' },
		{ id: 'subagents', label: tr('admin.subagents'), icon: 'user' },
		{ id: 'workspace', label: tr('admin.workspace'), icon: 'folder' },
		{ id: 'memory', label: tr('settings.memory'), icon: 'brain' },
		{ id: 'skills', label: tr('chat.skills'), icon: 'spark' }
	]);

	onMount(() => {
		showPwaSettings = isInstalledPwa();
		if (showPwaSettings && initialTab === 'pwa') {
			activeTab = 'pwa';
		} else if (
			!$session ||
			($session.role !== 'admin' && adminTabIds.includes(normalizeTab(initialTab)))
		) {
			activeTab = 'general';
		} else if (initialTab === 'git' && !gitSettingsAvailable) {
			activeTab = 'general';
		} else if (initialTab !== 'pwa') {
			activeTab = normalizeTab(initialTab);
		} else {
			activeTab = 'general';
		}
	});

	function isInstalledPwa(): boolean {
		const nav = navigator as Navigator & { standalone?: boolean };
		return (
			window.matchMedia('(display-mode: standalone)').matches ||
			window.matchMedia('(display-mode: window-controls-overlay)').matches ||
			nav.standalone === true
		);
	}
</script>

<Modal
	{onclose}
	class="w-full max-w-3xl lg:max-w-4xl xl:max-w-5xl mx-4 md:mx-0 flex flex-col md:flex-row max-h-[85vh] lg:max-h-[90vh] md:h-[35rem] lg:h-[42rem] xl:h-[46rem]"
>
	<nav
		class="shrink-0 min-w-0 md:min-h-0 overflow-x-auto md:overflow-x-hidden md:overflow-y-auto scrollbar-none border-b md:border-b-0 md:border-r border-gray-200 dark:border-white/6 md:w-[11.25rem]"
	>
		<div class="flex w-max min-w-full md:w-auto md:min-w-0 md:flex-col p-1 gap-px">
			<button
				class="flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 transition-colors duration-75 md:mb-1"
				onclick={onclose}
			>
				<Icon name="chevron-left" size={12} />
				<span>{tr('settings.back')}</span>
			</button>

			<!-- Personal -->
			{#each personalTabs as tab}
				<button
					class="flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-left transition-colors duration-75
						{activeTab === tab.id
						? 'font-medium text-gray-900 dark:text-white bg-gray-100 dark:bg-white/6'
						: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
					onclick={() => (activeTab = tab.id)}
				>
					<Icon name={tab.icon} size={14} />
					{tab.label}
				</button>
			{/each}

			<!-- Admin section -->
			{#if isAdmin}
				<span
					class="hidden md:block text-[0.625rem] text-gray-400 dark:text-gray-600 px-2 mt-2 mb-0.5"
					>{tr('sidebar.admin')}</span
				>

				{#each adminTabs as tab}
					<button
						class="flex items-center gap-1.5 h-7 px-2 md:w-full shrink-0 rounded-lg text-xs text-left transition-colors duration-75
							{activeTab === tab.id
							? 'font-medium text-gray-900 dark:text-white bg-gray-100 dark:bg-white/6'
							: 'text-gray-500 hover:text-gray-700 dark:hover:text-gray-300'}"
						onclick={() => (activeTab = tab.id)}
					>
						<Icon name={tab.icon} size={14} />
						{tab.label}
					</button>
				{/each}
			{/if}
		</div>
	</nav>

	<div class="flex-1 overflow-y-auto scrollbar-none min-h-0 p-4 md:px-5">
		{#if activeTab === 'general'}
			<General {showPwaSettings} />
		{:else if activeTab === 'notifications'}
			<Notifications />
		{:else if activeTab === 'appearance'}
			<Appearance />
		{:else if activeTab === 'usage'}
			<Usage />
		{:else if activeTab === 'memory'}
			<Memory />
		{:else if activeTab === 'pwa' && showPwaSettings}
			<PWA />
		{:else if activeTab === 'keyboard'}
			<Keyboard />
		{:else if activeTab === 'account'}
			<Account />
		{:else if activeTab === 'users'}
			<Users />
		{:else if activeTab === 'connections'}
			<Connections />
		{:else if activeTab === 'agents'}
			<Agents />
		{:else if activeTab === 'models'}
			<Models />
		{:else if activeTab === 'chat'}
			<Chat />
		{:else if activeTab === 'tools'}
			<Tools />
		{:else if activeTab === 'git'}
			<Git />
		{:else if activeTab === 'skills'}
			<Skills />
		{:else if activeTab === 'messaging'}
			<Messaging />
		{:else if activeTab === 'gateway'}
			<Gateway />
		{:else if activeTab === 'audio'}
			<AudioSettings />
		{:else if activeTab === 'images'}
			<Images />
		{:else if activeTab === 'web'}
			<AdminWeb />
		{:else if activeTab === 'toolservers'}
			<ToolServers />
		{:else if activeTab === 'subagents'}
			<Subagents />
		{:else if activeTab === 'workspace'}
			<Workspace />
		{/if}
	</div>
</Modal>
