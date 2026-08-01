<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';

	let loading = $state(true);
	let saving = $state(false);
	let commitMessageModel = $state<string | null>(null);

	onMount(async () => {
		try {
			const config = await getAdminConfig();
			commitMessageModel =
				typeof config['git.commit_message_generation.model'] === 'string'
					? config['git.commit_message_generation.model']
					: null;
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		} finally {
			loading = false;
		}
	});

	async function save() {
		saving = true;
		try {
			await updateConfig({ 'git.commit_message_generation.model': commitMessageModel });
			toast.success($t('settings.saved'));
		} catch {
			toast.error($t('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

<div class="flex flex-col h-full">
	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div class="flex-1 min-h-0 overflow-y-auto scrollbar-hover pr-1.5 -mr-1.5">
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('admin.git')}</h2>

			<div>
				<div class="flex items-center justify-between gap-3">
					<span class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
						{$t('admin.gitCommitMessageModel')}
					</span>
					<div class="shrink-0">
						<ModelSelector
							bind:selectedModel={commitMessageModel}
							nullable
							nullLabel={$t('modelSelector.defaultModel')}
							preferAbove={false}
						/>
					</div>
				</div>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
					{$t('admin.gitCommitMessageModelHint')}
				</p>
			</div>
		</div>

		<div class="shrink-0 pt-3 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
				disabled={saving}
				onclick={save}
			>
				{saving ? $t('settings.saving') : $t('settings.save')}
			</button>
		</div>
	{/if}
</div>
