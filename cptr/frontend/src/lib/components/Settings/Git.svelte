<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { get } from 'svelte/store';
	import { toast } from 'svelte-sonner';
	import { activeWorkspace } from '$lib/stores';
	import { session } from '$lib/session';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import { getPreferences, savePreferences } from '$lib/apis/state';
	import {
		cancelGhLogin,
		getGhLoginStatus,
		getGitConfig,
		ghLogout,
		ghSetupGit,
		ghSwitch,
		startGhLogin,
		type GhAccount,
		type GhLoginStatus,
		type GitIdentity,
		type GitSettingsConfig
	} from '$lib/apis/git';
	import { t } from '$lib/i18n';
	import Collapsible from '$lib/components/Collapsible.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';

	let loading = $state(true);
	let saving = $state(false);
	let ghBusy = $state(false);
	let config = $state<GitSettingsConfig | null>(null);
	let gitName = $state('');
	let gitEmail = $state('');
	let commitMessageModel = $state<string | null>(null);
	let login = $state<GhLoginStatus | null>(null);
	let pollTimer: ReturnType<typeof setTimeout> | null = null;

	const workspacePath = $derived($activeWorkspace?.path ?? '');
	const isAdmin = $derived($session?.role === 'admin');
	const detectedIdentity = $derived(config?.git.identity ?? {});
	const identityComplete = $derived(Boolean(detectedIdentity.name && detectedIdentity.email));
	const ghHosts = $derived.by((): [string, GhAccount[]][] =>
		Object.entries((config?.gh.hosts ?? {}) as Record<string, GhAccount[]>)
	);
	const tr = (key: string) => (get(t) as (key: string) => string)(key);

	onMount(load);
	onDestroy(() => {
		if (pollTimer) clearTimeout(pollTimer);
	});

	async function load() {
		loading = true;
		try {
			const [nextConfig, prefs] = await Promise.all([
				getGitConfig(workspacePath || undefined),
				getPreferences()
			]);
			config = nextConfig;
			const gitPrefs = prefs.git && typeof prefs.git === 'object' ? prefs.git : {};
			const identity =
				'identity' in gitPrefs && typeof gitPrefs.identity === 'object' ? gitPrefs.identity : {};
			gitName =
				typeof (identity as Record<string, unknown>).name === 'string'
					? ((identity as Record<string, string>).name ?? '')
					: nextConfig.app_identity?.name || '';
			gitEmail =
				typeof (identity as Record<string, unknown>).email === 'string'
					? ((identity as Record<string, string>).email ?? '')
					: nextConfig.app_identity?.email || '';
			if (isAdmin) {
				const adminConfig = await getAdminConfig();
				commitMessageModel =
					typeof adminConfig['git.commit_message_generation.model'] === 'string'
						? adminConfig['git.commit_message_generation.model']
						: null;
			}
		} catch (e) {
			toast.error(e instanceof Error ? e.message : tr('admin.failedToLoadConfig'));
		} finally {
			loading = false;
		}
	}

	async function save() {
		saving = true;
		try {
			await savePreferences({
				git: { identity: { name: gitName.trim(), email: gitEmail.trim() } }
			});
			if (isAdmin) {
				await updateConfig({ 'git.commit_message_generation.model': commitMessageModel });
			}
			toast.success(tr('settings.saved'));
			config = await getGitConfig(workspacePath || undefined);
		} catch (e) {
			toast.error(e instanceof Error ? e.message : tr('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}

	async function beginLogin(hostname = 'github.com') {
		if (!config?.permissions.can_manage_gh) return;
		ghBusy = true;
		try {
			login = await startGhLogin(hostname);
			pollLogin();
		} catch (e) {
			toast.error(e instanceof Error ? e.message : 'GitHub login failed');
		} finally {
			ghBusy = false;
		}
	}

	function pollLogin() {
		if (!login || login.status !== 'pending') return;
		if (pollTimer) clearTimeout(pollTimer);
		pollTimer = setTimeout(async () => {
			if (!login) return;
			try {
				login = await getGhLoginStatus(login.session_id);
				if (login.status === 'complete') {
					toast.success('GitHub signed in');
					config = await getGitConfig(workspacePath || undefined);
					return;
				}
				if (login.status === 'pending') pollLogin();
			} catch (e) {
				toast.error(e instanceof Error ? e.message : 'GitHub login failed');
			}
		}, 2000);
	}

	async function cancelLogin() {
		if (!login) return;
		try {
			await cancelGhLogin(login.session_id);
		} catch {}
		login = null;
	}

	async function runGhAction(action: () => Promise<unknown>) {
		ghBusy = true;
		try {
			await action();
			config = await getGitConfig(workspacePath || undefined);
			toast.success(tr('settings.saved'));
		} catch (e) {
			toast.error(e instanceof Error ? e.message : 'GitHub CLI action failed');
		} finally {
			ghBusy = false;
		}
	}

	function activeAccount(accounts: GhAccount[]) {
		return accounts.find((account) => account.active) ?? accounts[0];
	}

	function accountKey(account: GhAccount, index: number) {
		return account.login || account.host || String(index);
	}

	function identityLabel(identity?: GitIdentity) {
		const name = identity?.name?.trim();
		const email = identity?.email?.trim();
		if (name && email) return `${name} <${email}>`;
		if (name) return name;
		if (email) return email;
		return 'Not configured';
	}

	function identitySource(identity?: GitIdentity) {
		const sources = [identity?.name_source, identity?.email_source].filter(Boolean);
		return sources.length ? Array.from(new Set(sources)).join(' / ') : 'No git config found';
	}

	function fallbackIdentityLabel() {
		return gitName || gitEmail ? identityLabel({ name: gitName, email: gitEmail }) : 'Not set';
	}

	function ghSummary() {
		if (!config?.gh.installed) return 'Not installed';
		if (!ghHosts.length) return 'Not signed in';
		const [host, accounts] = ghHosts[0];
		const active = activeAccount(accounts);
		return active?.login ? `${active.login} on ${host}` : `Not signed in on ${host}`;
	}
</script>

<div class="flex flex-col h-full">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 -mr-1.5">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{tr('admin.git')}</h2>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">Git</h3>
			<div class="border-y border-gray-100 dark:border-white/5">
				<div class="py-2.5 flex items-center justify-between gap-3">
					<div class="min-w-0">
						<div class="text-xs text-gray-700 dark:text-gray-300">Commit author</div>
						<div class="text-xs text-gray-700 dark:text-gray-300 truncate mt-0.5">
							{identityLabel(detectedIdentity)}
						</div>
						<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">
							{identitySource(detectedIdentity)}
						</div>
					</div>
					<span
						class="shrink-0 text-[0.625rem] {identityComplete
							? 'text-gray-400 dark:text-gray-600'
							: 'text-amber-600 dark:text-amber-400'}"
					>
						{identityComplete ? 'Git config' : 'Needs identity'}
					</span>
				</div>
			</div>

			<div class="mt-3">
				<Collapsible title="Commit identity fallback" summary={fallbackIdentityLabel()}>
					<div class="flex flex-col gap-2.5">
						<div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="git-user-name"
									>Name</label
								>
								<input
									id="git-user-name"
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
									placeholder="Jane Developer"
									bind:value={gitName}
								/>
							</div>
							<div>
								<label class="text-xs text-gray-600 dark:text-gray-400" for="git-user-email"
									>Email</label
								>
								<input
									id="git-user-email"
									class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
									placeholder="jane@example.com"
									bind:value={gitEmail}
								/>
							</div>
						</div>
						<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
							Git config wins. This fallback only fills missing commit author details.
						</p>
					</div>
				</Collapsible>
			</div>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">GitHub</h3>
			<div class="border-y border-gray-100 dark:border-white/5 divide-y divide-gray-100 dark:divide-white/5">
				<div class="py-2.5 flex items-center justify-between gap-3">
					<div class="min-w-0">
						<div class="text-xs text-gray-700 dark:text-gray-300">GitHub CLI</div>
						<div class="text-xs text-gray-700 dark:text-gray-300 truncate mt-0.5">
							{ghSummary()}
						</div>
						<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">
							{config?.gh.message || config?.gh.version || 'GitHub CLI'}
						</div>
					</div>
					{#if config?.gh.installed && !ghHosts.length && config.permissions.can_manage_gh}
						<button
							class="shrink-0 text-[0.6875rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
							disabled={ghBusy}
							onclick={() => beginLogin()}
						>
							{ghBusy ? 'Starting...' : 'Sign in'}
						</button>
					{/if}
				</div>

				{#if login}
					<div class="py-2.5 flex items-center justify-between gap-3">
						<div class="min-w-0">
							<div class="text-xs text-gray-700 dark:text-gray-300 truncate">
								{login.verification_uri || 'Waiting for GitHub CLI...'}
							</div>
							<div class="font-mono text-[0.8125rem] text-gray-900 dark:text-white">
								{login.user_code}
							</div>
						</div>
						<div class="shrink-0 flex gap-3">
							{#if login.verification_uri}
								<a
									class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
									href={login.verification_uri}
									target="_blank"
									rel="noopener noreferrer">Open</a
								>
							{/if}
							<button
								class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
								onclick={() => navigator.clipboard.writeText(login?.user_code || '')}
								>Copy code</button
							>
							<button
								class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
								onclick={cancelLogin}>Cancel</button
							>
						</div>
					</div>
				{/if}
			</div>

			<div class="mt-3">
				<Collapsible title="Accounts and actions" summary={ghSummary()}>
					<div class="border-y border-gray-100 dark:border-white/5 divide-y divide-gray-100 dark:divide-white/5">
						{#if !config?.gh.installed}
							<div class="py-2.5 text-xs text-gray-700 dark:text-gray-300">
								{config?.gh.message || 'GitHub CLI is not installed.'}
							</div>
						{:else if ghHosts.length}
							{#each ghHosts as [host, accounts]}
								{@const active = activeAccount(accounts)}
								<div class="py-2.5 flex items-start justify-between gap-3">
									<div class="min-w-0">
										<div class="truncate text-xs text-gray-700 dark:text-gray-300">
											{active?.login || 'Not signed in'}
										</div>
										<div class="truncate text-[0.625rem] text-gray-400 dark:text-gray-600">
											{host} · {active?.state || 'unknown'} · {active?.gitProtocol || 'git'}
										</div>
										{#if accounts.length > 1}
											<div class="mt-1 flex flex-wrap gap-x-2 gap-y-1">
												{#each accounts as account, index (accountKey(account, index))}
													<span class="text-[0.625rem] text-gray-400 dark:text-gray-600">
														{account.login || 'unknown'}
															{#if account.active}
																(active)
															{:else if config.permissions.can_manage_gh && account.login}
																<button
																	class="ml-1 text-gray-500 hover:text-gray-900 dark:hover:text-white"
																	disabled={ghBusy}
																	onclick={() =>
																		runGhAction(() => ghSwitch(host, account.login as string))}
																	>Switch</button
															>
														{/if}
													</span>
												{/each}
											</div>
										{/if}
									</div>
									{#if config.permissions.can_manage_gh}
										<div class="shrink-0 flex flex-wrap justify-end gap-x-2 gap-y-1">
											<button
												class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
												disabled={ghBusy}
												onclick={() => beginLogin(host)}>Add account</button
											>
											<button
												class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
												disabled={ghBusy}
												onclick={() => runGhAction(() => ghSetupGit(host))}>Setup Git</button
											>
											<button
												class="text-[0.625rem] text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors duration-100"
												disabled={ghBusy}
												onclick={() => runGhAction(() => ghLogout(host, active?.login))}
												>Logout</button
											>
										</div>
									{/if}
								</div>
							{/each}
						{:else}
							<div class="py-2.5 flex items-center justify-between gap-3">
								<div class="min-w-0">
									<div class="text-xs text-gray-700 dark:text-gray-300">
										{config.permissions.can_manage_gh ? 'Not signed in' : 'Managed by admin'}
									</div>
									<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate">
										{config.gh.version}
									</div>
								</div>
								{#if config.permissions.can_manage_gh}
									<button
										class="shrink-0 text-[0.6875rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
										disabled={ghBusy}
										onclick={() => beginLogin()}
									>
										{ghBusy ? 'Starting...' : 'Sign in'}
										</button>
									{/if}
								</div>
							{/if}
						</div>
					</Collapsible>
				</div>

			{#if isAdmin}
				<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
					{tr('sidebar.admin')}
				</h3>
				<div class="border-y border-gray-100 dark:border-white/5">
						<div class="py-2.5 flex items-center justify-between gap-3">
							<div class="min-w-0">
								<div class="text-xs text-gray-700 dark:text-gray-300">
									{tr('admin.gitCommitMessageModel')}
								</div>
							<div class="text-[0.625rem] text-gray-400 dark:text-gray-600 truncate mt-0.5">
								{tr('admin.gitCommitMessageModelHint')}
							</div>
						</div>
						<div class="shrink-0">
							<ModelSelector
								bind:selectedModel={commitMessageModel}
								nullable
								nullLabel={tr('modelSelector.defaultModel')}
								preferAbove={false}
							/>
						</div>
					</div>
				</div>
			{/if}
		</div>

		<div class="shrink-0 pt-3 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
				disabled={saving}
				onclick={save}
			>
				{saving ? tr('settings.saving') : tr('settings.save')}
			</button>
		</div>
	{/if}
</div>
