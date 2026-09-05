<script lang="ts">
	import { toast } from 'svelte-sonner';
	import Icon from '../Icon.svelte';
	import CreateBotModal from './CreateBotModal.svelte';
	import { onMount } from 'svelte';
	import { listBots, deleteBot, startBot, stopBot, type BotData } from '$lib/apis/bots';
	import { t } from '$lib/i18n';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ToggleSwitch from '$lib/components/common/ToggleSwitch.svelte';
	import { fetchJSON } from '$lib/apis';
	import { updateConfig } from '$lib/apis/admin';

	let bots = $state<BotData[]>([]);
	let loading = $state(true);
	let systemPrompt = $state('');
	let promptSaving = $state(false);

	async function loadSystemPrompt() {
		try {
			const cfg = await fetchJSON<{ config: Record<string, unknown> }>(
				'/api/admin/config/gateway'
			);
			systemPrompt =
				typeof cfg.config['gateway.system_prompt'] === 'string'
					? (cfg.config['gateway.system_prompt'] as string)
					: '';
		} catch {
			// non-fatal: the prompt stays editable with the default (empty) value
		}
	}

	async function saveSystemPrompt() {
		promptSaving = true;
		try {
			await updateConfig({ 'gateway.system_prompt': systemPrompt });
		} catch {
			toast.error($t('messaging.failedToLoad'));
		} finally {
			promptSaving = false;
		}
	}

	let showCreate = $state(false);
	let editBot = $state<BotData | null>(null);

	async function load() {
		try {
			bots = await listBots();
		} catch {
			toast.error($t('messaging.failedToLoad'));
		} finally {
			loading = false;
		}
	}

	function handleSaved() {
		showCreate = false;
		editBot = null;
		load();
	}

	async function toggleRunning(bot: BotData) {
		const wasRunning = bot.is_running;
		// Optimistic
		bot.is_running = !wasRunning;
		bots = [...bots];
		try {
			if (wasRunning) {
				await stopBot(bot.id);
			} else {
				await startBot(bot.id);
			}
			await load();
		} catch {
			bot.is_running = wasRunning;
			bots = [...bots];
			toast.error($t('messaging.failedToToggle'));
		}
	}

	async function handleDelete(e: Event, bot: BotData) {
		e.stopPropagation();
		try {
			await deleteBot(bot.id);
			await load();
		} catch {
			toast.error($t('messaging.failedToDelete'));
		}
	}

	onMount(() => {
		load();
		loadSystemPrompt();
	});
</script>

<div class="mb-5 border-b border-gray-100 dark:border-white/5 pb-4">
	<h3 class="text-xs text-gray-400 dark:text-gray-600 mb-2">
		{$t('admin.messaging.systemPrompt')}
	</h3>
	<textarea
		class="w-full min-h-24 px-2 py-1.5 rounded-lg text-xs bg-gray-100 dark:bg-white/6 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-white/8 outline-none focus:border-gray-400 dark:focus:border-white/20 transition-colors resize-y font-mono"
		bind:value={systemPrompt}
		placeholder={$t('admin.gateway.systemPromptPlaceholder')}
		disabled={promptSaving}
	></textarea>
	<div class="flex items-start justify-between mt-1.5 gap-2">
		<p class="text-[0.6875rem] text-gray-400 dark:text-gray-600">
			{$t('admin.messaging.systemPromptDescription')}
		</p>
		<button
			type="button"
			class="shrink-0 text-[0.6875rem] px-2 py-1 rounded-md bg-gray-900 text-white dark:bg-white dark:text-gray-900 disabled:opacity-50"
			disabled={promptSaving}
			onclick={saveSystemPrompt}
		>
			{$t('messaging.save')}
		</button>
	</div>
</div>

<div class="flex items-center justify-between mb-4">
	<h2 class="text-sm font-medium text-gray-900 dark:text-white">{$t('admin.messaging')}</h2>
	<button
		class="flex items-center justify-center w-6 h-6 rounded-lg text-gray-400 hover:text-gray-700 dark:text-gray-600 dark:hover:text-gray-300 transition-colors duration-75"
		onclick={() => (showCreate = true)}
	>
		<Icon name="plus" size={14} />
	</button>
</div>

{#if loading}
	<div class="flex justify-center py-8">
		<Spinner size={16} />
	</div>
{:else}
	<div>
		{#each bots as bot}
			<div class="group flex items-center gap-2 w-full h-7">
				<!-- Platform icon -->
				<span
					class="shrink-0
					{bot.is_running ? 'text-gray-400 dark:text-gray-500' : 'text-gray-300 dark:text-gray-700'}"
				>
					<Icon name={bot.platform} size={14} />
				</span>

				<!-- Name (clickable to edit) -->
				<!-- svelte-ignore a11y_no_static_element_interactions -->
				<span
					class="flex-1 text-[0.8125rem] truncate cursor-pointer
					{bot.is_running ? 'text-gray-700 dark:text-gray-300' : 'text-gray-400 dark:text-gray-600'}"
					onclick={() => (editBot = bot)}
					onkeydown={() => {}}
				>
					{bot.name}
				</span>

				<!-- Delete (hover) -->
				<button
					type="button"
					class="opacity-0 group-hover:opacity-100 text-gray-300 dark:text-gray-700 hover:text-red-500 dark:hover:text-red-400 transition-all p-0.5"
					onclick={(e) => handleDelete(e, bot)}
				>
					<Icon name="trash" size={11} />
				</button>

				<ToggleSwitch value={bot.is_running} onchange={() => toggleRunning(bot)} />
			</div>
		{/each}

		{#if bots.length === 0}
			<p class="text-[0.8125rem] text-gray-400 dark:text-gray-600 py-4">{$t('messaging.noBots')}</p>
		{/if}
	</div>
{/if}

{#if showCreate}
	<CreateBotModal onclose={() => (showCreate = false)} onsave={handleSaved} />
{/if}

{#if editBot}
	<CreateBotModal bot={editBot} onclose={() => (editBot = null)} onsave={handleSaved} />
{/if}
