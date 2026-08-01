<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import ModelSelector from '$lib/components/common/ModelSelector.svelte';

	let loading = $state(true);
	let saving = $state(false);
	let titleGenerationEnabled = $state(true);
	let titleGenerationModel = $state<string | null>(null);
	let contextCompactionModel = $state<string | null>(null);
	let compactTokenThreshold = $state(80000);

	onMount(async () => {
		try {
			const config = await getAdminConfig();
			titleGenerationEnabled =
				config['chat.title_generation.enabled'] !== false &&
				config['chat.title_generation.enabled'] !== 'false';
			titleGenerationModel =
				typeof config['chat.title_generation.model'] === 'string'
					? config['chat.title_generation.model']
					: null;
			contextCompactionModel =
				typeof config['chat.context_compaction.model'] === 'string'
					? config['chat.context_compaction.model']
					: null;
			compactTokenThreshold = Number(config['chat.compact_token_threshold']) || 80000;
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		} finally {
			loading = false;
		}
	});

	async function save() {
		saving = true;
		try {
			await updateConfig({
				'chat.title_generation.enabled': titleGenerationEnabled,
				'chat.title_generation.model': titleGenerationModel,
				'chat.context_compaction.model': contextCompactionModel,
				'chat.compact_token_threshold': Math.max(10000, Number(compactTokenThreshold) || 80000)
			});
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
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('admin.chat')}</h2>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">
				{$t('admin.chatTitles')}
			</h3>
			<div class="flex flex-col gap-2.5">
				<label class="flex items-center justify-between cursor-pointer">
					<span class="text-xs text-gray-600 dark:text-gray-400">
						{$t('admin.chatTitleGeneration')}
					</span>
					<ToggleSwitch
						value={titleGenerationEnabled}
						onchange={(value) => {
							titleGenerationEnabled = value;
						}}
					/>
				</label>
				<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
					{$t('admin.chatTitleGenerationHint')}
				</p>

				{#if titleGenerationEnabled}
					<div>
						<div class="flex items-center justify-between gap-3">
							<span class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
								{$t('admin.chatTitleModel')}
							</span>
							<div class="shrink-0">
								<ModelSelector
									bind:selectedModel={titleGenerationModel}
									nullable
									nullLabel={$t('modelSelector.currentModel')}
									preferAbove={false}
								/>
							</div>
						</div>
						<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
							{$t('admin.chatTitleModelHint')}
						</p>
					</div>
				{/if}
			</div>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
				{$t('admin.contextCompaction')}
			</h3>
			<div class="flex flex-col gap-2.5">
				<div>
					<div class="flex items-center justify-between gap-3">
						<span class="min-w-0 text-xs text-gray-600 dark:text-gray-400">
							{$t('admin.contextSummaryModel')}
						</span>
						<div class="shrink-0">
							<ModelSelector
								bind:selectedModel={contextCompactionModel}
								nullable
								nullLabel={$t('modelSelector.currentModel')}
								preferAbove={false}
							/>
						</div>
					</div>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
						{$t('admin.contextSummaryModelHint')}
					</p>
				</div>
				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="compact-threshold">
						{$t('admin.compactTokenThreshold')}
					</label>
					<div class="flex items-center gap-1.5 mt-1">
						<input
							id="compact-threshold"
							type="number"
							bind:value={compactTokenThreshold}
							min="10000"
							max="1000000"
							step="10000"
							class="w-24 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
						/>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
							{$t('admin.compactTokenThresholdUnit')}
						</span>
					</div>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
						{$t('admin.compactTokenThresholdHint')}
					</p>
				</div>
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
