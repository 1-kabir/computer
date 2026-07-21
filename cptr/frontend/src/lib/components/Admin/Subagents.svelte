<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount } from 'svelte';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let loading = $state(true);
	let saving = $state(false);

	let enabled = $state(false);
	let backgroundEnabled = $state(false);
	let maxConcurrent = $state(20);
	let maxAsync = $state(20);
	let maxIterations = $state(30);
	let maxOutput = $state(30000);
	let systemPrompt = $state('');

	onMount(async () => {
		try {
			const config = await getAdminConfig();
			enabled = config['subagents.enabled'] === true || config['subagents.enabled'] === 'true';
			backgroundEnabled =
				config['subagents.background_enabled'] === true ||
				config['subagents.background_enabled'] === 'true';
			maxConcurrent = Number(config['subagents.max_concurrent']) || 20;
			maxAsync = Number(config['subagents.max_async']) || 20;
			maxIterations = Number(config['subagents.max_iterations']) || 30;
			maxOutput = Number(config['subagents.max_output']) || 30000;
			systemPrompt = (config['subagents.system_prompt'] as string) || '';
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		}
		loading = false;
	});

	async function save() {
		saving = true;
		try {
			await updateConfig({
				'subagents.enabled': enabled,
				'subagents.background_enabled': backgroundEnabled,
				'subagents.max_concurrent': maxConcurrent,
				'subagents.max_async': maxAsync,
				'subagents.max_iterations': maxIterations,
				'subagents.max_output': maxOutput,
				'subagents.system_prompt': systemPrompt
			});
			toast.success($t('settings.saved'));
		} catch {
			toast.error($t('admin.failedToSave'));
		} finally {
			saving = false;
		}
	}
</script>

<div class="flex flex-col min-h-full">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">{$t('admin.subagents')}</h2>

	{#if loading}
		<div class="flex justify-center py-8"><Spinner size={16} /></div>
	{:else}
		<div class="flex flex-col gap-2.5">
			<label class="flex items-center justify-between cursor-pointer">
				<span class="text-xs text-gray-600 dark:text-gray-400">{$t('admin.subagentsEnabled')}</span>
				<ToggleSwitch
					value={enabled}
					onchange={(v) => {
						enabled = v;
					}}
				/>
			</label>
			<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 -mt-1">
				{$t('admin.subagentsHint')}
			</p>

			{#if enabled}
				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="sa-concurrent"
						>{$t('admin.subagentsMaxConcurrent')}</label
					>
					<div class="flex items-center gap-1.5 mt-1">
						<input
							id="sa-concurrent"
							type="number"
							bind:value={maxConcurrent}
							min="-1"
							class="w-16 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
						/>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
							>{$t('admin.subagentsMaxConcurrentHint')}</span
						>
					</div>
				</div>

				<div>
					<label class="flex items-center justify-between cursor-pointer">
						<span class="text-xs text-gray-600 dark:text-gray-400"
							>{$t('admin.subagentsBackgroundEnabled')}</span
						>
						<ToggleSwitch
							value={backgroundEnabled}
							onchange={(v) => {
								backgroundEnabled = v;
							}}
						/>
					</label>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-1">
						{$t('admin.subagentsBackgroundHint')}
					</p>
				</div>

				{#if backgroundEnabled}
					<div>
						<label class="text-xs text-gray-600 dark:text-gray-400" for="sa-async"
							>{$t('admin.subagentsMaxAsync')}</label
						>
						<div class="flex items-center gap-1.5 mt-1">
							<input
								id="sa-async"
								type="number"
								bind:value={maxAsync}
								min="-1"
								class="w-16 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
							/>
							<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
								>{$t('admin.subagentsMaxAsyncHint')}</span
							>
						</div>
					</div>
				{/if}

				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="sa-iterations"
						>{$t('admin.subagentsMaxIterations')}</label
					>
					<div class="flex items-center gap-1.5 mt-1">
						<input
							id="sa-iterations"
							type="number"
							bind:value={maxIterations}
							min="1"
							max="100"
							class="w-16 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
						/>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
							>{$t('admin.subagentsMaxIterationsHint')}</span
						>
					</div>
				</div>

				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="sa-output"
						>{$t('admin.subagentsMaxOutput')}</label
					>
					<div class="flex items-center gap-1.5 mt-1">
						<input
							id="sa-output"
							type="number"
							bind:value={maxOutput}
							min="1000"
							max="100000"
							step="1000"
							class="w-20 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors"
						/>
						<span class="text-[0.6875rem] text-gray-400 dark:text-gray-600"
							>{$t('admin.chars')}</span
						>
					</div>
				</div>

				<div>
					<label class="text-xs text-gray-600 dark:text-gray-400" for="sa-prompt"
						>{$t('admin.subagentsSystemPrompt')}</label
					>
					<textarea
						id="sa-prompt"
						bind:value={systemPrompt}
						rows="4"
						placeholder={$t('admin.subagentsSystemPromptPlaceholder')}
						class="w-full mt-1 px-2 py-1.5 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-blue-400 dark:focus:border-blue-500 transition-colors resize-y font-mono"
					/>
					<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600 mt-0.5">
						{$t('admin.subagentsSystemPromptHint')}
					</p>
				</div>
			{/if}
		</div>

		<!-- Save -->
		<div class="mt-auto pt-6 flex justify-end">
			<button
				class="text-[0.8125rem] text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white transition-colors duration-100 disabled:opacity-50"
				onclick={() => save()}
				disabled={saving}>{$t('settings.save')}</button
			>
		</div>
	{/if}
</div>
