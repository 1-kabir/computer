<script lang="ts">
	import Modal from '../Modal.svelte';
	import { t } from '$lib/i18n';
	import type { AgentProfile } from '$lib/apis/admin';

	interface Props {
		profile: AgentProfile;
		isNew?: boolean;
		onclose: () => void;
		onsave: (profile: AgentProfile) => void;
		ondelete: () => void;
	}

	let { profile, isNew = false, onclose, onsave, ondelete }: Props = $props();
	const fieldPrefix = `agent-profile-${Math.random().toString(36).slice(2)}`;

	// svelte-ignore state_referenced_locally
	let draft = $state<AgentProfile>({
		...profile,
		models: [...(profile.models?.length ? profile.models : [])]
	});

	function normalizeDraft(): AgentProfile {
		const currentModels = draft.models?.map((model) => model.trim()).filter(Boolean) || [];
		const models = currentModels.filter((model) => model !== 'default');
		const defaultModel = models.includes(draft.default_model)
			? draft.default_model
			: models[0] || '';
		return {
			...draft,
			id: draft.id.trim(),
			name: draft.name.trim() || draft.id.trim(),
			command: draft.command.trim() || defaultCommand(draft.agent),
			home: draft.home?.trim() || null,
			models,
			default_model: defaultModel,
			launch_args: draft.launch_args?.trim() || '',
			api_endpoint: draft.api_endpoint?.trim() || '',
			server_url: draft.server_url?.trim() || '',
			server_password: draft.server_password?.trim() || ''
		};
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
			pi: 'pi',
			antigravity: 'agy'
		}[agent];
	}

	function defaultName(agent: AgentProfile['agent']): string {
		return {
			codex: 'Codex',
			claude_code: 'Claude Code',
			cursor: 'Cursor',
			grok: 'Grok',
			opencode: 'OpenCode',
			cline: 'Cline',
			gemini: 'Gemini',
			pi: 'Pi',
			antigravity: 'Antigravity'
		}[agent];
	}
</script>

<Modal {onclose} class="w-full max-w-md mx-4">
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="p-4"
		onkeydown={(e) => {
			if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) onsave(normalizeDraft());
		}}
	>
		<div class="flex items-center justify-between gap-3 mb-3">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white">
				{isNew ? $t('admin.agentsAddProfile') : draft.name}
			</h2>
			<button
				type="button"
				class="text-[0.8125rem] text-gray-400 dark:text-gray-600 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
				onclick={onclose}
			>
				Close
			</button>
		</div>

		<div class="flex gap-3">
			<div class="flex-1">
				<label for={`${fieldPrefix}-name`} class="text-[0.625rem] text-gray-400 dark:text-gray-600">
					{$t('admin.agentsProfileName')}
				</label>
				<input
					id={`${fieldPrefix}-name`}
					type="text"
					bind:value={draft.name}
					autocomplete="off"
					spellcheck="false"
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5"
				/>
			</div>
			<div class="w-32 shrink-0">
				<label for={`${fieldPrefix}-type`} class="text-[0.625rem] text-gray-400 dark:text-gray-600">
					{$t('admin.agentsType')}
				</label>
				<select
					id={`${fieldPrefix}-type`}
					value={draft.agent}
					onchange={(e) => {
						const agent = e.currentTarget.value as AgentProfile['agent'];
						draft = {
							...draft,
							agent,
							name: defaultName(agent),
							command: defaultCommand(agent),
							models: [],
							default_model: '',
							approval_mode: draft.approval_mode || 'auto',
							sandbox_mode: draft.sandbox_mode || 'workspace-write',
							permission_mode: draft.permission_mode || 'default',
							launch_args: draft.launch_args || ''
						};
					}}
					class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 outline-none py-0.5 cursor-pointer"
				>
					<option value="codex">Codex</option>
					<option value="claude_code">Claude Code</option>
					<option value="cursor">Cursor</option>
					<option value="grok">Grok</option>
					<option value="opencode">OpenCode</option>
					<option value="cline">Cline</option>
					<option value="gemini">Gemini</option>
					<option value="pi">Pi</option>
					<option value="antigravity">Antigravity</option>
				</select>
			</div>
		</div>

		<label for={`${fieldPrefix}-id`} class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2">
			{$t('admin.agentsProfileId')}
		</label>
		<input
			id={`${fieldPrefix}-id`}
			type="text"
			bind:value={draft.id}
			autocomplete="off"
			spellcheck="false"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<label
			for={`${fieldPrefix}-command`}
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
		>
			{$t('admin.agentsCommand')}
		</label>
		<input
			id={`${fieldPrefix}-command`}
			type="text"
			bind:value={draft.command}
			placeholder={defaultCommand(draft.agent)}
			autocomplete="off"
			spellcheck="false"
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		<label
			for={`${fieldPrefix}-home`}
			class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
		>
			{$t('admin.agentsHome')}
		</label>
		<input
			id={`${fieldPrefix}-home`}
			type="text"
			value={draft.home || ''}
			placeholder={$t('admin.optional')}
			autocomplete="off"
			spellcheck="false"
			oninput={(e) => (draft.home = e.currentTarget.value || null)}
			class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
		/>

		{#if draft.agent === 'claude_code'}
			<label
				for={`${fieldPrefix}-launch-args`}
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>
				{$t('admin.agentsLaunchArgs')}
			</label>
			<input
				id={`${fieldPrefix}-launch-args`}
				type="text"
				bind:value={draft.launch_args}
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>
		{/if}

		{#if draft.agent === 'cursor'}
			<label
				for={`${fieldPrefix}-api-endpoint`}
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>
				{$t('admin.agentsApiEndpoint')}
			</label>
			<input
				id={`${fieldPrefix}-api-endpoint`}
				type="text"
				bind:value={draft.api_endpoint}
				placeholder={$t('admin.optional')}
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>
		{/if}

		{#if draft.agent === 'opencode'}
			<label
				for={`${fieldPrefix}-server-url`}
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>
				{$t('admin.agentsServerUrl')}
			</label>
			<input
				id={`${fieldPrefix}-server-url`}
				type="text"
				bind:value={draft.server_url}
				placeholder={$t('admin.optional')}
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>

			<label
				for={`${fieldPrefix}-server-password`}
				class="text-[0.625rem] text-gray-400 dark:text-gray-600 mt-2"
			>
				{$t('admin.agentsServerPassword')}
			</label>
			<input
				id={`${fieldPrefix}-server-password`}
				type="password"
				bind:value={draft.server_password}
				placeholder={$t('admin.optional')}
				autocomplete="off"
				spellcheck="false"
				class="block w-full bg-transparent text-[0.8125rem] text-gray-700 dark:text-gray-300 placeholder:text-gray-300 dark:placeholder:text-gray-700 outline-none py-0.5 font-mono"
			/>
		{/if}

		<div class="flex items-center justify-between mt-3">
			<div class="flex items-center gap-3">
				{#if !isNew}
					<button
						type="button"
						class="text-[0.8125rem] text-gray-400 hover:text-red-500 dark:hover:text-red-400 transition-colors duration-100"
						onclick={ondelete}
						title={$t('admin.agentsDeleteProfile')}
					>
						{$t('admin.agentsDeleteProfile')}
					</button>
				{/if}
			</div>
			<button
				type="button"
				onclick={() => onsave(normalizeDraft())}
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100"
			>
				{$t('settings.save')}
			</button>
		</div>
	</div>
</Modal>
