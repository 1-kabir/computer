<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { getAdminConfig, updateConfig } from '$lib/apis/admin';
	import { t } from '$lib/i18n';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	let loading = $state(true);
	let saving = $state(false);
	let enabled = $state(true);
	let toolEnabled = $state(true);
	let backgroundReview = $state(true);
	let reviewInterval = $state(10);

	onMount(async () => {
		try {
			const config = await getAdminConfig();
			enabled = config['skills.enabled'] !== false && config['skills.enabled'] !== 'false';
			toolEnabled =
				config['skills.tool_enabled'] !== false && config['skills.tool_enabled'] !== 'false';
			backgroundReview =
				config['skills.background_review_enabled'] !== false &&
				config['skills.background_review_enabled'] !== 'false';
			reviewInterval = Number(config['skills.review_interval_turns']) || 10;
		} catch {
			toast.error($t('admin.failedToLoadConfig'));
		}
		loading = false;
	});

	async function save() {
		saving = true;
		try {
			await updateConfig({
				'skills.enabled': enabled,
				'skills.tool_enabled': toolEnabled,
				'skills.background_review_enabled': backgroundReview,
				'skills.review_interval_turns': Math.max(1, Number(reviewInterval) || 10)
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
			<h2 class="text-sm font-medium text-gray-900 dark:text-white mb-4">
				{$t('chat.skills')}
			</h2>

			<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">
				{$t('admin.skillsBehavior')}
			</h3>
			<div class="flex flex-col gap-2.5">
				<label class="flex items-center justify-between cursor-pointer">
					<span class="text-xs text-gray-600 dark:text-gray-400">{$t('admin.skillsEnable')}</span>
					<ToggleSwitch
						value={enabled}
						onchange={(v) => {
							enabled = v;
						}}
					/>
				</label>

				{#if enabled}
					<label class="flex items-center justify-between cursor-pointer">
						<span class="text-xs text-gray-600 dark:text-gray-400"
							>{$t('admin.skillsAssistantCanManage')}</span
						>
						<ToggleSwitch
							value={toolEnabled}
							onchange={(v) => {
								toolEnabled = v;
							}}
						/>
					</label>

					<label class="flex items-center justify-between cursor-pointer">
						<span class="text-xs text-gray-600 dark:text-gray-400"
							>{$t('admin.skillsBackgroundReview')}</span
						>
						<ToggleSwitch
							value={backgroundReview}
							onchange={(v) => {
								backgroundReview = v;
							}}
						/>
					</label>
				{/if}
			</div>

			{#if enabled}
				<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2 mt-5">
					{$t('admin.skillsLimits')}
				</h3>
				<div class="flex flex-col gap-2.5">
					<div>
						<label class="text-xs text-gray-600 dark:text-gray-400" for="skills-review-interval">
							{$t('admin.skillsReviewEvery')}
						</label>
						<input
							id="skills-review-interval"
							type="number"
							bind:value={reviewInterval}
							min="1"
							class="w-full mt-1 h-7 px-2 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none transition-colors"
						/>
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
				{saving ? $t('settings.saving') : $t('settings.save')}
			</button>
		</div>
	{/if}
</div>
