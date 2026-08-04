<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import {
		getAgents,
		refreshAgents,
		updateAgents,
		type AgentProfile,
		type AgentsResponseProfile
	} from '$lib/apis/admin';
	import Icon from '$lib/components/Icon.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import { t } from '$lib/i18n';
	import { refreshChatState } from '$lib/stores/chat';
	import AgentProfileModal from './AgentProfileModal.svelte';

	type ModalState =
		| { mode: 'create'; profile: AgentProfile }
		| { mode: 'edit'; index: number; profile: AgentProfile };

	let loading = $state(true);
	let saving = $state(false);
	let refreshing = $state(false);
	let profiles = $state<AgentProfile[]>([]);
	let detected = $state<Record<string, AgentsResponseProfile['detected']>>({});
	let available = $state<Record<string, boolean>>({});
	let saved = $state<Record<string, boolean>>({});
	let modal = $state<ModalState | null>(null);
	let deletedProfile = $state(false);

	const statusLabelKey: Record<string, string> = {
		ready: 'admin.agentsStatusReady',
		not_found: 'admin.agentsStatusNotFound',
		missing_dependency: 'admin.agentsStatusMissingDependency',
		auth_unknown: 'admin.agentsStatusAuthUnknown',
		error: 'admin.agentsStatusError'
	};

	function normalizeProfile(profile: AgentProfile): AgentProfile {
		const models = profile.models?.map((m) => m.trim()).filter(Boolean);
		const nextModels = models.length ? models : [];
		const defaultModel = nextModels.includes(profile.default_model)
			? profile.default_model
			: nextModels[0] || '';
		return {
			...profile,
			id: profile.id.trim(),
			name: profile.name.trim() || profile.id.trim(),
			command: profile.command.trim() || defaultCommand(profile.agent),
			home: profile.home?.trim() || null,
			models: nextModels,
			default_model: defaultModel,
			launch_args: profile.launch_args?.trim() || '',
			api_endpoint: profile.api_endpoint?.trim() || '',
			server_url: profile.server_url?.trim() || '',
			server_password: profile.server_password?.trim() || ''
		};
	}

	function applyResponse(data: { profiles: AgentsResponseProfile[] }) {
		profiles = data.profiles.map((entry) => entry.config);
		detected = Object.fromEntries(data.profiles.map((entry) => [entry.id, entry.detected]));
		available = Object.fromEntries(data.profiles.map((entry) => [entry.id, entry.available]));
		saved = Object.fromEntries(data.profiles.map((entry) => [entry.id, !entry.implicit]));
		deletedProfile = false;
	}

	function newProfile(agent: AgentProfile['agent'] = 'codex'): AgentProfile {
		const base = defaultProfileId(agent);
		let suffix = profiles.length + 1;
		let id = `${base}-${suffix}`;
		while (profiles.some((p) => p.id === id)) {
			suffix += 1;
			id = `${base}-${suffix}`;
		}
		return {
			id,
			agent,
			name: agentLabel(agent),
			mode: 'enabled',
			command: defaultCommand(agent),
			home: null,
			models: [],
			default_model: '',
			approval_mode: 'auto',
			sandbox_mode: 'workspace-write',
			permission_mode: 'default',
			launch_args: '',
			api_endpoint: '',
			server_url: '',
			server_password: ''
		};
	}

	function profileSubtitle(profile: AgentProfile): string {
		const modelCount = profile.models?.length || 0;
		const modelLabel =
			modelCount === 0
				? 'auto-detect'
				: modelCount === 1
					? profile.default_model
					: `${modelCount} models`;
		return `agent:${profile.id} · ${modelLabel}`;
	}

	function defaultProfileId(agent: AgentProfile['agent']): string {
		return agent === 'claude_code' ? 'claude-code' : agent;
	}

	function defaultCommand(agent: AgentProfile['agent']): string {
		return {
			codex: 'codex',
			claude_code: 'claude',
			cursor: 'agent',
			grok: 'grok',
			opencode: 'opencode',
			cline: 'cline',
			gemini: 'gemini',
			pi: 'pi'
		}[agent];
	}

	function agentLabel(agent: AgentProfile['agent']): string {
		return {
			codex: 'Codex',
			claude_code: 'Claude Code',
			cursor: 'Cursor',
			grok: 'Grok',
			opencode: 'OpenCode',
			cline: 'Cline',
			gemini: 'Gemini',
			pi: 'Pi'
		}[agent];
	}

	function statusKey(profile: AgentProfile): string {
		return statusLabelKey[detected[profile.id]?.status || 'error'] || statusLabelKey.error;
	}

	function statusTone(profile: AgentProfile): string {
		if (profile.mode === 'disabled') return 'bg-gray-300 dark:bg-gray-700';
		const status = detected[profile.id]?.status || 'error';
		if (available[profile.id] && status === 'ready') return 'bg-emerald-500 dark:bg-emerald-400';
		if (status === 'missing_dependency' || status === 'auth_unknown') {
			return 'bg-amber-400 dark:bg-amber-300';
		}
		return 'bg-gray-300 dark:bg-gray-700';
	}

	function saveProfile(profile: AgentProfile) {
		const normalized = normalizeProfile(profile);
		const currentModal = modal;
		if (currentModal?.mode === 'edit') {
			const previousId = currentModal.profile.id;
			profiles = profiles.map((current, index) =>
				index === currentModal.index ? normalized : current
			);
			if (previousId !== normalized.id) {
				const { [previousId]: _removed, ...rest } = saved;
				saved = rest;
			}
		} else {
			profiles = [...profiles, normalized];
		}
		saved = { ...saved, [normalized.id]: true };
		modal = null;
	}

	async function deleteProfile() {
		const currentModal = modal;
		if (currentModal?.mode !== 'edit') {
			modal = null;
			return;
		}
		const profile = profiles[currentModal.index];
		profiles = profiles.filter((_, index) => index !== currentModal.index);
		if (profile) {
			const { [profile.id]: _removed, ...rest } = saved;
			saved = rest;
			deletedProfile = true;
		}
		modal = null;
		await save();
	}

	function toggleProfile(index: number) {
		const profile = profiles[index];
		if (!profile) return;
		const mode = profile.mode === 'disabled' ? 'enabled' : 'disabled';
		profiles = profiles.map((current, i) => (i === index ? { ...current, mode } : current));
		saved = { ...saved, [profile.id]: true };
	}

	async function save() {
		const savedProfiles = deletedProfile
			? profiles
			: profiles.filter((profile) => saved[profile.id]);
		if (savedProfiles.length === 0 && !deletedProfile) {
			toast.success($t('settings.saved'));
			return;
		}
		saving = true;
		try {
			applyResponse(await updateAgents(savedProfiles.map(normalizeProfile)));
			await refreshChatState();
			toast.success($t('settings.saved'));
		} catch (error) {
			toast.error(error instanceof Error ? error.message : $t('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}

	async function refresh() {
		refreshing = true;
		try {
			applyResponse(await refreshAgents());
			await refreshChatState();
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		} finally {
			refreshing = false;
		}
	}

	onMount(async () => {
		try {
			applyResponse(await getAgents());
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		} finally {
			loading = false;
		}
	});
</script>

<div class="flex flex-col min-h-full">
	<div class="flex items-center justify-between mb-4">
		<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('admin.agents')}</h2>
		<div class="flex items-center gap-1">
			<button
				class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75 disabled:opacity-30"
				onclick={refresh}
				disabled={refreshing}
				title={$t('admin.agentsRefresh')}
			>
				{#if refreshing}
					<Spinner size={13} />
				{:else}
					<Icon name="refresh" size={13} />
				{/if}
			</button>
			<button
				class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
				onclick={() => (modal = { mode: 'create', profile: newProfile() })}
				title={$t('admin.agentsAddProfile')}
			>
				<Icon name="plus" size={14} />
			</button>
		</div>
	</div>

	{#if loading}
		<div class="flex justify-center py-8">
			<Spinner size={16} />
		</div>
	{:else}
		<div class="divide-y divide-gray-100 dark:divide-white/5">
			{#each profiles as profile, index (profile.id)}
				<div class="group flex items-center gap-2 w-full min-h-9 py-1.5 text-left">
					<button
						type="button"
						class="flex-1 min-w-0 text-left"
						onclick={() => (modal = { mode: 'edit', index, profile })}
					>
						<span
							class="block text-[0.8125rem] truncate {profile.mode === 'disabled'
								? 'text-gray-400 dark:text-gray-600'
								: 'text-gray-700 dark:text-gray-300'}"
						>
							{profile.name}
						</span>
						<span class="block text-[0.6875rem] text-gray-400 dark:text-gray-600 truncate">
							{profileSubtitle(profile)}
						</span>
						{#if detected[profile.id]?.message}
							<span class="block text-[0.6875rem] text-gray-300 dark:text-gray-700 truncate">
								{detected[profile.id]?.message}
							</span>
						{/if}
					</button>
					<span
						class="hidden sm:flex items-center gap-1.5 shrink-0 text-[0.6875rem] text-gray-400 dark:text-gray-600"
					>
						<span class="w-1.5 h-1.5 rounded-full {statusTone(profile)}"></span>
						<span>{$t(statusKey(profile))}</span>
					</span>
					<ToggleSwitch value={profile.mode !== 'disabled'} onchange={() => toggleProfile(index)} />
				</div>
			{/each}
		</div>

		{#if profiles.length === 0}
			<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">
				{$t('admin.agentsNoProfiles')}
			</p>
		{/if}

		<div class="mt-auto pt-6 flex items-center justify-between">
			<span class="text-xs text-gray-500">
				{profiles.length} profile{profiles.length === 1 ? '' : 's'}
			</span>
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
				onclick={save}
				disabled={saving}
			>
				{#if saving}
					<Spinner size={14} />
				{:else}
					{$t('settings.save')}
				{/if}
			</button>
		</div>
	{/if}
</div>

{#if modal}
	<AgentProfileModal
		profile={modal.profile}
		isNew={modal.mode === 'create'}
		onclose={() => (modal = null)}
		onsave={saveProfile}
		ondelete={deleteProfile}
	/>
{/if}
